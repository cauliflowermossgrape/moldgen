"""Smoke-test a frozen moldgen build the way a user runs it.

    python packaging/smoke_test.py [path/to/moldgen-executable]

1. `moldgen --version` and `moldgen generate` on a test part (the geometry
   engine works frozen, every half watertight).
2. Start it with no arguments (double-click mode): it must print its URL,
   answer /api/health, serve the UI and the vendored three.js (so it works
   offline), then shut down cleanly.

Default executable: build/app/dist/moldgen/moldgen(.exe), as left by
packaging/build_app.py. Exit status 0 = all good.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_EXE = (
    REPO
    / "build"
    / "app"
    / "dist"
    / "moldgen"
    / ("moldgen.exe" if sys.platform == "win32" else "moldgen")
)


def fail(msg: str) -> int:
    print(f"SMOKE TEST FAILED: {msg}", file=sys.stderr)
    return 1


def run_cli(exe: Path, tmp: Path, env: dict) -> int:
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, env=env)
    if out.returncode != 0 or "moldgen" not in out.stdout:
        return fail(f"--version: {out.returncode} {out.stdout!r} {out.stderr!r}")
    print(out.stdout.strip())

    import trimesh

    part = tmp / "part.stl"
    trimesh.creation.torus(major_radius=25.0, minor_radius=6.0).export(part)
    out = subprocess.run(
        [str(exe), "generate", str(part), "--out", str(tmp / "out"), "--ejector-holes"],
        capture_output=True,
        text=True,
        env=env,
    )
    if out.returncode != 0:
        return fail(f"generate exited {out.returncode}: {out.stderr}")
    for name in ("female", "male"):
        mesh = trimesh.load(tmp / "out" / f"moldgen-{name}.stl")
        if not mesh.is_watertight:
            return fail(f"{name} half is not watertight")
    print("cli generate: ok (both halves watertight)")
    return 0


def run_app(exe: Path, env: dict) -> int:
    env = dict(env, BROWSER="none")  # don't try to open a browser on CI
    proc = subprocess.Popen(
        [str(exe)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
    )
    try:
        url = None
        deadline = time.monotonic() + 120
        while url is None and time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    return fail(f"app exited early with {proc.returncode}")
                continue
            print("  |", line.rstrip())
            match = re.search(r"running at\s+(http://\S+)", line)
            if match:
                url = match.group(1)
        if url is None:
            return fail("app never printed its URL")
        for path, needle in (
            ("api/health", '"status":"ok"'),
            ("", "<title>moldgen</title>"),
            ("vendor/three/build/three.module.js", "REVISION"),
            ("vendor/three/examples/jsm/loaders/STLLoader.js", "STLLoader"),
        ):
            body = _get(url + path, deadline)
            if body is None or needle not in body:
                return fail(f"GET /{path} did not return {needle!r}")
        print(f"app mode: ok ({url}, UI and offline three.js served)")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def _get(url: str, deadline: float) -> str | None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.read().decode("utf-8", "replace")
        except OSError:
            time.sleep(0.5)
    return None


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXE
    if not exe.is_file():
        return fail(f"no executable at {exe}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # keep the test's uploads out of the runner's real user data dir
        env = dict(os.environ, MOLDGEN_WORKDIR=str(tmp / "workdir"))
        return run_cli(exe, tmp, env) or run_app(exe, env)


if __name__ == "__main__":
    sys.exit(main())
