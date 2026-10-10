"""Tests for forged-mold hardware: telescoping fit, registration pins, bolt
holes, pry slots, overflow channels, lead-in chamfer.

Pin geometry is measured directly off the output meshes: in the female half,
the only material above the flange plane is the pegs, and manifold's
cylinders place vertices exactly on the requested radius, so radial distances
from the pin centers recover peg/hole radii to float32 precision.
"""

import numpy as np
import pytest
import trimesh

from moldgen.core import MoldParams, generate_mold
from moldgen.core.hardware import CIRCULAR_SEGMENTS, HOLE_OVERDEPTH, PRY_REACH

PLUNGE = 10.0
Z_FLANGE = PLUNGE  # all tests part at z=0


def sphere():
    return trimesh.creation.icosphere(subdivisions=3, radius=20.0)


def box():
    return trimesh.creation.box(extents=[30.0, 20.0, 10.0])


def compression(**kw):
    kw.setdefault("parting_z_mm", 0.0)
    return MoldParams(mode="compression", **kw)


def bare(**kw):
    """Everything optional off, no draft/lead-in: exact prism arithmetic."""
    base = dict(
        plug_draft_deg=0.0,
        lead_in_mm=0.0,
        pins=False,
        pry_slots=False,
        overflow_channels=False,
        jack_screws=False,
    )
    base.update(kw)
    return compression(**base)


def max_radius(mesh, center, z_lo, z_hi, window):
    v = mesh.vertices
    d = np.linalg.norm(v[:, :2] - np.asarray(center), axis=1)
    mask = (v[:, 2] > z_lo) & (v[:, 2] < z_hi) & (d < window)
    assert mask.any(), "no vertices found in probe region"
    return float(d[mask].max())


# --- telescoping fit ---------------------------------------------------------


def test_male_plug_fits_inside_female_pocket():
    res = generate_mold(box(), bare())
    assert res.ok, res.errors
    female, male = res.halves["female"], res.halves["male"]

    def wall_x(mesh, lo, hi, pick):
        # prism walls only carry vertices at their corner rings (z=0 and
        # z=z_flange), so the probe slab must include those
        v = mesh.vertices
        mask = (
            (v[:, 2] > -0.5)
            & (v[:, 2] < Z_FLANGE + 0.5)
            & (np.abs(v[:, 0]) > lo)
            & (np.abs(v[:, 0]) < hi)
        )
        assert mask.any()
        return pick(np.abs(v[mask, 0]))

    plug_wall = wall_x(male, 20.0, 30.0, np.max)  # plug wall: bbox 15 + border 8
    pocket_wall = wall_x(female, 20.0, 30.0, np.min)
    assert plug_wall == pytest.approx(23.0, abs=1e-3)
    assert pocket_wall > plug_wall  # the male always enters the female
    assert pocket_wall - plug_wall == pytest.approx(0.15, abs=1e-3)


def test_forged_metadata():
    res = generate_mold(sphere(), compression())
    assert res.ok, res.errors
    forged = res.metadata["forged"]
    assert forged["z_flange"] == pytest.approx(Z_FLANGE, abs=1e-2)
    assert forged["plunge_mm"] == PLUNGE
    assert forged["pocket_clearance_mm"] == 0.15


# --- registration pins -------------------------------------------------------


def test_pins_present_by_default():
    res = generate_mold(sphere(), compression())
    assert res.ok, res.errors
    female, male = res.halves["female"], res.halves["male"]
    assert female.is_watertight and male.is_watertight

    info = res.metadata["hardware"]["pins"]
    assert len(info["centers"]) == 4
    assert info["radius"] == pytest.approx(4.0)
    # pegs rise exactly pin_height above the flange plane
    assert female.bounds[1][2] == pytest.approx(Z_FLANGE + info["height"], abs=1e-3)
    for center in info["centers"]:
        r_meas = max_radius(
            female,
            center,
            Z_FLANGE + 0.05,
            Z_FLANGE + info["height"],
            info["radius"] + 1.0,
        )
        assert r_meas == pytest.approx(info["radius"], abs=1e-3)


