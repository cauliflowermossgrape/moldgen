"""Part loading and mold export."""

from __future__ import annotations

from pathlib import Path

import trimesh

from . import step_io

MESH_SUFFIXES = {".stl", ".3mf", ".obj"}
# kept for backward compatibility; use supported_suffixes() for the live set
SUPPORTED_SUFFIXES = MESH_SUFFIXES


def supported_suffixes() -> set[str]:
    """File types loadable right now (CAD formats only with the step extra)."""
    if step_io.HAS_STEP:
        return MESH_SUFFIXES | step_io.CAD_SUFFIXES
    return set(MESH_SUFFIXES)


def load_part(
    path: str | Path,
    linear_deflection_mm: float = step_io.DEFAULT_LINEAR_DEFLECTION_MM,
    angular_deflection_deg: float = step_io.DEFAULT_ANGULAR_DEFLECTION_DEG,
) -> trimesh.Trimesh:
    """Load a part mesh from an STL/3MF/OBJ file, or STEP/IGES with the
    "step" extra installed (the deflection arguments only apply there).

    Multi-body files are concatenated into a single mesh. Raises ValueError
    for unsupported or empty files.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in step_io.CAD_SUFFIXES:
        return step_io.load_cad(path, linear_deflection_mm, angular_deflection_deg)
    if suffix not in MESH_SUFFIXES:
        raise ValueError(
            f"unsupported file type {suffix!r}; "
            f"expected one of {sorted(supported_suffixes())}"
        )
    loaded = trimesh.load(path, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise ValueError(f"{path.name} contains no triangle geometry")
    return loaded


def export_stl(mesh: trimesh.Trimesh, path: str | Path) -> Path:
    """Write `mesh` to `path` as binary STL."""
    path = Path(path)
    path.write_bytes(trimesh.exchange.stl.export_stl(mesh))
    return path
