"""Tests for mold hardware: registration pins, bolt holes, trim groove.

Pin geometry is measured directly off the output meshes: in the bottom half,
the only material above the parting plane is the pegs, and manifold's
cylinders place vertices exactly on the requested radius, so radial distances
from the pin centers recover peg/hole radii to float32 precision.
"""

import numpy as np
import pytest
import trimesh

from moldgen.core import MoldParams, generate_mold
from moldgen.core.hardware import CIRCULAR_SEGMENTS, HOLE_OVERDEPTH


def sphere():
    return trimesh.creation.icosphere(subdivisions=3, radius=20.0)


def compression(**kw):
    return MoldParams(mode="compression", parting_z_mm=0.0, **kw)


def max_radius(mesh, center, z_lo, z_hi, window):
    """Largest xy-distance from `center` among vertices in the z-slab."""
    v = mesh.vertices
    d = np.linalg.norm(v[:, :2] - np.asarray(center), axis=1)
    mask = (v[:, 2] > z_lo) & (v[:, 2] < z_hi) & (d < window)
    assert mask.any(), "no vertices found in probe region"
    return float(d[mask].max())


def test_pins_present_by_default():
    res = generate_mold(sphere(), compression())
    assert res.ok, res.errors
    top, bottom = res.halves["top"], res.halves["bottom"]
    assert top.is_watertight and bottom.is_watertight

    info = res.metadata["hardware"]["pins"]
    assert len(info["centers"]) == 4
    assert info["radius"] == pytest.approx(4.0)
    # pegs rise exactly pin_height above the parting face (z_split = 0)
    assert bottom.bounds[1][2] == pytest.approx(info["height"], abs=1e-3)
    # every pin center has peg material above the parting plane around it
    for center in info["centers"]:
        r_meas = max_radius(bottom, center, 0.05, info["height"], info["radius"] + 1.0)
        assert r_meas == pytest.approx(info["radius"], abs=1e-3)


def test_pins_absent_when_disabled():
    res = generate_mold(sphere(), compression(pins=False))
    assert res.ok, res.errors
    assert "pins" not in res.metadata["hardware"]
    # nothing rises above the parting face without pegs
    assert res.halves["bottom"].bounds[1][2] == pytest.approx(0.0, abs=1e-4)


@pytest.mark.parametrize("clearance", [0.15, 0.25])
def test_peg_hole_radii_differ_by_exact_clearance(clearance):
    res = generate_mold(sphere(), compression(pin_clearance_mm=clearance))
    assert res.ok, res.errors
    info = res.metadata["hardware"]["pins"]
    r, h, c = info["radius"], info["height"], info["chamfer"]
    center = info["centers"][0]

    # peg: probe the straight section below the chamfer
    r_peg = max_radius(res.halves["bottom"], center, 0.05, h - c + 0.01, r + 1.0)
    # hole: probe up to and including the hole ceiling ring
    r_hole = max_radius(
        res.halves["top"], center, 0.05, h + HOLE_OVERDEPTH + 0.1,
        r + clearance + 1.0,
    )
    assert r_peg == pytest.approx(r, abs=1e-3)
    assert r_hole - r_peg == pytest.approx(clearance, abs=1e-3)


def test_pins_shrunk_when_margin_small():
    # margin 8 → ring midline inset 4 → largest fitting radius 2
    res = generate_mold(sphere(), compression(wall_margin_mm=8.0))
    assert res.ok, res.errors
    assert any("pin radius shrunk" in w for w in res.warnings)
    info = res.metadata["hardware"]["pins"]
    assert info["radius"] == pytest.approx(2.0)
    assert all(h.is_watertight for h in res.halves.values())


def test_pins_skipped_when_margin_tiny():
    # margin 6 → largest fitting radius 1 < minimum useful radius
    res = generate_mold(sphere(), compression(wall_margin_mm=6.0))
    assert res.ok, res.errors
    assert any("pins skipped" in w for w in res.warnings)
    assert "pins" not in res.metadata["hardware"]
    assert res.halves["bottom"].bounds[1][2] == pytest.approx(0.0, abs=1e-4)


def test_bolt_holes_volume_and_count():
    part = sphere()
    plain = generate_mold(part, compression(pins=False))
    bolted = generate_mold(
        part, compression(pins=False, bolts=True, bolt_count=4, bolt_diameter_mm=6.0)
    )
    assert bolted.ok, bolted.errors
    assert all(h.is_watertight for h in bolted.halves.values())

    info = bolted.metadata["hardware"]["bolts"]
    assert len(info["centers"]) == 4
    r = info["radius"]
    assert r == pytest.approx(3.25)  # M6/2 + 0.25 sliding clearance

    # each hole removes exactly its polygon cross-section times half height
    area = 0.5 * CIRCULAR_SEGMENTS * np.sin(2 * np.pi / CIRCULAR_SEGMENTS) * r**2
    height_bottom = 0.0 - (part.bounds[0][2] - 15.0)
    removed = plain.halves["bottom"].volume - bolted.halves["bottom"].volume
    assert removed == pytest.approx(4 * area * height_bottom, rel=1e-3)


def test_bolt_count_two():
    res = generate_mold(sphere(), compression(pins=False, bolts=True, bolt_count=2))
    assert res.ok, res.errors
    info = res.metadata["hardware"]["bolts"]
    assert len(info["centers"]) == 2
    # first two positions are the left/right edge midpoints: same y, mirrored x
    (x0, y0), (x1, y1) = info["centers"]
    assert y0 == pytest.approx(y1)
    assert x0 != pytest.approx(x1)


def test_bolts_ignored_in_open_layup():
    res = generate_mold(
        sphere(),
        MoldParams(mode="open_layup", parting_z_mm=0.0, bolts=True, keep_side="below"),
    )
    assert res.ok, res.errors
    assert "bolts" not in res.metadata["hardware"]
    assert any("bolt" in w for w in res.warnings)


def test_trim_groove():
    part = sphere()
    base = MoldParams(mode="open_layup", parting_z_mm=0.0, keep_side="below")
    plain = generate_mold(part, base)
    grooved = generate_mold(
        part,
        MoldParams(
            mode="open_layup", parting_z_mm=0.0, keep_side="below",
            trim_groove=True, trim_groove_offset_mm=8.0,
        ),
    )
    assert grooved.ok, grooved.errors
    tool = grooved.halves["tool"]
    assert tool.is_watertight

    info = grooved.metadata["hardware"]["trim_groove"]
    x0, y0, x1, y1 = info["rect"]
    perimeter = 2 * ((x1 - x0) + (y1 - y0))
    # V-groove removes ~depth² per unit length (45° V, depth deep)
    estimate = perimeter * info["depth"] ** 2
    removed = plain.halves["tool"].volume - tool.volume
    assert 0.5 * estimate < removed < 1.5 * estimate


def test_trim_groove_ignored_in_compression():
    res = generate_mold(sphere(), compression(trim_groove=True))
    assert res.ok, res.errors
    assert "trim_groove" not in res.metadata["hardware"]
    assert any("trim groove" in w for w in res.warnings)
