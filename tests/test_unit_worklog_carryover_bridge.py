"""worklog — carryover lookback + the CPEng competency propose→accept bridge.

Two defects/gaps this pins, both found 2026-09-07:

* **Carryover stopped at the most recent day, not the most recent day that
  logged anything.** A day note created but never filled (and a missing day)
  both mean "nothing was recorded", yet `api_carryover` returned on the first
  one it met and reported an empty open set. Measured live: 3 Sep was a blank
  note, 4 Sep had no note, and the four items genuinely open on 2 Sep went
  invisible — which is how they were dropped. The distinction that must NOT be
  lost the other way: a day whose items are all ✅ really is finished, and must
  still carry nothing over.

* **`tag_competency` is the only write in an otherwise read-only module.** It
  appends `#cN` into an item's own text, which is also the match key every
  other write uses, so double-accept must be a no-op rather than `#c11 #c11`.

Daemon-free: both are plain coroutines over stubbed `_all_days` / `read` /
`write` / `think`.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP_DIR = _ROOT / "apps/public/standard/worklog"

_pkg = types.ModuleType("apps")
_pkg.__path__ = [str(_ROOT / "apps")]
sys.modules.setdefault("apps", _pkg)
_wl = types.ModuleType("apps.worklog")
_wl.__path__ = [str(_APP_DIR)]
sys.modules.setdefault("apps.worklog", _wl)


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(f"apps.worklog.{name}", _APP_DIR / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"apps.worklog.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_load("parser", "parser.py")
_load("shared", "shared.py")
reporting = _load("reporting", "reporting.py")
competency = _load("competency", "competency.py")

from datetime import date, timedelta  # noqa: E402


def _day(d: str, projects):
    """A `_all_days` row: projects = [(name, [(text, status), ...]), ...]."""
    return {
        "date": d,
        "weekday": date.fromisoformat(d).strftime("%A"),
        "employer": "Acme",
        "parsed": {"projects": [
            {"project": name, "items": [{"text": t, "status": st} for t, st in items]}
            for name, items in projects
        ]},
    }


def _ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


class _Req:
    def __init__(self, **q):
        self.query_params = q


class _FakeApp:
    def __init__(self, days=None, content="", think=""):
        self._days = days or []
        self.content = content
        self.written = None
        self._think = think
        self.emitted = []

    async def _all_days(self, employer=""):
        return [d for d in self._days if not employer or d["employer"] == employer]

    # tag_competency's write path
    def _daily_path(self, d):
        return pathlib.PurePosixPath(f"60_Worklogs/{d.year}/{d.isoformat()}.md")

    def _daily_lock(self, d):
        class _L:
            async def __aenter__(self_): return None
            async def __aexit__(self_, *a): return False
        return _L()

    async def read(self, path):
        if self.content is None:
            raise FileNotFoundError(path)
        return self.content

    async def write(self, path, content):
        self.written = content
        self.content = content

    async def emit(self, name, payload):
        self.emitted.append((name, payload))

    async def think(self, prompt, **kw):
        self.prompt = prompt
        self.think_kwargs = kw
        return self._think

    def last_provenance(self):
        return {"mode": "local", "provider": "ollama"}


def _run(coro):
    return asyncio.run(coro)


# ── carryover lookback ──────────────────────────────────────────────────────

def test_blank_day_is_skipped_for_the_last_day_that_logged_something():
    """The live 2026-09-07 failure: a blank note between today and the open set."""
    app = _FakeApp(days=[
        _day(_ago(4), []),                                            # blank note
        _day(_ago(5), [("Tomago", [("Submit drawings to EDMS", "todo")])]),
    ])
    out = _run(reporting.api_carryover(app, _Req()))
    assert out["from"] == _ago(5)
    assert [i["text"] for i in out["items"]] == ["Submit drawings to EDMS"]
    assert out["skipped_blank_days"] == [_ago(4)]


def test_a_finished_day_carries_nothing_and_does_not_reach_further_back():
    """The regression the fix must not introduce: all-✅ means done, not blank."""
    app = _FakeApp(days=[
        _day(_ago(1), [("Tomago", [("Submit drawings to EDMS", "complete")])]),
        _day(_ago(5), [("Tomago", [("Something older", "in-progress")])]),
    ])
    out = _run(reporting.api_carryover(app, _Req()))
    assert out["from"] == _ago(1)
    assert out["items"] == []          # finished, so nothing carries
    assert out["skipped_blank_days"] == []


def test_lookback_is_bounded():
    n = reporting.CARRYOVER_LOOKBACK_DAYS
    app = _FakeApp(days=[
        _day(_ago(1), []),
        _day(_ago(n + 1), [("Old", [("stale work", "in-progress")])]),
    ])
    out = _run(reporting.api_carryover(app, _Req()))
    assert out["from"] == ""
    assert out["items"] == []
    # The blank day WAS visited before the bound stopped the scan — an
    # implementation that broke one line early would also return "" here.
    assert out["skipped_blank_days"] == [_ago(1)]
    # An exhausted bound must be distinguishable from "genuinely nothing open",
    # or long leave reads as an empty open set — the same false negative again.
    assert out["lookback_exhausted"] is True


def test_the_day_exactly_on_the_lookback_floor_is_still_reachable():
    """Pins the boundary in both directions; `<` vs `<=` is a silent off-by-one."""
    n = reporting.CARRYOVER_LOOKBACK_DAYS
    on_floor = _FakeApp(days=[_day(_ago(n), [("Old", [("work", "todo")])])])
    assert _run(reporting.api_carryover(on_floor, _Req()))["from"] == _ago(n)
    past = _FakeApp(days=[_day(_ago(n + 1), [("Old", [("work", "todo")])])])
    assert _run(reporting.api_carryover(past, _Req()))["from"] == ""


def test_nothing_open_is_not_reported_as_lookback_exhausted():
    app = _FakeApp(days=[_day(_ago(1), [("Tomago", [("done thing", "complete")])])])
    out = _run(reporting.api_carryover(app, _Req()))
    assert out["items"] == [] and out["lookback_exhausted"] is False


def test_today_and_future_are_never_carried_from():
    app = _FakeApp(days=[
        _day(_ago(-1), [("Future", [("planned item", "todo")])]),
        _day(date.today().isoformat(), [("Now", [("today's item", "in-progress")])]),
        _day(_ago(2), [("Prev", [("yesterday's item", "todo")])]),
    ])
    out = _run(reporting.api_carryover(app, _Req()))
    assert out["from"] == _ago(2)


# ── competency: propose ─────────────────────────────────────────────────────

_DAY_WITH_ONE_TAGGED = _day(_ago(0), [
    ("Teebar", [("Cable pulling bend radius question", "in-progress"),
                ("Already evidenced #c11", "complete")]),
])


def test_suggest_only_considers_untagged_items():
    app = _FakeApp(days=[_DAY_WITH_ONE_TAGGED],
                   think='{"proposals": [{"item": 1, "elements": [13], "why": "AS/NZS practice"}]}')
    out = _run(competency.suggest_competencies(app, date_s=_ago(0)))
    assert out["considered"] == 1
    assert "Already evidenced" not in app.prompt
    assert out["proposals"][0]["elements"] == [13]


def test_suggest_sends_the_taxonomy_as_the_system_prompt_at_a_parsing_temperature():
    """Without this, swapping system= and the user message passes every other
    suggest test — the fake would simply discard the kwargs (CLAUDE.md rule 12)."""
    app = _FakeApp(days=[_DAY_WITH_ONE_TAGGED], think='{"proposals": []}')
    _run(competency.suggest_competencies(app, date_s=_ago(0)))
    assert app.think_kwargs["system"] is competency.COMPETENCY_SUGGEST_SYSTEM
    assert 0.1 <= app.think_kwargs["temperature"] <= 0.3
    # The day's items belong in the user message, never the persona.
    assert "Cable pulling bend radius question" in app.prompt


def test_suggest_addresses_items_by_index_not_by_echoed_text():
    """Two items with the same text in different projects must stay distinct.
    Echoing text back collapsed them, and the tag landed on an arbitrary one."""
    day = _day(_ago(0), [
        ("Teebar", [("Reviewed cable schedule", "in-progress")]),
        ("Tomago", [("Reviewed cable schedule", "in-progress")]),
    ])
    app = _FakeApp(days=[day], think='{"proposals": [{"item": 2, "elements": [13]}]}')
    out = _run(competency.suggest_competencies(app, date_s=_ago(0)))
    assert out["considered"] == 2
    assert len(out["proposals"]) == 1
    assert out["proposals"][0]["project"] == "Tomago"


def test_suggest_drops_hallucinated_items_and_out_of_range_elements():
    app = _FakeApp(days=[_DAY_WITH_ONE_TAGGED],
                   think='{"proposals": ['
                         '{"item": 47, "elements": [11]},'
                         '{"item": 0, "elements": [11]},'
                         '{"item": 1, "elements": [99, 0, 13]}]}')
    out = _run(competency.suggest_competencies(app, date_s=_ago(0)))
    assert len(out["proposals"]) == 1
    assert out["proposals"][0]["elements"] == [13]


def test_suggest_cap_keeps_the_two_elements_the_feature_exists_to_catch():
    """`sorted(elements)[:3]` truncates by element NUMBER, so it drops 11 and 13
    — the two shared.py says can fail the application — in favour of 2 and 6."""
    app = _FakeApp(days=[_DAY_WITH_ONE_TAGGED],
                   think='{"proposals": [{"item": 1, "elements": [2, 6, 11, 13, 14]}]}')
    out = _run(competency.suggest_competencies(app, date_s=_ago(0)))
    got = out["proposals"][0]["elements"]
    assert len(got) == competency.MAX_ELEMENTS_PER_ITEM
    assert 11 in got and 13 in got


def test_suggest_writes_nothing():
    app = _FakeApp(days=[_DAY_WITH_ONE_TAGGED],
                   think='{"proposals": [{"item": 1, "elements": [13]}]}')
    _run(competency.suggest_competencies(app, date_s=_ago(0)))
    assert app.written is None and app.emitted == []


@pytest.mark.parametrize("reply", [
    "I'm afraid I can't do that.",                       # prose — parse raises
    '[{"item": 1, "elements": [13]}]',                   # bare array — .get() would blow up
    '{"proposals": "not a list"}',
    '{"proposals": [null, 3, {"item": "one", "elements": [13]}]}',
])
def test_suggest_survives_every_malformed_model_reply(reply):
    app = _FakeApp(days=[_DAY_WITH_ONE_TAGGED], think=reply)
    out = _run(competency.suggest_competencies(app, date_s=_ago(0)))
    assert out["ok"] is True and out["proposals"] == []


def test_suggest_degrades_in_band_when_the_model_cannot_be_reached():
    """A 429 / provider outage / unapproved consent gate is an ordinary outcome
    for a suggestion, not a server fault. Verified live against a real 429,
    which 500'd before this."""
    class _Boom(_FakeApp):
        async def think(self, prompt, **kw):
            raise RuntimeError("429 Too Many Requests")
    app = _Boom(days=[_DAY_WITH_ONE_TAGGED])
    out = _run(competency.suggest_competencies(app, date_s=_ago(0)))
    assert out["ok"] is False and out["proposals"] == []
    assert "429" in out["error"]
    assert app.written is None