def test_pins_absent_when_disabled():
    res = generate_mold(sphere(), compression(pins=False))
    assert res.ok, res.errors
    assert "pins" not in res.metadata["hardware"]
    assert res.halves["female"].bounds[1][2] == pytest.approx(Z_FLANGE, abs=1e-4)


@pytest.mark.parametrize("clearance", [0.15, 0.25])
def test_peg_hole_radii_differ_by_exact_clearance(clearance):
    res = generate_mold(sphere(), compression(pin_clearance_mm=clearance))
    assert res.ok, res.errors
    info = res.metadata["hardware"]["pins"]
    r, h, c = info["radius"], info["height"], info["chamfer"]
    center = info["centers"][0]

    r_peg = max_radius(
        res.halves["female"],
        center,
        Z_FLANGE + 0.05,
        Z_FLANGE + h - c + 0.01,
        r + 1.0,
    )
    r_hole = max_radius(
        res.halves["male"],
        center,
        Z_FLANGE + 0.05,
        Z_FLANGE + h + HOLE_OVERDEPTH + 0.1,
        r + clearance + 1.0,
    )
    assert r_peg == pytest.approx(r, abs=1e-3)
    assert r_hole - r_peg == pytest.approx(clearance, abs=1e-3)


def test_pins_shrunk_when_band_narrow():
    # margin 17 → band ≈ 17 − 8 − 0.15 − draft growth → fitting radius < 4
    res = generate_mold(sphere(), compression(wall_margin_mm=17.0))
    assert res.ok, res.errors
    assert any("pin radius shrunk" in w for w in res.warnings)
    info = res.metadata["hardware"]["pins"]
    assert 1.5 <= info["radius"] < 4.0
    assert all(h.is_watertight for h in res.halves.values())


def test_pins_skipped_when_band_tiny():
    # margin 14.5 → band ≈ 6.2: legal, but no usable pin radius
    res = generate_mold(sphere(), compression(wall_margin_mm=14.5))
    assert res.ok, res.errors
    assert any("pins skipped" in w for w in res.warnings)
    assert "pins" not in res.metadata["hardware"]


# --- clamping bolts ----------------------------------------------------------


def test_bolt_holes_volume_and_count():
    part = sphere()
    plain = generate_mold(part, bare())
    bolted = generate_mold(part, bare(bolts=True, bolt_count=4, bolt_diameter_mm=6.0))
    assert bolted.ok, bolted.errors
    assert all(h.is_watertight for h in bolted.halves.values())

    info = bolted.metadata["hardware"]["bolts"]
    assert len(info["centers"]) == 4
    r = info["radius"]
    assert r == pytest.approx(3.25)  # M6/2 + 0.25 sliding clearance

    area = 0.5 * CIRCULAR_SEGMENTS * np.sin(2 * np.pi / CIRCULAR_SEGMENTS) * r**2
    bottom_z = part.bounds[0][2] - 10.0  # default base thickness
    top_z = max(part.bounds[1][2], Z_FLANGE) + 10.0
    d_female = plain.halves["female"].volume - bolted.halves["female"].volume
    d_male = plain.halves["male"].volume - bolted.halves["male"].volume
    assert d_female == pytest.approx(4 * area * (Z_FLANGE - bottom_z), rel=1e-3)
    assert d_male == pytest.approx(4 * area * (top_z - Z_FLANGE), rel=1e-3)


def test_bolt_count_two():
    res = generate_mold(sphere(), bare(bolts=True, bolt_count=2))
    assert res.ok, res.errors
    info = res.metadata["hardware"]["bolts"]
    assert len(info["centers"]) == 2
    (x0, y0), (x1, y1) = info["centers"]
    assert y0 == pytest.approx(y1)
    assert x0 != pytest.approx(x1)


