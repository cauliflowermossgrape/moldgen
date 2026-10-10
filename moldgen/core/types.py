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
        "prepreg" — laminating tool for prepreg/wet-lay vacuum work: like
        open_layup, but the cavity opening continues `trim_allowance_mm`
        past the part edge with a `trim_kick_deg` outward kick (Easy
        Composites: 5-10 mm with a 5° kick — the laminate runs out past the
        finished edge and the kick both aids release and leaves a witness
        line to trim to), the flange face sits at the top of that run-out,
        and the flange can carry a sealant-tape scribe line and split-mould
        bush holes. Returned half is keyed "tool".
    parting_z_mm:
        Height of the flat parting plane, in the part's own coordinate frame.
    wall_margin_mm:
        Extra mold material around the part bounding box, on all six sides.
    cavity_offset_mm:
        Uniform offset applied to the part surface before subtraction
        (e.g. small positive values for release clearance). Flat faces move
        by exactly this much, sharp edges and corners included. See
        `offset.offset_mesh` for the self-intersection caveats.
    shrink_comp_pct:
        Uniform scale-up of the cavity in percent (±5), about the part's
        bbox center at the parting height, to compensate for shrinkage of
        the cast/cured part or of a printed tool. Applied before the offset.
    keep_side:
        For open_layup only: which side of the parting plane to keep,
        "above" or "below".

    Hardware (see hardware.py; features that don't fit are shrunk or skipped
    with a warning, never a hard failure):

    plunge_mm / plug_shape / plug_border_mm / plug_clearance_mm /
    plug_draft_deg / lead_in_mm:
        The telescoping section. The plug face is the part's outline
        (plug_shape="contour") or its bounding rectangle ("rect") grown by
        plug_border_mm; the female pocket is the plug grown by
        plug_clearance_mm per side (the pinch-off gap: 0.1 machined, 0.15
        resin-printed, 0.25-0.3 FDM). Both walls share plug_draft_deg of
        taper so the gap stays constant. lead_in_mm cuts a chamfer at the
        pocket mouth (that much horizontal, twice vertical).
    demold_check / min_draft_deg:
        When on, generation analyzes the part against each half's pull
        direction and warns about undercuts (part mechanically locks) and
        low-draft walls (within min_draft_deg of vertical — they release but
        drag/score; common practice is 1-2° minimum draft).
    jack_screws / jack_screw_*:
        Tap-sized holes through the male plate over the female flange
        (compression only, on by default). Threading a bolt in bears on the
        female face and jacks the halves apart evenly — the screw
        counterpart to the pry slots, standard mould-making practice. Hole
        is bolt diameter − 1 mm so the bolt cuts its own thread in a
        printed plate.
    ejector_holes / ejector_diameter_mm:
        Optional ejector-pin hole through the female base into the cavity at
        the part's centroid (compression only, off by default) — push a pin
        or dowel through to eject a stuck part, standard compression-molding
        practice. Plug it with wax/tape before layup; the part carries a
        small witness mark.
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
    # vertical shell: material under the deepest cavity point (female /
    # single-sided tool floor) and above the cavity (male plate). Separate
    # from wall_margin_mm, which is a HORIZONTAL requirement (the flange
    # band must fit pins/bolts) — using it vertically makes thin parts
    # produce needlessly thick molds.
    base_thickness_mm: float = 10.0
    cavity_offset_mm: float = 0.0
    # uniform cavity scale-up (%) compensating shrinkage of the part
    # material and/or a printed tool; about the part center at parting height
    shrink_comp_pct: float = 0.0
    keep_side: str = "below"
    # telescoping plug/pocket — compression mode only. Defaults follow Easy
    # Composites' forged carbon mould design guidance: piston depth ~6 mm for
    # small tools up to 15-20 mm for large (2-3x part depth of travel to load
    # the fiber charge), ~0.1 mm wall gap on machined tools as the resin
    # escape path, 1 mm x 2 mm lead-in chamfer at the pocket mouth.
    plunge_mm: float = 10.0
    # "contour": plug/pocket follow the part outline grown by plug_border_mm;
    # "rect": its bounding rectangle
    plug_shape: str = "contour"
    plug_border_mm: float = 8.0
    plug_clearance_mm: float = 0.15
    plug_draft_deg: float = 1.0
    lead_in_mm: float = 1.0
    # demolding analysis & hardware
    demold_check: bool = True
    min_draft_deg: float = 1.0
    jack_screws: bool = True
    jack_screw_count: int = 2
    jack_screw_diameter_mm: float = 6.0
    ejector_holes: bool = False
    ejector_diameter_mm: float = 6.0
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
    # prepreg / laminating tool — mode "prepreg". Values follow Easy
    # Composites' composite mould CAD guidance: 5-10 mm trim allowance with
    # a 5° kick (release + witness line), ~30 mm finished flange, split-mould
    # bushes 20-25 mm from the mould surface edge at 75-100 mm spacing
    # (6.1 mm holes for their M6 bush/dowel system).
    trim_allowance_mm: float = 8.0
    trim_kick_deg: float = 5.0
    tape_line: bool = True
    tape_zone_mm: float = 15.0
    flange_bolt_holes: bool = False
    flange_hole_diameter_mm: float = 6.1
    flange_hole_spacing_mm: float = 90.0


@dataclass
class MoldResult:
    """Outcome of generate_mold.

    On success (ok=True) `halves` holds trimesh objects keyed
    "female"/"male" (compression) or "tool" (open_layup, prepreg), and
    `metadata` holds volumes, bounds, triangle counts, hardware placement
    and the demolding numbers. On failure (ok=False) `errors` lists what is
    wrong with the input; `halves` is empty. `warnings` may be populated in
    either case.
    """

    ok: bool
    halves: dict[str, trimesh.Trimesh] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
