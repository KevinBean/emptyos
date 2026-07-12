"""System app tests: Feedback.

Acceptance criteria -> tests:
  AC1 GET /feedback/api/items returns items/stats metadata -> test_items_endpoint_shape
  AC2 POST /feedback/api/items creates or dedupes feedback -> test_submit_feedback_api
  AC3 Vote/comment/status actions mutate an item -> test_vote_comment_status_flow
  AC4 GET /feedback/ renders the working board UI -> test_page_loads
  AC5 Changelog write verb stays off the public path -> test_changelog_write_not_on_public_path
"""

import pytest

from helpers import TEST_PREFIX, assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly


def _feedback_or_skip(http_client):
    resp = http_client.get("/feedback/api/items")
    if resp.status_code == 404:
        pytest.skip("feedback app is not loaded in the running daemon")
    return assert_dict_response(resp, ["items", "stats"])


@pytest.mark.api
class TestFeedbackAPI:
    def test_items_endpoint_shape(self, http_client):
        data = _feedback_or_skip(http_client)
        assert "status_order" in data
        assert "kind_options" in data

    def test_submit_feedback_api(self, http_client):
        _feedback_or_skip(http_client)
        resp = http_client.post(
            "/feedback/api/items",
            json={
                "title": TEST_PREFIX + "Feedback voting board",
                "body": "Users can vote on roadmap requests.",
                "kind": "feature",
                "source": "pytest",
            },
        )
        data = assert_dict_response(resp, ["ok", "item"])
        assert data["item"]["title"].startswith(TEST_PREFIX)

    def test_vote_comment_status_flow(self, http_client):
        _feedback_or_skip(http_client)
        created = assert_dict_response(
            http_client.post(
                "/feedback/api/items",
                json={
                    "title": TEST_PREFIX + "Roadmap changelog flow",
                    "body": "Ship notes from completed feedback.",
                    "kind": "improvement",
                },
            ),
            ["item"],
        )
        item_id = created["item"]["id"]
        voted = assert_dict_response(http_client.post(f"/feedback/api/items/{item_id}/vote", json={}))
        assert voted["item"]["votes"] >= 1
        commented = assert_dict_response(
            http_client.post(
                f"/feedback/api/items/{item_id}/comments",
                json={"text": TEST_PREFIX + "comment"},
            )
        )
        assert commented["item"]["comment_count"] >= 1
        moved = assert_dict_response(
            http_client.post(f"/feedback/api/items/{item_id}/status", json={"status": "shipped"})
        )
        assert moved["item"]["status"] == "shipped"

    def test_changelog_write_not_on_public_path(self, http_client):
        """H1 regression: the changelog generator does an LLM call + state write,
        so it must NOT sit under a public_routes prefix. ``/api/changelog`` is a
        public GET-only read route; the generator lives at the non-public sibling
        ``/api/changelog-draft``. If someone re-adds POST /api/changelog it
        becomes an unauthenticated write — this test fails first."""
        _feedback_or_skip(http_client)
        # Write verb must NOT be reachable on the public read path. The EOS
        # router returns 404 for an unregistered (method, path) pair (405 on a
        # stock Starlette router) — either way it must never succeed as a write.
        on_public = http_client.post("/feedback/api/changelog", json={"style": "simple"})
        assert on_public.status_code in (404, 405), (
            "POST /feedback/api/changelog must be rejected — the changelog "
            "generator (LLM call + write) must not share a path in public_routes"
        )
        # The generator exists at the non-public sibling and accepts POST.
        # Assert only that the route is registered for POST (not 404/405) —
        # not == 200, which would couple this guard to a live LLM draft call
        # (slow/flaky under parallel load when shipped items exist).
        on_sibling = http_client.post("/feedback/api/changelog-draft", json={"style": "simple"})
        assert on_sibling.status_code not in (404, 405), (
            "the changelog generator must be reachable at the non-public sibling path"
        )


@pytest.mark.interactive
class TestFeedbackUI:
    def test_page_loads(self, app_page, page_errors):
        page = app_page("feedback")
        wait_briefly(page, 1000)
        assert page.locator("#feedback-board").count() == 1
        assert page.locator("#feedback-title").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
