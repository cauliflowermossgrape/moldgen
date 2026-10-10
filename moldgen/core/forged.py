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


def build(part_man, part_bounds, z_split, params, warnings, part_mesh=None,
          part_centroid_xy=None):
    """Construct the female and male halves of a telescoping mold.

    Returns (female, male, meta, hw_meta). Raises ValueError when the
    requested dimensions cannot produce a usable flange band.
    `part_mesh` (the trimesh of part_man) is needed for the optional ejector
    hole, which starts its search at `part_centroid_xy`.
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

    female, male, core_info = transfer_cores(female, male, z_split, warnings)

    meta = {
        "z_flange": float(z_flange),
        "plunge_mm": plunge,
        "band_mm": float(band),
        "plug_rect": [cx - plug_w / 2, cy - plug_h / 2, cx + plug_w / 2, cy + plug_h / 2],
        "pocket_clearance_mm": clear,
        "draft_deg": float(params.plug_draft_deg),
        "cores": core_info,
    }
    meta["charge"] = _charge_check(part_man, female, block_min, block_max, z_flange, warnings)

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
            avoid += [tuple(k) for k in pry_info["keepouts"]]

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
        if part_mesh is None:
            part_mesh = mfld.to_trimesh(part_man)
        hole, ej_info = hardware.ejector_hole(
            block_min, part_mesh, z_split, params, warnings, part_centroid_xy
        )
        if hole is not None:
            female = female - hole
            hw_meta["ejector"] = ej_info

    meta["block_bounds"] = [block_min.tolist(), block_max.tolist()]
    return female, male, meta, hw_meta


def transfer_cores(female, male, z_split, warnings):
    """Hand shell cores to the half that can actually pull them.

    A cup cut through its walls leaves the mold material inside it, below
    the plane, sealed off from the female by the shell — a loose body that
    would lock the part. It is exactly the male's job (a core reaching down
    into the cup), and it meets the plug face at the parting plane, so it
    is unioned onto the male; an inverted cup's core goes the other way.
    Bodies that don't reach the plane are internal voids of a hollow part
    — nothing can mold them; they are dropped with a warning.
    """
    info = {"to_male": 0, "to_female": 0, "voids_dropped": 0}
    # manifold stores float32: allow its rounding at the plane's magnitude
    tol = 1e-3 * max(1.0, abs(z_split))

    def split(half, top_edge):
        bodies = mfld.solid_bodies(half)
        if len(bodies) <= 1:
            return half, []
        bodies.sort(key=lambda b: b.volume(), reverse=True)
        main, cores = bodies[0], []
        for body in bodies[1:]:
            bb = np.asarray(body.bounding_box(), dtype=float).reshape(2, 3)
            touches = abs((bb[1, 2] if top_edge else bb[0, 2]) - z_split) <= tol
            if touches:
                cores.append(body)
            else:
                info["voids_dropped"] += 1
        return main, cores

    female, f_cores = split(female, top_edge=True)
    male, m_cores = split(male, top_edge=False)
    for core in f_cores:
        male = male + core
    for core in m_cores:
        female = female + core
    info["to_male"], info["to_female"] = len(f_cores), len(m_cores)
    if f_cores:
        warnings.append(
            f"shell part: {len(f_cores)} core(s) inside the part moved from the "
            "female to the male, which molds the inside surface"
        )
    if m_cores:
        warnings.append(
            f"shell part: {len(m_cores)} core(s) inside the part moved from the "
            "male to the female, which molds the inside surface"
        )
    if info["voids_dropped"]:
        warnings.append(
            "part has sealed internal voids; they can't be molded and were ignored"
        )
    return female, male, info


# Forged carbon (chopped tow + epoxy at ~50% fibre volume) is ~1.5 g/cm³.
FORGED_DENSITY_G_CM3 = 1.5
# Loose chopped fibre bulks to 2-3x its compressed volume; Easy Composites'
# "2-3x the part depth of travel" exists to hold it before closing.
LOADING_BULK_FACTOR = 2.0


def _charge_check(part_man, female, block_min, block_max, z_flange, warnings):
    """Charge estimate and loading-volume check for the compression mold.

    Before closing, the female holds the loose charge in its cavity plus the
    telescoping pocket above it (pocket area x plunge). If that is less
    than ~2x the part volume, the loose fibre won't fit and the plug can't
    start its stroke.
    """
    part_vol = float(part_man.volume())
    region = mfld.box(
        (block_min[0], block_min[1], block_min[2]), (block_max[0], block_max[1], z_flange)
    )
    loading = float((region - female).volume())
    info = {
        "part_volume_cm3": part_vol / 1000.0,
        "est_part_mass_g": part_vol / 1000.0 * FORGED_DENSITY_G_CM3,
        "loading_volume_cm3": loading / 1000.0,
        "loading_ratio": loading / part_vol if part_vol > 0 else 0.0,
    }
    if part_vol > 0 and loading < LOADING_BULK_FACTOR * part_vol:
        warnings.append(
            f"loading volume is {loading / part_vol:.1f}x the part volume; loose "
            "chopped fibre bulks to 2-3x, so the charge may not fit before the "
            "plug engages — increase plunge_mm"
        )
    return info
