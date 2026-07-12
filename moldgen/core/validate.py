"""Input mesh validation and best-effort repair.

A part is usable when trimesh's `is_volume` holds: watertight, consistent
winding, outward normals, positive volume. Anything less and booleans against
it are meaningless, so generate_mold refuses with a structured error.
"""

from __future__ import annotations

import trimesh


def validate_and_repair(
    mesh: trimesh.Trimesh,
) -> tuple[trimesh.Trimesh, list[str], list[str]]:
    """Return (mesh, issues, notes).

    `issues` is empty when the (possibly repaired) mesh is a valid solid;
    otherwise it lists what is wrong. `notes` records any repair performed.
    """
    m = mesh.copy()
    notes: list[str] = []

    m.process(validate=True)
    m.remove_unreferenced_vertices()
    if m.is_volume:
        return m, [], notes

    original = describe_issues(m)
    try:
        trimesh.repair.fix_normals(m)
        if not m.is_watertight:
            trimesh.repair.fill_holes(m)
            trimesh.repair.fix_normals(m)
    except Exception as exc:  # repair heuristics can fail on garbage input
        return m, original + [f"repair attempt failed: {exc}"], notes

    if m.is_volume:
        notes.append("input mesh was repaired: " + "; ".join(original))
        return m, [], notes
    return m, describe_issues(m), notes


def describe_issues(m: trimesh.Trimesh) -> list[str]:
    issues = []
    if not m.is_watertight:
        issues.append("mesh is not watertight (open boundary edges / holes)")
    if not m.is_winding_consistent:
        issues.append("triangle winding is inconsistent")
    if m.is_watertight and m.is_winding_consistent and m.volume <= 0:
        issues.append(f"enclosed volume is non-positive ({m.volume:.4g} mm³)")
    return issues or ["mesh is not a usable solid"]
