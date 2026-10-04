"""worklog — `## Work` must survive a rewrite with every line it started with.

`parse_work` models that section as headings + items and nothing else, so
`parse_work -> render_work` deleted every other line in it. The concrete loss:
`timer_stop` writes `> Logged time: 09:00-17:30` into `## Work`, and 138 of the
227 day notes in this vault carry that hand-written convention — so stopping
the timer and then logging one more item destroyed the day's recorded hours,
with no error and no sign in the UI. Three write paths carried it: `log_work`,
`set_status`, and the import merge in `apply_merge_to_note` (whose own
docstring already names this hazard for Timesheet and Notes).

The invariant these pin: **no line is ever lost**, and a second rewrite is a
no-op. Daemon-free — the parser is pure, and the two app writes run against a
stubbed read/write.
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

_pkg = types.ModuleType("apps"); _pkg.__path__ = [str(_ROOT / "apps")]
sys.modules.setdefault("apps", _pkg)
_wl = types.ModuleType("apps.worklog"); _wl.__path__ = [str(_APP_DIR)]
sys.modules.setdefault("apps.worklog", _wl)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(f"apps.worklog.{name}", _APP_DIR / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"apps.worklog.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


parser = _load("parser", "parser.py")
_load("shared", "shared.py")
portable = _load("portable", "portable.py")

# The shape the corpus actually uses: the timer's line trailing the last group.
WORK = """### Tungkillo
- 🔄 research on the road layer TR value

### Tomago
- ⬜ Submit drawings to EDMS

