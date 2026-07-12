"""Unit tests for the shared undo actions-log (Phase 3).

No daemon. Fakes a kernel with a verb registry + legacy manifests to prove:

1. lookup_inverse is registry-aware — a verb-registry-migrated app (whose legacy
   [provides.assistant] block is gone) still resolves its inverse. This is the
   regression the shared module fixes.
2. lookup_inverse falls back to legacy provides.assistant.commands.
3. record_action + undo_last round-trip: an action is logged, undo calls the
   declared inverse, and a second undo is a no-op.
"""

from __future__ import annotations

import asyncio
import types

from emptyos.sdk.actions_log import lookup_inverse, record_action, undo_last
from emptyos.sdk.verb_registry import VerbRegistry, parse_verb_entry


def _make_kernel(verb_entries=None, legacy_manifests=None):
    """Fake kernel exposing kernel.apps.get_verbs() + kernel.apps.manifests."""
    entries = []
    for app_id, raw in (verb_entries or []):
        entry, err = parse_verb_entry(raw, app_id, allowed_app_halves={app_id})
        assert err is None, err
        entries.append(entry)
    registry = VerbRegistry(entries)

    manifests = {}
    for app_id, commands in (legacy_manifests or []):
        manifests[app_id] = types.SimpleNamespace(
            provides={"assistant": {"commands": commands}}
        )

    apps = types.SimpleNamespace(get_verbs=lambda: registry, manifests=manifests)
    return types.SimpleNamespace(apps=apps)


def test_lookup_inverse_registry_top_level_key():
    # Migrated app: inverse declared as a top-level key on the verb entry.
    kernel = _make_kernel(verb_entries=[
        ("expense", {"verb": "expense.add", "method": "add", "eligibility": "stable",
                     "surfaces": ["voice"], "inverse": "remove_last"}),
    ])
    assert lookup_inverse(kernel, "expense", "add") == "remove_last"


def test_lookup_inverse_registry_assistant_subtable():
    # inverse declared inside the assistant sub-table (the documented shape).
    kernel = _make_kernel(verb_entries=[
        ("task", {"verb": "task.complete", "method": "complete", "eligibility": "gated",
                  "surfaces": ["assistant"], "args": {"query": "string"},
                  "assistant": {"slash": "/done", "arg": "query", "inverse": "reopen"}}),
    ])
    assert lookup_inverse(kernel, "task", "complete") == "reopen"


def test_lookup_inverse_matches_voice_wrapper_method():
    # The voice surface dispatches to a wrapper method; inverse still resolves
    # when the caller passes the wrapper method name.
    kernel = _make_kernel(verb_entries=[
        ("task", {"verb": "task.add", "method": "add", "eligibility": "stable",
                  "surfaces": ["voice"], "inverse": "remove",
                  "voice": {"method": "voice_add_task"}}),
    ])
    assert lookup_inverse(kernel, "task", "voice_add_task") == "remove"
    assert lookup_inverse(kernel, "task", "add") == "remove"


def test_lookup_inverse_legacy_fallback():
    # No registry entry — legacy provides.assistant.commands still works.
    kernel = _make_kernel(legacy_manifests=[
        ("journal", [{"method": "add_entry", "inverse": "delete_entry"}]),
    ])
    assert lookup_inverse(kernel, "journal", "add_entry") == "delete_entry"


def test_lookup_inverse_none_when_undeclared():
    kernel = _make_kernel(verb_entries=[
        ("kb", {"verb": "kb.search", "method": "search", "eligibility": "gated",
                "surfaces": ["voice"]}),
    ])
    assert lookup_inverse(kernel, "kb", "search") == ""
    assert lookup_inverse(kernel, "ghost", "nope") == ""


def test_record_and_undo_round_trip(tmp_path):
    log = tmp_path / "actions_log.jsonl"
    record_action(log, app="task", method="add", args={"text": "x"},
                  result={"id": "t1"}, inverse="remove")

    calls = []

    async def call_app(app, method, **kwargs):
        calls.append((app, method, kwargs))
        return {"ok": True}

    out = asyncio.run(undo_last(log, call_app))
    assert out["ok"] is True
    assert out["undid"]["app"] == "task"
    assert out["undid"]["inverse"] == "remove"
    assert calls == [("task", "remove", {"text": "x"})]

    # Second undo — the entry is now reversed, so nothing left to undo.
    out2 = asyncio.run(undo_last(log, call_app))
    assert out2["ok"] is False
    assert "Nothing to undo" in out2.get("message", "")


def test_undo_skips_irreversible_entries(tmp_path):
    log = tmp_path / "actions_log.jsonl"
    record_action(log, app="journal", method="add_entry", args={}, result="ok", inverse="")
    record_action(log, app="task", method="add", args={"text": "y"}, result="ok", inverse="remove")

    calls = []

    async def call_app(app, method, **kwargs):
        calls.append((app, method))
        return {}

    out = asyncio.run(undo_last(log, call_app))
    assert out["ok"] is True
    assert out["undid"]["app"] == "task"   # the reversible one, not the journal entry
    assert calls == [("task", "remove")]
