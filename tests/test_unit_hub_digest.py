"""Offline unit tests for the hub home-page digest + Next-move logic.

``test_unit_`` prefix → runs offline per conftest (no daemon). Covers the
deterministic command-surface logic added in the home redesign
(docs/HOME-COMPANION-REDESIGN.md):

- ``_overdue_bucket`` — coarse cache-signature bucketing.
- ``_template_next_move`` — the priority ladder + the >20-overdue "triage"
  reframe (the (1) tuning), and the invariant that every move targets a real
  route.
- ``_aura_next_move`` — the route whitelist clamp, the ability gate, the
  signals+titles-never-bodies payload schema (the privacy invariant), the
  include_titles opt-out, and graceful fallback on a bad model reply.

The Aura tests drive the async method with a fake ``self`` (no kernel, no
real model) via ``asyncio.run`` so they stay offline + deterministic.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest

from apps.hub.app import HubApp, _overdue_bucket

# Routes the template ladder + Aura synthesis are allowed to target.
TEMPLATE_ROUTES = {"/task/", "/journal/", "/quick-action/"}
AURA_ROUTES = {"/task/", "/journal/", "/calendar/", "/quick-action/", "/people/", "/assistant/"}
# The exact payload schema sent to the model — locks the privacy invariant:
# adding a note-body field here would (correctly) fail this test.
ALLOWED_PROMPT_KEYS = {
    "time_of_day", "hour", "overdue", "due_today", "journaled_today",
    "journal_streak", "mood", "next_events", "due_titles", "routes",
}


def _digest(*, hour=20, overdue=0, due_today=0, tasks=None, journaled=False,
            streak=3, mood="okay", now=None):
    return {
        "greeting": "Good evening",
        "hour": hour,
        "now": now if now is not None else [],
        "today": {
            "overdue": overdue,
            "due_today": due_today,
            "tasks": tasks if tasks is not None else [],
            "journaled": journaled,
            "streak": streak,
            "mood": mood,
        },
    }


def _move(digest):
    # _template_next_move only reads `digest`; self is unused → pass None.
    return HubApp._template_next_move(None, digest)


# ── _overdue_bucket ────────────────────────────────────────────────────────
class TestOverdueBucket:
    @pytest.mark.parametrize("n,expected", [
        (0, "0"), (-3, "0"), (1, "lo"), (5, "lo"),
        (6, "mid"), (20, "mid"), (21, "hi"), (157, "hi"),
    ])
    def test_buckets(self, n, expected):
        assert _overdue_bucket(n) == expected


# ── _template_next_move — the deterministic ladder ─────────────────────────
class TestTemplateLadder:
    def test_huge_overdue_reframes_as_triage(self):
        """(1) tuning: >20 overdue → 'triage', not a 'clear 157' nag."""
        m = _move(_digest(overdue=157))
        assert m["title"] == "Your task list needs a triage"
        assert "157" in m["why"]
        assert m["tone"] == "overdue"
        assert m["action_href"] == "/task/"

    def test_small_overdue_is_clear_what_slipped(self):
        m = _move(_digest(overdue=3))
        assert m["title"] == "Clear what slipped"
        assert "3 tasks" in m["why"]
        assert m["action_href"] == "/task/"

    def test_one_overdue_is_singular(self):
        m = _move(_digest(overdue=1))
        assert "1 task slipped" in m["why"]

    def test_due_today_when_no_overdue(self):
        m = _move(_digest(overdue=0, due_today=2, now=[{"time": "9:00", "title": "standup"}]))
        assert m["title"] == "Knock out today's tasks"
        assert "2 due today" in m["why"]
        assert "standup" in m["why"]  # next event woven in
        assert m["action_href"] == "/task/"

    def test_evening_unjournaled_closes_the_day(self):
        m = _move(_digest(hour=20, journaled=False, streak=57))
        assert m["title"] == "Close out the day"
        assert m["action_href"] == "/journal/"
        assert "57-day streak" in m["why"]

    def test_morning_unjournaled_plans_the_day(self):
        m = _move(_digest(hour=9, journaled=False))
        assert m["title"] == "Plan the day"
        assert m["action_href"] == "/journal/"

    def test_all_clear_when_nothing_pending(self):
        m = _move(_digest(hour=14, journaled=True, now=[]))
        assert m["title"] == "All clear"
        assert m["action_href"] == "/quick-action/"

    def test_all_clear_mentions_count_when_events_ahead(self):
        m = _move(_digest(hour=14, journaled=True, now=[{"time": "x", "title": "a"}, {"time": "y", "title": "b"}]))
        assert m["title"] == "All clear"
        assert "2 things on deck" in m["why"]

    @pytest.mark.parametrize("digest", [
        _digest(overdue=157), _digest(overdue=2), _digest(due_today=5),
        _digest(hour=21, journaled=False), _digest(hour=8, journaled=False),
        _digest(hour=13, journaled=True),
    ])
    def test_every_move_targets_a_real_route(self, digest):
        """Invariant: the ladder never emits an unroutable action."""
        assert _move(digest)["action_href"] in TEMPLATE_ROUTES


# ── _aura_next_move — gate, clamp, privacy ─────────────────────────────────
class _FakeHub:
    """Minimal stand-in for a HubApp instance — no kernel, no real model."""

    def __init__(self, *, think_return, ability=True, enabled=True, include_titles=True):
        self._think_return = think_return
        self._ability = ability
        self._cfg = {
            "companion.enabled": enabled,
            "companion.include_titles": include_titles,
            "companion.cadence": 60,
        }
        self.captured = {}
        self.kernel = types.SimpleNamespace(
            syslog=types.SimpleNamespace(warn=lambda *a, **k: None)
        )

    def app_config(self, key, default=None):
        return self._cfg.get(key, default)

    async def ability_meets(self, level, domain="text"):
        return self._ability

    async def think(self, prompt, **kw):
        self.captured["prompt"] = prompt
        self.captured["kw"] = kw
        if isinstance(self._think_return, Exception):
            raise self._think_return
        return self._think_return

    def last_provenance(self):
        return {"mode": "cloud", "provider": "test", "model": None}


_GOOD_REPLY = json.dumps({
    "title": "Break the zero",
    "why": "one application beats none",
    "action_label": "Open tasks",
    "action_href": "/task/",
})


def _run_aura(fake, digest):
    return asyncio.run(HubApp._aura_next_move(fake, digest))


class TestAuraNextMove:
    def test_happy_path_returns_aura_move(self):
        fake = _FakeHub(think_return=_GOOD_REPLY)
        m = _run_aura(fake, _digest(overdue=157))
        assert m is not None
        assert m["source"] == "aura"
        assert m["title"] == "Break the zero"
        assert m["action_href"] == "/task/"
        assert m["provenance"]["mode"] == "cloud"
        # tone derives from signals, not the model
        assert m["tone"] == "overdue"

    def test_ability_gate_skips_weak_model(self):
        fake = _FakeHub(think_return=_GOOD_REPLY, ability=False)
        assert _run_aura(fake, _digest()) is None
        assert "prompt" not in fake.captured  # never even called the model

    def test_disabled_returns_none(self):
        fake = _FakeHub(think_return=_GOOD_REPLY, enabled=False)
        assert _run_aura(fake, _digest()) is None

    def test_invented_route_is_clamped(self):
        reply = json.dumps({"title": "x", "why": "y", "action_label": "Go", "action_href": "/evil/"})
        fake = _FakeHub(think_return=reply)
        m = _run_aura(fake, _digest())
        assert m["action_href"] == "/quick-action/"  # clamped to the safe fallback

    def test_returned_route_always_in_whitelist(self):
        fake = _FakeHub(think_return=_GOOD_REPLY)
        m = _run_aura(fake, _digest(overdue=4))
        assert m["action_href"] in AURA_ROUTES

    def test_bad_reply_falls_back_to_none(self):
        fake = _FakeHub(think_return="not json at all")
        assert _run_aura(fake, _digest()) is None

    def test_think_exception_falls_back_to_none(self):
        fake = _FakeHub(think_return=RuntimeError("boom"))
        assert _run_aura(fake, _digest()) is None

    def test_prompt_schema_is_signals_only_no_bodies(self):
        """Privacy invariant: the model prompt carries only the whitelisted
        signal keys — never a note body. A new body field would fail here."""
        fake = _FakeHub(think_return=_GOOD_REPLY)
        _run_aura(fake, _digest(tasks=["buy milk", "call mom"]))
        prompt = fake.captured["prompt"]
        payload = json.loads(prompt.split("Digest:\n", 1)[1])
        assert set(payload.keys()) == ALLOWED_PROMPT_KEYS
        # titles flow through (include_titles default true); bodies never exist
        assert payload["due_titles"] == ["buy milk", "call mom"]

    def test_include_titles_false_sends_counts_not_titles(self):
        fake = _FakeHub(think_return=_GOOD_REPLY, include_titles=False)
        _run_aura(fake, _digest(tasks=["buy milk", "call mom"],
                                now=[{"time": "9", "title": "standup"}]))
        payload = json.loads(fake.captured["prompt"].split("Digest:\n", 1)[1])
        assert payload["due_titles"] == 2          # count, not the strings
        assert payload["next_events"] == 1
