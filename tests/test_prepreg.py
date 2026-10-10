"""Prepreg / laminating tool tests.

A cylinder part is used for the exact-volume cases: it has no vertices on
the z=0 parting plane (clean cross-section slice), it's z-symmetric (half
its volume below the plane), and its parting-plane cross-section is the
same 64-gon manifold slices, so the run-out volume is exactly
cross-section-area × trim allowance when the kick is zero.
"""

import math

import numpy as np
import pytest
import trimesh

from moldgen.core import MoldParams, generate_mold, mfld

MARGIN = 30.0  # EC: ~30 mm finished flange is enough for a prepreg tool
TRIM = 8.0


def cylinder():
    return trimesh.creation.cylinder(radius=15.0, height=30.0, sections=64)


def prepreg_params(**kw):
    base = dict(
        mode="prepreg",
        parting_z_mm=0.0,
        wall_margin_mm=MARGIN,
        keep_side="below",
        trim_allowance_mm=TRIM,
        trim_kick_deg=0.0,
        tape_line=False,
    )
    base.update(kw)
    return MoldParams(**base)


def hole_extent_at(tool, z):
    """Max |x| of the run-out opening's outline at height z (the smaller of
    the section's closed loops; the larger is the block outline)."""
    section = tool.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    loops = sorted(section.discrete, key=lambda d: np.ptp(d[:, 0]))
    return float(np.abs(loops[0][:, 0]).max())


def test_tool_volume_and_run_out():
    part = cylinder()
    res = generate_mold(part, prepreg_params())
    assert res.ok, res.errors
    tool = res.halves["tool"]
    assert tool.is_watertight
    # flange face sits trim_allowance above the parting plane
    assert tool.bounds[1][2] == pytest.approx(TRIM, abs=1e-3)
    assert res.metadata["prepreg"]["flange_z"] == pytest.approx(TRIM)
    assert res.metadata["prepreg"]["notes"]

    # kick 0: run-out removes exactly cross-section-area × trim
    cs_area = mfld.to_manifold(part).slice(0.0).area()
    dx = 30.0 + 2 * MARGIN
    block = dx * dx * (15.0 + 10.0 + TRIM)  # part + base thickness + run-out
    expected = block - part.volume / 2 - cs_area * TRIM
    assert tool.volume == pytest.approx(expected, rel=1e-4)

    # the cavity opening continues through the run-out zone: a slice halfway
    # up shows the block outline plus the open run-out
    section = tool.section(plane_origin=[0, 0, TRIM / 2], plane_normal=[0, 0, 1])
    assert len(section.discrete) == 2


def test_kick_widens_run_out():
    res = generate_mold(cylinder(), prepreg_params(trim_kick_deg=5.0))
    assert res.ok, res.errors
    tool = res.halves["tool"]
    assert tool.is_watertight
    # a cylinder's cross-section is centered at the origin, so the scale
    # approximation is a true uniform offset: radius grows tan(5°) per mm
    r_low = hole_extent_at(tool, 1.0)
    r_high = hole_extent_at(tool, 7.0)
    assert r_high - r_low == pytest.approx(6.0 * np.tan(np.radians(5.0)), abs=0.05)


def test_keep_above_is_mirrored():
    res = generate_mold(cylinder(), prepreg_params(keep_side="above"))
    assert res.ok, res.errors
    tool = res.halves["tool"]
    assert tool.is_watertight
    assert tool.bounds[0][2] == pytest.approx(-TRIM, abs=1e-3)
    assert res.metadata["prepreg"]["flange_z"] == pytest.approx(-TRIM)


