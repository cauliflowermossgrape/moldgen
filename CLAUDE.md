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

A two-part **telescoping** (plunge) mold — NOT a flat clamshell. The female
half's pocket walls rise `plunge_mm` above the parting plane and the male
half's plug pistons down into them. This matters because chopped fiber loaded
in the cavity would get caught at a flat seam and stop the mold from
compressing; with a telescoping section the plug always closes fully, and the
plug/pocket wall gap (the **pinch-off**, ~0.1 mm machined / 0.15 resin /
0.25-0.3 FDM) doubles as the resin escape path while fiber is retained.

Design values follow Easy Composites' forged carbon mould guidance
(easycomposites.co.uk/learning/forged-carbon-mould-design-and-optimisation):
piston depth ~6 mm small tools up to 15-20 mm large (2-3x part depth of
travel to load the charge), 1x2 mm lead-in chamfer at the pocket mouth,
wedge features at the seam for separation, bolt spacing within 50-75 mm on
printed tools.

Features (see `forged.py` + `hardware.py`):
- **Registration pins** on the female flange corners, holes in the male plate.
- **Clamping bolt holes** distributed around the flange band.
- **Pry (wedge) slots** across the flange seam to break the halves apart.
- **Jack screws**: tap-sized holes through the male plate over the female
  flange — a threaded bolt bears on the female face and jacks the halves
  apart evenly (holes are bolt Ø − 1 mm for self-threading in prints).
- **Ejector pin hole** (opt-in): through the female base into the cavity at
  the part centroid; plug with wax/tape before layup.
- **Demolding check** (on by default): generation warns per half about
  undercut area (part locks) and low-draft walls within `min_draft_deg` of
  vertical (part drags; common practice is 1-2° minimum draft). The Analyze
  view colors undercuts red and low-draft walls amber.
- **Resin overflow channels** on the female flange, from pinch gap to outer
  wall, preventing hydraulic lock.
- Shared **wall draft** (default 1°) on plug and pocket so the gap stays
  constant; 0° = true piston like machined tools.

Halves are keyed/exported as **"female"** and **"male"**.

### 2. Prepreg / laminating tool

A single-sided female tool for prepreg or wet-lay vacuum work, mode
"prepreg". Design rules follow Easy Composites' composite mould CAD guidance
(easycomposites.co.uk/learning/CAD-techniques-for-composite-mould-design):

- **Trim allowance** (5-10 mm for prepreg): the cavity opening continues
  `trim_allowance_mm` past the part edge with a `trim_kick_deg` (default 5°)
  outward kick — the laminate runs out past the finished edge, the kick aids
  release AND leaves a witness line on the part to trim to. Built in
  `prepreg.py` by slicing the part at the parting plane (Manifold.slice) and
  extruding the cross-section with a scale approximating the kick.
- **Flange**: ~30 mm is enough for a prepreg tool (frontend default); the
  flange face sits at the top of the run-out (`flange_z`).
- **Sealant-tape zone scribe**: shallow (0.2 mm) V-loop marking the inner
  edge of the bagging-tape zone; closed loop inside the bag = no leak path.
- **Flange bush holes** (opt-in): EC split-mould system — 6.1 mm holes,
  20-25 mm from the mould surface edge, 75-100 mm spacing (auto count from
  `flange_hole_spacing_mm`).
- **Drape check**: warns on part edges sharper than 45° on the tool side
  (fabric bridges tight mold corners; EC recommend generous fillets).
- Metadata carries cure/sealing notes (low-temp initial cure for printed
  tools, sealing porous surfaces, CTE mismatch).

### 3. Open layup

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
  - `forged.py` — telescoping compression construction: female block minus
    drafted pocket prism, male drafted plug + plate, lead-in funnel, and
    flange-hardware orchestration. Errors (ValueError) when the flange band
    (margin − border − clearance − draft growth) is under 6 mm.
  - `hardware.py` — registration pins (pegs + clearance holes), clamping
    bolt through-holes with 50-75 mm spacing warning, pry slots, resin
    overflow channels, flange trim groove. All built as manifold solids
    and applied with booleans; features that don't fit are shrunk or
    skipped with a warning, never a hard failure. Positions live on the
    flange-band midline, with (x, y, radius) keep-out collision checks
    between feature types.
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
