"""Optional STEP/IGES input (OpenCascade via cadquery).

Heavy optional dependency — OCP wheels are hundreds of MB — so it ships as a
pyproject extra: `pip install "moldgen[step]"`. Availability is detected at
import time via HAS_STEP; callers must degrade gracefully when it is False.

BRep surfaces are tessellated with user-configurable deflections: linear
(max chord sagitta, mm) and angular (max normal deviation, degrees). Finer
values follow curved surfaces more closely at the cost of triangle count.
The resulting mesh feeds the normal validate → boolean pipeline.
"""

from __future__ import annotations

import math
from pathlib import Path

import trimesh

try:
    import cadquery as _cq
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing as _Sewing
    from OCP.IFSelect import IFSelect_RetDone as _RetDone
    from OCP.IGESControl import IGESControl_Reader as _IGESReader

    HAS_STEP = True
except ImportError:
    HAS_STEP = False

STEP_SUFFIXES = {".step", ".stp"}
IGES_SUFFIXES = {".iges", ".igs"}
CAD_SUFFIXES = STEP_SUFFIXES | IGES_SUFFIXES

DEFAULT_LINEAR_DEFLECTION_MM = 0.05
DEFAULT_ANGULAR_DEFLECTION_DEG = 0.3

INSTALL_HINT = 'STEP/IGES support is not installed — pip install "moldgen[step]"'


def load_cad(
    path: str | Path,
    linear_deflection_mm: float = DEFAULT_LINEAR_DEFLECTION_MM,
    angular_deflection_deg: float = DEFAULT_ANGULAR_DEFLECTION_DEG,
) -> trimesh.Trimesh:
    """Read a STEP/IGES file and tessellate it to a trimesh."""
    if not HAS_STEP:
        raise ValueError(INSTALL_HINT)
    path = Path(path)
    if path.suffix.lower() in IGES_SUFFIXES:
        shape = _read_iges(path)
    else:
        shape = _read_step(path)
    vertices, triangles = shape.tessellate(
        float(linear_deflection_mm), math.radians(float(angular_deflection_deg))
    )
    if not triangles:
        raise ValueError(f"{path.name} contains no surface geometry")
    # tessellate() emits vertices per BRep face; identical seam coordinates
    # merge in process=True, welding the shell closed
    return trimesh.Trimesh(
        vertices=[(v.x, v.y, v.z) for v in vertices],
        faces=triangles,
        process=True,
    )


def _read_step(path: Path):
    shapes = _cq.importers.importStep(str(path)).vals()
    if not shapes:
        raise ValueError(f"{path.name} contains no shapes")
    if len(shapes) == 1:
        return shapes[0]
    return _cq.Compound.makeCompound(shapes)


def _read_iges(path: Path):
    reader = _IGESReader()
    if reader.ReadFile(str(path)) != _RetDone:
        raise ValueError(f"could not read {path.name} as IGES")
    reader.TransferRoots()
    # IGES stores unsewn surfaces: without sewing, adjacent faces tessellate
    # their shared edges independently and the mesh never closes
    sewing = _Sewing(1e-3)
    sewing.Add(reader.OneShape())
    sewing.Perform()
    return _cq.Shape.cast(sewing.SewedShape())
