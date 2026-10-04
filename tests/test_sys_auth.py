"""Access-control regression suite — Track A security fixes.

Covers the three hardening changes in ``emptyos/web/server.py``:

  A1  App WebSockets are owner-gated before ``accept()`` (``_add_ws_route``).
  A2  The auth-exempt prefix match is slash-bounded (no ``startswith`` bleed).
  A3  ``/api/health`` is auth-aware — anonymous callers get a minimal liveness
      body with no vault paths or diagnostics, even with ``?full=true``.

These assert the *new* behaviour, so they only pass against a daemon running
the patched code — restart ``:9000`` (or verify on a leased sandbox member)
before expecting green.

The whole module is skipped when the running daemon has no owner gate active
(local mode / no credentials), because every assertion below presupposes the
gate. Detection is a live anonymous probe of ``/api/apps``: 401 ⇒ gate active.

NOTE on clients: the shared ``api_client`` fixture injects a bearer header, so
anonymous rows MUST use a fresh header-less client — otherwise they false-pass.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

import httpx
import pytest

from helpers import BASE_URL

try:
    import websockets
except ImportError:  # pragma: no cover - websockets is a test dep
    websockets = None

_WS_BASE = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
_WS_SID = "PLAYWRIGHT-TEST-authprobe"


def _auth_token() -> str:
    """Read network.auth_token from emptyos.toml (mirrors conftest)."""
    cfg = Path(__file__).resolve().parents[1] / "emptyos.toml"
    if not cfg.exists():
        return ""
    try:
        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        return str((data.get("network") or {}).get("auth_token") or "")
    except Exception:
        return ""


_TOKEN = _auth_token()


def _network_setting(key: str) -> str:
    cfg = Path(__file__).resolve().parents[1] / "emptyos.toml"
    try:
        with open(cfg, "rb") as f:
            return str((tomllib.load(f).get("network") or {}).get(key) or "")
    except Exception:
        return ""


@pytest.fixture(scope="session")
def gate_active() -> bool:
    """True when the daemon enforces an owner gate. Skip the module otherwise."""
    try:
        r = httpx.get(f"{BASE_URL}/api/apps", timeout=5)  # no auth header
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip("EmptyOS not running on localhost:9000")
    if r.status_code != 401:
        pytest.skip("owner gate not active (local mode / no credentials) — "
                    "Track A fixes are no-ops here")
    return True


@pytest.fixture()
def anon() -> httpx.Client:
    """Header-less client — must NOT inherit the suite-wide bearer."""
    c = httpx.Client(base_url=BASE_URL, timeout=15)
    yield c
    c.close()


@pytest.fixture()
def owner() -> httpx.Client:
    c = httpx.Client(
        base_url=BASE_URL, timeout=15,
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    yield c
    c.close()


# ──────────────────────────────────────────────────────────────────────────
# A2 — slash-bounded exempt-prefix matching
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.api
class TestExemptPrefixBoundary:
    """A public_routes entry must exempt its subtree, never a sibling whose
    name merely *starts with* the same string."""

    def test_declared_public_route_reachable(self, gate_active, anon):
        # welcome declares public_routes = ["/", "/api/programme"]
        r = anon.get("/welcome/api/programme")
        assert r.status_code != 401, "declared public route should bypass auth"

    def test_sibling_with_shared_prefix_is_gated(self, gate_active, anon):
        # radio declares `/api/channels` (a specific sub-path, not `/`), so
        # `/radio/api/channelsZZ` shares the prefix but is neither the route
        # nor a slash-bounded child → must require auth. Under the old
        # unbounded startswith() it leaked through to routing (200/404).
        # Path contains `/api/`, so the gated response is a JSON 401.
        r = anon.get("/radio/api/channelsZZ")
        assert r.status_code == 401, (
            "prefix-sibling must be gated, not exempted by startswith bleed"
        )

    def test_login_sibling_is_gated(self, gate_active, anon):
        # `/login` is an exempt prefix; `/loginxyz` must not inherit the bypass.
        # It has no `/api/` segment, so a gated request redirects to login (302)
        # rather than returning JSON 401 — either is "gated". The bug signal
        # would be 404 (leaked through to routing) or 200.
        r = anon.get("/loginxyz", follow_redirects=False)
        assert r.status_code in (302, 401), (
            "login-prefix sibling must be gated (302 redirect or 401), "
            f"not leaked through to routing; got {r.status_code}"
        )


# ──────────────────────────────────────────────────────────────────────────
# A3 — auth-aware health
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.api
class TestHealthSurfaces:
    def test_anon_health_is_minimal(self, gate_active, anon):
        r = anon.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") in {"ok", "starting"}
        assert "vault_path" not in body, "anon health must not leak vault_path"
        assert "viewer" not in body, "anon health must not leak viewer config"
        assert "capabilities" not in body

    def test_anon_full_health_does_not_leak_diagnostics(self, gate_active, anon):
        # The pre-fix bug: ?full=true reached the deep-diagnostics branch for
        # anyone because /api/health was exempt by exact path (query ignored).
        r = anon.get("/api/health", params={"full": "true"})
        assert r.status_code == 200
        body = r.json()
        assert "capabilities" not in body
        assert "plugins" not in body
        assert "vault_path" not in body

    def test_owner_health_has_vault(self, gate_active, owner):
        r = owner.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        # Owner gets vault info + viewer (frontend note-link bootstrap).
        assert "vault_path" in body

    def test_owner_full_health_has_diagnostics(self, gate_active, owner):
        r = owner.get("/api/health", params={"full": "true"})
        assert r.status_code == 200
        body = r.json()
        assert "capabilities" in body

    # The nav's account menu reads `account` from here: only an owner gets it,
    # and Sign out is offered only for a session there is to end.
    def test_anon_health_has_no_account_menu(self, gate_active, anon):
        assert "account" not in anon.get("/api/health").json()

    def test_owner_health_offers_sign_out_only_for_a_browser_session(self, gate_active):
        configured = _network_setting("sign_out_url")
        with httpx.Client(base_url=BASE_URL, timeout=15,
                          headers={"Authorization": f"Bearer {_TOKEN}"}) as bearer:
            acct = bearer.get("/api/health").json()["account"]
        assert acct["sign_out_url"] == (configured or None), "a bearer caller has no session"
        with httpx.Client(base_url=BASE_URL, timeout=15, cookies={"eos_session": _TOKEN}) as browser:
            acct = browser.get("/api/health").json()["account"]
        assert acct["sign_out_url"] == (configured or "/logout")


# ──────────────────────────────────────────────────────────────────────────
# A1 — app WebSockets are owner-gated before accept()
# ──────────────────────────────────────────────────────────────────────────
def _ws_connect_and_close(url: str) -> None:
    async def go():
        async with websockets.connect(url, open_timeout=8) as ws:
            await ws.close()

    asyncio.run(go())


@pytest.mark.api
@pytest.mark.skipif(websockets is None, reason="websockets not installed")
class TestAppWebSocketAuth:
    def test_anon_agent_ws_rejected(self, gate_active):
        url = f"{_WS_BASE}/agent/ws/{_WS_SID}"
        with pytest.raises(Exception):
            _ws_connect_and_close(url)

    def test_anon_assistant_ws_rejected(self, gate_active):
        url = f"{_WS_BASE}/assistant/ws/{_WS_SID}"
        with pytest.raises(Exception):
            _ws_connect_and_close(url)

    def test_owner_agent_ws_accepted(self, gate_active):
        url = f"{_WS_BASE}/agent/ws/{_WS_SID}?token={_TOKEN}"
        # Should complete the handshake (accept()) without raising.
        _ws_connect_and_close(url)
