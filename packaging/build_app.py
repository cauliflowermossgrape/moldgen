"""Build the standalone desktop app for the current platform.

    python packaging/build_app.py            # → dist/moldgen-<version>-<os>-<arch>.zip

Steps:
1. Stage a copy of the web UI with three.js vendored next to it and the
   import map pointed at the local copy — the packaged app must work
   offline (the dev setup loads three.js from a CDN). The version comes from
   the import map in index.html, so there is one source of truth.
2. Freeze `moldgen.desktop` with PyInstaller (one folder, console app: the
   window shows the URL and closing it quits the server).
3. Zip the folder with a short README for the platform.

PyInstaller can't cross-compile: run this on each target OS (the release
workflow does, on GitHub's Windows, macOS and Linux runners).
Needs `pip install pyinstaller` and network access for the three.js
download, or `--three-dir` pointing at an unpacked `three` npm package.
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import re
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONTEND = REPO / "moldgen" / "frontend"
BUILD = REPO / "build" / "app"
DIST = REPO / "dist"

# the three.js files the UI imports (index.html import map + main.js)
THREE_FILES = [
    "build/three.module.js",
    "examples/jsm/controls/OrbitControls.js",
    "examples/jsm/loaders/STLLoader.js",
]
CDN_RE = re.compile(r"https://cdn\.jsdelivr\.net/npm/three@([0-9.]+)/")

APP_README = """moldgen {version}
================

Start: double-click "{exe}" (inside this folder). A window opens showing
the address, and the app opens in your web browser. Close the window to quit.

Your uploads and generated molds are stored in:
  {data_dir}

Command line: "{exe}" generate part.stl --out molds/   (see "{exe}" --help)

{platform_note}
Everything runs on this computer; nothing is uploaded anywhere.
https://github.com/cauliflowermossgrape/moldgen
"""

PLATFORM_NOTES = {
    "windows": (
        "First start: Windows SmartScreen may warn about an unrecognised app —\n"
        'click "More info" -> "Run anyway" (the build is not code-signed).\n'
    ),
    "macos": (
        "First start: macOS blocks unsigned apps. Right-click (or Ctrl-click)\n"
        '"moldgen" -> Open -> Open, or run once in Terminal:\n'
        "  xattr -dr com.apple.quarantine /path/to/this/folder\n"
    ),
    "linux": 'Run "./moldgen" from a terminal if your file manager doesn\'t start it.\n',
}

DATA_DIRS = {
    "windows": r"%LOCALAPPDATA%\moldgen\workdir",
    "macos": "~/Library/Application Support/moldgen/workdir",
    "linux": "~/.local/share/moldgen/workdir",
}


def target() -> tuple[str, str]:
    os_name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        machine, machine
    )
    return os_name, arch


def three_version() -> str:
    match = CDN_RE.search((FRONTEND / "index.html").read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("could not find the three.js CDN import map in index.html")
    return match.group(1)


def fetch_three(version: str, three_dir: Path | None) -> dict[str, bytes]:
    if three_dir is not None:
        return {f: (three_dir / f).read_bytes() for f in THREE_FILES}
    url = f"https://registry.npmjs.org/three/-/three-{version}.tgz"
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for f in THREE_FILES:
            member = tar.extractfile(f"package/{f}")
            if member is None:
                raise SystemExit(f"three@{version} has no {f}")
            out[f] = member.read()
    return out


def stage_frontend(three_dir: Path | None) -> Path:
    """Copy the UI and vendor three.js so it runs without internet."""
    staged = BUILD / "frontend"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(FRONTEND, staged)
    version = three_version()
    for rel, data in fetch_three(version, three_dir).items():
        dest = staged / "vendor" / "three" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    index = staged / "index.html"
    html = index.read_text(encoding="utf-8")
    html, n = CDN_RE.subn("./vendor/three/", html)
    if n != 2:
        raise SystemExit(f"expected 2 three.js import-map entries, rewrote {n}")
    index.write_text(html, encoding="utf-8")
    print(f"vendored three.js {version} into {staged}")
    return staged


def freeze(frontend: Path) -> Path:
    import PyInstaller.__main__

    entry = BUILD / "moldgen_app.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "import sys\nfrom moldgen.desktop import main\n\nsys.exit(main())\n", encoding="utf-8"
    )
    PyInstaller.__main__.run(
        [
            str(entry),
            "--name=moldgen",
            "--onedir",
            "--console",
            "--noconfirm",
            "--clean",
            f"--distpath={BUILD / 'dist'}",
            f"--workpath={BUILD / 'work'}",
            f"--specpath={BUILD}",
            f"--add-data={frontend}{os.pathsep}moldgen/frontend",
            # imported lazily / by string: make sure they're bundled
            "--collect-submodules=moldgen",
            "--collect-submodules=uvicorn",
            "--collect-data=trimesh",
            "--hidden-import=multipart",
            "--hidden-import=python_multipart",
            # not used by moldgen; keeps the bundle smaller
            "--exclude-module=tkinter",
            "--exclude-module=matplotlib",
            "--exclude-module=IPython",
        ]
    )
    return BUILD / "dist" / "moldgen"


def package(app_dir: Path, version: str) -> Path:
    os_name, arch = target()
    exe = "moldgen.exe" if os_name == "windows" else "moldgen"
    (app_dir / "README.txt").write_text(
        APP_README.format(
            version=version,
            exe=exe,
            data_dir=DATA_DIRS[os_name],
            platform_note=PLATFORM_NOTES[os_name],
        ),
        encoding="utf-8",
    )
    DIST.mkdir(exist_ok=True)
    archive = DIST / f"moldgen-{version}-{os_name}-{arch}.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(app_dir.rglob("*")):
            arcname = Path("moldgen") / path.relative_to(app_dir)
            info = zipfile.ZipInfo.from_file(path, arcname)
            if path.is_file():
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as f:
                    zf.writestr(info, f.read())
            else:
                zf.writestr(info, b"")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--three-dir", type=Path, help="unpacked `three` npm package to vendor")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO))
    from moldgen import __version__

    frontend = stage_frontend(args.three_dir)
    app_dir = freeze(frontend)
    archive = package(app_dir, __version__)
    size = archive.stat().st_size / 1e6
    print(f"\nbuilt {archive.relative_to(REPO)} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
