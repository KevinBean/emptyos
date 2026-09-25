"""Unit tests for conversation-ingest's proactive backlog nudge.

Pure in-process — no daemon, no kernel. Exercises `backlog_nudge_sweep`
(conversation-ingest-no-backlog-nudge) against a fake app + a stubbed
`discover_imports`, so the dark-flag gate, threshold logic, and
`proactive_notify` call shape are pinned without a live vault or the
proactive delivery gate itself (that gate has its own test coverage).

Run standalone (root conftest skips suites when :9000 is down):
    python -m pytest tests/test_unit_conversation_ingest_backlog_nudge.py --noconftest -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types

from helpers import app_path

_CI = app_path("conversation-ingest")

# Register the package so `app.py`'s `from .store import ...` resolves.
_pkg = types.ModuleType("eos_ci_pkg")
_pkg.__path__ = [str(_CI)]
sys.modules["eos_ci_pkg"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"eos_ci_pkg.{name}", str(_CI / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"eos_ci_pkg.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


app_mod = _load("store")  # load store first so `app`'s relative import resolves
app_mod = _load("app")


def _run(coro):
    return asyncio.run(coro)


class _FakeApp:
    """Minimal stand-in for the bound ConversationIngestApp."""

    def __init__(self, *, flag_on=True, threshold=None, imports_result=None):
        self._flag_on = flag_on
        self._threshold = threshold
        self._imports_result = imports_result if imports_result is not None else {
            "root_ok": True, "imports": [],
        }
        self.notified: list[dict] = []

    def app_config(self, key, default=None):
        if key == "feature.backlog-nudge.enabled":
            return self._flag_on
        return default

    def setting_or_config(self, key, default=None):
        if key == "conversation-ingest.backlog_threshold" and self._threshold is not None:
            return self._threshold
        return default

    def _imports_root(self):
        return "/fake/imports"

    async def proactive_notify(self, kind, text, *, dedup_key=None, link=None, **kw):
        self.notified.append({"kind": kind, "text": text, "dedup_key": dedup_key, "link": link})
        return {"delivered": True, "reason": "ok", "channels": []}


def _sweep(app, imports_result):
    """Run backlog_nudge_sweep with discover_imports stubbed to return
    `imports_result` regardless of the (fake) imports root passed in."""
    real_discover = app_mod.discover_imports
    app_mod.discover_imports = lambda _root: imports_result
    try:
        _run(app_mod.ConversationIngestApp.backlog_nudge_sweep(app))
    finally:
        app_mod.discover_imports = real_discover


class TestBacklogNudgeSweep:
    def test_dark_by_default_never_calls_discover_or_notify(self):
        """The flag must be OFF by default — a fresh install's daily cron
        does nothing until explicitly opted in."""
        app = _FakeApp(flag_on=False)
        called = []
        real_discover = app_mod.discover_imports
        app_mod.discover_imports = lambda _root: called.append(1) or {"root_ok": True, "imports": []}
        try:
            _run(app_mod.ConversationIngestApp.backlog_nudge_sweep(app))
        finally:
            app_mod.discover_imports = real_discover
        assert not called, "discover_imports must not run while the feature flag is off"
        assert app.notified == []

    def test_root_not_ok_is_a_silent_noop(self):
        app = _FakeApp(flag_on=True)
        _sweep(app, {"root_ok": False, "error": "import root not found", "imports": []})
        assert app.notified == []

    def test_below_threshold_no_nudge(self):
        app = _FakeApp(flag_on=True, threshold=20)
        _sweep(app, {"root_ok": True, "imports": [{"key": "claude-export", "pending": 5}]})
        assert app.notified == []

    def test_at_or_above_threshold_nudges(self):
        app = _FakeApp(flag_on=True, threshold=20)
        _sweep(app, {"root_ok": True, "imports": [{"key": "claude-export", "pending": 20}]})
        assert len(app.notified) == 1
        n = app.notified[0]
        assert n["kind"] == "conversation-ingest-backlog"
        assert "20" in n["text"] and "claude-export" in n["text"]
        assert n["dedup_key"] == "conversation-ingest-backlog:claude-export"
        assert n["link"]["href"] == "/conversation-ingest/?import=claude-export"

    def test_each_import_over_threshold_gets_its_own_nudge(self):
        app = _FakeApp(flag_on=True, threshold=10)
        _sweep(app, {"root_ok": True, "imports": [
            {"key": "chatgpt-export", "pending": 15},
            {"key": "claude-export", "pending": 3},
            {"key": "gemini-export", "pending": 40},
        ]})
        keys = {n["dedup_key"] for n in app.notified}
        assert keys == {
            "conversation-ingest-backlog:chatgpt-export",
            "conversation-ingest-backlog:gemini-export",
        }

    def test_default_threshold_is_20_when_unset(self):
        app = _FakeApp(flag_on=True, threshold=None)
        _sweep(app, {"root_ok": True, "imports": [{"key": "x", "pending": 19}]})
        assert app.notified == []
        _sweep(app, {"root_ok": True, "imports": [{"key": "x", "pending": 20}]})
        assert len(app.notified) == 1

    def test_missing_pending_field_treated_as_zero_not_a_crash(self):
        app = _FakeApp(flag_on=True, threshold=1)
        _sweep(app, {"root_ok": True, "imports": [{"key": "no-pending-field"}]})
        assert app.notified == []