def test_tape_line_scribe():
    part = cylinder()
    plain = generate_mold(part, prepreg_params())
    taped = generate_mold(part, prepreg_params(tape_line=True))
    assert taped.ok, taped.errors
    tool = taped.halves["tool"]
    assert tool.is_watertight

    info = taped.metadata["hardware"]["tape_line"]
    x0, y0, x1, y1 = info["rect"]
    assert x0 == pytest.approx(-15.0 - MARGIN + 15.0)  # block edge + tape zone
    perimeter = 2 * ((x1 - x0) + (y1 - y0))
    estimate = perimeter * info["depth"] ** 2
    removed = plain.halves["tool"].volume - tool.volume
    assert 0.5 * estimate < removed < 1.5 * estimate


def test_flange_bush_holes():
    part = cylinder()
    plain = generate_mold(part, prepreg_params())
    holed = generate_mold(part, prepreg_params(flange_bolt_holes=True))
    assert holed.ok, holed.errors
    tool = holed.halves["tool"]
    assert tool.is_watertight

    info = holed.metadata["hardware"]["flange_holes"]
    # 90 mm edges at 90 mm target spacing: one hole per edge
    assert len(info["centers"]) == 4
    assert info["diameter"] == pytest.approx(6.1)
    # EC placement: 20-25 mm from the cavity (mould surface) edge
    x = abs(info["centers"][0][0]) if info["centers"][0][0] else abs(info["centers"][0][1])
    from_cavity_edge = x - 15.0
    assert 20.0 <= from_cavity_edge <= 25.0

    from moldgen.core.hardware import CIRCULAR_SEGMENTS

    area = 0.5 * CIRCULAR_SEGMENTS * np.sin(2 * np.pi / CIRCULAR_SEGMENTS) * info["radius"] ** 2
    height = (15.0 + 10.0) + TRIM  # block bottom (base thickness) to flange face
    removed = plain.halves["tool"].volume - tool.volume
    assert removed == pytest.approx(4 * area * height, rel=1e-3)


def test_drape_warning_on_sharp_edges():
    box = trimesh.creation.box(extents=[30.0, 20.0, 10.0])
    res = generate_mold(box, prepreg_params())
    assert res.ok, res.errors
    assert any("drape" in w and "bridge" in w for w in res.warnings)

    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    res = generate_mold(sphere, prepreg_params())
    assert res.ok, res.errors
    assert not any("drape" in w for w in res.warnings)


def test_prepreg_param_validation():
    part = cylinder()
    res = generate_mold(part, prepreg_params(trim_kick_deg=30.0))
    assert not res.ok and any("trim_kick" in e for e in res.errors)
    res = generate_mold(part, prepreg_params(trim_allowance_mm=0.0))
    assert not res.ok and any("trim_allowance" in e for e in res.errors)


def test_kick_is_a_true_offset_on_concave_outlines():
    # scaling about the center (the old construction) gave an L-shaped
    # part's notch edges no kick at all; the run-out must be the outline
    # offset by tan(kick) * height on every edge
    from manifold3d import CrossSection, JoinType

    notch = trimesh.creation.box(extents=[30.0, 30.0, 20.0])
    notch.apply_translation([15.0, 15.0, 0.0])
    part = trimesh.boolean.difference(
        [trimesh.creation.box(extents=[60.0, 60.0, 10.0]), notch], engine="manifold"
    )
    res = generate_mold(part, MoldParams(mode="prepreg", parting_z_mm=4.9, wall_margin_mm=30.0))
    assert res.ok, res.errors
    tool = mfld.to_manifold(res.halves["tool"])
    z_top = res.metadata["prepreg"]["flange_z"] - 0.05
    big = CrossSection([[(-200, -200), (200, -200), (200, 200), (-200, 200)]])
    opening = big - tool.slice(z_top)
    grow = math.tan(math.radians(5.0)) * (z_top - 4.9)
    # every edge kicked by the full amount (convex corners come out rounded,
    # which keeps the same 5° draft round the corner)
    ideal = mfld.to_manifold(part).slice(4.9).offset(grow, JoinType.Round, 2.0, 0)
    assert (ideal - opening).area() < 1e-3
