"""Parameter and result types for the mold-generation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import trimesh


@dataclass
class MoldParams:
    """Settings for a mold-generation run.

    mode:
        "compression" — two-part clamshell mold (forged carbon): the block is
        split at the parting plane and both halves are returned.
        "open_layup" — single-sided female tool: only the half on `keep_side`
        of the parting plane is returned; its flat parting face is the
        vacuum-bagging flange.
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

    pins / pin_*:
        Registration pins, compression mode only, on by default. Pegs on the
        bottom half at the four parting-face corners (45° top chamfer),
        matching oversized holes in the top half. pin_clearance_mm is radial:
        0.15 suits resin-printed molds, use 0.25 for FDM.
    bolts / bolt_*:
        Vertical clamping-bolt through-holes, compression mode only, off by
        default. bolt_count (1-4) holes midway along the parting-face edges;
        hole diameter is bolt_diameter_mm plus a sliding-fit clearance.
    trim_groove / trim_groove_*:
        Open-layup mode only, off by default. A shallow V-groove rectangle
        embossed on the flange face, trim_groove_offset_mm out from the part
        bbox outline, as a trimming guide.
    """

    mode: str
    parting_z_mm: float
    wall_margin_mm: float = 15.0
    cavity_offset_mm: float = 0.0
    keep_side: str = "below"
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