def test_bolt_spacing_warning_on_large_mold():
    slab = trimesh.creation.box(extents=[200.0, 200.0, 20.0])
    res = generate_mold(slab, bare(bolts=True, bolt_count=4))
    assert res.ok, res.errors
    assert any("50-75 mm" in w for w in res.warnings)


def test_bolts_ignored_in_open_layup():
    res = generate_mold(
        sphere(),
        MoldParams(mode="open_layup", parting_z_mm=0.0, bolts=True, keep_side="below"),
    )
    assert res.ok, res.errors
    assert "bolts" not in res.metadata["hardware"]
    assert any("bolt" in w for w in res.warnings)


# --- pry slots ---------------------------------------------------------------


def test_pry_slots_cut_both_halves():
    part = box()
    plain = generate_mold(part, bare())
    pried = generate_mold(part, bare(pry_slots=True))
    assert pried.ok, pried.errors
    assert all(h.is_watertight for h in pried.halves.values())

    info = pried.metadata["hardware"]["pry_slots"]
    assert len(info["centers"]) == 2
    removed_per_half = 2 * info["width"] * info["reach"] * info["depth"]
    d_female = plain.halves["female"].volume - pried.halves["female"].volume
    d_male = plain.halves["male"].volume - pried.halves["male"].volume
    assert d_female == pytest.approx(removed_per_half, rel=1e-3)
    assert d_male == pytest.approx(removed_per_half, rel=1e-3)


# --- overflow channels -------------------------------------------------------


def test_overflow_channels_cut_female_flange():
    part = box()
    plain = generate_mold(part, bare())
    flowed = generate_mold(part, bare(overflow_channels=True, overflow_per_edge=2))
    assert flowed.ok, flowed.errors
    assert all(h.is_watertight for h in flowed.halves.values())

    info = flowed.metadata["hardware"]["overflow"]
    assert info["count"] == 8  # 2 per edge × 4 edges
    band = 22.0 - 8.0 - 0.15  # margin − border − clearance (no draft)
    expected = info["count"] * info["width"] * info["depth"] * band
    d_female = plain.halves["female"].volume - flowed.halves["female"].volume
    d_male = plain.halves["male"].volume - flowed.halves["male"].volume
    assert d_female == pytest.approx(expected, rel=1e-3)
    assert d_male == pytest.approx(0.0, abs=1e-6)


# --- demolding: jack screws, ejector, warnings --------------------------------


def test_jack_screws_cut_male_plate_only():
    part = sphere()
    plain = generate_mold(part, bare())
    jacked = generate_mold(part, bare(jack_screws=True, jack_screw_count=2))
    assert jacked.ok, jacked.errors
    assert all(h.is_watertight for h in jacked.halves.values())

    info = jacked.metadata["hardware"]["jack_screws"]
    assert len(info["centers"]) == 2
    assert info["radius"] == pytest.approx(2.5)  # M6 − 1 mm tap fit, /2

    # holes pierce only the male plate (top_z − z_flange thick)
    area = 0.5 * CIRCULAR_SEGMENTS * np.sin(2 * np.pi / CIRCULAR_SEGMENTS) * info["radius"] ** 2
    top_z = max(part.bounds[1][2], Z_FLANGE) + 10.0
    d_male = plain.halves["male"].volume - jacked.halves["male"].volume
    d_female = plain.halves["female"].volume - jacked.halves["female"].volume
    assert d_male == pytest.approx(2 * area * (top_z - Z_FLANGE), rel=1e-3)
    assert d_female == pytest.approx(0.0, abs=1e-6)

    # diagonal: the two screws sit on opposite edges at different y
    (x0, y0), (x1, y1) = info["centers"]
    assert x0 != pytest.approx(x1)
    assert y0 != pytest.approx(y1)


