"""FastAPI web server — dashboard + app route mounting."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


def create_server(kernel: Kernel) -> FastAPI:
    """Create the FastAPI app with kernel context."""
    from emptyos.kernel.app_loader import AppState

    server = FastAPI(title="EmptyOS", version="0.1.0")

    # --- Presentation-mode response scrubber ---
    # When settings.presentation.enabled is true, rewrite JSON responses by
    # replacing matches of `.eos-personal` regex patterns with "***". Frontend
    # pairs this with a `html.eos-redact` CSS class on annotated elements.
    # Both layers compose: CSS misses unannotated apps; regex misses
    # non-string-shaped leaks like a vault path embedded in a status field.
    import json as _json

    from starlette.middleware.base import BaseHTTPMiddleware as _BHM
    from starlette.responses import Response as _R

    from emptyos.sdk.personal_patterns import load as _load_personal_patterns

    _PRESENT_PATTERNS_FILE = Path(kernel.config.path).parent / ".eos-personal"
    _PRESENT_PATH_EXEMPT = ("/static/", "/api/presentation/", "/login")
    _PRESENT_REGEX_CACHE = {"patterns": None}

    def _present_patterns():
        if _PRESENT_REGEX_CACHE["patterns"] is None:
            _PRESENT_REGEX_CACHE["patterns"] = _load_personal_patterns(
                _PRESENT_PATTERNS_FILE
            )
        return _PRESENT_REGEX_CACHE["patterns"]

    def _scrub_value(v, patterns):
        if isinstance(v, str):
            out = v
            for pat in patterns:
                out = pat.sub("***", out)
            return out
        if isinstance(v, list):
            return [_scrub_value(x, patterns) for x in v]
        if isinstance(v, dict):
            return {k: _scrub_value(x, patterns) for k, x in v.items()}
        return v

    class PresentationMiddleware(_BHM):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            try:
                active = bool(kernel.settings.get("presentation.enabled", False))
            except Exception:
                active = False
            if not active:
                return response
            path = request.url.path
            if any(path.startswith(p) for p in _PRESENT_PATH_EXEMPT):
                return response
            ctype = (response.headers.get("content-type") or "").lower()
            if "application/json" not in ctype:
                return response
            patterns = _present_patterns()
            if not patterns:
                return response
            # Once body_iterator is consumed we MUST return a new Response,
            # never the original — Starlette can't replay the stream.
            body = b""
            try:
                async for chunk in response.body_iterator:
                    body += chunk
            except Exception:
                return _R(
                    content=b'{"error":"presentation scrub failed"}',
                    status_code=500,
                    media_type="application/json",
                )
            new_body = body
            try:
                if body:
                    data = _json.loads(body.decode("utf-8"))
                    scrubbed = _scrub_value(data, patterns)
                    new_body = _json.dumps(scrubbed, default=str).encode("utf-8")
            except Exception:
                # Non-JSON body (or parse error) — pass through unchanged.
                new_body = body
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return _R(
                content=new_body,
                status_code=response.status_code,
                headers=headers,
                media_type="application/json",
            )

    server.add_middleware(PresentationMiddleware)

    # --- Auth middleware (activates only when network.auth_token is set) ---
    _auth_token: str = kernel.config.auth_token
    if _auth_token:
        import hmac

        from starlette.middleware.base import BaseHTTPMiddleware

        _AUTH_COOKIE = "eos_session"
        # Paths that bypass auth — login page, static assets, favicon, service worker, PWA manifest, offline page
        _AUTH_EXEMPT_PREFIXES = ["/static/", "/login"]
        _AUTH_EXEMPT_PATHS = {
            "/favicon.ico",
            "/sw.js",
            "/manifest.webmanifest",
            "/offline.html",
            "/api/health",
        }

        # Apps may publish a "public face" via [provides.web].public_routes.
        # Each entry is appended to the app's prefix and added to the bypass
        # set. Lets a single app (e.g. /radio/live) be reachable without a
        # token while the rest of the daemon stays gated. The auth boundary
        # is the network gate; per-app code is still responsible for filtering
        # what content a public caller can read.
        for _m in kernel.apps.manifests.values():
            _web = (_m.provides or {}).get("web", {}) or {}
            _prefix = _web.get("prefix", "")
            for _r in _web.get("public_routes", []) or []:
                if not isinstance(_r, str) or not _r:
                    continue
                _full = _prefix + _r if _r.startswith("/") else _prefix + "/" + _r
                _AUTH_EXEMPT_PREFIXES.append(_full)
        _AUTH_EXEMPT_PREFIXES = tuple(_AUTH_EXEMPT_PREFIXES)

        def _check_token(provided: str) -> bool:
            return bool(provided) and hmac.compare_digest(provided, _auth_token)

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path
                # Exempt login, static, favicon, health
                if path in _AUTH_EXEMPT_PATHS:
                    return await call_next(request)
                if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
                    return await call_next(request)

                # Check bearer token (API clients, CLI)
                auth_header = request.headers.get("authorization", "")
                if auth_header.lower().startswith("bearer "):
                    if _check_token(auth_header[7:].strip()):
                        return await call_next(request)

                # Check session cookie (browser)
                cookie_tok = request.cookies.get(_AUTH_COOKIE, "")
                if _check_token(cookie_tok):
                    return await call_next(request)

                # Check ?token= query param (deep-link sign-in for landing pages).
                # On match, set the cookie and 302 to the same path with token
                # stripped from the URL — so it never lingers in the address bar
                # or browser history beyond the first hop.
                qtok = request.query_params.get("token", "")
                if qtok and _check_token(qtok):
                    clean_qs = "&".join(
                        f"{k}={v}" for k, v in request.query_params.multi_items() if k != "token"
                    )
                    clean_url = path + (("?" + clean_qs) if clean_qs else "")
                    resp = RedirectResponse(url=clean_url, status_code=302)
                    resp.set_cookie(
                        _AUTH_COOKIE,
                        _auth_token,
                        httponly=True,
                        samesite="lax",
                        max_age=60 * 60 * 24 * 30,
                    )
                    return resp

                # API returns 401 JSON, browser redirects to login
                accept = request.headers.get("accept", "")
                if path.startswith("/api/") or "application/json" in accept:
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
                return RedirectResponse(url=f"/login?next={path}", status_code=302)

        server.add_middleware(AuthMiddleware)

        # Login page — GET shows form, POST sets cookie
        _LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>EmptyOS — Login</title>
<style>
body{font-family:system-ui,sans-serif;background:#0e1117;color:#e6edf3;margin:0;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:24px;box-sizing:border-box}
.box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:32px;width:320px}
h1{margin:0 0 8px;font-size:18px;font-weight:600}
p{margin:0 0 16px;color:#8b949e;font-size:13px}
input{width:100%;box-sizing:border-box;padding:8px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-family:inherit;font-size:13px}
button{width:100%;margin-top:12px;padding:8px;background:#238636;border:0;border-radius:6px;color:#fff;font-weight:600;cursor:pointer}
button:hover{background:#2ea043}
.err{color:#f85149;font-size:12px;margin-top:8px}
.foot{margin-top:24px;font-size:12px;color:#6e7681;text-align:center;line-height:1.6}
.foot a{color:#8b949e;text-decoration:none;border-bottom:1px dotted #30363d}
.foot a:hover{color:#e6edf3;border-bottom-color:#8b949e}
.foot .sep{margin:0 6px;color:#30363d}
</style></head><body>
<form class="box" method="post" action="/login">
<h1>EmptyOS</h1><p>Enter your access token to continue.</p>
<input type="password" name="token" placeholder="Access token" autofocus required>
<input type="hidden" name="next" value="__NEXT__">
<button type="submit">Sign in</button>
__ERR__
</form>
<div class="foot">
EmptyOS — a mind companion. Think and create with you, not for you.<br>
<a href="https://eos.binbian.net" target="_blank" rel="noopener">About</a>
<span class="sep">·</span>
<a href="https://github.com/KevinBean/emptyos" target="_blank" rel="noopener">Source</a>
<span class="sep">·</span>
<a href="https://eos.binbian.net/getting-started.html" target="_blank" rel="noopener">Self-host</a>
<span class="sep">·</span>
<a href="https://binbian.net" target="_blank" rel="noopener">Blog</a>
</div>
</body></html>"""

        @server.get("/login", response_class=HTMLResponse)
        async def login_page(request: Request):
            import html as _html

            raw_next = request.query_params.get("next", "/") or "/"
            # Clamp next to local paths only — prevents the form from POSTing
            # the credential to an external host. Mirrors login_submit's check.
            if not raw_next.startswith("/") or raw_next.startswith("//"):
                raw_next = "/"
            next_url = _html.escape(raw_next, quote=True)
            err = _html.escape(request.query_params.get("err", "") or "", quote=True)
            err_html = f'<div class="err">{err}</div>' if err else ""
            page = _LOGIN_HTML.replace("__NEXT__", next_url).replace("__ERR__", err_html)
            return HTMLResponse(page)

        @server.post("/login")
        async def login_submit(request: Request):
            form = await request.form()
            provided = str(form.get("token", ""))
            next_url = str(form.get("next", "/")) or "/"
            # Basic next-URL safety: must be a local path
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = "/"
            if not _check_token(provided):
                return RedirectResponse(
                    url=f"/login?next={next_url}&err=Invalid+token",
                    status_code=302,
                )
            resp = RedirectResponse(url=next_url, status_code=302)
            resp.set_cookie(
                _AUTH_COOKIE,
                _auth_token,
                httponly=True,
                samesite="lax",
                max_age=60 * 60 * 24 * 30,
            )
            return resp

        @server.get("/logout")
        async def logout():
            resp = RedirectResponse(url="/login", status_code=302)
            resp.delete_cookie(_AUTH_COOKIE)
            return resp

    @server.get("/api/health")
    async def health(full: bool = False):
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
            "vault_name": vault_name,
            "vault_path": str(vault).replace("\\", "/") if vault else "",
            "apps": n_apps,
        }
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
            state = kernel.apps.states.get(m.id, AppState.DISCOVERED).value
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

        # Integrity audit
        integrity_app = kernel.apps.instances.get("integrity")
        if integrity_app and hasattr(integrity_app, "_run_audit"):
            try:
                audit = integrity_app._run_audit()
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

    # --- Presentation mode (runtime privacy toggle) ---
    # Different from demo.enabled: this is a flick-of-a-switch view-layer redact
    # for "I'm showing the running daemon to a friend, hide my data". No restart,
    # no data wipe. Two layers of hiding (frontend blur + backend regex scrub)
    # that compose with the existing eos-redact CSS conventions used by ppt
    # embed slides.

    @server.get("/api/presentation/state")
    async def presentation_state():
        return {"enabled": bool(kernel.settings.get("presentation.enabled", False))}

    @server.post("/api/presentation/toggle")
    async def presentation_toggle():
        cur = bool(kernel.settings.get("presentation.enabled", False))
        new = not cur
        kernel.settings.set("presentation.enabled", new)
        try:
            await kernel.events.emit(
                "presentation:changed", {"enabled": new}, source="web"
            )
        except Exception:
            pass
        return {"enabled": new}

    @server.post("/api/presentation/set")
    async def presentation_set(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        new = bool(body.get("enabled", False))
        kernel.settings.set("presentation.enabled", new)
        try:
            await kernel.events.emit(
                "presentation:changed", {"enabled": new}, source="web"
            )
        except Exception:
            pass
        return {"enabled": new}

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

        available = []
        for p in cap.providers:
            try:
                if await p.available():
                    available.append({"name": p.name, "is_cloud": getattr(p, "is_cloud", False)})
            except Exception:
                continue

        if not available:
            return {"available": False, "reason": "no think provider is currently available"}
        return {"available": True, "providers": available}

    # --- Cloud consent endpoints ---
    @server.get("/api/cloud/status")
    async def cloud_status():
        cm = getattr(kernel, "cloud_consent", None)
        if cm is None:
            return {"enabled": False}
        return {"enabled": True, **cm.status()}

    @server.get("/api/cloud/pending")
    async def cloud_pending():
        cm = getattr(kernel, "cloud_consent", None)
        if cm is None:
            return {"pending": []}
        return {"pending": cm.pending_list()}

    @server.post("/api/cloud/consent")
    async def cloud_consent_submit(request: Request):
        cm = getattr(kernel, "cloud_consent", None)
        if cm is None:
            return JSONResponse({"error": "consent manager not available"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)
        req_id = str(body.get("id", "")).strip()
        approved = bool(body.get("approved", False))
        remember = bool(body.get("remember", True))
        if not req_id:
            return JSONResponse({"error": "missing id"}, status_code=400)
        ok = cm.approve(req_id, remember=remember) if approved else cm.deny(req_id)
        if not ok:
            return JSONResponse({"error": "request not found"}, status_code=404)
        return {"ok": True, "approved": approved}

    @server.post("/api/cloud/approve")
    async def cloud_approve_provider(request: Request):
        """Pre-approve a cloud provider by name for the current session.

        Used by surfaces like Model Bench that silently skip un-approved
        cloud providers — one click approves, then the user can re-run.
        """
        cm = getattr(kernel, "cloud_consent", None)
        if cm is None:
            return JSONResponse({"error": "consent manager not available"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)
        provider = str(body.get("provider", "")).strip()
        if not provider:
            return JSONResponse({"error": "missing provider"}, status_code=400)
        cm.approve_provider(provider)
        return {"ok": True, "provider": provider, "approved": sorted(cm._session_approved)}

    @server.post("/api/cloud/policy")
    async def cloud_policy_set(request: Request):
        """Update the consent policy (ask/always/never) at runtime.

        Persists to data/settings.json under "cloud.consent" so the policy
        survives daemon restart. emptyos.toml stays read-only.
        """
        cm = getattr(kernel, "cloud_consent", None)
        if cm is None:
            return JSONResponse({"error": "consent manager not available"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)
        policy = str(body.get("policy", "")).lower().strip()
        if policy not in ("ask", "always", "never"):
            return JSONResponse({"error": "policy must be ask | always | never"}, status_code=400)
        cm.set_policy(policy)
        kernel.settings.set("cloud.consent", policy)
        return {"ok": True, "policy": cm.policy}

    @server.get("/api/cloud/llm-scan")
    async def cloud_llm_scan_get():
        """Read current LLM-scan settings + list of local think providers."""
        settings = getattr(kernel, "settings", None)
        cfg = {
            "mode": "off",
            "on_flag": "warn",
            "provider": "",
            "max_chars": 4000,
            "timeout": 5.0,
        }
        if settings is not None:
            cfg["mode"] = settings.get("cloud.llm_scan.mode", "off") or "off"
            cfg["on_flag"] = settings.get("cloud.llm_scan.on_flag", "warn") or "warn"
            cfg["provider"] = settings.get("cloud.llm_scan.provider", "") or ""
            try:
                cfg["max_chars"] = int(settings.get("cloud.llm_scan.max_chars", 4000) or 4000)
            except (TypeError, ValueError):
                cfg["max_chars"] = 4000
            try:
                cfg["timeout"] = float(settings.get("cloud.llm_scan.timeout", 5.0) or 5.0)
            except (TypeError, ValueError):
                cfg["timeout"] = 5.0
        # Also surface local providers so the UI can offer a picker
        locals_list = []
        try:
            think = kernel.capabilities.get("think")
            for p in think.providers:
                if getattr(p, "is_cloud", False) or p.name == "human":
                    continue
                locals_list.append(
                    {
                        "variant_id": p.variant_id,
                        "name": p.name,
                        "model": getattr(p, "model", "") or "",
                    }
                )
        except Exception:
            pass
        return {"config": cfg, "local_providers": locals_list}

    @server.post("/api/cloud/llm-scan")
    async def cloud_llm_scan_set(request: Request):
        """Update LLM-scan settings. Body: {mode, on_flag, provider, max_chars}."""
        settings = getattr(kernel, "settings", None)
        if settings is None:
            return JSONResponse({"error": "settings service not available"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)
        if "mode" in body:
            mode = str(body["mode"]).lower().strip()
            if mode not in ("off", "classify", "redact"):
                return JSONResponse(
                    {"error": "mode must be off | classify | redact"}, status_code=400
                )
            settings.set("cloud.llm_scan.mode", mode)
        if "on_flag" in body:
            on_flag = str(body["on_flag"]).lower().strip()
            if on_flag not in ("warn", "block"):
                return JSONResponse({"error": "on_flag must be warn | block"}, status_code=400)
            settings.set("cloud.llm_scan.on_flag", on_flag)
        if "provider" in body:
            settings.set("cloud.llm_scan.provider", str(body["provider"] or "").strip())
        if "max_chars" in body:
            try:
                mc = int(body["max_chars"])
                if mc < 100 or mc > 20000:
                    return JSONResponse({"error": "max_chars must be 100..20000"}, status_code=400)
                settings.set("cloud.llm_scan.max_chars", mc)
            except (TypeError, ValueError):
                return JSONResponse({"error": "max_chars must be an integer"}, status_code=400)
        if "timeout" in body:
            try:
                to = float(body["timeout"])
                if to < 0.5 or to > 120:
                    return JSONResponse(
                        {"error": "timeout must be 0.5..120 seconds"}, status_code=400
                    )
                settings.set("cloud.llm_scan.timeout", to)
            except (TypeError, ValueError):
                return JSONResponse({"error": "timeout must be a number"}, status_code=400)
        return {"ok": True}

    @server.post("/api/health/gpu/free")
    async def health_gpu_free():
        hp = kernel.services.get_optional("health")
        if hp and hasattr(hp, "gpu_free"):
            return await hp.gpu_free()
        return {"error": "health plugin not available"}

    @server.get("/api/apps")
    async def list_apps():
        return [
            {
                "id": m.id,
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "state": kernel.apps.states.get(m.id, AppState.DISCOVERED).value,
                "web_prefix": m.provides.get("web", {}).get("prefix", ""),
                "cli_commands": m.provides.get("cli", {}).get("commands", []),
            }
            for m in kernel.apps.manifests.values()
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
            (
                {"app_id": aid, **t}
                for aid, t in timings.items()
            ),
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
        from emptyos.web.clustering import get_clusters

        return get_clusters(kernel.apps.manifests)

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
            "state": kernel.apps.states.get(m.id, AppState.DISCOVERED).value,
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
            return FileResponse(
                str(result),
                media_type="text/html",
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
            net_mode = kernel.config.network.mode
        except Exception:
            net_mode = "local"
        return {"capabilities": out, "consent": consent, "network_mode": net_mode}

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

    @server.get("/api/topology")
    async def topology():
        """Return the full system dependency graph as nodes + edges."""
        return _build_topology(kernel)

    @server.get("/api/topology/layers")
    async def topology_layers():
        """Layered architecture analysis — dependency depth, cycles, critical path."""
        return _analyze_layers(kernel)

    @server.get("/api/topology/timeline")
    async def topology_timeline():
        """Per-node creation date for the timeline scrubber.

        Order: manifest `created` override → git first-add date → mtime.
        Cached at data/apps/topology/dates.json keyed by HEAD.
        """
        return _topology_timeline(kernel)

    @server.get("/api/topology/tree")
    async def topology_tree():
        """Capability-rooted tree view: 9 capabilities → providers + consuming apps."""
        return _build_topology_tree(kernel)

    @server.get("/api/topology/releases")
    async def topology_releases():
        """Git-tag release markers for the timeline slider."""
        return _topology_releases(kernel)

    @server.get("/api/topology/node/{node_id:path}")
    async def topology_node(node_id: str):
        """Subgraph centered on a node — all direct and 2nd-degree connections.

        Returns the focused node, its neighbors, their neighbors, and all
        edges between them. Useful for understanding one app's dependencies.
        """
        topo = _build_topology(kernel)
        nodes_map = {n["id"]: n for n in topo["nodes"]}
        if node_id not in nodes_map:
            # Try prefixed: app:dashboard, cap:think, etc.
            for prefix in ("app:", "cap:", "plugin:", "service:", "engine:", "data:", "event:"):
                if prefix + node_id in nodes_map:
                    node_id = prefix + node_id
                    break
            else:
                return {"error": f"Node '{node_id}' not found"}

        # Collect 1st and 2nd degree neighbors
        degree1 = {node_id}
        for e in topo["edges"]:
            if e["source"] == node_id:
                degree1.add(e["target"])
            elif e["target"] == node_id:
                degree1.add(e["source"])

        degree2 = set(degree1)
        for e in topo["edges"]:
            if e["source"] in degree1:
                degree2.add(e["target"])
            elif e["target"] in degree1:
                degree2.add(e["source"])

        # Filter nodes and edges
        focused_nodes = [n for n in topo["nodes"] if n["id"] in degree2]
        focused_edges = [
            e for e in topo["edges"] if e["source"] in degree2 and e["target"] in degree2
        ]

        # Annotate distance from focus
        for n in focused_nodes:
            if n["id"] == node_id:
                n["_degree"] = 0
            elif n["id"] in degree1:
                n["_degree"] = 1
            else:
                n["_degree"] = 2

        return {
            "focus": node_id,
            "focus_label": nodes_map[node_id]["label"],
            "focus_type": nodes_map[node_id]["type"],
            "nodes": focused_nodes,
            "edges": focused_edges,
            "stats": {
                "total_nodes": len(focused_nodes),
                "total_edges": len(focused_edges),
                "degree1": sum(1 for n in focused_nodes if n.get("_degree") == 1),
                "degree2": sum(1 for n in focused_nodes if n.get("_degree") == 2),
            },
        }

    @server.get("/api/topology/improvements")
    async def topology_improvements():
        """Actionable improvement recommendations from topology + integrity analysis.

        Returns prioritized list of concrete fixes with file paths and commands.
        Designed to be consumed by Claude Code for automated improvement cycles.
        """
        return await _compute_improvements(kernel)

    @server.get("/api/vault/reconcile")
    async def vault_reconcile(folder: str = "", tags: str = "", fields: str = ""):
        """Check vault notes against expected data structure. Read-only."""
        vi = kernel.services.get("vault_index")
        if not vi:
            return {"error": "VaultIndex not available"}
        if not folder:
            return {"error": "folder parameter required (e.g. ?folder=30_Resources/Books)"}
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
        return vi.reconcile(folder, tag_list, field_list)

    @server.post("/api/vault/enrich")
    async def vault_enrich(request: Request):
        """Add missing tags/defaults to vault notes. Only adds, never overwrites."""
        vi = kernel.services.get("vault_index")
        if not vi:
            return {"error": "VaultIndex not available"}
        data = await request.json()
        paths = data.get("paths", [])
        add_tags = data.get("tags", [])
        defaults = data.get("defaults", {})
        if not paths:
            return {"error": "paths required"}
        modified = 0
        for path in paths:
            if vi.enrich(path, add_tags or None, defaults or None):
                modified += 1
        return {"ok": True, "modified": modified, "total": len(paths)}

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

    # --- Vault Map API ---
    @server.get("/api/vault-map")
    async def vault_map_get():
        return kernel.vault_map.all()

    @server.post("/api/vault-map")
    async def vault_map_set(request: Request):
        data = await request.json()
        app_id = data.get("app", "")
        key = data.get("key", "")
        value = data.get("value", "")
        if not app_id or not key:
            return JSONResponse({"error": "app and key required"}, status_code=400)
        kernel.vault_map.set(app_id, key, value)
        return {"ok": True, "app": app_id, "key": key, "value": value}

    @server.post("/api/vault-map/rescan")
    async def vault_map_rescan():
        """Rescan vault structure, heal broken paths, detect new patterns."""
        changes = kernel.vault_map.rescan()
        return {"changes": changes, "map": kernel.vault_map.all()}

    # --- Vault file API (read/write any vault note) ---
    @server.get("/api/vault/read")
    async def vault_read(path: str):
        """Read a vault file by relative or absolute path."""
        vault = kernel.config.notes_path
        if not vault:
            return JSONResponse({"error": "No vault configured"}, status_code=500)
        full = Path(path) if Path(path).is_absolute() else vault / path
        if not full.exists():
            return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
        try:
            content = full.read_text(encoding="utf-8")
            rel = (
                str(full.relative_to(vault)).replace("\\", "/")
                if str(full).startswith(str(vault))
                else path.replace("\\", "/")
            )
            return {"path": str(full).replace("\\", "/"), "relative": rel, "content": content}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @server.post("/api/vault/write")
    async def vault_write(request: Request):
        """Write/update a vault file."""
        data = await request.json()
        file_path = data.get("path", "")
        content = data.get("content", "")
        if not file_path:
            return JSONResponse({"error": "path is required"}, status_code=400)
        vault = kernel.config.notes_path
        if not vault:
            return JSONResponse({"error": "No vault configured"}, status_code=500)
        full = Path(file_path) if Path(file_path).is_absolute() else vault / file_path
        # Safety: must be inside vault
        try:
            full.resolve().relative_to(vault.resolve())
        except ValueError:
            return JSONResponse({"error": "Path outside vault"}, status_code=403)
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            await kernel.events.emit("vault:edited", {"path": str(full)}, source="web")
            return {"ok": True, "path": str(full)}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # --- Keyboard Shortcuts API ---

    # Default shortcuts — overridable via settings key "shortcuts.go_map"
    DEFAULT_GO_MAP = {
        "h": {"path": "/", "label": "Home"},
        "t": {"path": "/task/", "label": "Tasks"},
        "j": {"path": "/journal/", "label": "Journal"},
        "e": {"path": "/expense/", "label": "Expense"},
        "s": {"path": "/search/", "label": "Search"},
        "a": {"path": "/assistant/", "label": "Assistant"},
        "b": {"path": "/briefing/", "label": "Briefing"},
        "d": {"path": "/hub/", "label": "Dashboard"},
        "n": {"path": "/nutrition/", "label": "Nutrition"},
        "p": {"path": "/projects/", "label": "Projects"},
        "c": {"path": "/contacts/", "label": "Contacts"},
        "i": {"path": "/items/", "label": "Items"},
        "l": {"path": "/healing/", "label": "Healing"},
        "m": {"path": "/media/", "label": "Media"},
        "r": {"path": "/reader/", "label": "Reader"},
        "k": {"path": "/tracker/", "label": "Tracker"},
        "v": {"path": "/app-analytics/#vault", "label": "Vault Analytics"},
        "x": {"path": "/english/", "label": "English"},
        "w": {"path": "/briefing/#review", "label": "Review"},
        "q": {"path": "/quotes/", "label": "Quotes"},
        "f": {"path": "/focus/", "label": "Focus"},
        "y": {"path": "/briefing/#digest", "label": "Digest"},
        "o": {"path": "/podcast/", "label": "Podcast"},
        "u": {"path": "/console", "label": "Console"},
        "z": {"path": "/hub/#pinnedSection", "label": "Pinned Refs"},
    }

    DEFAULT_GLOBAL_SHORTCUTS = [
        {"key": "Ctrl+K", "action": "palette", "label": "Command Palette"},
        {"key": "Ctrl+/", "action": "help", "label": "Show Shortcuts"},
        {"key": "Ctrl+Shift+P", "action": "presentation", "label": "Toggle Presentation Mode"},
        {"key": "?", "action": "help", "label": "Show Shortcuts"},
        {"key": "/", "action": "focus-search", "label": "Focus Search"},
        {"key": "Esc", "action": "close", "label": "Close Overlay"},
    ]

    # Non-app routes that may appear in shortcut paths (kept regardless of tier)
    NON_APP_ROUTES = {"", "console", "topology", "settings", "docs", "ws"}

    def _filter_go_map_to_loaded(go_map: dict) -> dict:
        """Drop go-to entries whose target app isn't loaded in this tier."""
        loaded = set(kernel.apps.manifests.keys())
        out = {}
        for key, entry in go_map.items():
            path = (entry or {}).get("path", "") or "/"
            first = path.lstrip("/").split("/", 1)[0].split("#", 1)[0]
            if first in NON_APP_ROUTES or first in loaded:
                out[key] = entry
        return out

    @server.get("/api/shortcuts")
    async def get_shortcuts():
        """Get all keyboard shortcuts (go-to map + globals). Respects settings overrides."""
        settings = kernel.services.get_optional("settings")
        go_map = dict(DEFAULT_GO_MAP)
        if settings:
            custom = settings.get("shortcuts.go_map")
            if isinstance(custom, dict):
                go_map.update(custom)
        go_map = _filter_go_map_to_loaded(go_map)

        # Collect per-app shortcuts from manifests
        app_shortcuts = []
        for app_id, manifest in kernel.apps.manifests.items():
            app_keys = manifest.provides.get("shortcuts", [])
            if app_keys:
                for s in app_keys:
                    app_shortcuts.append({**s, "app": app_id})

        return {
            "go_map": go_map,
            "global": DEFAULT_GLOBAL_SHORTCUTS,
            "app_shortcuts": app_shortcuts,
        }

    @server.post("/api/shortcuts")
    async def set_shortcuts(request: Request):
        """Override go-to shortcuts via settings."""
        data = await request.json()
        settings = kernel.services.get_optional("settings")
        if not settings:
            return JSONResponse({"error": "Settings service not available"}, status_code=500)
        go_map = data.get("go_map")
        if go_map and isinstance(go_map, dict):
            settings.set("shortcuts.go_map", go_map)
        return {"ok": True, "go_map": go_map}

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
            try:
                await kernel.apps.load(app_id)
            except Exception as e:
                return {"error": f"Failed to load app '{app_id}': {e}"}

        instance = kernel.apps.instances.get(app_id)
        if not instance:
            return {"error": f"App '{app_id}' not found"}

        method = None
        for meta, m in instance.get_cli_methods():
            if meta["name"] == cmd_name:
                method = m
                break
        if not method:
            return {"error": f"Command '{cmd_name}' not found in '{app_id}'"}

        # Capture print output
        import contextlib
        import inspect as _inspect
        import io

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                # Parse args same way as CLI
                sig = _inspect.signature(method)
                params = [p for p in sig.parameters.values() if p.name != "self"]
                kwargs = {}
                for i, param in enumerate(params):
                    if i < len(args):
                        val = args[i]
                        ann = param.annotation
                        if ann == int or ann is int:
                            val = int(val)
                        kwargs[param.name] = val
                    elif param.default is not _inspect.Parameter.empty:
                        kwargs[param.name] = param.default

                result = method(**kwargs)
                if _inspect.isawaitable(result):
                    await result

            return {"output": buf.getvalue(), "error": None}
        except Exception as e:
            return {"output": buf.getvalue(), "error": str(e)}

    # --- WebSocket endpoint ---
    @server.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        # Auth check: when auth_token is set, require it via ?token= query or cookie
        if _auth_token:
            import hmac

            tok = ws.query_params.get("token") or ws.cookies.get("eos_session", "")
            if not (tok and hmac.compare_digest(tok, _auth_token)):
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

    # --- Per-IP rate limit ---
    # Defense-in-depth alongside Cloudflare/Caddy. Sliding 10-second window.
    # Threshold is configurable via EOS_RATE_LIMIT_PER_10S env var; 0 disables.
    # On hits, returns HTTP 429 with a small JSON body. Excludes /static/ and
    # /ws so streaming + asset loads don't trip it.
    import collections as _collections
    import os as _os
    import time as _time

    _RATE_LIMIT = int(_os.environ.get("EOS_RATE_LIMIT_PER_10S", "0") or 0)
    _rate_buckets: dict[str, _collections.deque[float]] = {}

    def _client_ip(request: Request) -> str:
        # Caddy/Cloudflare set X-Forwarded-For; fall back to direct client.
        xff = (request.headers.get("x-forwarded-for") or "").split(",")
        ip = (
            xff[0].strip()
            if xff and xff[0].strip()
            else (request.client.host if request.client else "unknown")
        )
        return ip

    if _RATE_LIMIT > 0:

        @server.middleware("http")
        async def _rate_limit_middleware(request: Request, call_next):
            path = request.url.path
            # Skip static + websocket — these are bursty by nature
            if path.startswith("/static/") or path.startswith("/ws"):
                return await call_next(request)
            ip = _client_ip(request)
            now = _time.time()
            cutoff = now - 10.0
            bucket = _rate_buckets.setdefault(ip, _collections.deque(maxlen=_RATE_LIMIT * 2))
            # Drop expired entries from the left
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= _RATE_LIMIT:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    {"error": "rate limited", "retry_after_s": 10},
                    status_code=429,
                    headers={"Retry-After": "10"},
                )
            bucket.append(now)
            return await call_next(request)

    # --- BYOK middleware ---
    # Visitors can paste their own OpenAI/Anthropic key in Settings; the
    # frontend sends it as X-User-{Provider}-Key on every request. We stash
    # it in a per-request contextvar so providers can prefer it over the
    # server's env-var key. Per-request scope: one visitor's key never
    # bleeds into another visitor's request (contextvars are bound to the
    # asyncio task handling the request).
    @server.middleware("http")
    async def _byok_middleware(request: Request, call_next):
        from emptyos.capabilities.byok import HEADER_MAP, reset_byok_keys, set_byok_keys

        keys: dict[str, str] = {}
        for header_name, key_name in HEADER_MAP.items():
            val = (request.headers.get(header_name) or "").strip()
            if val:
                keys[key_name] = val
        if not keys:
            return await call_next(request)
        token = set_byok_keys(keys)
        try:
            return await call_next(request)
        finally:
            reset_byok_keys(token)

    @server.middleware("http")
    async def _lazy_load_middleware(request: Request, call_next):
        """Lazy-load apps on first request to their web prefix."""
        path = request.url.path
        for app_id, manifest in kernel.apps.manifests.items():
            if app_id in _lazy_mounted:
                continue
            prefix = manifest.provides.get("web", {}).get("prefix", "")
            if not prefix:
                continue
            if path.startswith(prefix + "/") or path == prefix:
                _lazy_mounted.add(app_id)
                try:
                    await kernel.apps.load(app_id)
                    _mount_single_app_routes(server, kernel, app_id)
                except Exception as e:
                    print(f"[Web] Lazy-load failed for '{app_id}': {e}")
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
            msg = str(e)
            if "No available provider for capability" in msg or "simulate offline" in msg:
                cap = "think"
                if "capability '" in msg:
                    try:
                        cap = msg.split("capability '", 1)[1].split("'", 1)[0]
                    except Exception:
                        pass
                return JSONResponse(
                    {
                        "error": "ai_offline" if cap == "think" else "capability_offline",
                        "capability": cap,
                        "message": (
                            "AI is offline. The feature you clicked needs a think provider — "
                            "it will return when one is available."
                        ),
                    },
                    status_code=503,
                )
            raise

    # --- Home redirect + static files ---
    @server.get("/")
    async def home():
        """Home page redirects to hub app — the real dashboard."""
        return RedirectResponse(url="/hub/", status_code=302)

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
                if request.url.path.startswith("/static/") and request.url.path.endswith(
                    (".js", ".css")
                ):
                    response.headers["Cache-Control"] = "no-cache, must-revalidate"
                return response

        server.add_middleware(NoCacheStaticMiddleware)

    return server


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

    # Mount @web_route decorated methods
    # Sort: more specific routes first (more segments, fewer {params}) to avoid
    # greedy {id} capturing sub-paths like /api/projects/{id} eating /api/projects/{id}/docs
    web_methods = list(instance.get_web_methods())
    web_methods.sort(key=lambda pair: _route_specificity(pair[0]["path"]))
    for meta, method in web_methods:
        http_method = meta["method"].upper()
        route_path = prefix + meta["path"]
        _add_route(server, http_method, route_path, method, app_id)

    # Mount @ws_route decorated WebSocket endpoints
    for meta, method in instance.get_ws_methods():
        ws_path = prefix + meta["path"]
        _add_ws_route(server, ws_path, method, app_id)

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

            async def _custom_page(p=_page_path):
                return HTMLResponse(p.read_text(encoding="utf-8"))

            _custom_page.__name__ = f"{app_id}_custom_page"
            server.get(f"{prefix}/")(_custom_page)

        # Also serve all static files under /pages/ for additional assets
        server.mount(
            f"{prefix}/pages",
            StaticFiles(directory=str(pages_dir), html=True),
            name=f"{app_id}_pages",
        )
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
                return HTMLResponse(html)

            _auto_page.__name__ = f"{app_id}_auto_page"
            server.get(f"{prefix}/")(_auto_page)

    # --- Deep-path catch-all: serve index for any unmatched sub-path ---
    # Registered LAST so API routes, WebSocket routes, and StaticFiles all take priority.
    # Enables client-side routing (pushState) without 404 on browser refresh.
    if _catchall_path or _catchall_html:

        async def _catchall(path: str, p=_catchall_path, html=_catchall_html, _aid=app_id):
            # Unknown /api/* must 404, not return the SPA shell — otherwise
            # wrong-verb hits look like silent success to non-browser callers.
            if path == "api" or path.startswith("api/"):
                return JSONResponse(
                    {"error": "no such endpoint", "app": _aid, "path": "/" + path},
                    status_code=404,
                )
            return HTMLResponse(p.read_text(encoding="utf-8") if p else html)

        _catchall.__name__ = f"{app_id}_catchall"
        server.get(f"{prefix}/{{path:path}}")(_catchall)


def _add_route(server: FastAPI, http_method: str, path: str, method, app_id: str):
    """Add a single app method as a FastAPI route."""

    # The app method signature is: method(self, request) for web routes
    # We wrap it to handle the FastAPI request object
    async def route_handler(request: Request):
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


def _add_ws_route(server: FastAPI, path: str, method, app_id: str):
    """Add an app WebSocket method as a FastAPI WebSocket route.

    Handles path parameters by using Starlette's WebSocket.path_params
    which is populated automatically by the router.
    """
    from starlette.routing import WebSocketRoute
    from starlette.websockets import WebSocket as _SWS

    async def ws_endpoint(websocket: _SWS):
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


def _build_topology(kernel) -> dict:
    """Build the full system graph: nodes and edges.

    Node types: app, plugin, capability, provider, event, engine, data
    Edge types: uses_capability, uses_service, provides_service,
                emits_event, listens_event, has_provider, uses_engine,
                reads_data, writes_data
    """
    from emptyos.kernel.app_loader import AppState

    nodes = []
    edges = []
    node_ids = set()

    def add_node(nid: str, ntype: str, label: str, **extra):
        if nid not in node_ids:
            node_ids.add(nid)
            nodes.append({"id": nid, "type": ntype, "label": label, **extra})

    # --- Capabilities + Providers ---
    for cap_name, cap in kernel.capabilities.list().items():
        add_node(f"cap:{cap_name}", "capability", cap_name)
        for provider in cap.providers:
            pid = f"prov:{cap_name}:{provider.name}"
            add_node(pid, "provider", provider.name)
            edges.append(
                {
                    "source": f"cap:{cap_name}",
                    "target": pid,
                    "type": "has_provider",
                }
            )

    # --- Plugins ---
    for plugin_id, manifest in kernel.plugins.manifests.items():
        loaded = plugin_id in kernel.plugins.instances
        add_node(
            f"plugin:{plugin_id}",
            "plugin",
            manifest.name,
            loaded=loaded,
            description=manifest.description,
        )
        for svc in manifest.provides.get("services", []):
            add_node(f"service:{svc}", "service", svc)
            edges.append(
                {
                    "source": f"plugin:{plugin_id}",
                    "target": f"service:{svc}",
                    "type": "provides_service",
                }
            )

    # --- Engines ---
    for engine_id, manifest in kernel.engines.manifests.items():
        loaded = engine_id in kernel.engines.instances
        add_node(
            f"engine:{engine_id}",
            "engine",
            manifest.name,
            loaded=loaded,
            description=manifest.description,
        )
        # Engines may depend on capabilities
        for cap in manifest.requires.get("capabilities", []):
            edges.append(
                {
                    "source": f"engine:{engine_id}",
                    "target": f"cap:{cap}",
                    "type": "uses_capability",
                }
            )

    # --- Apps ---
    for app_id, manifest in kernel.apps.manifests.items():
        state = kernel.apps.states.get(app_id, AppState.DISCOVERED).value
        add_node(
            f"app:{app_id}", "app", manifest.name, state=state, description=manifest.description
        )

        requires = manifest.requires

        # Capability edges
        for cap in requires.get("capabilities", []):
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": f"cap:{cap}",
                    "type": "uses_capability",
                }
            )

        # Service edges
        for svc in requires.get("services", []):
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": f"service:{svc}",
                    "type": "uses_service",
                }
            )

        # Event listen edges — manifest-declared listens plus live @on_event
        # decorators on the running instance. Mixin-heavy apps (reactor)
        # register handlers in code without duplicating them in the manifest;
        # reading from the instance keeps the graph honest.
        listen_events = set(requires.get("events", []))
        listen_events.update(manifest.provides.get("events", {}).get("listens", []))
        instance = kernel.apps.instances.get(app_id)
        if instance is not None:
            try:
                for meta, _ in instance._get_decorated("_eos_event"):
                    listen_events.add(meta["type"])
            except Exception:
                pass
        for evt in listen_events:
            add_node(f"event:{evt}", "event", evt)
            edges.append(
                {
                    "source": f"event:{evt}",
                    "target": f"app:{app_id}",
                    "type": "listens_event",
                }
            )

        # Event emit edges
        for evt in manifest.provides.get("events", {}).get("emits", []):
            add_node(f"event:{evt}", "event", evt)
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": f"event:{evt}",
                    "type": "emits_event",
                }
            )

        # Engine edges
        for eng in requires.get("engines", []):
            eng_nid = f"engine:{eng}"
            if eng_nid not in node_ids:
                add_node(eng_nid, "engine", eng)
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": eng_nid,
                    "type": "uses_engine",
                }
            )

        # App-to-app dependency edges
        for dep_app in requires.get("apps", []):
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": f"app:{dep_app}",
                    "type": "calls_app",
                }
            )

    # --- Vault Data Nodes (from DEFAULT_PATHS) ---
    from emptyos.runtime.vault_map import DEFAULT_PATHS

    def _data_folder(path: str) -> str:
        """Extract a meaningful data folder (up to 2 levels)."""
        parts = path.replace("\\", "/").split("/")
        # Filter out template vars and file patterns
        parts = [p for p in parts if not p.startswith("{") and "*" not in p and "." not in p]
        if not parts:
            return ""
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return parts[0]

    # Determine write vs read: apps with vault_write, vault_config for known write dirs
    write_apps = {
        "journal",
        "quick-action",
        "healing",
        "expense",
        "nutrition",
        "jobmonitor",
        "rooms",
        "reactor",
        "compose",
        "lyrics",
        "mv-creator",
        "interview-studio",
        "contacts",
    }

    for app_id, keys in DEFAULT_PATHS.items():
        for key, fallbacks in keys.items():
            primary = fallbacks[0] if fallbacks else ""
            # Handle comma-separated multi-folder values (e.g. task scan_folders)
            for segment in primary.split(","):
                folder = _data_folder(segment.strip())
                if not folder:
                    continue
                data_nid = f"data:{folder}"
                add_node(data_nid, "data", folder)
                edge_type = "writes_data" if app_id in write_apps else "reads_data"
                edges.append(
                    {
                        "source": f"app:{app_id}",
                        "target": data_nid,
                        "type": edge_type,
                    }
                )

    return {"nodes": nodes, "edges": edges}


