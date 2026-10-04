"""System app tests: Expense — 12 use cases.

Covers core CRUD, the smart-add parser (free-text + AA split), the budget
round-trip, and two regression tests for bugs found 2026-05-18:
  - api_edit built a fake Starlette request and called api_delete on it,
    which raised TypeError on `await request.json()` → 500.
  - add() / api_add ignored the date arg; editing an entry's date silently
    rewrote it to today.
"""

from datetime import date, timedelta

import pytest

import factories
from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok


def _find_entry(entries, description_substr):
    for e in entries:
        if description_substr in str(e.get("description", "")):
            return e
    return None


@pytest.mark.api
class TestExpenseAPI:
    def test_timeline_items_contract(self, http_client):
        """Life-suite timeline contract (docs/suites/life-cohesion.md):
        every item carries ts/title/kind/href + amount, kind is 'expense'."""
        data = assert_dict_response(http_client.get("/expense/api/timeline-items?days=30"))
        items = data.get("items")
        assert isinstance(items, list)
        for it in items[:10]:
            assert {"ts", "title", "kind", "href"} <= set(it), it
            assert it["kind"] == "expense"
            assert isinstance(it.get("amount"), (int, float))

    def test_budget_endpoint_returns_number(self, http_client):
        """GET /expense/api/budget resolves settings-over-state to a float."""
        data = assert_dict_response(http_client.get("/expense/api/budget"))
        assert isinstance(data.get("budget"), (int, float))

    def test_report_rejects_bad_month(self, http_client):
        """POST /expense/api/report/monthly validates the month before writing."""
        data = assert_dict_response(
            http_client.post("/expense/api/report/monthly", json={"month": "not-a-month"})
        )
        assert data.get("error")

    def test_report_rejects_empty_month(self, http_client):
        """A month with no expenses errors instead of writing an empty report."""
        data = assert_dict_response(
            http_client.post("/expense/api/report/monthly", json={"month": "1999-01"})
        )
        assert data.get("error")

    def test_smart_add_parses_amount_and_desc(self, http_client):
        data = assert_ok(http_client.post("/expense/api/smart-add", json=factories.expense(amount=7, desc="latte")))
        assert isinstance(data, dict)
        assert data.get("amount") == 7

    def test_smart_add_rejects_empty_text(self, http_client):
        data = assert_ok(http_client.post("/expense/api/smart-add", json={"text": ""}))
        assert "error" in data

    def test_smart_add_aa_split_halves_amount(self, http_client):
        # AA split: "50 dinner aa 2" should record half the amount
        data = assert_ok(http_client.post(
            "/expense/api/smart-add",
            json={"text": f"50 {TEST_PREFIX}dinner aa 2"},
        ))
        assert isinstance(data, dict)
        assert data.get("amount") == 25

    def test_add_explicit_category_and_amount(self, http_client):
        payload = factories.expense_full(amount=12.5, category="Dining", desc="brunch")
        data = assert_ok(http_client.post("/expense/api/add", json=payload))
        assert data.get("amount") == 12.5
        assert data.get("category") == "Dining"

    def test_add_rejects_zero_amount(self, http_client):
        data = assert_ok(http_client.post("/expense/api/add", json={"amount": 0, "description": "x"}))
        assert "error" in data

    def test_add_honors_explicit_date(self, http_client):
        """Regression: api_add used to ignore data['date'] and stamp today."""
        target = (date.today() - timedelta(days=3)).isoformat()
        payload = factories.expense_full(amount=8, category="Transport", desc="bus", date=target)
        data = assert_ok(http_client.post("/expense/api/add", json=payload))
        assert data.get("date") == target

    def test_list_returns_recent_entries(self, http_client):
        assert_ok(http_client.post("/expense/api/add", json=factories.expense_full(amount=3, desc="snack")))
        rows = assert_list_response(http_client.get("/expense/api/list"))
        assert any(TEST_PREFIX in str(r.get("description", "")) for r in rows)

    def test_summary_shape(self, http_client):
        data = assert_dict_response(http_client.get("/expense/api/summary"))
        for k in ("month", "total", "count", "by_category"):
            assert k in data, f"summary missing {k}"

    def test_edit_change_category_only(self, http_client):
        """Smoke: editing one field keeps amount + date intact."""
        original = assert_ok(http_client.post("/expense/api/add",
            json=factories.expense_full(amount=9, category="Other", desc="recat")))
        updated = dict(original)
        updated["category"] = "Dining"
        result = assert_ok(http_client.post("/expense/api/edit",
            json={"original": original, "updated": updated}))
        # api_edit returns the new entry from add()
        assert result.get("amount") == 9
        assert result.get("category") == "Dining"
        assert result.get("date") == original["date"]

    def test_edit_honors_new_date(self, http_client):
        """Regression: changing the date in the edit modal was silently dropped
        because add() always used today.isoformat()."""
        original = assert_ok(http_client.post("/expense/api/add",
            json=factories.expense_full(amount=11, category="Transport", desc="datechg")))
        new_date = (date.today() - timedelta(days=5)).isoformat()
        updated = dict(original)
        updated["date"] = new_date
        result = assert_ok(http_client.post("/expense/api/edit",
            json={"original": original, "updated": updated}))
        assert result.get("date") == new_date, f"edit dropped the date: {result}"

    def test_edit_does_not_500(self, http_client):
        """Regression: api_edit used to build a fake request whose .json was
        a sync lambda, then await it → TypeError → 500."""
        original = assert_ok(http_client.post("/expense/api/add",
            json=factories.expense_full(amount=4, desc="no500")))
        updated = dict(original)
        updated["description"] = original["description"] + "-edited"
        resp = http_client.post("/expense/api/edit",
            json={"original": original, "updated": updated})
        assert resp.status_code == 200, f"api_edit returned {resp.status_code}: {resp.text[:200]}"

    def test_budget_get_and_set_roundtrip(self, http_client):
        before = assert_dict_response(http_client.get("/expense/api/budget"))
        assert "budget" in before
        assert_ok(http_client.post("/expense/api/budget", json={"amount": 2500}))
        after = assert_dict_response(http_client.get("/expense/api/budget"))
        assert after.get("budget") == 2500
        # restore
        http_client.post("/expense/api/budget", json={"amount": before["budget"]})

    # ── Statement import (preview → confirm) ───────────────────

    def _stmt_csv(self, tag, day="2026-07-03"):
        d, m, y = day[8:10], day[5:7], day[0:4]
        return (
            "Date,Description,Amount\n"
            f"{d}/{m}/{y},{tag} coles,-45.20\n"     # "coles" is a Groceries keyword
            f"{d}/{m}/{y},{tag} salary,3000.00\n"   # positive → income, skipped
        )

    def test_import_preview_does_not_write(self, http_client):
        tag = f"{TEST_PREFIX}preview"
        before = assert_list_response(http_client.get("/expense/api/list?limit=500"))
        data = assert_ok(http_client.post(
            "/expense/api/import/preview", json={"csv_content": self._stmt_csv(tag)},
        ))
        # The salary (positive) row is skipped; one expense row is proposed.
        assert data["summary"]["skipped"] == 1
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert row["amount"] == 45.20 and row["duplicate"] is False
        assert row["category"] == "Groceries"  # detect_category on "groceries"
        after = assert_list_response(http_client.get("/expense/api/list?limit=500"))
        assert len(after) == len(before), "preview must not write to the vault"

    def test_import_confirm_writes_then_dedupes(self, http_client):
        tag = f"{TEST_PREFIX}confirm"
        row = {"date": "2026-07-03", "amount": 12.50, "description": f"{tag} lunch", "category": "Dining"}
        first = assert_ok(http_client.post("/expense/api/import/confirm", json={"rows": [row]}))
        assert first["imported"] == 1 and first["skipped"] == 0
        # A second confirm of the same row is deduped against the now-written vault.
        second = assert_ok(http_client.post("/expense/api/import/confirm", json={"rows": [row]}))
        assert second["imported"] == 0 and second["skipped"] == 1

    def test_import_preview_flags_existing_as_duplicate(self, http_client):
        tag = f"{TEST_PREFIX}dupflag"
        row = {"date": "2026-07-03", "amount": 7.00, "description": f"{tag} coffee", "category": "Dining"}
        assert_ok(http_client.post("/expense/api/import/confirm", json={"rows": [row]}))
        csv = f"Date,Description,Amount\n03/07/2026,{tag} coffee,-7.00\n"
        data = assert_ok(http_client.post("/expense/api/import/preview", json={"csv_content": csv}))
        assert data["rows"][0]["duplicate"] is True
        assert data["summary"]["new"] == 0

    def test_import_preview_no_columns_errors(self, http_client):
        r = http_client.post("/expense/api/import/preview", json={"csv_content": "foo,bar\n1,2\n"})
        assert "error" in r.json()

    def test_import_confirm_rejects_empty(self, http_client):
        r = http_client.post("/expense/api/import/confirm", json={"rows": []})
        assert "error" in r.json()


