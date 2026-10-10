"""Mold hardware: registration pins, clamping bolts, pry slots, resin
overflow channels, flange trim groove.

Every feature is a manifold solid applied with a boolean op, so the mold
halves stay watertight. Pin/bolt/slot positions live on the flange band —
the flat contact ring between the pocket wall and the outer wall — on its
midline. Features that don't fit are shrunk or skipped with a warning,
never a hard failure.

Sizing guidance follows Easy Composites' forged carbon mould design notes:
bolt spacing within 50-75 mm on printed tools, wedge features at the seam
for separating the halves.
"""

from __future__ import annotations

import math

import numpy as np
from manifold3d import Manifold

CIRCULAR_SEGMENTS = 64
MIN_PIN_RADIUS = 1.5  # below this a pin is structurally useless; skip instead
MIN_PIN_HEIGHT = 2.0
RING_CLEARANCE = 2.0  # min material between a feature wall and pocket/outer wall
PEG_EMBED = 1.0  # pegs extend this deep into the female flange body
HOLE_OVERDEPTH = 0.5  # holes are this much deeper than the peg is tall
HOLE_CAP = 1.5  # solid material kept above the hole ceiling
BOLT_RADIAL_CLEARANCE = 0.25  # sliding fit for the named bolt size
BOLT_MAX_SPACING = 75.0  # Easy Composites: 50-75 mm max on printed tools
PRY_REACH = 6.0  # how far a pry slot cuts into the flange band


def _union(solids: list[Manifold]) -> Manifold:
    acc = solids[0]
    for s in solids[1:]:
        acc = acc + s
    return acc


def _ring_rect(block_min, block_max, inset):
    return (
        float(block_min[0]) + inset,
        float(block_min[1]) + inset,
        float(block_max[0]) - inset,
        float(block_max[1]) - inset,
    )


def registration_pins(block_min, block_max, z_plane, inset, params, warnings):
    """Alignment pegs (female flange, at z_plane) and matching holes (male).

    `inset` is the band midline distance in from the outer wall; pegs sit at
    the four corners of that ring. Returns (pegs, holes, info), or
    (None, None, None) when pins cannot fit. Pegs get a 45° top chamfer for
    easy engagement; holes are oversized by the radial clearance and cut
    HOLE_OVERDEPTH deeper than the peg so it never bottoms out.
    """
    r = params.pin_radius_mm
    r_fit = inset - RING_CLEARANCE
    if r > r_fit:
        if r_fit < MIN_PIN_RADIUS:
            warnings.append(
                f"flange band leaves no room for registration pins "
                f"(largest fitting radius {max(r_fit, 0.0):g} mm); pins skipped"
            )
            return None, None, None
        warnings.append(f"pin radius shrunk from {r:g} to {r_fit:g} mm to fit the flange band")
        r = r_fit

    h = params.pin_height_mm
    h_fit = float(block_max[2]) - z_plane - HOLE_OVERDEPTH - HOLE_CAP
    if h > h_fit:
        if h_fit < MIN_PIN_HEIGHT:
            warnings.append("male half is too thin for registration pins; pins skipped")
            return None, None, None
        warnings.append(f"pin height shrunk from {h:g} to {h_fit:g} mm to fit the male plate")
        h = h_fit

    c = min(params.pin_chamfer_mm, 0.4 * r, 0.4 * h)
    clearance = params.pin_clearance_mm
    x0, y0, x1, y1 = _ring_rect(block_min, block_max, inset)
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    pegs, holes = [], []
    for x, y in corners:
        shaft = Manifold.cylinder(PEG_EMBED + h - c, r, r, CIRCULAR_SEGMENTS).translate(
            (x, y, z_plane - PEG_EMBED)
        )
        if c > 0:
            chamfer = Manifold.cylinder(c, r, r - c, CIRCULAR_SEGMENTS).translate(
                (x, y, z_plane + h - c)
            )
            pegs.append(shaft + chamfer)
        else:
            pegs.append(shaft)
        holes.append(
            Manifold.cylinder(
                PEG_EMBED + h + HOLE_OVERDEPTH,
                r + clearance,
                r + clearance,
                CIRCULAR_SEGMENTS,
            ).translate((x, y, z_plane - PEG_EMBED))
        )

    info = {
        "centers": [[float(x), float(y)] for x, y in corners],
        "radius": float(r),
        "height": float(h),
        "chamfer": float(c),
        "clearance": float(clearance),
    }
    return _union(pegs), _union(holes), info


