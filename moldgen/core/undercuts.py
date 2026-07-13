"""Demolding analysis for a flat, horizontal parting plane.

Pull directions are fixed by the mold layout: the male half releases in +Z,
the female half in −Z. Two things stop a part from demolding:

- **Undercuts** — a face whose normal fights its half's pull direction. The
  half wraps over it and the part mechanically locks. A part triangle molded
  by the female half (centroid below the plane) must not face upward beyond
  a small threshold; mirrored for the male half.
- **Insufficient draft** — walls parallel to the pull direction don't lock,
  but they drag along the whole cavity surface during release, scoring the
  part and the tool. Common molding practice is a minimum of 1-2° of draft
  on such walls; faces within `min_draft_deg` of vertical are reported as
  low-draft area (this includes slightly *negative* draft that stays under
  the undercut threshold — worse than vertical).

This is the standard first-order screen and it is *incomplete*: it only
looks at each face's own normal. It does not catch silhouette-occluded
undercuts, where a face points the "right" way but other geometry above or
below it blocks the pull path (e.g. the bore of a tilted hole, or the
inside of an S-curve). A complete check needs visibility testing — casting
rays along ±Z from every face and checking for self-occlusion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import trimesh

DEFAULT_NORMAL_Z_THRESHOLD = 0.02  # ~1° past vertical; ignores equator jitter
DEFAULT_MIN_DRAFT_DEG = 1.0  # common practice: 1-2° minimum wall draft
# a half is "likely demoldable" when flagged area is negligible vs total area
DEMOLDABLE_AREA_FRACTION = 1e-6


@dataclass
class HalfUndercuts:
    undercut_area_mm2: float
    low_draft_area_mm2: float
    likely_demoldable: bool


@dataclass
class UndercutReport:
    parting_z_mm: float
    min_draft_deg: float
    face_is_undercut: np.ndarray  # (n_faces,) bool, aligned with mesh.faces
    face_is_low_draft: np.ndarray  # (n_faces,) bool; excludes undercut faces
    undercut_area_mm2: float
    bottom: HalfUndercuts  # faces below the plane, molded by the female (−Z pull)
    top: HalfUndercuts  # faces above the plane, molded by the male (+Z pull)


def _undercut_mask(
    centroid_z: np.ndarray, normal_z: np.ndarray, parting_z: float, threshold: float
) -> np.ndarray:
    below = centroid_z < parting_z
    return np.where(below, normal_z > threshold, normal_z < -threshold)


def analyze_undercuts(
    mesh: trimesh.Trimesh,
    parting_z_mm: float,
    normal_z_threshold: float = DEFAULT_NORMAL_Z_THRESHOLD,
    min_draft_deg: float = DEFAULT_MIN_DRAFT_DEG,
) -> UndercutReport:
    """Classify every part triangle as undercut / low-draft / fine.

    Triangles are assigned to a half by face centroid. See the module
    docstring for what this check does and does not catch.
    """
    normal_z = mesh.face_normals[:, 2]
    centroid_z = mesh.triangles_center[:, 2]
    areas = mesh.area_faces
    below = centroid_z < parting_z_mm
    flags = _undercut_mask(centroid_z, normal_z, parting_z_mm, normal_z_threshold)
    low_draft = ~flags & (np.abs(normal_z) < math.sin(math.radians(min_draft_deg)))

    area_eps = DEMOLDABLE_AREA_FRACTION * float(mesh.area)

    def half(mask):
        undercut = float(areas[flags & mask].sum())
        drag = float(areas[low_draft & mask].sum())
        return HalfUndercuts(undercut, drag, undercut <= area_eps)

    return UndercutReport(
        parting_z_mm=float(parting_z_mm),
        min_draft_deg=float(min_draft_deg),
        face_is_undercut=flags,
        face_is_low_draft=low_draft,
        undercut_area_mm2=float(areas[flags].sum()),
        bottom=half(below),
        top=half(~below),
    )


def sharp_edge_length_mm(
    mesh: trimesh.Trimesh,
    parting_z_mm: float,
    side: str = "below",
    angle_deg: float = 45.0,
) -> float:
    """Total length of sharp part edges on one side of the parting plane.

    Fabric-drape heuristic for laminating tools: sharp convex part edges
    become tight internal mold corners that prepreg fabric bridges over;
    sharp concave ones become knife-edge mold ridges (resin-rich, fragile).
    Easy Composites recommend generous fillets — both kinds are counted.
    """
    angles = mesh.face_adjacency_angles
    sharp = angles > math.radians(angle_deg)
    edges = mesh.face_adjacency_edges[sharp]
    if len(edges) == 0:
        return 0.0
    points = mesh.vertices[edges]  # (n, 2, 3)
    mid_z = points[:, :, 2].mean(axis=1)
    mask = mid_z < parting_z_mm if side == "below" else mid_z > parting_z_mm
    return float(np.linalg.norm(points[mask, 1] - points[mask, 0], axis=1).sum())


def suggest_parting_plane(
    mesh: trimesh.Trimesh,
    samples: int = 50,
    normal_z_threshold: float = DEFAULT_NORMAL_Z_THRESHOLD,
) -> float:
    """Parting height minimizing total undercut area.

    Evaluates `samples` candidate heights at bin centers across the part's
    z-range (bin centers, so no candidate degenerates to an empty half) and
    returns the best one. Ties go to the lowest height.
    """
    z_min, z_max = mesh.bounds[:, 2]
    normal_z = mesh.face_normals[:, 2]
    centroid_z = mesh.triangles_center[:, 2]
    areas = mesh.area_faces

    candidates = z_min + (np.arange(samples) + 0.5) / samples * (z_max - z_min)
    totals = [
        float(areas[_undercut_mask(centroid_z, normal_z, z, normal_z_threshold)].sum())
        for z in candidates
    ]
    return float(candidates[int(np.argmin(totals))])
