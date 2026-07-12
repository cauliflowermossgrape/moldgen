"""Parameter and result types for the mold-generation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import trimesh


@dataclass
class MoldParams:
    """Settings for a mold-generation run.

    mode:
        "compression" — forged-carbon telescoping mold: a female half whose
        pocket walls rise `plunge_mm` above the parting plane, and a male
        half whose plug pistons down into them. The wall gap
        (`plug_clearance_mm`) is the pinch-off: excess resin escapes up it
        while fiber is retained, and the plug can always fully close instead
        of jamming on material caught at a flat seam. Returned halves are
        keyed "female" and "male".
        "open_layup" — single-sided female tool: only the half on `keep_side`
        of the parting plane is returned; its flat parting face is the
        vacuum-bagging flange. Returned half is keyed "tool".
    parting_z_mm:
        Height of the flat parting plane, in the part's own coordinate frame.
    wall_margin_mm:
        Extra mold material around the part bounding box, on all six sides.
    cavity_offset_mm:
        Uniform offset applied to the part surface before subtraction
        (e.g. small positive values for release clearance). See
        `offset.offset_mesh` for the self-intersection caveats.
    keep_side:
        For open_layup only: which side of the parting plane to keep,
        "above" or "below".

    Hardware (see hardware.py; features that don't fit are shrunk or skipped
    with a warning, never a hard failure):

    plunge_mm / plug_border_mm / plug_clearance_mm / plug_draft_deg /
    lead_in_mm:
        The telescoping section. The plug face is the part bbox grown by
        plug_border_mm; the female pocket is the plug grown by
        plug_clearance_mm per side (the pinch-off gap: 0.1 machined, 0.15
        resin-printed, 0.25-0.3 FDM). Both walls share plug_draft_deg of
        taper so the gap stays constant. lead_in_mm cuts a chamfer at the
        pocket mouth (that much horizontal, twice vertical).
    pry_slots / pry_slot_*:
        Wedge slots cut across the flange seam (both halves) on two opposite
        outer edges, for driving a wedge or screwdriver in to separate the
        halves after cure.
    overflow_channels / overflow_*:
        Shallow channels on the female flange face running from the pinch
        gap to the outer wall, so squeezed-out resin has an escape path and
        hydraulic lock can't stop the mold closing.
    pins / pin_*:
        Registration pins, compression mode only, on by default. Pegs on the
        female flange at the four corners (45° top chamfer), matching
        oversized holes in the male plate. pin_clearance_mm is radial: 0.15
        suits resin-printed molds, use 0.25 for FDM.
    bolts / bolt_*:
        Vertical clamping-bolt through-holes, compression mode only, off by
        default. bolt_count (1-12) holes distributed around the flange band;
        hole diameter is bolt_diameter_mm plus a sliding-fit clearance. Keep
        spacing within 50-75 mm on printed tools (Easy Composites guidance).
    trim_groove / trim_groove_*:
        Open-layup mode only, off by default. A shallow V-groove rectangle
        embossed on the flange face, trim_groove_offset_mm out from the part
        bbox outline, as a trimming guide.
    """

    mode: str
    parting_z_mm: float
    wall_margin_mm: float = 22.0
    cavity_offset_mm: float = 0.0
    keep_side: str = "below"
    # telescoping plug/pocket — compression mode only. Defaults follow Easy
    # Composites' forged carbon mould design guidance: piston depth ~6 mm for
    # small tools up to 15-20 mm for large (2-3x part depth of travel to load
    # the fiber charge), ~0.1 mm wall gap on machined tools as the resin
    # escape path, 1 mm x 2 mm lead-in chamfer at the pocket mouth.
    plunge_mm: float = 10.0
    plug_border_mm: float = 8.0
    plug_clearance_mm: float = 0.15
    plug_draft_deg: float = 1.0
    lead_in_mm: float = 1.0
    # pry (wedge) slots across the flange seam — compression mode only
    pry_slots: bool = True
    pry_slot_width_mm: float = 20.0
    pry_slot_depth_mm: float = 3.0
    # resin overflow channels on the female flange — compression mode only
    overflow_channels: bool = True
    overflow_per_edge: int = 2
    overflow_width_mm: float = 2.0
    overflow_depth_mm: float = 0.5
    # registration pins — compression mode only
    pins: bool = True
    pin_radius_mm: float = 4.0
    pin_height_mm: float = 8.0
    pin_chamfer_mm: float = 0.5
    pin_clearance_mm: float = 0.15
    # clamping bolt through-holes — compression mode only
    bolts: bool = False
    bolt_count: int = 4
    bolt_diameter_mm: float = 6.0
    # flange trim groove — open-layup mode only
    trim_groove: bool = False
    trim_groove_offset_mm: float = 10.0
    trim_groove_depth_mm: float = 0.3


@dataclass
class MoldResult:
    """Outcome of generate_mold.

    On success (ok=True) `halves` holds trimesh objects keyed "top"/"bottom"
    (compression) or "tool" (open_layup), and `metadata` holds volumes,
    bounds and triangle counts. On failure (ok=False) `errors` lists what is
    wrong with the input; `halves` is empty. `warnings` may be populated in
    either case.
    """

    ok: bool
    halves: dict[str, trimesh.Trimesh] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
