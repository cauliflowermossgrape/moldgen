# moldgen

A local web app that takes a 3D model of a part (STL or 3MF) and generates a
two-part mold for carbon fiber work, exportable as STL for 3D printing or CNC
machining.

## Mold styles

- **Compression / forged carbon** — a two-part **telescoping** mold: the male
  half's plug pistons into the female half's pocket walls, so chopped fiber
  can't jam a flat seam and excess resin escapes up the pinch-off gap while
  fiber is retained. Includes registration pins, clamping bolt holes, pry
  (wedge) slots and jack screws for opening the cured mold, an optional
  ejector-pin hole, resin overflow channels, wall draft and a lead-in
  chamfer. Generation runs a demolding check: undercuts (part locks) and
  low-draft walls (part drags; <1–2° from vertical) are reported per half
  and highlighted red/amber in the analyzer. Defaults follow [Easy Composites' forged
  carbon mould design guidance](https://www.easycomposites.co.uk/learning/forged-carbon-mould-design-and-optimisation),
  with per-process clearance presets for resin-printed, FDM-printed and
  CNC-machined tools.
- **Open layup** — a single-sided female tool. You pick which side of the
  parting plane to keep; the flat parting face becomes a natural flange around
  the cavity for vacuum bagging tape.

## Architecture

| Layer    | Tech                                                                 |
|----------|----------------------------------------------------------------------|
| Backend  | Python 3.11+, FastAPI + uvicorn                                      |
| Geometry | trimesh + manifold3d (all booleans via manifold — watertight-safe)   |
| Frontend | Vanilla JS + three.js (CDN import map, no build step)                |
| Storage  | `./workdir/` folder for uploads and generated molds — no database    |

The FastAPI app serves both the JSON API (under `/api`) and the static
frontend, so `make dev` starts everything on a single port.

## Getting started

```sh
make dev      # sets up .venv, installs deps, runs http://localhost:8000
make test     # runs pytest
```

### Optional: STEP / IGES input

Mesh formats (STL/3MF/OBJ) work out of the box. To upload STEP or IGES
directly, install the `step` extra — it pulls cadquery/OpenCascade, whose
wheels are several hundred MB, so it's opt-in:

```sh
.venv/bin/pip install "moldgen[step]"
```

BRep surfaces are tessellated on upload with configurable deflections
(defaults: 0.05 mm linear sagitta, 0.3° angular). The UI detects support at
runtime and enables the formats + tessellation settings automatically.

## Layout

```
moldgen/         Python package (FastAPI app, geometry code)
frontend/        index.html + main.js + style.css (three.js viewport & sidebar)
tests/           pytest suite
workdir/         uploads & generated molds (gitignored)
```
