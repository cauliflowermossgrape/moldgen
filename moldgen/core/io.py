"""Part loading and mold export."""

from __future__ import annotations

from pathlib import Path

import trimesh

SUPPORTED_SUFFIXES = {".stl", ".3mf", ".obj"}


def load_part(path: str | Path) -> trimesh.Trimesh:
    """Load a part mesh from an STL/3MF/OBJ file.

    Multi-body files (e.g. 3MF scenes) are concatenated into a single mesh.
    Raises ValueError for unsupported or empty files.
    """
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"unsupported file type {path.suffix!r}; "
            f"expected one of {sorted(SUPPORTED_SUFFIXES)}"
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
