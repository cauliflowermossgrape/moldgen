"""Conversions between trimesh and manifold3d.

All boolean/split operations in moldgen go through manifold3d, which
guarantees watertight output. manifold3d stores vertices as float32, so a
round trip loses a little precision (~1e-7 relative); keep that in mind when
comparing volumes.
"""

from __future__ import annotations

import numpy as np
import trimesh
from manifold3d import CrossSection, Manifold, Mesh
from scipy.spatial import cKDTree


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


def signed_area(poly) -> float:
    """Shoelace area of a 2-D polygon: positive for counter-clockwise
    (manifold's outer contours), negative for clockwise (holes)."""
    p = np.asarray(poly, dtype=float)
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


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


# cap on a miter's length (in offset units) at needle-sharp corners
MAX_MITER = 4.0
# outline cleanup tolerance (mm) — far below anything physical
OUTLINE_EPS = 1e-5


def drafted_extrude(cs: CrossSection, height: float, grow: float) -> Manifold:
    """Extrude `cs` up `height` from z=0 with every wall leaning outward so
    the top outline is `cs` offset by exactly `grow` (negative leans in).

    manifold3d's extrude only scales the top, which about a center is a
    true offset only for outlines equidistant from it — an L-shaped
    outline's inner edges get no draft at all. Here the straight extrusion's
    top ring is warped along per-vertex miter vectors, which move both
    adjacent edges by exactly `grow`, so every wall gets the same draft
    angle and stays planar. Miters are capped at MAX_MITER x `grow` at
    needle-sharp corners. Keep `grow` small next to the outline's features:
    like any offset, large values self-intersect at concave detail.
    """
    # Clipper offsets can emit points nanometres apart, which extrude turns
    # into distinct vertices at the same position (non-manifold once a
    # slicer welds the STL); dropping them also drops exactly collinear
    # points, which slices of quad-tessellated meshes are full of
    cs = cs.simplify(OUTLINE_EPS)
    man = Manifold.extrude(cs, float(height))
    if grow == 0:
        return man
    pts, miters = [], []
    for poly in cs.to_polygons():
        p = np.asarray(poly, dtype=np.float64)
        if len(p) < 3:
            continue
        e_in = p - np.roll(p, 1, axis=0)
        e_out = np.roll(p, -1, axis=0) - p
        # right-hand normals point away from the material for both outer
        # (CCW) contours and holes (CW)
        n_in = np.column_stack([e_in[:, 1], -e_in[:, 0]])
        n_out = np.column_stack([e_out[:, 1], -e_out[:, 0]])
        n_in /= np.maximum(np.linalg.norm(n_in, axis=1, keepdims=True), 1e-300)
        n_out /= np.maximum(np.linalg.norm(n_out, axis=1, keepdims=True), 1e-300)
        denom = np.maximum(1.0 + np.einsum("ij,ij->i", n_in, n_out), 1e-9)
        m = (n_in + n_out) / denom[:, None]
        length = np.linalg.norm(m, axis=1)
        over = length > MAX_MITER
        m[over] *= (MAX_MITER / length[over])[:, None]
        pts.append(p)
        miters.append(m)
    if not pts:
        return man
    tree = cKDTree(np.concatenate(pts))
    miter = np.concatenate(miters) * float(grow)
    h = float(height)

    def warp(v: np.ndarray) -> np.ndarray:
        out = np.array(v, dtype=np.float64, copy=True)
        top = out[:, 2] > 0.5 * h
        if top.any():
            _, idx = tree.query(out[top, :2])
            out[top, :2] += miter[idx]
        return out

    return man.warp_batch(warp)
