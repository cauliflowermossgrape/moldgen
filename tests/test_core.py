"""Engine tests. All geometry is generated programmatically — no fixture files.

Volume assertions rely on manifold's booleans being exact on the input mesh:
e.g. an icosphere is z-symmetric, so the cavity below the z=0 parting plane is
exactly half the mesh volume, up to manifold's float32 vertex precision.

Compression tests use the telescoping (forged) construction with draft,
lead-in and all hardware disabled, so half volumes reduce to exact prism
arithmetic:
    female = A_block·(z_flange − block_bottom) − A_pocket·plunge − part_below
    male   = A_plug·(top − z_split) + (A_block − A_plug)·(top − z_flange)
             − part_above
"""

import numpy as np
import pytest
import trimesh

from moldgen.core import MoldParams, export_stl, generate_mold, mfld

MARGIN = 15.0
BASE = 10.0  # default base_thickness_mm
PLUNGE = 10.0
BORDER = 8.0
CLEARANCE = 0.15
REL_TOL = 1e-4


def bare_forged(**kw):
    """Compression params with hardware/draft/lead-in off: exact volumes."""
    base = dict(
        mode="compression",
        parting_z_mm=0.0,
        wall_margin_mm=MARGIN,
        plunge_mm=PLUNGE,
        plug_border_mm=BORDER,
        plug_clearance_mm=CLEARANCE,
        plug_draft_deg=0.0,
        lead_in_mm=0.0,
        plug_shape="rect",
        pins=False,
        pry_slots=False,
        overflow_channels=False,
        jack_screws=False,
    )
    base.update(kw)
    return MoldParams(**base)


def forged_expected(part, res, part_below, part_above):
    """Analytic female/male volumes for bare_forged params."""
    z_split = res.metadata["parting_z_mm"]
    zf = z_split + PLUNGE
    (x0, y0, z0), (x1, y1, z1) = part.bounds
    a_blk = (x1 - x0 + 2 * MARGIN) * (y1 - y0 + 2 * MARGIN)
    a_plug = (x1 - x0 + 2 * BORDER) * (y1 - y0 + 2 * BORDER)
    a_pocket = (x1 - x0 + 2 * (BORDER + CLEARANCE)) * (y1 - y0 + 2 * (BORDER + CLEARANCE))
    top = max(z1, zf) + BASE
    bottom = z0 - BASE
    female = a_blk * (zf - bottom) - a_pocket * PLUNGE - part_below
    male = a_plug * (top - z_split) + (a_blk - a_plug) * (top - zf) - part_above
    return female, male


def test_sphere_compression():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    res = generate_mold(sphere, bare_forged())
    assert res.ok, res.errors
    female, male = res.halves["female"], res.halves["male"]
    assert female.is_watertight and male.is_watertight

    # icosphere is symmetric about z=0: exactly half its volume each side
    exp_female, exp_male = forged_expected(sphere, res, sphere.volume / 2, sphere.volume / 2)
    assert female.volume == pytest.approx(exp_female, rel=REL_TOL)
    assert male.volume == pytest.approx(exp_male, rel=REL_TOL)


def test_tube_annular_cross_section():
    # tube (hollow cylinder) standing across the parting plane: the pocket
    # floor of the female shows an annulus — an island (the bore core)
    # inside a hole (the cavity)
    outer = trimesh.creation.cylinder(radius=15.0, height=60.0, sections=96)
    inner = trimesh.creation.cylinder(radius=8.0, height=80.0, sections=96)
    tube = mfld.to_trimesh(mfld.to_manifold(outer) - mfld.to_manifold(inner))
    assert tube.is_watertight

    res = generate_mold(tube, bare_forged())
    assert res.ok, res.errors
    female, male = res.halves["female"], res.halves["male"]
    assert female.is_watertight and male.is_watertight

    exp_female, _ = forged_expected(tube, res, tube.volume / 2, tube.volume / 2)
    assert female.volume == pytest.approx(exp_female, rel=REL_TOL)

    # a slice just below the parting plane must show 3 closed loops:
    # block outline, cavity wall (r=15), and the bore core island (r=8)
    section = female.section(plane_origin=[0, 0, -0.5], plane_normal=[0, 0, 1])
    assert section is not None
    assert len(section.discrete) == 3


def test_rotated_box_watertight():
    box = trimesh.creation.box(extents=[30.0, 20.0, 10.0])
    box.apply_transform(trimesh.transformations.rotation_matrix(np.radians(30), [1.0, 1.0, 0.0]))
    res = generate_mold(box, bare_forged())
    assert res.ok, res.errors
    female, male = res.halves["female"], res.halves["male"]
    assert female.is_watertight and male.is_watertight
    exp_f, exp_m = forged_expected(box, res, box.volume / 2, box.volume / 2)
    assert female.volume + male.volume == pytest.approx(exp_f + exp_m, rel=REL_TOL)


