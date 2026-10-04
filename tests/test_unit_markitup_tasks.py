"""markitup: accepted comments → tasks (MarkitupApp.send_to_tasks).

Daemon-free: a fake app holds one review in memory and records every
task.add call, so the rules are pinned without a vault — only accepted
comments go, each goes once (including two confirms racing), the preview
creates nothing and is exactly what gets written, a task app failure keeps the
ones that landed, and page-derived titles cannot write extra lines or task
markers into the project note. The soft call is the real BaseApp.try_call_app.
"""

from __future__ import annotations

import asyncio
import copy

from helpers import load_app_module

mk = load_app_module("markitup", "app")

REVIEW = {
    "id": "rv-1", "title": "Acme review", "rubric": "site",
    "shots": [{"id": "s1", "title": "Home"}, {"id": "s2", "title": "Contact"}],
    "comments": [
        {"n": 1, "shot_id": "s1", "title": "Fix the hero copy", "status": "accepted"},
        {"n": 2, "shot_id": "s1", "title": "Keep the logo", "status": "dismissed"},
        {"n": 3, "shot_id": "s2", "title": "Form   has\nno label", "status": "accepted"},
        {"n": 4, "shot_id": "s2", "title": "Still open", "status": "open"},
    ],
}


class _App:
    try_call_app = mk.BaseApp.try_call_app          # the real soft-call helper
    _task_text = mk.MarkitupApp._task_text
    _TASK_MARKERS_RE = mk.MarkitupApp._TASK_MARKERS_RE

    def __init__(self, fail_after=None, doc=None):
        self.doc = copy.deepcopy(doc or REVIEW)
        self.calls, self.saves, self.fail_after = [], 0, fail_after
        self._lock = asyncio.Lock()

    def read_review(self, rid):
        return self.doc if rid == "rv-1" else None

    def _save_review(self, rid, doc):
        self.saves += 1

    def write_lock(self, key):
        return self._lock                        # a real lock: concurrency is observable

    async def call_app(self, app_id, method, **kw):
        assert (app_id, method) == ("task", "add")
        await asyncio.sleep(0)                   # yield, as a real cross-app call does
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("task app is down")
        self.calls.append(kw)
        return {"text": kw["text"]}


def _send(app, **kw):
    return asyncio.run(mk.MarkitupApp.send_to_tasks(app, "rv-1", **kw))


def test_preview_lists_only_accepted_comments_and_creates_nothing():
    app = _App()
    out = _send(app, dry_run=True)
    assert [t["n"] for t in out["tasks"]] == [1, 3]
    assert app.calls == [] and app.saves == 0
    assert {s["n"]: s["reason"] for s in out["skipped"]} == {2: "not accepted", 4: "not accepted"}


def test_task_text_names_the_comment_where_it_is_and_a_way_back():
    text = _send(_App(), dry_run=True)["tasks"][1]["text"]
    assert text == "Form has no label — Acme review · Contact (markitup rv-1, comment 3)"


def test_what_is_written_is_exactly_what_was_previewed():
    app = _App()
    plan = [t["text"] for t in _send(app, dry_run=True)["tasks"]]
    _send(app, dry_run=False)
    assert [c["text"] for c in app.calls] == plan


def test_page_titles_cannot_write_extra_lines_or_task_markers():
    doc = copy.deepcopy(REVIEW)
    doc["title"] = "Acme\n- [ ] injected"
    doc["shots"][0]["title"] = "Home\n## Notes"
    doc["comments"][0]["title"] = "Fix it 📅 2020-01-01 due:2020-02-02 ✅ 2020-01-02 🗨️ room-1"
    text = _send(_App(doc=doc), dry_run=True)["tasks"][0]["text"]
    assert "\n" not in text
    for marker in ("📅", "✅", "🗨", "due:"):
        assert marker not in text, (marker, text)


def test_a_body_only_comment_still_says_what_to_do():
    doc = copy.deepcopy(REVIEW)
    doc["comments"][0].update(title="", body="The contact form\nhas no label")
    assert _send(_App(doc=doc), dry_run=True)["tasks"][0]["text"].startswith("The contact form has no label — ")


def test_the_task_number_is_not_a_task_tag():
    assert "#" not in _send(_App(), dry_run=True)["tasks"][0]["text"]


def test_confirm_creates_one_task_per_accepted_comment_in_the_project():
    app = _App()
    out = _send(app, dry_run=False, project="site-refresh")
    assert [c["project"] for c in app.calls] == ["site-refresh", "site-refresh"]
    assert [s["n"] for s in out["sent"]] == [1, 3]
    marked = {c["n"]: c.get("task_sent") for c in app.doc["comments"]}
    assert marked[1]["project"] == "site-refresh" and marked[1]["at"]
    assert "file" not in marked[1]      # task.add reports a flat path that is not where it wrote
    assert marked[2] is None and marked[4] is None
    assert app.saves == 2               # saved after every task, not once at the end


def test_a_second_send_creates_nothing():
    app = _App()
    _send(app, dry_run=False)
    again = _send(app, dry_run=False)
    assert len(app.calls) == 2
    assert again["sent"] == []
    assert {s["reason"] for s in again["skipped"] if s["n"] in (1, 3)} == {"already sent"}


def test_two_confirms_racing_create_each_task_once():
    app = _App()

    async def both():
        return await asyncio.gather(mk.MarkitupApp.send_to_tasks(app, "rv-1", dry_run=False),
                                    mk.MarkitupApp.send_to_tasks(app, "rv-1", dry_run=False))
    asyncio.run(both())
    assert len(app.calls) == 2, [c["text"] for c in app.calls]


def test_only_the_chosen_comments_are_sent():
    app = _App()
    _send(app, dry_run=False, numbers=[3])
    assert [c["text"].endswith("comment 3)") for c in app.calls] == [True]


def test_an_empty_choice_sends_nothing():
    app = _App()
    out = _send(app, dry_run=False, numbers=[])
    assert app.calls == [] and out["sent"] == []


def test_a_task_app_failure_keeps_what_landed():
    app = _App(fail_after=1)
    out = _send(app, dry_run=False)
    assert "error" in out and [s["n"] for s in out["sent"]] == [1]
    assert out["dry_run"] is False and "skipped" in out          # same shape as a send
    assert app.doc["comments"][0].get("task_sent") and not app.doc["comments"][2].get("task_sent")
    assert app.saves == 1


def test_no_project_means_the_inbox():
    app = _App()
    _send(app, dry_run=False)
    assert app.doc["comments"][0]["task_sent"]["project"] == "inbox"
    assert app.calls[0]["project"] == ""        # the task app's own default routing


# ── the route's own guards ────────────────────────────────────────────

class _Req:
    def __init__(self, body, raises=False):
        self.path_params = {"rid": "rv-1"}
        self._body, self._raises = body, raises

    async def json(self):
        if self._raises:
            raise ValueError("Expecting value")
        return self._body


def _route(body, raises=False):
    app = _App()
    app.send_to_tasks = lambda *a, **k: mk.MarkitupApp.send_to_tasks(app, *a, **k)
    out = asyncio.run(mk.MarkitupApp.api_send_to_tasks(app, _Req(body, raises)))
    return out, app


def test_route_refuses_a_traversal_shaped_project():
    out, app = _route({"dry_run": False, "project": "../outside"})
    assert "error" in out and app.calls == []


def test_route_refuses_a_body_that_is_not_an_object():
    assert "error" in _route([1, 2])[0]
    assert "error" in _route(None, raises=True)[0]
