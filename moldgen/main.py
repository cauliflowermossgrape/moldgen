"""FastAPI application: JSON API under /api, static frontend at /.

No database. Each uploaded part gets a directory under workdir/ keyed by a
short random id. The upload is converted once to a canonical binary
part.stl; analysis, generation and the frontend preview all read that same
file, so triangle order stays consistent everywhere (undercut face indices
map 1:1 onto the triangles the viewer renders).
"""

from __future__ import annotations

import base64
import dataclasses
import io
import json
import logging
import re
import shutil
import threading
import time
import typing
import uuid
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, create_model

from moldgen import __version__
from moldgen.core import (
    MoldParams,
    analyze_undercuts,
    export_stl,
    generate_mold,
    step_io,
)
from moldgen.core.io import load_part, supported_suffixes
from moldgen.core.report import setup_sheet
from moldgen.core.undercuts import PartVisibility, parting_summary

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
WORKDIR = ROOT / "workdir"

MESH_ID_RE = re.compile(r"^[0-9a-f]{12}$")
DOWNLOADABLE = {"part", "female", "male", "tool"}
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
RETENTION_SECONDS = 7 * 24 * 3600  # uploads untouched this long are pruned
PROFILE_SAMPLES = 60
NO_STORE = {"Cache-Control": "no-store"}
# upload units → millimeters (everything downstream is mm)
UNIT_SCALE = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}

log = logging.getLogger("moldgen")

app = FastAPI(title="moldgen", version=__version__)


@app.exception_handler(Exception)
async def _internal_error(_: Request, exc: Exception) -> JSONResponse:
    """Unexpected failures come back as JSON the UI can show, with the
    traceback in the server log — never an opaque HTML 500."""
    log.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content={"detail": f"internal error ({type(exc).__name__}: {exc})"},
    )


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 with a one-line readable `detail`. The default handler echoes the
    offending input back, which itself fails to serialize for NaN/Infinity
    (turning a bad request into a 500)."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()) if x != "body")
        parts.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else err.get("msg", "invalid"))
    return JSONResponse(status_code=422, content={"detail": "; ".join(parts) or "invalid request"})


class _Strict(BaseModel):
    # NaN / Infinity are legal Python-JSON literals; never let them reach
    # the geometry engine (manifold3d can crash the process on them)
    model_config = ConfigDict(allow_inf_nan=False)


class AnalyzeRequest(_Strict):
    mesh_id: str
    parting_z_mm: float
    min_draft_deg: float = 1.0
    mode: str = "compression"
    keep_side: str = "below"


class OrientRequest(_Strict):
    mesh_id: str
    down: list[float] | None = None  # current-frame direction to point at −Z
    reset: bool = False  # reload the as-uploaded orientation instead


def _generate_request_model() -> type[BaseModel]:
    """Request body for /api/generate, derived from MoldParams so every
    engine parameter is reachable and defaults can't drift apart."""
    hints = typing.get_type_hints(MoldParams)
    fields: dict[str, Any] = {"mesh_id": (str, ...)}
    for f in dataclasses.fields(MoldParams):
        if f.name == "mode":
            fields[f.name] = (str, "compression")
        elif f.default is dataclasses.MISSING:
            fields[f.name] = (hints[f.name], ...)
        else:
            fields[f.name] = (hints[f.name], f.default)
    return create_model("GenerateRequest", __base__=_Strict, **fields)


GenerateRequest = _generate_request_model()


def _part_path(mesh_id: str) -> Path:
    if not MESH_ID_RE.fullmatch(mesh_id):
        raise HTTPException(status_code=400, detail="invalid mesh id")
    path = WORKDIR / mesh_id / "part.stl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="unknown mesh id")
    return path


def _load_mesh(mesh_id: str) -> trimesh.Trimesh:
    return trimesh.load(_part_path(mesh_id))


# Ray-cast visibility is the expensive part of analysis and doesn't depend
# on the parting height — keep it for the few most recent parts.
_vis_cache: OrderedDict[tuple[str, int], PartVisibility] = OrderedDict()
_vis_lock = threading.Lock()
_VIS_CACHE_SIZE = 4