def bolt_holes(block_min, block_max, inset, params, pin_info, warnings):
    """Vertical clamping-bolt through-holes distributed around the band.

    bolt_count (1-12) is spread round-robin over the four edges, evenly
    spaced along each. Returns (cylinders, info) to subtract from both
    halves, or (None, None) if no hole fits. Warns when the largest gap
    between neighbouring bolts exceeds 75 mm (printed tools distort).
    """
    r = params.bolt_diameter_mm / 2.0 + BOLT_RADIAL_CLEARANCE
    if r > inset - RING_CLEARANCE:
        warnings.append(
            f"flange band too narrow for {params.bolt_diameter_mm:g} mm bolt holes; bolts skipped"
        )
        return None, None

    x0, y0, x1, y1 = _ring_rect(block_min, block_max, inset)
    edges = [
        ((x0, y0), (x0, y1)),  # left
        ((x1, y0), (x1, y1)),  # right
        ((x0, y0), (x1, y0)),  # front
        ((x0, y1), (x1, y1)),  # back
    ]
    n = params.bolt_count
    per_edge = [n // 4 + (1 if i < n % 4 else 0) for i in range(4)]

    kept = []
    max_gap = 0.0
    for (a, b), k in zip(edges, per_edge):
        if k == 0:
            continue
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        max_gap = max(max_gap, length / (k + 1))
        for i in range(k):
            t = (i + 1) / (k + 1)
            x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            if pin_info is not None:
                min_gap = r + pin_info["radius"] + pin_info["clearance"] + 1.0
                if any(math.hypot(x - px, y - py) < min_gap for px, py in pin_info["centers"]):
                    warnings.append(
                        f"bolt hole at ({x:.1f}, {y:.1f}) too close to a "
                        "registration pin; that hole skipped"
                    )
                    continue
            kept.append((x, y))
    if not kept:
        return None, None
    if max_gap > BOLT_MAX_SPACING:
        warnings.append(
            f"largest bolt spacing is {max_gap:.0f} mm; Easy Composites "
            "recommend 50-75 mm on printed tools — consider more bolts"
        )

    z0 = float(block_min[2]) - 1.0
    height = float(block_max[2] - block_min[2]) + 2.0
    cylinders = [
        Manifold.cylinder(height, r, r, CIRCULAR_SEGMENTS).translate((x, y, z0)) for x, y in kept
    ]
    info = {
        "centers": [[float(x), float(y)] for x, y in kept],
        "radius": float(r),
        "bolt_diameter": float(params.bolt_diameter_mm),
    }
    return _union(cylinders), info


def jack_screw_holes(block_min, block_max, inset, params, avoid, warnings):
    """Jacking-screw holes through the male plate, over the female flange.

    Thread a bolt in and its tip bears on the female flange face, jacking
    the halves apart evenly — the screw counterpart to pry slots, standard
    mould-making practice for stubborn tools. The hole is tap-sized (bolt
    diameter − 1 mm) so the bolt cuts its own thread in a printed plate;
    drill and tap to suit on machined tools. Subtract from the MALE only —
    the female face under each hole must stay solid to push against.
    """
    r = (params.jack_screw_diameter_mm - 1.0) / 2.0
    if r <= 0 or r > inset - RING_CLEARANCE:
        warnings.append("flange band too narrow for jack screws; skipped")
        return None, None

    x0, y0, x1, y1 = _ring_rect(block_min, block_max, inset)
    # alternate left/right edges, staggered so opposing screws sit diagonal
    layout = [(0, 1 / 3), (1, 2 / 3), (0, 2 / 3), (1, 1 / 3)]
    scan = (0.0, 0.08, -0.08, 0.16, -0.16, 0.24, -0.24)

    kept = []
    for edge, t_base in layout[: params.jack_screw_count]:
        x = x0 if edge == 0 else x1
        placed = None
        for dt in scan:
            t = t_base + dt
            if not 0.1 <= t <= 0.9:
                continue
            y = y0 + t * (y1 - y0)
            if all(math.hypot(x - ax, y - ay) > r + ar + 1.5 for ax, ay, ar in avoid):
                placed = (x, y)
                break
        if placed is None:
            warnings.append("no clear spot for a jack screw; one skipped")
            continue
        kept.append(placed)
    if not kept:
        return None, None

    z0 = float(block_min[2])  # generous span; only the male plate intersects it
    height = float(block_max[2] - block_min[2]) + 2.0
    holes = [
        Manifold.cylinder(height, r, r, CIRCULAR_SEGMENTS).translate((x, y, z0)) for x, y in kept
    ]
    info = {
        "centers": [[float(x), float(y)] for x, y in kept],
        "radius": float(r),
        "bolt_diameter": float(params.jack_screw_diameter_mm),
    }
    return _union(holes), info


EJECTOR_OVERSHOOT = 0.3  # how far the hole pokes past the cavity surface
EJECTOR_MIN_WALL = 1.0  # part material required above the pin face


def ejector_hole(block_min, part_mesh, z_split, params, warnings, preferred_xy=None):
    """Ejector-pin hole through the female base into the cavity floor.

    Standard compression-molding practice: a pin or dowel pushed through it
    ejects a stuck part. The hole must land squarely on the part: every
    point of the pin's disc has to hit part surface from below, below the
    parting plane (female-molded), with at least EJECTOR_MIN_WALL of part
    material above the pin face. The search starts at `preferred_xy` (the
    volume centroid — the deepest, stickiest region) and falls back to the
    nearest flat-bottomed spots, so ring- or C-shaped parts whose centroid
    is empty space never get a hole straight into the pocket floor. The
    hole opens into the cavity, so it must be plugged (wax/tape) before
    layup and the part carries a small witness mark. Subtract from the
    FEMALE only. Returns (hole, info), or (None, None) when no spot fits.
    """
    from .rays import VerticalRayCaster

    r = params.ejector_diameter_mm / 2.0
    caster = VerticalRayCaster.from_mesh(part_mesh)
    z_floor = float(part_mesh.bounds[0][2]) - 1.0
    ring = np.array(
        [[0.0, 0.0]]
        + [
            [math.cos(a) * r, math.sin(a) * r]
            for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)
        ]
    )

    def landing(xy):
        """(top of the hole, ok) for a pin centered at xy."""
        pts = np.column_stack([xy[0] + ring[:, 0], xy[1] + ring[:, 1], np.full(len(ring), z_floor)])
        t_in = caster.first_hit(pts, 1.0)
        if not np.all(np.isfinite(t_in)):
            return None
        z_in = z_floor + t_in
        if np.any(z_in >= z_split):
            return None
        top = float(z_in.max()) + EJECTOR_OVERSHOOT
        inside = pts.copy()
        inside[:, 2] = z_in + 1e-6
        t_out = caster.first_hit(inside, 1.0)
        if np.any(z_in + t_out < top + EJECTOR_MIN_WALL):
            return None
        return top

    center = np.asarray(
        preferred_xy if preferred_xy is not None else part_mesh.bounds.mean(axis=0)[:2],
        dtype=float,
    )
    candidates = [center]
    # fallbacks: centroids of downward-facing faces, nearest first
    down = part_mesh.face_normals[:, 2] < -0.5
    if down.any():
        cxy = part_mesh.triangles_center[down][:, :2]
        order = np.argsort(np.linalg.norm(cxy - center, axis=1))
        candidates += list(cxy[order[:400]])

    for xy in candidates:
        top = landing(xy)
        if top is not None:
            x, y = float(xy[0]), float(xy[1])
            z0 = float(block_min[2]) - 1.0
            hole = Manifold.cylinder(top - z0, r, r, CIRCULAR_SEGMENTS).translate((x, y, z0))
            if np.linalg.norm(np.asarray(xy) - center) > 1e-6:
                warnings.append(
                    f"ejector hole moved to ({x:.1f}, {y:.1f}) — the part has "
                    "no flat underside at its centroid"
                )
            warnings.append(
                "ejector hole opens into the cavity — plug it with wax or tape before "
                "layup; the part will carry a small witness mark"
            )
            return hole, {"center": [x, y], "radius": float(r), "top_z": top}
    warnings.append(
        f"no spot on the part's underside fits a {2 * r:g} mm ejector pin; ejector hole skipped"
    )
    return None, None


