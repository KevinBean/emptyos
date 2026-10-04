"""FastAPI web server — dashboard + app route mounting."""

from __future__ import annotations

import asyncio
import inspect
import posixpath
from pathlib import Path
from urllib.parse import unquote, urlsplit
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles

from emptyos.web.auth_exchange import safe_next
from emptyos.web.routes_auth import (
    register_auth,
    register_byok_middleware,
    register_cloud_routes,
    register_posture,
    register_presentation_middleware,
    register_presentation_routes,
    register_rate_limit,
)
from emptyos.web.routes_sdk import register_sdk_routes, register_shortcut_routes
from emptyos.web.routes_vault import (
    _vault_query_restricted,  # noqa: F401 — re-export (tests/test_unit_vault_query_gate.py)
    _vault_query_visible,  # noqa: F401 — re-export (tests/test_unit_vault_query_gate.py)
    register_vault_file_routes,
    register_vault_query_routes,
)
from emptyos.web.topology import (
    _app_dependency_cycles,  # noqa: F401 — re-export (tests/test_unit_topology_analysis.py)
    register_topology_routes,
)

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


def create_server(kernel: Kernel) -> FastAPI:
    """Create the FastAPI app with kernel context."""
    from emptyos.kernel.app_loader import AppState

    server = FastAPI(title="EmptyOS", version="0.1.0")

    # --- Presentation-mode response scrubber (extracted to routes_auth.py) ---
    register_presentation_middleware(server, kernel)

    # Without viewport-fit=cover, iOS resolves env(safe-area-inset-*) to 0
    # and every safe-area rule downstream is dead code. Patching here at the
    # response boundary so per-page boilerplate can't regress the invariant.
    import re as _re
    from starlette.middleware.base import BaseHTTPMiddleware as _BHM
    from starlette.responses import Response as _R

    _VIEWPORT_META_RE = _re.compile(
        rb'(<meta\s+[^>]*\bname\s*=\s*["\']viewport["\'][^>]*\bcontent\s*=\s*)["\']([^"\']*)["\']',
        _re.IGNORECASE,
    )
    _HEAD_OPEN_RE = _re.compile(rb"<head\b[^>]*>", _re.IGNORECASE)
    _DEFAULT_VIEWPORT_META = (
        b'\n<meta name="viewport" '
        b'content="width=device-width, initial-scale=1.0, viewport-fit=cover">'
    )

    def _inject_viewport_fit(body: bytes) -> bytes:
        m = _VIEWPORT_META_RE.search(body)
        if m:
            content = m.group(2)
            if b"viewport-fit" in content.lower():
                return body
            new_content = content.rstrip(b",; ") + b", viewport-fit=cover"
            return body[: m.start(2)] + new_content + body[m.end(2) :]
        h = _HEAD_OPEN_RE.search(body)
        if not h:
            return body
        return body[: h.end()] + _DEFAULT_VIEWPORT_META + body[h.end() :]

    _PUBLIC_FACE_TAG = b"\n<script>window.EOS_PUBLIC_FACE=true;</script>"

    def _inject_public_face(body: bytes) -> bytes:
        # Mark a page as the anonymous public face so eos.js renders stripped
        # chrome (no owner nav, no private API probes). Set by AuthMiddleware
        # for unauthenticated callers on a [provides.web].public_routes page.
        h = _HEAD_OPEN_RE.search(body)
        if not h:
            return body
        return body[: h.end()] + _PUBLIC_FACE_TAG + body[h.end() :]

    class ViewportMiddleware(_BHM):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            ctype = (response.headers.get("content-type") or "").lower()
            if "text/html" not in ctype:
                return response
            # Drain streamed body so we can patch it.
            chunks = [c async for c in response.body_iterator]
            body = b"".join(chunks)
            patched = _inject_viewport_fit(body)
            if getattr(request.state, "public_face", False):
                patched = _inject_public_face(patched)
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return _R(
                content=patched,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )

    server.add_middleware(ViewportMiddleware)

    # --- Auth middleware + login/logout (extracted to routes_auth.py) ---
    # Activates when token OR password is set — see routes_auth.register_auth.
    # _auth_token/_login_password stay in the spine because /api/health and
    # /ws key off them; register_auth returns the credential-check closures
    # /api/health reuses ((None, None) when auth is not configured — health
    # guards on _auth_token/_login_password before calling them).
    _auth_token: str = kernel.config.auth_token
    _login_password: str = kernel.config.login_password
    _check_token, _check_bearer = register_auth(server, kernel)
    # Operator-vs-user route policy. Registered AFTER auth so it is outermost and
    # dispatches first — an operator-only core route is refused before auth even
    # looks at the request. Unconditional and a no-op in operator posture.
    register_posture(server, kernel)

    @server.get("/api/health")
    async def health(request: Request, full: bool = False):
        # Auth-aware. Anonymous callers (Docker readiness, offline page,
        # public kiosk) get a minimal liveness body — no vault paths, viewer
        # templates, or diagnostics. Owner callers (browser cookie, CLI
        # bearer) get vault info + viewer, and `full=true` adds the deep
        # diagnostics. Local mode (no creds configured) is fully open.
        if _auth_token or _login_password:
            _hc = request.cookies.get("eos_session", "")
            _hb = request.headers.get("authorization", "")
            authed = _check_token(_hc) or (
                _hb.lower().startswith("bearer ") and _check_bearer(_hb[7:].strip())
            )
        else:
            authed = True

        vault = kernel.config.notes_path
        vault_name = vault.name if vault else ""
        # `apps` count lets readiness probes wait until manifests are
        # populated before firing traffic — daemon-listening != apps-mounted.
        try:
            n_apps = len(kernel.apps.manifests)
        except Exception:
            n_apps = 0
        result = {
            "status": "ok" if n_apps > 0 else "starting",
            "name": kernel.config.get("os.name", "EmptyOS"),
            "apps": n_apps,
        }
        if not authed:
            # Minimal liveness only — never expose vault paths or internals
            # to an unauthenticated caller (closes the anonymous ?full=true
            # diagnostics leak).
            return result

        # Where "/" lands, so the nav can mark the right app as Home. Owner
        # pages only: an anonymous caller could not reach "/" to learn it.
        result["home"] = home_target(kernel)
        result["vault_name"] = vault_name
        result["vault_path"] = str(vault).replace("\\", "/") if vault else ""
        # Viewer config (URI templates for note links) from whichever
        # plugin registered as service "viewer". Frontend falls back to
        # its built-in default templates when absent.
        viewer = kernel.services.get_optional("viewer")
        if viewer and hasattr(viewer, "uri_templates"):
            try:
                result["viewer"] = {
                    "id": getattr(viewer, "name", "viewer"),
                    "uri_templates": viewer.uri_templates(),
                }
            except Exception:
                pass
        # The nav's account menu. Present for every owner (local mode too: it
        # carries the name, Settings and Usage); the two links appear only
        # where they mean something. A proxy in front of the daemon (the
        # EnglishOS control plane) owns the real session, so it names its own
        # account page and sign-out endpoint; otherwise a browser session
        # cookie is the daemon's own, ended by POST /logout.
        result["account"] = _account_links(
            kernel.config.get,
            bool(_check_token and _check_token(request.cookies.get("eos_session", ""))),
        )
        if not full:
            return result

        # Capabilities + providers
        try:
            result["capabilities"] = await kernel.capabilities.status()
        except Exception:
            result["capabilities"] = {}

        # Apps summary with error details
        app_states = {}
        error_apps = []
        for m in kernel.apps.manifests.values():
            state = kernel.apps.state_of(m.id).value
            app_states[state] = app_states.get(state, 0) + 1
            if state == "error":
                error_apps.append(m.id)
        result["apps"] = {
            "total": len(kernel.apps.manifests),
            "by_state": app_states,
            "errors": error_apps,
        }

        # Services
        result["services"] = [
            {"name": e.name, "status": e.status.value} for e in kernel.services.list()
        ]

        # Plugins
        result["plugins"] = [
            {"id": m.id, "loaded": m.id in kernel.plugins.instances}
            for m in kernel.plugins.manifests.values()
        ]

        # Health plugin deep check
        hp = kernel.services.get_optional("health")
        if hp and hasattr(hp, "check"):
            try:
                deep = await hp.check()
                result["uptime_seconds"] = deep.get("uptime_seconds", 0)
                result["recent_problems"] = deep.get("recent_problems", [])
            except Exception:
                pass

        # Integrity audit — the P12 privacy check rglobs + reads the whole
        # repo (seconds of sync I/O), so it runs off-loop and is TTL-cached:
        # unwrapped it stalled EVERY request for ~3.5s per nav health poll
        # (py-spy verified 2026-07-04).
        integrity_app = kernel.apps.instances.get("integrity")
        if integrity_app and hasattr(integrity_app, "_run_audit"):
            try:
                import time as _time

                cached = getattr(server.state, "_integrity_cache", None)
                if cached and _time.monotonic() - cached[0] < 120:
                    audit = cached[1]
                else:
                    audit = await asyncio.to_thread(integrity_app._run_audit)
                    server.state._integrity_cache = (_time.monotonic(), audit)
                result["integrity"] = {
                    "score": audit["total_score"],
                    "max": audit["max_score"],
                    "pct": audit["pct"],
                    "dimensions": {k: v["score"] for k, v in audit["dimensions"].items()},
                    "violations": len(audit.get("violations", [])),
                    "growth_signals": [gs["signal"] for gs in audit.get("growth_signals", [])[:3]],
                }
            except Exception:
                pass

        return result

    @server.get("/api/health/gpu")
    async def health_gpu():
        hp = kernel.services.get_optional("health")
        if hp and hasattr(hp, "gpu_status"):
            return await hp.gpu_status()
        return {"error": "health plugin not available"}

    # --- Presentation mode routes (extracted to routes_auth.py) ---
    register_presentation_routes(server, kernel)

    # --- Demo mode status ---
    @server.get("/api/demo/status")
    async def demo_status():
        return {
            "enabled": kernel.config.demo_enabled,
            "banner": (
                "Public demo — sample vault, don't put real data here. "
                "GPU-powered features (image generation, voice) are disabled."
            )
            if kernel.config.demo_enabled
            else "",
            "install_url": "https://github.com/KevinBean/emptyos",
            "about_url": "https://eos.binbian.net",
        }

    # --- OpenAI-compatible facade — any LiteLLM tool can point at /v1 and
    # inherit EmptyOS's think provider chain (claude-cli first via Max
    # subscription, ollama fallback, openai-mini, etc.) plus middleware
    # (Headroom compress) and the cloud consent gate. See openai_facade.py.
    from emptyos.web.openai_facade import register_routes as _register_openai_facade  # noqa: PLC0415

    _register_openai_facade(server, kernel)

    # --- Think-capability status (drives the "AI offline" system banner) ---
    @server.get("/api/think-status")
    async def think_status():
        try:
            cap = kernel.capability("think")
        except Exception:
            return {"available": False, "reason": "think capability not registered"}

        # Simulate-offline setting wins over real provider state — this is what
        # lets the AI-off walkthrough be reproducible.
        try:
            if cap._simulate_offline():
                return {
                    "available": False,
                    "reason": "simulated offline (capability.simulate_offline)",
                    "simulated": True,
                }
        except Exception:
            pass

        return await _think_status_body(cap)

    # --- AI form-fill / i18n / 4D timeline / field-suggest
    # (extracted to routes_sdk.py) ---
    register_sdk_routes(server, kernel)

    # --- Cloud consent endpoints (extracted to routes_auth.py) ---
    register_cloud_routes(server, kernel)

    @server.post("/api/health/gpu/free")
    async def health_gpu_free():
        hp = kernel.services.get_optional("health")
        if hp and hasattr(hp, "gpu_free"):
            return await hp.gpu_free()
        return {"error": "health plugin not available"}

    @server.get("/api/apps")
    async def list_apps():
        # Only surface apps that are actually serving routes. Discovered-but-
        # not-loaded apps (excluded by the store install gate, or load errors)
        # would 404 if a caller tried to navigate to them — see eos.js nav
        # filter, which trusts this list to reflect "what's reachable". The
        # /store catalog endpoint (/api/catalog/apps) is the surface for
        # browsing every manifest including not-installed ones.
        from emptyos.sdk.app_icons import active_system_icon_ids, manifest_icon_id

        active_icon_ids = active_system_icon_ids(kernel.config.data_dir)
        return [
            {
                "id": m.id,
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "icon": (m.raw.get("app", {}) or {}).get("icon", ""),
                "icon_id": manifest_icon_id(m, active_icon_ids),
                "state": kernel.apps.state_of(m.id).value,
                "web_prefix": m.provides.get("web", {}).get("prefix", ""),
                "cli_commands": m.provides.get("cli", {}).get("commands", []),
                # Declared grouping + function-search data layer (read straight
                # off the manifest; see .claude/rules/store.md + user-intent.md).
                "store_category": (m.raw.get("app", {}) or {}).get("store_category", "other"),
                "user_intent": (m.raw.get("app", {}) or {}).get("user_intent", []),
            }
            for m in kernel.apps.reachable_manifests()
        ]

    @server.get("/api/apps/load-timings")
    async def app_load_timings():
        """Per-app boot-time timings (import_ms / setup_ms / total_ms).

        Populated by ``app_loader.load`` as each app boots. Useful for
        diagnosing slow boots: sort by ``total_ms`` to see which apps
        blocked the loader.
        """
        timings = kernel.apps.get_load_timings()
        rows = sorted(
            ({"app_id": aid, **t} for aid, t in timings.items()),
            key=lambda r: r["total_ms"],
            reverse=True,
        )
        return {
            "apps": rows,
            "total_ms": sum(t["total_ms"] for t in timings.values()),
            "slowest_app_id": rows[0]["app_id"] if rows else None,
        }

    @server.get("/api/apps/clusters")
    async def app_clusters():
        """Auto-clustered apps by dependency graph. Fully dynamic."""
        from emptyos.sdk.clustering import get_clusters

        return get_clusters(kernel.apps.manifests)

    @server.get("/api/apps/sections")
    async def app_sections():
        """Reachable apps grouped into stable, declared store_category sections.

        Same reachability gate as ``/api/apps`` (skip discovered-but-not-loaded
        + errored). Unlike ``/api/apps/clusters`` (dependency-graph, dynamic),
        the section names are fixed and come from each app's manifest
        ``[app] store_category`` — see ``emptyos/sdk/app_sections.py``.
        """
        from emptyos.sdk.app_icons import active_system_icon_ids
        from emptyos.sdk.app_sections import group_by_category

        reachable = {m.id for m in kernel.apps.reachable_manifests()}
        return group_by_category(
            kernel.apps.manifests,
            reachable,
            icon_ids=active_system_icon_ids(kernel.config.data_dir),
        )

    @server.get("/api/app-icons/sprite")
    async def app_icon_sprite():
        """Serve the trusted built-in sprite plus approved Studio overlays."""
        from emptyos.sdk.app_icons import effective_sprite

        source = (Path(__file__).parent / "static" / "app-icons.svg").read_text(encoding="utf-8")
        return Response(
            effective_sprite(source, kernel.config.data_dir),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    @server.get("/api/apps/{app_id}")
    async def app_detail(app_id: str):
        """Full app info — auto-generated from manifest + code."""
        m = kernel.apps.manifests.get(app_id)
        if not m:
            return JSONResponse({"error": f"App not found: {app_id}"}, status_code=404)

        prefix = m.provides.get("web", {}).get("prefix", "")
        pages_dir = m.path / "pages"

        # Get routes from loaded instance
        routes = []
        instance = kernel.apps.instances.get(app_id)
        if instance:
            routes = [
                {"method": meta["method"].upper(), "path": prefix + meta["path"]}
                for meta, _ in instance.get_web_methods()
            ]

        return {
            "id": m.id,
            "name": m.name,
            "version": m.version,
            "description": m.description,
            "state": kernel.apps.state_of(m.id).value,
            "requires": {
                "capabilities": m.requires.get("capabilities", []),
                "apps": m.requires.get("apps", []),
                "connectors": m.requires.get("connectors", []),
                "services": m.requires.get("services", []),
            },
            "provides": {
                "cli": m.provides.get("cli", {}).get("commands", []),
                "web_prefix": prefix,
                "events": m.provides.get("events", {}).get("emits", []),
                "ui_type": "custom" if pages_dir.exists() else "auto-generated",
            },
            "routes": routes,
            "export": {
                "enabled": bool(m.provides.get("export", {}).get("enabled")),
                "mode": m.provides.get("export", {}).get("mode", ""),
                "fallbacks": m.provides.get("export", {}).get("fallbacks", []),
            },
        }

    @server.post("/api/apps/{app_id}/export")
    async def app_export(app_id: str, format: str = "zip"):
        """Produce a standalone bundle for an app.

        Requires the app to declare ``[provides.export].enabled = true``.
        Returns a streaming ZIP by default; ``?format=single-html`` returns a
        single HTML file.
        """
        m = kernel.apps.manifests.get(app_id)
        if not m:
            return JSONResponse({"error": f"App not found: {app_id}"}, status_code=404)
        export_cfg = m.provides.get("export", {}) or {}
        if not export_cfg.get("enabled"):
            return JSONResponse(
                {"error": f"App '{app_id}' has not declared [provides.export].enabled = true"},
                status_code=400,
            )
        if format not in ("dir", "zip", "single-html"):
            return JSONResponse({"error": f"unknown format: {format}"}, status_code=400)

        import tempfile

        from starlette.responses import FileResponse

        from emptyos.sdk.exporter import AppExporter

        if app_id not in kernel.apps.manifests:
            return JSONResponse({"error": f"app '{app_id}' not found"}, status_code=404)
        if app_id not in kernel.apps.instances and app_id not in kernel.apps.enabled_ids():
            return JSONResponse(
                {"error": f"App '{app_id}' is not enabled - install/enable via /store and restart."},
                status_code=404,
            )
        instance = kernel.apps.instances.get(app_id) or await kernel.apps.load(app_id)
        tmp_root = Path(tempfile.mkdtemp(prefix=f"eos-export-{app_id}-"))
        out_dir = tmp_root / app_id
        exporter = AppExporter(instance, out_dir=out_dir, fmt=format)  # type: ignore[arg-type]
        result = await exporter.build()

        if format == "zip":
            return FileResponse(
                str(result),
                media_type="application/zip",
                filename=f"{app_id}-export.zip",
            )
        if format == "single-html":
            # octet-stream, NOT text/html: this is a download, not a page to
            # render. Every text/html response passes through the viewport
            # middleware, which rewrites the first literal `<head>` it finds —
            # and in an inlined bundle that string occurs inside an eos.js
            # comment ("may be loaded synchronously in <head> before <body>
            # exists"). The injected <meta> landed mid-comment and broke the
            # JS, so every single-html export downloaded over HTTP arrived
            # corrupted. Measured 2026-08-20; the on-disk build path was never
            # affected, which is why it went unnoticed.
            # See .claude/rules/dev-gotchas.md § "any response with
            # content-type: text/html is rewritten in middleware".
            return FileResponse(
                str(result),
                media_type="application/octet-stream",
                filename=f"{app_id}.html",
            )
        # dir: return a JSON pointer (CLI users get the tree on disk; web users
        # typically want the zip)
        return {"ok": True, "path": str(result)}

    @server.post("/api/apps/{app_id}/rpc/{method}")
    async def app_rpc(app_id: str, method: str, request: Request):
        """Live-mode parity for in-browser EOS.callApp used by export groups.

        Dispatches to a public method on the app instance with kwargs from
        the JSON body. Private methods (leading underscore) are 403."""
        if method.startswith("_"):
            return JSONResponse({"error": "private method"}, status_code=403)
        if app_id not in kernel.apps.manifests:
            return JSONResponse({"error": f"app '{app_id}' not found"}, status_code=404)
        if app_id not in kernel.apps.instances and app_id not in kernel.apps.enabled_ids():
            return JSONResponse(
                {"error": f"App '{app_id}' is not enabled - install/enable via /store and restart."},
                status_code=404,
            )
        instance = kernel.apps.instances.get(app_id) or await kernel.apps.load(app_id)
        if instance is None:
            return JSONResponse({"error": f"app '{app_id}' not loaded"}, status_code=404)
        fn = getattr(instance, method, None)
        if not callable(fn):
            return JSONResponse({"error": f"no such method: {app_id}.{method}"}, status_code=404)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            result = fn(**(payload or {}))
            if inspect.isawaitable(result):
                result = await result
            return JSONResponse(result if isinstance(result, (dict, list)) else {"result": result})
        except TypeError as e:
            return JSONResponse({"error": f"bad kwargs: {e}"}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @server.get("/api/export-groups")
    async def list_export_groups():
        """Return declared groups + per-member export-enabled status."""
        from emptyos.sdk.exporter import load_groups

        groups = load_groups(Path(kernel.config.path).parent / "export-groups.toml")
        out = []
        for g in groups:
            members = []
            for app_id in g.get("apps", []):
                m = kernel.apps.manifests.get(app_id)
                members.append(
                    {
                        "id": app_id,
                        "name": m.name if m else app_id,
                        "found": m is not None,
                        "export_enabled": bool(
                            (m.provides.get("export", {}) if m else {}).get("enabled")
                        ),
                    }
                )
            out.append({**g, "members_detail": members})
        return out

    @server.post("/api/export-groups/{group_id}/build")
    async def build_export_group(group_id: str, format: str = "zip"):
        """Build a group bundle — ZIP by default."""
        if format not in ("dir", "zip"):
            return JSONResponse({"error": f"unsupported format: {format}"}, status_code=400)
        import tempfile

        from starlette.responses import FileResponse

        from emptyos.sdk.exporter import GroupExporter, load_groups

        groups = load_groups(Path(kernel.config.path).parent / "export-groups.toml")
        match = next((g for g in groups if g.get("id") == group_id), None)
        if not match:
            return JSONResponse({"error": f"group '{group_id}' not found"}, status_code=404)

        tmp_root = Path(tempfile.mkdtemp(prefix=f"eos-group-{group_id}-"))
        out_dir = tmp_root / group_id
        exporter = GroupExporter(kernel, match, out_dir=out_dir, fmt=format)
        try:
            result, warnings = await exporter.build()
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

        if format == "zip":
            return FileResponse(
                str(result),
                media_type="application/zip",
                filename=f"{group_id}-export.zip",
            )
        return {"ok": True, "path": str(result), "warnings": warnings}

    @server.get("/api/services")
    async def list_services():
        return [
            {"name": e.name, "type": type(e.instance).__name__, "status": e.status.value}
            for e in kernel.services.list()
        ]

    @server.get("/api/capabilities")
    async def list_capabilities():
        return await kernel.capabilities.status()

    @server.get("/api/capabilities/full")
    async def list_capabilities_full():
        """Capability inspector data — providers + recovery hints + cloud consent.

        Shape:
            {
              "capabilities": {
                "<cap>": {
                  "active": "<provider name>" | None,
                  "providers": [{name, available, reason, recovery, domain, is_cloud, ...}]
                },
                ...
              },
              "consent": {policy, approved, pending, last_decisions},
              "network_mode": "local"|"private"|"public"
            }
        """
        snapshot = await kernel.capabilities.status()
        out = {}
        for cap_name, rows in snapshot.items():
            active = next((r["name"] for r in rows if r.get("available")), None)
            out[cap_name] = {"active": active, "providers": rows}
        cm = getattr(kernel, "cloud_consent", None)
        consent = (
            cm.status()
            if cm
            else {"policy": "ask", "approved": [], "pending": [], "last_decisions": {}}
        )
        try:
            net_mode = kernel.config.network_mode
        except Exception:
            net_mode = "local"
        return {"capabilities": out, "consent": consent, "network_mode": net_mode}

    @server.get("/api/capabilities/think/effective")
    async def think_effective(app: str = "", domain: str = ""):
        """Resolve the effective `think` provider for a given (app, domain).

        Mirrors the resolution order in `base_app.py::think`:
          1. settings[`think.app.<id>`]              — single override wins
          2. settings[`think.app.<id>.providers`]    — chain override
          3. settings[`think.domain.<domain>`]       — domain override
          4. capability chain (per-domain subchain from emptyos.toml)

        Returns:
            {
              "provider":  "<first reachable provider name>",
              "source":    "override-app" | "override-app-chain" | "override-domain" | "chain",
              "chain":     ["<name>", ...],
              "override":  "<raw setting value>" | None,
              "providers": [{name, variant, available, is_cloud, model}, ...],
            }

        Drives the shared model-pill UI (`EOS_UI.modelPill`) so consumers
        don't have to replicate the resolution logic client-side.
        """
        from emptyos.sdk.base_app_think import routing_settings

        think_cap = kernel.capabilities.get("think")
        settings = routing_settings(kernel)  # None in a locked build, as think() does

        def _prov_meta(p):
            return {
                "name": getattr(p, "name", ""),
                "variant": getattr(p, "variant", "") or "",
                "available": True,  # populated below if status() was called
                "is_cloud": bool(getattr(p, "is_cloud", False)),
                "model": getattr(p, "model", "") or "",
                "ability": getattr(p, "ability", "standard"),
                "auth_mode": getattr(p, "auth_mode", "") or "",
            }

        chain_providers = think_cap._get_providers(domain=domain or None)
        chain_names = [getattr(p, "name", "") for p in chain_providers]

        override_app = settings.get(f"think.app.{app}") if (settings and app) else None
        override_app_chain = (
            settings.get(f"think.app.{app}.providers") if (settings and app) else None
        )
        override_domain = settings.get(f"think.domain.{domain}") if (settings and domain) else None

        provider = ""
        source = "chain"
        if override_app and "," not in str(override_app):
            provider = str(override_app)
            source = "override-app"
        elif override_app_chain:
            first = str(override_app_chain).split(",")[0].strip()
            if first:
                provider = first
                source = "override-app-chain"
        elif override_domain:
            provider = str(override_domain)
            source = "override-domain"
        else:
            # First available in the chain.
            for p in chain_providers:
                try:
                    if await p.available():
                        provider = getattr(p, "name", "")
                        break
                except Exception:
                    continue
            if not provider and chain_names:
                provider = chain_names[0]

        # Full provider listing (so the UI can render the dropdown without
        # a second round-trip to /api/capabilities/full).
        all_providers = []
        seen = set()
        for p in chain_providers + list(getattr(think_cap, "providers", []) or []):
            n = getattr(p, "name", "")
            key = (n, getattr(p, "variant", ""))
            if key in seen:
                continue
            seen.add(key)
            try:
                avail = await p.available()
            except Exception:
                avail = False
            meta = _prov_meta(p)
            meta["available"] = bool(avail)
            all_providers.append(meta)

        override_raw = override_app or override_app_chain or override_domain or None
        # Ability of the active provider — drives model-ability feature gating
        # (EOS_UI.abilityGate). See .claude/rules/model-ability.md.
        active_ability = next(
            (m["ability"] for m in all_providers if m["name"] == provider), "standard"
        )
        # Auth mode of the active provider — drives the model pill's auth chip
        # (login / api-key / byok / local). See .claude/rules/model-pill.md.
        active_auth_mode = next(
            (m["auth_mode"] for m in all_providers if m["name"] == provider), ""
        )
        return {
            "provider": provider,
            "source": source,
            "chain": chain_names,
            "override": str(override_raw) if override_raw else None,
            "providers": all_providers,
            "active_ability": active_ability,
            "active_auth_mode": active_auth_mode,
            # A locked build refuses a switch (settings `_operator_key`), so
            # the pill shows the model without offering one.
            "locked": bool(getattr(kernel.config, "cloud_locked", False)),
        }

    @server.get("/api/tailnet")
    async def tailnet_status():
        """Tailscale-aware integration data for /system.

        Returns {available, status, identity, peers, funnel_enabled} when the
        tailscale plugin is loaded + the CLI is present + backend is Running.
        Returns {available: False, reason: "..."} otherwise. Never raises.
        """
        ts = kernel.services.get_optional("tailscale")
        if not ts:
            return {"available": False, "reason": "tailscale plugin not loaded"}
        try:
            up = await ts.available()
        except Exception as e:
            return {"available": False, "reason": f"probe failed: {e}"}
        if not up:
            try:
                summary = await ts.status()
            except Exception:
                summary = {}
            return {
                "available": False,
                "reason": summary.get("backend_state", "not running"),
                "status": summary,
            }
        # `tailscale serve` is a separate opt-in plugin. Read it best-effort:
        # its HTTPS origin is the only secure-context URL a phone can install a
        # PWA from, so it wins over the plain-http tailnet IP when it is up.
        # Absent/erroring plugin must never break the read panel -> "".
        serve_url = ""
        try:
            svc = kernel.services.get_optional("tailscale-serve")
            if svc and await svc.available():
                st = await svc.serve_state()
                if st.get("on") and st.get("https_ready"):
                    serve_url = str(st.get("url") or "")
        except Exception:
            serve_url = ""
        try:
            return _tailnet_payload(
                await ts.status(),
                await ts.identity(),
                await ts.peers(),
                await ts.funnel_enabled(),
                port=kernel.config.port,
                network_mode=kernel.config.network_mode,
                demo_enabled=kernel.config.demo_enabled,
                serve_url=serve_url,
            )
        except Exception as e:
            return {"available": False, "reason": f"read failed: {e}"}

    @server.get("/api/tailnet/serve")
    async def tailnet_serve_state():
        """Current `tailscale serve` state (always safe to read).

        Returns {available, enabled, on, url, https_ready, ...} when the
        opt-in `tailscale-serve` plugin is loaded, else {available: False,
        reason}. Never raises — the read panel keeps working regardless.
        """
        svc = kernel.services.get_optional("tailscale-serve")
        if not svc:
            return {"available": False, "reason": "tailscale-serve plugin not loaded"}
        try:
            return {"available": await svc.available(), **(await svc.serve_state())}
        except Exception as e:
            return {"available": False, "reason": f"serve probe failed: {e}"}

    @server.post("/api/tailnet/serve/on")
    async def tailnet_serve_on():
        """Turn on tailnet HTTPS (proxy → 127.0.0.1:<daemon port>).

        Reversible/internal (inverse = serve_off), so it's a normal user-gated
        toggle — not autopilot, just a button. Emits an auditable event.
        """
        svc = kernel.services.get_optional("tailscale-serve")
        if not svc:
            return {"ok": False, "error": "tailscale-serve plugin not loaded"}
        try:
            res = await svc.serve_on()
        except Exception as e:
            return {"ok": False, "error": f"serve_on failed: {e}"}
        if res.get("ok"):
            await kernel.events.emit("tailscale:serve_on", {"url": res.get("url", "")}, source="web")
        return res

    @server.post("/api/tailnet/serve/off")
    async def tailnet_serve_off():
        """Turn off tailnet HTTPS — the exact inverse of serve_on."""
        svc = kernel.services.get_optional("tailscale-serve")
        if not svc:
            return {"ok": False, "error": "tailscale-serve plugin not loaded"}
        try:
            res = await svc.serve_off()
        except Exception as e:
            return {"ok": False, "error": f"serve_off failed: {e}"}
        if res.get("ok"):
            await kernel.events.emit("tailscale:serve_off", {}, source="web")
        return res

    @server.get("/api/events")
    async def recent_events(type: str | None = None, limit: int = 50):
        return await kernel.events.history(event_type=type, limit=limit)

    @server.get("/api/syslog")
    async def api_syslog(limit: int = 50, level: str = "", source: str = ""):
        """Quick access to structured system logs."""
        return kernel.syslog.query(limit=limit, level=level, source=source)

    @server.get("/api/jobs")
    async def list_jobs():
        """All active/recent jobs across all apps. Merges:
        - kernel.jobs (apps that register jobs directly)
        - kernel.workers.list_jobs() (WorkerPool — GPU/MV/compose).
        Both live ≤5 min after finish so the banner can show "done".
        """
        import time

        now = time.time()
        out = []
        # Source 1: kernel.jobs registry (existing path).
        for j in kernel.jobs.values():
            if not j.get("finished") or (now - j["finished"]) < 300:
                out.append({**j, "elapsed_s": round(now - j.get("started", now))})
        # Source 2: WorkerPool jobs — normalised to the banner's shape.
        workers = getattr(kernel, "workers", None) or kernel.services.get("workers")
        if workers and hasattr(workers, "list_jobs"):
            for w in workers.list_jobs(limit=50):
                state = w.get("state", "")
                if state in ("completed", "failed", "cancelled"):
                    if not w.get("completed_at") or (now - w["completed_at"]) > 300:
                        continue
                started = w.get("started_at") or w.get("submitted_at") or now
                finished = (
                    w.get("completed_at") if state in ("completed", "failed", "cancelled") else 0
                )
                out.append(
                    {
                        "id": w["id"],
                        "app": w.get("source") or "workers",
                        "label": w.get("name") or w["id"][:8],
                        "phase": "done"
                        if state == "completed"
                        else (state if state != "running" else "running"),
                        "started": started,
                        "finished": finished,
                        "error": w.get("error") or "",
                        "elapsed_s": round(now - started),
                    }
                )
        return out

    @server.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        import time

        job = kernel.jobs.get(job_id)
        if not job:
            return {"error": "not found", "phase": "unknown"}
        return {**job, "elapsed_s": round(time.time() - job.get("started", time.time()))}

    @server.post("/api/jobs/test")
    async def test_job():
        """Fire a demo job to test the banner. Runs 5 seconds."""
        import asyncio
        import time

        job_id = f"test-{int(time.time())}"
        job = {
            "id": job_id,
            "app": "system",
            "label": "Test job",
            "phase": "starting",
            "detail": "",
            "pct": 0,
            "started": time.time(),
            "finished": None,
            "error": None,
        }
        kernel.jobs[job_id] = job
        await kernel.events.emit("job:started", job, source="system")

        async def _run():
            steps = [("Processing", 25), ("Building", 50), ("Rendering", 75), ("Finalizing", 95)]
            for phase, pct in steps:
                await asyncio.sleep(1)
                job["phase"] = phase
                job["pct"] = pct
                await kernel.events.emit("job:progress", {**job}, source="system")
            await asyncio.sleep(1)
            job["phase"] = "done"
            job["pct"] = 100
            job["detail"] = "completed"
            job["finished"] = time.time()
            await kernel.events.emit("job:completed", {**job}, source="system")

        asyncio.create_task(_run())
        return {"job_id": job_id, "status": "started"}

    @server.get("/api/plugins")
    async def list_plugins():
        return [
            {
                "id": m.id,
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "services": m.provides.get("services", []),
                "tags": m.provides.get("tags", []),
                "loaded": m.id in kernel.plugins.instances,
            }
            for m in kernel.plugins.manifests.values()
        ]

    # --- Topology endpoints (extracted to topology.py) ---
    register_topology_routes(server, kernel)

    # --- Vault reconcile/enrich/query (extracted to routes_vault.py) ---
    register_vault_query_routes(server, kernel)

    @server.get("/api/think/usage")
    async def think_usage():
        """Which apps used which LLM providers — from event history."""
        events = await kernel.events.history(event_type="think:executed", limit=200)
        # Aggregate by app + provider
        usage = {}
        for e in events:
            d = e["data"]
            key = f"{d.get('app', '?')}:{d.get('provider', '?')}"
            if key not in usage:
                usage[key] = {
                    "app": d.get("app"),
                    "provider": d.get("provider"),
                    "domain": d.get("domain"),
                    "count": 0,
                    "total_ms": 0,
                }
            usage[key]["count"] += 1
            usage[key]["total_ms"] += d.get("latency_ms", 0)
        result = sorted(usage.values(), key=lambda x: -x["count"])
        for r in result:
            r["avg_ms"] = round(r["total_ms"] / r["count"]) if r["count"] else 0
        return result

    @server.get("/api/scheduler/jobs")
    async def scheduler_jobs():
        if kernel.scheduler:
            return kernel.scheduler.jobs
        return []

    @server.get("/api/realtime/status")
    async def realtime_status():
        return {
            "clients": kernel.realtime.client_count if kernel.realtime else 0,
            "active": kernel.realtime is not None,
        }

    # --- Vault Map + vault file APIs (extracted to routes_vault.py) ---
    register_vault_file_routes(server, kernel)

    # --- Keyboard Shortcuts API (extracted to routes_sdk.py) ---
    register_shortcut_routes(server, kernel)

    # --- CLI proxy endpoint (daemon mode) ---
    @server.post("/api/cli")
    async def cli_proxy(request: Request):
        """Execute an app CLI command via the running daemon.

        CLI clients POST here instead of creating their own kernel.
        Single kernel, shared state.
        """
        data = await request.json()
        app_id = data.get("app", "")
        cmd_name = data.get("command", "")
        args = data.get("args", [])

        if app_id not in kernel.apps.instances:
            if app_id in kernel.apps.manifests and app_id not in kernel.apps.enabled_ids():
                return {
                    "ok": False,
                    "error_code": "app_not_enabled",
                    "error": f"App '{app_id}' is not enabled — install/enable via /store and restart.",
                }
            try:
                await kernel.apps.load(app_id)
            except Exception as e:
                return {
                    "ok": False,
                    "error_code": "app_load_failed",
                    "error": f"Failed to load app '{app_id}': {e}",
                }

        instance = kernel.apps.instances.get(app_id)
        if not instance:
            return {
                "ok": False,
                "error_code": "app_not_found",
                "error": f"App '{app_id}' not found",
            }

        from emptyos.sdk.cli_args import (
            bind_cli_kwargs,
            cli_command_label,
            cli_usage,
            missing_required_args,
            render_cli_return,
            resolve_cli_method,
        )

        # Subcommand-aware resolution: `eos <app> <sub> …` finds a
        # @cli_command("<sub>") method and consumes the sub-verb from args.
        method, cmd_args = resolve_cli_method(
            instance.get_cli_methods(), cmd_name, args
        )
        if not method:
            return {
                "ok": False,
                "error_code": "command_not_found",
                "error": f"Command '{cmd_name}' not found in '{app_id}'",
            }

        kwargs = bind_cli_kwargs(method, cmd_args)

        # A short/bare invocation missing a required arg gets a usage hint
        # instead of a raw `TypeError: ... missing N required positional
        # argument` reaching the caller (the calculator-CLI leak).
        missing = missing_required_args(method, kwargs)
        if missing:
            label = cli_command_label(instance.get_cli_methods(), method, cmd_name)
            return {
                "ok": False,
                "error_code": "missing_args",
                "error": f"Missing required argument(s): {', '.join(missing)}",
                "usage": cli_usage(method, label),
            }

        # Capture print output
        import contextlib
        import io

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                result = method(**kwargs)
                if inspect.isawaitable(result):
                    result = await result

            # A command that returns a value instead of printing still shows it.
            output = buf.getvalue()
            if not output:
                rendered = render_cli_return(result)
                if rendered is not None:
                    output = rendered
            return {"ok": True, "output": output, "error": None}
        except Exception as e:
            return {
                "ok": False,
                "error_code": "command_failed",
                "output": buf.getvalue(),
                "error": str(e),
            }

    # --- WebSocket endpoint ---
    @server.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        # Auth check: when token OR password is set, require either via
        # ?token= query or cookie. Mirrors the HTTP middleware's accept set.
        if _auth_token or _login_password:
            tok = ws.query_params.get("token") or ws.cookies.get("eos_session", "")
            if not _ws_credential_ok(tok, _auth_token, _login_password):
                await ws.close(code=1008, reason="Unauthorized")
                return
        if kernel.realtime:
            await kernel.realtime.handle_connection(ws)
        else:
            await ws.close(code=1013, reason="Realtime service not available")

    # --- Mount app web routes ---
    # Apps already loaded (autostart) get routes mounted immediately.
    # All other apps are lazy-loaded on first web request to their prefix.
    _mount_loaded_app_routes(server, kernel)

    # Track which apps have been lazy-mounted to avoid double-mounting
    _lazy_mounted: set[str] = set()
    for app_id in kernel.apps.instances:
        _lazy_mounted.add(app_id)

    # Prefix→app_id map for pageview tracking (built once at server start)
    _prefix_to_app: dict[str, str] = {}
    for _aid, _m in kernel.apps.manifests.items():
        _p = _m.provides.get("web", {}).get("prefix", "")
        if _p:
            _prefix_to_app[_p] = _aid
    _SKIP_EXTS = (".js", ".css", ".ico", ".png", ".svg", ".woff", ".woff2", ".map", ".json")

    # Alias prefix → canonical prefix. Manifest `aliases` already exist for
    # call_app() lookup (e.g. quick-action exposes alias "capture"). Mirror
    # them on the web so a stray `/capture/api/save` 307s to `/quick-action/api/save`
    # instead of returning a bare 404 — same friction the dogfood persona hit.
    _alias_to_prefix: dict[str, str] = {}
    for _aid, _m in kernel.apps.manifests.items():
        _p = _m.provides.get("web", {}).get("prefix", "")
        if not _p:
            continue
        for _alias in getattr(_m, "aliases", []) or []:
            _alias_path = "/" + _alias.strip("/")
            if _alias_path == _p or _alias_path in _prefix_to_app:
                continue  # don't shadow a real app prefix
            _alias_to_prefix[_alias_path] = _p

    if _alias_to_prefix:
        from fastapi.responses import RedirectResponse as _RedirectResponse

        @server.middleware("http")
        async def _alias_redirect_middleware(request: Request, call_next):
            path = request.url.path
            for _alias_path, _canonical in _alias_to_prefix.items():
                if path == _alias_path or path.startswith(_alias_path + "/"):
                    new_path = _canonical + path[len(_alias_path) :]
                    target = new_path
                    if request.url.query:
                        target = target + "?" + request.url.query
                    # 307 preserves method + body (POST stays POST).
                    return _RedirectResponse(url=target, status_code=307)
            return await call_next(request)

    # --- Per-IP rate limit (extracted to routes_auth.py) ---
    register_rate_limit(server)

    # --- BYOK middleware (extracted to routes_auth.py) ---
    register_byok_middleware(server)

    @server.middleware("http")
    async def _lazy_load_middleware(request: Request, call_next):
        """Lazy-load enabled apps on first request to their web prefix.

        Uninstalled AND disabled apps stay discovered (so the store can
        list them) but never mount routes — requests to their prefix fall
        through to the 404 handler. Restart-required after a `/store`
        install/uninstall/enable/disable.
        """
        path = request.url.path
        enabled = kernel.apps.enabled_ids()
        for app_id, manifest in kernel.apps.manifests.items():
            if app_id in _lazy_mounted:
                continue
            if app_id not in enabled:
                continue
            prefix = manifest.provides.get("web", {}).get("prefix", "")
            if not prefix:
                continue
            if path.startswith(prefix + "/") or path == prefix:
                try:
                    await kernel.apps.load(app_id)
                    _mount_single_app_routes(server, kernel, app_id)
                    # Mark mounted ONLY on success. Marking before the try (the
                    # old behaviour) made a single failed lazy-load permanent —
                    # the app stayed 404 until the next restart with no self-heal.
                    # Now a transient failure retries on the next request.
                    _lazy_mounted.add(app_id)
                except Exception as e:
                    kernel.apps.log_load_failure(app_id, e, source="web", phase="lazy-load")
                break

        # Emit ui:viewed for page loads (not API calls, not assets)
        if request.method == "GET" and "/api/" not in path and not path.endswith(_SKIP_EXTS):
            hit_app = None
            for prefix, aid in _prefix_to_app.items():
                if path == prefix or path.startswith(prefix + "/"):
                    if hit_app is None or len(prefix) > len(hit_app[0]):
                        hit_app = (prefix, aid)
            if hit_app:
                try:
                    await kernel.events.emit(
                        "ui:viewed",
                        {"path": path},
                        source=hit_app[1],
                    )
                except Exception:
                    pass

        return await call_next(request)

    # --- AI-offline exception translator ---
    # Catches `RuntimeError("No available provider...")` raised by `think()`
    # (and other capabilities) and turns it into a structured 503 instead of
    # a stack trace. The front-end re-checks /api/think-status so the banner
    # appears automatically.
    @server.middleware("http")
    async def _ai_offline_handler(request: Request, call_next):
        try:
            return await call_next(request)
        except RuntimeError as e:
            body = _ai_offline_body(e)
            if body is not None:
                return JSONResponse(body, status_code=503)
            raise

    # --- Home redirect + static files ---
    @server.get("/")
    async def home():
        """Home page redirects to the landing route — see `home_target`."""
        return RedirectResponse(url=home_target(kernel), status_code=302)

    # --- Retired app redirects (REGROW consolidations) ---
    @server.get("/net-worth/{path:path}")
    async def redirect_net_worth(path: str = ""):
        return RedirectResponse(url="/finance/", status_code=301)

    @server.get("/retirement/{path:path}")
    async def redirect_retirement(path: str = ""):
        return RedirectResponse(url="/finance/", status_code=301)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        topology_path = static_dir / "topology.html"
        if topology_path.exists():

            @server.get("/topology", response_class=HTMLResponse)
            async def topology_page():
                return topology_path.read_text(encoding="utf-8")

        system_path = static_dir / "system.html"
        if system_path.exists():

            @server.get("/system", response_class=HTMLResponse)
            async def system_page():
                return system_path.read_text(encoding="utf-8")

        console_path = static_dir / "console.html"
        if console_path.exists():

            @server.get("/console", response_class=HTMLResponse)
            async def console_page():
                return console_path.read_text(encoding="utf-8")

        # Deep-link note viewer — /notes?path=<vault-relative> opens the shared
        # EOS_UI.viewNote modal. Makes vault notes URL-addressable (chat links,
        # reminders, cross-app references) without requiring an external viewer.
        notes_path = static_dir / "notes.html"
        if notes_path.exists():

            @server.get("/notes", response_class=HTMLResponse)
            async def notes_page():
                return notes_path.read_text(encoding="utf-8")

        # App-shipped docs. Eight apps carry markdown beside their code —
        # FORGE.md, ALGORITHM.md, RT07-VALIDATION.md, DATABASE-STRUCTURE.md …
        # — and until now none of them rendered anywhere: they were readable
        # in the repo and nowhere else. This is /notes one level up: the doc
        # travels with the app (so a fresh clone and the public snapshot both
        # have it, with no vault dependency), and the platform renders it.
        appdoc_path = static_dir / "appdoc.html"
        if appdoc_path.exists():

            @server.get("/appdoc", response_class=HTMLResponse)
            async def appdoc_page():
                return appdoc_path.read_text(encoding="utf-8")

        @server.get("/api/appdoc")
        async def appdoc_source(app: str = "", file: str = ""):
            """Raw markdown of one doc shipped inside an app directory.

            Constrained deliberately: a known app id, a `.md` file, and a
            path that resolves inside that app's own directory. The id and
            each path segment go through the shared guard, so a caller
            cannot walk out with `..` or a Windows separator.
            """
            from emptyos.sdk.utils import path_segment_error

            if err := path_segment_error(app, "app"):
                return JSONResponse({"error": err}, status_code=400)
            m = kernel.apps.manifests.get(app)
            if not m:
                return JSONResponse({"error": f"App not found: {app}"}, status_code=404)
            rel = (file or "").replace("\\", "/").strip("/")
            if not rel.lower().endswith(".md"):
                return JSONResponse({"error": "only .md documents"}, status_code=400)
            for seg in rel.split("/"):
                if err := path_segment_error(seg, "path"):
                    return JSONResponse({"error": err}, status_code=400)
            target = (m.path / rel).resolve()
            try:
                target.relative_to(m.path.resolve())
            except ValueError:
                return JSONResponse({"error": "outside the app directory"}, status_code=400)
            if not target.is_file():
                return JSONResponse({"error": f"No such document: {rel}"}, status_code=404)
            return PlainTextResponse(
                target.read_text(encoding="utf-8"), media_type="text/markdown"
            )

        favicon_path = static_dir / "favicon.svg"
        if favicon_path.exists():

            @server.get("/favicon.ico")
            async def favicon():
                return FileResponse(str(favicon_path), media_type="image/svg+xml")

        # Service worker must be served at root scope
        sw_path = static_dir / "sw.js"
        if sw_path.exists():

            @server.get("/sw.js")
            async def service_worker():
                return FileResponse(
                    str(sw_path),
                    media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
                )

        # PWA manifest at root with proper Content-Type — iOS Safari prefers this over /static/manifest.json
        manifest_path = static_dir / "manifest.json"
        if manifest_path.exists():

            @server.get("/manifest.webmanifest")
            async def pwa_manifest():
                return FileResponse(
                    str(manifest_path),
                    media_type="application/manifest+json",
                    headers={"Cache-Control": "no-cache"},
                )

        # Offline fallback page — shown by service worker when both network and cache miss
        offline_path = static_dir / "offline.html"
        if offline_path.exists():

            @server.get("/offline.html", response_class=HTMLResponse)
            async def offline_page():
                return HTMLResponse(offline_path.read_text(encoding="utf-8"))

        server.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        # Dev-friendly: no aggressive caching for JS/CSS (hot-reload friendly)
        from starlette.middleware.base import BaseHTTPMiddleware

        class NoCacheStaticMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                response = await call_next(request)
                # Both /static/* and app /pages/* assets must revalidate, so a
                # JS/CSS edit takes effect on next load without a hard refresh.
                # Without this, app-page JS (e.g. /hub/pages/hub.js, served by a
                # StaticFiles mount that only sets an etag) gets heuristically
                # cached and a code change silently renders stale.
                p = request.url.path
                if (p.startswith("/static/") or "/pages/" in p) and p.endswith(
                    (".js", ".css")
                ):
                    response.headers["Cache-Control"] = "no-cache, must-revalidate"
                return response

        server.add_middleware(NoCacheStaticMiddleware)

    return server


_THEME_BOOTSTRAP_TAG = (
    # Pre-paint theme class, so a themed page never flashes unstyled.
    #
    # This deliberately does NOT carry the theme allowlist. It used to, and the
    # list was duplicated into every page that ships its own bootstrap — so
    # adding a theme meant editing 20+ files, and *missing one* was silently
    # destructive: that page's snippet rewrote localStorage to 'eos', resetting
    # the user's choice globally the moment they visited it.
    #
    # Now it only shape-validates (a syntactically safe class suffix) and never
    # writes. `_setThemeClass` in eos.js owns the real registry and does the
    # authoritative self-heal a few ms later, so an unknown id costs one
    # unstyled pre-paint frame instead of a wiped preference.
    "<script>try{"
    "var _t=localStorage.getItem('eos-theme')||'eos';"
    "if(!/^[a-z][a-z0-9-]{0,31}$/.test(_t))_t='eos';"
    "document.documentElement.classList.add('theme-'+_t);"
    "}catch(e){document.documentElement.classList.add('theme-eos');}</script>"
)

# Runtime UI-translation loader. Injected into every served page <head> so it
# loads even when the browser is running a stale cached eos.js (the eos.js
# loader is the redundant fallback). eos-i18n.js self-gates — a complete no-op
# when ui.language=en — and is idempotent via window.__eosI18nLoaded.
# Pre-paint embed mark: a page iframed by a host shell (portal's app pane opens
# /<id>/?embed=1) gets html.eos-embed before CSS resolves, so theme.css's
# --eos-nav-h is 0 from the first frame instead of after eos.js runs -- which,
# on pages that load eos.js at the end of <body>, was a visible 46px jump.
# Same class name the pages that self-mark already use (daily-brief, ...).
_EMBED_BOOTSTRAP_TAG = (
    "<script>try{if(new URLSearchParams(location.search).get('embed')==='1')"
    "document.documentElement.classList.add('eos-embed')}catch(e){}</script>"
)

_I18N_BOOTSTRAP_TAG = '<script src="/static/eos-i18n.js" defer></script>'
# Deliberately NOT deferred (unlike eos-i18n.js above). It must wrap window.fetch
# before any page script runs: a deferred script executes only after parsing, so a
# page that fetches during parse — the normal "load my data on open" path — would
# issue its request against the unwrapped fetch and never get a chip. The runtime
# touches no DOM at execution time; its observer self-defers on readyState.
_PROVENANCE_TAG = '<script src="/static/eos-provenance.js"></script>'


async def _think_status_body(cap) -> dict:
    """Which think providers can answer right now. A provider the monthly spend
    cap has stopped does not count — otherwise the page would re-enable AI after
    every "limit reached" reply and the learner would hit it on each click."""
    available, capped = [], False
    for p in cap.providers:
        try:
            if not await p.available():
                continue
        except Exception:
            continue
        spend_cap = getattr(cap, "spend_cap", None)
        if spend_cap is not None and spend_cap.blocks("think", p):
            capped = True
            continue
        available.append({"name": p.name, "is_cloud": getattr(p, "is_cloud", False)})

    if available:
        return {"available": True, "providers": available}
    if capped:
        return {"available": False, "reason": "spend_cap",
                "message": "This month's AI limit is reached."}
    return {"available": False, "reason": "no think provider is currently available"}


def _same_site_path(value) -> str | None:
    """A configured link target, kept only if it is a path on this site.

    Same rules as a login ``next`` (``safe_next``); a refused value hides the
    link instead of rendering it.
    """
    return safe_next(value if isinstance(value, str) else "", default="") or None


def _account_links(config_get, has_session_cookie: bool) -> dict:
    """The account menu's two optional links, for /api/health.

    ``network.sign_out_url`` / ``network.account_url`` name a proxy's own
    endpoints (the EnglishOS control plane owns its learners' sessions).
    Without a configured sign-out, a valid browser session cookie is the
    daemon's own, ended by POST /logout; with neither there is nothing to
    sign out of (local mode, or a bearer-only API caller).
    """
    sign_out = _same_site_path(config_get("network.sign_out_url", ""))
    if not sign_out and has_session_cookie:
        sign_out = "/logout"
    return {
        "manage_url": _same_site_path(config_get("network.account_url", "")),
        "sign_out_url": sign_out,
    }


def _ai_offline_body(e: RuntimeError) -> dict | None:
    """The 503 body for a capability that had no provider to run, or None
    when `e` is some other error (which then propagates unchanged)."""
    msg = str(e)
    if "No available provider for capability" not in msg and "simulate offline" not in msg:
        return None
    cap = "think"
    if "capability '" in msg:
        try:
            cap = msg.split("capability '", 1)[1].split("'", 1)[0]
        except Exception:
            pass
    from emptyos.capabilities.note_scope import NOTES_SETTING_LABEL, NotesToCloudOff
    from emptyos.capabilities.spend_cap import SpendCapReached

    body = {
        "error": "ai_offline" if cap == "think" else "capability_offline",
        "capability": cap,
        "message": (
            "AI is offline. The feature you clicked needs a think provider — "
            "it will return when one is available."
        ),
    }
    if isinstance(e, SpendCapReached):
        # Same code, so the offline banner still shows, but say why: this does
        # not come back when a provider does.
        body["reason"] = "spend_cap"
        body["message"] = "This month's AI limit is reached. Features that need AI resume next month."
    elif getattr(e, "keep_local", False):
        # speech_guard kept the text on the device and no on-device voice
        # could speak it.
        from emptyos.capabilities.speech_guard import learner_message

        body["reason"] = "speech_local_only"
        body["message"] = learner_message(getattr(e, "code", ""))
    elif isinstance(e, NotesToCloudOff):
        # Not offline at all: this feature needs the learner's permission.
        body["reason"] = "notes_opt_in"
        body["message"] = (
            f"AI features in this app can read your notes, so they are off until "
            f"you turn on '{NOTES_SETTING_LABEL}' in Settings."
        )
    return body


HOME_FALLBACK = "/hub/"
HOME_PORTAL = "/portal/"


def home_target(kernel) -> str:
    """The route ``GET /`` redirects to.

    Precedence: the Settings store ``os.home`` (the Home page field on
    /settings) → TOML ``[os] home`` → ``/portal/`` when the portal app is
    loaded → ``/hub/``. Portal became the landing surface on 2026-10-03 — it
    renders the hub's lanes and panel contributions under its composer — and
    hub stays the fallback for a build that does not ship portal (hub is an
    ESSENTIAL app, portal is standard-tier).

    A configured value is normalised to a leading slash, then must pass
    ``safe_next`` — a path on THIS daemon. ``os.home`` is a declared setting,
    writable from /settings, so ``//evil.example`` (protocol-relative: it
    starts with ``/``) or ``/\\evil.example`` would otherwise make ``GET /``
    an open redirect. A refused value, ``""``, ``None`` and anything that
    resolves to ``/`` itself (which would redirect to itself forever — ``/?x``,
    ``#x``, ``/./``, ``/%2e/``) all fall through to the next source.

    In user posture (public demo, hosted learner) the stored setting is ignored:
    it is the operator's choice for every visitor, and the settings app refuses
    the write there too. The settings service is optional (absent in some test
    kernels).
    """
    candidates = []
    settings = kernel.services.get_optional("settings")
    if settings is not None and getattr(kernel.config, "web_is_operator", True):
        try:
            candidates.append(settings.get("os.home"))
        except Exception:
            pass
    candidates.append(kernel.config.get("os.home"))
    for raw in candidates:
        target = str(raw or "").strip()
        if target and not target.startswith("/"):
            target = "/" + target
        target = safe_next(target, default="")
        if target and not _lands_on_root(target):
            return target
    try:
        portal_loaded = kernel.apps.instances.get("portal") is not None
    except Exception:
        portal_loaded = False
    return HOME_PORTAL if portal_loaded else HOME_FALLBACK


def _lands_on_root(target: str) -> bool:
    """Does a same-site redirect target resolve to ``/`` itself?

    Compares the PATH the browser will request, after percent-decoding and
    dot-segment removal — a query (``/?x``) or fragment (``/#x``) does not
    change the path, and ``/./`` or ``/%2e/`` normalise to ``/``.
    """
    path = unquote(urlsplit(target).path) or "/"
    return posixpath.normpath(path) == "/"


def _auto_provenance_enabled(kernel) -> bool:
    """Resolve the restart-free ``ui.auto_provenance`` dark flag.

    A ``None`` from the settings store means *unset*, not *false*: ``POST
    /settings/api/reset`` clears a key by writing an explicit ``null`` rather
    than deleting it, so a sentinel that only tests for absence would let one
    reset permanently shadow the ``[ui]`` TOML value and the
    ``EOS_UI_AUTO_PROVENANCE`` env override.
    """
    from emptyos.sdk.utils import is_truthy

    settings = kernel.services.get_optional("settings")
    if settings is not None:
        value = settings.get("ui.auto_provenance")
        if value is not None:
            return is_truthy(value)
    return is_truthy(kernel.config.get("ui.auto_provenance", False))


def _tailnet_payload(
    summary: dict,
    identity: dict,
    peers: list,
    funnel_enabled: bool,
    *,
    port: int,
    network_mode: str,
    demo_enabled: bool,
    serve_url: str = "",
) -> dict:
    """Build the ``/api/tailnet`` success payload from raw service reads.

    Pure (no I/O) so the redaction + derived-field logic is unit-testable.

    - ``phone_url`` / ``magic_dns`` — derived conveniences so the frontend need
      not recompute. Rule 17: the daemon port comes from the caller's config,
      never hardcoded.
    - ``serve_url`` — the `tailscale serve` HTTPS origin when that opt-in plugin
      has it up, else "". **When present it IS ``phone_url``**, because the
      tailnet-IP fallback is plain ``http://`` on a non-loopback host, which is
      not a secure context: a phone pointed at it can register no service worker
      and install no PWA, so EmptyOS degrades to a browser tab the user must
      re-navigate to. ``phone_url_secure`` reports which of the two it is, so
      the panel can say *why* rather than silently handing out the weaker URL.
    - ``mode_nudge`` — True when the tailnet is up but the daemon listens on
      loopback only (``network.mode == "local"``), so the tailnet IP isn't
      actually reachable yet. The panel turns this into a *propose-not-autofill*
      nudge; it never writes config.
    - Runtime redaction — in a public/demo snapshot the owner's hostname,
      tailnet IP, login, and peer devices are personal. ``.eos-personal`` is a
      release-time *source* scan and cannot touch a live API response, so the
      redaction gate must live here (keyed on ``demo.enabled``).
    """
    summary = summary or {}
    self_ip = summary.get("self_ip", "")
    self_dns = summary.get("self_dns", "")
    serve_url = (serve_url or "").strip()
    if demo_enabled:
        # Keep only coarse, non-identifying counts.
        summary = {
            "backend_state": summary.get("backend_state", "?"),
            "peer_count": summary.get("peer_count", 0),
            "online_peer_count": summary.get("online_peer_count", 0),
        }
        identity, peers, self_ip, self_dns = {}, [], "", ""
        serve_url = ""
    ip_url = f"http://{self_ip}:{port}/" if self_ip else ""
    return {
        "available": True,
        "status": summary,
        "identity": identity,
        "peers": peers,
        "funnel_enabled": funnel_enabled,
        "phone_url": serve_url or ip_url,
        "phone_url_secure": bool(serve_url),
        "serve_url": serve_url,
        "magic_dns": self_dns,
        "mode_nudge": network_mode == "local",
    }


def _inject_theme_bootstrap(html: str, *, auto_provenance: bool = False) -> str:
    """Inject theme/i18n bootstraps and the optional provenance runtime.

    Ensures the theme class is set on <html> before CSS resolves.

    Apps that forget the inline `theme-X` className applier end up with every
    `var(--bg-card)` / `var(--border)` / `var(--accent)` resolving to nothing
    (see `feedback_app_page_theme_bootstrap` in operator memory). Inject the
    bootstrap script right after the opening `<head>` if the page doesn't
    already contain `eos-theme` (idempotent — doubled-script-tag pages don't
    grow further).
    """
    if not html:
        return html
    # Match the opening <head> tag (with optional attrs); insert immediately after.
    import re as _re

    m = _re.search(r"<head(\s[^>]*)?>", html, _re.IGNORECASE)
    if not m:
        return html  # malformed page; leave alone
    # Theme + runtime loaders are injected independently (each only if absent), so a
    # page that already self-injects the theme bootstrap still gets the i18n
    # loader, and vice versa. Both are idempotent on the client.
    extra = ""
    # Pages that self-mark (daily-brief, …) carry this exact call already.
    if "classList.add('eos-embed')" not in html:
        extra += _EMBED_BOOTSTRAP_TAG
    if "eos-theme" not in html:
        extra += _THEME_BOOTSTRAP_TAG
    if "eos-i18n.js" not in html:
        extra += _I18N_BOOTSTRAP_TAG
    if auto_provenance and "eos-provenance.js" not in html:
        extra += _PROVENANCE_TAG
    if not extra:
        return html
    insert_at = m.end()
    return html[:insert_at] + extra + html[insert_at:]


def _inject_asset_versions(html: str, prefix: str, pages_dir) -> str:
    """Append ``?v=<mtime>`` to this app's own ``/pages/*.js|css`` asset tags.

    App-page JS/CSS hot-reloads from disk, but a PWA service worker (or an HTTP
    heuristic cache) can keep serving a stale copy after the file changes — the
    cached URL never changes, so the client never re-fetches and renders against
    old code (e.g. the hub launcher rendering empty cards after hub.js changed).
    Stamping the referenced file's mtime onto the URL makes every edit a new
    URL, so the cache misses and refetches automatically — no manual version
    bumping in the markup.

    Only the app's own ``pages/`` assets are stamped; shared ``/static/*``
    bundles are network-first in the service worker and tied to its CACHE_NAME.
    Idempotent: URLs that already carry a query string are left alone, and a
    missing file is left unstamped.
    """
    if not html or not pages_dir:
        return html
    import re as _re
    from pathlib import Path as _P

    pfx = (prefix or "").rstrip("/")
    if not pfx:
        return html
    marker = pfx + "/pages/"

    def _stamp(m):
        attr, url = m.group(1), m.group(2)
        rel = url[len(marker):]  # regex guarantees url starts with marker
        try:
            mtime = int((_P(pages_dir) / rel).stat().st_mtime)
        except OSError:
            return m.group(0)
        return f'{attr}="{url}?v={mtime}"'

    pattern = _re.compile(
        r'(src|href)="(' + _re.escape(pfx) + r'/pages/[^"?]+\.(?:js|css))"',
        _re.IGNORECASE,
    )
    return pattern.sub(_stamp, html)


# Platform pixel->source locator (groundwork). When an app page is requested
# with ?debug=locate, stamp each editable element with data-eos-src="<rel>:<line>"
# and load the read-only locator overlay. Query-gated → zero cost on normal
# serves. Parse result cached by file mtime. See eos-debug-locate.js + the
# designer element-edit loop (apps/public/standard/designer/editing.py) that
# shares the same emptyos.sdk.html_anchors parser.
_LOCATE_CACHE: dict[str, tuple[float, str]] = {}
_LOCATE_TAG = '<script src="/static/eos-debug-locate.js"></script>'


def _locate_rel_path(path) -> str:
    """Repo-relative forward-slash path for a served page file, for display."""
    from pathlib import Path as _P

    parts = _P(path).parts
    for anchor in ("apps", "plugins", "emptyos"):
        if anchor in parts:
            return "/".join(parts[parts.index(anchor):])
    return _P(path).name


def _inject_locate(html: str, path, overlay: bool = True) -> str:
    """Stamp data-eos-src provenance (mtime-cached); optionally inject the
    human overlay.

    ``overlay=False`` is the stamp-only mode for automated consumers (UI-walk
    audits, dogfood walk) — it adds the `data-eos-src` attributes but NOT the
    visible badge/click overlay. The overlay is a position:fixed element that
    would otherwise pollute screenshots and get flagged by the click-intercept
    audit, so headless harnesses request `?debug=locate&overlay=0`. The cache
    stores the stamped-only HTML (the expensive parse); the overlay tag is
    appended per-request."""
    from emptyos.sdk.html_anchors import inject_src

    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = _LOCATE_CACHE.get(key)
    if cached and cached[0] == mtime:
        stamped = cached[1]
    else:
        stamped = inject_src(html, _locate_rel_path(path))
        _LOCATE_CACHE[key] = (mtime, stamped)
    if not overlay:
        return stamped
    if "</body>" in stamped:
        return stamped.replace("</body>", _LOCATE_TAG + "</body>", 1)
    return stamped + _LOCATE_TAG


def _inject_full_screen_marker(html: str) -> str:
    """Mark <html> with `eos-full-screen` so eos.js can opt out of injecting
    the FAB dock + capability dots for apps that declare `[app] full_screen
    = true` in their manifest (focus timers, voice assistant, tour, etc.)."""
    import re as _re

    m = _re.search(r"<html(\s[^>]*)?>", html, _re.IGNORECASE)
    if not m:
        return html
    attrs = m.group(1) or ""
    # If existing class= attribute is present, append to it; else add fresh.
    cls_m = _re.search(r'class=(["\'])([^"\']*)\1', attrs, _re.IGNORECASE)
    if cls_m:
        if "eos-full-screen" in cls_m.group(2):
            return html  # already marked
        new_attrs = (
            attrs[: cls_m.start()]
            + "class="
            + cls_m.group(1)
            + cls_m.group(2)
            + " eos-full-screen"
            + cls_m.group(1)
            + attrs[cls_m.end() :]
        )
    else:
        new_attrs = attrs + ' class="eos-full-screen"'
    return html[: m.start()] + "<html" + new_attrs + ">" + html[m.end() :]


def _route_specificity(path: str) -> tuple:
    """Sort key for route registration order. Lower tuple = registered first.
    Fewer {params} win; among equal param count, more segments win.
    e.g. /api/projects/{id}/docs (1 param, 4 seg) before /api/projects/{id} (1 param, 3 seg)
    """
    parts = [p for p in path.split("/") if p]
    param_count = sum(1 for p in parts if "{" in p)
    return (param_count, -len(parts))


def _mount_loaded_app_routes(server: FastAPI, kernel):
    """Mount web routes for all currently-loaded apps."""
    for app_id in list(kernel.apps.instances.keys()):
        _mount_single_app_routes(server, kernel, app_id)


def _mount_single_app_routes(server: FastAPI, kernel, app_id: str):
    """Mount web routes for a single loaded app."""
    instance = kernel.apps.instances.get(app_id)
    manifest = kernel.apps.manifests.get(app_id)
    if not instance or not manifest:
        return

    web_section = manifest.provides.get("web", {})
    prefix = web_section.get("prefix", "")
    if not prefix:
        return

    # External FastAPI sub-mount — manifest [provides.web] mount_external =
    # "pkg.mod:app" turns an EmptyOS app into a thin wrapper around an
    # existing FastAPI app (e.g. a standalone tool we host inside the
    # daemon without rewriting its routes). The sub-app's full route
    # surface lands under this app's prefix. Wrapper's own @web_route
    # methods + pages/ still take priority — they're registered first
    # below; the sub-app catches everything that falls through. We skip
    # the auto-UI + catch-all paths for external-mounted apps so they
    # don't shadow the sub-app's own routing.
    mount_external = web_section.get("mount_external")

    # Mount @web_route decorated methods
    # Sort: more specific routes first (more segments, fewer {params}) to avoid
    # greedy {id} capturing sub-paths like /api/projects/{id} eating /api/projects/{id}/docs
    web_methods = list(instance.get_web_methods())
    web_methods.sort(key=lambda pair: _route_specificity(pair[0]["path"]))
    _web_is_operator = kernel.config.web_is_operator
    for meta, method in web_methods:
        http_method = meta["method"].upper()
        route_path = prefix + meta["path"]
        # An operator-only app route (settings network/product/autopilot, store
        # install/marketplace, kb open-local/source-pdf, …) is refused in user
        # posture. See docs/AUTH.md § Operator vs user.
        operator_only = bool(meta.get("operator", False)) and not _web_is_operator
        _add_route(server, http_method, route_path, method, app_id, operator_only=operator_only)

    # Mount @ws_route decorated WebSocket endpoints
    for meta, method in instance.get_ws_methods():
        ws_path = prefix + meta["path"]
        _add_ws_route(server, ws_path, method, app_id, kernel)

    # Mount app pages: custom pages/ directory, or auto-generated UI
    # Track page source for deep-path catch-all registration
    _catchall_path = None  # Path object → read from disk (hot-reload)
    _catchall_html = None  # str → cached HTML (template/auto-gen)

    pages_dir = manifest.path / "pages"
    if pages_dir.exists():
        # Serve index.html at the prefix root — read from disk each request (hot-reload)
        index_file = pages_dir / "index.html"
        if index_file.exists():
            _page_path = index_file
            _catchall_path = index_file

            _full_screen = bool(manifest.raw.get("app", {}).get("full_screen", False))

            async def _custom_page(
                request: Request,
                p=_page_path,
                full_screen=_full_screen,
                pfx=prefix,
                pdir=pages_dir,
            ):
                raw = p.read_text(encoding="utf-8")
                if request.query_params.get("debug") == "locate":
                    # overlay=0 → stamp-only (automated harnesses); default → human overlay.
                    raw = _inject_locate(raw, p, overlay=request.query_params.get("overlay") != "0")
                html = _inject_theme_bootstrap(
                    raw, auto_provenance=_auto_provenance_enabled(kernel)
                )
                # Auto cache-bust this app's own pages/ JS+CSS by file mtime, so a
                # service-worker / HTTP cache can't pin a stale bundle after an edit.
                html = _inject_asset_versions(html, pfx, pdir)
                if full_screen:
                    html = _inject_full_screen_marker(html)
                # no-cache: pages hot-reload from disk; without this, browsers
                # heuristically cache (no validators) and serve stale UIs.
                return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

            _custom_page.__name__ = f"{app_id}_custom_page"
            server.get(f"{prefix}/")(_custom_page)

        # Also serve all static files under /pages/ for additional assets
        server.mount(
            f"{prefix}/pages",
            StaticFiles(directory=str(pages_dir), html=True),
            name=f"{app_id}_pages",
        )
    elif mount_external:
        # External-mount wrappers skip auto-UI — the sub-app's own routing
        # fills the prefix surface (its StaticFiles mount usually serves
        # index.html at /). Wrapper's @web_route + pages/ above still win
        # because they're registered before the mount below.
        pass
    else:
        # Check for template declaration in manifest
        web_config = manifest.provides.get("web", {})
        template_name = web_config.get("template")
        template_config = web_config.get("template_config", {})

        if template_name:
            # Serve template with injected config
            from emptyos.web.templates_engine import serve_template

            _catchall_html = serve_template(
                server,
                prefix,
                app_id,
                manifest,
                template_name,
                template_config,
                return_html=True,
            )
        else:
            # Auto-generate UI from manifest + routes
            from emptyos.web.auto_ui import generate_app_page

            routes = [meta for meta, _ in instance.get_web_methods()]
            auto_html = generate_app_page(manifest, routes)
            _catchall_html = auto_html

            async def _auto_page(html=auto_html):
                return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

            _auto_page.__name__ = f"{app_id}_auto_page"
            server.get(f"{prefix}/")(_auto_page)

    # External FastAPI sub-mount — register LAST so wrapper's @web_route
    # methods and pages/ assets (registered above) take priority. The
    # sub-app handles everything that falls through.
    if mount_external:
        try:
            module_path, _, attr = mount_external.partition(":")
            attr = attr or "app"
            import importlib

            mod = importlib.import_module(module_path)
            sub_app = getattr(mod, attr)
            server.mount(prefix, sub_app, name=f"{app_id}_external")
            import logging

            logging.getLogger("emptyos.web").info(
                "[%s] mounted external app '%s' at %s",
                app_id,
                mount_external,
                prefix,
            )
        except Exception as e:
            import logging

            logging.getLogger("emptyos.web").error(
                "[%s] failed to mount_external %r: %s",
                app_id,
                mount_external,
                e,
            )
        return  # external-mount wins; skip catch-all

    # --- Deep-path catch-all: serve index for any unmatched sub-path ---
    # Registered LAST so API routes, WebSocket routes, and StaticFiles all take priority.
    # Enables client-side routing (pushState) without 404 on browser refresh.
    if _catchall_path or _catchall_html:

        async def _catchall(request: Request, path: str, p=_catchall_path, html=_catchall_html, _aid=app_id, _pfx=prefix, _pdir=pages_dir):
            # Non-GET requests to an unmatched sub-path must 404, not 405.
            # A GET-only catch-all made Starlette report wrong-verb / unknown
            # POSTs as 405 (Method Not Allowed) — which misleads callers into
            # thinking the path is real. Unknown /api/* (any method) is also a
            # 404, never the SPA shell, so non-browser callers don't see a wrong
            # verb as silent success.
            if request.method != "GET" or path == "api" or path.startswith("api/"):
                # Trailing-slash variant of a real endpoint (e.g. GET
                # /app/api/studies/ when the route is /app/api/studies):
                # this {path:path} catch-all otherwise preempts Starlette's
                # slash-redirect, turning a one-char slip into a 404. Redirect
                # to the de-slashed path (307 preserves method + body).
                full = request.url.path
                if full.endswith("/") and _route_exists(request, full.rstrip("/")):
                    return RedirectResponse(full.rstrip("/"), status_code=307)
                body = {"error": "no such endpoint", "app": _aid, "path": "/" + path}
                # Help the next caller: list this app's real /api endpoints and
                # suggest the closest match, so a wrong guess costs a hint, not
                # a turn. (Several dogfood personas asked for exactly this.)
                hint = _endpoint_hint(request, _pfx, request.url.path)
                if hint:
                    body.update(hint)
                return JSONResponse(body, status_code=404)
            # Parity with _custom_page: the deep-route SPA shell gets the same
            # theme bootstrap + page-asset cache-bust as the prefix-root page,
            # so a hard-loaded deep route renders themed and against fresh JS.
            shell = _inject_theme_bootstrap(
                p.read_text(encoding="utf-8") if p else html,
                auto_provenance=_auto_provenance_enabled(kernel),
            )
            shell = _inject_asset_versions(shell, _pfx, _pdir)
            return HTMLResponse(shell)

        _catchall.__name__ = f"{app_id}_catchall"
        server.api_route(
            f"{prefix}/{{path:path}}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        )(_catchall)


def _route_exists(request: Request, full_path: str) -> bool:
    """True when an exact (non-catch-all) route is registered at full_path."""
    try:
        for r in request.app.routes:
            rp = getattr(r, "path", "")
            if isinstance(rp, str) and rp == full_path and "{path:path}" not in rp:
                return True
    except Exception:
        pass
    return False


def _endpoint_hint(request: Request, prefix: str, req_path: str) -> dict:
    """Build a 'did you mean' hint for an unmatched /api path under an app
    prefix: the app's real /api endpoints plus the closest match to the
    requested path. Returns {} on any introspection failure (never raises)."""
    try:
        import difflib

        seen: list[str] = []
        for r in request.app.routes:
            rp = getattr(r, "path", "")
            if not isinstance(rp, str) or not rp.startswith(prefix + "/api"):
                continue
            if "{path:path}" in rp:  # skip the catch-all itself
                continue
            for m in sorted(getattr(r, "methods", None) or []):
                if m in ("HEAD", "OPTIONS"):
                    continue
                seen.append(f"{m} {rp}")
        if not seen:
            return {}
        seen = sorted(set(seen))
        paths = sorted({s.split(" ", 1)[1] for s in seen})
        close = difflib.get_close_matches(req_path, paths, n=1, cutoff=0.4)
        hint: dict = {"available": seen[:25]}
        if close:
            hint["did_you_mean"] = close[0]
        return hint
    except Exception:
        return {}


def _add_route(server: FastAPI, http_method: str, path: str, method, app_id: str,
               *, operator_only: bool = False):
    """Add a single app method as a FastAPI route.

    ``operator_only`` (resolved by the caller from the route's ``operator=True``
    flag AND the daemon's posture) refuses the call with a 403 before the app
    method runs — the app-route half of the operator/user policy.
    """

    # The app method signature is: method(self, request) for web routes
    # We wrap it to handle the FastAPI request object
    async def route_handler(request: Request):
        if operator_only:
            return JSONResponse(
                {"error": "not available in this edition", "app": app_id},
                status_code=403,
            )
        try:
            result = method(request)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict) or isinstance(result, list):
                return JSONResponse(result)
            return result
        except (SystemExit, KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as e:
            return JSONResponse({"error": str(e), "app": app_id}, status_code=500)

    route_handler.__name__ = f"{app_id}_{method.__name__}"
    server.api_route(path, methods=[http_method])(route_handler)


def _ws_credential_ok(token: str, auth_token: str, login_password: str) -> bool:
    """True when a WebSocket-handshake token matches a configured credential.

    Shared by the core /ws endpoint and every app WebSocket (_add_ws_route).
    Browsers can't set an Authorization header on a WS handshake, so auth is
    via ?token= or the eos_session cookie; either is checked against the
    machine token or the human password with a constant-time compare.
    """
    import hmac

    return bool(token) and (
        (bool(auth_token) and hmac.compare_digest(token, auth_token))
        or (bool(login_password) and hmac.compare_digest(token, login_password))
    )


def _add_ws_route(server: FastAPI, path: str, method, app_id: str, kernel):
    """Add an app WebSocket method as a FastAPI WebSocket route.

    Handles path parameters by using Starlette's WebSocket.path_params
    which is populated automatically by the router.

    Auth: when a token or password is configured, the socket is owner-gated
    (cookie or ?token=) before accept(), mirroring the core /ws endpoint. No
    app declares a public WebSocket today, so default-deny is safe; a public
    kiosk socket would need an explicit opt-in here. Local mode (no creds) is
    open, unchanged.
    """
    from starlette.routing import WebSocketRoute
    from starlette.websockets import WebSocket as _SWS

    async def ws_endpoint(websocket: _SWS):
        _tok = kernel.config.auth_token
        _pwd = kernel.config.login_password
        if _tok or _pwd:
            t = websocket.query_params.get("token") or websocket.cookies.get(
                "eos_session",
                "",
            )
            if not _ws_credential_ok(t, _tok, _pwd):
                await websocket.close(code=1008, reason="Unauthorized")
                return
        await websocket.accept()
        try:
            result = method(websocket)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    # Use Starlette WebSocketRoute directly — handles path params automatically
    route = WebSocketRoute(path, ws_endpoint, name=f"{app_id}_{method.__name__}_ws")
    server.routes.append(route)
