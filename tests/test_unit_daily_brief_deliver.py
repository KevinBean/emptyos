r"""A morning brief that never arrives is a page, not a brief.

`daily-brief` generated on a cron, filed a vault note, and then told nobody —
`grep -E "notify|proactive|send\("` across all seven modules returned nothing,
while the `proactive` engine had five other consumers. These pin the delivery
step and, more importantly, the two ways it must not misbehave: it must never
break generation (the brief is already written by the time we deliver), and it
must not push markdown at a channel that may *speak* it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DIR = _ROOT / "apps/public/standard/daily-brief"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        pytest.skip(f"{path.name} not loadable", allow_module_level=True)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_pkg = types.ModuleType("db_under_test")
_pkg.__path__ = [str(_DIR)]
sys.modules["db_under_test"] = _pkg
shared = _load(_DIR / "shared.py", "db_under_test.shared")


# ── the notification line ───────────────────────────────────────────────


class TestNotifyText:
    def test_leads_with_count_and_first_line(self):
        out = shared._notify_text("Grid storage costs fell again.", 5, "en")
        assert out == "Your daily brief is ready — 5 stories. Grid storage costs fell again."

    def test_singular_story(self):
        assert "1 story." in shared._notify_text("", 1, "en")

    def test_chinese_locale(self):
        out = shared._notify_text("电网储能成本继续下降。", 3, "zh")
        assert out.startswith("今日简报已就绪 — 3 条.")
        assert "电网储能成本继续下降。" in out

    def test_unknown_locale_falls_back_to_english(self):
        assert shared._notify_text("x", 2, "de").startswith("Your daily brief is ready")

    @pytest.mark.parametrize("md,expected", [
        ("# Today's brief\n\nBatteries got cheaper.", "Batteries got cheaper."),
        ("- Batteries got cheaper.", "Batteries got cheaper."),
        ("1. Batteries got cheaper.", "Batteries got cheaper."),
        ("**Batteries** got _cheaper_.", "Batteries got cheaper."),
        ("`Batteries` got cheaper.", "Batteries got cheaper."),
        ("[Batteries](https://x.invalid) got cheaper.", "Batteries got cheaper."),
        ("![chart](x.png) Batteries got cheaper.", "chart Batteries got cheaper."),
    ])
    def test_markdown_is_stripped(self, md, expected):
        """The voice channel speaks this — asterisks and URLs must not survive."""
        assert shared._notify_text(md, 4, "en").endswith(expected)

    @pytest.mark.parametrize("md", [
        "> a pull quote",
        "| a | table |",
        "---",
        "```\ncode\n```",
    ])
    def test_non_prose_blocks_are_skipped(self, md):
        out = shared._notify_text(md + "\n\nReal opening line.", 4, "en")
        assert out.endswith("Real opening line.")

    def test_empty_brief_still_says_something(self):
        assert shared._notify_text("", 0, "en") == "Your daily brief is ready — 0 stories."

    def test_whitespace_only_brief(self):
        assert shared._notify_text("\n\n   \n", 2, "en").endswith("2 stories.")

    def test_long_line_is_truncated(self):
        out = shared._notify_text("x" * 500, 3, "en")
        assert out.endswith("…")
        assert len(out) < 220

    def test_heading_only_brief_falls_through_to_its_text(self):
        assert shared._notify_text("## Energy", 1, "en").endswith("Energy")

    def test_title_heading_does_not_shadow_the_prose(self):
        """"Today's brief" says nothing the lead hasn't already said."""
        out = shared._notify_text("# Today's brief\n\nBatteries got cheaper.", 4, "en")
        assert out.endswith("Batteries got cheaper.")


# ── the delivery step ───────────────────────────────────────────────────

app_mod = _load(_DIR / "app.py", "db_under_test.app")


class _AppStub:
    def __init__(self, raises=None, locale="en"):
        self._raises = raises
        self._locale_value = locale
        self.notified = []
        self.activity = []

    def _locale(self):
        return self._locale_value

    def log_activity(self, row):
        self.activity.append(row)

    async def proactive_notify(self, kind, text, **kw):
        if self._raises is not None:
            raise self._raises
        self.notified.append((kind, text, kw))
        return {"delivered": True, "reason": "", "channels": ["notify"]}

    _deliver = app_mod.DailyBriefApp._deliver


_RECORD = {"date": "2026-08-10", "brief_md": "Batteries got cheaper.", "item_count": 5}


def _deliver(stub, record=None):
    return asyncio.run(stub._deliver(record if record is not None else dict(_RECORD)))


class TestDeliver:
    def test_notifies_with_the_daily_brief_kind(self):
        stub = _AppStub()
        _deliver(stub)
        kind, text, kw = stub.notified[0]
        assert kind == "daily-brief"
        assert "5 stories" in text

    def test_dedup_key_is_per_date(self):
        """A manual re-run after the scheduled one must not nudge twice."""
        stub = _AppStub()
        _deliver(stub)
        assert stub.notified[0][2]["dedup_key"] == "daily-brief:2026-08-10"

    def test_links_back_to_the_app(self):
        stub = _AppStub()
        _deliver(stub)
        assert stub.notified[0][2]["link"] == {
            "text": "Read the brief", "href": "/daily-brief/"
        }

    def test_locale_reaches_the_text(self):
        stub = _AppStub(locale="zh")
        _deliver(stub)
        assert stub.notified[0][1].startswith("今日简报已就绪")

    def test_missing_fields_do_not_crash(self):
        stub = _AppStub()
        result = _deliver(stub, {})
        assert stub.notified and result["delivered"] is True


class TestDeliveryNeverBreaksGeneration:
    """The brief is already written and recorded before `_deliver` runs."""

    def test_exception_is_swallowed(self):
        stub = _AppStub(raises=RuntimeError("notifications plugin exploded"))
        result = _deliver(stub)
        assert result == {"delivered": False, "reason": "error"}

    def test_failure_is_logged_for_diagnosis(self):
        stub = _AppStub(raises=RuntimeError("boom"))
        _deliver(stub)
        assert stub.activity[0]["event"] == "deliver_failed"
        assert "boom" in stub.activity[0]["error"]
