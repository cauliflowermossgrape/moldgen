"""Mold hardware: registration pins, clamping bolt holes, flange trim groove.

Every feature is a manifold solid applied with a boolean op, so the mold
halves stay watertight. Pin and bolt positions live on the parting-face
"margin ring" — the flat band between the part bbox and the outer wall — on
its midline (wall_margin/2 in from the wall). Features that don't fit are
shrunk or skipped with a warning, never a hard failure.
"""

from __future__ import annotations

import math

import numpy as np
from manifold3d import Manifold

CIRCULAR_SEGMENTS = 64
MIN_PIN_RADIUS = 1.5  # below this a pin is structurally useless; skip instead
MIN_PIN_HEIGHT = 2.0
RING_CLEARANCE = 2.0  # min material between a pin/hole wall and bbox/outer wall
PEG_EMBED = 1.0  # pegs extend this deep into the bottom half's body
HOLE_OVERDEPTH = 0.5  # holes are this much deeper than the peg is tall
HOLE_CAP = 1.5  # solid material kept above the hole ceiling
BOLT_RADIAL_CLEARANCE = 0.25  # sliding fit for the named bolt size


def _union(solids: list[Manifold]) -> Manifold:
    acc = solids[0]
    for s in solids[1:]:
        acc = acc + s
    return acc


def _ring_positions(block_min, block_max, inset):
    """Corner and edge-midpoint xy positions on the margin-ring midline."""
    x0, y0 = float(block_min[0]) + inset, float(block_min[1]) + inset
    x1, y1 = float(block_max[0]) - inset, float(block_max[1]) - inset
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edge_mids = [(x0, cy), (x1, cy), (cx, y0), (cx, y1)]
    return corners, edge_mids


def registration_pins(block_min, block_max, z_split, params, warnings):
    """Alignment pegs for the bottom half and matching holes for the top.

    Returns (pegs, holes, info) manifolds+metadata, or (None, None, None)
    when pins cannot fit. Pegs get a 45° top chamfer for easy engagement;
    holes are oversized by the radial clearance and cut HOLE_OVERDEPTH deeper
    than the peg so it never bottoms out.
    """
    margin = params.wall_margin_mm
    inset = margin / 2.0
    r = params.pin_radius_mm
    r_fit = inset - RING_CLEARANCE
    if r > r_fit:
        if r_fit < MIN_PIN_RADIUS:
            warnings.append(
                f"wall_margin_mm={margin:g} leaves no room for registration "
                f"pins (largest fitting radius {max(r_fit, 0.0):g} mm); pins skipped"
            )
            return None, None, None
        warnings.append(
            f"pin radius shrunk from {r:g} to {r_fit:g} mm to fit "
            f"wall_margin_mm={margin:g}"
        )
        r = r_fit

    h = params.pin_height_mm
    h_fit = float(block_max[2]) - z_split - HOLE_OVERDEPTH - HOLE_CAP
    if h > h_fit:
        if h_fit < MIN_PIN_HEIGHT:
            warnings.append("top mold half is too thin for registration pins; pins skipped")
            return None, None, None
        warnings.append(f"pin height shrunk from {h:g} to {h_fit:g} mm to fit the top half")
        h = h_fit

    c = min(params.pin_chamfer_mm, 0.4 * r, 0.4 * h)
    clearance = params.pin_clearance_mm
    corners, _ = _ring_positions(block_min, block_max, inset)

    pegs, holes = [], []
    for x, y in corners:
        shaft = Manifold.cylinder(
            PEG_EMBED + h - c, r, r, CIRCULAR_SEGMENTS
        ).translate((x, y, z_split - PEG_EMBED))
        if c > 0:
            chamfer = Manifold.cylinder(c, r, r - c, CIRCULAR_SEGMENTS).translate(
                (x, y, z_split + h - c)
            )
            pegs.append(shaft + chamfer)
        else:
            pegs.append(shaft)
        holes.append(
            Manifold.cylinder(
                PEG_EMBED + h + HOLE_OVERDEPTH, r + clearance, r + clearance,
                CIRCULAR_SEGMENTS,
            ).translate((x, y, z_split - PEG_EMBED))
        )

    info = {
        "centers": [[float(x), float(y)] for x, y in corners],
        "radius": float(r),
        "height": float(h),
        "chamfer": float(c),
        "clearance": float(clearance),
    }
    return _union(pegs), _union(holes), info


