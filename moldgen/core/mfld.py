"""Conversions between trimesh and manifold3d.

All boolean/split operations in moldgen go through manifold3d, which
guarantees watertight output. manifold3d stores vertices as float32, so a
round trip loses a little precision (~1e-7 relative); keep that in mind when
comparing volumes.
"""

from __future__ import annotations

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh


def to_manifold(mesh: trimesh.Trimesh) -> Manifold:
    return Manifold(
        Mesh(
            vert_properties=np.ascontiguousarray(mesh.vertices, dtype=np.float32),
            tri_verts=np.ascontiguousarray(mesh.faces, dtype=np.uint32),
        )
    )


def to_trimesh(man: Manifold) -> trimesh.Trimesh:
    mesh = man.to_mesh()
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vert_properties[:, :3], dtype=np.float64),
        faces=np.asarray(mesh.tri_verts, dtype=np.int64),
        process=False,
    )


def box(bounds_min, bounds_max) -> Manifold:
    """Axis-aligned solid box spanning [bounds_min, bounds_max]."""
    bounds_min = np.asarray(bounds_min, dtype=np.float64)
    size = np.asarray(bounds_max, dtype=np.float64) - bounds_min
    return Manifold.cube(size.tolist()).translate(bounds_min.tolist())


def is_valid(man: Manifold) -> bool:
    return man.status().name in ("NoError", "NO_ERROR") and not man.is_empty()
