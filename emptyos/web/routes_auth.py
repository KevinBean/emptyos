"""EmptyOS web — auth / privacy / trust-boundary middleware and routes.

Extracted verbatim from ``emptyos/web/server.py`` to keep the server spine
atomic (P4 Atomic, CLAUDE.md rule 4). Owns: the presentation-mode response
scrubber middleware + its /api/presentation/* toggle routes, the auth
middleware + /login /logout routes + login rate-limit state, the cloud-consent
endpoints (/api/cloud/*, /api/delegated-action), the per-IP request rate-limit
middleware, and the BYOK key-header middleware.

Each ``register_*`` function is called by ``create_server`` at the exact
source position its inline block previously occupied — **middleware
registration order is behavior** (Starlette wraps in add-order), so never
reorder the call sites in server.py.

``register_auth`` returns the ``(_check_token, _check_bearer)`` credential
closures (or ``(None, None)`` when no credential is configured) because the
spine's /api/health handler reuses them for its auth-aware response shape.

Note on ``_client_ip``: create_server historically defined it twice (once in
the auth block, once in the rate-limit block; the later binding won at
runtime). Both verbatim copies live here in their own scopes — the two
implementations are behaviorally equivalent (first X-Forwarded-For entry,
else client host, else "unknown").

Do not import from ``emptyos.web.server`` (it imports us — that would cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


def register_presentation_middleware(server: FastAPI, kernel: Kernel) -> None:
    """Presentation-mode response scrubber (moved verbatim from create_server)."""
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
    # Latch so a broken settings store logs once, not once per request.
    _PRESENT_SETTINGS_WARNED = {"warned": False}

    def _present_patterns():
        if _PRESENT_REGEX_CACHE["patterns"] is None:
            _PRESENT_REGEX_CACHE["patterns"] = _load_personal_patterns(_PRESENT_PATTERNS_FILE)
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

    # 2 MB ceiling on HTML scrubbing — defends against latency hit on large
    # SPA bundles or generated reports. Above this, pass through unchanged
    # and log a warning so an operator can investigate.
    _PRESENT_HTML_MAX_BYTES = 2 * 1024 * 1024

    def _scrub_html_bytes(body: bytes, patterns) -> bytes:
        """Run every personal-pattern regex over the HTML body as text.

        Operates on the full string (incl. inline <script>/<style>); a
        personal string in JS is still a leak. UTF-8 decode is errors='ignore'
        to handle pages with mixed encoding without raising.
        """
        text = body.decode("utf-8", errors="ignore")
        for pat in patterns:
            text = pat.sub("***", text)
        return text.encode("utf-8")

    class PresentationMiddleware(_BHM):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            try:
                active = bool(kernel.settings.get("presentation.enabled", False))
            except Exception as e:
                # Fail-open: we cannot 500 every response because the settings
                # store broke. But this silently disables the leak scrubber, so
                # say so once — an operator who enabled it deserves to know.
                active = False
                if not _PRESENT_SETTINGS_WARNED["warned"]:
                    _PRESENT_SETTINGS_WARNED["warned"] = True
                    try:
                        kernel.syslog.error(
                            "presentation",
                            f"settings read failed; scrubber DISABLED for this run: {e}",
                        )
                    except Exception:
                        pass
            if not active:
                return response
            path = request.url.path
            if any(path.startswith(p) for p in _PRESENT_PATH_EXEMPT):
                return response
            ctype = (response.headers.get("content-type") or "").lower()
            is_json = "application/json" in ctype
            is_html = "text/html" in ctype
            if not (is_json or is_html):
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
            new_media_type = "application/json" if is_json else "text/html"
            try:
                if body and is_json:
                    data = _json.loads(body.decode("utf-8"))
                    scrubbed = _scrub_value(data, patterns)
                    new_body = _json.dumps(scrubbed, default=str).encode("utf-8")
                elif body and is_html:
                    if len(body) > _PRESENT_HTML_MAX_BYTES:
                        # Pass through unchanged; warn so operator notices.
                        try:
                            kernel.syslog.warn(
                                "presentation",
                                f"skipped HTML scrub: {path} ({len(body)} bytes > 2MB)",
                            )
                        except Exception:
                            pass
                    else:
                        new_body = _scrub_html_bytes(body, patterns)
            except (_json.JSONDecodeError, UnicodeDecodeError):
                # Body was labelled JSON but isn't parseable as such. There is
                # nothing to scrub structurally; pass it through unchanged.
                new_body = body
            except Exception as e:
                # A failure *inside* the scrubber would otherwise emit the
                # unscrubbed body — the exact leak this middleware exists to
                # prevent. Fail closed.
                try:
                    kernel.syslog.error("presentation", f"scrub failed on {path}: {e}")
                except Exception:
                    pass
                return _R(
                    content=b'{"error":"presentation scrub failed"}',
                    status_code=500,
                    media_type="application/json",
                )
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return _R(
                content=new_body,
                status_code=response.status_code,
                headers=headers,
                media_type=new_media_type,
            )

    server.add_middleware(PresentationMiddleware)


def register_auth(server: FastAPI, kernel: Kernel):
    """Auth middleware + /login /logout routes (moved verbatim).

    Returns ``(_check_token, _check_bearer)`` when a credential is configured,
    else ``(None, None)``. The spine's /api/health guards on
    ``_auth_token or _login_password`` before calling them.
    """
    # --- Auth middleware (activates when token OR password is set) ---
    # Two credentials, one trust boundary — see docs/AUTH.md.
    #   auth_token: machine bearer (CLI, API clients, deep links)
    #   password:   human-typeable browser login
    # Either alone activates the gate; both work simultaneously.
    _auth_token: str = kernel.config.auth_token
    _login_password: str = kernel.config.login_password
    # Desktop-shell login exchange (dark). Needs a token to be worth anything:
    # the mint leg is bearer-only, so with no auth_token there is nothing to
    # exchange and the routes are not registered at all.
    # config_flag, not bool(): Config.get returns an ENV override as a string,
    # so bool() turns "false" and "0" into True. A dark flag any string
    # switches on is not a dark flag — and this one opens an auth-exempt route.
    from emptyos.sdk.utils import config_flag as _config_flag

    _shell_exchange_on: bool = _config_flag(
        kernel.config, "network.feature.shell-exchange.enabled"
    ) and bool(_auth_token)
    if _auth_token or _login_password:
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
        # The shell exchange's REDEEM leg is exempt by necessity: it is the
        # route that makes you authenticated, so it cannot require a session.
        # Everything it accepts is checked inside it — an unexpired single-use
        # code, a peer on the loopback SOCKET, a local `next` — and it is only
        # registered when [network] feature.shell-exchange.enabled is on.
        # An exact path, not a prefix: a subtree exemption would silently
        # unauthenticate anything ever mounted beneath it.
        if _shell_exchange_on:
            _AUTH_EXEMPT_PATHS.add("/auth/shell-exchange")

        # Apps may publish a "public face" via [provides.web].public_routes.
        # Each entry is appended to the app's prefix and added to the bypass
        # set. Lets a single app (e.g. /radio/live) be reachable without a
        # token while the rest of the daemon stays gated. The auth boundary
        # is the network gate; per-app code is still responsible for filtering
        # what content a public caller can read.
        # App public-route prefixes only (NOT /static or /login) — used to flag
        # the anonymous public face so the page strips owner chrome.
        _PUBLIC_APP_PREFIXES: list[str] = []
        for _m in kernel.apps.manifests.values():
            _web = (_m.provides or {}).get("web", {}) or {}
            _prefix = _web.get("prefix", "")
            for _r in _web.get("public_routes", []) or []:
                if not isinstance(_r, str) or not _r:
                    continue
                _full = _prefix + _r if _r.startswith("/") else _prefix + "/" + _r
                _AUTH_EXEMPT_PREFIXES.append(_full)
                _PUBLIC_APP_PREFIXES.append(_full)
        _AUTH_EXEMPT_PREFIXES = tuple(_AUTH_EXEMPT_PREFIXES)
        _PUBLIC_APP_PREFIXES = tuple(_PUBLIC_APP_PREFIXES)

        # Cookie value is whichever credential the user signed in with —
        # not normalized — so rotating one without the other doesn't
        # invalidate the other's sessions. compare_digest against both.
        def _check_token(provided: str) -> bool:
            if not provided:
                return False
            if _auth_token and hmac.compare_digest(provided, _auth_token):
                return True
            if _login_password and hmac.compare_digest(provided, _login_password):
                return True
            return False

        def _check_bearer(provided: str) -> bool:
            # Bearer header is for machines — only the token is valid here.
            # Humans don't paste passwords as Authorization headers.
            return (
                bool(provided)
                and bool(_auth_token)
                and hmac.compare_digest(
                    provided,
                    _auth_token,
                )
            )

        # --- Rate limit /login + ?token= attempts ---
        # Sliding window per client IP. Failures inside the window count;
        # crossing the threshold trips a fixed cooldown. In-process state —
        # resets on daemon restart, doesn't need to survive that.
        _LOGIN_WINDOW_S = 60.0
        _LOGIN_MAX_FAILS = 5
        _LOGIN_COOLDOWN_S = 30.0
        _login_fails: dict[str, list[float]] = {}
        _login_locked_until: dict[str, float] = {}

        # `tailscale` plugin already caches `_raw_status` with a 30s TTL, and
        # `_audit_unauth` below is throttled to 1 entry per 30s per (ip,
        # path-prefix), so this resolver doesn't need its own cache layer.
        async def _peer_label(ip: str) -> str:
            if not ip or ip == "unknown":
                return ""
            ts = kernel.services.get_optional("tailscale")
            if not ts:
                return ""
            try:
                for p in await ts.peers():
                    if ip in (p.get("ips") or [p.get("ip")]):
                        name = p.get("name") or p.get("dns") or ""
                        return f"{name} (tailnet)" if name else ""
                status = await ts.status()
                if status.get("self_ip") == ip:
                    return f"{status.get('self_name', '')} (self, tailnet)"
            except Exception:
                pass
            return ""

        # Throttle audit log writes: at most one per (ip, path-prefix) per 30s
        # so a hammering attacker can't fill syslog with auth failures.
        _audit_throttle: dict[tuple[str, str], float] = {}

        async def _audit_unauth(request: Request, reason: str) -> None:
            import time as _t

            ip = _client_ip(request)
            path = request.url.path
            # Bucket by path-prefix so unique paths from one attacker still
            # get throttled together.
            key = (ip, path.split("/", 3)[1] if "/" in path[1:] else path)
            now = _t.time()
            last = _audit_throttle.get(key, 0.0)
            if now - last < 30.0:
                return
            _audit_throttle[key] = now
            label = await _peer_label(ip)
            who = f"{label} {ip}" if label else ip
            try:
                kernel.syslog.warn(
                    "auth",
                    f"unauthenticated {reason}: {who} -> {path}",
                )
            except Exception:
                pass

        def _client_ip(request: Request) -> str:
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                first = xff.split(",")[0].strip()
                if first:
                    return first
            return (request.client.host if request.client else "") or "unknown"

        def _peer_ip(request: Request) -> str:
            """The address the connection actually came FROM.

            Deliberately not ``_client_ip``: that prefers ``X-Forwarded-For``,
            which is correct for rate-limiting behind a proxy and catastrophic
            for a locality decision — the header is set by the caller, so
            "loopback only" would mean "claims to be loopback". A proxied
            deployment terminates TCP at the proxy, so a real remote peer can
            never present a loopback socket address here.
            """
            return (request.client.host if request.client else "") or ""

        def _login_check_lock(ip: str) -> float:
            import time as _t

            until = _login_locked_until.get(ip, 0.0)
            if until and until > _t.time():
                return until - _t.time()
            if until:
                _login_locked_until.pop(ip, None)
            return 0.0

        def _login_record_fail(ip: str) -> None:
            import time as _t

            now = _t.time()
            buf = _login_fails.setdefault(ip, [])
            buf.append(now)
            cutoff = now - _LOGIN_WINDOW_S
            buf[:] = [t for t in buf if t >= cutoff]
            if len(buf) >= _LOGIN_MAX_FAILS:
                _login_locked_until[ip] = now + _LOGIN_COOLDOWN_S
                _login_fails.pop(ip, None)

        def _login_record_success(ip: str) -> None:
            _login_fails.pop(ip, None)
            _login_locked_until.pop(ip, None)

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path
                # Exempt login, static, favicon, health
                if path in _AUTH_EXEMPT_PATHS:
                    return await call_next(request)
                # Boundary-safe prefix match: exact, or prefix followed by a
                # slash. Plain startswith() would exempt `/loginxyz` for a
                # `/login` rule, or `/radio/api/audiobar` for `/radio/api/audio`.
                if any(
                    path == p or path.startswith(p.rstrip("/") + "/") for p in _AUTH_EXEMPT_PREFIXES
                ):
                    # On an app public route, flag the anonymous face (no valid
                    # cookie/bearer) so the page renders stripped chrome. An
                    # authenticated owner on the same route gets the normal app.
                    if any(
                        path == p or path.startswith(p.rstrip("/") + "/")
                        for p in _PUBLIC_APP_PREFIXES
                    ):
                        _ah = request.headers.get("authorization", "")
                        _authed = (
                            _ah.lower().startswith("bearer ")
                            and _check_bearer(_ah[7:].strip())
                        ) or _check_token(request.cookies.get(_AUTH_COOKIE, ""))
                        if not _authed:
                            request.state.public_face = True
                    return await call_next(request)

                # Check bearer token (API clients, CLI) — token only, not password
                auth_header = request.headers.get("authorization", "")
                if auth_header.lower().startswith("bearer "):
                    if _check_bearer(auth_header[7:].strip()):
                        return await call_next(request)

                # Check session cookie (browser)
                cookie_tok = request.cookies.get(_AUTH_COOKIE, "")
                if _check_token(cookie_tok):
                    return await call_next(request)

                # Check ?token= query param (deep-link sign-in for landing pages).
                # On match, set the cookie and 302 to the same path with token
                # stripped from the URL — so it never lingers in the address bar
                # or browser history beyond the first hop. Wrong values count
                # against the per-IP rate limit (same brute-force shape as
                # POST /login). Absent token is not a failure.
                qtok = request.query_params.get("token", "")
                if qtok:
                    ip = _client_ip(request)
                    remaining = _login_check_lock(ip)
                    if remaining > 0:
                        return JSONResponse(
                            {
                                "error": "too many failed attempts",
                                "retry_after_seconds": int(remaining) + 1,
                            },
                            status_code=429,
                            headers={"Retry-After": str(int(remaining) + 1)},
                        )
                    if _check_token(qtok):
                        _login_record_success(ip)
                        clean_qs = "&".join(
                            f"{k}={v}"
                            for k, v in request.query_params.multi_items()
                            if k != "token"
                        )
                        clean_url = path + (("?" + clean_qs) if clean_qs else "")
                        resp = RedirectResponse(url=clean_url, status_code=302)
                        resp.set_cookie(
                            _AUTH_COOKIE,
                            qtok,
                            httponly=True,
                            samesite="lax",
                            max_age=60 * 60 * 24 * 30,
                        )
                        return resp
                    _login_record_fail(ip)

                # API returns 401 JSON, browser redirects to login.
                # Match `/api/` anywhere in the path so app-namespaced
                # endpoints like `/dogfood-agent/api/...`, `/cable-network/api/...`
                # get a JSON 401 too — without this, SPA fetches with an
                # expired cookie silently 302 → /login (HTML), the SPA's
                # response.json() throws, and the user sees "nothing happens"
                # (or a generic Network error) instead of a real auth failure.
                accept = request.headers.get("accept", "")
                ctype = request.headers.get("content-type", "")
                # Audit log — peer-labelled, throttled per (ip, path-prefix).
                await _audit_unauth(request, "request")
                if "/api/" in path or "application/json" in accept or "application/json" in ctype:
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
                return RedirectResponse(url=f"/login?next={path}", status_code=302)

        server.add_middleware(AuthMiddleware)

        # Login page — GET shows form, POST sets cookie
        _LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"><title>EmptyOS — Login</title>
<style>
body{font-family:system-ui,sans-serif;background:#0e1117;color:#e6edf3;margin:0;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100dvh;padding:24px;box-sizing:border-box}
.box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:32px;width:320px}
h1{margin:0 0 8px;font-size:18px;font-weight:600}
p{margin:0 0 16px;color:#8b949e;font-size:13px}
input{width:100%;box-sizing:border-box;padding:8px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-family:inherit;font-size:13px}
button{width:100%;margin-top:12px;padding:8px;background:#238636;border:0;border-radius:6px;color:#fff;font-weight:600;cursor:pointer}
button:hover{background:#2ea043}
.err{color:#f85149;font-size:12px;margin-top:8px}
.demo-hint{background:#1f2937;border:1px solid #30363d;border-radius:6px;padding:10px 12px;font-size:12px;color:#8b949e;margin-bottom:14px;line-height:1.5}
.demo-hint code{background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:1px 6px;color:#e6edf3;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.foot{margin-top:24px;font-size:12px;color:#6e7681;text-align:center;line-height:1.6}
.foot a{color:#8b949e;text-decoration:none;border-bottom:1px dotted #30363d}
.foot a:hover{color:#e6edf3;border-bottom-color:#8b949e}
.foot .sep{margin:0 6px;color:#30363d}
</style></head><body>
<form class="box" method="post" action="/login">
<h1>EmptyOS</h1><p>__SUBTITLE__</p>
__DEMO_HINT__
<input type="password" name="token" placeholder="__PLACEHOLDER__" autofocus required>
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

            # On a public demo, show the password inline so first-time
            # visitors don't need a token-bearing link to get in. Length cap
            # guards against accidentally displaying a long auth_token if an
            # operator misconfigured the demo without a separate password.
            demo_hint_html = ""
            subtitle = "Sign in with your password or access token."
            placeholder = "Password or token"
            if kernel.config.demo_enabled and _login_password and len(_login_password) <= 32:
                pw_safe = _html.escape(_login_password, quote=True)
                demo_hint_html = (
                    f'<div class="demo-hint"><strong>Public demo.</strong> '
                    f"Password: <code>{pw_safe}</code></div>"
                )
                subtitle = "EmptyOS — public demo. Sign in with the password below."
                placeholder = "Password"

            page = (
                _LOGIN_HTML.replace("__NEXT__", next_url)
                .replace("__ERR__", err_html)
                .replace("__DEMO_HINT__", demo_hint_html)
                .replace("__SUBTITLE__", subtitle)
                .replace("__PLACEHOLDER__", placeholder)
            )
            return HTMLResponse(page)

        @server.post("/login")
        async def login_submit(request: Request):
            ip = _client_ip(request)
            remaining = _login_check_lock(ip)
            if remaining > 0:
                return JSONResponse(
                    {
                        "error": "too many failed attempts",
                        "retry_after_seconds": int(remaining) + 1,
                    },
                    status_code=429,
                    headers={"Retry-After": str(int(remaining) + 1)},
                )

            form = await request.form()
            provided = str(form.get("token", ""))
            next_url = str(form.get("next", "/")) or "/"
            # Basic next-URL safety: must be a local path
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = "/"
            if not _check_token(provided):
                _login_record_fail(ip)
                return RedirectResponse(
                    url=f"/login?next={next_url}&err=Invalid+token",
                    status_code=302,
                )
            _login_record_success(ip)
            resp = RedirectResponse(url=next_url, status_code=302)
            resp.set_cookie(
                _AUTH_COOKIE,
                provided,
                httponly=True,
                samesite="lax",
                max_age=60 * 60 * 24 * 30,
            )
            return resp

        if _shell_exchange_on:
            from emptyos.web.auth_exchange import CodeStore, request_is_local, safe_next

            _shell_codes = CodeStore()

            @server.post("/api/auth/shell-exchange")
            async def shell_exchange_mint(request: Request):
                """Bearer token in, one-shot code out. NEVER cookie-authenticated.

                The auth middleware would already admit a valid session cookie
                to any /api/ route, so this re-checks the Authorization header
                itself: a cookie is what a PAGE holds, and an XSS anywhere in
                the daemon could otherwise mint a code and carry a working
                login off the machine. A caller holding the bearer already
                holds the token, so the code grants it nothing new.
                """
                if not request_is_local(_peer_ip(request)):
                    return JSONResponse({"error": "not local"}, status_code=403)
                header = request.headers.get("authorization", "")
                if not (
                    header.lower().startswith("bearer ")
                    and _check_bearer(header[7:].strip())
                ):
                    return JSONResponse(
                        {"error": "shell exchange requires the machine token, not a session"},
                        status_code=403,
                    )
                return {"code": _shell_codes.mint(), "expires_in": _shell_codes.ttl_s}

            @server.get("/auth/shell-exchange")
            async def shell_exchange_redeem(request: Request):
                """Spend a code for the session cookie, then go to `next`.

                Auth-exempt, so this is the whole gate: loopback only, the code
                must be live and unused, and `next` is clamped to a path on this
                daemon (safe_next) so the cookie we just set cannot be carried
                to another origin.
                """
                if not request_is_local(_peer_ip(request)):
                    return JSONResponse({"error": "not local"}, status_code=403)
                # Rate-limit key: the socket address too. _client_ip would let
                # a caller choose their own key — escaping their lockout, and
                # pinning the real loopback key into permanent cooldown.
                ip = _peer_ip(request)
                # Wrong codes count against the same per-IP limit as /login:
                # the code is short-lived but it is still a guessable secret.
                remaining = _login_check_lock(ip)
                if remaining > 0:
                    return JSONResponse(
                        {"error": "too many failed attempts",
                         "retry_after_seconds": int(remaining) + 1},
                        status_code=429,
                        headers={"Retry-After": str(int(remaining) + 1)},
                    )
                offered = request.query_params.get("code", "")
                if not offered:
                    # No attempt was made, so nothing failed. Counting this
                    # would let any page the user visits lock them out of
                    # /login with five <img src="/auth/shell-exchange">.
                    return JSONResponse({"error": "no code"}, status_code=400)
                if not _shell_codes.redeem(offered):
                    _login_record_fail(ip)
                    return JSONResponse({"error": "expired or unknown code"}, status_code=403)
                _login_record_success(ip)
                resp = RedirectResponse(
                    url=safe_next(request.query_params.get("next", "/")), status_code=302
                )
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

        return _check_token, _check_bearer
    return None, None


def register_presentation_routes(server: FastAPI, kernel: Kernel) -> None:
    """/api/presentation/{state,toggle,set} (moved verbatim)."""
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
            await kernel.events.emit("presentation:changed", {"enabled": new}, source="web")
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
            await kernel.events.emit("presentation:changed", {"enabled": new}, source="web")
        except Exception:
            pass
        return {"enabled": new}


def register_cloud_routes(server: FastAPI, kernel: Kernel) -> None:
    """Cloud-consent + delegated-action endpoints (moved verbatim)."""
    # --- Cloud consent endpoints ---
    @server.get("/api/cloud/status")
    async def cloud_status():
        cm = getattr(kernel, "cloud_consent", None)
        if cm is None:
            return {"enabled": False}
        return {"enabled": True, **cm.status()}

    @server.post("/api/delegated-action")
    async def delegated_action(request: Request):
        """Governance delegation seam — a verified external principal invokes a
        gated verb through the existing autopilot decision path.

        Trust model: the only thing allowed to stamp an actor is a request
        carrying the daemon's own ``auth_token`` as a Bearer — i.e. one that
        arrived over the authenticated control-plane proxy hop (the proxy strips
        the browser's Authorization, so a browser authenticates by cookie and is
        rejected here and cannot forge ``X-EOS-Actor``). Logic lives in the
        shared ``emptyos.sdk.delegation.gate_or_dispatch``; this route is just
        the trust gate + actor parse + the rooms review surface for gated calls.
        """
        import hmac as _hmac

        from emptyos.sdk.utils import config_flag
        from emptyos.sdk.delegation import gate_or_dispatch

        if not config_flag(kernel.config, "autopilot.feature.delegated-action.enabled"):
            return JSONResponse({"error": "delegated-action disabled"}, status_code=404)

        # Trust gate (self-contained, mode-aware — does not rely on the
        # auth-middleware closure, which only exists in private/public mode).
        # When an auth_token is configured, only a request presenting it as a
        # Bearer (i.e. one that arrived over the authenticated control-plane
        # proxy hop, which substitutes the daemon's own token and strips the
        # browser's Authorization) may stamp an actor — a cookie-authed browser
        # is rejected and cannot forge X-EOS-Actor. When no token is configured
        # (local mode), the daemon is already an open loopback surface.
        configured = str(kernel.config.get("network.auth_token", "") or "")
        if configured:
            auth = request.headers.get("authorization", "")
            provided = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not (provided and _hmac.compare_digest(provided, configured)):
                return JSONResponse({"error": "bearer auth_token required"}, status_code=403)
        if getattr(request.state, "public_face", False):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        raw_actor = request.headers.get("x-eos-actor", "")
        if ":" not in raw_actor:
            return JSONResponse(
                {"error": "X-EOS-Actor header required (<type>:<id>)"}, status_code=400,
            )
        actor_type, actor_id = raw_actor.split(":", 1)
        actor = {"type": actor_type.strip(), "id": actor_id.strip()}
        if not actor["type"] or not actor["id"]:
            return JSONResponse({"error": "malformed X-EOS-Actor"}, status_code=400)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be an object"}, status_code=400)
        app_id = str(body.get("app", "")).strip()
        method = str(body.get("method", "")).strip()
        if not app_id or not method:
            return JSONResponse({"error": "app and method required"}, status_code=400)
        args = body.get("args") or {}
        if not isinstance(args, dict):
            return JSONResponse({"error": "args must be an object"}, status_code=400)
        scope = [*(body.get("scope_candidates") or []), "global"]

        rooms = kernel.apps.instances.get("rooms")

        async def _on_gate(record):
            if rooms is not None:
                await rooms.save_pending_action(
                    app=app_id, method=method, args=args, source_actor=actor,
                )

        return await gate_or_dispatch(
            kernel, actor=actor, app=app_id, method=method, args=args,
            scope_candidates=scope, on_gate=_on_gate,
        )

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
        if not cm.set_policy(policy):
            # [cloud] locked: the operator set this build's policy.
            return JSONResponse(
                {"error": "the cloud consent policy is set by the operator", "policy": cm.policy},
                status_code=409,
            )
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


def register_rate_limit(server: FastAPI) -> None:
    """Per-IP sliding-window rate limit middleware (moved verbatim)."""
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


def register_byok_middleware(server: FastAPI) -> None:
    """BYOK per-request key-header middleware (moved verbatim)."""
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
