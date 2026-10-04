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

from emptyos.sdk.actions_log import (
    lookup_inverse,
    record_action,
    undo_entry,
    undo_last,
    verb_for_method,
)
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


def test_a_voice_wrapper_does_not_borrow_the_canonical_inverse():
    # The wrapper returns a different shape from `add`, so `remove`'s arguments
    # would not fit it (T3 review): only a voice-table inverse reverses it.
    # No live manifest relied on the old borrow (checked 2026-09-30).
    kernel = _make_kernel(verb_entries=[
        ("task", {"verb": "task.add", "method": "add", "eligibility": "stable",
                  "surfaces": ["voice"], "inverse": "remove",
                  "voice": {"method": "voice_add_task"}}),
    ])
    assert lookup_inverse(kernel, "task", "voice_add_task") == ""
    assert lookup_inverse(kernel, "task", "add") == "remove"


def test_a_wrapper_result_without_undo_args_is_not_undoable():
    # A duplicate skipped / nothing added: recording the inverse would give an
    # Undo that can only fail — and block undo_last for every older action.
    kernel = _make_kernel(verb_entries=[
        ("task", {"verb": "task.add", "method": "add", "eligibility": "stable",
                  "surfaces": ["voice"],
                  "voice": {"method": "voice_add_task", "inverse": "voice_undo_add"}}),
    ])
    assert lookup_inverse(kernel, "task", "voice_add_task") == "voice_undo_add"
    assert lookup_inverse(kernel, "task", "voice_add_task",
                          {"say": "Already added it"}) == ""
    assert lookup_inverse(kernel, "task", "voice_add_task",
                          {"say": "Added", "undo_args": {"task_line": "- [ ] x"}}) == "voice_undo_add"


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


# ── Undo one specific action (the phone Undo button, tg-life-surface T3) ──