def test_ejector_hole_pierces_female_base():
    part = box()  # bottom face at z=-5, centroid at origin
    plain = generate_mold(part, bare())
    ejected = generate_mold(part, bare(ejector_holes=True))
    assert ejected.ok, ejected.errors
    female = ejected.halves["female"]
    assert female.is_watertight

    info = ejected.metadata["hardware"]["ejector"]
    assert info["center"] == pytest.approx([0.0, 0.0], abs=1e-6)
    assert any("wax or tape" in w for w in ejected.warnings)

    # hole runs from the block bottom (−5−10) to the cavity floor (−5)
    area = 0.5 * CIRCULAR_SEGMENTS * np.sin(2 * np.pi / CIRCULAR_SEGMENTS) * info["radius"] ** 2
    removed = plain.halves["female"].volume - female.volume
    assert removed == pytest.approx(area * 10.0, rel=1e-3)
    # and the female now opens at its underside
    assert female.bounds[0][2] == pytest.approx(-15.0, abs=1e-3)


def test_demold_warnings():
    # vertical box walls: no undercuts, but both cavities drag
    res = generate_mold(box(), bare())
    assert res.ok
    drag = [w for w in res.warnings if "draft" in w and "demolding" in w]
    assert any("female" in w for w in drag)
    assert any("male" in w for w in drag)

    # mushroom parted through the stem: the male cavity locks
    stem = trimesh.creation.cylinder(radius=5.0, height=20.0, sections=64)
    stem.apply_translation([0, 0, 10.0])
    cap = trimesh.creation.cylinder(radius=15.0, height=5.0, sections=64)
    cap.apply_translation([0, 0, 22.5])
    from moldgen.core import mfld

    mushroom = mfld.to_trimesh(mfld.to_manifold(stem) + mfld.to_manifold(cap))
    res = generate_mold(mushroom, bare(parting_z_mm=10.0))
    assert res.ok
    assert any("undercut" in w and "male" in w and "lock" in w for w in res.warnings)

    # check can be disabled
    res = generate_mold(box(), bare(demold_check=False))
    assert not any("demolding" in w for w in res.warnings)


# --- lead-in chamfer ---------------------------------------------------------


def test_lead_in_widens_pocket_mouth():
    part = box()
    plain = generate_mold(part, bare())
    led = generate_mold(part, bare(lead_in_mm=1.0))
    assert led.ok, led.errors
    female = led.halves["female"]
    assert female.is_watertight
    assert female.volume < plain.halves["female"].volume

    # funnel rim at the flange plane sits lead_in outside the pocket wall;
    # the rim rectangle only has corner vertices, so probe near a corner
    v = female.vertices
    pocket_wall = 15.0 + 8.0 + 0.15
    at_rim = (
        (np.abs(v[:, 2] - Z_FLANGE) < 1e-3)
        & (np.abs(v[:, 0]) > pocket_wall + 0.2)
        & (np.abs(v[:, 0]) < pocket_wall + 2.0)
    )
    assert at_rim.any()
    rim_x = float(np.abs(v[at_rim, 0]).min())
    assert rim_x - pocket_wall == pytest.approx(1.0, abs=1e-2)


# --- trim groove (open layup, unchanged) --------------------------------------


def test_trim_groove():
    part = sphere()
    plain = generate_mold(part, MoldParams(mode="open_layup", parting_z_mm=0.0, keep_side="below"))
    grooved = generate_mold(
        part,
        MoldParams(
            mode="open_layup",
            parting_z_mm=0.0,
            keep_side="below",
            trim_groove=True,
            trim_groove_offset_mm=8.0,
        ),
    )
    assert grooved.ok, grooved.errors
    tool = grooved.halves["tool"]
    assert tool.is_watertight

    info = grooved.metadata["hardware"]["trim_groove"]
    x0, y0, x1, y1 = info["rect"]
    perimeter = 2 * ((x1 - x0) + (y1 - y0))
    estimate = perimeter * info["depth"] ** 2
    removed = plain.halves["tool"].volume - tool.volume
    assert 0.5 * estimate < removed < 1.5 * estimate


def test_trim_groove_ignored_in_compression():
    res = generate_mold(sphere(), compression(trim_groove=True))
    assert res.ok, res.errors
    assert "trim_groove" not in res.metadata["hardware"]
    assert any("trim groove" in w for w in res.warnings)
