"""What a slicer sees: every exported half must survive an STL round trip
(float32 vertices, positions welded on load) as a closed, manifold solid.

manifold3d guarantees topological manifoldness of its own meshes, but two
surfaces that merely touch can leave distinct vertices at identical
coordinates — invisible in memory, non-manifold after any loader welds
them. This catches that whole class of construction bug.
"""

import io

import numpy as np
import pytest
import trimesh

from moldgen.core import MoldParams, generate_mold


def cup():
    outer = trimesh.creation.cylinder(radius=20.0, height=30.0, sections=48)
    inner = trimesh.creation.cylinder(radius=17.0, height=30.0, sections=48)
    inner.apply_translation([0, 0, 3.0])
    return trimesh.boolean.difference([outer, inner], engine="manifold")


def l_part():
    notch = trimesh.creation.box(extents=[30.0, 30.0, 20.0])
    notch.apply_translation([15.0, 15.0, 0.0])
    return trimesh.boolean.difference(
        [trimesh.creation.box(extents=[60.0, 60.0, 10.0]), notch], engine="manifold"
    )


PARTS = {
    "sphere": lambda: trimesh.creation.icosphere(subdivisions=3, radius=20.0),
    "torus": lambda: trimesh.creation.torus(major_radius=25.0, minor_radius=6.0),
    "cup": cup,
    "L": l_part,
    # surfaces of revolution: the ejector pin sits on their axis, where its
    # edges used to share planes with the part's meridians
    "capsule": lambda: trimesh.creation.capsule(height=40.0, radius=8.0),
    "cone": lambda: trimesh.creation.cone(radius=12.0, height=20.0, sections=48),
}
MODES = {
    "compression": dict(mode="compression", parting_z_mm=0.0, ejector_holes=True, bolts=True),
    "compression-rect": dict(mode="compression", parting_z_mm=0.0, plug_shape="rect"),
    "prepreg": dict(mode="prepreg", parting_z_mm=0.0, wall_margin_mm=30.0, flange_bolt_holes=True),
    "open_layup": dict(mode="open_layup", parting_z_mm=0.0, trim_groove=True),
}


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("part", list(PARTS))
def test_halves_survive_stl_round_trip(part, mode):
    mesh = PARTS[part]()
    kw = dict(MODES[mode])
    kw["parting_z_mm"] = float(mesh.bounds[:, 2].mean()) + 0.37  # off any facet row
    res = generate_mold(mesh, MoldParams(**kw))
    assert res.ok, res.errors
    for name, half in res.halves.items():
        v = half.vertices
        assert len(np.unique(v, axis=0)) == len(v), f"{name}: coincident vertices"
        loaded = trimesh.load(io.BytesIO(trimesh.exchange.stl.export_stl(half)), file_type="stl")
        assert loaded.is_watertight, f"{name} not watertight after STL round trip"
        assert loaded.is_winding_consistent
        assert loaded.volume == pytest.approx(half.volume, rel=1e-5)
