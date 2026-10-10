"""Command-line interface: options mirror MoldParams, outputs land on disk,
exit codes mean something."""

import json

import pytest
import trimesh

from moldgen.cli import main


@pytest.fixture()
def mushroom(tmp_path):
    stem = trimesh.creation.cylinder(radius=5.0, height=20.0, sections=48)
    stem.apply_translation([0, 0, 10.0])
    cap = trimesh.creation.cylinder(radius=15.0, height=5.0, sections=48)
    cap.apply_translation([0, 0, 22.5])
    path = tmp_path / "mushroom.stl"
    trimesh.boolean.union([stem, cap], engine="manifold").export(path)
    return path


def test_generate_writes_halves_and_setup_sheet(mushroom, tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["generate", str(mushroom), "--out", str(out), "--bolts", "--plunge-mm", "14"])
    assert code == 0
    for name in ("female", "male"):
        mesh = trimesh.load(out / f"moldgen-{name}.stl")
        assert mesh.is_watertight
    record = json.loads((out / "moldgen-params.json").read_text())
    assert record["params"]["plunge_mm"] == 14.0 and record["params"]["bolts"] is True
    # parting height defaulted to the suggestion (inside the cap band)
    assert 20.0 < record["metadata"]["parting_z_mm"] < 25.0
    assert "setup sheet" in (out / "moldgen-setup.txt").read_text()


def test_generate_3mf_single_sided_with_units(mushroom, tmp_path):
    out = tmp_path / "out"
    code = main(
        [
            "generate",
            str(mushroom),
            "--mode",
            "prepreg",
            "--units",
            "cm",
            "--wall-margin-mm",
            "120",
            "--format",
            "3mf",
            "--out",
            str(out),
            "--no-tape-line",
        ]
    )
    assert code == 0
    tool = trimesh.load(out / "moldgen-tool.3mf", force="mesh")
    assert tool.is_watertight
    assert tool.extents[0] > 300  # 3 cm part → 300 mm + margins


def test_analyze_exit_code_reflects_verdict(mushroom, capsys):
    assert main(["analyze", str(mushroom), "--parting-z", "10"]) == 1  # cap locks
    assert "LOCKS" in capsys.readouterr().out
    assert main(["analyze", str(mushroom), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["halves"]["female"]["likely_demoldable"]


def test_errors_are_reported_not_raised(mushroom, tmp_path, capsys):
    assert main(["generate", str(mushroom), "--parting-z", "999", "--out", str(tmp_path)]) == 1
    assert "outside the mold block" in capsys.readouterr().err
    assert main(["generate", str(tmp_path / "missing.stl")]) == 1
    assert "no such file" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(mushroom), "--mode", "nope"])
    assert exc.value.code == 2
