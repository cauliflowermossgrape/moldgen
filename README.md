# moldgen

A local web app that takes a 3D model of a part (STL or 3MF) and generates a
two-part mold for carbon fiber work, exportable as STL for 3D printing or CNC
machining.

## Mold styles

- **Compression / forged carbon** — a two-part clamshell mold. The cavity is
  the exact part shape, split across a flat parting plane. Chopped fiber +
  epoxy is loaded in and the halves are pressed together. The mold gets
  registration pins for alignment and clamping bolt holes; a small flash gap
  at the parting line is acceptable.
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

## Layout

```
moldgen/         Python package (FastAPI app, geometry code)
frontend/        index.html + main.js + style.css (three.js viewport & sidebar)
tests/           pytest suite
workdir/         uploads & generated molds (gitignored)
```
