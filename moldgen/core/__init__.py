"""Core mold-generation engine.

Pure geometry — no FastAPI imports. See engine.generate_mold for the
pipeline; CLAUDE.md for the domain context (mold styles, requirements).
"""

from .engine import generate_mold
from .io import export_stl, load_part
from .types import MoldParams, MoldResult

__all__ = ["generate_mold", "export_stl", "load_part", "MoldParams", "MoldResult"]