def test_suggest_on_a_day_that_does_not_exist():
    app = _FakeApp(days=[], think='{"proposals": []}')
    out = _run(competency.suggest_competencies(app, date_s="2020-01-01"))
    assert out["ok"] is False and out["proposals"] == []


# ── competency: accept (the write) ──────────────────────────────────────────

_NOTE = """---
date: 2026-09-07
tags:
  - worklog
---

# 2026-09-07 Monday

## Plan


## Work

### Teebar
- 🔄 Cable pulling bend radius question
"""


def test_tag_appends_into_the_item_text():
    app = _FakeApp(content=_NOTE)
    out = _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                         "Cable pulling bend radius question", [13, 11]))
    assert out["ok"] is True
    assert out["elements"] == [11, 13]
    assert "- 🔄 Cable pulling bend radius question #c11 #c13" in app.written
    assert app.emitted[0][0] == "worklog:competency-tagged"


def test_tag_twice_is_a_no_op_not_a_duplicate():
    app = _FakeApp(content=_NOTE)
    _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                   "Cable pulling bend radius question", [13]))
    first, emits_after_first = app.written, len(app.emitted)
    # The item text now carries the tag, so the accept replays against it.
    out = _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                         "Cable pulling bend radius question #c13", [13]))
    assert out.get("unchanged") is True
    assert app.written.count("#c13") == 1
    assert app.written == first
    # A no-op must not write at all: replace_section re-flows blank lines, so
    # writing anyway silently reformats hand-written markdown, and a second
    # event would claim a change that did not happen.
    assert len(app.emitted) == emits_after_first


