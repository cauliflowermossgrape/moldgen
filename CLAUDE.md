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
  map (`moldgen/frontend/index.html`). No bundler, no node_modules — FastAPI serves
  `moldgen/frontend/` directly (inside the package, so wheels and the
  packaged app ship it).
- **Storage**: no database. Uploaded parts and generated molds live in
  `./workdir/` (gitignored) when running from a checkout; an installed or
  packaged app uses the per-user data dir (`main._default_workdir`;
  `MOLDGEN_WORKDIR` overrides).

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
  the part centroid — or, when the pin disc wouldn't land fully on the
  part's underside there (rings, C-shapes), the nearest spot where it does
  (vertical rays); plug with wax/tape before layup.
- **Shell cores**: a cup cut through its walls leaves the mold material
  inside it sealed off from the female by the shell. `forged.transfer_cores`
  finds these bodies (`mfld.solid_bodies`) and unions them onto the male
  (inverted shells: onto the female); the single-sided tools drop them
  (`mfld.keep_main_body`) — the inside is the bagged side.
- **Charge / loading check**: metadata `forged.charge` estimates the part
  mass at 1.5 g/cm³ and warns when cavity + pocket (loading volume) is
  under 2x the part volume (loose chopped fibre bulks 2-3x).
- **Demolding check** (on by default): generation warns per half about
  undercut area (part locks — wrong-facing normals AND faces whose pull ray
  is blocked by other part geometry) and low-draft walls within
  `min_draft_deg` of vertical (part drags; common practice is 1-2° minimum
  draft). Faces are attributed to halves the way the construction builds
  them (shell cores included). The Analyze view colors undercuts red,
  occluded ("hidden") undercuts purple, low-draft walls amber, core-molded
  faces cyan.
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
    vertices as float32), `drafted_extrude` (extrude a CrossSection with
    every wall leaning out by an EXACT offset — manifold's own extrude only
    scales, which gives concave outlines no draft), `solid_bodies` /
    `keep_main_body` (connected solids with their voids), `signed_area`.
  - `forged.py` — telescoping compression construction: female block minus
    drafted pocket, male drafted plug + plate, lead-in funnel, and
    flange-hardware orchestration. The plug outline is the part's projected
    silhouette grown by plug_border (`plug_shape="contour"`, default) or its
    bbox rectangle ("rect"); hardware positions still use the rectangle's
    flange band, which bounds the contour. Overflow channels run inward and
    are clipped 1 mm inside the plug outline so they never cut a core
    handed to the female. Errors (ValueError) when the flange band
    (margin − border − clearance − draft growth) is under 6 mm.
  - `hardware.py` — registration pins (pegs + clearance holes), clamping
    bolt through-holes with 50-75 mm spacing warning, pry slots, resin
    overflow channels, flange trim groove. All built as manifold solids
    and applied with booleans; features that don't fit are shrunk or
    skipped with a warning, never a hard failure. Positions live on the
    flange-band midline, with (x, y, radius) keep-out collision checks
    between feature types.
  - `validate.py` — watertightness checks + best-effort trimesh repair.
  - `offset.py` — cavity offset solving n·d = offset per vertex over the
    incident face normals, so flat faces move exactly (incl. at sharp
    edges/corners); see docstring for self-intersection caveats.
  - `rays.py` — `VerticalRayCaster`: numpy XY-grid ray casting along ±Z
    (first hit, crossing count, inside test). manifold3d's `ray_cast` is
    one ray per Python call — too slow per face.
  - `io.py` — `load_part` (STL/3MF/OBJ, plus STEP/IGES when the "step"
    extra is installed) and `export_stl` (binary).
  - `step_io.py` — optional STEP/IGES tessellation via cadquery/OCP behind
    the `step` pyproject extra (OCP wheels are heavy). `HAS_STEP` is the
    runtime capability flag; `/api/health` reports it and the UI greys the
    formats out when missing. Never import cadquery outside this module.
  - `report.py` — the plain-text setup sheet (ZIP bundle and CLI).
  - `undercuts.py` — `PartVisibility` (per-face ±Z ray distances, computed
    once per part; the API caches it), `analyze_undercuts` (normals +
    occlusion + shell cores by exact connectivity, `single_sided` for
    prepreg/open layup), `parting_profile` and `suggest_parting_plane`
    (coarse + fine scan using a cheaper core heuristic; two-part ties go to
    the middle of the tied band, single-sided `side="below"/"above"` to the
    plane covering the most part).
