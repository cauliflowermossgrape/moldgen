"""Undercut analysis tests: sphere (clean at equator) and mushroom (cap
underside is an undercut for any parting plane below the cap)."""

import numpy as np
import pytest
import trimesh

from moldgen.core import analyze_undercuts, suggest_parting_plane
from moldgen.core import mfld


def mushroom():
    """Stem r=5 from z=0..20, cap r=15 from z=20..25.

    The cap's underside annulus (area π(15²−5²), facing −Z at z=20) is an
    undercut for every parting height below 20; between 20 and 25 (the widest
    section) there are no undercuts at all.
    """
    stem = trimesh.creation.cylinder(radius=5.0, height=20.0, sections=64)
    stem.apply_translation([0, 0, 10.0])
    cap = trimesh.creation.cylinder(radius=15.0, height=5.0, sections=64)
    cap.apply_translation([0, 0, 22.5])
    return mfld.to_trimesh(mfld.to_manifold(stem) + mfld.to_manifold(cap))


def test_low_draft_walls_flagged():
    # a plain cylinder: no undercuts anywhere, but its side wall is exactly
    # vertical — all of it should count as low-draft drag area
    cyl = trimesh.creation.cylinder(radius=10.0, height=30.0, sections=64)
    report = analyze_undercuts(cyl, parting_z_mm=0.0)
    assert report.undercut_area_mm2 == 0.0
    assert report.bottom.likely_demoldable and report.top.likely_demoldable

    # lateral surface splits evenly across the parting plane (discretized
    # circle, hence the polygon perimeter, but rel tolerance absorbs that)
    lateral_half = np.pi * 2 * 10.0 * 30.0 / 2
    assert report.bottom.low_draft_area_mm2 == pytest.approx(lateral_half, rel=0.01)
    assert report.top.low_draft_area_mm2 == pytest.approx(lateral_half, rel=0.01)
    # flagged faces are exactly the vertical ones
    flagged = report.face_is_low_draft
    assert np.all(np.abs(cyl.face_normals[flagged][:, 2]) < 0.02)

    # a tapered cone releases: its wall has ~18° of draft
    cone = trimesh.creation.cone(radius=10.0, height=30.0, sections=64)
    rep = analyze_undercuts(cone, parting_z_mm=15.0)
    assert rep.top.low_draft_area_mm2 == 0.0


def test_sphere_clean_at_equator():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    report = analyze_undercuts(sphere, parting_z_mm=0.0)
    assert report.undercut_area_mm2 == 0.0
    assert not report.face_is_undercut.any()
    assert report.bottom.likely_demoldable and report.top.likely_demoldable

    # off-equator the band between the plane and the equator faces away from
    # the top half's +Z pull: undercut
    off = analyze_undercuts(sphere, parting_z_mm=-10.0)
    assert off.undercut_area_mm2 > 0.0
    assert off.bottom.likely_demoldable
    assert not off.top.likely_demoldable

    suggested = suggest_parting_plane(sphere)
    assert abs(suggested) < 1.0  # equator, within candidate-grid resolution


def test_mushroom_undercuts():
    part = mushroom()
    annulus_area = np.pi * (15.0**2 - 5.0**2)

    # parting through the stem: the cap underside is above the plane and
    # faces −Z, blocking the top half's +Z pull
    low = analyze_undercuts(part, parting_z_mm=10.0)
    assert not low.top.likely_demoldable
    assert low.bottom.likely_demoldable
    # flagged area is the cap underside (discretized circle, hence 1% slack)
    assert low.undercut_area_mm2 == pytest.approx(annulus_area, rel=0.01)
    flagged = low.face_is_undercut
    centroids = part.triangles_center[flagged]
    normals = part.face_normals[flagged]
    assert np.allclose(centroids[:, 2], 20.0, atol=1e-3)
    assert np.all(normals[:, 2] < -0.9)

    # parting through the widest section (the cap): no undercuts
    wide = analyze_undercuts(part, parting_z_mm=22.5)
    assert wide.undercut_area_mm2 == 0.0
    assert wide.bottom.likely_demoldable and wide.top.likely_demoldable

    # the optimizer should land inside the cap band
    suggested = suggest_parting_plane(part)
    assert 20.0 < suggested < 25.0
