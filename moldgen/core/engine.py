"""Mold-generation pipeline: validate part → build halves → hardware.

All boolean work happens in manifold3d (watertight-guaranteed); trimesh is
used for analysis, repair and I/O. Units are millimeters. The parting plane
is horizontal (normal +Z) at z = params.parting_z_mm in the part's own frame.

Compression mode builds a telescoping (plunge) mold via forged.build —
female pocket + male piston, per Easy Composites' forged carbon guidance.
Open-layup mode splits a simple block at the parting plane and keeps one
side as a single-sided tool.
"""

from __future__ import annotations

import numpy as np
import trimesh

from . import forged, hardware, mfld, prepreg
from .offset import offset_mesh
from .types import MoldParams, MoldResult
from .undercuts import analyze_undercuts, sharp_edge_length_mm
from .validate import validate_and_repair

VALID_MODES = ("compression", "open_layup", "prepreg")
VALID_SIDES = ("above", "below")


def generate_mold(mesh: trimesh.Trimesh, params: MoldParams) -> MoldResult:
    warnings: list[str] = []

    errors = _check_params(params)
    if errors:
        return MoldResult(ok=False, errors=errors)

    part, issues, repair_notes = validate_and_repair(mesh)
    warnings.extend(repair_notes)
    if issues:
        return MoldResult(ok=False, errors=issues, warnings=warnings)

    if part.extents.max() < 1.0:
        warnings.append(
            f"largest part dimension is {part.extents.max():.4g} mm; "
            "if the model was exported in meters, scale it by 1000"
        )

    if params.cavity_offset_mm:
        part = offset_mesh(part, params.cavity_offset_mm)
        if not part.is_volume:
            warnings.append(
                f"cavity offset of {params.cavity_offset_mm:g} mm produced a "
                "self-intersecting or degenerate surface; the cavity may be "
                "unreliable"
            )

    bounds = part.bounds
    z = float(params.parting_z_mm)
    block_lo = bounds[0][2] - params.wall_margin_mm
    block_hi = bounds[1][2] + params.wall_margin_mm
    if not (block_lo < z < block_hi):
        return MoldResult(
            ok=False,
            errors=[
                f"parting_z_mm={z:g} lies outside the mold block "
                f"(z from {block_lo:g} to {block_hi:g})"
            ],
            warnings=warnings,
        )
    if not (bounds[0][2] < z < bounds[1][2]):
        warnings.append(
            "parting plane does not pass through the part; "
            "one half will contain the entire cavity"
        )

    part_man = mfld.to_manifold(part)
    if not mfld.is_valid(part_man):
        return MoldResult(
            ok=False,
            errors=[f"manifold3d rejected the part mesh (status: {part_man.status()})"],
            warnings=warnings,
        )

    z_split = _nudged_parting_height(part, z, warnings)

    if params.demold_check:
        _demold_warnings(part, z_split, params, warnings)

    hardware_meta: dict = {}
    extra_meta: dict = {}
    if params.mode == "compression":
        if params.trim_groove:
            warnings.append("trim groove applies only to open-layup tools; ignored")
        try:
            female, male, forged_meta, hardware_meta = forged.build(
                part_man, bounds, z_split, params, warnings,
                part_centroid_xy=part.center_mass[:2],
            )
        except ValueError as exc:
            return MoldResult(ok=False, errors=[str(exc)], warnings=warnings)
        halves = {
            "female": mfld.to_trimesh(female),
            "male": mfld.to_trimesh(male),
        }
        block_bounds = forged_meta.pop("block_bounds")
        extra_meta["forged"] = forged_meta
    elif params.mode == "prepreg":
        if params.bolts:
            warnings.append(
                "bolt holes apply only to compression molds; use "
                "flange_bolt_holes on a prepreg tool"
            )
        if params.trim_groove:
            warnings.append(
                "prepreg tools get a witness line from the trim-allowance "
                "kick; trim_groove ignored"
            )
        if params.demold_check:
            sharp = sharp_edge_length_mm(part, z_split, params.keep_side)
            if sharp > 1.0:
                warnings.append(
                    f"drape: {sharp:.0f} mm of edges sharper than 45° on the tool "
                    "side — prepreg fabric will bridge tight corners; add 2-3 mm "
                    "fillets to the part or fill corners with a resin fillet"
                )
        tool_man, prepreg_meta, hardware_meta = prepreg.build(
            part_man, bounds, z_split, params, warnings
        )
        halves = {"tool": mfld.to_trimesh(tool_man)}
        block_bounds = prepreg_meta.pop("block_bounds")
        extra_meta["prepreg"] = prepreg_meta
    else:
        if params.bolts:
            warnings.append("bolt holes apply only to compression molds; ignored")
        block_min = bounds[0] - params.wall_margin_mm
        block_max = bounds[1] + params.wall_margin_mm
        mold = mfld.box(block_min, block_max) - part_man

        half_a, half_b = mold.split_by_plane((0.0, 0.0, 1.0), z_split)
        if half_a.is_empty() or half_b.is_empty():
            return MoldResult(
                ok=False,
                errors=["splitting at the parting plane produced an empty half"],
                warnings=warnings,
            )
        # Assign above/below by measured position rather than trusting the
        # split_by_plane ordering convention.
        z_a = float(np.mean(half_a.to_mesh().vert_properties[:, 2]))
        z_b = float(np.mean(half_b.to_mesh().vert_properties[:, 2]))
        above, below = (half_a, half_b) if z_a >= z_b else (half_b, half_a)

        keep = above if params.keep_side == "above" else below
        if params.trim_groove:
            groove, groove_info = hardware.trim_groove_solid(
                bounds, block_min, block_max, z_split, params, warnings
            )
            if groove is not None:
                keep = keep - groove
                hardware_meta["trim_groove"] = groove_info
        halves = {"tool": mfld.to_trimesh(keep)}
        block_bounds = [block_min.tolist(), block_max.tolist()]

    block_size = np.asarray(block_bounds[1]) - np.asarray(block_bounds[0])
    metadata = {
        "mode": params.mode,
        "parting_z_mm": z_split,
        "hardware": hardware_meta,
        "part": _mesh_stats(part),
        "block": {
            "bounds": block_bounds,
            "volume": float(np.prod(block_size)),
        },
        "halves": {name: _mesh_stats(h) for name, h in halves.items()},
        **extra_meta,
    }
    return MoldResult(ok=True, halves=halves, warnings=warnings, metadata=metadata)