def _visibility(mesh_id: str, mesh: trimesh.Trimesh) -> PartVisibility:
    key = (mesh_id, _part_path(mesh_id).stat().st_mtime_ns)
    with _vis_lock:
        if key in _vis_cache:
            _vis_cache.move_to_end(key)
            return _vis_cache[key]
    vis = PartVisibility(mesh)
    with _vis_lock:
        _vis_cache[key] = vis
        while len(_vis_cache) > _VIS_CACHE_SIZE:
            _vis_cache.popitem(last=False)
    return vis


def _prune_workdir() -> None:
    """Delete upload directories nobody has touched for RETENTION_SECONDS."""
    cutoff = time.time() - RETENTION_SECONDS
    for d in WORKDIR.iterdir() if WORKDIR.exists() else ():
        if d.is_dir() and MESH_ID_RE.fullmatch(d.name):
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "step_support": step_io.HAS_STEP}


@app.post("/api/upload")
async def upload(
    file: UploadFile,
    linear_deflection_mm: float = Form(step_io.DEFAULT_LINEAR_DEFLECTION_MM),
    angular_deflection_deg: float = Form(step_io.DEFAULT_ANGULAR_DEFLECTION_DEG),
    units: str = Form("mm"),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if units not in UNIT_SCALE:
        raise HTTPException(status_code=400, detail=f"units must be one of {', '.join(UNIT_SCALE)}")
    if suffix not in supported_suffixes():
        if suffix in step_io.CAD_SUFFIXES:
            raise HTTPException(status_code=400, detail=step_io.INSTALL_HINT)
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type {suffix or '(none)'}; "
            f"use {', '.join(sorted(supported_suffixes()))}",
        )
    if not (0 < linear_deflection_mm <= 10 and 0 < angular_deflection_deg <= 45):
        raise HTTPException(
            status_code=400,
            detail="deflections must be in (0, 10] mm and (0, 45] degrees",
        )
    _prune_workdir()
    mesh_id = uuid.uuid4().hex[:12]
    mesh_dir = WORKDIR / mesh_id
    mesh_dir.mkdir(parents=True, exist_ok=True)
    original = mesh_dir / f"original{suffix}"
    size = 0
    with original.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                shutil.rmtree(mesh_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"file is larger than {MAX_UPLOAD_BYTES >> 20} MB",
                )
            out.write(chunk)
    upload_meta = {
        "filename": file.filename,
        "units": units,
        "linear_deflection_mm": linear_deflection_mm,
        "angular_deflection_deg": angular_deflection_deg,
    }
    (mesh_dir / "upload.json").write_text(json.dumps(upload_meta))
    try:
        mesh = _load_original(mesh_dir)
    except Exception as exc:
        shutil.rmtree(mesh_dir, ignore_errors=True)
        raise HTTPException(
            status_code=400, detail=f"could not read {file.filename}: {exc}"
        ) from exc
    export_stl(mesh, mesh_dir / "part.stl")
    mesh = trimesh.load(mesh_dir / "part.stl")  # canonical face order from here on
    return {"filename": file.filename, **_part_stats(mesh_id, mesh)}


def _load_original(mesh_dir: Path) -> trimesh.Trimesh:
    """The as-uploaded part, tessellated and scaled to mm exactly as it was
    at upload (settings from upload.json)."""
    original = next(mesh_dir.glob("original.*"))
    meta_path = mesh_dir / "upload.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    mesh = load_part(
        original,
        **{k: meta[k] for k in ("linear_deflection_mm", "angular_deflection_deg") if k in meta},
    )
    scale = UNIT_SCALE.get(meta.get("units", "mm"), 1.0)
    if scale != 1.0:
        mesh.apply_scale(scale)
    return mesh


