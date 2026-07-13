"""Telescoping (plunge) mold construction for forged carbon compression.

Design follows Easy Composites' forged carbon mould guidance
(easycomposites.co.uk/learning/forged-carbon-mould-design-and-optimisation):

- The male tool acts as a piston inside the female pocket walls (the
  "telescopic section"), leaving room above the cavity to load the chopped
  fiber charge before compression. Piston depth ~6 mm for small tools up to
  15-20 mm for large ones; allow 2-3x the part depth of travel.
- The plug/pocket wall gap is the pinch-off AND the resin escape path:
  excess resin squeezes up it while fiber is retained (~0.1 mm machined,
  more for printed tools). Without a telescoping section, fiber caught at a
  flat seam stops the mold from closing.
- A lead-in chamfer at the pocket mouth (1 mm horizontal, 2 mm vertical in
  their tooling) locates the plug as it enters.
- Both walls carry the same draft so the gap stays constant over the
  plunge; 0° is a true piston like machined tools, 1-2° releases much more
  easily on printed molds.

Geometry (z up, parting plane at z_split):

    block_top ─┬──────────────────────┐
               │        male plate    │
    z_flange ─┤├──┐   ┌──plug──┐   ┌──┤  ← flange seam: pins, bolts,
               ││  │   │ cavity │   │ │     pry slots, overflow channels
    z_split  ─┤│  └───┴────────┴───┘ │  ← pocket floor / plug face
               │  female   cavity     │
    block_min ─┴──────────────────────┘
"""

from __future__ import annotations

import math

import numpy as np
from manifold3d import CrossSection, Manifold

from . import hardware, mfld

MIN_BAND_MM = 6.0  # narrowest usable flange band around the pocket


