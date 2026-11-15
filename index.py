"""Vercel WSGI entry point; preserve the existing src/ script imports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from app import app  # noqa: E402,F401
