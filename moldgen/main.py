"""FastAPI application: JSON API under /api, static frontend at /.

No database. Each uploaded part gets a directory under workdir/ keyed by a
short random id. The upload is converted once to a canonical binary
part.stl; analysis, generation and the frontend preview all read that same
file, so triangle order stays consistent everywhere (undercut face indices
map 1:1 onto the triangles the viewer renders).
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

import numpy as np
import trimesh
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from moldgen import __version__
from moldgen.core import (
    MoldParams,
    analyze_undercuts,
    export_stl,
    generate_mold,
    suggest_parting_plane,
)
from moldgen.core import step_io
from moldgen.core.io import load_part, supported_suffixes

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
WORKDIR = ROOT / "workdir"

MESH_ID_RE = re.compile(r"^[0-9a-f]{12}$")
DOWNLOADABLE = {"part", "female", "male", "tool"}

app = FastAPI(title="moldgen", version=__version__)


class AnalyzeRequest(BaseModel):
    mesh_id: str
    parting_z_mm: float


class GenerateRequest(BaseModel):
    mesh_id: str
    parting_z_mm: float
    mode: str = "compression"
    wall_margin_mm: float = 22.0
    cavity_offset_mm: float = 0.0
    keep_side: str = "below"
    plunge_mm: float = 10.0
    plug_border_mm: float = 8.0
    plug_clearance_mm: float = 0.15
    plug_draft_deg: float = 1.0
    lead_in_mm: float = 1.0
    demold_check: bool = True
    min_draft_deg: float = 1.0
    jack_screws: bool = True
    jack_screw_count: int = 2
    jack_screw_diameter_mm: float = 6.0
    ejector_holes: bool = False
    ejector_diameter_mm: float = 6.0
    pry_slots: bool = True
    pry_slot_width_mm: float = 20.0
    pry_slot_depth_mm: float = 3.0
    overflow_channels: bool = True
    overflow_per_edge: int = 2
    overflow_width_mm: float = 2.0
    overflow_depth_mm: float = 0.5
    pins: bool = True
    pin_clearance_mm: float = 0.15
    bolts: bool = False
    bolt_count: int = 4
    bolt_diameter_mm: float = 6.0
    trim_groove: bool = False
    trim_groove_offset_mm: float = 10.0


def _part_path(mesh_id: str) -> Path:
    if not MESH_ID_RE.fullmatch(mesh_id):
        raise HTTPException(status_code=400, detail="invalid mesh id")
    path = WORKDIR / mesh_id / "part.stl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="unknown mesh id")
    return path


def _load_mesh(mesh_id: str) -> trimesh.Trimesh:
    return trimesh.load(_part_path(mesh_id))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "step_support": step_io.HAS_STEP}


@app.post("/api/upload")
async def upload(
    file: UploadFile,
    linear_deflection_mm: float = Form(step_io.DEFAULT_LINEAR_DEFLECTION_MM),
    angular_deflection_deg: float = Form(step_io.DEFAULT_ANGULAR_DEFLECTION_DEG),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in supported_suffixes():
        if suffix in step_io.CAD_SUFFIXES:
            raise HTTPException(status_code=400, detail=step_io.INSTALL_HINT)
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type {suffix or '(none)'}; "
            f"use {', '.join(sorted(supported_suffixes()))}",
        )
    mesh_id = uuid.uuid4().hex[:12]
    mesh_dir = WORKDIR / mesh_id
    mesh_dir.mkdir(parents=True, exist_ok=True)
    original = mesh_dir / f"original{suffix}"
    original.write_bytes(await file.read())
    try:
        mesh = load_part(
            original,
            linear_deflection_mm=linear_deflection_mm,
            angular_deflection_deg=angular_deflection_deg,
        )
    except Exception as exc:
        shutil.rmtree(mesh_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"could not read {file.filename}: {exc}")
    export_stl(mesh, mesh_dir / "part.stl")
    mesh = trimesh.load(mesh_dir / "part.stl")  # canonical face order from here on

    warnings = []
    if float(mesh.extents.max()) < 1.0:
        warnings.append(
            f"largest dimension is {mesh.extents.max():.4g} mm — if the model "
            "was exported in meters, scale it by 1000"
        )
    if not mesh.is_watertight:
        warnings.append("mesh is not watertight; repair will be attempted during generation")

    return {
        "mesh_id": mesh_id,
        "filename": file.filename,
        "triangles": int(len(mesh.faces)),
        "bounds": mesh.bounds.tolist(),
        "extents": mesh.extents.tolist(),
        "watertight": bool(mesh.is_watertight),
        "suggested_parting_z_mm": suggest_parting_plane(mesh),
        "warnings": warnings,
        "mesh_url": f"/api/download/{mesh_id}/part.stl",
    }


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    mesh = _load_mesh(req.mesh_id)
    report = analyze_undercuts(mesh, req.parting_z_mm)

    def half(h):
        return {
            "undercut_area_mm2": h.undercut_area_mm2,
            "low_draft_area_mm2": h.low_draft_area_mm2,
            "likely_demoldable": h.likely_demoldable,
        }

    return {
        "parting_z_mm": report.parting_z_mm,
        "min_draft_deg": report.min_draft_deg,
        "undercut_faces": np.flatnonzero(report.face_is_undercut).tolist(),
        "low_draft_faces": np.flatnonzero(report.face_is_low_draft).tolist(),
        "undercut_area_mm2": report.undercut_area_mm2,
        "bottom": half(report.bottom),
        "top": half(report.top),
    }


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:
    mesh = _load_mesh(req.mesh_id)
    params = MoldParams(**req.model_dump(exclude={"mesh_id"}))
    result = generate_mold(mesh, params)
    if not result.ok:
        return {"ok": False, "errors": result.errors, "warnings": result.warnings}

    mesh_dir = WORKDIR / req.mesh_id
    halves = {}
    for name, half in result.halves.items():
        export_stl(half, mesh_dir / f"{name}.stl")
        halves[name] = {
            "url": f"/api/download/{req.mesh_id}/{name}.stl",
            **result.metadata["halves"][name],
        }
    return {
        "ok": True,
        "warnings": result.warnings,
        "metadata": result.metadata,
        "halves": halves,
    }


@app.get("/api/download/{mesh_id}/{name}.stl")
def download(mesh_id: str, name: str) -> FileResponse:
    _part_path(mesh_id)  # validates the id and that the upload exists
    if name not in DOWNLOADABLE:
        raise HTTPException(status_code=404, detail="unknown file")
    path = WORKDIR / mesh_id / f"{name}.stl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="not generated yet")
    return FileResponse(path, media_type="model/stl", filename=f"moldgen-{name}.stl")


WORKDIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