def _entries(log):
    import json as _json
    return [_json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_voice_subtable_inverse_wins_for_the_wrapper_only():
    kernel = _make_kernel(verb_entries=[
        ("task", {"verb": "task.add", "method": "add", "eligibility": "stable",
                  "surfaces": ["voice"],
                  "voice": {"method": "voice_add_task", "inverse": "voice_undo_add"}}),
    ])
    assert lookup_inverse(kernel, "task", "voice_add_task") == "voice_undo_add"
    # The canonical method returns a Task, not undo_args — it must not borrow
    # the wrapper's inverse, or its Undo would be offered and then fail.
    assert lookup_inverse(kernel, "task", "add") == ""
    assert verb_for_method(kernel, "task", "voice_add_task") == "task.add"
    assert verb_for_method(kernel, "task", "nope") == ""


def test_record_keeps_undo_args_verbatim_and_ids_the_entry(tmp_path):
    log = tmp_path / "actions.jsonl"
    line = "- [ ] " + "x" * 600
    a = record_action(log, app="task", method="voice_add_task", args={"text": "x"},
                      result={"say": "Added", "undo_args": {"task_line": line}},
                      inverse="voice_undo_add")
    b = record_action(log, app="task", method="voice_add_task", args={}, result="ok", inverse="")
    assert a["id"] and b["id"] and a["id"] != b["id"]
    # result is truncated to 500 chars; the undo args must not be.
    assert _entries(log)[0]["undo_args"]["task_line"] == line
    assert "undo_args" not in _entries(log)[1]


def test_undo_entry_reverses_that_entry_with_its_undo_args(tmp_path):
    log = tmp_path / "actions.jsonl"
    first = record_action(log, app="task", method="voice_add_task", args={"text": "a"},
                          result={"undo_args": {"task_line": "- [ ] a"}}, inverse="voice_undo_add")
    record_action(log, app="task", method="voice_add_task", args={"text": "b"},
                  result={"undo_args": {"task_line": "- [ ] b"}}, inverse="voice_undo_add")
    calls = []

    async def call_app(app, method, **kw):
        calls.append((app, method, kw))
        return {"say": "Removed"}

    out = asyncio.run(undo_entry(log, call_app, first["id"]))
    assert out["ok"] is True
    # The first action, not the most recent one, and via undo_args not args.
    assert calls == [("task", "voice_undo_add", {"task_line": "- [ ] a"})]
    rows = _entries(log)
    assert rows[0]["reversed"] is True and "reversing" not in rows[0]
    assert rows[1]["reversed"] is False


def test_undo_entry_double_tap_runs_the_inverse_once(tmp_path):
    log = tmp_path / "actions.jsonl"
    e = record_action(log, app="task", method="m", args={}, result={}, inverse="inv")
    calls = []
    gate = asyncio.Event()

    async def call_app(app, method, **kw):
        calls.append(method)
        if len(calls) == 1:
            await gate.wait()      # hold the first tap mid-inverse; a second
        return {}                  # call returns at once, so a missing claim fails, not hangs

    async def run():
        t1 = asyncio.create_task(undo_entry(log, call_app, e["id"]))
        await asyncio.sleep(0)     # t1 claims and awaits the inverse
        second = await undo_entry(log, call_app, e["id"])
        gate.set()
        return await t1, second

    first, second = asyncio.run(run())
    assert first["ok"] is True
    assert second["ok"] is False and second["in_progress"] is True
    assert calls == ["inv"]
    again = asyncio.run(undo_entry(log, call_app, e["id"]))
    assert again["already"] is True and again["message"] == "Already undone."


def test_undo_entry_refuses_an_expired_entry_without_calling(tmp_path):
    import json as _json
    log = tmp_path / "actions.jsonl"
    e = record_action(log, app="task", method="m", args={}, result={}, inverse="inv")
    row = _entries(log)[0]
    row["ts"] = "2020-01-01T00:00:00+00:00"
    log.write_text(_json.dumps(row) + "\n", encoding="utf-8")
    calls = []

    async def call_app(app, method, **kw):
        calls.append(method)
        return {}

    out = asyncio.run(undo_entry(log, call_app, e["id"], max_age_s=86400))
    assert out["ok"] is False and out["expired"] is True
    assert calls == []
    # max_age_s=0 means no limit.
    assert asyncio.run(undo_entry(log, call_app, e["id"]))["ok"] is True


def test_a_refused_inverse_leaves_the_action_undoable(tmp_path):
    log = tmp_path / "actions.jsonl"
    e = record_action(log, app="task", method="m", args={}, result={}, inverse="inv")
    answers = [{"error": "that task changed"}, {"say": "Removed"}]

    async def call_app(app, method, **kw):
        return answers.pop(0)

    out = asyncio.run(undo_entry(log, call_app, e["id"]))
    assert out["ok"] is False and "changed" in out["error"]
    row = _entries(log)[0]
    assert row["reversed"] is False and "reversing" not in row
    assert asyncio.run(undo_entry(log, call_app, e["id"]))["ok"] is True


def test_an_action_logged_during_the_inverse_is_not_lost(tmp_path):
    log = tmp_path / "actions.jsonl"
    e = record_action(log, app="task", method="m", args={}, result={}, inverse="inv")

    async def call_app(app, method, **kw):
        # Another action lands while the inverse is awaited.
        record_action(log, app="expense", method="add", args={}, result={}, inverse="")
        return {}

    assert asyncio.run(undo_entry(log, call_app, e["id"]))["ok"] is True
    rows = _entries(log)
    assert [r["app"] for r in rows] == ["task", "expense"]
    assert rows[0]["reversed"] is True


def test_undo_last_uses_undo_args_and_skips_an_entry_mid_reversal(tmp_path):
    import json as _json
    log = tmp_path / "actions.jsonl"
    record_action(log, app="task", method="m", args={"text": "old"},
                  result={"undo_args": {"task_line": "- [ ] old"}}, inverse="inv")
    record_action(log, app="task", method="m", args={}, result={}, inverse="inv")
    rows = _entries(log)
    rows[1]["reversing"] = "2026-09-30T00:00:00+00:00"
    log.write_text("\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    calls = []

    async def call_app(app, method, **kw):
        calls.append(kw)
        return {}

    assert asyncio.run(undo_last(log, call_app))["ok"] is True
    assert calls == [{"task_line": "- [ ] old"}]


def test_a_cancelled_undo_releases_its_claim(tmp_path):
    log = tmp_path / "actions.jsonl"
    e = record_action(log, app="task", method="m", args={}, result={}, inverse="inv")

    async def call_app(app, method, **kw):
        raise asyncio.CancelledError

    import pytest as _pytest
    with _pytest.raises(asyncio.CancelledError):
        asyncio.run(undo_entry(log, call_app, e["id"]))
    assert "reversing" not in _entries(log)[0]

    async def ok(app, method, **kw):
        return {}

    assert asyncio.run(undo_entry(log, ok, e["id"]))["ok"] is True
