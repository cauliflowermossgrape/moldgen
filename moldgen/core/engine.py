"""Mold-generation pipeline: validate part → block → subtract → split.

All boolean work happens in manifold3d (watertight-guaranteed); trimesh is
used for analysis, repair and I/O. Units are millimeters. The parting plane
is horizontal (normal +Z) at z = params.parting_z_mm in the part's own frame.
"""

from __future__ import annotations

import numpy as np
import trimesh

from . import hardware, mfld
from .offset import offset_mesh
from .types import MoldParams, MoldResult
from .validate import validate_and_repair

VALID_MODES = ("compression", "open_layup")
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
    block_min = bounds[0] - params.wall_margin_mm
    block_max = bounds[1] + params.wall_margin_mm
    z = float(params.parting_z_mm)
    if not (block_min[2] < z < block_max[2]):
        return MoldResult(
            ok=False,
            errors=[
                f"parting_z_mm={z:g} lies outside the mold block "
                f"(z from {block_min[2]:g} to {block_max[2]:g})"
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

    mold = mfld.box(block_min, block_max) - part_man

    z_split = _nudged_parting_height(part, z, warnings)

    hardware_meta: dict = {}
    pegs = pin_holes = None
    if params.mode == "compression":
        if params.trim_groove:
            warnings.append("trim groove applies only to open-layup tools; ignored")
        if params.pins:
            pegs, pin_holes, pin_info = hardware.registration_pins(
                block_min, block_max, z_split, params, warnings
            )
            if pin_info is not None:
                hardware_meta["pins"] = pin_info
        if params.bolts:
            bolt_cyls, bolt_info = hardware.bolt_holes(
                block_min, block_max, params, hardware_meta.get("pins"), warnings
            )
            if bolt_cyls is not None:
                mold = mold - bolt_cyls
                hardware_meta["bolts"] = bolt_info
    elif params.bolts:
        warnings.append("bolt holes apply only to compression molds; ignored")

    half_a, half_b = mold.split_by_plane((0.0, 0.0, 1.0), z_split)
    if half_a.is_empty() or half_b.is_empty():
        return MoldResult(
            ok=False,
            errors=["splitting at the parting plane produced an empty half"],
            warnings=warnings,
        )
    # Assign top/bottom by measured position rather than trusting the
    # split_by_plane ordering convention.
    z_a = float(np.mean(half_a.to_mesh().vert_properties[:, 2]))
    z_b = float(np.mean(half_b.to_mesh().vert_properties[:, 2]))
    top_man, bottom_man = (half_a, half_b) if z_a >= z_b else (half_b, half_a)

    if pegs is not None:
        bottom_man = bottom_man + pegs
        top_man = top_man - pin_holes

    if params.mode == "open_layup":
        keep_man = top_man if params.keep_side == "above" else bottom_man
        if params.trim_groove:
            groove, groove_info = hardware.trim_groove_solid(
                bounds, block_min, block_max, z_split, params, warnings
            )
            if groove is not None:
                keep_man = keep_man - groove
                hardware_meta["trim_groove"] = groove_info
        halves = {"tool": mfld.to_trimesh(keep_man)}
    else:
        halves = {"top": mfld.to_trimesh(top_man), "bottom": mfld.to_trimesh(bottom_man)}

    metadata = {
        "mode": params.mode,
        "parting_z_mm": z_split,
        "hardware": hardware_meta,
        "part": _mesh_stats(part),
        "block": {
            "bounds": [block_min.tolist(), block_max.tolist()],
            "volume": float(np.prod(block_max - block_min)),
        },
        "halves": {name: _mesh_stats(h) for name, h in halves.items()},
    }
    return MoldResult(ok=True, halves=halves, warnings=warnings, metadata=metadata)


def _check_params(p: MoldParams) -> list[str]:
    errors = []
    if p.mode not in VALID_MODES:
        errors.append(f"mode must be one of {VALID_MODES}, got {p.mode!r}")
    if p.wall_margin_mm <= 0:
        errors.append(f"wall_margin_mm must be positive, got {p.wall_margin_mm:g}")
    if p.mode == "open_layup" and p.keep_side not in VALID_SIDES:
        errors.append(f"keep_side must be one of {VALID_SIDES}, got {p.keep_side!r}")
    if p.pins:
        if p.pin_radius_mm <= 0 or p.pin_height_mm <= 0:
            errors.append("pin_radius_mm and pin_height_mm must be positive")
        if p.pin_chamfer_mm < 0 or p.pin_clearance_mm < 0:
            errors.append("pin_chamfer_mm and pin_clearance_mm must be non-negative")
    if p.bolts:
        if not 1 <= p.bolt_count <= 4:
            errors.append(
                f"bolt_count must be 1-4 (holes sit midway along the "
                f"parting-face edges), got {p.bolt_count}"
            )
        if p.bolt_diameter_mm <= 0:
            errors.append(f"bolt_diameter_mm must be positive, got {p.bolt_diameter_mm:g}")
    if p.trim_groove and (p.trim_groove_offset_mm <= 0 or p.trim_groove_depth_mm <= 0):
        errors.append("trim_groove_offset_mm and trim_groove_depth_mm must be positive")
    return errors


def _nudged_parting_height(part: trimesh.Trimesh, z: float, warnings: list[str]) -> float:
    """Nudge the split height when part facets are coplanar with the plane.

    A split exactly through coplanar facets is numerically ambiguous (the flat
    cap and the cavity wall coincide). A ~1 µm nudge is physically invisible
    but keeps the split unambiguous. float32 (manifold's vertex type) resolves
    ~1e-7 relative, so 1e-3 mm is safely representable at part scale.
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
