"""Unit tests for the shared capture destination table.

No daemon. Fakes an app exposing `call_app` / `propose_action` / `propose_kb_note`
to prove:

1. The table shapes each destination's args identically for both executors, so
   quick-action (direct) and braindump (proposed) can never drift.
2. Empty items are skipped silently — an empty task is nothing, not an error.
3. `propose_capture` returns the exact `{action_id, type, verb, summary}` row the
   Telegram bridge rehydrates `action_id` from.
4. A failing propose drops one item without sinking the batch.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.capture_routes import (
    CAPTURE_DESTINATIONS,
    apply_capture_direct,
    capture_destination,
    propose_capture,
    shape_capture_args,
)


class _FakeApp:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple] = []

    async def call_app(self, app, method, **kwargs):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(("call_app", app, method, kwargs))
        return {"ok": True}

    async def propose_action(self, *, app, method, args=None):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(("propose_action", app, method, args))
        return {"id": "act-123"}

    async def propose_kb_note(self, **kwargs):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(("propose_kb_note", kwargs))
        return {"id": "act-kb"}


# ── table shape ──────────────────────────────────────────────────

def test_table_covers_exactly_the_three_destinations():
    assert set(CAPTURE_DESTINATIONS) == {"task", "journal", "kb"}


def test_lookup_is_case_and_space_insensitive():
    assert capture_destination(" Task ").key == "task"
    assert capture_destination("JOURNAL").key == "journal"
    assert capture_destination("nope") is None
    assert capture_destination("") is None


def test_journal_uses_the_public_verb_not_the_private_add_entry():
    """quick-action used to reach journal._add_entry directly — a private method."""
    dest = capture_destination("journal")
    assert dest.method == "voice_add_entry"
    assert not dest.method.startswith("_")


# ── arg shaping ──────────────────────────────────────────────────

def test_task_shape_defaults_project_to_inbox():
    assert shape_capture_args("task", {"text": " write it "}) == {
        "text": "write it", "due": "", "project": "inbox",
    }


def test_task_shape_honours_explicit_project_and_due():
    args = shape_capture_args("task", {"text": "x", "due": "2026-01-01", "project": "emptyos"})
    assert args["due"] == "2026-01-01" and args["project"] == "emptyos"


def test_journal_shape_collapses_newlines():
    """journal's single-line guard rejects natural speech split across lines."""
    args = shape_capture_args("journal", {"text": "line one\nline two\n\n  line three "})
    assert args == {"text": "line one line two line three", "mood": "okay"}


def test_journal_shape_lowercases_mood():
    assert shape_capture_args("journal", {"text": "x", "mood": "GOOD"})["mood"] == "good"


def test_kb_shape_defaults_kind_to_lesson():
    args = shape_capture_args("kb", {"title": "  A thing "})
    assert args == {"kind": "lesson", "title": "A thing", "body": "", "topic": ""}


@pytest.mark.parametrize("key,item", [
    ("task", {"text": "   "}),
    ("task", {}),
    ("journal", {"text": "\n \n"}),
    ("kb", {"title": ""}),
])
def test_empty_items_shape_to_none(key, item):
    assert shape_capture_args(key, item) is None


def test_non_dict_item_shapes_to_none():
    assert shape_capture_args("task", "just a string") is None


# ── direct executor (quick-action posture) ───────────────────────

@pytest.mark.asyncio
async def test_apply_direct_calls_the_tabled_app_and_method():
    app = _FakeApp()
    r = await apply_capture_direct(app, "task", {"text": "do it"})
    assert r["ok"] is True and r["verb"] == "task.add"
    kind, target_app, method, kwargs = app.calls[0]
    assert (kind, target_app, method) == ("call_app", "task", "add")
    assert kwargs["text"] == "do it"


@pytest.mark.asyncio
async def test_apply_direct_reports_unknown_destination():
    assert "error" in await apply_capture_direct(_FakeApp(), "nope", {"text": "x"})


@pytest.mark.asyncio
async def test_apply_direct_reports_empty_capture():
    assert "error" in await apply_capture_direct(_FakeApp(), "task", {"text": " "})


@pytest.mark.asyncio
async def test_apply_direct_wraps_a_raised_exception():
    r = await apply_capture_direct(_FakeApp(fail=True), "task", {"text": "x"})
    assert "error" in r and "RuntimeError" in r["error"]


@pytest.mark.asyncio
async def test_apply_direct_surfaces_a_declining_app():
    class _Decliner(_FakeApp):
        async def call_app(self, app, method, **kwargs):
            return {"error": "no thanks"}

    r = await apply_capture_direct(_Decliner(), "task", {"text": "x"})
    assert r["error"] == "task declined: no thanks"


# ── propose executor (braindump posture) ─────────────────────────

@pytest.mark.asyncio
async def test_propose_returns_the_ui_row_the_telegram_bridge_reads():
    app = _FakeApp()
    row = await propose_capture(app, "task", {"text": "ship it"})
    assert row == {"action_id": "act-123", "type": "task",
                   "verb": "task.add", "summary": "ship it"}


@pytest.mark.asyncio
async def test_propose_kb_routes_through_propose_kb_note_with_source():
    app = _FakeApp()
    row = await propose_capture(app, "kb", {"title": "Lesson"}, source="braindump")
    assert row["action_id"] == "act-kb" and row["verb"] == "kb.create_note"
    assert row["summary"] == "Lesson"          # kb's label field is `title`
    kind, kwargs = app.calls[0]
    assert kind == "propose_kb_note" and kwargs["source"] == "braindump"


@pytest.mark.asyncio
async def test_propose_summary_is_truncated_to_120_chars():
    row = await propose_capture(_FakeApp(), "task", {"text": "x" * 500})
    assert len(row["summary"]) == 120


@pytest.mark.asyncio
async def test_propose_skips_empty_and_unknown_without_raising():
    app = _FakeApp()
    assert await propose_capture(app, "task", {"text": ""}) is None
    assert await propose_capture(app, "nope", {"text": "x"}) is None
    assert app.calls == []


@pytest.mark.asyncio
async def test_a_failing_propose_drops_one_item_not_the_batch():
    """One bad item never sinks the rest of a brain dump."""
    assert await propose_capture(_FakeApp(fail=True), "task", {"text": "x"}) is None


@pytest.mark.asyncio
async def test_both_executors_shape_identical_args():
    """The whole point of the table: direct and proposed args can never drift."""
    item = {"text": "meeting notes\nsecond line", "mood": "TIRED"}
    direct, proposed = _FakeApp(), _FakeApp()
    await apply_capture_direct(direct, "journal", item)
    await propose_capture(proposed, "journal", item)
    _, _, _, direct_kwargs = direct.calls[0]
    _, _, _, proposed_args = proposed.calls[0]
    assert direct_kwargs == proposed_args
