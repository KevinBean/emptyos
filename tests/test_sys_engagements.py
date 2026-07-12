"""System app tests: Engagements — FDE portfolio view-layer (read-only).

The app reads segments from `workspaces` and customer engagements from
`fde-engagement` vault notes. Read-only, so tests assert contract shape rather
than CRUD. Customer count may be 0 (no engagement run yet) — that's valid.
"""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly

_KINDS = {"segment", "customer"}
_EVIDENCE = {"segment-served", "measured", "in-progress"}
_REQUIRED_KEYS = {"id", "name", "kind", "status", "evidence"}


@pytest.mark.api
class TestEngagementsAPI:
    def test_summary_ok(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/summary"))
        assert isinstance(data, dict)

    def test_summary_has_stats(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/summary"))
        stats = data.get("stats") or {}
        for k in ("segments", "customers", "measured", "apps_deployed"):
            assert k in stats, f"stats missing {k}"

    def test_stats_are_nonneg_ints(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/summary"))
        for k, v in (data.get("stats") or {}).items():
            assert isinstance(v, int) and v >= 0, f"{k}={v!r}"

    def test_engagements_endpoint_list(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/engagements"))
        assert isinstance(data.get("engagements"), list)

    def test_rows_have_required_keys(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/summary"))
        for r in data.get("engagements", []):
            assert _REQUIRED_KEYS <= set(r), f"missing keys: {_REQUIRED_KEYS - set(r)}"

    def test_kinds_constrained(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/summary"))
        for r in data.get("engagements", []):
            assert r["kind"] in _KINDS, r["kind"]

    def test_evidence_labels_valid(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/summary"))
        for r in data.get("engagements", []):
            assert r["evidence"] in _EVIDENCE, r["evidence"]

    def test_segments_served_status(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/summary"))
        segs = [r for r in data.get("engagements", []) if r["kind"] == "segment"]
        for r in segs:
            assert r["status"] == "served"
            assert r["evidence"] == "segment-served"

    def test_summary_engagements_match_count(self, http_client):
        data = assert_ok(http_client.get("/engagements/api/summary"))
        rows = data.get("engagements", [])
        stats = data.get("stats") or {}
        assert stats["segments"] + stats["customers"] == len(rows)


@pytest.mark.interactive
class TestEngagementsUI:
    def test_ui_loads(self, app_page, page_errors):
        app_page("engagements")
        assert_no_js_errors(page_errors)

    def test_ui_no_errors_on_reload(self, app_page, page_errors):
        page = app_page("engagements")
        wait_briefly(page, 800)
        page.reload()
        wait_briefly(page, 800)
        assert_no_js_errors(page_errors)