@pytest.mark.interactive
class TestExpenseHomeReadsHeatmap:
    """The home tab's 7-day sparkline and no-spend chips must show the same
    daily amounts `/expense/api/heatmap` reports.

    Regression for 2026-09-06 (system-check walk 9): the API answers
    ``{"start", "data": {date: amount}}`` while the page read ``.dates`` and
    fell back to the whole envelope, so every day rendered as $0 and the
    no-spend chips counted spend days as no-spend days — on any vault, since
    at least 2026-05-30. Executes the real page against the real payload;
    a source grep would be satisfied by the comment that now explains it.
    """

    def test_sparkline_and_chips_match_heatmap_api(self, app_page, http_client, page_errors):
        from page_helpers import assert_no_js_errors

        from decimal import ROUND_HALF_UP, Decimal

        def js_fixed0(v):
            # JS toFixed(0) rounds half up; Python round() is banker's.
            return int(Decimal(str(v)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        hm = assert_ok(http_client.get("/expense/api/heatmap"))
        daily = hm.get("data") or {}
        today = date.today()   # local — the page keys by local date too
        window = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
        if not any(daily.get(d, 0) >= 0.5 for d in window):
            # < $0.50 would render "$0" on both the fixed and the broken page.
            pytest.skip("no spend >= $0.50 in the last 7 days on this vault — the assertion would be vacuous")

        page = app_page("expense")
        page.wait_for_selector(".sparkline-bar", timeout=8000)
        titles = page.locator(".sparkline-bar").evaluate_all("els => els.map(e => e.title)")
        assert len(titles) == 7, titles
        shown = {t.split(": $")[0]: int(t.split(": $")[1]) for t in titles}
        assert set(shown) == set(window), (sorted(shown), window)   # local-date keys, not UTC
        for d in window:
            assert shown[d] == js_fixed0(daily.get(d, 0)), (d, shown[d], daily.get(d))

        # The chips derive from the same payload: spend days this month.
        month_days = [d for d in daily if d.startswith(today.strftime("%Y-%m")) and d <= today.isoformat()]
        expect_spend_days = sum(1 for d in month_days if daily[d] > 0)
        chip = page.locator("#spend-chips").inner_text().lower()   # labels are CSS-uppercased
        assert "spend days this month" in chip, chip
        value = chip.split("spend days this month")[0].strip().split()[-1]
        assert int(value) == expect_spend_days, (value, expect_spend_days, chip)
        assert_no_js_errors(page_errors)
