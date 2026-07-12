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
    """

    mode: str
    parting_z_mm: float
    wall_margin_mm: float = 15.0
    cavity_offset_mm: float = 0.0
    keep_side: str = "below"


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
