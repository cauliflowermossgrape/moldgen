"""Command-line interface: batch mold generation without the browser.

    moldgen generate part.stl --mode compression --out molds/
    moldgen generate bracket.step --units in --parting-z 12.5 --bolts --bolt-count 8
    moldgen analyze part.stl --mode prepreg
    moldgen serve --port 8000

Every engine parameter is an option, generated from the MoldParams
dataclass (so the CLI can't drift from the engine): `--plunge-mm 14`,
`--plug-shape rect`, booleans as `--bolts` / `--no-bolts`. The parting
height defaults to the suggestion for the chosen mold style.

Exit status: 0 success, 1 the part/settings can't produce a mold (errors
are printed), 2 bad command line.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import typing
from pathlib import Path

import trimesh

from moldgen import __version__
from moldgen.core import MoldParams, analyze_undercuts, export_stl, generate_mold, load_part
from moldgen.core.engine import VALID_MODES
from moldgen.core.report import setup_sheet
from moldgen.core.undercuts import PartVisibility, suggest_parting_plane

UNIT_SCALE = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
# set by the subcommand, or derived from the part — not engine options
_NOT_OPTIONS = {"mode", "parting_z_mm"}


def _param_options(parser: argparse.ArgumentParser) -> None:
    hints = typing.get_type_hints(MoldParams)
    group = parser.add_argument_group("mold parameters (see MoldParams)")
    for f in dataclasses.fields(MoldParams):
        if f.name in _NOT_OPTIONS:
            continue
        flag = "--" + f.name.replace("_", "-")
        kind = hints[f.name]
        if kind is bool:
            group.add_argument(
                flag,
                action=argparse.BooleanOptionalAction,
                default=None,
                help=f"(default: {'on' if f.default else 'off'})",
            )
        else:
            group.add_argument(
                flag,
                type=kind,
                default=None,
                metavar=kind.__name__.upper(),
                help=f"(default: {f.default})",
            )


def _load(path: str, units: str) -> trimesh.Trimesh:
    if not Path(path).is_file():
        raise ValueError(f"no such file: {path}")
    mesh = load_part(path)
    if units != "mm":
        mesh.apply_scale(UNIT_SCALE[units])
    return mesh


def _side(mode: str, keep_side: str) -> str | None:
    return None if mode == "compression" else keep_side


def _params(args: argparse.Namespace, z: float) -> MoldParams:
    overrides = {
        f.name: getattr(args, f.name)
        for f in dataclasses.fields(MoldParams)
        if f.name not in _NOT_OPTIONS and getattr(args, f.name, None) is not None
    }
    return MoldParams(mode=args.mode, parting_z_mm=z, **overrides)


def _parting_height(args, mesh, vis) -> float:
    if args.parting_z is not None:
        return args.parting_z
    keep = args.keep_side or "below"
    return suggest_parting_plane(mesh, side=_side(args.mode, keep), visibility=vis)


def cmd_generate(args: argparse.Namespace) -> int:
    mesh = _load(args.part, args.units)
    vis = PartVisibility(mesh)
    z = _parting_height(args, mesh, vis)
    params = _params(args, z)
    result = generate_mold(mesh, params, visibility=vis)
    for w in result.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if not result.ok:
        for e in result.errors:
            print(f"error: {e}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, half in result.halves.items():
        path = out / f"{args.prefix}{name}.{args.format}"
        if args.format == "3mf":
            path.write_bytes(trimesh.exchange.threemf.export_3MF(half))
        else:
            export_stl(half, path)
        written.append(str(path))
    record = {
        "version": __version__,
        "params": dataclasses.asdict(params),
        "warnings": result.warnings,
        "metadata": result.metadata,
    }
    (out / f"{args.prefix}setup.txt").write_text(setup_sheet(record))
    (out / f"{args.prefix}params.json").write_text(json.dumps(record, indent=2))
    if args.json:
        print(json.dumps({"ok": True, "files": written, **record}, indent=2))
    else:
        print(f"parting plane z = {result.metadata['parting_z_mm']:.3f} mm")
        for path in written:
            print(f"wrote {path}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    mesh = _load(args.part, args.units)
    vis = PartVisibility(mesh)
    z = _parting_height(args, mesh, vis)
    keep = args.keep_side or "below"
    report = analyze_undercuts(
        mesh,
        z,
        min_draft_deg=args.min_draft_deg,
        single_sided=args.mode != "compression",
        visibility=vis,
    )
    if args.mode == "compression":
        halves = {"female": report.bottom, "male": report.top}
    else:
        halves = {"tool": report.bottom if keep == "below" else report.top}
    summary = {
        "parting_z_mm": z,
        "halves": {k: dataclasses.asdict(v) for k, v in halves.items()},
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"parting plane z = {z:.3f} mm ({args.mode})")
        for name, h in halves.items():
            verdict = (
                "LOCKS"
                if not h.likely_demoldable
                else "drags"
                if h.low_draft_area_mm2 > 0
                else "releases cleanly"
            )
            print(
                f"  {name:7s} {verdict:17s} undercut {h.undercut_area_mm2:8.1f} mm²  "
                f"low draft {h.low_draft_area_mm2:8.1f} mm²"
            )
    return 0 if all(h.likely_demoldable for h in halves.values()) else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("moldgen.main:app", host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moldgen",
        description="Generate molds for carbon fiber work from STL/3MF/OBJ/STEP parts.",
    )
    parser.add_argument("--version", action="version", version=f"moldgen {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def part_args(p):
        p.add_argument("part", help="part file (STL, 3MF, OBJ; STEP/IGES with the step extra)")
        p.add_argument("--mode", choices=VALID_MODES, default="compression")
        p.add_argument(
            "--units",
            choices=list(UNIT_SCALE),
            default="mm",
            help="units the file was exported in (default: mm)",
        )
        p.add_argument(
            "--parting-z",
            type=float,
            default=None,
            metavar="MM",
            help="parting plane height (default: the suggested height)",
        )
        p.add_argument("--json", action="store_true", help="machine-readable output")

    gen = sub.add_parser("generate", help="build the mold and write its halves")
    part_args(gen)
    gen.add_argument("--out", default=".", help="output directory (default: .)")
    gen.add_argument("--format", choices=("stl", "3mf"), default="stl")
    gen.add_argument("--prefix", default="moldgen-", help="output file name prefix")
    _param_options(gen)
    gen.set_defaults(func=cmd_generate)

    ana = sub.add_parser("analyze", help="demolding check at a parting height (exit 1 if it locks)")
    part_args(ana)
    ana.add_argument("--keep-side", choices=("below", "above"), default="below")
    ana.add_argument("--min-draft-deg", type=float, default=1.0)
    ana.set_defaults(func=cmd_analyze)

    srv = sub.add_parser("serve", help="run the web app")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
