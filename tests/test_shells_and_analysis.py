"""Ray casting, occlusion-aware demolding, shell parts (cores), parting
suggestions, exact offsets and parameter hardening.

All geometry is generated programmatically — no fixture files.
"""

import math

import numpy as np
import pytest
import trimesh

from moldgen.core import MoldParams, generate_mold, mfld
from moldgen.core.offset import offset_mesh
from moldgen.core.rays import VerticalRayCaster
from moldgen.core.undercuts import (
    PartVisibility,
    analyze_undercuts,
    suggest_parting_plane,
)


def cup(drain_hole=False):
    """Open-top cup, z from -15 to 15: 3 mm floor, 3 mm walls."""
    outer = trimesh.creation.cylinder(radius=20.0, height=30.0, sections=48)
    inner = trimesh.creation.cylinder(radius=17.0, height=30.0, sections=48)
    inner.apply_translation([0, 0, 3.0])
    part = trimesh.boolean.difference([outer, inner], engine="manifold")
    if drain_hole:
        hole = trimesh.creation.cylinder(radius=3.0, height=40.0)
        part = trimesh.boolean.difference([part, hole], engine="manifold")
    return part


def tilted_bore_block():
    """Block with a through-hole tilted 30° — a silhouette-occluded undercut
    the per-face normal test can't see on its own."""
    block = trimesh.creation.box(extents=[40.0, 40.0, 20.0])
    bore = trimesh.creation.cylinder(radius=4.0, height=80.0)
    bore.apply_transform(trimesh.transformations.rotation_matrix(np.radians(30), [1, 0, 0]))
    return trimesh.boolean.difference([block, bore], engine="manifold")


def n_bodies(mesh):
    return len(mfld.solid_bodies(mfld.to_manifold(mesh)))


# --- rays ---------------------------------------------------------------------


def test_ray_caster_hits_and_contains():
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    rc = VerticalRayCaster.from_mesh(sphere)
    d_up = rc.first_hit(np.array([[0.0, 0.0, -50.0], [30.0, 0.0, 0.0]]), +1.0)
    assert d_up[0] == pytest.approx(40.0, abs=0.05)
    assert math.isinf(d_up[1])

    rng = np.random.default_rng(1)
    pts = rng.uniform(-12, 12, (20000, 3))
    r = np.linalg.norm(pts, axis=1)
    inside = rc.contains(pts)
    clear = np.abs(r - 10.0) > 0.1  # away from the faceted surface
    assert np.array_equal(inside[clear], (r < 10.0)[clear])


# --- occlusion ----------------------------------------------------------------


def test_tilted_bore_is_flagged_including_hidden_faces():
    part = tilted_bore_block()
    report = analyze_undercuts(part, 0.0)
    assert report.undercut_area_mm2 > 0
    assert not (report.bottom.likely_demoldable and report.top.likely_demoldable)
    # some bore faces point the "right" way but are shadowed by the bore
    assert report.face_is_occluded.any()
    assert np.all(report.face_is_undercut[report.face_is_occluded])


def test_sphere_has_no_occlusion_at_equator():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    report = analyze_undercuts(sphere, 0.0)
    assert report.undercut_area_mm2 == 0
    assert not report.face_is_occluded.any()


# --- shell parts: cores -------------------------------------------------------


def test_cup_analysis_assigns_inside_to_male_core():
    report = analyze_undercuts(cup(), 0.0)
    assert report.undercut_area_mm2 == 0
    assert report.face_is_core.any()
    # core faces sit below the plane but are molded by the top half
    assert np.all(report.face_molded_by_top[report.face_is_core])


def test_cup_compression_moves_core_to_male():
    part = cup()
    res = generate_mold(part, MoldParams(mode="compression", parting_z_mm=0.0))
    assert res.ok, res.errors
    female, male = res.halves["female"], res.halves["male"]
    assert n_bodies(female) == 1 and n_bodies(male) == 1  # no loose core
    assert res.metadata["forged"]["cores"]["to_male"] == 1
    assert not any("undercut" in w for w in res.warnings)
    # closed halves don't overlap each other or the part
    f, m, p = (mfld.to_manifold(x) for x in (female, male, part))
    assert abs((f ^ m).volume()) < 1e-3
    assert abs((f ^ p).volume()) < 1e-3 and abs((m ^ p).volume()) < 1e-3
    # the male core fills the cup's inside below the plane: r=17, 12 mm deep
    core = math.pi * 17.0**2 * 12.0
    assert m.volume() > core * 0.95


