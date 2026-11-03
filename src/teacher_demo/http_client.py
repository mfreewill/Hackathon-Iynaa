"""Fixed same-origin static client for the teacher workspace.

Exactly seven packaged files are served by explicit route mapping:
the Arabic setup/source shell at ``/`` and its stylesheet, scripts
and font under ``/assets/``. There is no filename or path
parameter, no directory browsing and no generic static mount, so
no other packaged or source file is reachable through this module.
The app's existing Host-enforcement middleware covers these routes
like every other route.
"""
from __future__ import annotations

import pathlib

from fastapi import Request
from fastapi.responses import Response

_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"

# route -> (packaged filename, media type). This closed mapping is
# the whole client surface; nothing is derived from request input.
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
    "/assets/workspace.js": (
        "workspace.js", "text/javascript; charset=utf-8"),
    "/assets/review.js": (
        "review.js", "text/javascript; charset=utf-8"),
    "/assets/review-render.js": (
        "review-render.js", "text/javascript; charset=utf-8"),
    "/assets/studio.js": (
        "studio.js", "text/javascript; charset=utf-8"),
    "/assets/cairo.ttf": ("cairo.ttf", "font/ttf"),
}

# Local-only policy: same-origin module script, stylesheet, font,
# images (including user-selected data/blob bytes) and API calls.
# No inline script, external font/script, eval, framing or base
# override is permitted.
_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; connect-src 'self'; "
    "font-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'")

_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": _CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
}


def register_client(app) -> None:
    """Register the seven fixed client routes on ``app``.

    The packaged bytes are loaded once here, so a missing asset
    fails app creation instead of becoming a per-request error.
    """
    payloads = {
        route: ((_STATIC_DIR / filename).read_bytes(), media_type)
        for route, (filename, media_type) in _ASSETS.items()}

    def serve(route):
        content, media_type = payloads[route]
        return Response(
            content=content, media_type=media_type, headers=_HEADERS)

    @app.get("/")
    async def client_index(request: Request):
        return serve("/")

    @app.get("/assets/workspace.css")
    async def client_stylesheet(request: Request):
        return serve("/assets/workspace.css")

    @app.get("/assets/workspace.js")
    async def client_script(request: Request):
        return serve("/assets/workspace.js")

    @app.get("/assets/review.js")
    async def client_review_script(request: Request):
        return serve("/assets/review.js")

    @app.get("/assets/review-render.js")
    async def client_review_render_script(request: Request):
        return serve("/assets/review-render.js")

    @app.get("/assets/studio.js")
    async def client_studio_script(request: Request):
        return serve("/assets/studio.js")

    @app.get("/assets/cairo.ttf")
    async def client_cairo_font(request: Request):
        return serve("/assets/cairo.ttf")