def _part_stats(mesh_id: str, mesh: trimesh.Trimesh) -> dict:
    warnings = []
    if float(mesh.extents.max()) < 1.0:
        warnings.append(
            f"largest dimension is {mesh.extents.max():.4g} mm — if the model "
            "was exported in meters, scale it by 1000"
        )
    elif float(mesh.extents.max()) < 10.0:
        warnings.append(
            f"largest dimension is only {mesh.extents.max():.3g} mm — if the "
            "model was exported in inches, scale it by 25.4"
        )
    if not mesh.is_watertight:
        warnings.append("mesh is not watertight; repair will be attempted during generation")

    summary = parting_summary(mesh, PROFILE_SAMPLES, visibility=_visibility(mesh_id, mesh))
    suggested = summary["suggested"]
    return {
        "mesh_id": mesh_id,
        "triangles": int(len(mesh.faces)),
        "bounds": mesh.bounds.tolist(),
        "extents": mesh.extents.tolist(),
        "watertight": bool(mesh.is_watertight),
        "volume_mm3": float(mesh.volume) if mesh.is_volume else None,
        "suggested_parting_z_mm": suggested["two_part"],
        "suggested": suggested,
        "parting_profile": summary["profile"],
        "warnings": warnings,
        "mesh_url": f"/api/download/{mesh_id}/part.stl",
    }


@app.post("/api/orient")
def orient(req: OrientRequest) -> dict:
    """Re-orient the canonical part: lay it on a face, or reset.

    `down` is a direction in the part's current frame (e.g. a clicked face's
    outward normal) that should point at −Z afterwards — the part ends up
    lying on that face. The part is then rested with its bbox centered on
    the origin and its lowest point at z=0. Rotation preserves triangle
    order, so analyze face indices stay valid for the re-fetched mesh.
    """
    part_path = _part_path(req.mesh_id)
    if req.reset:
        if next(part_path.parent.glob("original.*"), None) is None:
            raise HTTPException(status_code=404, detail="original upload not found")
        mesh = _load_original(part_path.parent)
    else:
        if req.down is None or len(req.down) != 3:
            raise HTTPException(status_code=400, detail="down must be a 3-vector")
        down = np.asarray(req.down, dtype=float)
        norm = np.linalg.norm(down)
        if norm < 1e-9:
            raise HTTPException(status_code=400, detail="down must be non-zero")
        mesh = trimesh.load(part_path)
        mesh.apply_transform(trimesh.geometry.align_vectors(down / norm, [0.0, 0.0, -1.0]))
        center = mesh.bounds.mean(axis=0)
        mesh.apply_translation([-center[0], -center[1], -mesh.bounds[0][2]])
    export_stl(mesh, part_path)
    # generated halves belong to the old orientation
    for name in DOWNLOADABLE - {"part"}:
        (part_path.parent / f"{name}.stl").unlink(missing_ok=True)
    (part_path.parent / "mold.json").unlink(missing_ok=True)
    mesh = trimesh.load(part_path)
    return _part_stats(req.mesh_id, mesh)


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    if not 0 < req.min_draft_deg <= 10:
        raise HTTPException(status_code=400, detail="min_draft_deg must be in (0, 10]")
    mesh = _load_mesh(req.mesh_id)
    single = req.mode in ("open_layup", "prepreg")
    report = analyze_undercuts(
        mesh,
        req.parting_z_mm,
        min_draft_deg=req.min_draft_deg,
        single_sided=single,
        visibility=_visibility(req.mesh_id, mesh),
    )

    def half(h):
        return {
            "undercut_area_mm2": h.undercut_area_mm2,
            "low_draft_area_mm2": h.low_draft_area_mm2,
            "likely_demoldable": h.likely_demoldable,
        }

    return {
        "parting_z_mm": report.parting_z_mm,
        "min_draft_deg": report.min_draft_deg,
        "single_sided": single,
        "undercut_faces": np.flatnonzero(report.face_is_undercut).tolist(),
        "low_draft_faces": np.flatnonzero(report.face_is_low_draft).tolist(),
        "occluded_faces": np.flatnonzero(report.face_is_occluded).tolist(),
        "core_faces": np.flatnonzero(report.face_is_core).tolist(),
        "draft_deg_b64": encode_draft(report.face_draft_deg),
        "undercut_area_mm2": report.undercut_area_mm2,
        "bottom": half(report.bottom),
        "top": half(report.top),
    }


DRAFT_UNMOLDED = -128  # int8 sentinel in draft_deg_b64


