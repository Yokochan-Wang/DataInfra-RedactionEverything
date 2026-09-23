"""Compatibility alias for the dated closed-loop entrypoint.

Three-round closed-loop recognition is the baseline now: app.main installs
the dated 20260902 overlay itself, so this module only re-exports that app
and both entrypoints serve the same version:

    uvicorn app.main:app                 # baseline
    uvicorn app.main_20260902:app        # same app, kept for older scripts
"""
from __future__ import annotations

from app.main import app

__all__ = ["app"]
