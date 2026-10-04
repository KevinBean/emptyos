"""Offline unit test for the reactor's journal-breadcrumb write guard.

``test_unit_`` prefix -> runs offline per conftest (no daemon). Every reactor
breadcrumb (cable calcs, mood check-ins, workouts, ...) funnels through the
single ``_journal_ripple`` choke point before landing in the real daily
journal note. Dogfood/UI-walk runs exercise real events against the live
:9000 daemon, so a fixture-tagged entity name (``PLAYWRIGHT-TEST-...``) can
otherwise ripple into the real vault and distort the wellbeing-wheel signal
(.claude/rules/vault-operator.md § Test-fixture leak guard). This pins the
write-side guard added alongside journal/related.py's existing read-side
filter of the same marker.

Drives the async method with a fake ``self`` (no kernel) via ``asyncio.run``,
same pattern as test_unit_hub_digest.py.
"""

from __future__ import annotations

import asyncio
import types

from apps.reactor.app import ReactorApp


def _fake_reactor(record):
    fake = types.SimpleNamespace()

    async def call_app(app_id, method, **kwargs):
        record.append((app_id, method, kwargs))

    fake.call_app = call_app
    return fake


def test_journal_ripple_writes_normal_entries():
    calls = []
    fake = _fake_reactor(calls)
    asyncio.run(ReactorApp._journal_ripple(fake, "🍅", "Pomodoro completed"))
    assert len(calls) == 1
    app_id, method, kwargs = calls[0]
    assert app_id == "journal"
    assert method == "_add_entry"
    assert kwargs["text"] == "🍅 Pomodoro completed"
    assert kwargs["as_ai"] is True


def test_journal_ripple_appends_dimension_hashtag():
    calls = []
    fake = _fake_reactor(calls)
    asyncio.run(ReactorApp._journal_ripple(fake, "👤", "Connected with Sam", dim="social"))
    assert calls[0][2]["text"] == "👤 Connected with Sam #social"


def test_journal_ripple_skips_playwright_test_fixture_names():
    """The write-side guard: a fixture-tagged entity name never reaches the vault."""
    calls = []
    fake = _fake_reactor(calls)
    asyncio.run(
        ReactorApp._journal_ripple(
            fake, "⚡", "Cable rating calculated for PLAYWRIGHT-TEST-cable-run-123"
        )
    )
    assert calls == []


def test_journal_ripple_skips_regardless_of_dim():
    calls = []
    fake = _fake_reactor(calls)
    asyncio.run(
        ReactorApp._journal_ripple(
            fake, "🌟", "PLAYWRIGHT-TEST-interference case solved", dim="intellectual"
        )
    )
    assert calls == []
