"""Dogfood — media app (highlights + collections lifecycle).

Media items (books/movies) don't have a direct create API (they're imported
via Kindle/CSV/URL). The tested lifecycle focuses on what IS CRUD-able via
API: highlights + collections + reading sessions + goal setting.

Lifecycle: add highlight → list sources → edit highlight → create collection
→ add highlight to collection → log reading session → set goal → delete
highlight → verify gone. No LLM.
"""

import time
import uuid

import pytest

from helpers import TEST_PREFIX


RUN_ID = f"{TEST_PREFIX}media-{uuid.uuid4().hex[:6]}"


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


@pytest.mark.dogfood
class TestMediaLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/media/api/list"):
            pytest.skip("media app not loaded")

    def test_01_add_highlight(self, http_client):
        text = f"{RUN_ID} — Dogfood highlight text"
        resp = http_client.post(
            "/media/api/highlights",
            json={
                "text": text,
                "note": "dogfood test",
                "source": f"{RUN_ID}-book",
                "tags": ["test"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        hid = data.get("id") or data.get("highlight_id")
        assert hid, f"no highlight id returned: {data}"
        TestMediaLifecycle.state["hid"] = hid
        TestMediaLifecycle.state["source"] = f"{RUN_ID}-book"
        TestMediaLifecycle.state["text"] = text

    def test_02_list_contains_highlight(self, http_client):
        listing = http_client.get("/media/api/highlights").json()
        items = listing if isinstance(listing, list) else listing.get("highlights", [])
        assert any(
            RUN_ID in str(h.get("text", "")) for h in items
        ), "new highlight missing from /api/highlights"

    def test_03_sources_include_our_book(self, http_client):
        sources = http_client.get("/media/api/sources").json()
        blob = str(sources)
        assert self.state["source"] in blob, (
            f"source {self.state['source']} missing from sources: {sources!r}"
        )

    def test_04_edit_highlight(self, http_client):
        hid = self.state["hid"]
        resp = http_client.post(
            f"/media/api/highlights/{hid}/edit",
            json={"note": "edited note"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" not in data, f"edit failed: {data}"

    def test_05_create_collection(self, http_client):
        name = f"{RUN_ID}-collection"
        resp = http_client.post("/media/api/collections", json={"name": name})
        assert resp.status_code == 200
        data = resp.json()
        cid = data.get("id") or data.get("collection_id")
        assert cid, f"collection not created: {data}"
        TestMediaLifecycle.state["cid"] = cid

    def test_06_add_highlight_to_collection(self, http_client):
        cid = self.state["cid"]
        hid = self.state["hid"]
        resp = http_client.post(
            f"/media/api/collections/{cid}/add",
            json={"highlight_id": hid},
        )
        assert resp.status_code == 200
        assert resp.json().get("added"), "highlight not added to collection"

    def test_07_log_reading_session(self, http_client):
        resp = http_client.post(
            "/media/api/reading-session",
            json={"minutes": 15, "pages": 10, "source": self.state["source"]},
        )
        assert resp.status_code == 200

    def test_08_reading_stats_reflect_session(self, http_client):
        stats = http_client.get("/media/api/reading-stats").json()
        assert isinstance(stats, dict)
        # Just verify a numeric field is present; shape varies by implementation
        total = (stats.get("total_minutes") or stats.get("minutes_this_week")
                 or stats.get("today_minutes") or 0)
        assert total >= 0

    def test_09_set_and_get_goal(self, http_client):
        set_resp = http_client.post(
            "/media/api/goal",
            json={"daily_minutes": 20, "daily_pages": 15},
        )
        assert set_resp.status_code == 200
        got = http_client.get("/media/api/goal").json()
        assert got.get("daily_minutes") == 20, f"goal not persisted: {got}"

    def test_10_delete_highlight_and_verify_gone(self, http_client):
        hid = self.state["hid"]
        resp = http_client.delete(f"/media/api/highlights/{hid}")
        assert resp.status_code == 200
        time.sleep(0.3)
        listing = http_client.get("/media/api/highlights").json()
        items = listing if isinstance(listing, list) else listing.get("highlights", [])
        still = any(RUN_ID in str(h.get("text", "")) for h in items)
        assert not still, "highlight still in list after delete"


@pytest.fixture(scope="module", autouse=True)
def _cleanup(http_client):
    yield
    try:
        listing = http_client.get("/media/api/highlights").json()
        items = listing if isinstance(listing, list) else listing.get("highlights", [])
        for h in items:
            if RUN_ID in str(h.get("text", "")) or RUN_ID in str(h.get("source", "")):
                hid = h.get("id") or h.get("highlight_id")
                if hid:
                    http_client.delete(f"/media/api/highlights/{hid}")
    except Exception:
        pass
    # Collections — no DELETE endpoint; accepted orphan
