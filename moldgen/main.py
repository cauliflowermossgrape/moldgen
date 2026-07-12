"""FastAPI application entry point.

Serves the JSON API under /api and the static frontend at /.
Uploaded parts and generated molds are stored in WORKDIR (no database).
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from moldgen import __version__

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
WORKDIR = ROOT / "workdir"

app = FastAPI(title="moldgen", version=__version__)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


WORKDIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
