# moldgen

Local web app: upload a 3D part (STL or 3MF), generate a two-part mold for carbon
fiber work, export the mold halves as STL for 3D printing or CNC.

## Architecture

- **Backend**: Python 3.11+, FastAPI + uvicorn. Package lives in `moldgen/`,
  entry point `moldgen/main.py`. API routes under `/api/*`; the frontend is
  served as static files at `/`.
- **Geometry**: trimesh + manifold3d. **All boolean operations must go through
  manifold3d** (`trimesh.boolean` with `engine="manifold"`, or the manifold3d
  API directly) — it guarantees watertight results. Do NOT use trimesh's
  built-in booleans or Blender.
- **Frontend**: single-page vanilla JS + three.js, loaded from CDN via an import
  map (`frontend/index.html`). No bundler, no node_modules — FastAPI serves
  `frontend/` directly.
- **Storage**: no database. Uploaded parts and generated molds live in
  `./workdir/` (gitignored).

## Domain: mold styles

This context drives all geometry features.

### 1. Compression / forged carbon

A two-part clamshell mold. The cavity is the exact part shape, split across a
**flat parting plane**. Chopped fiber + epoxy is loaded in and the halves are
pressed together.

Requirements:
- **Registration pins** so the halves align repeatably.
- **Clamping bolt holes** to press and hold the halves together under load.
- A small **flash gap** at the parting line is acceptable (excess resin
  squeezes out there).

### 2. Open layup

A single-sided **female** tool. The user picks which side of the parting plane
to keep; the flat parting face becomes a natural **flange** around the cavity,
used for vacuum bagging tape (sealant tape sticks to the flat flange).

## Code layout

- `moldgen/core/` — pure-geometry engine, no FastAPI imports, fully
  unit-testable. Entry point: `generate_mold(mesh, MoldParams) -> MoldResult`
  in `engine.py`. `MoldResult` is a structured success-or-error value
  (`ok`, `halves`, `errors`, `warnings`, `metadata`) — validation failures
  return errors, they don't raise.
  - `mfld.py` — trimesh ↔ manifold3d conversion (note: manifold stores
    vertices as float32).
  - `hardware.py` — registration pins (pegs + clearance holes), clamping
    bolt through-holes, flange trim groove. All built as manifold solids
    and applied with booleans; features that don't fit the wall margin are
    shrunk or skipped with a warning, never a hard failure. Positions live
    on the margin-ring midline (wall_margin/2 in from the outer wall).
  - `validate.py` — watertightness checks + best-effort trimesh repair.
  - `offset.py` — vertex-normal cavity offset (approximate; see docstring
    for self-intersection caveats).
  - `io.py` — `load_part` (STL/3MF/OBJ, plus STEP/IGES when the "step"
    extra is installed) and `export_stl` (binary).
  - `step_io.py` — optional STEP/IGES tessellation via cadquery/OCP behind
    the `step` pyproject extra (OCP wheels are heavy). `HAS_STEP` is the
    runtime capability flag; `/api/health` reports it and the UI greys the
    formats out when missing. Never import cadquery outside this module.
  - `undercuts.py` — `analyze_undercuts` (first-order per-face normal check
    against the ±Z pull directions; does NOT catch silhouette-occluded
    undercuts — that needs visibility testing) and `suggest_parting_plane`
    (scans 50 candidate heights for minimum undercut area).
- `moldgen/main.py` — FastAPI app, serves `/api/*` and the static frontend.
- Tests generate primitives programmatically (no fixture files) and verify
  mold halves by exact volume arithmetic — manifold booleans are exact on
  the input mesh, so e.g. a z-symmetric icosphere leaves exactly half its
  volume below the parting plane.

## Dev workflow

- `make dev` — create venv if needed, install deps, run uvicorn with reload on
  http://localhost:8000 (serves API + frontend together).
- `make test` — run pytest.
- Units are millimeters throughout (STL convention).
