"""External Lab Host — FastAPI app mounting external-style modules on one port.

Routes:
  GET  /health                    identity + version + registered modules
  /chatbot/*                      the chatbot service (mounted; lifespan driven here)
  GET  /demos/printer-store/      demo storefront shell (assets come from /chatbot/*)
  GET  /demo-store/  /demo-store   307 → /demos/printer-store/ (old local bookmark)

First version uses in-process ASGI mounts only: no reverse proxy, no hidden
child ports, no arbitrary launch commands. Process-isolated modules can be added
later when a real service needs conflicting deps or independent fault isolation.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

import chatbot
from chatbot.main import app as chatbot_app, chatbot_init, chatbot_shutdown

IDENTITY = "emptyos-external-lab"
VERSION = "0.1.0"

# Local defaults so the host is self-contained: point the chatbot at the repo's
# own service config + data. The plugin may override any of these via env.
_CHATBOT_PKG = Path(chatbot.__file__).resolve().parent
os.environ.setdefault("CHATBOT_SITES_PATH", str(_CHATBOT_PKG / "sites.toml"))
os.environ.setdefault("CHATBOT_DATA_DIR", str(_CHATBOT_PKG / "data"))
os.environ.setdefault("CHATBOT_DEMO_ENABLED", "1")

# Static module registry (one module today). Site Lab reads this via /health and
# polls each module's `health` path for live status. A demo module reuses another
# module's mount for its data/assets (`serves_from`).
MODULES: list[dict] = [
    {
        "id": "chatbot",
        "kind": "service",
        "title": "Site Chatbot",
        "mount": "/chatbot",
        "health": "/chatbot/health",
        "manager_app": "chatbot-studio",
    },
    {
        "id": "printer-store",
        "kind": "demo",
        "title": "Northstar Office — printer pilot",
        "url": "/demos/printer-store/",
        "serves_from": "chatbot",
        "manager_app": "chatbot-studio",
    },
]


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    # Starlette does not propagate lifespan to mounted sub-apps, so drive the
    # chatbot's lifecycle explicitly here.
    await chatbot_init()
    try:
        yield
    finally:
        await chatbot_shutdown()


app = FastAPI(title="EmptyOS External Lab Host", version=VERSION, lifespan=lifespan)


def _is_admin_path(path: str) -> bool:
    """Admin routes are server-to-server (X-Admin-Token). Mirror caddy.snippet:
    CORS is applied to public routes only and stripped on /admin/*."""
    return "/admin/" in path or path.endswith("/admin")


class PublicCorsMiddleware(BaseHTTPMiddleware):
    """Add permissive CORS to public routes only; leave /*/admin/* untouched.

    The in-app per-site Origin lock (chatbot `_gate_request`/`_origin_site`) is the
    real access gate; this only lets a browser on another origin READ public
    responses, matching the production Caddy behavior."""

    async def dispatch(self, request: Request, call_next):
        if _is_admin_path(request.url.path):
            return await call_next(request)
        if request.method == "OPTIONS":
            resp = Response(status_code=200)
        else:
            resp = await call_next(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp


app.add_middleware(PublicCorsMiddleware)


@app.get("/")
async def root() -> dict:
    """Friendly landing so a bare :9100 visit isn't a raw 404."""
    return {
        "service": IDENTITY,
        "version": VERSION,
        "status": "ok",
        "modules": MODULES,
        "entries": {"health": "/health", "demo": "/demos/printer-store/", "chatbot": "/chatbot/health"},
    }


@app.get("/health")
async def health() -> dict:
    return {"service": IDENTITY, "version": VERSION, "status": "ok", "modules": MODULES}


@app.get("/demos/printer-store")
@app.get("/demos/printer-store/")
async def printer_store() -> HTMLResponse:
    """Serve the demo storefront shell. Its catalog + widget + chat calls all target
    the mounted chatbot at /chatbot, so BASE is fixed to /chatbot here."""
    path = _CHATBOT_PKG / "demo-store" / "index.html"
    if not path.exists():
        raise HTTPException(404, "demo store is not installed")
    html = path.read_text(encoding="utf-8").replace("{{BASE}}", "/chatbot")
    return HTMLResponse(html)


@app.get("/demo-store")
@app.get("/demo-store/")
async def legacy_demo_store() -> RedirectResponse:
    # Old local bookmark → new lab-host URL.
    return RedirectResponse("/demos/printer-store/", status_code=307)


# Mount the chatbot service under /chatbot (lifespan driven by this host's lifespan).
app.mount("/chatbot", chatbot_app)
