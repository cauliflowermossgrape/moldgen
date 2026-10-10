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

The kick is approximated by scaling the part's parting-plane cross-section
about its bounding-box center — exact only for outlines equidistant from
that center, but at sub-millimeter offsets (8 mm × tan 5° ≈ 0.7 mm) the
deviation is irrelevant to release or the witness line.
"""

from __future__ import annotations

import math

import numpy as np
from manifold3d import Manifold

from . import hardware, mfld

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
    extended `trim` past it with an outward kick."""
    cs = part_man.slice(float(z_split))
    polygons = cs.to_polygons()
    if not polygons or cs.area() <= 0:
        return None
    points = np.vstack(polygons)
    lo, hi = points.min(axis=0), points.max(axis=0)
    cx, cy = (lo + hi) / 2.0
    w, h = hi - lo
    height = trim + 1.0  # overshoot past the flange face for a clean boolean
    grow = math.tan(math.radians(kick_deg)) * height
    scale = (
        (w + 2.0 * grow) / w if w > 0 else 1.0,
        (h + 2.0 * grow) / h if h > 0 else 1.0,
    )
    prism = Manifold.extrude(cs.translate((-cx, -cy)), height, 0, 0.0, scale)
    if not below:
        prism = prism.mirror((0.0, 0.0, 1.0))
    return prism.translate((cx, cy, float(z_split)))
