"""Release bookkeeping for the release workflow.

    python packaging/release_notes.py --check v0.2.0   # versions agree? (exit 1 if not)
    python packaging/release_notes.py 0.2.0            # print that CHANGELOG section

--check verifies that the tag, moldgen.__version__, pyproject.toml and
CHANGELOG.md all name the same version, so a release can't ship with a
stale version string or without notes.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def package_version() -> str:
    text = (REPO / "moldgen" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("no __version__ in moldgen/__init__.py")
    return match.group(1)


def pyproject_version() -> str:
    with (REPO / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def changelog_section(version: str) -> str | None:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    return match.group(1).strip() if match else None


def check(tag: str | None) -> list[str]:
    problems = []
    version = package_version()
    if pyproject_version() != version:
        problems.append(
            f"pyproject.toml says {pyproject_version()}, moldgen.__version__ says {version}"
        )
    if tag is not None and tag != f"v{version}":
        problems.append(f"tag {tag} doesn't match the package version v{version}")
    if not changelog_section(version):
        problems.append(f"CHANGELOG.md has no '## {version}' section")
    return problems


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--check":
        problems = check(argv[1] if len(argv) > 1 else None)
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        if not problems:
            print(f"version {package_version()} is consistent")
        return 1 if problems else 0
    version = argv[0].lstrip("v") if argv else package_version()
    notes = changelog_section(version)
    if notes is None:
        print(f"error: CHANGELOG.md has no '## {version}' section", file=sys.stderr)
        return 1
    print(notes)
    print(
        "\n---\n**Downloads:** the `moldgen-…-windows/macos/linux` zips are standalone "
        "apps (no Python needed) — unzip and run `moldgen`; see README.txt inside. "
        "The `.whl` is the Python package (`pip install moldgen-….whl`)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
