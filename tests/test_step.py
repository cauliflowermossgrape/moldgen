"""STEP input test — skipped unless the "step" extra is installed."""

import pytest

# not importorskip: a present-but-unloadable cadquery (e.g. missing system
# libGL) raises plain ImportError, which pytest 8 reports as a collection
# error instead of a skip
try:
    import cadquery as cq
except ImportError:
    pytest.skip(
        'STEP extra not installed or not loadable (pip install "moldgen[step]")',
        allow_module_level=True,
    )

from moldgen.core import MoldParams, generate_mold  # noqa: E402
from moldgen.core.io import load_part, supported_suffixes  # noqa: E402


def test_step_tessellation_feeds_pipeline(tmp_path):
    assert ".step" in supported_suffixes()

    # box with a through-bore: exercises planar and cylindrical faces
    part = cq.Workplane("XY").box(30, 20, 10).faces(">Z").workplane().hole(6)
    path = tmp_path / "part.step"
    cq.exporters.export(part, str(path))

    mesh = load_part(path)
    assert mesh.is_watertight
    assert mesh.extents == pytest.approx([30.0, 20.0, 10.0], abs=0.05)

    # finer angular deflection follows the bore more closely: more triangles
    coarse = load_part(path, angular_deflection_deg=10.0)
    assert len(mesh.faces) > len(coarse.faces)

    # tessellated STEP feeds the normal mold pipeline
    res = generate_mold(mesh, MoldParams(mode="compression", parting_z_mm=0.0))
    assert res.ok, res.errors
    assert all(h.is_watertight for h in res.halves.values())