def encode_draft(draft_deg: np.ndarray) -> str:
    """Per-face signed draft angles as base64 int8 (whole degrees, -90..90;
    DRAFT_UNMOLDED where no half molds the face) — 1 byte per triangle, so
    even million-triangle parts stay a small response."""
    out = np.full(len(draft_deg), DRAFT_UNMOLDED, dtype=np.int8)
    ok = np.isfinite(draft_deg)
    out[ok] = np.clip(np.rint(draft_deg[ok]), -90, 90).astype(np.int8)
    return base64.b64encode(out.tobytes()).decode("ascii")


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:  # type: ignore[valid-type]
    mesh = _load_mesh(req.mesh_id)
    params = MoldParams(**req.model_dump(exclude={"mesh_id"}))
    result = generate_mold(mesh, params, visibility=_visibility(req.mesh_id, mesh))
    if not result.ok:
        return {"ok": False, "errors": result.errors, "warnings": result.warnings}

    mesh_dir = WORKDIR / req.mesh_id
    for name in DOWNLOADABLE - {"part"}:  # don't mix halves from different modes
        (mesh_dir / f"{name}.stl").unlink(missing_ok=True)
    halves = {}
    stamp = int(time.time() * 1000)
    for name, half in result.halves.items():
        export_stl(half, mesh_dir / f"{name}.stl")
        halves[name] = {
            "url": f"/api/download/{req.mesh_id}/{name}.stl?v={stamp}",
            "url_3mf": f"/api/download/{req.mesh_id}/{name}.3mf?v={stamp}",
            **result.metadata["halves"][name],
        }
    (mesh_dir / "mold.json").write_text(
        json.dumps(
            {
                "version": __version__,
                "params": dataclasses.asdict(params),
                "warnings": result.warnings,
                "metadata": result.metadata,
            },
            indent=2,
        )
    )
    return {
        "ok": True,
        "warnings": result.warnings,
        "metadata": result.metadata,
        "halves": halves,
        "bundle_url": f"/api/download/{req.mesh_id}/mold.zip?v={stamp}",
    }


@app.get("/api/download/{mesh_id}/mold.zip")
def download_bundle(mesh_id: str) -> Response:
    """All generated halves plus a plain-text setup sheet, as one ZIP."""
    part_dir = _part_path(mesh_id).parent
    record_path = part_dir / "mold.json"
    if not record_path.exists():
        raise HTTPException(status_code=404, detail="not generated yet")
    record = json.loads(record_path.read_text())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in record["metadata"]["halves"]:
            path = part_dir / f"{name}.stl"
            if path.exists():
                zf.write(path, f"moldgen-{name}.stl")
        zf.writestr("moldgen-setup.txt", setup_sheet(record))
        zf.writestr("moldgen-params.json", json.dumps(record, indent=2))
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="moldgen-mold.zip"',
            **NO_STORE,
        },
    )


def _half_path(mesh_id: str, name: str) -> Path:
    _part_path(mesh_id)  # validates the id and that the upload exists
    if name not in DOWNLOADABLE:
        raise HTTPException(status_code=404, detail="unknown file")
    path = WORKDIR / mesh_id / f"{name}.stl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="not generated yet")
    return path


@app.get("/api/download/{mesh_id}/{name}.stl")
def download(mesh_id: str, name: str) -> FileResponse:
    path = _half_path(mesh_id, name)
    # the same URL is rewritten by re-orienting / regenerating: never cache
    return FileResponse(
        path, media_type="model/stl", filename=f"moldgen-{name}.stl", headers=NO_STORE
    )


@app.get("/api/download/{mesh_id}/{name}.3mf")
def download_3mf(mesh_id: str, name: str) -> Response:
    """The same half as 3MF: units are explicit (millimeters) in the file,
    so slicers never guess the scale, and it's smaller than binary STL."""
    mesh = trimesh.load(_half_path(mesh_id, name))
    data = trimesh.exchange.threemf.export_3MF(mesh)
    return Response(
        data,
        media_type="model/3mf",
        headers={
            "Content-Disposition": f'attachment; filename="moldgen-{name}.3mf"',
            **NO_STORE,
        },
    )


WORKDIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
