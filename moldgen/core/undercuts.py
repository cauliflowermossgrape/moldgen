"""Demolding analysis for a flat, horizontal parting plane.

Pull directions are fixed by the mold layout: the male half releases in +Z,
the female half in −Z. Three things decide whether a part demolds:

- **Which half molds each face.** Normally the plane decides — below it the
  female, above it the male. Shell parts are the exception: inside a cup
  cut through its walls, the mold material filling the cup below the plane
  is a *core* sealed off from the female by the shell, so the construction
  hands it to the male (compression) or drops it (single-sided tools, where
  the inside is the bagged side). A face is in a core when its point
  reaches the parting plane vertically without crossing the part and lands
  inside a hole of the part's cross-section there.
- **Undercuts** — a face whose normal fights its half's pull direction. The
  half wraps over it and the part mechanically locks.
- **Occlusion** — a face pointing the right way, but with other part
  geometry in its pull path (the bore of a tilted hole, the inside of an
  S-curve, a ledge under an overhang). Each face casts a vertical ray along
  its pull direction; a hit is an undercut the per-face normal test misses.
- **Insufficient draft** — walls parallel to the pull direction don't lock,
  but they drag along the whole cavity surface during release, scoring the
  part and the tool. Common molding practice is a minimum of 1-2° of draft
  on such walls; faces within `min_draft_deg` of vertical are reported as
  low-draft area (this includes slightly *negative* draft that stays under
  the undercut threshold — worse than vertical).

Rays sample each face at its centroid, so a face only partly shadowed can
slip through; dense meshes make that irrelevant in practice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import trimesh
from manifold3d import CrossSection

from . import mfld
from .rays import PolygonContainment, VerticalRayCaster

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
    bottom: HalfUndercuts  # faces molded by the female / tool below (−Z pull)
    top: HalfUndercuts  # faces molded by the male / tool above (+Z pull)
    # faces whose normal is fine but whose pull path is blocked by the part
    # (included in face_is_undercut)
    face_is_occluded: np.ndarray | None = None
    # faces molded by a shell core (handed to the opposite half, or not
    # molded at all on a single-sided tool)
    face_is_core: np.ndarray | None = None
    # faces molded by the top (+Z) half; the rest of the molded faces belong
    # to the bottom half
    face_molded_by_top: np.ndarray | None = None
    # signed draft angle (degrees) of each face relative to the pull
    # direction of the half that molds it: positive releases, negative
    # fights the pull; NaN where no half molds the face
    face_draft_deg: np.ndarray | None = None


class PartVisibility:
    """Per-face vertical-ray data for one part, independent of the parting
    height — compute once, then evaluate any number of candidate planes.

    `up_t` / `down_t` are the distances from each face's sample point
    (centroid nudged just off the surface) to the first part hit along ±Z.
    """

    def __init__(self, mesh: trimesh.Trimesh):
        self.mesh = mesh
        self.normal_z = mesh.face_normals[:, 2]
        self.centroid_z = mesh.triangles_center[:, 2]
        self.areas = mesh.area_faces
        diag = float(np.linalg.norm(mesh.extents)) or 1.0
        eps = 1e-4 * diag
        self.points = mesh.triangles_center + mesh.face_normals * eps
        caster = VerticalRayCaster.from_mesh(mesh)
        tiny = 1e-7 * diag
        self.up_t = caster.first_hit(self.points, +1.0, tiny)
        self.down_t = caster.first_hit(self.points, -1.0, tiny)
        try:
            man = mfld.to_manifold(mesh)
            self._man = man if mfld.is_valid(man) else None
        except Exception:  # non-manifold input: no slicing, no core detection
            self._man = None

    def holes_contain(self, z: float, idx: np.ndarray) -> np.ndarray:
        """For face sample points `idx`: does their XY lie in a hole of the
        part's cross-section at height z (where a shell core meets the
        plane)?"""
        out = np.zeros(len(idx), dtype=bool)
        if self._man is None or len(idx) == 0:
            return out
        holes = _hole_contours(self._man.slice(float(z)))
        if not holes:
            return out
        return PolygonContainment(holes).contains(self.points[idx, :2])


def _hole_contours(cs: CrossSection) -> list[np.ndarray]:
    """Contours of the hole regions of a cross-section (where shell cores
    meet the plane), for even-odd containment; empty when there are none."""
    polys = cs.to_polygons()
    outers = [p for p in polys if mfld.signed_area(p) > 0]
    if len(outers) == len(polys):
        return []
    holes = CrossSection(outers) - cs
    if holes.area() <= 0:
        return []
    return [np.asarray(p) for p in holes.to_polygons()]


def _core_heuristic(
    vis: PartVisibility, z: float, threshold: float, single_sided: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Cheap core test for scanning many heights: the face's point reaches
    the plane without crossing the part, the column is closed by the part
    on the far side (so it isn't a through-hole), and the plane point lies
    in a cross-section hole. Returns (core, void) masks; void is always
    empty here.

    Only faces whose undercut verdict would actually change by being in a
    core get the (expensive) hole test — on a million-triangle scan that is
    a small fraction, and it keeps the parting scan interactive.
    """
    nz = vis.normal_z
    pz = vis.points[:, 2]
    below = vis.centroid_z < z
    blocked_up = np.isfinite(vis.up_t)
    blocked_down = np.isfinite(vis.down_t)
    clear = np.where(
        below,
        (vis.up_t > z - pz) & blocked_down,
        (vis.down_t > pz - z) & blocked_up,
    )
    if single_sided:
        # a core face is simply not molded: matters where it would lock
        matters = np.where(
            below,
            (nz > threshold) | ((nz < -threshold) & blocked_down),
            (nz < -threshold) | ((nz > threshold) & blocked_up),
        )
    else:
        # bottom vs top attribution differ only for unblocked steep faces
        matters = ((nz > threshold) & ~blocked_up) | ((nz < -threshold) & ~blocked_down)
    core = np.zeros(len(pz), dtype=bool)
    cand = np.flatnonzero(clear & matters)
    if len(cand):
        core[cand] = vis.holes_contain(z, cand)
    return core, np.zeros(len(pz), dtype=bool)


