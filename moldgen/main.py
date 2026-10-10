"""FastAPI application: JSON API under /api, static frontend at /.

No database. Each uploaded part gets a directory under workdir/ keyed by a
short random id. The upload is converted once to a canonical binary
part.stl; analysis, generation and the frontend preview all read that same
file, so triangle order stays consistent everywhere (undercut face indices
map 1:1 onto the triangles the viewer renders).
"""

from __future__ import annotations

import dataclasses
import io
import json
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
)
from moldgen.core import step_io
from moldgen.core.io import load_part, supported_suffixes
from moldgen.core.undercuts import PartVisibility, parting_profile, suggest_parting_plane

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
WORKDIR = ROOT / "workdir"

MESH_ID_RE = re.compile(r"^[0-9a-f]{12}$")
DOWNLOADABLE = {"part", "female", "male", "tool"}
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
RETENTION_SECONDS = 7 * 24 * 3600  # uploads untouched this long are pruned
PROFILE_SAMPLES = 60
NO_STORE = {"Cache-Control": "no-store"}

app = FastAPI(title="moldgen", version=__version__)


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
    deflection = {
        "linear_deflection_mm": linear_deflection_mm,
        "angular_deflection_deg": angular_deflection_deg,
    }
    try:
        mesh = load_part(original, **deflection)
    except Exception as exc:
        shutil.rmtree(mesh_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"could not read {file.filename}: {exc}")
    (mesh_dir / "upload.json").write_text(
        json.dumps({"filename": file.filename, **deflection})
    )
    export_stl(mesh, mesh_dir / "part.stl")
    mesh = trimesh.load(mesh_dir / "part.stl")  # canonical face order from here on
    return {"filename": file.filename, **_part_stats(mesh_id, mesh)}


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

    vis = _visibility(mesh_id, mesh)
    two = parting_profile(mesh, PROFILE_SAMPLES, visibility=vis)
    single = parting_profile(mesh, PROFILE_SAMPLES, single_sided=True, visibility=vis)
    suggested = {
        "two_part": suggest_parting_plane(mesh, visibility=vis),
        "below": suggest_parting_plane(mesh, side="below", visibility=vis),
        "above": suggest_parting_plane(mesh, side="above", visibility=vis),
    }
    return {
        "mesh_id": mesh_id,
        "triangles": int(len(mesh.faces)),
        "bounds": mesh.bounds.tolist(),
        "extents": mesh.extents.tolist(),
        "watertight": bool(mesh.is_watertight),
        "volume_mm3": float(mesh.volume) if mesh.is_volume else None,
        "suggested_parting_z_mm": suggested["two_part"],
        "suggested": suggested,
        "parting_profile": {
            "z": two.z.tolist(),
            "two_part": (two.bottom_mm2 + two.top_mm2).tolist(),
            "below": single.bottom_mm2.tolist(),
            "above": single.top_mm2.tolist(),
        },
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
        original = next(part_path.parent.glob("original.*"), None)
        if original is None:
            raise HTTPException(status_code=404, detail="original upload not found")
        meta_path = part_path.parent / "upload.json"
        deflection = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            deflection = {
                k: meta[k]
                for k in ("linear_deflection_mm", "angular_deflection_deg")
                if k in meta
            }
        mesh = load_part(original, **deflection)
    else:
        if req.down is None or len(req.down) != 3:
            raise HTTPException(status_code=400, detail="down must be a 3-vector")
        down = np.asarray(req.down, dtype=float)
        norm = np.linalg.norm(down)
        if norm < 1e-9:
            raise HTTPException(status_code=400, detail="down must be non-zero")
        mesh = trimesh.load(part_path)
        mesh.apply_transform(
            trimesh.geometry.align_vectors(down / norm, [0.0, 0.0, -1.0])
        )
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
        "undercut_area_mm2": report.undercut_area_mm2,
        "bottom": half(report.bottom),
        "top": half(report.top),
    }


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:  # type: ignore[valid-type]
    mesh = _load_mesh(req.mesh_id)
    params = MoldParams(**req.model_dump(exclude={"mesh_id"}))
    result = generate_mold(mesh, params)
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