def bolt_holes(block_min, block_max, params, pin_info, warnings):
    """Vertical clamping-bolt through-holes on the margin ring.

    bolt_count 1–4 fills edge midpoints in order left, right, front, back.
    Returns (cylinders, info) to subtract from the mold before splitting, or
    (None, None) if no hole fits.
    """
    margin = params.wall_margin_mm
    inset = margin / 2.0
    r = params.bolt_diameter_mm / 2.0 + BOLT_RADIAL_CLEARANCE
    if r > inset - RING_CLEARANCE:
        warnings.append(
            f"wall_margin_mm={margin:g} too small for {params.bolt_diameter_mm:g} mm "
            "bolt holes; bolts skipped"
        )
        return None, None

    _, edge_mids = _ring_positions(block_min, block_max, inset)
    kept = []
    for x, y in edge_mids[: params.bolt_count]:
        if pin_info is not None:
            min_gap = r + pin_info["radius"] + pin_info["clearance"] + 1.0
            if any(
                math.hypot(x - px, y - py) < min_gap
                for px, py in pin_info["centers"]
            ):
                warnings.append(
                    f"bolt hole at ({x:.1f}, {y:.1f}) too close to a "
                    "registration pin; that hole skipped"
                )
                continue
        kept.append((x, y))
    if not kept:
        return None, None

    z0 = float(block_min[2]) - 1.0
    height = float(block_max[2] - block_min[2]) + 2.0
    cylinders = [
        Manifold.cylinder(height, r, r, CIRCULAR_SEGMENTS).translate((x, y, z0))
        for x, y in kept
    ]
    info = {
        "centers": [[float(x), float(y)] for x, y in kept],
        "radius": float(r),
        "bolt_diameter": float(params.bolt_diameter_mm),
    }
    return _union(cylinders), info


def trim_groove_solid(part_bounds, block_min, block_max, z_split, params, warnings):
    """V-groove rectangle embossed on the open-layup flange as a trim guide.

    Built from four square prisms rotated 45° about their long axis: the
    diamond cross-section reads as a `depth`-deep, 90°-included V where it
    crosses the flange face. Returns (solid, info) or (None, None).
    """
    depth = params.trim_groove_depth_mm
    offset = params.trim_groove_offset_mm
    rect_min = np.asarray(part_bounds[0][:2], dtype=float) - offset
    rect_max = np.asarray(part_bounds[1][:2], dtype=float) + offset
    lim_min = np.asarray(block_min[:2], dtype=float) + RING_CLEARANCE
    lim_max = np.asarray(block_max[:2], dtype=float) - RING_CLEARANCE
    clamped_min = np.maximum(rect_min, lim_min)
    clamped_max = np.minimum(rect_max, lim_max)
    if not (np.allclose(clamped_min, rect_min) and np.allclose(clamped_max, rect_max)):
        warnings.append("trim groove pulled inward to stay on the flange")
    if np.any(clamped_max - clamped_min <= 2.0 * depth):
        warnings.append("no room on the flange for a trim groove; groove skipped")
        return None, None

    (x0, y0), (x1, y1) = clamped_min, clamped_max
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    s = depth * math.sqrt(2.0)  # square side whose half-diagonal equals depth
    lx = (x1 - x0) + 2.0 * depth  # run past the corners so the loop closes
    ly = (y1 - y0) + 2.0 * depth
    prisms = [
        Manifold.cube((lx, s, s), True).rotate((45.0, 0.0, 0.0)).translate((cx, y0, z_split)),
        Manifold.cube((lx, s, s), True).rotate((45.0, 0.0, 0.0)).translate((cx, y1, z_split)),
        Manifold.cube((s, ly, s), True).rotate((0.0, 45.0, 0.0)).translate((x0, cy, z_split)),
        Manifold.cube((s, ly, s), True).rotate((0.0, 45.0, 0.0)).translate((x1, cy, z_split)),
    ]
    info = {
        "rect": [float(x0), float(y0), float(x1), float(y1)],
        "depth": float(depth),
    }
    return _union(prisms), info
