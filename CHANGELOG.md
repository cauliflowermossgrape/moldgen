# Changelog

All notable changes to moldgen. Versions follow [semantic versioning](https://semver.org/).

## 0.2.0

### Mold construction
- **Shell parts** (cups, cases, panels): mold material trapped inside the shell
  is built onto the opposite half as a core (compression) or removed
  (single-sided tools, where the inside is the bagged side). Previously the
  female STL held a loose core and the part could not be demolded.
- **Contoured telescoping section** (`plug_shape="contour"`, the new default):
  plug and pocket follow the part outline grown by the plug border, with exact
  draft and lead-in, instead of the bounding rectangle — less wasted charge,
  shorter flash line. `plug_shape="rect"` keeps the rectangle.
- **Exact prepreg kick**: the trim-allowance run-out is a true offset of the
  part outline, so concave outlines (L-shapes, notches) get the full kick;
  scaling about the center left their inner edges with none.
- **Exact cavity offset**: flat faces move by exactly `cavity_offset_mm`,
  sharp edges and corners included (was 1/√3 short on box-like parts).
- **Shrinkage compensation** (`shrink_comp_pct`).
- Ejector pin lands squarely on the part's underside (ring/C-shaped parts no
  longer get a hole into empty space).
- Overflow channels slide to a clear spot instead of being dropped; pry slots
  fall back to the other pair of edges when crowded.
- Charge-weight estimate and loading-volume check for forged molds.

### Export integrity
- Every half survives an STL/3MF round trip as a closed manifold solid. Fixed
  several constructions that left touching edges (distinct vertices at
  identical coordinates — valid in memory, non-manifold once a slicer welds
  the file): V-groove scribe corners, lead-in funnel on contoured pockets,
  registration-pin chamfer seams, prepreg run-out on revolved parts, and the
  ejector pin on the axis of revolved parts. A 1,000-mold sweep across shapes,
  modes and parting heights is clean.
- 3MF download for every half (explicit millimeter units).

### Analysis
- Occlusion-aware demolding check: faces whose pull path is blocked by other
  part geometry (tilted bores, overhangs) are flagged, not just faces whose
  normal points the wrong way.
- Faces are attributed to the half that actually molds them (shell cores
  included), so warnings describe the mold that is built.
- Per-face **draft-angle map** in the viewer.
- Parting-plane suggestion per mold style, with an undercut-vs-height chart;
  a 1M-triangle part is analysed in seconds (was minutes).

### App, API, CLI
- `moldgen` command-line tool: `generate`, `analyze`, `serve`; every engine
  parameter is an option.
- Upload units (mm / cm / m / in), settings remembered in the browser, view
  presets and shortcuts (1/2/3/F), section view, busy overlay, narrow-screen
  layout.
- ZIP download with every half plus a setup sheet.
- Hardening: non-finite numbers are rejected (a NaN used to crash the server
  process), unexpected errors come back as JSON, generated files are never
  served from cache, upload size cap, stale uploads pruned after 7 days.
- CI on Python 3.11-3.13 with ruff lint/format checks.

## 0.1.0

Initial release: forged carbon compression molds, prepreg laminating tools and
open-layup tools from STL/3MF/OBJ/STEP parts, with registration pins, bolts,
pry slots, jack screws, overflow channels and a demolding check.
