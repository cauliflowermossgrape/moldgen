"""Approximate uniform surface offset."""

from __future__ import annotations

import numpy as np
import trimesh

# eigenvalues below this fraction of the largest are treated as zero: the
# incident face normals don't constrain that direction (smooth surface / edge)
_RANK_TOL = 0.05
# cap on |displacement| / |offset| at needle-sharp vertices
_MAX_STRETCH = 3.0


def offset_mesh(mesh: trimesh.Trimesh, offset_mm: float) -> trimesh.Trimesh:
    """Offset the surface by moving each vertex so that every incident face
    plane moves by `offset_mm` along its normal.

    Per vertex this solves the least-squares system n_i · d = offset over
    the incident face normals n_i (area-weighted). On flat regions it moves
    the vertex along the normal; on a sharp edge or corner it finds the
    displacement that keeps *all* incident faces at the full offset — a
    cube offset by 1 mm grows by exactly 1 mm per face, where plain vertex
    normals would move its faces only 1/√3 mm. Directions the normals don't
    constrain (along an edge, tangent to a smooth surface) get no motion.
    Displacements are capped at 3x the offset for needle-sharp vertices.

    Self-intersection risk: the offset surface self-intersects wherever
    |offset_mm| exceeds the local concave feature radius (positive offsets) or
    half the local wall thickness (negative offsets). The topology stays
    manifold so downstream booleans won't crash, but the resulting cavity can
    be wrong in those regions — keep offsets small relative to the part's
    smallest features.
    """
    out = mesh.copy()
    d = float(offset_mm)
    if d == 0 or len(mesh.faces) == 0:
        return out

    normals = mesh.face_normals
    weights = mesh.area_faces
    n_verts = len(mesh.vertices)
    # M_v = Σ w n nᵀ, b_v = Σ w n d over faces incident to v
    outer = weights[:, None, None] * normals[:, :, None] * normals[:, None, :]
    M = np.zeros((n_verts, 3, 3))
    b = np.zeros((n_verts, 3))
    for k in range(3):
        np.add.at(M, mesh.faces[:, k], outer)
        np.add.at(b, mesh.faces[:, k], weights[:, None] * normals * d)

    vals, vecs = np.linalg.eigh(M)  # ascending eigenvalues
    top = vals[:, -1:]
    keep = vals > _RANK_TOL * np.maximum(top, 1e-300)
    proj = np.einsum("nij,ni->nj", vecs, b)  # b in the eigenbasis
    coef = np.where(keep, proj / np.where(keep, vals, 1.0), 0.0)
    disp = np.einsum("nij,nj->ni", vecs, coef)

    length = np.linalg.norm(disp, axis=1)
    cap = _MAX_STRETCH * abs(d)
    over = length > cap
    disp[over] *= (cap / length[over])[:, None]
    # isolated / degenerate vertices: fall back to the vertex normal
    lost = ~np.isfinite(disp).all(axis=1) | (vals[:, -1] <= 0)
    disp[lost] = mesh.vertex_normals[lost] * d

    out.vertices = mesh.vertices + disp
    return out