def _demold_warnings(
    part: trimesh.Trimesh, z_split: float, params: MoldParams, warnings: list[str]
) -> None:
    """Warn about geometry that will fight demolding, per half.

    Undercuts mean the part mechanically locks in that half; low-draft walls
    (within min_draft_deg of vertical) release but drag and score — common
    molding practice wants 1-2° minimum draft on them.
    """
    report = analyze_undercuts(part, z_split, min_draft_deg=params.min_draft_deg)
    if params.mode == "compression":
        sides = (("female", report.bottom), ("male", report.top))
    else:
        side = report.bottom if params.keep_side == "below" else report.top
        sides = (("tool", side),)
    mitigation = (
        "rely on release agent, pry slots and jack screws"
        if params.mode == "compression"
        else "rely on release agent and wedges at the flange"
    )
    for name, half in sides:
        if half.undercut_area_mm2 > 0:
            warnings.append(
                f"demolding: {half.undercut_area_mm2:.0f} mm² of undercuts in the "
                f"{name} cavity — the part will mechanically lock; move the parting "
                "plane (see the suggested height) or reshape the part"
            )
        elif half.low_draft_area_mm2 > 0:
            warnings.append(
                f"demolding: {half.low_draft_area_mm2:.0f} mm² of walls in the "
                f"{name} cavity have <{params.min_draft_deg:g}° draft; the part "
                f"will drag on release — common practice is 1-2° minimum draft, or "
                f"{mitigation}"
            )


