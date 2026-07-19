"""Offline system tests: Agent Fleet — 13 use cases.

Acceptance criteria → tests (from .claude/plans/agent-fleet-codex-prompt.md):
  Frozen allowlisted envelope; malformed returns ok:false →
      test_envelope_fixture_drops_unknown_keys, test_malformed_envelopes_never_raise
  Flag off accepts/no-ops and list_all is empty → test_dark_flag_is_inert
  One persisted record per source:session_id → test_hook_persists_one_session_record
  Duplicate/out-of-order events are dropped → test_duplicate_and_out_of_order_are_idempotent
  Blocked emission is edge-triggered → test_blocked_event_emits_once
  TTL transitions unknown → ended and emits ended once → test_ttl_reconciliation
  Sustained blocked sessions route through the proactive gate → test_blocked_alert_uses_proactive_gate_once
  Normalized run row shape → test_list_all_contract_and_phase
  Blocked renders needs-review through Run Center → test_run_center_gather_keeps_blocked_signal
  Detail route backs Run Center capability → test_run_detail
  Minimal page shows the required session columns → test_manifest_and_page_contract
Edge/regression (no AC): test_branch_lookup_is_fail_soft

These tests instantiate the app with a throwaway data directory. They do not
install the app, mutate user config, or touch the live :9000 daemon.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "apps/extension/dev/agent_fleet"
FIXTURES = Path(__file__).resolve().parent / "fixtures/agent_fleet"
ROW_KEYS = {
    "id", "harness", "kind", "title", "target", "status", "phase",
    "branch", "diff_stat", "started", "finished", "scope",
}


def _load_agent_fleet_module():
    parent = sys.modules.setdefault("eos_apps", types.ModuleType("eos_apps"))
    if not hasattr(parent, "__path__"):
        parent.__path__ = []
    package_name = "eos_apps.agent_fleet"
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(APP_DIR)]
        sys.modules[package_name] = package
    for name in ("reducer", "app"):
        full_name = f"{package_name}.{name}"
        if full_name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(full_name, APP_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return sys.modules[f"{package_name}.app"]


def _load_run_center_module():
    path = REPO / "apps/extension/dev/run-center/app.py"
    spec = importlib.util.spec_from_file_location("run_center_agent_fleet_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Config:
    def __init__(self, data_dir: Path, *, enabled: bool = True):
        self.data_dir = data_dir
        self.path = REPO / "emptyos.toml"
        self.values = {
            "apps.agent_fleet.feature.fleet.enabled": enabled,
            "apps.agent_fleet.ttl_minutes": 30,
            "apps.agent_fleet.blocked_alert_minutes": 5,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Request:
    def __init__(self, payload=None, *, raw: bytes | None = None, path_params=None):
        self._raw = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.path_params = path_params or {}

    async def body(self):
        return self._raw


@pytest.fixture
def fleet(tmp_path):
    module = _load_agent_fleet_module()
    config = _Config(tmp_path)
    # A real kernel always has a services registry; model it so the app can use
    # BaseApp.setting() (which reads the settings service) without a shim.
    kernel = types.SimpleNamespace(
        config=config,
        services=types.SimpleNamespace(get_optional=lambda name: None),
    )
    manifest = types.SimpleNamespace(id="agent_fleet")
    app = module.AgentFleetApp(kernel, manifest)
    app.emit = AsyncMock(return_value=None)
    app.proactive_notify = AsyncMock(
        return_value={"delivered": False, "reason": "disabled", "channels": []}
    )
    return app


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _event(event: str, ts: datetime, **overrides) -> dict:
    payload = {
        "source": "codex",
        "session_id": "session-001",
        "event": event,
        "ts": ts.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "cwd": str(REPO),
    }
    payload.update(overrides)
    return payload


async def _flush_background(app) -> None:
    for _ in range(3):
        tasks = list(app.__dict__.get("_fleet_background_tasks", ()))
        if not tasks:
            return
        await __import__("asyncio").gather(*tasks)


@pytest.mark.asyncio
async def test_envelope_fixture_drops_unknown_keys(fleet):
    module = _load_agent_fleet_module()
    raw = _fixture("claude_session_start.json")
    clean, error = module.normalize_hook_envelope(raw)
    assert error is None
    assert set(clean) <= module._ALLOWED_FIELDS
    assert "prompt" not in clean and "_fixture_kind" not in clean
    assert clean["cwd"] == "D:/emptyos"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"source": "claude"},
        {"source": "other", "session_id": "s", "event": "Stop", "ts": "2026-07-17T00:00:00Z"},
        {"source": [], "session_id": "s", "event": "Stop", "ts": "2026-07-17T00:00:00Z"},
    ],
)
async def test_malformed_envelopes_never_raise(fleet, payload):
    result = await fleet.api_hook(_Request(payload))
    assert result["ok"] is False
    assert isinstance(result["error"], str) and result["error"]


@pytest.mark.asyncio
async def test_dark_flag_is_inert(fleet, tmp_path):
    fleet.kernel.config.values["apps.agent_fleet.feature.fleet.enabled"] = False
    result = await fleet.api_hook(_Request(_fixture("claude_session_start.json")))
    assert result == {"ok": True, "enabled": False}
    assert await fleet.list_all() == []
    assert not (tmp_path / "apps/agent_fleet/runs").exists()


@pytest.mark.asyncio
async def test_hook_persists_one_session_record(fleet):
    start = _fixture("claude_session_start.json")
    result = await fleet.api_hook(_Request(start))
    assert result == {"ok": True, "state": "idle"}
    sessions = await fleet.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "claude:claude-synthetic-001"
    assert sessions[0]["last_event"] == "SessionStart"
    assert "prompt" not in sessions[0]


@pytest.mark.asyncio
async def test_duplicate_and_out_of_order_are_idempotent(fleet):
    base = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    current = _event("UserPromptSubmit", base)
    assert (await fleet.api_hook(_Request(current)))["state"] == "working"
    assert (await fleet.api_hook(_Request(current)))["ignored"] == "duplicate"
    older = _event("SessionEnd", base - timedelta(seconds=1))
    assert (await fleet.api_hook(_Request(older)))["ignored"] == "out_of_order"
    assert (await fleet.list_sessions())[0]["status"] == "working"


@pytest.mark.asyncio
async def test_blocked_event_emits_once(fleet):
    payload = _fixture("codex_permission_request.json")
    await fleet.api_hook(_Request(payload))
    repeat_edge = dict(payload, event="Notification", notification_type="permission_prompt", ts="2026-07-17T09:42:03Z")
    await fleet.api_hook(_Request(repeat_edge))
    await _flush_background(fleet)
    assert fleet.emit.await_count == 1
    assert fleet.emit.await_args.args[0] == "agent_fleet:blocked"


@pytest.mark.asyncio
async def test_ttl_reconciliation(fleet):
    fleet.kernel.config.values["apps.agent_fleet.ttl_minutes"] = 1
    start = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    await fleet.api_hook(_Request(_event("SessionStart", start)))
    result = await fleet.ttl_sweep(now=start + timedelta(minutes=2))
    await _flush_background(fleet)
    assert [item["state"] for item in result["transitions"]] == ["unknown", "ended"]
    assert (await fleet.list_sessions())[0]["status"] == "ended"
    ended_calls = [call for call in fleet.emit.await_args_list if call.args[0] == "agent_fleet:ended"]
    assert len(ended_calls) == 1


@pytest.mark.asyncio
async def test_blocked_alert_uses_proactive_gate_once(fleet):
    start = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    await fleet.api_hook(_Request(_event("PermissionRequest", start)))
    first = await fleet.ttl_sweep(now=start + timedelta(minutes=6))
    await _flush_background(fleet)
    second = await fleet.ttl_sweep(now=start + timedelta(minutes=7))
    await _flush_background(fleet)
    assert first["alerts"] == 1 and second["alerts"] == 0
    assert fleet.proactive_notify.await_count == 1
    assert fleet.proactive_notify.await_args.args[0] == "agent-fleet-blocked"


@pytest.mark.asyncio
async def test_list_all_contract_and_phase(fleet):
    payload = _fixture("codex_permission_request.json")
    await fleet.api_hook(_Request(payload))
    row = (await fleet.list_all())[0]
    assert set(row) == ROW_KEYS
    assert row["harness"] == "agent_fleet" and row["kind"] == "session"
    assert row["status"] == "blocked" and row["phase"] == "needs-review"
    assert row["scope"] == "D:/emptyos" and row["target"] == "emptyos"


@pytest.mark.asyncio
async def test_run_center_gather_keeps_blocked_signal(fleet):
    await fleet.api_hook(_Request(_fixture("codex_permission_request.json")))
    center_module = _load_run_center_module()
    center = object.__new__(center_module.RunCenterApp)

    async def call_app(harness, method):
        assert method == "list_all"
        return await fleet.list_all() if harness == "agent_fleet" else []

    center.call_app = call_app
    rows, missing = await center._gather()
    fleet_rows = [row for row in rows if row["harness"] == "agent_fleet"]
    assert missing == []
    assert len(fleet_rows) == 1 and fleet_rows[0]["phase"] == "needs-review"
    assert center_module.HARNESS_CAPS["agent_fleet"] == {
        "detail": True, "stream": False, "doctor": False, "actions": []
    }


@pytest.mark.asyncio
async def test_run_detail(fleet):
    await fleet.api_hook(_Request(_fixture("claude_session_start.json")))
    request = _Request({}, path_params={"run_id": "claude:claude-synthetic-001"})
    detail = await fleet.api_run_detail(request)
    assert detail["id"] == "claude:claude-synthetic-001"
    assert detail["status"] == "idle"


def test_manifest_and_page_contract():
    manifest = (APP_DIR / "manifest.toml").read_text(encoding="utf-8")
    page = (APP_DIR / "pages/index.html").read_text(encoding="utf-8")
    assert 'id = "agent_fleet"' in manifest
    assert 'prefix = "/agent_fleet"' in manifest
    # Namespaced: the settings page writes the schema key verbatim into a global
    # store, so a bare "feature.fleet.enabled" / "ttl_minutes" would both collide
    # with other apps and miss what this app reads. See test_settings_key_matches_
    # the_manifest_schema_key.
    assert 'key = "agent_fleet.feature.fleet.enabled"' in manifest
    assert "default = false" in manifest
    for label in ("State", "Source", "Workspace", "Branch", "Last event"):
        assert label in page
    assert "EOS_UI.settingsPanel" in page


@pytest.mark.asyncio
async def test_branch_lookup_is_fail_soft(fleet):
    assert await fleet._branch_for("Z:/definitely/missing/worktree") == ""


# ── declared settings must actually be read (added by review, 2026-07-17) ────
#
# The manifest declares [provides.settings], which renders a live toggle in
# /settings and in the app's gear panel. That toggle writes to the SETTINGS
# SERVICE (data/settings.json, restart-free) using the schema key verbatim --
# the page calls saveSetting(s.key, ...) with no app namespacing. app_config()
# reads a DIFFERENT store (kernel.config / emptyos.toml, boot-loaded). Reading
# only via app_config made the declared toggle a silent no-op.

class _FakeSettings:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _with_settings(app, values):
    app.kernel.services = types.SimpleNamespace(
        get_optional=lambda name: _FakeSettings(values) if name == "settings" else None
    )
    return app


def test_settings_toggle_actually_enables_the_app(fleet):
    """REGRESSION: flipping the declared toggle must enable the app with no
    restart. Before the fix this wrote a value the app never read."""
    fleet.kernel.config.values["apps.agent_fleet.feature.fleet.enabled"] = False
    _with_settings(fleet, {"agent_fleet.feature.fleet.enabled": True})
    assert fleet._enabled() is True


def test_settings_key_matches_the_manifest_schema_key():
    """The key the app reads must be exactly what the settings page writes.
    The page sends the schema key verbatim, so a namespace mismatch here is
    invisible at runtime -- the toggle just does nothing."""
    import tomllib
    manifest = tomllib.load(open(APP_DIR / "manifest.toml", "rb"))
    declared = {s["key"] for s in manifest["provides"]["settings"]["schema"]}
    # what _setting() looks up for each logical key
    looked_up = {f"agent_fleet.{k}" for k in
                 ("feature.fleet.enabled", "ttl_minutes", "blocked_alert_minutes")}
    assert declared == looked_up, (
        f"schema keys {declared} != keys the app reads {looked_up}"
    )


def test_toml_still_works_when_no_settings_service(fleet):
    """The emptyos.toml dark-flag convention (CLAUDE.md rule 15) must survive --
    scripts/check_dark_flags.py reads [apps.agent_fleet] feature.fleet.enabled."""
    fleet.kernel.services = types.SimpleNamespace(get_optional=lambda name: None)
    fleet.kernel.config.values["apps.agent_fleet.feature.fleet.enabled"] = True
    assert fleet._enabled() is True


def test_settings_none_means_unset_not_false(fleet):
    """/settings/api/reset clears a key by writing an explicit null. A sentinel
    that only tested for absence would let one reset permanently shadow the TOML
    value -- the same subtlety _auto_provenance_enabled documents."""
    fleet.kernel.config.values["apps.agent_fleet.feature.fleet.enabled"] = True
    _with_settings(fleet, {"agent_fleet.feature.fleet.enabled": None})
    assert fleet._enabled() is True, "a null settings value must fall through to TOML"


def test_settings_false_overrides_a_true_toml(fleet):
    """The live toggle must be able to turn the app OFF as well as on."""
    fleet.kernel.config.values["apps.agent_fleet.feature.fleet.enabled"] = True
    _with_settings(fleet, {"agent_fleet.feature.fleet.enabled": False})
    assert fleet._enabled() is False


def test_detached_worktree_shows_a_sha_not_the_word_HEAD(fleet, tmp_path, monkeypatch):
    """REGRESSION: every codex session runs in a DETACHED worktree, where
    `git rev-parse --abbrev-ref HEAD` answers the literal "HEAD" -- true, and
    useless in a column whose job is saying where an agent is working. Fall back
    to a tagged short sha.

    monkeypatch, not a hand-rolled restore: `git_run` is a module global and
    _load_agent_fleet_module() caches the module in sys.modules, so a patch that
    leaked would poison every later test in this file.
    """
    calls = []

    def fake_git(args, cwd, *, timeout=60):
        calls.append(args)
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "HEAD\n", ""          # detached
        if args[:2] == ["rev-parse", "--short"]:
            return 0, "a1b2c3d\n", ""
        return -1, "", "unexpected"

    monkeypatch.setattr(_load_agent_fleet_module(), "git_run", fake_git)
    assert fleet._git_branch_sync(str(tmp_path)) == "@a1b2c3d"
    assert ["rev-parse", "--short", "HEAD"] in calls, "never asked for the sha"


def test_attached_branch_is_returned_verbatim(fleet, tmp_path, monkeypatch):
    """The common case must not gain an @ prefix."""
    monkeypatch.setattr(_load_agent_fleet_module(), "git_run",
                        lambda args, cwd, *, timeout=60: (0, "main\n", ""))
    assert fleet._git_branch_sync(str(tmp_path)) == "main"
