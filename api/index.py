"""Vercel's Python runtime entry point -- a thin re-export so the real app
code (app/main.py) stays a normal, independently runnable/testable FastAPI
app (`uvicorn app.main:app`) with no Vercel-specific code mixed in."""
from app.main import app  # noqa: F401
