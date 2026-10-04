"""Operator-vs-user trust posture — the route policy, config resolution, and
the vault-confinement of read/write, all unit-tested without a daemon.

These pin the P1 hardening (docs/AUTH.md § Operator vs user), at TWO layers:

- the pure predicate/data layer — `is_operator_route`, `normalise_path`,
  `is_within_base`, `Config.trust_web`, the decorator flag; and
- the ENFORCEMENT layer — the C1 core-route middleware returns 403, the C2
  `_add_route` operator flag returns 403, the C3 capability confinement fires
  through `Capability.execute()` (not just the helper), and the C4 settings
  `_refuse_key` gate refuses a non-allowlisted key.

The enforcement tests are the load-bearing half: an earlier version of this file
tested only the predicates, so the actual security behaviour (middleware,
execute-level confinement, settings refusal) could be deleted with the suite
still green — the audits.md § 3 trap ("green because it checks nothing"). Each
enforcement assertion has a mutation partner: delete `register_posture`, gut
`PostureMiddleware.dispatch`, drop the `_enforce_confinement` CALL in
`Capability.execute`, or bypass `_refuse_key`, and a test here goes red.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

ROOT_PATH = Path(__file__).resolve().parents[1]

from emptyos.basepath import is_within_base
from emptyos.kernel.config import Config
from emptyos.posture import OPERATOR_ROUTES, is_operator_route, normalise_path


# ── The route table: operator-only paths are blocked, user paths pass ────────

OPERATOR_PATHS = [
    "/api/apps/hub/rpc/read",
    "/api/apps/hub/rpc/write",
    "/api/cli",
    "/api/delegated-action",
    "/api/apps/hub/export",
    "/api/export-groups/x/build",
    "/v1/models",
    "/v1/chat/completions",
    "/api/tailnet",
    "/api/tailnet/serve/on",
    "/api/health/gpu/free",
    "/api/jobs/test",
    "/api/syslog",
    "/api/plugins",
    "/api/capabilities/full",
    "/api/cloud/approve",
    "/api/cloud/llm-scan",
    "/api/cloud/policy",
    "/api/vault-map",
    "/api/vault-map/rescan",
]

USER_PATHS = [
    "/api/apps",
    "/api/apps/hub",
    "/api/apps/sections",
    "/api/health",
    "/api/demo/status",
    "/api/think-status",
    "/api/jobs",
    "/api/shortcuts",
    "/api/cloud/consent",       # per-visitor BYOK, NOT the operator policy
    "/api/capabilities",        # the light read; /full is operator
    "/api/vault/file",
    "/ws",
    "/hub/",
    "/api/pluginsX",            # not /api/plugins — must not false-match
]


@pytest.mark.parametrize("path", OPERATOR_PATHS)
def test_operator_routes_are_blocked(path):
    assert is_operator_route(path), path


@pytest.mark.parametrize("path", USER_PATHS)
def test_user_routes_are_allowed(path):
    assert not is_operator_route(path), path


def test_normalisation_defeats_evasion():
    # Case, doubled slashes, dot and dot-dot segments, trailing slash all
    # normalise to the canonical operator path.
    for evil in [
        "/API/APPS/hub/RPC/read",
        "/api//apps/hub/rpc/read",
        "//api/apps/hub/rpc/read",
        "/api/apps/hub/./rpc/read",
        "/api/x/../apps/hub/rpc/read",
        "/api/apps/hub/rpc/read/",
        "/api/cli?foo=bar",
    ]:
        assert is_operator_route(evil), evil


def test_normalise_path_examples():
    assert normalise_path("/API//x/./y/../z/") == "/api/x/z"
    assert normalise_path("") == "/"
    assert normalise_path("/api/cli?x=1#frag") == "/api/cli"


def test_table_is_nonempty_and_all_absolute():
    # A truncated/emptied table would silently allow everything — pin its shape.
    assert len(OPERATOR_ROUTES) >= 15
    assert all(r.startswith("/") for r in OPERATOR_ROUTES)


# ── Config posture resolution (fail closed) ──────────────────────────────────

def _cfg(**data) -> Config:
    d = tempfile.mkdtemp()
    p = Path(d) / "emptyos.toml"
    p.write_text("")
    c = Config(str(p))
    c._data = data
    return c


def test_explicit_trust_web_wins_over_mode_and_demo():
    assert _cfg(trust={"web": "user"}, network={"mode": "local"}).trust_web == "user"
    assert _cfg(trust={"web": "operator"}, demo={"enabled": True}).trust_web == "operator"


def test_unrecognised_trust_web_reads_as_user():
    # Fail closed: a typo must not widen access (mirrors cloud_locked).
    assert _cfg(trust={"web": "OPER8TOR"}).trust_web == "user"


def test_demo_and_locked_infer_user():
    assert _cfg(demo={"enabled": True}, network={"mode": "public"}).trust_web == "user"
    assert _cfg(cloud={"locked": True}, network={"mode": "private"}).trust_web == "user"


def test_local_and_private_infer_operator():
    assert _cfg(network={"mode": "local"}).trust_web == "operator"
    assert _cfg(network={"mode": "private"}).trust_web == "operator"
    assert _cfg().trust_web == "operator"  # bare local default


def test_public_unset_is_unresolved_and_refuses_operator():
    c = _cfg(network={"mode": "public"})
    assert c.trust_web == ""           # the boot check turns "" into a refusal
    assert c.web_is_operator is False  # never operator when unresolved


def test_web_is_operator_only_true_for_operator():
    assert _cfg(network={"mode": "local"}).web_is_operator is True
    assert _cfg(demo={"enabled": True}).web_is_operator is False


# ── Vault confinement (basepath.is_within_base) ──────────────────────────────

def test_is_within_base_containment():
    d = tempfile.mkdtemp()
    base = Path(d) / "vault"
    base.mkdir()
    (base / "a.md").write_text("x")
    assert is_within_base("a.md", str(base))                       # relative inside
    assert is_within_base(str(base / "sub" / "b.md"), str(base))   # absolute inside, non-existent
    assert not is_within_base("../escape.md", str(base))           # dot-dot escape
    assert not is_within_base("/etc/passwd", str(base))            # absolute outside
    assert not is_within_base(str(base / ".." / "x"), str(base))   # absolute dot-dot
    assert not is_within_base("a.md", "")                          # empty base confines nothing


def test_is_within_base_follows_symlink_out(tmp_path):
    base = tmp_path / "vault"
    base.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    link = base / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform/runner")
    # A symlink inside the vault pointing out of it is NOT within.
    assert not is_within_base(str(link), str(base))


# ── Capability confinement: set on read/write in user posture only ───────────

def test_capability_enforces_confinement_when_set():
    from emptyos.capabilities import Capability

    d = tempfile.mkdtemp()
    base = Path(d) / "vault"
    base.mkdir()
    cap = Capability()
    cap.name = "read"
    cap.confine_to = str(base)

    # A path outside the vault raises before any provider runs.
    with pytest.raises(PermissionError):
        cap._enforce_confinement("/etc/passwd")
    with pytest.raises(PermissionError):
        cap._enforce_confinement("../../etc/passwd")

    # An in-vault path and a None path (human-provider fallback) are fine.
    cap._enforce_confinement("note.md")
    cap._enforce_confinement(None)


def test_confinement_off_by_default():
    from emptyos.capabilities import Capability

    cap = Capability()
    assert cap.confine_to is None
    # With confine_to None, execute() never calls _enforce_confinement — a
    # bad path would reach the provider, which is correct in operator posture.


# ── C3 enforcement: the confinement fires THROUGH Capability.execute ─────────
# (not just the helper in isolation — this pins the CALL SITE at
# Capability.execute / execute_stream, the wiring a refactor would drop.)

@pytest.mark.asyncio
async def test_execute_refuses_out_of_vault_path():
    from emptyos.capabilities import Capability

    d = tempfile.mkdtemp()
    base = Path(d) / "vault"
    base.mkdir()
    cap = Capability()
    cap.name = "read"
    cap.confine_to = str(base)
    # execute() must raise BEFORE reaching any provider (there are none).
    with pytest.raises(PermissionError):
        await cap.execute(path="/etc/passwd")
    with pytest.raises(PermissionError):
        await cap.execute(path="../../etc/passwd")


@pytest.mark.asyncio
async def test_execute_stream_refuses_out_of_vault_path():
    from emptyos.capabilities import Capability

    d = tempfile.mkdtemp()
    base = Path(d) / "vault"
    base.mkdir()
    cap = Capability()
    cap.name = "read"
    cap.confine_to = str(base)
    with pytest.raises(PermissionError):
        async for _ in cap.execute_stream(path="/etc/passwd"):
            break


@pytest.mark.asyncio
async def test_search_refuses_code_and_files_domains_in_user_posture():
    # The code/files domains ignore `path` and reach the repo / OS index, so the
    # generic path-confinement cannot see them — SearchCapability refuses them.
    from emptyos.capabilities.types import SearchCapability

    d = tempfile.mkdtemp()
    base = Path(d) / "vault"
    base.mkdir()
    cap = SearchCapability()
    cap.confine_to = str(base)
    with pytest.raises(PermissionError):
        await cap.execute(query="secret", domain="code")
    with pytest.raises(PermissionError):
        await cap.execute(query="secret", domain="files")
    # and an out-of-vault path on the default domain is refused too
    with pytest.raises(PermissionError):
        await cap.execute(query="secret", path="/etc/passwd")


# ── build_capabilities wires confinement iff user posture ────────────────────

def test_build_capabilities_confines_only_in_user_posture():
    from emptyos.capabilities.setup import build_capabilities

    d = tempfile.mkdtemp()
    vault = Path(d) / "vault"
    vault.mkdir()

    user_cfg = _cfg(notes={"path": str(vault)}, demo={"enabled": True})
    reg = build_capabilities(user_cfg)
    assert reg.get("read").confine_to == str(vault)
    assert reg.get("write").confine_to == str(vault)

    op_cfg = _cfg(notes={"path": str(vault)}, network={"mode": "local"})
    reg2 = build_capabilities(op_cfg)
    assert reg2.get("read").confine_to is None
    assert reg2.get("write").confine_to is None


# ── The decorator flag rides through to the route metadata ───────────────────

def test_web_route_operator_flag():
    from emptyos.sdk.decorators import web_route

    @web_route("POST", "/api/network", operator=True)
    def op_route(self, request):  # pragma: no cover - metadata only
        return {}

    @web_route("GET", "/api/config")
    def plain_route(self, request):  # pragma: no cover - metadata only
        return {}

    assert op_route._eos_web["operator"] is True
    assert plain_route._eos_web["operator"] is False


# ── C1 enforcement: PostureMiddleware returns 403 on operator routes ─────────

import types as _types  # noqa: E402


def _fake_kernel(web_is_operator: bool):
    return _types.SimpleNamespace(
        config=_types.SimpleNamespace(web_is_operator=web_is_operator)
    )


def _app_with_posture(web_is_operator: bool):
    from fastapi import FastAPI

    from emptyos.web.routes_auth import register_posture

    app = FastAPI()

    @app.post("/api/cli")           # an OPERATOR_ROUTES entry
    async def cli():
        return {"ok": True}

    @app.get("/api/health")         # a user-ok route
    async def health():
        return {"ok": True}

    register_posture(app, _fake_kernel(web_is_operator))
    return app


def test_create_server_actually_registers_posture():
    # The middleware tests above prove register_posture WORKS; this pins that
    # emptyos/web/server.py's create_server CALLS it (uncommented) — the wiring a
    # refactor would silently drop, which no behavioural test of the helper can
    # see. AST, so a comment mentioning register_posture cannot satisfy it.
    import ast

    src = (ROOT_PATH / "emptyos" / "web" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "register_posture"
        for n in ast.walk(tree)
    )
    assert called, "create_server must call register_posture(server, kernel)"


def test_middleware_blocks_operator_route_in_user_posture():
    from starlette.testclient import TestClient

    client = TestClient(_app_with_posture(web_is_operator=False))
    assert client.post("/api/cli").status_code == 403          # operator → blocked
    assert client.get("/api/health").status_code == 200        # user → allowed


def test_middleware_allows_operator_route_in_operator_posture():
    from starlette.testclient import TestClient

    client = TestClient(_app_with_posture(web_is_operator=True))
    assert client.post("/api/cli").status_code == 200          # operator posture → through
    assert client.get("/api/health").status_code == 200


# ── C2 enforcement: _add_route(operator_only=True) returns 403 ───────────────

def test_add_route_operator_only_returns_403():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from emptyos.web.server import _add_route

    app = FastAPI()

    async def handler(request):
        return {"ran": True}

    _add_route(app, "POST", "/settings/api/network", handler, "settings", operator_only=True)
    _add_route(app, "POST", "/settings/api/set", handler, "settings", operator_only=False)

    client = TestClient(app)
    assert client.post("/settings/api/network").status_code == 403   # gated
    r = client.post("/settings/api/set")
    assert r.status_code == 200 and r.json() == {"ran": True}         # runs


# ── C4 enforcement: settings _refuse_key allowlists user keys ────────────────

def _settings_app(web_is_operator: bool, cloud_locked: bool = True):
    """A SettingsApp with just enough wired to exercise _refuse_key."""
    import importlib
    mod = importlib.import_module("apps.public.core.settings.app")
    app = mod.SettingsApp.__new__(mod.SettingsApp)
    app.kernel = _types.SimpleNamespace(
        config=_types.SimpleNamespace(
            web_is_operator=web_is_operator, cloud_locked=cloud_locked
        ),
        apps=_types.SimpleNamespace(manifests={}),
        note_scope=None,
        speech_guard=None,
    )
    return app


def test_settings_refuses_dangerous_keys_in_user_posture():
    app = _settings_app(web_is_operator=False, cloud_locked=True)
    # Config/posture-shaping keys never appear in the schema, so they are refused.
    assert app._refuse_key("trust.web")
    assert app._refuse_key("cloud.consent")
    assert app._refuse_key("network.mode")
    assert app._refuse_key("cloud.llm_scan.mode")
    # think.* refused on a locked build (both by _operator_key and the allowlist).
    assert app._refuse_key("think.default")
    # A declared user-preference key IS allowed.
    assert app._refuse_key("system.theme") is None
    assert app._refuse_key("user.name") is None


def test_settings_allows_everything_in_operator_posture():
    app = _settings_app(web_is_operator=True, cloud_locked=False)
    # Operator posture: no allowlist. Only the think-lock could refuse, and it's
    # off when the build isn't locked.
    assert app._refuse_key("trust.web") is None
    assert app._refuse_key("anything.at.all") is None


def test_home_page_is_operator_only_in_user_posture():
    # os.home decides where "/" lands for every visitor of a shared daemon —
    # one visitor must not be able to move it for everyone (hostile review,
    # 2026-10-03). The operator still sets it.
    assert _settings_app(web_is_operator=False, cloud_locked=False)._refuse_key("os.home")
    assert _settings_app(web_is_operator=True, cloud_locked=False)._refuse_key("os.home") is None
    # the filter copies the section — the class-level schema is not mutated
    import importlib
    mod = importlib.import_module("apps.public.core.settings.app")
    keys = {i["key"] for s in mod.SettingsApp.SYSTEM_SETTINGS for i in s["settings"]}
    assert "os.home" in keys


def test_settings_refuses_llm_routing_on_unlocked_user_build():
    # The public demo is user posture but NOT [cloud] locked. The LLM Routing
    # section (think.*, capability.simulate_offline, think.global_timeout) must
    # still be refused there — on a shared demo daemon one visitor setting
    # `capability.simulate_offline = all` would break AI for everyone. Keying the
    # drop on cloud_locked alone left that DoS open (hostile review, 2026-09-30).
    app = _settings_app(web_is_operator=False, cloud_locked=False)
    assert app._refuse_key("capability.simulate_offline")
    assert app._refuse_key("think.global_timeout")
    assert app._refuse_key("think.default")
    # a genuine user preference is still writable
    assert app._refuse_key("system.theme") is None