def _check_params(p: MoldParams) -> list[str]:
    errors = []
    if p.mode not in VALID_MODES:
        errors.append(f"mode must be one of {VALID_MODES}, got {p.mode!r}")
    if p.wall_margin_mm <= 0:
        errors.append(f"wall_margin_mm must be positive, got {p.wall_margin_mm:g}")
    if p.mode in ("open_layup", "prepreg") and p.keep_side not in VALID_SIDES:
        errors.append(f"keep_side must be one of {VALID_SIDES}, got {p.keep_side!r}")
    if p.mode == "prepreg":
        if p.trim_allowance_mm <= 0:
            errors.append(f"trim_allowance_mm must be positive, got {p.trim_allowance_mm:g}")
        if not 0 <= p.trim_kick_deg <= 15:
            errors.append(f"trim_kick_deg must be 0-15, got {p.trim_kick_deg:g}")
        if p.tape_line and p.tape_zone_mm <= 0:
            errors.append("tape_zone_mm must be positive")
        if p.flange_bolt_holes and (
            p.flange_hole_diameter_mm <= 0
            or not 20 <= p.flange_hole_spacing_mm <= 300
        ):
            errors.append(
                "flange_hole_diameter_mm must be positive and "
                "flange_hole_spacing_mm in 20-300"
            )
    if p.mode == "compression":
        if p.plunge_mm <= 0:
            errors.append(f"plunge_mm must be positive, got {p.plunge_mm:g}")
        if p.plug_border_mm < 2:
            errors.append(f"plug_border_mm must be at least 2, got {p.plug_border_mm:g}")
        if p.plug_clearance_mm < 0:
            errors.append("plug_clearance_mm must be non-negative")
        if not 0 <= p.plug_draft_deg <= 5:
            errors.append(f"plug_draft_deg must be 0-5, got {p.plug_draft_deg:g}")
        if p.lead_in_mm < 0:
            errors.append("lead_in_mm must be non-negative")
        if p.pry_slots and (p.pry_slot_width_mm <= 0 or p.pry_slot_depth_mm <= 0):
            errors.append("pry_slot_width_mm and pry_slot_depth_mm must be positive")
        if p.overflow_channels and (
            not 1 <= p.overflow_per_edge <= 4
            or p.overflow_width_mm <= 0
            or p.overflow_depth_mm <= 0
        ):
            errors.append(
                "overflow_per_edge must be 1-4 and overflow width/depth positive"
            )
        if p.jack_screws:
            if not 1 <= p.jack_screw_count <= 4:
                errors.append(f"jack_screw_count must be 1-4, got {p.jack_screw_count}")
            if p.jack_screw_diameter_mm <= 1:
                errors.append("jack_screw_diameter_mm must be greater than 1")
        if p.ejector_holes and p.ejector_diameter_mm <= 0:
            errors.append("ejector_diameter_mm must be positive")
    if p.demold_check and not 0 < p.min_draft_deg <= 10:
        errors.append(f"min_draft_deg must be in (0, 10], got {p.min_draft_deg:g}")
    if p.pins:
        if p.pin_radius_mm <= 0 or p.pin_height_mm <= 0:
            errors.append("pin_radius_mm and pin_height_mm must be positive")
        if p.pin_chamfer_mm < 0 or p.pin_clearance_mm < 0:
            errors.append("pin_chamfer_mm and pin_clearance_mm must be non-negative")
    if p.bolts:
        if not 1 <= p.bolt_count <= 12:
            errors.append(f"bolt_count must be 1-12, got {p.bolt_count}")
        if p.bolt_diameter_mm <= 0:
            errors.append(f"bolt_diameter_mm must be positive, got {p.bolt_diameter_mm:g}")
    if p.trim_groove and (p.trim_groove_offset_mm <= 0 or p.trim_groove_depth_mm <= 0):
        errors.append("trim_groove_offset_mm and trim_groove_depth_mm must be positive")
    return errors


def _nudged_parting_height(part: trimesh.Trimesh, z: float, warnings: list[str]) -> float:
    """Nudge the parting height when part facets are coplanar with the plane.

    A pocket floor or plane split exactly through coplanar facets is
    numerically ambiguous (the flat face and the cavity wall coincide). A
    ~1 µm nudge is physically invisible but keeps the geometry unambiguous.
    float32 (manifold's vertex type) resolves ~1e-7 relative, so 1e-3 mm is
    safely representable at part scale.
    """
    diag = float(np.linalg.norm(part.extents))
    tol = 1e-8 * max(diag, 1.0)
    eps = max(1e-3, 1e-6 * diag)
    verts_z = part.vertices[:, 2]

    z_out = z
    for _ in range(4):
        on_plane = np.abs(verts_z - z_out) <= tol
        if not np.all(on_plane[part.faces], axis=1).any():
            break
        z_out += eps
    if z_out != z:
        warnings.append(
            f"parting plane coincides with flat part facets; "
            f"split nudged to z={z_out:.6g} mm"
        )
    return z_out


def _mesh_stats(m: trimesh.Trimesh) -> dict:
    return {
        "volume": float(m.volume),
        "bounds": m.bounds.tolist(),
        "triangles": int(len(m.faces)),
        "watertight": bool(m.is_watertight),
    }
