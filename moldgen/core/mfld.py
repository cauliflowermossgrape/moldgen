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


def solid_bodies(man: Manifold) -> list[Manifold]:
    """Connected solid bodies of `man`, each with its internal voids.

    Manifold.decompose() returns every connected surface separately, so an
    internal void comes back as its own inside-out shell (negative volume).
    Each void is re-attached to the smallest body whose bounding box holds
    it, so callers can reason about whole solids.
    """
    parts = man.decompose()
    if len(parts) <= 1:
        return parts
    solids = [p for p in parts if p.volume() > 0]
    voids = [p for p in parts if p.volume() <= 0]
    if not solids:
        return parts
    boxes = [np.asarray(p.bounding_box(), dtype=float).reshape(2, 3) for p in solids]
    groups: list[list[Manifold]] = [[p] for p in solids]
    for v in voids:
        vb = np.asarray(v.bounding_box(), dtype=float).reshape(2, 3)
        holders = [
            i
            for i, b in enumerate(boxes)
            if np.all(b[0] <= vb[0] + 1e-6) and np.all(b[1] >= vb[1] - 1e-6)
        ]
        i = (
            min(holders, key=lambda k: solids[k].volume())
            if holders
            else int(np.argmax([p.volume() for p in solids]))
        )
        groups[i].append(v)
    return [g[0] if len(g) == 1 else Manifold.compose(g) for g in groups]


def keep_main_body(man: Manifold) -> tuple[Manifold, int]:
    """Largest connected solid of `man`, plus how many others were dropped
    (shell interiors or bodies sealed off from the main one)."""
    bodies = solid_bodies(man)
    if len(bodies) <= 1:
        return man, 0
    bodies.sort(key=lambda b: b.volume(), reverse=True)
    return bodies[0], len(bodies) - 1