def _prism(cx, cy, w, h, z0, height, grow_per_side=0.0):
    """Rectangular prism centered at (cx, cy) from z0 up by height, with
    walls flaring outward linearly by grow_per_side at the top."""
    hw, hh = w / 2.0, h / 2.0
    cs = CrossSection([[(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]])
    scale = ((w + 2.0 * grow_per_side) / w, (h + 2.0 * grow_per_side) / h)
    return Manifold.extrude(cs, height, 0, 0.0, scale).translate((cx, cy, z0))


def build(part_man, part_bounds, z_split, params, warnings, part_centroid_xy=None):
    """Construct the female and male halves of a telescoping mold.

    Returns (female, male, meta, hw_meta). Raises ValueError when the
    requested dimensions cannot produce a usable flange band.
    `part_centroid_xy` locates the optional ejector hole.
    """
    m = params.wall_margin_mm
    (px0, py0, pz0), (px1, py1, pz1) = np.asarray(part_bounds, dtype=float)
    plunge = float(params.plunge_mm)
    z_flange = z_split + plunge
    draft_slope = math.tan(math.radians(params.plug_draft_deg))
    g_flange = plunge * draft_slope  # pocket wall growth at flange level, per side

    border = float(params.plug_border_mm)
    clear = float(params.plug_clearance_mm)
    band = m - border - clear - g_flange
    if band < MIN_BAND_MM:
        raise ValueError(
            f"wall_margin_mm={m:g} leaves a {band:.1f} mm flange band around the "
            f"pocket; needs ≥ {MIN_BAND_MM:g} mm — increase wall_margin_mm to at "
            f"least {math.ceil(border + clear + g_flange + MIN_BAND_MM):g}"
        )

    base = float(params.base_thickness_mm)
    block_min = np.array([px0 - m, py0 - m, min(pz0, z_split) - base])
    block_max = np.array([px1 + m, py1 + m, max(pz1, z_flange) + base])

    cx, cy = (px0 + px1) / 2.0, (py0 + py1) / 2.0
    plug_w = (px1 - px0) + 2.0 * border
    plug_h = (py1 - py0) + 2.0 * border

    # --- female: solid to the flange, minus drafted pocket, minus part -----
    pocket = _prism(
        cx, cy, plug_w + 2.0 * clear, plug_h + 2.0 * clear,
        z_split, plunge + 1.0, (plunge + 1.0) * draft_slope,
    )
    female = (
        mfld.box(block_min, (block_max[0], block_max[1], z_flange))
        - pocket
        - part_man
    )

    # lead-in funnel at the pocket mouth (EC: 1 mm horizontal, 2 mm vertical)
    li = float(params.lead_in_mm)
    if li > 0:
        lv = 2.0 * li
        lv = min(lv, plunge * 0.8)  # keep some straight pinch wall
        base_grow = clear + (plunge - lv) * draft_slope  # pocket size at funnel base
        funnel_h = lv + 0.5
        funnel = _prism(
            cx, cy, plug_w + 2.0 * base_grow, plug_h + 2.0 * base_grow,
            z_flange - lv, funnel_h,
            (li + lv * draft_slope) * funnel_h / lv,
        )
        female = female - funnel

    # --- male: drafted plug fused to a full-footprint plate ----------------
    plug_height = float(block_max[2]) - z_split
    plug = _prism(cx, cy, plug_w, plug_h, z_split, plug_height, plug_height * draft_slope)
    plate = mfld.box((block_min[0], block_min[1], z_flange), block_max)
    male = (plug + plate) - part_man

    meta = {
        "z_flange": float(z_flange),
        "plunge_mm": plunge,
        "band_mm": float(band),
        "plug_rect": [cx - plug_w / 2, cy - plug_h / 2, cx + plug_w / 2, cy + plug_h / 2],
        "pocket_clearance_mm": clear,
        "draft_deg": float(params.plug_draft_deg),
    }

    # --- flange hardware ----------------------------------------------------
    hw_meta: dict = {}
    avoid: list[tuple[float, float, float]] = []
    inset = band / 2.0

    if params.pins:
        pegs, pin_holes, pin_info = hardware.registration_pins(
            block_min, block_max, z_flange, inset, params, warnings
        )
        if pin_info is not None:
            female = female + pegs
            male = male - pin_holes
            hw_meta["pins"] = pin_info
            avoid += [
                (x, y, pin_info["radius"] + pin_info["clearance"])
                for x, y in pin_info["centers"]
            ]

    if params.bolts:
        bolt_cyls, bolt_info = hardware.bolt_holes(
            block_min, block_max, inset, params, hw_meta.get("pins"), warnings
        )
        if bolt_cyls is not None:
            female = female - bolt_cyls
            male = male - bolt_cyls
            hw_meta["bolts"] = bolt_info
            avoid += [(x, y, bolt_info["radius"]) for x, y in bolt_info["centers"]]

    if params.jack_screws:
        jacks, jack_info = hardware.jack_screw_holes(
            block_min, block_max, inset, params, avoid, warnings
        )
        if jacks is not None:
            male = male - jacks
            hw_meta["jack_screws"] = jack_info
            avoid += [(x, y, jack_info["radius"]) for x, y in jack_info["centers"]]

    if params.pry_slots:
        slots, pry_info = hardware.pry_slot_solids(
            block_min, block_max, z_flange, params, avoid, warnings
        )
        if slots is not None:
            female = female - slots
            male = male - slots
            hw_meta["pry_slots"] = pry_info
            avoid += [
                (x, y, pry_info["width"] / 2.0) for x, y in pry_info["centers"]
            ]

    if params.overflow_channels:
        pocket_at_flange = [
            cx - plug_w / 2 - clear - g_flange,
            cy - plug_h / 2 - clear - g_flange,
            cx + plug_w / 2 + clear + g_flange,
            cy + plug_h / 2 + clear + g_flange,
        ]
        chans, of_info = hardware.overflow_channel_solids(
            block_min, block_max, z_flange, pocket_at_flange, params, avoid, warnings
        )
        if chans is not None:
            female = female - chans
            hw_meta["overflow"] = of_info

    if params.ejector_holes:
        center = part_centroid_xy if part_centroid_xy is not None else (cx, cy)
        hole, ej_info = hardware.ejector_hole(block_min, center, z_split, params, warnings)
        female = female - hole
        hw_meta["ejector"] = ej_info

    meta["block_bounds"] = [block_min.tolist(), block_max.tolist()]
    return female, male, meta, hw_meta