def test_inverted_cup_core_goes_to_female():
    part = cup()
    part.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]))
    res = generate_mold(part, MoldParams(mode="compression", parting_z_mm=0.0))
    assert res.ok, res.errors
    assert res.metadata["forged"]["cores"]["to_female"] == 1
    assert n_bodies(res.halves["female"]) == 1 and n_bodies(res.halves["male"]) == 1


@pytest.mark.parametrize("mode", ["prepreg", "open_layup"])
def test_cup_single_sided_tool_drops_core(mode):
    res = generate_mold(cup(), MoldParams(mode=mode, parting_z_mm=12.0, wall_margin_mm=30))
    assert res.ok, res.errors
    assert n_bodies(res.halves["tool"]) == 1
    assert any("shell part" in w for w in res.warnings)
    assert not any("undercut" in w for w in res.warnings)


def test_cup_with_drain_hole_reports_lock():
    # the inside connects to the outside through the hole, so it is NOT a
    # sealed core — the construction keeps it on the female, and the
    # analysis must agree that the part then locks
    report = analyze_undercuts(cup(drain_hole=True), 0.0)
    assert not report.face_is_core.any()
    assert not report.bottom.likely_demoldable


# --- parting suggestions ------------------------------------------------------


def test_suggestion_splits_straight_walls_in_the_middle():
    plate = trimesh.creation.box(extents=[80.0, 50.0, 6.0])
    assert suggest_parting_plane(plate) == pytest.approx(0.0, abs=0.1)


def test_single_sided_suggestions_cover_the_most_part():
    plate = trimesh.creation.box(extents=[80.0, 50.0, 6.0])
    vis = PartVisibility(plate)
    assert suggest_parting_plane(plate, side="below", visibility=vis) > 2.9
    assert suggest_parting_plane(plate, side="above", visibility=vis) < -2.9


def test_suggestion_for_cup():
    part = cup()
    vis = PartVisibility(part)
    # two-part: anywhere through the walls works; tool below: up to the rim
    two = suggest_parting_plane(part, visibility=vis)
    assert analyze_undercuts(part, two, visibility=vis).undercut_area_mm2 == 0
    assert suggest_parting_plane(part, side="below", visibility=vis) > 14.0


def test_sphere_suggestion_refined_to_equator():
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=20.0)
    assert suggest_parting_plane(sphere) == pytest.approx(0.0, abs=0.05)


# --- offset / shrink compensation ---------------------------------------------


@pytest.mark.parametrize("d", [1.0, -1.0, 0.2])
def test_offset_moves_flat_faces_exactly(d):
    cube = trimesh.creation.box(extents=[20.0, 20.0, 20.0])
    out = offset_mesh(cube, d)
    assert out.extents == pytest.approx([20 + 2 * d] * 3, abs=1e-9)
    assert out.is_volume


def test_offset_on_sphere_stays_spherical():
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    r = np.linalg.norm(offset_mesh(sphere, 0.5).vertices, axis=1)
    assert r.min() == pytest.approx(10.5, abs=0.01)
    assert r.max() == pytest.approx(10.5, abs=0.01)


def test_shrink_compensation_scales_cavity():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    res = generate_mold(
        sphere,
        MoldParams(mode="open_layup", parting_z_mm=0.0, keep_side="below", shrink_comp_pct=2.0),
    )
    assert res.ok, res.errors
    # the cavity is the part scaled 1.02x about its center at the plane
    assert res.metadata["part"]["volume"] == pytest.approx(sphere.volume * 1.02**3, rel=1e-4)
    assert res.metadata["part"]["bounds"][0][2] == pytest.approx(-20.4, abs=1e-3)


# --- hardening ----------------------------------------------------------------