def pry_slot_solids(block_min, block_max, z_flange, params, avoid, warnings):
    """Wedge slots across the flange seam, subtracted from BOTH halves.

    Two slots on opposite outer edges — the ±Y edges by default, falling
    back to the ±X edges when pins/bolts crowd a Y edge. Each is a
    rectangular notch reaching PRY_REACH into the band, half
    pry_slot_depth_mm into each half, so a wedge or flat screwdriver can
    break the cured seam open (Easy Composites use a 3 mm chamfer for the
    same job). `avoid` is a list of (x, y, radius) keep-outs; a conflicting
    slot slides along its edge and narrows before giving up.
    """
    w0 = params.pry_slot_width_mm
    d = params.pry_slot_depth_mm
    lo = (float(block_min[0]), float(block_min[1]))
    hi = (float(block_max[0]), float(block_max[1]))
    # scan outward from the edge middle
    t_candidates = (0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.18, 0.82)

    def find_spot(axis, wall, direction, width):
        """Slot along `axis` ("x": the slot sits on a Y wall and slides in
        x) at `wall`, cutting inward in `direction`."""
        a = 0 if axis == "x" else 1
        n_lo = min(wall, wall + direction * PRY_REACH)
        n_hi = max(wall, wall + direction * PRY_REACH)
        for t in t_candidates:
            c = lo[a] + t * (hi[a] - lo[a])
            if c - width / 2.0 < lo[a] + 2.0 or c + width / 2.0 > hi[a] - 2.0:
                continue
            ok = True
            for ax, ay, ar in avoid:
                along, across = (ax, ay) if axis == "x" else (ay, ax)
                if (
                    abs(c - along) < width / 2.0 + ar + 1.5
                    and n_lo - ar - 1.5 <= across <= n_hi + ar + 1.5
                ):
                    ok = False
                    break
            if ok:
                return c
        return None

    def place(axis, wall, direction):
        for width in (w0, 0.75 * w0, 0.6 * w0, 0.5 * w0):
            if width < 8.0:
                break
            c = find_spot(axis, wall, direction, width)
            if c is not None:
                return c, width
        return None

    # (preferred edge, fallback edge) for each of the two slots
    edges = (
        (("x", lo[1], 1.0), ("y", lo[0], 1.0)),
        (("x", hi[1], -1.0), ("y", hi[0], -1.0)),
    )
    slots, centers, widths, keepouts = [], [], [], []
    for preferred, fallback in edges:
        placed = None
        for axis, wall, direction in (preferred, fallback):
            spot = place(axis, wall, direction)
            if spot is not None:
                placed = (axis, wall, direction, *spot)
                break
        if placed is None:
            warnings.append("no clear spot for a pry slot on either edge; one skipped")
            continue
        axis, wall, direction, c, width = placed
        if width < w0:
            warnings.append(f"pry slot narrowed to {width:g} mm to clear pins/bolts")
        start = wall - 1.0 if direction > 0 else wall - PRY_REACH
        mid = wall + direction * PRY_REACH / 2.0
        if axis == "x":
            solid = Manifold.cube((width, PRY_REACH + 1.0, 2.0 * d)).translate(
                (c - width / 2.0, start, z_flange - d)
            )
            centers.append([float(c), float(wall)])
            keepouts.append((float(c), float(mid), max(width, PRY_REACH) / 2.0))
        else:
            solid = Manifold.cube((PRY_REACH + 1.0, width, 2.0 * d)).translate(
                (start, c - width / 2.0, z_flange - d)
            )
            centers.append([float(wall), float(c)])
            keepouts.append((float(mid), float(c), max(width, PRY_REACH) / 2.0))
        slots.append(solid)
        widths.append(float(width))

    if not slots:
        return None, None
    info = {
        "centers": centers,
        "width": float(w0),
        "widths": widths,
        "depth": float(d),
        "reach": PRY_REACH,
        "keepouts": [list(k) for k in keepouts],
    }
    return _union(slots), info


