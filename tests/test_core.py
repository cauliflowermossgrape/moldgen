"""Engine tests. All geometry is generated programmatically — no fixture files.

Volume assertions rely on manifold's booleans being exact on the input mesh:
e.g. an icosphere is z-symmetric, so the cavity below the z=0 parting plane is
exactly half the mesh volume, up to manifold's float32 vertex precision.
"""

import numpy as np
import pytest
import trimesh

from moldgen.core import MoldParams, generate_mold, export_stl
from moldgen.core import mfld

MARGIN = 15.0
REL_TOL = 1e-4


def block_volume_below(part, parting_z, margin=MARGIN):
    lo = part.bounds[0] - margin
    hi = part.bounds[1] + margin
    return (hi[0] - lo[0]) * (hi[1] - lo[1]) * (parting_z - lo[2])


def block_volume(part, margin=MARGIN):
    return float(np.prod(part.extents + 2 * margin))


def test_sphere_compression():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    res = generate_mold(
        sphere, MoldParams(mode="compression", parting_z_mm=0.0, wall_margin_mm=MARGIN, pins=False)
    )
    assert res.ok, res.errors
    top, bottom = res.halves["top"], res.halves["bottom"]
    assert top.is_watertight and bottom.is_watertight

    # icosphere is symmetric about z=0, so the hemisphere below the parting
    # plane is exactly half the mesh volume
    expected_bottom = block_volume_below(sphere, 0.0) - sphere.volume / 2
    assert bottom.volume == pytest.approx(expected_bottom, rel=REL_TOL)
    expected_total = block_volume(sphere) - sphere.volume
    assert top.volume + bottom.volume == pytest.approx(expected_total, rel=REL_TOL)


def test_tube_annular_cross_section():
    # tube (hollow cylinder) standing across the parting plane: the parting
    # face of each half shows an annulus — an island (the bore core) inside a
    # hole (the cavity)
    outer = trimesh.creation.cylinder(radius=15.0, height=60.0, sections=96)
    inner = trimesh.creation.cylinder(radius=8.0, height=80.0, sections=96)
    tube = mfld.to_trimesh(mfld.to_manifold(outer) - mfld.to_manifold(inner))
    assert tube.is_watertight

    res = generate_mold(
        tube, MoldParams(mode="compression", parting_z_mm=0.0, wall_margin_mm=MARGIN, pins=False)
    )
    assert res.ok, res.errors
    top, bottom = res.halves["top"], res.halves["bottom"]
    assert top.is_watertight and bottom.is_watertight

    # both source cylinders are z-symmetric, so the tube below z=0 is half
    expected_bottom = block_volume_below(tube, 0.0) - tube.volume / 2
    assert bottom.volume == pytest.approx(expected_bottom, rel=REL_TOL)

    # a slice just below the parting plane must show 3 closed loops:
    # block outline, cavity wall (r=15), and the bore core island (r=8)
    section = bottom.section(plane_origin=[0, 0, -0.5], plane_normal=[0, 0, 1])
    assert section is not None
    assert len(section.discrete) == 3


def test_rotated_box_watertight():
    box = trimesh.creation.box(extents=[30.0, 20.0, 10.0])
    box.apply_transform(
        trimesh.transformations.rotation_matrix(np.radians(30), [1.0, 1.0, 0.0])
    )
    res = generate_mold(
        box, MoldParams(mode="compression", parting_z_mm=0.0, wall_margin_mm=MARGIN, pins=False)
    )
    assert res.ok, res.errors
    top, bottom = res.halves["top"], res.halves["bottom"]
    assert top.is_watertight and bottom.is_watertight
    expected_total = block_volume(box) - box.volume
    assert top.volume + bottom.volume == pytest.approx(expected_total, rel=REL_TOL)


def test_coplanar_facets_nudged():
    # parting plane exactly on the box's top face: split must be nudged, not
    # produce a broken mesh
    box = trimesh.creation.box(extents=[30.0, 20.0, 10.0])  # top face at z=5
    res = generate_mold(
        box, MoldParams(mode="compression", parting_z_mm=5.0, wall_margin_mm=MARGIN, pins=False)
    )
    assert res.ok, res.errors
    assert any("nudged" in w for w in res.warnings)
    top, bottom = res.halves["top"], res.halves["bottom"]
    assert top.is_watertight and bottom.is_watertight
    expected_total = block_volume(box) - box.volume
    assert top.volume + bottom.volume == pytest.approx(expected_total, rel=REL_TOL)


def test_open_layup_keep_below():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    res = generate_mold(
        sphere,
        MoldParams(
            mode="open_layup", parting_z_mm=0.0, wall_margin_mm=MARGIN,
            keep_side="below",
        ),
    )
    assert res.ok, res.errors
    assert set(res.halves) == {"tool"}
    tool = res.halves["tool"]
    assert tool.is_watertight
    expected = block_volume_below(sphere, 0.0) - sphere.volume / 2
    assert tool.volume == pytest.approx(expected, rel=REL_TOL)
    # the tool sits entirely below the parting plane (plus float32 slack)
    assert tool.bounds[1][2] == pytest.approx(0.0, abs=1e-3)


def test_cavity_offset_grows_cavity():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    plain = generate_mold(sphere, MoldParams(mode="compression", parting_z_mm=0.0, pins=False))
    offset = generate_mold(
        sphere, MoldParams(mode="compression", parting_z_mm=0.0, cavity_offset_mm=0.5, pins=False)
    )
    assert plain.ok and offset.ok

    # the block also grows (it is sized from the offset part's bbox), so
    # compare cavity volumes, not mold-half volumes
    def cavity(res):
        material = sum(h.volume for h in res.halves.values())
        return res.metadata["block"]["volume"] - material

    assert cavity(offset) > cavity(plain)
    # and the offset cavity should be close to the offset sphere's volume
    r = 20.5
    assert cavity(offset) == pytest.approx(4 / 3 * np.pi * r**3, rel=0.02)


def test_non_watertight_input_structured_error():
    # an open sheet cannot be repaired into a solid: expect a structured
    # error result, not an exception
    sheet = trimesh.Trimesh(
        vertices=[[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]],
        faces=[[0, 1, 2], [0, 2, 3]],
        process=False,
    )
    res = generate_mold(sheet, MoldParams(mode="compression", parting_z_mm=0.0, pins=False))
    assert res.ok is False
    assert res.errors
    assert res.halves == {}


def test_bad_params_rejected():
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    res = generate_mold(sphere, MoldParams(mode="injection", parting_z_mm=0.0))
    assert not res.ok and any("mode" in e for e in res.errors)
    res = generate_mold(
        sphere, MoldParams(mode="compression", parting_z_mm=999.0)
    )
    assert not res.ok and any("parting_z" in e for e in res.errors)


def test_export_stl_binary(tmp_path):
    box = trimesh.creation.box(extents=[5.0, 5.0, 5.0])
    path = export_stl(box, tmp_path / "box.stl")
    data = path.read_bytes()
    # binary STL: 80-byte header + uint32 count + 50 bytes per triangle
    assert len(data) == 84 + 50 * len(box.faces)
    reloaded = trimesh.load(path)
    assert reloaded.volume == pytest.approx(box.volume)
