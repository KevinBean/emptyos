"""System app tests: Build Record (progress) — pure-logic units + API/UI smoke.

The unit class needs no daemon (loads the module directly — app.py has only
absolute `emptyos.sdk` imports, no relative imports, so a file-spec load works).
The API/UI classes require the `progress` app live on :9000 (restart after install).
"""
import importlib.util
import pathlib
from datetime import date

import pytest

from helpers import assert_dict_response

_APP_PY = pathlib.Path(__file__).resolve().parents[1] / "apps/extension/dev/progress/app.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("progress_app_under_test", _APP_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
class TestProgressLogic:
    def test_leading_date_regex(self):
        mod = _load_module()
        assert mod._DATE_RE.match("2026-06-29.md").group(1) == "2026-06-29"
        assert mod._DATE_RE.match("2026-06-27-harness-round3.md").group(1) == "2026-06-27"
        assert mod._DATE_RE.match("_index.md") is None
        assert mod._DATE_RE.match("notes.md") is None

    def test_index_row_regex(self):
        mod = _load_module()
        m = mod._INDEX_ROW_RE.match("| em-engines | 2026-06-29 | Meshed NR load flow | [[em-engines]] |")
        assert m and m.group(1) == "em-engines" and m.group(2) == "2026-06-29"
        assert mod._INDEX_ROW_RE.match("| Track | Last touched | Last session | File |") is None

    def test_week_streak_empty(self):
        assert _load_module().ProgressApp._week_streak(set()) == 0

    def test_week_streak_consecutive(self):
        ws = _load_module().ProgressApp._week_streak
        today = date(2026, 6, 29)  # Monday
        # this week + the two prior weeks → 3
        assert ws({"2026-06-29", "2026-06-22", "2026-06-15"}, today=today) == 3
        # a gap two weeks back breaks the streak at 1
        assert ws({"2026-06-29", "2026-06-08"}, today=today) == 1

    def test_week_streak_grace_current_week_empty(self):
        # current week has no log yet, but last week does → streak still counts
        ws = _load_module().ProgressApp._week_streak
        assert ws({"2026-06-22"}, today=date(2026, 6, 29)) == 1


@pytest.mark.api
class TestProgressAPI:
    def test_summary_shape(self, http_client):
        d = assert_dict_response(http_client.get("/progress/api/summary"))
        assert isinstance(d.get("heatmap"), dict)
        assert isinstance(d.get("themes"), list)
        h = d.get("headline") or {}
        for k in ("sessions", "commits", "streak_weeks", "shipped_week", "since"):
            assert k in h, f"missing headline.{k}"

    def test_headline_accumulates(self, http_client):
        h = assert_dict_response(http_client.get("/progress/api/summary"))["headline"]
        # the anti-vanishing numbers are non-negative integers
        assert isinstance(h["sessions"], int) and h["sessions"] >= 0
        assert isinstance(h["commits"], int) and h["commits"] >= 0

    def test_themes_have_bars(self, http_client):
        themes = assert_dict_response(http_client.get("/progress/api/summary"))["themes"]
        if not themes:
            pytest.skip("no _themes.toml seeded on this daemon's vault")
        t = themes[0]
        for k in ("id", "name", "purpose", "shipped", "total", "moved", "track_count"):
            assert k in t, f"missing theme.{k}"
        assert t["shipped"] <= t["total"]

    def test_panel_on_hub(self, http_client):
        panels = http_client.get("/hub/api/panels").json()
        rows = panels.get("panels", panels) if isinstance(panels, dict) else panels
        blob = str(rows)
        assert "dev-progress" in blob or "build record" in blob.lower(), \
            "progress panel not aggregated on /hub/api/panels"


@pytest.mark.interactive
class TestProgressUI:
    def test_page_loads(self, page, base_url):
        from page_helpers import assert_no_js_errors
        page.goto(f"{base_url}/progress/")
        page.wait_for_selector("#headline", timeout=8000)
        assert_no_js_errors(page)
