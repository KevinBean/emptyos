"""System app tests: Conversation Ingest - 12 use cases.

Acceptance criteria -> tests
  AC1 inventory truth          -> test_overview_shape, test_active_counts_match_queue
  AC2 joined evidence detail   -> test_item_detail_joins_by_provider_id
  AC2b visible Vault receipt   -> test_item_detail_has_four_layer_receipt
  AC3 canonical mechanism      -> test_mechanism_reads_full_contract
  AC4 resumability             -> test_resume_matches_first_pending
  AC5 privacy and safety       -> test_item_list_has_no_raw_body_fields
  AC6 navigation               -> test_page_loads_without_js_errors, test_detail_has_note_actions
  AC7 failure visibility       -> test_traversal_is_rejected_fail_soft
Edge/regression:
  test_page_limit_is_capped, test_tabs_are_keyboard_buttons,
  test_pending_table_opens_bookmarkable_detail
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors


def _active(http_client):
    overview = assert_ok(http_client.get("/conversation-ingest/api/overview"))
    if not overview.get("imports"):
        pytest.skip("No supported conversation import artifacts")
    active = overview.get("active")
    row = next(item for item in overview["imports"] if item["key"] == active)
    return overview, row


@pytest.mark.api
class TestConversationIngestAPI:
    def test_overview_shape(self, http_client):
        body = assert_ok(http_client.get("/conversation-ingest/api/overview"))
        assert {"root", "root_ok", "imports", "active"}.issubset(body)
        assert isinstance(body["imports"], list)

    def test_active_counts_match_queue(self, http_client):
        overview, row = _active(http_client)
        root = Path(overview["root"])
        queue_path = root / row["key"] / (row["queue_file"] or "")
        if not queue_path.is_file():
            pytest.skip("Active import has no queue artifact")
        queue = json.loads(queue_path.read_text(encoding="utf-8-sig"))
        assert row["total"] == queue["total_unique"]
        assert row["processed"] == queue["processed"]
        assert row["pending"] == queue["pending"]

    def test_item_list_has_no_raw_body_fields(self, http_client):
        _, active = _active(http_client)
        body = assert_ok(
            http_client.get(
                "/conversation-ingest/api/items",
                params={"import": active["key"], "bucket": "pending", "limit": 5},
            )
        )
        for row in body.get("rows", []):
            assert "messages" not in row
            assert "content" not in row
            assert "conversation" not in row

    def test_item_detail_joins_by_provider_id(self, http_client):
        _, active = _active(http_client)
        listing = assert_ok(
            http_client.get(
                "/conversation-ingest/api/items",
                params={"import": active["key"], "bucket": "all", "limit": 20},
            )
        )
        if not listing.get("rows"):
            pytest.skip("Active import has no rows")
        provider_id = listing["rows"][0]["provider_id"]
        detail = assert_ok(
            http_client.get(
                f"/conversation-ingest/api/items/{provider_id}",
                params={"import": active["key"]},
            )
        )
        assert detail["provider_id"] == provider_id
        assert len(detail["stages"]) == 8
        assert all("ok" in stage for stage in detail["stages"])

    def test_item_detail_has_four_layer_receipt(self, http_client):
        _, active = _active(http_client)
        listing = assert_ok(
            http_client.get(
                "/conversation-ingest/api/items",
                params={"import": active["key"], "bucket": "complete", "limit": 20},
            )
        )
        if not listing.get("rows"):
            pytest.skip("Active import has no complete rows")
        provider_id = listing["rows"][0]["provider_id"]
        detail = assert_ok(
            http_client.get(
                f"/conversation-ingest/api/items/{provider_id}",
                params={"import": active["key"]},
            )
        )
        receipts = detail["receipts"]
        assert [receipt["id"] for receipt in receipts] == [
            "source",
            "digest",
            "derived",
            "ledger",
        ]
        assert all("outcome" in receipt and "ok" in receipt for receipt in receipts)
        derived = receipts[2]
        if not derived.get("notes"):
            assert derived["outcome"] in {
                "accounted-no-mutation",
                "no-durable-delta",
            }
            assert (
                derived.get("dispositions")
                or derived.get("no_mutation_reason")
            )

    def test_mechanism_reads_full_contract(self, http_client):
        body = assert_ok(http_client.get("/conversation-ingest/api/mechanism"))
        assert body["canonical_owner"] == "eos-ai-conversation-ingest"
        assert body["required_sections"] == [
            "## Digest",
            "## Domain coverage",
            "## Decisions and durable deltas",
            "## Fact-check notes",
            "## Routing",
            "## Evidence chain",
            "## Source",
        ]
        assert len(body["domains"]) == 15
        assert body["completion_gate_present"] is True
        assert body["evidence_contract_present"] is True

    def test_resume_matches_first_pending(self, http_client):
        _, active = _active(http_client)
        pending = assert_ok(
            http_client.get(
                "/conversation-ingest/api/items",
                params={"import": active["key"], "bucket": "pending", "limit": 1},
            )
        )
        resume = assert_ok(
            http_client.get(
                "/conversation-ingest/api/resume",
                params={"import": active["key"]},
            )
        )
        if pending.get("rows"):
            assert resume["next"]["provider_id"] == pending["rows"][0]["provider_id"]
            assert resume["action"] == "ingest"

    def test_traversal_is_rejected_fail_soft(self, http_client):
        body = assert_ok(
            http_client.get(
                "/conversation-ingest/api/items",
                params={"import": "../outside", "bucket": "all"},
            )
        )
        assert "error" in body
        assert "invalid import key" in body["error"]

    def test_page_limit_is_capped(self, http_client):
        _, active = _active(http_client)
        body = assert_ok(
            http_client.get(
                "/conversation-ingest/api/items",
                params={"import": active["key"], "bucket": "all", "limit": 999},
            )
        )
        assert body["limit"] == 200
        assert len(body["rows"]) <= 200


@pytest.mark.interactive
class TestConversationIngestUI:
    def test_page_loads_without_js_errors(self, page, base_url, page_errors):
        response = page.goto(
            base_url + "/conversation-ingest/", wait_until="domcontentloaded"
        )
        assert response and response.status == 200
        page.wait_for_selector(".ci-tabs", timeout=5000)
        page.wait_for_timeout(800)
        assert_no_js_errors(page_errors)

    def test_tabs_are_keyboard_buttons(self, page, base_url):
        page.goto(base_url + "/conversation-ingest/", wait_until="domcontentloaded")
        page.wait_for_selector(".ci-tab", timeout=5000)
        tabs = page.locator("button.ci-tab")
        assert tabs.count() == 5
        assert tabs.evaluate_all(
            "nodes => nodes.map(node => node.dataset.tab)"
        ) == ["overview", "queue", "routing", "backfill", "mechanism"]
        assert all(
            tabs.nth(index).get_attribute("title")
            for index in range(tabs.count())
        )

    def test_pending_table_opens_bookmarkable_detail(self, page, base_url):
        page.goto(base_url + "/conversation-ingest/", wait_until="domcontentloaded")
        page.wait_for_selector(".ci-tab[data-tab='queue']", timeout=5000)
        page.locator(".ci-tab[data-tab='queue']").click()
        try:
            page.wait_for_selector("tr[data-open]", timeout=5000)
        except Exception:
            pytest.skip("No pending conversation rows")
        page.locator("tr[data-open]").first.press("Enter")
        page.wait_for_function(
            "decodeURIComponent(location.hash).indexOf('::') !== -1",
            timeout=5000,
        )
        page.wait_for_selector(".ci-stage-list", timeout=5000)
        page.wait_for_selector(".ci-receipt-grid", timeout=5000)
        assert page.locator(".ci-receipt").count() == 4

    def test_detail_has_note_actions(self, page, base_url):
        page.goto(base_url + "/conversation-ingest/", wait_until="domcontentloaded")
        page.wait_for_selector(".ci-tab[data-tab='backfill']", timeout=5000)
        page.locator(".ci-tab[data-tab='backfill']").click()
        try:
            page.wait_for_selector("tr[data-open]", timeout=5000)
        except Exception:
            pytest.skip("No backfill rows")
        page.locator("tr[data-open]").first.click()
        page.wait_for_selector(".ci-stage-list", timeout=5000)
        assert page.locator(".ci-detail a[href='/vault-graph/']").count() == 1
