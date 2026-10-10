"""Contoured telescoping section (plug_shape="contour", the default): the
plug and pocket follow the part outline grown by plug_border_mm."""

import math

import numpy as np
import pytest
import trimesh
from manifold3d import CrossSection

from moldgen.core import MoldParams, generate_mold, mfld


def block_rect(res):
    (x0, y0, _), (x1, y1, _) = res.metadata["block"]["bounds"]
    return CrossSection([[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]])


def l_part():
    notch = trimesh.creation.box(extents=[30.0, 30.0, 20.0])
    notch.apply_translation([15.0, 15.0, 0.0])
    return trimesh.boolean.difference(
        [trimesh.creation.box(extents=[60.0, 60.0, 10.0]), notch], engine="manifold"
    )


@pytest.mark.parametrize(
    "part",
    [trimesh.creation.icosphere(subdivisions=3, radius=20.0), l_part()],
    ids=["sphere", "L"],
)
def test_contour_halves_are_clean_and_disjoint(part):
    res = generate_mold(part, MoldParams(mode="compression", parting_z_mm=0.0))
    assert res.ok, res.errors
    female, male = (mfld.to_manifold(res.halves[k]) for k in ("female", "male"))
    assert res.halves["female"].is_watertight and res.halves["male"].is_watertight
    assert len(mfld.solid_bodies(female)) == 1 and len(mfld.solid_bodies(male)) == 1
    assert abs((female ^ male).volume()) < 1e-3
    meta = res.metadata["forged"]
    assert meta["plug_shape"] == "contour"
    x0, y0, x1, y1 = meta["plug_rect"]
    assert meta["plug_area_mm2"] < (x1 - x0) * (y1 - y0)


def test_contour_pinch_gap_is_uniform():
    # 0° draft: the void between pocket and plug at mid-plunge is a ring of
    # constant width = plug_clearance_mm all the way round the contour
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    clear = 0.3
    res = generate_mold(
        sphere,
        MoldParams(
            mode="compression",
            parting_z_mm=0.0,
            plug_clearance_mm=clear,
            plug_draft_deg=0.0,
            pry_slots=False,
            overflow_channels=False,
        ),
    )
    assert res.ok, res.errors
    z = res.metadata["parting_z_mm"] + 5.0
    female, male = (mfld.to_manifold(res.halves[k]) for k in ("female", "male"))
    # the plug's outer outline (the part's cross-section is a hole in it)
    plug = CrossSection([p for p in male.slice(z).to_polygons() if mfld.signed_area(p) > 0])
    gap = block_rect(res) - female.slice(z) - plug
    # ring area ≈ (plug perimeter + π·clear) · clear for a convex outline
    perimeter = sum(
        float(np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1).sum()) for p in plug.to_polygons()
    )
    assert gap.area() / (perimeter + math.pi * clear) == pytest.approx(clear, rel=0.02)


def test_contour_overflow_channels_reach_the_pocket():
    torus = trimesh.creation.torus(major_radius=25.0, minor_radius=6.0)
    res = generate_mold(torus, MoldParams(mode="compression", parting_z_mm=0.0, pry_slots=False))
    assert res.ok, res.errors
    assert res.metadata["hardware"]["overflow"]["count"] == 8
    female = mfld.to_manifold(res.halves["female"])
    z = res.metadata["forged"]["z_flange"] - 0.25  # inside the channel depth
    void = block_rect(res) - female.slice(z)
    # pocket + every channel form one region open to the outer wall
    assert len(void.decompose()) == 1


def test_contour_channels_never_cut_a_transferred_core():
    # an upside-down cup's core is handed to the female and stands inside
    # the pocket, up past the flange — channels must stop short of it
    outer = trimesh.creation.cylinder(radius=20.0, height=30.0, sections=48)
    inner = trimesh.creation.cylinder(radius=17.0, height=30.0, sections=48)
    inner.apply_translation([0, 0, -3.0])
    cup = trimesh.boolean.difference([outer, inner], engine="manifold")
    plain = generate_mold(
        cup, MoldParams(mode="compression", parting_z_mm=0.0, overflow_channels=False)
    )
    chans = generate_mold(cup, MoldParams(mode="compression", parting_z_mm=0.0))
    assert plain.ok and chans.ok
    assert chans.metadata["forged"]["cores"]["to_female"] == 1
    core_zone = CrossSection.circle(16.0, 64)
    z = chans.metadata["forged"]["z_flange"] - 0.25
    a = (core_zone ^ mfld.to_manifold(plain.halves["female"]).slice(z)).area()
    b = (core_zone ^ mfld.to_manifold(chans.halves["female"]).slice(z)).area()
    assert a == pytest.approx(b, abs=1e-6)


def test_rect_shape_still_available():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    res = generate_mold(sphere, MoldParams(mode="compression", parting_z_mm=0.0, plug_shape="rect"))
    assert res.ok, res.errors
    x0, y0, x1, y1 = res.metadata["forged"]["plug_rect"]
    assert res.metadata["forged"]["plug_area_mm2"] == pytest.approx((x1 - x0) * (y1 - y0))
    bad = generate_mold(sphere, MoldParams(mode="compression", parting_z_mm=0.0, plug_shape="oval"))
    assert not bad.ok