def _topology_timeline(kernel) -> dict:
    """Compute per-node creation date with cache.

    Resolution order per node:
      1. Manifest `created` field in [app] / [plugin] / [engine] table
      2. `git log --diff-filter=A --follow --format=%aI -- <manifest.toml>` (oldest)
      3. Filesystem mtime of manifest.toml

    Derived nodes (capabilities, providers, services, events, data) inherit the
    earliest date among manifests that introduce them.
    """
    import datetime
    import json
    import subprocess

    repo_root = kernel.config.path.resolve().parent
    cache_path = kernel.config.data_dir / "apps" / "topology" / "dates.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    head = ""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        pass

    # Cache schema version — bump when shape changes.
    # v2: full ISO timestamps. v3: nodes are {date, message} dicts (was bare strings).
    CACHE_VERSION = 3

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("head") == head and head and cached.get("version") == CACHE_VERSION:
                return _topology_timeline_serve(
                    kernel,
                    cached.get("min_date"),
                    cached.get("max_date"),
                    cached.get("nodes", {}),
                )
        except Exception:
            pass

    def _git_first_seen(path) -> tuple[str, str] | None:
        """Returns (UTC ISO timestamp, commit subject) of the commit that first
        added `path`. Subject (`%s`) gives the birth log a real changelog feel."""
        try:
            r = subprocess.run(
                [
                    "git",
                    "log",
                    "--follow",
                    "--diff-filter=A",
                    "--format=%aI%x09%s",
                    "--",
                    str(path),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = [line for line in r.stdout.strip().splitlines() if line]
            if not lines:
                return None
            parts = lines[-1].split("\t", 1)
            iso = parts[0]
            subj = parts[1] if len(parts) > 1 else ""
            try:
                dt = datetime.datetime.fromisoformat(iso)
                return (dt.astimezone(datetime.timezone.utc).isoformat(), subj)
            except Exception:
                return (iso, subj)
        except Exception:
            return None

    def _normalize(value) -> str:
        """Coerce a manifest `created` value (date-only or full ISO) to UTC ISO."""
        s = str(value)
        try:
            if "T" not in s:
                s = s + "T00:00:00+00:00"
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc).isoformat()
        except Exception:
            return s

    def _date_for(manifest) -> dict:
        """Returns {date: ISO, message: str}."""
        raw = getattr(manifest, "raw", {}) or {}
        for table in ("app", "plugin", "engine"):
            section = raw.get(table)
            if isinstance(section, dict) and section.get("created"):
                return {"date": _normalize(section["created"]), "message": ""}
        manifest_dir = getattr(manifest, "path", None)
        if manifest_dir is not None:
            mfile = Path(manifest_dir) / "manifest.toml"
            if mfile.exists():
                hit = _git_first_seen(mfile)
                if hit:
                    return {"date": hit[0], "message": hit[1]}
                try:
                    return {
                        "date": datetime.datetime.fromtimestamp(
                            mfile.stat().st_mtime, tz=datetime.timezone.utc
                        ).isoformat(),
                        "message": "(uncommitted)",
                    }
                except Exception:
                    pass
        return {
            "date": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            "message": "",
        }

    nodes: dict[str, dict] = {}

    today_iso = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    today_entry = {"date": today_iso, "message": ""}

    def _earlier(a: dict, b: dict) -> dict:
        return a if a["date"] <= b["date"] else b

    # Apps
    for app_id, manifest in kernel.apps.manifests.items():
        nodes[f"app:{app_id}"] = _date_for(manifest)

    # Plugins
    for plugin_id, manifest in kernel.plugins.manifests.items():
        d = _date_for(manifest)
        nodes[f"plugin:{plugin_id}"] = d
        for svc in manifest.provides.get("services", []):
            sid = f"service:{svc}"
            nodes[sid] = _earlier(d, nodes.get(sid, d))

    # Engines
    for engine_id, manifest in kernel.engines.manifests.items():
        nodes[f"engine:{engine_id}"] = _date_for(manifest)

    # Capabilities + providers — earliest plugin date that provides into them
    for cap_name, cap in kernel.capabilities.list().items():
        cap_id = f"cap:{cap_name}"
        earliest = today_entry
        for plugin_id, manifest in kernel.plugins.manifests.items():
            provided = manifest.raw.get("provides", {}).get("capabilities", {}) or {}
            if cap_name in provided or cap_name in (manifest.raw.get("enhances") or []):
                pdate = nodes.get(f"plugin:{plugin_id}", today_entry)
                earliest = _earlier(pdate, earliest)
        nodes[cap_id] = earliest
        for provider in cap.providers:
            pid = f"prov:{cap_name}:{provider.name}"
            nodes[pid] = earliest

    # Events — earliest date of any emitter/listener
    event_dates: dict[str, dict] = {}
    for app_id, manifest in kernel.apps.manifests.items():
        ad = nodes.get(f"app:{app_id}", today_entry)
        for evt in manifest.provides.get("events", {}).get("emits", []) or []:
            event_dates[evt] = _earlier(event_dates.get(evt, today_entry), ad)
        for evt in manifest.requires.get("events", []) or []:
            event_dates[evt] = _earlier(event_dates.get(evt, today_entry), ad)
        for evt in manifest.provides.get("events", {}).get("listens", []) or []:
            event_dates[evt] = _earlier(event_dates.get(evt, today_entry), ad)
    for evt, d in event_dates.items():
        nodes[f"event:{evt}"] = d

    if nodes:
        all_iso = [v["date"] for v in nodes.values()]
        min_date = min(all_iso)
        max_date = max(all_iso)
    else:
        min_date = max_date = today_iso

    payload = {
        "version": CACHE_VERSION,
        "head": head,
        "min_date": min_date,
        "max_date": max_date,
        "nodes": nodes,
    }
    try:
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass

    return _topology_timeline_serve(kernel, min_date, max_date, nodes)


def _topology_timeline_serve(kernel, min_date: str, max_date: str, nodes: dict) -> dict:
    """Apply public-mode privacy filter on the way out.

    On `network.mode = "public"` or `demo.enabled`, strip time-of-day from every
    timestamp so commit hours never leak. Cache stays full-fidelity so a private
    machine that later opens to public has no cache invalidation needed.
    """
    public_mode = (
        kernel.config.get("network.mode") == "public"
        or bool(kernel.config.get("demo.enabled"))
    )
    if public_mode:
        def _to_day(iso: str) -> str:
            return (iso[:10] + "T00:00:00+00:00") if iso else iso
        nodes = {k: {"date": _to_day(v["date"]), "message": v.get("message", "")}
                 for k, v in nodes.items()}
        min_date = _to_day(min_date)
        max_date = _to_day(max_date)
    return {
        "min_date": min_date,
        "max_date": max_date,
        "nodes": nodes,
        "time_resolution": "day" if public_mode else "minute",
    }


def _topology_releases(kernel) -> dict:
    """Return git tags as release markers: [{tag, date, message}, ...] in UTC ISO."""
    import datetime
    import subprocess

    repo_root = kernel.config.path.resolve().parent
    try:
        r = subprocess.run(
            [
                "git",
                "tag",
                "--sort=creatordate",
                "--format=%(refname:short)|%(creatordate:iso-strict)|%(subject)",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return {"releases": []}

    public_mode = (
        kernel.config.get("network.mode") == "public"
        or bool(kernel.config.get("demo.enabled"))
    )
    releases = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) < 2:
            continue
        tag, iso = parts[0].strip(), parts[1].strip()
        msg = parts[2].strip() if len(parts) > 2 else ""
        try:
            dt = datetime.datetime.fromisoformat(iso)
            iso_utc = dt.astimezone(datetime.timezone.utc).isoformat()
        except Exception:
            iso_utc = iso
        if public_mode and iso_utc:
            iso_utc = iso_utc[:10] + "T00:00:00+00:00"
        releases.append({"tag": tag, "date": iso_utc, "message": msg})
    return {"releases": releases, "time_resolution": "day" if public_mode else "minute"}


def _build_topology_tree(kernel) -> dict:
    """Capability-rooted tree: 9 capabilities → providers + consuming apps.

    Apps that declare no capabilities show up in `groundcover` rather than on
    a branch — rendered as wildflowers / grass at the base of the tree.
    """
    raw = _topology_timeline(kernel).get("nodes", {})
    timeline = {k: (v.get("date") if isinstance(v, dict) else v) for k, v in raw.items()}
    roots = []
    # Build app-by-capability index
    cap_consumers: dict[str, list[dict]] = {}
    rooted_apps: set[str] = set()
    for app_id, manifest in kernel.apps.manifests.items():
        caps = manifest.requires.get("capabilities", []) or []
        for cap in caps:
            cap_consumers.setdefault(cap, []).append(
                {
                    "id": f"app:{app_id}",
                    "label": manifest.name,
                    "type": "app",
                    "created": timeline.get(f"app:{app_id}"),
                    "description": manifest.description,
                }
            )
            rooted_apps.add(app_id)
    # Engines that depend on a capability appear under it as enhancers
    cap_engines: dict[str, list[dict]] = {}
    for engine_id, manifest in kernel.engines.manifests.items():
        for cap in manifest.requires.get("capabilities", []) or []:
            cap_engines.setdefault(cap, []).append(
                {
                    "id": f"engine:{engine_id}",
                    "label": manifest.name,
                    "type": "engine",
                    "created": timeline.get(f"engine:{engine_id}"),
                }
            )

    for cap_name, cap in kernel.capabilities.list().items():
        cap_id = f"cap:{cap_name}"
        providers = []
        for provider in cap.providers:
            pid = f"prov:{cap_name}:{provider.name}"
            providers.append(
                {
                    "id": pid,
                    "label": provider.name,
                    "type": "provider",
                    "created": timeline.get(pid),
                    "is_cloud": getattr(provider, "is_cloud", False),
                }
            )
        consumers = sorted(
            cap_consumers.get(cap_name, []),
            key=lambda n: (n.get("created") or "9999", n["label"]),
        )
        engines = sorted(
            cap_engines.get(cap_name, []),
            key=lambda n: (n.get("created") or "9999", n["label"]),
        )
        roots.append(
            {
                "id": cap_id,
                "label": cap_name,
                "type": "capability",
                "created": timeline.get(cap_id),
                "providers": providers,
                "engines": engines,
                "consumers": consumers,
            }
        )
    # Order roots by total node count desc (richest capability first), then alpha
    roots.sort(
        key=lambda r: (-(len(r["providers"]) + len(r["consumers"]) + len(r["engines"])), r["label"])
    )

    # Groundcover: apps that declare no capabilities. They live in the soil at
    # the base of the tree as flowers / grass, with their own roots — they're
    # not parasitic on any branch.  An app counts as "kind" = "engine-bound" if
    # it requires at least one engine, otherwise "rootless".  The frontend uses
    # this to draw saplings (engine-bound) vs wildflowers (rootless).
    groundcover = []
    for app_id, manifest in kernel.apps.manifests.items():
        if app_id in rooted_apps:
            continue
        engines_req = manifest.requires.get("engines", []) or []
        kind = "sapling" if engines_req else "flower"
        groundcover.append(
            {
                "id": f"app:{app_id}",
                "label": manifest.name,
                "type": "app",
                "kind": kind,
                "created": timeline.get(f"app:{app_id}"),
                "description": manifest.description,
            }
        )
    groundcover.sort(key=lambda n: (n.get("created") or "9999", n["label"]))

    return {"roots": roots, "groundcover": groundcover}


def _analyze_layers(kernel) -> dict:
    """Compute dependency layers, detect cycles, find critical path.

    Uses the topology graph to assign each node a depth (layer) via
    topological sort.  Layer = max(depth of dependencies) + 1.
    Infrastructure nodes get fixed layers (0=providers, 1=capabilities,
    2=plugins/services, 3=engines).  App layers are computed from deps.
    """
    from collections import deque

    topo = _build_topology(kernel)
    nodes = {n["id"]: n for n in topo["nodes"]}
    edges = topo["edges"]

    # --- Build adjacency in a single pass ---
    structural_edges = {
        "uses_capability",
        "uses_service",
        "uses_engine",
        "calls_app",
        "has_provider",
        "provides_service",
        "reads_data",
        "writes_data",
    }
    deps: dict[str, list[str]] = {nid: [] for nid in nodes}
    reverse_deps: dict[str, list[str]] = {nid: [] for nid in nodes}
    in_degree: dict[str, int] = {nid: 0 for nid in nodes}

    for e in edges:
        if e["type"] not in structural_edges:
            continue
        src, tgt = e["source"], e["target"]
        if src in nodes and tgt in nodes:
            deps[src].append(tgt)
            reverse_deps[tgt].append(src)
            in_degree[src] += 1

    # --- Fixed layers for infrastructure ---
    fixed_layers = {}
    for nid, node in nodes.items():
        if node["type"] == "provider":
            fixed_layers[nid] = 0
        elif node["type"] == "capability":
            fixed_layers[nid] = 1
        elif node["type"] in ("plugin", "service"):
            fixed_layers[nid] = 2
        elif node["type"] == "engine":
            fixed_layers[nid] = 3
        elif node["type"] == "data":
            fixed_layers[nid] = 3  # same level as engines (infrastructure)

    # --- Cycle detection (Kahn's algorithm) ---
    remaining = dict(in_degree)
    queue = deque(nid for nid, deg in remaining.items() if deg == 0)
    sorted_order = []
    while queue:
        nid = queue.popleft()
        sorted_order.append(nid)
        for dependent in reverse_deps.get(nid, []):
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                queue.append(dependent)

    cycles = []
    if len(sorted_order) < len(nodes):
        # Find nodes in cycles
        cycle_nodes = set(nodes.keys()) - set(sorted_order)
        # Group by app-to-app cycles
        cycle_apps = [nid for nid in cycle_nodes if nodes[nid]["type"] == "app"]
        if cycle_apps:
            cycles.append(
                {
                    "nodes": cycle_apps,
                    "description": f"{len(cycle_apps)} apps in dependency cycle",
                }
            )

    # --- Compute layers via BFS from leaves ---
    # Infrastructure (providers, capabilities, plugins, engines) gets fixed layers.
    # Apps always start at layer 4+ so they never collide with infrastructure.
    APP_BASE_LAYER = 4
    layer: dict[str, int] = {}
    for nid in sorted_order:
        if nid in fixed_layers:
            layer[nid] = fixed_layers[nid]
        elif nodes[nid]["type"] in ("app", "event"):
            # Only count app-to-app deps for app layer depth
            app_dep_layers = [
                layer[d] for d in deps[nid] if d in layer and nodes.get(d, {}).get("type") == "app"
            ]
            if app_dep_layers:
                layer[nid] = max(app_dep_layers) + 1
            else:
                layer[nid] = APP_BASE_LAYER
        else:
            dep_layers = [layer[d] for d in deps[nid] if d in layer]
            layer[nid] = (max(dep_layers) + 1) if dep_layers else 0

    # --- Assign layer names ---
    max_layer = max(layer.values()) if layer else 4

    def _layer_name(lv: int) -> str:
        fixed = {0: "Providers", 1: "Capabilities", 2: "Plugins & Services", 3: "Engines & Data"}
        if lv in fixed:
            return fixed[lv]
        depth = lv - 4
        if depth == 0:
            return "Base Apps"
        if depth == 1:
            return "Single-Dep Apps"
        if depth == 2:
            return "Aggregator Apps"
        return "Composition Apps"

    # --- Build layered output ---
    layers_out: dict[int, dict] = {}
    for nid, lv in layer.items():
        if lv not in layers_out:
            layers_out[lv] = {"level": lv, "name": _layer_name(lv), "nodes": []}
        node = dict(nodes[nid])
        node["layer"] = lv
        node["fan_in"] = len(reverse_deps.get(nid, []))
        node["fan_out"] = len(deps.get(nid, []))
        layers_out[lv]["nodes"].append(node)

    # Sort layers and nodes within
    for lv_data in layers_out.values():
        lv_data["nodes"].sort(key=lambda n: (-n["fan_in"], n["label"]))
        lv_data["width"] = len(lv_data["nodes"])

    # --- Critical path (longest chain) ---
    # BFS from each leaf to find the longest path
    longest_path: list[str] = []
    memo: dict[str, list[str]] = {}

    def _longest_from(nid: str) -> list[str]:
        if nid in memo:
            return memo[nid]
        best: list[str] = []
        for dep in deps.get(nid, []):
            if dep in layer:  # skip cycle nodes
                candidate = _longest_from(dep)
                if len(candidate) > len(best):
                    best = candidate
        memo[nid] = [nid] + best
        return memo[nid]

    for nid in sorted_order:
        path = _longest_from(nid)
        if len(path) > len(longest_path):
            longest_path = path

    critical_path = [
        {
            "id": nid,
            "label": nodes[nid]["label"],
            "type": nodes[nid]["type"],
            "layer": layer.get(nid, -1),
        }
        for nid in longest_path
    ]

    # --- Fan-in/fan-out rankings (top 10) ---
    fan_in_ranking = sorted(
        [(nid, len(reverse_deps.get(nid, []))) for nid in nodes if nodes[nid]["type"] == "app"],
        key=lambda x: -x[1],
    )[:10]
    fan_out_ranking = sorted(
        [(nid, len(deps.get(nid, []))) for nid in nodes if nodes[nid]["type"] == "app"],
        key=lambda x: -x[1],
    )[:10]

    # --- Summary stats ---
    app_layers = [layer[nid] for nid in nodes if nodes[nid]["type"] == "app" and nid in layer]
    layer_counts = {}
    for lv in app_layers:
        layer_counts[lv] = layer_counts.get(lv, 0) + 1

    # --- Data coupling analysis ---
    data_coupling = []
    for nid, node in nodes.items():
        if node["type"] != "data":
            continue
        consumers = reverse_deps.get(nid, [])
        if not consumers:
            continue
        readers = [
            c
            for c in consumers
            if any(
                e["source"] == c and e["target"] == nid and e["type"] == "reads_data" for e in edges
            )
        ]
        writers = [
            c
            for c in consumers
            if any(
                e["source"] == c and e["target"] == nid and e["type"] == "writes_data"
                for e in edges
            )
        ]
        reader_names = sorted(set(nodes[c]["label"] for c in readers if c in nodes))
        writer_names = sorted(set(nodes[c]["label"] for c in writers if c in nodes))
        unique_apps = len(set(readers) | set(writers))
        data_coupling.append(
            {
                "folder": node["label"],
                "total_apps": unique_apps,
                "readers": reader_names,
                "writers": writer_names,
                "coupling_risk": "high"
                if unique_apps >= 5
                else "medium"
                if unique_apps >= 3
                else "low",
            }
        )
    data_coupling.sort(key=lambda x: -x["total_apps"])

    return {
        "layers": [layers_out[lv] for lv in sorted(layers_out.keys())],
        "cycles": cycles,
        "critical_path": {
            "length": len(critical_path),
            "path": critical_path,
        },
        "data_coupling": data_coupling,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "max_depth": max_layer,
            "layer_widths": {_layer_name(lv): cnt for lv, cnt in sorted(layer_counts.items())},
            "most_depended_on": [
                {"id": nid, "label": nodes[nid]["label"], "fan_in": fi}
                for nid, fi in fan_in_ranking
            ],
            "most_dependencies": [
                {"id": nid, "label": nodes[nid]["label"], "fan_out": fo}
                for nid, fo in fan_out_ranking
            ],
        },
    }


async def _compute_improvements(kernel) -> dict:
    """Analyze topology + integrity and return prioritized improvement actions.

    Each improvement is a concrete, actionable item with:
    - category (integrity, topology, data, event)
    - priority (critical, high, medium, low)
    - description (what's wrong)
    - action (what to do)
    - files (which files to change)
    """

    improvements = []
    _audit = None  # cache for reuse by verb health section

    # --- Integrity dimension gaps ---
    try:
        integrity_app = kernel.apps.instances.get("integrity")
        if integrity_app:
            audit = integrity_app._run_audit()
            _audit = audit
            for dim_name, dim in audit.get("dimensions", {}).items():
                if dim["score"] < 10:
                    gap = 10 - dim["score"]
                    priority = "high" if gap >= 3 else "medium" if gap >= 2 else "low"
                    for violation in dim.get("violations", []):
                        improvements.append(
                            {
                                "category": "integrity",
                                "dimension": dim_name,
                                "priority": priority,
                                "score": f"{dim['score']}/10",
                                "description": violation,
                                "action": dim.get("growth_signal", ""),
                            }
                        )
    except Exception:
        pass

    # --- Topology: cycles ---
    layers = _analyze_layers(kernel)
    for cycle in layers.get("cycles", []):
        improvements.append(
            {
                "category": "topology",
                "priority": "critical",
                "description": cycle["description"],
                "action": f"Break dependency cycle between: {', '.join(cycle['nodes'][:5])}",
                "files": [
                    f"apps/{nid.replace('app:', '')}/manifest.toml" for nid in cycle["nodes"][:5]
                ],
            }
        )

    # --- Topology: monolith apps ---
    apps_dir = kernel.config.path.parent / "apps"
    for app_id in kernel.apps.manifests:
        app_py = apps_dir / app_id / "app.py"
        if app_py.exists():
            try:
                lines = len(app_py.read_text(encoding="utf-8", errors="ignore").split("\n"))
                if lines > 1200:
                    improvements.append(
                        {
                            "category": "topology",
                            "priority": "medium",
                            "description": f"{app_id} is {lines} lines — monolith risk",
                            "action": "Decompose into app.py + extended.py (like briefing, projects pattern)",
                            "files": [f"apps/{app_id}/app.py"],
                        }
                    )
            except Exception:
                pass

    # --- Data coupling: high-risk shared folders ---
    # Determine owner app per folder (the writer, or first declared in DEFAULT_PATHS)
    from emptyos.runtime.vault_map import DEFAULT_PATHS

    folder_owners = {}
    for app_id, keys in DEFAULT_PATHS.items():
        for key, fallbacks in keys.items():
            for seg in fallbacks[0].split(","):
                parts = [
                    p
                    for p in seg.strip().replace("\\", "/").split("/")
                    if not p.startswith("{") and "*" not in p and "." not in p
                ]
                folder = "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")
                if folder and folder not in folder_owners:
                    folder_owners[folder] = app_id

    for dc in layers.get("data_coupling", []):
        if dc["coupling_risk"] == "high" and dc["total_apps"] >= 5:
            owner = dc["writers"][0] if dc["writers"] else folder_owners.get(dc["folder"], "?")
            readers_list = ", ".join(dc["readers"])
            improvements.append(
                {
                    "category": "data",
                    "priority": "medium",
                    "description": f"{dc['folder']} shared by {dc['total_apps']} apps — {readers_list} read directly instead of calling {owner} app",
                    "action": f'Readers should use call_app("{owner.lower().replace(" ", "-")}", ...) instead of scanning vault files. Add missing read methods to {owner} app if needed.',
                    "files": [f"apps/{r.lower().replace(' ', '-')}/app.py" for r in dc["readers"]],
                }
            )

    # --- Events: unheard events from important apps ---
    topo = _build_topology(kernel)
    emitted = {}
    listened = set()
    for e in topo["edges"]:
        if e["type"] == "emits_event":
            emitted[e["target"]] = e["source"]
        elif e["type"] == "listens_event":
            listened.add(e["source"])
    unheard = {evt: src for evt, src in emitted.items() if evt not in listened}
    if unheard:
        # Group by source app
        by_app = {}
        for evt, src in unheard.items():
            by_app.setdefault(src, []).append(evt.replace("event:", ""))
        for src, evts in sorted(by_app.items(), key=lambda x: -len(x[1])):
            if len(evts) >= 3:
                app_id = src.replace("app:", "")
                improvements.append(
                    {
                        "category": "event",
                        "priority": "low",
                        "description": f"{app_id} emits {len(evts)} unheard events: {', '.join(evts[:5])}",
                        "action": "Wire events into reactor for logging/journal ripple, or remove unused emits",
                        "files": [f"apps/{app_id}/manifest.toml", "apps/reactor/app.py"],
                    }
                )

    # --- Apps with no custom UI ---
    for app_id, manifest in kernel.apps.manifests.items():
        manifest_dir = manifest.path if hasattr(manifest, "path") else apps_dir / app_id
        pages_dir = manifest_dir / "pages"
        if manifest_dir.name.startswith("_"):
            continue
        if not pages_dir.exists():
            improvements.append(
                {
                    "category": "integrity",
                    "priority": "low",
                    "description": f"{app_id} has no custom UI (pages/ directory)",
                    "action": "Add pages/index.html for custom UI, or accept auto-generated",
                    "files": [f"apps/{manifest_dir.name}/pages/index.html"],
                }
            )

    # --- Six Verbs health (唯识 metabolic cycle) ---
    # Reuse the audit already computed above — no re-scan needed
    verb_health = {}
    try:
        if _audit:
            verb_dim = _audit.get("dimensions", {}).get("P10 Six Verbs", {})
            verb_health = verb_dim.get("details", {}).get("verbs", {})
            # Add improvements for weak verbs
            for verb_id, vdata in verb_health.items():
                if vdata["points"] < 4:
                    missing = [k for k, v in vdata["layers"].items() if not v]
                    improvements.append(
                        {
                            "category": "verb",
                            "priority": "high" if vdata["points"] <= 2 else "medium",
                            "description": f"Weak lifecycle verb: {vdata['label']} ({vdata['points']}/6)",
                            "action": f"Add missing layers: {', '.join(missing)}",
                            "verb": verb_id,
                            "layers": vdata["layers"],
                        }
                    )
    except Exception:
        pass

    # --- Sort by priority ---
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    improvements.sort(key=lambda x: priority_order.get(x.get("priority", "low"), 9))

    return {
        "total": len(improvements),
        "by_priority": {
            p: sum(1 for i in improvements if i.get("priority") == p)
            for p in ("critical", "high", "medium", "low")
        },
        "improvements": improvements,
        "verb_health": verb_health,
    }
