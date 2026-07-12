"""First-order undercut analysis for a flat, horizontal parting plane.

Pull directions are fixed by the mold layout: the top half releases in +Z,
the bottom half in −Z. A part triangle molded by the bottom half (centroid
below the plane) must not face upward — a face normal with a positive Z
component beyond a small threshold means the bottom half wraps over it and
would lock during demolding. The logic mirrors for the top half.

This is the standard first-order screen and it is *incomplete*: it only looks
at each face's own normal. It does not catch silhouette-occluded undercuts,
where a face points the "right" way but other geometry above or below it
blocks the pull path (e.g. the bore of a tilted hole, or the inside of an
S-curve). A complete check needs visibility testing — casting rays along ±Z
from every face and checking for self-occlusion.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

DEFAULT_NORMAL_Z_THRESHOLD = 0.02  # ~1° past vertical; ignores equator jitter
# a half is "likely demoldable" when flagged area is negligible vs total area
DEMOLDABLE_AREA_FRACTION = 1e-6


@dataclass
class HalfUndercuts:
    undercut_area_mm2: float
    likely_demoldable: bool


@dataclass
class UndercutReport:
    parting_z_mm: float
    face_is_undercut: np.ndarray  # (n_faces,) bool, aligned with mesh.faces
    undercut_area_mm2: float
    bottom: HalfUndercuts  # faces below the plane, pulled in −Z
    top: HalfUndercuts  # faces above the plane, pulled in +Z


def _undercut_mask(
    centroid_z: np.ndarray, normal_z: np.ndarray, parting_z: float, threshold: float
) -> np.ndarray:
    below = centroid_z < parting_z
    return np.where(below, normal_z > threshold, normal_z < -threshold)


def analyze_undercuts(
    mesh: trimesh.Trimesh,
    parting_z_mm: float,
    normal_z_threshold: float = DEFAULT_NORMAL_Z_THRESHOLD,
) -> UndercutReport:
    """Classify every part triangle as undercut or not for the given plane.

    Triangles are assigned to a half by face centroid. See the module
    docstring for what this check does and does not catch.
    """
    normal_z = mesh.face_normals[:, 2]
    centroid_z = mesh.triangles_center[:, 2]
    areas = mesh.area_faces
    below = centroid_z < parting_z_mm
    flags = _undercut_mask(centroid_z, normal_z, parting_z_mm, normal_z_threshold)

    area_eps = DEMOLDABLE_AREA_FRACTION * float(mesh.area)
    bottom_area = float(areas[flags & below].sum())
    top_area = float(areas[flags & ~below].sum())
    return UndercutReport(
        parting_z_mm=float(parting_z_mm),
        face_is_undercut=flags,
        undercut_area_mm2=bottom_area + top_area,
        bottom=HalfUndercuts(bottom_area, bottom_area <= area_eps),
        top=HalfUndercuts(top_area, top_area <= area_eps),
    )


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
