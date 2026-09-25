"""Unit: worklog day-card content snippet (.claude/rules/list-card-density.md).

`_summarize()` (apps/public/standard/worklog/reads.py) is what
`GET /worklog/api/recent` returns per day, and the timeline card renders
straight from it — so these pin the server-side half of the fix: the
snippet text a card shows before the user clicks in, not the markup.
"""
import importlib.util
import pathlib
import sys
import types

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP_DIR = _ROOT / "apps/public/standard/worklog"

# reads.py does `from .parser import ...` (relative), so it needs a real
# parent package in sys.modules — the multi-module-apps.md standalone-load
# recipe, minimal form (only `parser` is a real runtime dependency; `.app`
# is TYPE_CHECKING-only and never executes).
_pkg = types.ModuleType("apps")
_pkg.__path__ = [str(_ROOT / "apps")]
sys.modules.setdefault("apps", _pkg)
_wl_pkg = types.ModuleType("apps.worklog")
_wl_pkg.__path__ = [str(_APP_DIR)]
sys.modules.setdefault("apps.worklog", _wl_pkg)


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(f"apps.worklog.{name}", _APP_DIR / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"apps.worklog.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


parser = _load("parser", "parser.py")
reads = _load("reads", "reads.py")


def _parsed(update="", plan="", groups=None):
    groups = groups or []
    return {
        "plan": plan, "update": update, "projects": groups,
        "timesheet": [], "logged_time": [], "hours": 0.0, "notes": [],
        "status_counts": parser.status_counts(groups),
    }


@pytest.mark.parametrize("text,expected", [
    ("- Rejected the deviation", "Rejected the deviation"),
    ("* Wrapped up early", "Wrapped up early"),
    ("**Shortlisted.**", "Shortlisted."),
    ("\n\n  Plain line  \nSecond line", "Plain line"),
    ("", ""),
    ("   ", ""),
])
def test_first_line(text, expected):
    assert reads._first_line(text) == expected


def test_truncate_short_text_unchanged():
    assert reads._truncate("short and sweet") == "short and sweet"


def test_truncate_cuts_at_word_boundary_with_ellipsis():
    long_text = "word " * 40  # far over the 110-char default limit
    out = reads._truncate(long_text.strip())
    assert len(out) <= 111  # 110 + the ellipsis char
    assert out.endswith("…")
    assert not out[:-1].endswith(" ")  # trimmed, not mid-cut on a boundary


def test_card_snippet_prefers_update_over_plan_over_items():
    parsed = _parsed(
        update="- Rejected the deviation",
        plan="- Plan for the day",
        groups=[{"project": "X", "items": [{"text": "an item", "status": "todo"}]}],
    )
    assert reads._card_snippet(parsed) == "Rejected the deviation"


def test_card_snippet_falls_back_to_plan_when_no_update():
    parsed = _parsed(plan="- Plan for the day")
    assert reads._card_snippet(parsed) == "Plan for the day"


def test_card_snippet_falls_back_to_first_item_text():
    parsed = _parsed(groups=[{"project": "X", "items": [{"text": "an item", "status": "todo"}]}])
    assert reads._card_snippet(parsed) == "an item"


def test_card_snippet_empty_when_nothing_to_show():
    assert reads._card_snippet(_parsed()) == ""


def test_summarize_includes_snippet_field():
    day = {
        "date": "2026-08-21", "weekday": "Friday", "employer": "Acme Co",
        "parsed": _parsed(update="- Rejected the deviation"),
    }
    out = reads._summarize(None, day)
    assert out["snippet"] == "Rejected the deviation"
    assert "item_count" in out  # still present — the card's other new field