- `moldgen/cli.py` — `moldgen generate|analyze|serve`; its options are
  generated from `MoldParams` too.
- `moldgen/main.py` — FastAPI app, serves `/api/*` and the static frontend.
  `GenerateRequest` is derived from the `MoldParams` dataclass — add new
  engine params there only. Requests reject NaN/Infinity (a NaN reaching
  manifold3d can segfault the server; `_check_params` guards too).
  Generated STLs are served `no-store` (same path rewritten on regenerate);
  `/api/download/{id}/mold.zip` bundles halves + setup sheet.
- Tests generate primitives programmatically (no fixture files) and verify
  mold halves by exact volume arithmetic (those tests pin
  `plug_shape="rect"`) — manifold booleans are exact on
  the input mesh, so e.g. a z-symmetric icosphere leaves exactly half its
  volume below the parting plane.

## Sizing gotcha

`wall_margin_mm` is a HORIZONTAL requirement (the flange band must fit pins
and bolts, ~14 mm minimum plus plug border). Vertical shell thickness is the
separate `base_thickness_mm` (default 10): material under the deepest cavity
point and above it (male plate / tool floor). Never reuse the wall margin
vertically — that's how thin parts ended up with 40 mm-thick molds.

## Packaging & releases

- `packaging/build_app.py` builds the standalone app for the current OS
  (PyInstaller, one-folder, zipped). It stages a copy of the frontend with
  three.js VENDORED and the import map rewritten to `./vendor/three/` — the
  packaged app must work offline; the repo frontend keeps the CDN. The
  three.js version is read from the import map: change it there only.
- Entry point `moldgen/desktop.py`: no args → server on a free port
  8000-8019 + browser; args → CLI. Anything the app imports lazily or by
  string must be bundled (`--collect-submodules` in build_app.py).
- `packaging/smoke_test.py` runs the frozen binary (CLI + app mode).
- `.github/workflows/release.yml`: tag `v<version>` → version check
  (`packaging/release_notes.py --check`), tests, wheel/sdist, apps on
  Windows / macOS arm64 / Ubuntu 22.04, GitHub Release with the CHANGELOG
  section. Manual dispatch = test build, no release.

## Dev workflow

- `make dev` — create venv if needed, install deps, run uvicorn with reload on
  http://localhost:8000 (serves API + frontend together).
- `make test` — run pytest; `make lint` — ruff check + format check (CI
  runs both on Python 3.11-3.13, plus a CLI smoke test).
- Units are millimeters throughout (STL convention).
- `decompose()` returns internal voids as separate inside-out shells
  (negative volume) — always group with `mfld.solid_bodies` before reasoning
  about connected bodies.
- Parting-plane nudges go INTO the part (down past a top face): nudging out
  seals the cavity under a 1 µm skin.

## Export integrity (read before adding geometry)

manifold3d guarantees *topological* manifoldness, but when two surfaces
merely touch — a feature edge lying exactly on a part edge, a union of
pieces whose faces should coincide — it emits distinct vertices at
identical coordinates. Fine in memory; non-manifold the moment a slicer
welds the STL. `tests/test_export_integrity.py` round-trips every half
through STL; run a wide sweep (shapes x modes x parting heights) after any
construction change. Patterns that avoid it:

- Never build a feature from the part's own outline vertices at the same
  height (the prepreg run-out grows its base 20 µm and turns it by an
  irrational micro-angle). Never let cylinders share planes with a
  revolved part's meridians (the ejector pin is rotated by √3°).
- Build convex features as one hull (`Manifold.batch_hull`), not a union of
  pieces that meet face to face (registration pegs).
- Start a feature that must cross a wall slightly *inside* it rather than
  exactly on it (lead-in funnel, `FUNNEL_INSET`).
- Clean offset outlines before extruding (`drafted_extrude` simplifies:
  Clipper emits points nanometres apart).