def test_a_no_op_accept_does_not_reflow_hand_written_markdown():
    note = _NOTE.replace("- 🔄 Cable pulling bend radius question",
                         "- 🔄 Cable pulling bend radius question #c13") + "\n\n## Notes\n\n- kept\n"
    app = _FakeApp(content=note)
    _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                   "Cable pulling bend radius question #c13", [13]))
    assert app.written is None
    assert app.content == note


def test_tag_refuses_a_bare_string_of_digits():
    """CRITICAL: `for n in "13"` iterates CHARACTERS, so a JSON client sending
    elements:"13" wrote #c1 #c3 — two competencies the user never accepted —
    and answered ok:True. In a corpus whose purpose is Chartered evidence, a
    silently wrong element is the worst possible failure."""
    app = _FakeApp(content=_NOTE)
    out = _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                         "Cable pulling bend radius question", "13"))
    assert "error" in out
    assert app.written is None and app.emitted == []


def test_tag_refuses_booleans_which_int_would_happily_coerce():
    app = _FakeApp(content=_NOTE)
    out = _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                         "Cable pulling bend radius question", [True, False]))
    assert "error" in out and app.written is None


def test_tag_cap_keeps_the_focus_elements():
    app = _FakeApp(content=_NOTE)
    out = _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                         "Cable pulling bend radius question",
                                         [2, 6, 11, 13, 14]))
    assert 11 in out["elements"] and 13 in out["elements"]