def setup_sheet(record: dict) -> str:
    """Human-readable build notes for the generated mold."""
    p = record["params"]
    meta = record["metadata"]
    hw = meta.get("hardware", {})
    lines = [
        f"moldgen {record.get('version', '')} — mold setup sheet",
        "=" * 48,
        "",
        f"Mode:           {p['mode']}",
        f"Parting plane:  z = {meta['parting_z_mm']:.3f} mm (part frame)",
        f"Wall margin:    {p['wall_margin_mm']:g} mm   base: {p['base_thickness_mm']:g} mm",
    ]
    if p.get("shrink_comp_pct"):
        lines.append(f"Shrink comp.:   +{p['shrink_comp_pct']:g} % cavity scale")
    if p.get("cavity_offset_mm"):
        lines.append(f"Cavity offset:  {p['cavity_offset_mm']:g} mm")
    lines += ["", "Files:"]
    for name, h in meta["halves"].items():
        lines.append(
            f"  moldgen-{name}.stl  {h['volume'] / 1000:.0f} cm³, "
            f"{h['triangles']:,} triangles"
        )
    forged = meta.get("forged")
    if forged:
        ch = forged.get("charge", {})
        lines += [
            "",
            "Compression (forged carbon):",
            f"  plunge {forged['plunge_mm']:g} mm, pinch gap "
            f"{forged['pocket_clearance_mm']:g} mm/side, wall draft {forged['draft_deg']:g}°",
        ]
        if ch:
            lines += [
                f"  part volume {ch['part_volume_cm3']:.1f} cm³ — forged part "
                f"≈ {ch['est_part_mass_g']:.0f} g at 1.5 g/cm³; weigh the charge "
                "to that plus a few % for flash",
                f"  loading volume {ch['loading_volume_cm3']:.1f} cm³ "
                f"({ch['loading_ratio']:.1f}x the part)",
            ]
        cores = forged.get("cores", {})
        if cores.get("to_male") or cores.get("to_female"):
            lines.append("  shell part: internal core(s) built onto the opposite half")
    if "pins" in hw:
        lines.append(
            f"  registration pins: 4x Ø{2 * hw['pins']['radius']:.1f} mm, "
            f"{hw['pins']['clearance']:g} mm clearance"
        )
    if "bolts" in hw:
        lines.append(
            f"  clamping bolts: {len(hw['bolts']['centers'])}x "
            f"M{hw['bolts']['bolt_diameter']:g} through both halves"
        )
    if "jack_screws" in hw:
        lines.append(
            f"  jack screws: {len(hw['jack_screws']['centers'])}x "
            f"M{hw['jack_screws']['bolt_diameter']:g} (tap-size holes in the male)"
        )
    if "ejector" in hw:
        lines.append("  ejector hole: plug with wax/tape before loading")
    pre = meta.get("prepreg")
    if pre:
        lines += [
            "",
            "Laminating tool:",
            f"  trim allowance {pre['trim_allowance_mm']:g} mm with a "
            f"{pre['trim_kick_deg']:g}° kick — trim to the witness line",
        ]
        lines += [f"  - {n}" for n in pre.get("notes", [])]
    if record.get("warnings"):
        lines += ["", "Warnings:"] + [f"  - {w}" for w in record["warnings"]]
    lines += [
        "",
        "General: seal printed tools, apply release agent (several coats of",
        "wax or a semi-permanent release) and do a test closure before the",
        "first real charge.",
        "",
    ]
    return "\n".join(lines)


@app.get("/api/download/{mesh_id}/{name}.stl")
def download(mesh_id: str, name: str) -> FileResponse:
    _part_path(mesh_id)  # validates the id and that the upload exists
    if name not in DOWNLOADABLE:
        raise HTTPException(status_code=404, detail="unknown file")
    path = WORKDIR / mesh_id / f"{name}.stl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="not generated yet")
    # the same URL is rewritten by re-orienting / regenerating: never cache
    return FileResponse(
        path, media_type="model/stl", filename=f"moldgen-{name}.stl", headers=NO_STORE
    )


WORKDIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
