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


def _windows_like(tmp_path, *args, io_encoding="cp1252"):
    """Run the CLI the way Windows does by default: stdout in the legacy
    code page, and any file I/O that doesn't name an encoding is an error
    (on Windows it would silently use cp1252 and choke on ≈, ≥, −)."""
    import os
    import subprocess
    import sys

    env = dict(os.environ, PYTHONIOENCODING=f"{io_encoding}:strict", PYTHONUTF8="0")
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "warn_default_encoding",
            "-W",
            "error::EncodingWarning",
            "-m",
            "moldgen",
            *args,
        ],
        capture_output=True,
        env=env,
        cwd=tmp_path,
    )


def test_generate_on_windows_like_encodings(mushroom, tmp_path):
    # regression: the v0.2.0 Windows build crashed writing the setup sheet
    out = tmp_path / "out"
    proc = _windows_like(tmp_path, "generate", str(mushroom), "--out", str(out))
    assert proc.returncode == 0, proc.stderr.decode("cp1252", "replace")
    sheet = (out / "moldgen-setup.txt").read_text(encoding="utf-8")
    assert "≈" in sheet  # written as UTF-8, not mangled


def test_output_survives_legacy_code_page(mushroom, tmp_path):
    # stdout on a redirected Windows console is cp1252: printing a path it
    # can't encode used to abort the run (stderr is always forgiving in
    # Python, stdout is not). It must print a replacement character instead.
    out = tmp_path / "模具"  # not representable in cp1252
    proc = _windows_like(tmp_path, "generate", str(mushroom), "--out", str(out))
    assert proc.returncode == 0, proc.stderr.decode("cp1252", "replace")
    assert "wrote" in proc.stdout.decode("cp1252")
    assert (out / "moldgen-female.stl").is_file()
