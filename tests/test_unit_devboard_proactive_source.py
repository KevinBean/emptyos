"""Unit tests for devboard's proactive fix-queue-backlog source.

Pure in-process — no daemon, no kernel. Exercises
`proactive_source_fixqueue` (devboard-no-proactive-alert) against a fake app
+ a stubbed `_collect_fixloop`, so the dark-flag gate, threshold logic, and
candidate shape are pinned without a live vault or the proactive delivery
gate itself (that gate has its own coverage).

Run standalone (root conftest skips suites when :9000 is down):
    python -m pytest tests/test_unit_devboard_proactive_source.py --noconftest -v
"""

from __future__ import annotations

import asyncio
import importlib.util

from helpers import app_path

_DB = app_path("devboard")

_spec = importlib.util.spec_from_file_location("eos_devboard_app", str(_DB / "app.py"))
app_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app_mod)


def _run(coro):
    return asyncio.run(coro)


class _FakeApp:
    """Minimal stand-in for the bound DevboardApp."""

    def __init__(self, *, flag_on=True, threshold=None, fixloop=None):
        self._flag_on = flag_on
        self._threshold = threshold
        self._fixloop = fixloop if fixloop is not None else {"queue": None}

    def app_config(self, key, default=None):
        if key == "feature.fixqueue-nudge.enabled":
            return self._flag_on
        return default

    def setting_or_config(self, key, default=None):
        if key == "devboard.fixqueue_nudge_threshold" and self._threshold is not None:
            return self._threshold
        return default

    async def _collect_fixloop(self):
        return self._fixloop


def _source(app):
    return _run(app_mod.DevboardApp.proactive_source_fixqueue(app))


class TestProactiveSourceFixqueue:
    def test_dark_by_default_never_reads_fixloop(self):
        """The flag must be OFF by default — a fresh install's proactive
        scan never even calls _collect_fixloop while it's off."""
        called = []

        class _App(_FakeApp):
            async def _collect_fixloop(self):
                called.append(1)
                return {"queue": {"pending": 999}}

        app = _App(flag_on=False)
        assert _source(app) == []
        assert not called, "_collect_fixloop must not run while the feature flag is off"

    def test_no_queue_source_is_a_noop(self):
        """dogfood-agent not installed / queue read failed -> queue is None."""
        app = _FakeApp(flag_on=True, fixloop={"queue": None})
        assert _source(app) == []

    def test_below_threshold_no_nudge(self):
        app = _FakeApp(flag_on=True, threshold=5,
                        fixloop={"queue": {"pending": 3, "by_kind": {"bug": 3}}})
        assert _source(app) == []

    def test_at_or_above_threshold_nudges(self):
        app = _FakeApp(flag_on=True, threshold=5,
                        fixloop={"queue": {"pending": 5, "by_kind": {"bug": 3, "ui-walk": 2}}})
        out = _source(app)
        assert len(out) == 1
        cand = out[0]
        assert cand["kind"] == "fix-queue-backlog"
        assert "5 fix-prompt(s)" in cand["text"]
        assert "bug" in cand["text"] and "ui-walk" in cand["text"]
        assert cand["dedup_key"].startswith("devboard-fixqueue:")
        assert cand["link"]["href"] == "/devboard/#fixloop"

    def test_default_threshold_is_5_when_unset(self):
        app = _FakeApp(flag_on=True, threshold=None,
                        fixloop={"queue": {"pending": 4, "by_kind": {}}})
        assert _source(app) == []
        app2 = _FakeApp(flag_on=True, threshold=None,
                         fixloop={"queue": {"pending": 5, "by_kind": {}}})
        assert len(_source(app2)) == 1

    def test_missing_pending_field_treated_as_zero_not_a_crash(self):
        app = _FakeApp(flag_on=True, threshold=1, fixloop={"queue": {"by_kind": {}}})
        assert _source(app) == []

    def test_collect_fixloop_exception_fails_soft(self):
        class _App(_FakeApp):
            async def _collect_fixloop(self):
                raise RuntimeError("boom")

        app = _App(flag_on=True, threshold=1)
        assert _source(app) == []

    def test_dedup_key_is_stable_within_a_day(self):
        """Same day, same pending shape -> same dedup_key, so the shared
        proactive gate's 24h TTL actually suppresses repeats."""
        app = _FakeApp(flag_on=True, threshold=1,
                        fixloop={"queue": {"pending": 2, "by_kind": {}}})
        first = _source(app)[0]["dedup_key"]
        second = _source(app)[0]["dedup_key"]
        assert first == second
