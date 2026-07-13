<p align="center">
  <img src="frontend/logo.svg" width="88" alt="moldgen logo" />
</p>

<h1 align="center">moldgen</h1>

<p align="center">
  Turn any 3D model into a ready-to-print (or CNC) mold for carbon fiber work.<br/>
  Forged-carbon compression molds, prepreg laminating tools, and open-layup tools —<br/>
  with the mold-making details already engineered in.
</p>

---

**moldgen** is a local web app: upload a part (STL / 3MF / OBJ — STEP / IGES with an extra), drag a parting plane through it in a 3D viewport, check demoldability, and export watertight mold halves as binary STL. All boolean geometry runs through [manifold3d](https://github.com/elalish/manifold), so the output is guaranteed watertight — sliceable and machinable as-is.

Design rules and default dimensions follow Easy Composites' published mold-making guidance ([forged carbon mould design](https://www.easycomposites.co.uk/learning/forged-carbon-mould-design-and-optimisation), [composite mould CAD techniques](https://www.easycomposites.co.uk/learning/CAD-techniques-for-composite-mould-design)); every setting has a tooltip explaining what it does and where the number comes from.

## Mold styles

| Style | What you get |
|---|---|
| **Compression / forged carbon** | Telescoping two-part mold: the male plug pistons into the female pocket so chopped fiber can't jam a flat seam, and the pinch-off wall gap doubles as the resin escape path. Registration pins, clamping bolt holes, pry slots, jack screws, resin overflow channels, optional ejector-pin hole, wall draft, lead-in chamfer. |
| **Prepreg / laminating tool** | Single-sided female tool: trim-allowance run-out past the part edge with a 5° kick (release aid + witness line to trim to), ~30 mm bagging flange, sealant-tape zone scribe, optional split-mould bush holes (6.1 mm @ 75–100 mm spacing), fabric-drape check for sharp corners. |
| **Open layup** | Basic single-sided female tool with a flat bagging flange and optional trim groove. |

Every generation runs a **demolding check**: undercut faces (part locks — red) and low-draft walls (part drags — amber) are reported per half and highlighted on the model, and the parting height is auto-suggested to minimize undercuts. Printer-process presets (resin / FDM / CNC) set all fit clearances in one click.

## Quick start

Requires **Python 3.11+**. No Node, no database, no build step.

```sh
git clone <this-repo> && cd moldgen

python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m uvicorn moldgen.main:app --reload
```

Open **http://localhost:8000** and drop a model in. (With `make` installed, `make dev` does all of the above and `make test` runs the suite.)

> The frontend loads three.js from a CDN via an import map, so the first page load needs internet access.

### Optional: STEP / IGES input

```sh
.venv/bin/pip install "moldgen[step]"
```

Pulls cadquery/OpenCascade (wheels are several hundred MB, hence opt-in). BRep surfaces are tessellated on upload with configurable linear/angular deflection (0.05 mm / 0.3° defaults); IGES surfaces are sewn into a closed shell automatically. The UI detects support at runtime and enables the formats when present. On a minimal Linux install you may need `libgl1` (`sudo apt install libgl1`).

## Workflow

1. **Upload** — drag a model into the viewport. You get triangle count, dimensions, a watertightness check (with automatic repair attempt), and a units sanity warning if the model looks like it was exported in meters.
2. **Part** — move the parting-plane slider (initialized to the suggested height) and hit *Analyze undercuts*: red faces lock, amber faces drag.
3. **Configure** — pick the mold style and printing process; every dimension is editable and explained on hover.
4. **Generate** — both halves render in the viewport with an explode slider and a ghost-part toggle. Warnings tell you about anything that was shrunk, moved, or skipped to fit — and anything about your part that will fight demolding.
5. **Download** — per-half binary STLs, ready to slice.

## API

The frontend is plain JS over a small JSON API — script it directly if you prefer:

| Endpoint | Purpose |
|---|---|
| `POST /api/upload` | multipart file → mesh id, stats, suggested parting height |
| `POST /api/analyze` | `{mesh_id, parting_z_mm}` → undercut + low-draft faces & per-half verdicts |
| `POST /api/generate` | `{mesh_id, parting_z_mm, mode, ...params}` → metadata + download URLs |
| `GET /api/download/{id}/{half}.stl` | binary STL (`female`, `male`, `tool`, or `part`) |

Engine failures return `{"ok": false, "errors": [...]}` with HTTP 200 — errors are data, not exceptions. Uploaded parts and generated molds live in `./workdir/` (no database).

## Architecture

```
moldgen/
  core/            pure-geometry engine (no web imports, fully unit-tested)
    engine.py        generate_mold(mesh, params) -> MoldResult
    forged.py        telescoping compression construction
    prepreg.py       laminating-tool construction
    hardware.py      pins, bolts, pry slots, jack screws, overflow, scribes
    undercuts.py     demold + drape analysis, parting-plane suggestion
    validate.py      watertightness checks + repair
    step_io.py       optional STEP/IGES tessellation (cadquery/OCP)
  main.py          FastAPI app: JSON API + static frontend
frontend/          vanilla JS + three.js viewport (CDN import map)
tests/             pytest suite — geometry verified by exact volume arithmetic
```

Trimesh handles I/O and analysis; **all** booleans and splits go through manifold3d. Units are millimeters throughout.

## Development

```sh
.venv/bin/python -m pytest        # 48 tests, a few seconds
```

Tests generate primitives programmatically (no fixture files) and verify molds by exact arithmetic — e.g. a z-symmetric icosphere leaves exactly half its volume below the parting plane, and peg/hole radii are measured off the output meshes to confirm they differ by exactly the requested clearance.

## Limitations worth knowing

- The undercut check is first-order (per-face normals): it won't catch silhouette-occluded undercuts like tilted bores. A full check needs ray-cast visibility testing.
- The parting surface is a flat plane; stepped/curved parting surfaces aren't supported (yet).
- Cavity fillets can't be added to your part automatically — the drape check tells you where they're needed.
