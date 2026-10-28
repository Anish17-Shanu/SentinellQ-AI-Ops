"""Vercel Python entrypoint: exposes the ASGI app for every /api/* path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from asgi import app  # noqa: E402,F401