def _core_exact(vis: PartVisibility, z: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Core test by connectivity, matching what the mold construction does.

    On each side of the plane, the mold material around the part (a box
    minus the part) splits into connected bodies. The one touching the
    outer box is the half itself; a body that touches only the parting
    plane is a core sealed inside a shell; a body touching neither is an
    internal void of a hollow part, which nothing can mold. Returns
    (core, void) masks, or None when the part can't be sliced as a solid.
    """
    man = vis._man
    if man is None:
        return None
    n = len(vis.centroid_z)
    core = np.zeros(n, dtype=bool)
    void = np.zeros(n, dtype=bool)
    lo, hi = (np.asarray(b, dtype=float) for b in vis.mesh.bounds)
    pad = 1.0 + 1e-3 * float(np.linalg.norm(hi - lo))
    # manifold stores float32 (~6e-8 relative): scale the tolerance with the
    # coordinates so large tools far from the origin still compare equal
    tol = 1e-4 * pad + 1e-6 * float(np.abs(np.concatenate([lo, hi, [z]])).max())
    below = vis.centroid_z < z
    for side_mask, z0, z1 in ((below, lo[2] - pad, z), (~below, z, hi[2] + pad)):
        if z1 - z0 <= 0 or not side_mask.any():
            continue
        box_lo = np.array([lo[0] - pad, lo[1] - pad, z0])
        box_hi = np.array([hi[0] + pad, hi[1] + pad, z1])
        region = mfld.box(box_lo, box_hi) - man
        bodies = mfld.solid_bodies(region)
        if len(bodies) <= 1:
            continue
        idx = np.flatnonzero(side_mask)
        for body in bodies:
            bb = np.asarray(body.bounding_box(), dtype=float).reshape(2, 3)
            outer_touch = (
                np.any(bb[0, :2] <= box_lo[:2] + tol)
                or np.any(bb[1, :2] >= box_hi[:2] - tol)
                or (bb[0, 2] <= box_lo[2] + tol if z0 < z else bb[1, 2] >= box_hi[2] - tol)
            )
            if outer_touch:
                continue
            plane_touch = abs((bb[1, 2] if z0 < z else bb[0, 2]) - z) <= tol
            pts = vis.points[idx]
            in_bb = np.all((pts >= bb[0] - tol) & (pts <= bb[1] + tol), axis=1)
            cand = idx[in_bb]
            if len(cand) == 0:
                continue
            inside = VerticalRayCaster.from_mesh(mfld.to_trimesh(body)).contains(vis.points[cand])
            (core if plane_touch else void)[cand[inside]] = True
    return core, void


def _classify(
    vis: PartVisibility,
    z: float,
    threshold: float,
    single_sided: bool,
    exact: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(undercut, occluded, core, molded_by_top, molded) face masks at z."""
    nz = vis.normal_z
    below = vis.centroid_z < z
    cores = _core_exact(vis, z) if exact else None
    core, void = cores if cores is not None else _core_heuristic(vis, z, threshold, single_sided)

    if single_sided:
        molded = ~core & ~void
        top = ~below & molded
    else:
        molded = ~void
        top = (below == core) & molded  # above & not core, or below & core
    blocked_down = np.isfinite(vis.down_t)
    blocked_up = np.isfinite(vis.up_t)
    wrong = np.where(top, nz < -threshold, nz > threshold)
    occluded = np.where(top, (nz > threshold) & blocked_up, (nz < -threshold) & blocked_down)
    undercut = molded & (wrong | occluded)
    return undercut, molded & occluded & ~wrong, core, top, molded


def analyze_undercuts(
    mesh: trimesh.Trimesh,
    parting_z_mm: float,
    normal_z_threshold: float = DEFAULT_NORMAL_Z_THRESHOLD,
    min_draft_deg: float = DEFAULT_MIN_DRAFT_DEG,
    single_sided: bool = False,
    visibility: PartVisibility | None = None,
) -> UndercutReport:
    """Classify every part triangle as undercut / low-draft / fine.

    `single_sided`: a laminating / open-layup tool — shell cores are
    dropped, so the faces they would mold aren't molded at all (the bagged
    side). Pass a precomputed `visibility` to skip the ray casting.
    """
    vis = visibility if visibility is not None else PartVisibility(mesh)
    z = float(parting_z_mm)
    undercut, occluded, core, top, molded = _classify(
        vis, z, normal_z_threshold, single_sided, exact=True
    )
    low_draft = molded & ~undercut & (np.abs(vis.normal_z) < math.sin(math.radians(min_draft_deg)))
    areas = vis.areas
    area_eps = DEMOLDABLE_AREA_FRACTION * float(areas.sum())

    def half(mask):
        u = float(areas[undercut & mask].sum())
        drag = float(areas[low_draft & mask].sum())
        return HalfUndercuts(u, drag, u <= area_eps)

    # draft = angle between the face and the pull direction; the molding
    # half moves along −Z (bottom) or +Z (top), so the face must face that way
    toward = np.where(top, vis.normal_z, -vis.normal_z)
    draft = np.degrees(np.arcsin(np.clip(toward, -1.0, 1.0)))
    draft[~molded] = np.nan
    return UndercutReport(
        parting_z_mm=z,
        min_draft_deg=float(min_draft_deg),
        face_is_undercut=undercut,
        face_is_low_draft=low_draft,
        undercut_area_mm2=float(areas[undercut].sum()),
        bottom=half(molded & ~top),
        top=half(top),
        face_is_occluded=occluded,
        face_is_core=core,
        face_molded_by_top=top,
        face_draft_deg=draft,
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


@dataclass
class PartingProfile:
    """Undercut area per candidate parting height (for the UI chart)."""

    z: np.ndarray
    bottom_mm2: np.ndarray  # undercut area molded by the bottom half
    top_mm2: np.ndarray


def parting_profile(
    mesh: trimesh.Trimesh,
    samples: int = 60,
    normal_z_threshold: float = DEFAULT_NORMAL_Z_THRESHOLD,
    single_sided: bool = False,
    visibility: PartVisibility | None = None,
    z_range: tuple[float, float] | None = None,
) -> PartingProfile:
    """Evaluate undercut area at `samples` heights — bin centers across the
    part's z-range (or `z_range`), so no candidate degenerates to an empty
    half."""
    vis = visibility if visibility is not None else PartVisibility(mesh)
    lo, hi = z_range if z_range is not None else tuple(mesh.bounds[:, 2])
    z = lo + (np.arange(samples) + 0.5) / samples * (hi - lo)
    bottom = np.zeros(samples)
    top = np.zeros(samples)
    for i, zi in enumerate(z):
        undercut, _, _, is_top, molded = _classify(vis, float(zi), normal_z_threshold, single_sided)
        bottom[i] = vis.areas[undercut & molded & ~is_top].sum()
        top[i] = vis.areas[undercut & is_top].sum()
    return PartingProfile(z=z, bottom_mm2=bottom, top_mm2=top)


def _pick(z: np.ndarray, cost: np.ndarray, tol: float, prefer: str) -> tuple[float, int]:
    """Choose among near-minimal candidates: the middle of the longest tied
    run ("middle"), or the highest / lowest tied height."""
    tied = cost <= cost.min() + tol
    if prefer == "highest":
        i = int(np.flatnonzero(tied)[-1])
        return float(z[i]), i
    if prefer == "lowest":
        i = int(np.flatnonzero(tied)[0])
        return float(z[i]), i
    best, run_start, best_span = 0, None, (-1, 0, 0)
    for i, t in enumerate(np.append(tied, False)):
        if t and run_start is None:
            run_start = i
        elif not t and run_start is not None:
            length = i - run_start
            if length > best_span[0]:
                best_span = (length, run_start, i - 1)
            run_start = None
    _, a, b = best_span
    mid = (z[a] + z[b]) / 2.0
    best = int(np.argmin(np.abs(z - mid)))
    return float(mid), best


def suggest_parting_plane(
    mesh: trimesh.Trimesh,
    samples: int = 50,
    normal_z_threshold: float = DEFAULT_NORMAL_Z_THRESHOLD,
    side: str | None = None,
    visibility: PartVisibility | None = None,
    coarse: PartingProfile | None = None,
) -> float:
    """Parting height minimizing undercut area.

    side=None (two-part mold): minimizes total undercut area. Where several
    heights tie (straight walls — a plate, a cylinder) it takes the middle of
    the tied band, splitting the drag evenly between the halves.
    side="below" / "above" (single-sided tool on that side): only that
    tool's faces count, and ties go to the plane that covers the most part
    (highest for a tool below, lowest above) — the tool should mold as much
    of the part as releases.

    A coarse scan over bin centers is followed by a fine scan around the
    winner, so the result isn't quantized to the coarse bin width. Pass a
    precomputed `coarse` profile (matching `side`'s single/two-part mode) to
    skip the coarse scan.
    """
    vis = visibility if visibility is not None else PartVisibility(mesh)
    single = side in ("below", "above")
    prefer = {"below": "highest", "above": "lowest"}.get(side, "middle")
    z_min, z_max = (float(v) for v in mesh.bounds[:, 2])
    span = z_max - z_min
    if span <= 0:
        return z_min
    tol = max(1e-4 * float(vis.areas.sum()), 1e-9)

    def cost(profile):
        if side == "below":
            return profile.bottom_mm2
        if side == "above":
            return profile.top_mm2
        return profile.bottom_mm2 + profile.top_mm2

    if coarse is None:
        coarse = parting_profile(mesh, samples, normal_z_threshold, single, vis)
    z0, i0 = _pick(coarse.z, cost(coarse), tol, prefer)
    if prefer == "middle" and z0 != coarse.z[i0]:
        return z0  # midpoint of a wide tied band; refining would only shift it
    step = span / len(coarse.z)
    margin = 1e-3 * span
    lo = max(z_min + margin, coarse.z[i0] - step)
    hi = min(z_max - margin, coarse.z[i0] + step)
    fine = parting_profile(mesh, 21, normal_z_threshold, single, vis, z_range=(lo, hi))
    fine_cost = cost(fine)
    if fine_cost.min() > cost(coarse)[i0] + tol:
        return z0
    z1, _ = _pick(fine.z, fine_cost, tol, prefer)
    return z1


def parting_summary(
    mesh: trimesh.Trimesh,
    samples: int = 60,
    visibility: PartVisibility | None = None,
) -> dict:
    """Everything the UI needs about parting heights, sharing one coarse
    scan per mold family: the undercut profiles (two-part total, tool
    below, tool above) and the suggested height for each."""
    vis = visibility if visibility is not None else PartVisibility(mesh)
    two = parting_profile(mesh, samples, visibility=vis)
    single = parting_profile(mesh, samples, single_sided=True, visibility=vis)
    return {
        "profile": {
            "z": two.z.tolist(),
            "two_part": (two.bottom_mm2 + two.top_mm2).tolist(),
            "below": single.bottom_mm2.tolist(),
            "above": single.top_mm2.tolist(),
        },
        "suggested": {
            "two_part": suggest_parting_plane(mesh, visibility=vis, coarse=two),
            "below": suggest_parting_plane(mesh, side="below", visibility=vis, coarse=single),
            "above": suggest_parting_plane(mesh, side="above", visibility=vis, coarse=single),
        },
    }
