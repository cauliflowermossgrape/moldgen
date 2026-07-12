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
from fastapi import FastAPI, HTTPException, UploadFile
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
from moldgen.core.io import SUPPORTED_SUFFIXES, load_part

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
WORKDIR = ROOT / "workdir"

MESH_ID_RE = re.compile(r"^[0-9a-f]{12}$")
DOWNLOADABLE = {"part", "top", "bottom", "tool"}

app = FastAPI(title="moldgen", version=__version__)


class AnalyzeRequest(BaseModel):
    mesh_id: str
    parting_z_mm: float


class GenerateRequest(BaseModel):
    mesh_id: str
    parting_z_mm: float
    mode: str = "compression"
    wall_margin_mm: float = 15.0
    cavity_offset_mm: float = 0.0
    keep_side: str = "below"
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
    return {"status": "ok", "version": __version__}


@app.post("/api/upload")
async def upload(file: UploadFile) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type {suffix or '(none)'}; use STL, 3MF or OBJ",
        )
    mesh_id = uuid.uuid4().hex[:12]
    mesh_dir = WORKDIR / mesh_id
    mesh_dir.mkdir(parents=True, exist_ok=True)
    original = mesh_dir / f"original{suffix}"
    original.write_bytes(await file.read())
    try:
        mesh = load_part(original)
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
    return {
        "parting_z_mm": report.parting_z_mm,
        "undercut_faces": np.flatnonzero(report.face_is_undercut).tolist(),
        "undercut_area_mm2": report.undercut_area_mm2,
        "bottom": {
            "undercut_area_mm2": report.bottom.undercut_area_mm2,
            "likely_demoldable": report.bottom.likely_demoldable,
        },
        "top": {
            "undercut_area_mm2": report.top.undercut_area_mm2,
            "likely_demoldable": report.top.likely_demoldable,
        },
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