@pytest.mark.parametrize("field", ["wall_margin_mm", "parting_z_mm", "plunge_mm"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_params_are_rejected_not_crashed(field, value):
    # a NaN reaching manifold3d segfaults the process; must be caught first
    box = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
    params = MoldParams(mode="compression", parting_z_mm=0.0)
    setattr(params, field, value)
    res = generate_mold(box, params)
    assert not res.ok
    assert any("finite" in e for e in res.errors)


def test_plane_on_top_face_nudges_into_the_part():
    box = trimesh.creation.box(extents=[30.0, 20.0, 10.0])  # top face at z=5
    res = generate_mold(box, MoldParams(mode="compression", parting_z_mm=5.0))
    assert res.ok, res.errors
    # below the top face, so the cavity opens to the pocket (no 1 µm skin)
    assert res.metadata["parting_z_mm"] < 5.0
    assert n_bodies(res.halves["female"]) == 1


# --- hardware placement -------------------------------------------------------


def test_default_overflow_channels_all_placed():
    # jack screws and pry slots used to knock out most channels on default
    # settings; they now slide to a clear spot
    plate = trimesh.creation.box(extents=[80.0, 50.0, 6.0])
    res = generate_mold(plate, MoldParams(mode="compression", parting_z_mm=0.0))
    assert res.ok, res.errors
    assert res.metadata["hardware"]["overflow"]["count"] == 8
    assert not any("overflow" in w for w in res.warnings)


def test_pry_slots_fall_back_to_x_edges():
    # narrow in x: the short ±Y edges are full of corner pins and a bolt,
    # so the slots move to the long ±X edges instead of being dropped
    bar = trimesh.creation.box(extents=[10.0, 60.0, 10.0])
    res = generate_mold(bar, MoldParams(mode="compression", parting_z_mm=0.0, bolts=True))
    assert res.ok, res.errors
    info = res.metadata["hardware"]["pry_slots"]
    assert len(info["centers"]) == 2
    (bx0, _, _), (bx1, _, _) = res.metadata["block"]["bounds"]
    assert all(x in (pytest.approx(bx0), pytest.approx(bx1)) for x, _ in info["centers"])


def test_ejector_lands_on_ring_not_in_its_hole():
    torus = trimesh.creation.torus(major_radius=25.0, minor_radius=6.0)
    res = generate_mold(torus, MoldParams(mode="compression", parting_z_mm=0.0, ejector_holes=True))
    assert res.ok, res.errors
    x, y = res.metadata["hardware"]["ejector"]["center"]
    assert math.hypot(x, y) == pytest.approx(25.0, abs=4.0)
    assert any("ejector hole moved" in w for w in res.warnings)


def test_loading_volume_warning_for_thick_parts():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    res = generate_mold(sphere, MoldParams(mode="compression", parting_z_mm=0.0))
    assert res.ok
    charge = res.metadata["forged"]["charge"]
    assert charge["loading_ratio"] < 2.0
    assert any("loading volume" in w for w in res.warnings)
    deeper = generate_mold(sphere, MoldParams(mode="compression", parting_z_mm=0.0, plunge_mm=30.0))
    assert not any("loading volume" in w for w in deeper.warnings)


def test_draft_angles_are_relative_to_the_molding_half():
    # a frustum narrowing upward, split at mid height: the same wall angle
    # reads as negative draft in the female (wide bottom locks in a cavity
    # that narrows upward) and positive in the male (pulls straight off)
    full = trimesh.creation.cone(radius=20.0, height=40.0, sections=64)
    keep = trimesh.creation.box(extents=[100.0, 100.0, 20.0])
    keep.apply_translation([0, 0, 10.0])
    cone = trimesh.boolean.intersection([full, keep], engine="manifold")  # z 0..20
    report = analyze_undercuts(cone, 10.0)
    walls = np.abs(cone.face_normals[:, 2]) < 0.99
    expected = np.degrees(np.arctan(20.0 / 40.0))  # half-angle of the cone
    below = cone.triangles_center[:, 2] < 10.0
    # female walls face up-and-out: they fight the −Z pull (negative draft)
    assert np.allclose(report.face_draft_deg[walls & below], -expected, atol=0.5)
    # male walls face up-and-out too: +Z pull releases them
    assert np.allclose(report.face_draft_deg[walls & ~below], expected, atol=0.5)


def test_ejector_found_on_dense_ring():
    # the nearest few hundred downward faces of a dense torus all sit on its
    # steep inner rim; the search must still reach the flat underside
    torus = trimesh.creation.torus(
        major_radius=40.0, minor_radius=12.0, major_sections=320, minor_sections=240
    )
    res = generate_mold(
        torus,
        MoldParams(
            mode="compression",
            parting_z_mm=0.0,
            ejector_holes=True,
            pins=False,
            pry_slots=False,
            overflow_channels=False,
            jack_screws=False,
        ),
    )
    assert res.ok, res.errors
    x, y = res.metadata["hardware"]["ejector"]["center"]
    assert 28.0 + 3.0 < math.hypot(x, y) < 52.0 - 3.0  # on the tube's underside
