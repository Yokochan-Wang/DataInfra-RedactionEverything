"""Dated application entrypoint with three-round closed-loop recognition.

The legacy ``app.main:app`` entrypoint remains unchanged.  Run this module
when you want the versioned closed-loop overlay enabled:

    uvicorn app.main_20260902:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from fastapi import Depends

from app.main import app
from app.core.auth import require_auth
from app.core.config import settings
from app.api import closed_loop_20260902
from app.services.closed_loop_runtime_20260902 import install_closed_loop_runtime_20260902
from app.services.recognition_duration_ui_20260902 import (
    install_recognition_duration_ui_overlay_20260902,
)

install_closed_loop_runtime_20260902()

# app.main registers a SPA catch-all route.  Insert the dated API routes before
# that fallback so they remain reachable without altering the legacy main.py.
app.include_router(
    closed_loop_20260902.router,
    prefix=settings.API_PREFIX,
    tags=["三轮闭环识别"],
    dependencies=[Depends(require_auth)],
)
_fallback_index = next(
    (
        index
        for index, route in enumerate(app.router.routes)
        if getattr(route, "path", None) == "/{full_path:path}"
    ),
    len(app.router.routes),
)
_dated_routes = app.router.routes[_fallback_index + 1 :]
if _dated_routes:
    del app.router.routes[_fallback_index + 1 :]
    for _route in reversed(_dated_routes):
        app.router.routes.insert(_fallback_index, _route)

# Add a visible recognition-duration indicator to the dated SPA while keeping
# the legacy frontend bundle and its source files unchanged.
install_recognition_duration_ui_overlay_20260902(app)

__all__ = ["app"]
