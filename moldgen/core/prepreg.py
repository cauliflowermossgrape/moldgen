r"""Prepreg / laminating tool construction.

Design rules follow Easy Composites' 'Practical CAD Techniques for Composite
Pattern/Mould Design' (easycomposites.co.uk/learning/CAD-techniques-for-
composite-mould-design) and their prepreg mould tutorials:

- **Trim allowance**: the mould surface continues 5-10 mm past the finished
  part edge so the laminate can run out and be trimmed back accurately. It
  is built from the part edge along Z with a ~5° outward kick, which both
  aids release and leaves a visible witness line on the part to trim to.
- **Flange**: ~30 mm of finished flange is enough for a prepreg tool; it
  carries the vacuum-bag sealant tape (scribe line marks the tape zone).
- **Draft**: composite parts want >3° of draft (the demold check warns).
- **Split-mould hardware**: bush/dowel holes 20-25 mm from the mould surface
  edge at 75-100 mm spacing; the M6 bush system uses 6.1 mm holes.

Geometry (keep_side="below" shown; "above" is mirrored):

    flange_z ──────┐  ┌── kick ──┐  ┌────────  ← flange face: tape scribe,
                   │  \  run-out  /  │             bush holes
    z_split  ──────┤   ├─ cavity ─┤  ├────────  ← part edge / witness line
                   │   └──────────┘  │
    block_min ─────┴─────────────────┴────────

The kick is a true offset of the parting-plane outline (mfld.drafted_extrude),
so every edge of the run-out wall gets the same angle, concave ones included.
"""

from __future__ import annotations

import math

import numpy as np
from manifold3d import JoinType

from . import hardware, mfld

RUN_OUT_STEP = 0.02  # mm, see _run_out_prism

CURE_NOTES = [
    "3D-printed tools: use a low-temperature-cure prepreg for the initial "
    "cure (~80-85 °C) and post-cure the part free-standing at full "
    "temperature; check the print material's HDT",
    "seal printed or porous tool surfaces (e.g. epoxy coat + board/mould "
    "sealer) before applying release agent, or the bag will leak through "
    "the tool",
    "composite/epoxy tooling matches the part's thermal expansion; printed "
    "and metal tools do not — expect some spring-in on large parts",
]


SHELL_DROP_NOTE = (
    "shell part: {n} body(ies) enclosed by the part removed from the tool — "
    "on a single-sided tool the inside of the shell is the bagged side"
)


def build(part_man, part_bounds, z_split, params, warnings):
    """Construct a prepreg laminating tool. Returns (tool, meta, hw_meta)."""
    m = params.wall_margin_mm
    (px0, py0, pz0), (px1, py1, pz1) = np.asarray(part_bounds, dtype=float)
    below = params.keep_side == "below"
    trim = float(params.trim_allowance_mm)
    flange_z = z_split + trim if below else z_split - trim

    base = float(params.base_thickness_mm)
    if below:
        block_min = np.array([px0 - m, py0 - m, min(pz0, z_split) - base])
        block_max = np.array([px1 + m, py1 + m, flange_z])
    else:
        block_min = np.array([px0 - m, py0 - m, flange_z])
        block_max = np.array([px1 + m, py1 + m, max(pz1, z_split) + base])

    tool = mfld.box(block_min, block_max) - part_man

    extension = _run_out_prism(part_man, z_split, trim, params.trim_kick_deg, below)
    if extension is not None:
        tool = tool - extension
    else:
        warnings.append(
            "part has no cross-section at the parting plane; trim-allowance run-out skipped"
        )

    tool, dropped = mfld.keep_main_body(tool)
    if dropped:
        warnings.append(SHELL_DROP_NOTE.format(n=dropped))

    meta = {
        "flange_z": float(flange_z),
        "trim_allowance_mm": trim,
        "trim_kick_deg": float(params.trim_kick_deg),
        "notes": list(CURE_NOTES),
        "block_bounds": [block_min.tolist(), block_max.tolist()],
    }

    hw_meta: dict = {}
    if params.tape_line:
        solid, info = hardware.tape_line_solid(
            block_min, block_max, part_bounds, flange_z, params, warnings
        )
        if solid is not None:
            tool = tool - solid
            hw_meta["tape_line"] = info
    if params.flange_bolt_holes:
        solid, info = hardware.flange_hole_solids(block_min, block_max, m, params, warnings)
        if solid is not None:
            tool = tool - solid
            hw_meta["flange_holes"] = info
            warnings.append(
                "bush holes pass through the flange inside the vacuum seal — fit "
                "the bushes/bolts or tape over the holes on the back face before "
                "bagging, or the bag will leak through them"
            )
    return tool, meta, hw_meta


def _run_out_prism(part_man, z_split, trim, kick_deg, below):
    """The laminate run-out void: part cross-section at the parting plane,
    extended `trim` past it with every edge kicked outward by kick_deg — a
    true offset of the outline, so concave outlines (L-shapes, notches) get
    the same kick as convex ones."""
    cs = part_man.slice(float(z_split))
    if cs.is_empty() or cs.area() <= 0:
        return None
    height = trim + 1.0  # overshoot past the flange face for a clean boolean
    grow = math.tan(math.radians(kick_deg)) * height
    # The base outline IS the part's outline at the plane: its corners sit
    # on the part's own edges, and on a revolved part the walls rising from
    # them share planes with the part's meridian edges — edge-on-edge
    # intersections that leave coincident vertices (non-manifold once a
    # slicer welds the STL). So the base is grown by a 20 µm step and turned
    # by an irrational micro-angle about its center (moving no point more
    # than a quarter of the step, so it still contains the part outline):
    # a deterministic perturbation that makes every intersection generic.
    base = cs.offset(RUN_OUT_STEP, JoinType.Round, 2.0, 0)
    (x0, y0), (x1, y1) = (np.asarray(b) for b in _bounds2d(base))
    c = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    reach = max(math.hypot(x1 - x0, y1 - y0) / 2.0, 1.0)
    twist = math.degrees(0.25 * RUN_OUT_STEP / reach) / math.sqrt(2.0)
    base = base.translate((-c[0], -c[1])).rotate(twist).translate(c)
    prism = mfld.drafted_extrude(base, height, grow)
    if not below:
        prism = prism.mirror((0.0, 0.0, 1.0))
    return prism.translate((0.0, 0.0, float(z_split)))


def _bounds2d(cs):
    pts = np.vstack([np.asarray(p) for p in cs.to_polygons()])
    return pts.min(axis=0), pts.max(axis=0)