> Logged time: 09:00 - 17:30 site visit
"""


# ── the parser primitive ────────────────────────────────────────────────────

def test_the_timers_line_survives_a_round_trip():
    groups = parser.parse_work(WORK)
    out = parser.render_work_preserving(WORK, groups)
    assert "> Logged time: 09:00 - 17:30 site visit" in out
    # And it is still readable as hours, which is the point of preserving it.
    assert parser.parse_logged_time(out)[0]["hours"] == 8.5


def test_the_old_render_is_what_dropped_it():
    """Pins the defect itself, so nobody 'simplifies' the preserving call away."""
    assert "Logged time" not in parser.render_work(parser.parse_work(WORK))


def test_evidence_markers_and_prose_survive_too():
    body = WORK + "\n<!-- ai-conversation-evidence:abc123:some-slug -->\nfree prose line\n"
    out = parser.render_work_preserving(body, parser.parse_work(body))
    assert "<!-- ai-conversation-evidence:abc123:some-slug -->" in out
    assert "free prose line" in out


def test_a_preamble_before_the_first_group_survives_and_stays_first():
    body = "a note before any project\n\n" + WORK
    out = parser.render_work_preserving(body, parser.parse_work(body))
    assert out.startswith("a note before any project")


def test_extras_stay_with_their_own_group():
    body = "### A\n- 🔄 one\n\n> A's own note\n\n### B\n- ⬜ two\n"
    out = parser.render_work_preserving(body, parser.parse_work(body))
    # Bracketed on BOTH sides: "before ### B" alone also passes when the note
    # has been hoisted to the top of the section, which is a different bug.
    assert out.index("- 🔄 one") < out.index("> A's own note") < out.index("### B")


def test_a_new_group_lands_before_the_day_level_tail():
    """A project added after the timer stopped must not push the hours mid-note."""
    groups = parser.parse_work(WORK)
    groups.append({"project": "Teebar", "items": [{"text": "new item", "status": "todo"}]})
    out = parser.render_work_preserving(WORK, groups)
    assert out.index("### Teebar") < out.index("> Logged time:")


def test_extras_of_a_vanished_group_are_appended_not_dropped():
    """The invariant is that no line is lost, even when its group is gone.

    The vanished group must NOT be the last one, or its extras are the section
    tail and the orphan path is never exercised.
    """
    body = "### A\n- 🔄 one\n\n> A's own note\n\n### B\n- ⬜ two\n"
    kept = [g for g in parser.parse_work(body) if g["project"] == "B"]
    out = parser.render_work_preserving(body, kept)
    assert "### A" not in out
    assert "> A's own note" in out


@pytest.mark.parametrize("body", [
    WORK,
    # A per-group tail as well as a day-level one: without both, trailing blank
    # lines can accumulate mid-section on every write and no assertion sees it.
    "### A\n- 🔄 one\n\n> A's note\n\n\n### B\n- ⬜ two\n\n> Logged time: 9:00 - 17:00\n",
    "preamble\n\n### A\n- 🔄 one\n",
])
def test_rewriting_twice_changes_nothing(body):
    once = parser.render_work_preserving(body, parser.parse_work(body))
    twice = parser.render_work_preserving(once, parser.parse_work(once))
    assert once == twice


def test_a_section_with_no_extras_renders_exactly_as_before():
    plain = "### A\n- 🔄 one\n- ✅ two\n\n### B\n- ⬜ three\n"
    groups = parser.parse_work(plain)
    assert parser.render_work_preserving(plain, groups) == parser.render_work(groups)


def test_a_heading_with_no_items_keeps_its_heading():
    """9 of the 209 real `## Work` sections have one (`### 54 day safe projects`).
    Dropping it does not lose one line — it silently re-attributes the prose
    beneath it to the group above, which is worse than deleting it."""
    body = "### A\n- 🔄 one\n\n### 54 day safe projects\n"
    out = parser.render_work_preserving(body, parser.parse_work(body))
    assert "### 54 day safe projects" in out


def test_prose_under_an_empty_heading_is_not_reattributed():
    """The 2024-02-06 shape: three paragraphs scoped by their own heading."""
    body = ("### General\n- 🔄 one\n\n> Logged time: 09:00-17:30\n\n"
            "### Sequence-impedance reference extraction\nAs part of the day's work…\n")
    out = parser.render_work_preserving(body, parser.parse_work(body))
    assert out.index("### Sequence-impedance reference extraction") < out.index("As part of")


def test_an_invented_empty_group_is_still_skipped():
    """Only a heading the ORIGINAL carried survives — otherwise a caller that
    builds an empty group would start emitting bare headings."""
    body = "### A\n- 🔄 one\n"
    out = parser.render_work_preserving(body, parser.parse_work(body) + [{"project": "Ghost", "items": []}])
    assert "Ghost" not in out


def test_an_indented_heading_is_not_swallowed():
    """`parse_work` requires a heading at column 0 but allows an indented item.
    A predicate here that used .strip() for both made `  ### X` structural to
    this function and unmodelled to parse_work, so it belonged to neither and
    vanished."""
    body = "### A\n- 🔄 one\n\n  ### Sub topic\n> belongs under Sub topic\n\n### B\n- ⬜ two\n"
    out = parser.render_work_preserving(body, parser.parse_work(body))
    assert "  ### Sub topic" in out
    assert out.index("### Sub topic") < out.index("### B")


def test_two_groups_sharing_a_name_keep_both_sets_of_extras():
    body = "### A\n- 🔄 one\n\n> extras1\n\n### A\n- ⬜ two\n\n> extras2\n\n### B\n- ⬜ three\n"
    once = parser.render_work_preserving(body, parser.parse_work(body))
    assert "> extras1" in once and "> extras2" in once
    # …and the SECOND write must not finish the job the first one started.
    twice = parser.render_work_preserving(once, parser.parse_work(once))
    assert "> extras1" in twice and "> extras2" in twice


@pytest.mark.parametrize("body", [
    WORK,
    "### A\n- 🔄 one\n\n> A's note\n\n### B\n- ⬜ two\n\n> Logged time: 9:00 - 17:00\n",
])
def test_the_first_write_is_already_a_no_op(body):
    """`render(1) == render(2)` cannot see a mutation the FIRST write makes to
    the user's file — and that is the write that touches it. A canonical body
    must come back byte-identical."""
    assert parser.render_work_preserving(body, parser.parse_work(body)) == body.strip()


def test_crlf_does_not_produce_mixed_line_endings():
    body = "### A\r\n- 🔄 one\r\n\r\n> note A\r\n\r\n### B\r\n- ⬜ two\r\n"
    out = parser.render_work_preserving(body, parser.parse_work(body))
    assert "\r" not in out


# ── the three write paths ───────────────────────────────────────────────────

_NOTE = ("---\ndate: 2026-09-07\ntags:\n  - worklog\nemployer: Acme\nauthor: user\n---\n\n"
         "# 2026-09-07 Monday\n\n## Plan\n\n\n## Work\n\n" + WORK)


class _FakeApp:
    def __init__(self, content):
        self.content = content
        self.written = None

    def _daily_path(self, d):
        return pathlib.PurePosixPath("x.md")

    def _daily_lock(self, d):
        class _L:
            async def __aenter__(s): return None
            async def __aexit__(s, *a): return False
        return _L()

    def _default_employer(self): return "Acme"
    def _default_status(self): return "in-progress"
    async def read(self, p): return self.content
    async def write(self, p, c): self.written = c; self.content = c
    async def emit(self, *a, **k): pass

    async def _ensure_daily(self, d, employer=""):
        return self.content


def _app():
    """A WorklogApp with only the spine methods these two writes touch."""
    spec = importlib.util.spec_from_file_location("apps.worklog.app", _APP_DIR / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apps.worklog.app"] = mod
    spec.loader.exec_module(mod)
    return mod.WorklogApp


@pytest.fixture(scope="module")
def cls():
    return _app()


def test_log_work_keeps_the_hours(cls):
    app = _FakeApp(_NOTE)
    asyncio.run(cls.log_work(app, "2026-09-07", "Teebar", "cable pulling question", "todo"))
    assert "> Logged time: 09:00 - 17:30 site visit" in app.written
    assert "- ⬜ cable pulling question" in app.written


def test_set_status_keeps_the_hours(cls):
    app = _FakeApp(_NOTE)
    asyncio.run(cls.set_status(app, "2026-09-07", "Tomago", "Submit drawings to EDMS", "complete"))
    assert "> Logged time: 09:00 - 17:30 site visit" in app.written
    assert "- ✅ Submit drawings to EDMS" in app.written


def test_the_import_merge_keeps_the_hours():
    merged = {"projects": [{"project": "Tungkillo",
                            "items": [{"text": "research on the road layer TR value",
                                       "status": "complete"}]}]}
    receipt = {"created": False, "prose_fields": [], "work_changed": True,
               "timesheet_new": [], "notes_new": []}
    out = portable.apply_merge_to_note(_NOTE, merged, receipt)
    assert "> Logged time: 09:00 - 17:30 site visit" in out


def test_the_created_branch_preserves_too():
    """`created` is really "the read failed", which a transient error can
    produce on a note that DOES exist — so that branch must not be the one
    lossy path left. On a genuinely new note the two renderers agree anyway.

    The old assertion here (`"- ⬜ one" in out`) was vacuous: both renderers
    emit that line, so it could not see which branch ran.
    """
    merged = {"plan": "", "update": "", "timesheet": [], "notes": [],
              "projects": [{"project": "Tungkillo",
                            "items": [{"text": "research on the road layer TR value",
                                       "status": "todo"}]}]}
    receipt = {"created": True, "prose_fields": [], "work_changed": True,
               "timesheet_new": [], "notes_new": []}
    out = portable.apply_merge_to_note(_NOTE, merged, receipt)
    assert "- ⬜ research on the road layer TR value" in out
    assert "> Logged time: 09:00 - 17:30 site visit" in out
