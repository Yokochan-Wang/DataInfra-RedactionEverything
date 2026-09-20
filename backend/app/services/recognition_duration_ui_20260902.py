"""Inject the dated recognition-duration indicator into the SPA.

The legacy frontend bundle is intentionally left unchanged.  The dated
entrypoint adds a route in front of the legacy SPA fallback and injects a
same-origin, date-suffixed observer script into the returned HTML document.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.routing import Route


_MARKER = "_recognition_duration_ui_20260902_installed"
# Keep this classic script non-deferred: it must install the fetch observer
# before the head's deferred/module bundle performs its first API request.
_SCRIPT_TAG = '<script src="/recognition-duration-overlay_20260902.js"></script>'


def _frontend_index_response(frontend_index: Path) -> HTMLResponse:
    html = frontend_index.read_text(encoding="utf-8")
    if _SCRIPT_TAG not in html:
        html = html.replace("</body>", f"    {_SCRIPT_TAG}\n  </body>")
    return HTMLResponse(content=html)


def install_recognition_duration_ui_overlay_20260902(app: Any) -> None:
    """Install dated SPA routes without modifying the legacy ``main.py``."""
    if getattr(app.state, _MARKER, False):
        return

    frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    frontend_public = Path(__file__).resolve().parents[3] / "frontend" / "public"
    frontend_index = frontend_dist / "index.html"
    duration_script = frontend_public / "recognition-duration-overlay_20260902.js"
    if not frontend_index.is_file():
        return

    async def serve_root(_request: Request) -> Response:
        return _frontend_index_response(frontend_index)

    async def serve_fallback(request: Request) -> Response:
        full_path = str(request.path_params.get("full_path") or "")
        if full_path.startswith(("api/", "api", "health", "metrics", "docs", "openapi", "redoc")):
            raise StarletteHTTPException(status_code=404)
        candidate = (frontend_dist / full_path).resolve()
        try:
            candidate.relative_to(frontend_dist.resolve())
        except ValueError:
            return _frontend_index_response(frontend_index)
        if full_path == "recognition-duration-overlay_20260902.js" and duration_script.is_file():
            return FileResponse(duration_script, media_type="application/javascript")
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return _frontend_index_response(frontend_index)

    routes = app.router.routes
    root_index = next(
        (index for index, route in enumerate(routes) if getattr(route, "path", None) == "/"),
        None,
    )
    fallback_index = next(
        (
            index
            for index, route in enumerate(routes)
            if getattr(route, "path", None) == "/{full_path:path}"
        ),
        None,
    )
    if root_index is not None:
        routes.insert(root_index, Route("/", endpoint=serve_root, methods=["GET", "HEAD"], include_in_schema=False))
    if fallback_index is not None:
        # Inserting the root route before the fallback shifts its index by one.
        if root_index is not None and root_index <= fallback_index:
            fallback_index += 1
        routes.insert(
            fallback_index,
            Route("/{full_path:path}", endpoint=serve_fallback, methods=["GET", "HEAD"], include_in_schema=False),
        )
    setattr(app.state, _MARKER, True)


__all__ = ["install_recognition_duration_ui_overlay_20260902"]