def _segment_clear(p0, p1, avoid, pad) -> bool:
    """True when the segment p0-p1 stays more than r + pad from every
    (x, y, r) keep-out circle."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    seg2 = dx * dx + dy * dy
    for ax, ay, ar in avoid:
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((ax - x0) * dx + (ay - y0) * dy) / seg2))
        if math.hypot(x0 + t * dx - ax, y0 + t * dy - ay) <= ar + pad:
            return False
    return True


# fractional slides tried, in order, when a channel's ideal spot is blocked
_SLIDES = (0.0, 0.04, -0.04, 0.08, -0.08, 0.12, -0.12, 0.16, -0.16, 0.22, -0.22)


def overflow_channel_solids(block_min, block_max, z_flange, pocket_rect, params, avoid, warnings):
    """Resin overflow channels on the female flange face.

    Shallow grooves running straight out from the pocket wall (where the
    pinch-off gap delivers squeezed-out resin) to the outer wall, so excess
    resin escapes instead of hydraulically locking the mold.
    overflow_per_edge channels per edge, evenly spaced along the pocket
    wall; a channel whose ideal spot crosses a pin, bolt, jack screw or pry
    slot slides along the edge to the nearest clear spot, and is only
    skipped (with a warning) when none exists. Subtract the result from the
    FEMALE half only.
    """
    w = params.overflow_width_mm
    d = params.overflow_depth_mm
    k = params.overflow_per_edge
    qx0, qy0, qx1, qy1 = (float(v) for v in pocket_rect)
    bx0, by0 = float(block_min[0]), float(block_min[1])
    bx1, by1 = float(block_max[0]), float(block_max[1])
    pad = w / 2.0 + 1.0
    min_sep = 3.0 * w  # keep neighbouring channels on one edge distinct

    # (edge length axis, fixed inner coord, outer wall coord, along-range)
    edges = (
        ("x", qy0, by0, (qx0, qx1)),  # front: channel at x, crossing the band in y
        ("x", qy1, by1, (qx0, qx1)),  # back
        ("y", qx0, bx0, (qy0, qy1)),  # left: channel at y, crossing the band in x
        ("y", qx1, bx1, (qy0, qy1)),  # right
    )
    channels = []
    skipped = moved = 0
    for axis, inner, outer, (a0, a1) in edges:
        placed: list[float] = []
        for i in range(k):
            t0 = (i + 1) / (k + 1)
            spot = None
            for dt in _SLIDES:
                t = t0 + dt
                if not 0.08 <= t <= 0.92:
                    continue
                c = a0 + t * (a1 - a0)
                if any(abs(c - o) < min_sep for o in placed):
                    continue
                p0, p1 = ((c, inner), (c, outer)) if axis == "x" else ((inner, c), (outer, c))
                if _segment_clear(p0, p1, avoid, pad):
                    spot = c
                    moved += dt != 0.0
                    break
            if spot is None:
                skipped += 1
                continue
            placed.append(spot)
            lo, hi = min(inner, outer), max(inner, outer)
            if axis == "x":
                solid = Manifold.cube((w, hi - lo + 2.0, d + 1.0)).translate(
                    (spot - w / 2.0, lo - 1.0, z_flange - d)
                )
            else:
                solid = Manifold.cube((hi - lo + 2.0, w, d + 1.0)).translate(
                    (lo - 1.0, spot - w / 2.0, z_flange - d)
                )
            channels.append(solid)

    if skipped:
        warnings.append(
            f"{skipped} overflow channel(s) skipped — no clear path past "
            "pins/bolts/jack screws/pry slots on that edge"
        )
    if not channels:
        return None, None
    info = {
        "count": len(channels),
        "width": float(w),
        "depth": float(d),
        "per_edge": int(k),
        "slid": int(moved),
    }
    return _union(channels), info


def _v_groove_rect(x0, y0, x1, y1, z, depth):
    """Rectangular V-groove (scribe) loop cut into a flat face at height z.

    Built from four square prisms rotated 45° about their long axis: the
    diamond cross-section reads as a `depth`-deep, 90°-included V where it
    crosses the face.
    """
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    s = depth * math.sqrt(2.0)  # square side whose half-diagonal equals depth
    lx = (x1 - x0) + 2.0 * depth  # run past the corners so the loop closes
    ly = (y1 - y0) + 2.0 * depth
    return _union(
        [
            Manifold.cube((lx, s, s), True).rotate((45.0, 0.0, 0.0)).translate((cx, y0, z)),
            Manifold.cube((lx, s, s), True).rotate((45.0, 0.0, 0.0)).translate((cx, y1, z)),
            Manifold.cube((s, ly, s), True).rotate((0.0, 45.0, 0.0)).translate((x0, cy, z)),
            Manifold.cube((s, ly, s), True).rotate((0.0, 45.0, 0.0)).translate((x1, cy, z)),
        ]
    )


def trim_groove_solid(part_bounds, block_min, block_max, z_split, params, warnings):
    """V-groove rectangle embossed on the open-layup flange as a trim guide."""
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
    info = {
        "rect": [float(x0), float(y0), float(x1), float(y1)],
        "depth": float(depth),
    }
    return _v_groove_rect(x0, y0, x1, y1, z_split, depth), info


TAPE_LINE_DEPTH = 0.2  # shallow enough not to compromise the vacuum seal


def tape_line_solid(block_min, block_max, part_bounds, z_face, params, warnings):
    """Scribe line marking the inner edge of the sealant-tape zone.

    Vacuum bagging off the flange needs a clean band of tape at the flange
    edge; the scribe tells you where the tape zone starts so layup materials
    stay inside it. A closed loop entirely inside the bag area — it can't
    become a leak path.
    """
    tz = params.tape_zone_mm
    x0 = float(block_min[0]) + tz
    y0 = float(block_min[1]) + tz
    x1 = float(block_max[0]) - tz
    y1 = float(block_max[1]) - tz
    (px0, py0, _), (px1, py1, _) = part_bounds
    if x0 > px0 - RING_CLEARANCE or y0 > py0 - RING_CLEARANCE:
        warnings.append(
            f"flange too narrow for a {tz:g} mm tape zone scribe; skipped (increase wall_margin_mm)"
        )
        return None, None
    info = {
        "rect": [x0, y0, x1, y1],
        "zone_mm": float(tz),
        "depth": TAPE_LINE_DEPTH,
    }
    return _v_groove_rect(x0, y0, x1, y1, z_face, TAPE_LINE_DEPTH), info


# Easy Composites split-mould bush layout: 20-25 mm from the mould surface
# edge, 75-100 mm apart (M6 bush system uses 6.1 mm holes)
FLANGE_HOLE_EDGE_DISTANCE = 22.5
FLANGE_HOLE_MAX_SPACING = 100.0


def flange_hole_solids(block_min, block_max, wall_margin, params, warnings):
    """Split-mould bush/dowel holes through the laminating-tool flange.

    Count per edge comes from flange_hole_spacing_mm (EC: 75-100 mm);
    positions sit FLANGE_HOLE_EDGE_DISTANCE in from the cavity edge. Used to
    bolt a matching tool half, backing frame, or dowel a split mould.
    """
    r = params.flange_hole_diameter_mm / 2.0
    inset = wall_margin - FLANGE_HOLE_EDGE_DISTANCE  # from the outer wall
    lo, hi = RING_CLEARANCE + r, wall_margin - RING_CLEARANCE - r
    if lo > hi:
        warnings.append("flange too narrow for bush holes; skipped")
        return None, None
    clamped = min(max(inset, lo), hi)
    if clamped != inset:
        warnings.append(
            f"bush holes moved to {wall_margin - clamped:.0f} mm from the cavity "
            "edge to stay on the flange (EC recommend 20-25 mm)"
        )
    x0, y0, x1, y1 = _ring_rect(block_min, block_max, clamped)
    edges = [
        ((x0, y0), (x0, y1)),
        ((x1, y0), (x1, y1)),
        ((x0, y0), (x1, y0)),
        ((x0, y1), (x1, y1)),
    ]
    positions = []
    max_gap = 0.0
    for a, b in edges:
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, round(length / params.flange_hole_spacing_mm))
        max_gap = max(max_gap, length / (k + 1))
        for i in range(k):
            t = (i + 1) / (k + 1)
            positions.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    if max_gap > FLANGE_HOLE_MAX_SPACING:
        warnings.append(
            f"largest bush-hole spacing is {max_gap:.0f} mm; Easy Composites "
            "recommend 75-100 mm — reduce flange_hole_spacing_mm"
        )

    z0 = float(block_min[2]) - 1.0
    height = float(block_max[2] - block_min[2]) + 2.0
    holes = [
        Manifold.cylinder(height, r, r, CIRCULAR_SEGMENTS).translate((x, y, z0))
        for x, y in positions
    ]
    info = {
        "centers": [[float(x), float(y)] for x, y in positions],
        "radius": float(r),
        "diameter": float(params.flange_hole_diameter_mm),
    }
    return _union(holes), info
