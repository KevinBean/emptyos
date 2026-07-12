"""System tests: Sheet — a light calc sheet.

End-to-end over HTTP against the live daemon: create a sheet, set cells +
formulas, assert the engine recomputes, edit and re-check, confirm the vault
note stores the formula *source* (not the computed value), then delete. All
fixtures carry TEST_PREFIX and are cleaned by conftest.
"""

from __future__ import annotations

import pytest

from helpers import assert_ok, TEST_PREFIX


def _create(http_client, title):
    body = assert_ok(http_client.post("/sheet/api/sheets", json={"title": title}))
    assert body.get("ok")
    return body["id"]


def _set(http_client, sid, cell, raw):
    return assert_ok(
        http_client.post(f"/sheet/api/sheets/{sid}/cell", json={"cell": cell, "raw": raw})
    )


@pytest.mark.api
class TestSheetAPI:
    def test_page_loads(self, http_client):
        r = http_client.get("/sheet/")
        assert r.status_code == 200
        assert "Sheet" in r.text

    def test_create_list_delete(self, http_client):
        sid = _create(http_client, f"{TEST_PREFIX}basic")
        listing = assert_ok(http_client.get("/sheet/api/sheets"))
        assert any(s["id"] == sid for s in listing["sheets"])
        got = assert_ok(http_client.get(f"/sheet/api/sheets/{sid}"))
        assert got["rows"] >= 1 and got["cols"] >= 1
        d = assert_ok(http_client.delete(f"/sheet/api/sheets/{sid}"))
        assert d.get("ok")

    def test_formula_recompute(self, http_client):
        sid = _create(http_client, f"{TEST_PREFIX}calc")
        try:
            _set(http_client, sid, "A1", "5")
            _set(http_client, sid, "A2", "7")
            res = _set(http_client, sid, "A3", "=SUM(A1:A2)")
            assert res["computed_grid"]["A3"] == "12"
            # Edit a dependency → dependent recomputes.
            res2 = _set(http_client, sid, "A1", "10")
            assert res2["computed_grid"]["A3"] == "17"
        finally:
            http_client.delete(f"/sheet/api/sheets/{sid}")

    def test_source_is_stored_not_value(self, http_client):
        sid = _create(http_client, f"{TEST_PREFIX}source")
        try:
            _set(http_client, sid, "A1", "5")
            _set(http_client, sid, "A2", "7")
            _set(http_client, sid, "A3", "=SUM(A1:A2)")
            got = assert_ok(http_client.get(f"/sheet/api/sheets/{sid}"))
            # raw_grid holds the formula source; computed_grid holds the value.
            assert got["raw_grid"]["A3"] == "=SUM(A1:A2)"
            assert got["computed_grid"]["A3"] == "12"
        finally:
            http_client.delete(f"/sheet/api/sheets/{sid}")

    def test_if_and_today(self, http_client):
        sid = _create(http_client, f"{TEST_PREFIX}fns")
        try:
            _set(http_client, sid, "A1", "15")
            r1 = _set(http_client, sid, "B1", '=IF(A1>10,"hi","lo")')
            assert r1["computed_grid"]["B1"] == "hi"
            r2 = _set(http_client, sid, "C1", "=TODAY()")
            from datetime import date

            assert r2["computed_grid"]["C1"] == date.today().isoformat()
        finally:
            http_client.delete(f"/sheet/api/sheets/{sid}")

    def test_cycle_detection(self, http_client):
        sid = _create(http_client, f"{TEST_PREFIX}cycle")
        try:
            _set(http_client, sid, "A1", "=B1")
            res = _set(http_client, sid, "B1", "=A1")
            assert res["computed_grid"]["A1"] == "#CYCLE"
            assert res["computed_grid"]["B1"] == "#CYCLE"
        finally:
            http_client.delete(f"/sheet/api/sheets/{sid}")

    def test_out_of_bounds_cell_rejected(self, http_client):
        sid = _create(http_client, f"{TEST_PREFIX}bounds")
        try:
            r = http_client.post(
                f"/sheet/api/sheets/{sid}/cell", json={"cell": "ZZ999", "raw": "1"}
            )
            assert r.status_code == 200
            assert "error" in r.json()
        finally:
            http_client.delete(f"/sheet/api/sheets/{sid}")
