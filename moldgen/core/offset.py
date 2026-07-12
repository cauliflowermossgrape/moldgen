"""Approximate uniform surface offset."""

from __future__ import annotations

import trimesh


def offset_mesh(mesh: trimesh.Trimesh, offset_mm: float) -> trimesh.Trimesh:
    """Offset the surface by displacing vertices along their angle-weighted
    vertex normals.

    This is an approximation, exact only in the limit of a smooth surface:
    sharp convex corners are displaced less than `offset_mm` measured
    face-to-face, and concave ones more.

    Self-intersection risk: the offset surface self-intersects wherever
    |offset_mm| exceeds the local concave feature radius (positive offsets) or
    half the local wall thickness (negative offsets). The topology stays
    manifold so downstream booleans won't crash, but the resulting cavity can
    be wrong in those regions — keep offsets small relative to the part's
    smallest features. manifold3d has no true 3-D mesh offset today; if it
    gains one, prefer it over this.
    """
    out = mesh.copy()
    out.vertices = out.vertices + out.vertex_normals * float(offset_mm)
    return out