def test_coplanar_facets_nudged():
    # parting plane exactly on the box's top face: the pocket floor must be
    # nudged, not produce broken geometry
    box = trimesh.creation.box(extents=[30.0, 20.0, 10.0])  # top face at z=5
    res = generate_mold(box, bare_forged(parting_z_mm=5.0))
    assert res.ok, res.errors
    assert any("nudged" in w for w in res.warnings)
    female, male = res.halves["female"], res.halves["male"]
    assert female.is_watertight and male.is_watertight
    # whole box sits below the (nudged) parting plane
    exp_f, exp_m = forged_expected(box, res, box.volume, 0.0)
    assert female.volume == pytest.approx(exp_f, rel=REL_TOL)
    assert male.volume == pytest.approx(exp_m, rel=REL_TOL)


def test_open_layup_keep_below():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    res = generate_mold(
        sphere,
        MoldParams(
            mode="open_layup",
            parting_z_mm=0.0,
            wall_margin_mm=MARGIN,
            keep_side="below",
        ),
    )
    assert res.ok, res.errors
    assert set(res.halves) == {"tool"}
    tool = res.halves["tool"]
    assert tool.is_watertight
    dx = sphere.extents[0] + 2 * MARGIN
    dy = sphere.extents[1] + 2 * MARGIN
    floor = sphere.bounds[0][2] - BASE
    expected = dx * dy * (0.0 - floor) - sphere.volume / 2
    assert tool.volume == pytest.approx(expected, rel=REL_TOL)
    # the tool sits entirely below the parting plane (plus float32 slack)
    assert tool.bounds[1][2] == pytest.approx(0.0, abs=1e-3)


def test_cavity_offset_grows_cavity():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)

    def lower_cavity(res):
        # block-below-plane volume minus tool material = cavity volume
        (bx0, by0, bz0), (bx1, by1, _) = res.metadata["block"]["bounds"]
        z_split = res.metadata["parting_z_mm"]
        below = (bx1 - bx0) * (by1 - by0) * (z_split - bz0)
        return below - res.halves["tool"].volume

    base = dict(mode="open_layup", parting_z_mm=0.0, keep_side="below")
    plain = generate_mold(sphere, MoldParams(**base))
    offset = generate_mold(sphere, MoldParams(**base, cavity_offset_mm=0.5))
    assert plain.ok and offset.ok
    assert lower_cavity(offset) > lower_cavity(plain)
    # offset cavity ≈ lower half of a r=20.5 sphere
    assert lower_cavity(offset) == pytest.approx((2 / 3) * np.pi * 20.5**3, rel=0.02)


def test_non_watertight_input_structured_error():
    # an open sheet cannot be repaired into a solid: expect a structured
    # error result, not an exception
    sheet = trimesh.Trimesh(
        vertices=[[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]],
        faces=[[0, 1, 2], [0, 2, 3]],
        process=False,
    )
    res = generate_mold(sheet, MoldParams(mode="compression", parting_z_mm=0.0))
    assert res.ok is False
    assert res.errors
    assert res.halves == {}


def test_bad_params_rejected():
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    res = generate_mold(sphere, MoldParams(mode="injection", parting_z_mm=0.0))
    assert not res.ok and any("mode" in e for e in res.errors)
    res = generate_mold(sphere, MoldParams(mode="compression", parting_z_mm=999.0))
    assert not res.ok and any("parting_z" in e for e in res.errors)
    res = generate_mold(
        sphere, MoldParams(mode="compression", parting_z_mm=0.0, plug_draft_deg=30.0)
    )
    assert not res.ok and any("draft" in e for e in res.errors)


def test_band_too_small_is_structured_error():
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    res = generate_mold(
        sphere, MoldParams(mode="compression", parting_z_mm=0.0, wall_margin_mm=12.0)
    )
    assert res.ok is False
    assert any("wall_margin" in e for e in res.errors)


def test_export_stl_binary(tmp_path):
    box = trimesh.creation.box(extents=[5.0, 5.0, 5.0])
    path = export_stl(box, tmp_path / "box.stl")
    data = path.read_bytes()
    # binary STL: 80-byte header + uint32 count + 50 bytes per triangle
    assert len(data) == 84 + 50 * len(box.faces)
    reloaded = trimesh.load(path)
    assert reloaded.volume == pytest.approx(box.volume)


def test_internal_failure_becomes_structured_error(monkeypatch):
    # generate_mold never raises: a bug deep in the pipeline is reported
    from moldgen.core import forged

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(forged, "build", boom)
    res = generate_mold(trimesh.creation.box(extents=[10.0, 10.0, 10.0]), bare_forged())
    assert not res.ok
    assert "internal error" in res.errors[0] and "kaboom" in res.errors[0]