def test_tag_reaches_an_item_under_no_project_heading():
    """parse_work calls a header-less item "General", so the suggester proposes
    it under that name. If the accept half cannot resolve it, the proposal is a
    dead end that answers "item not found" forever."""
    note = _NOTE.replace("### Teebar\n", "")
    app = _FakeApp(content=note)
    out = _run(competency.tag_competency(app, "2026-09-07", "General",
                                         "Cable pulling bend radius question", [13]))
    assert out["ok"] is True and "#c13" in app.written


@pytest.mark.parametrize("elements", [[], [0], [99], ["nope"], None, "13", {}, 13])
def test_tag_refuses_invalid_elements_without_writing(elements):
    app = _FakeApp(content=_NOTE)
    out = _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                         "Cable pulling bend radius question", elements))
    assert "error" in out and app.written is None


_NOTE_WITH_HOURS = _NOTE + """
> Logged time: 08:30 - 17:00 site visit

### Tomago
- ⬜ Submit drawings to EDMS
"""


def test_tag_preserves_the_timers_logged_time_line():
    """Tagging edits one line rather than re-rendering `## Work`, so anything
    the group model does not carry — the timer's `> Logged time:` blockquote
    included — is untouched. `render_work_preserving` now protects the other
    write paths too (tests/test_unit_worklog_work_preserve.py); this pins that
    the tag path never depended on it."""
    app = _FakeApp(content=_NOTE_WITH_HOURS)
    out = _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                         "Cable pulling bend radius question", [13]))
    assert out["ok"] is True
    assert "> Logged time: 08:30 - 17:00 site visit" in app.written
    assert "### Tomago" in app.written and "Submit drawings to EDMS" in app.written


def test_tag_only_touches_the_matched_project():
    """Two projects can hold the same item text; the wrong one must not move."""
    note = _NOTE + """
### Tomago
- 🔄 Cable pulling bend radius question
"""
    app = _FakeApp(content=note)
    _run(competency.tag_competency(app, "2026-09-07", "Tomago",
                                   "Cable pulling bend radius question", [13]))
    teebar, tomago = app.written.split("### Tomago")
    assert "#c13" not in teebar
    assert "#c13" in tomago


def test_tag_keeps_the_status_emoji():
    app = _FakeApp(content=_NOTE)
    _run(competency.tag_competency(app, "2026-09-07", "Teebar",
                                   "Cable pulling bend radius question", [11]))
    assert "- 🔄 Cable pulling bend radius question #c11" in app.written


def test_tag_refuses_an_item_it_cannot_match():
    app = _FakeApp(content=_NOTE)
    out = _run(competency.tag_competency(app, "2026-09-07", "Teebar", "not in the note", [11]))
    assert out["error"] == "item not found"
    assert app.written is None and app.emitted == []
