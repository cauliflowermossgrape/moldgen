"""Mold-generation pipeline: validate part → block → subtract → split.

All boolean work happens in manifold3d (watertight-guaranteed); trimesh is
used for analysis, repair and I/O. Units are millimeters. The parting plane
is horizontal (normal +Z) at z = params.parting_z_mm in the part's own frame.
"""

from __future__ import annotations

import numpy as np
import trimesh

from . import mfld
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
    half_a, half_b = mold.split_by_plane((0.0, 0.0, 1.0), z_split)
    mesh_a, mesh_b = mfld.to_trimesh(half_a), mfld.to_trimesh(half_b)
    if mesh_a.is_empty or mesh_b.is_empty:
        return MoldResult(
            ok=False,
            errors=["splitting at the parting plane produced an empty half"],
            warnings=warnings,
        )
    # Assign top/bottom by measured position rather than trusting the
    # split_by_plane ordering convention.
    if mesh_a.centroid[2] >= mesh_b.centroid[2]:
        top, bottom = mesh_a, mesh_b
    else:
        top, bottom = mesh_b, mesh_a

    if params.mode == "open_layup":
        halves = {"tool": top if params.keep_side == "above" else bottom}
    else:
        halves = {"top": top, "bottom": bottom}

    metadata = {
        "mode": params.mode,
        "parting_z_mm": z_split,
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
