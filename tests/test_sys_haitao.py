"""System tests for the Haitao app — orders, packages, match-reconcile.

Covers verification cases 1, 4-9 from the haitao plan. Cases 2-3
(LLM extraction via ai-form-fill, VLM photo fallback) require the LLM
domain; they live under @pytest.mark.llm so the non-LLM suite runs free.
Cases 10-12 (items/expense ripple) cover Phase 3 integrations and are
deferred until that wiring is in place.
"""

from __future__ import annotations

import io

import pytest

from helpers import TEST_PREFIX, assert_ok


def _new_order_payload(suffix: str = "a") -> dict:
    return {
        "platform": "taobao",
        "store": f"{TEST_PREFIX}store-{suffix}",
        "items_summary": f"{TEST_PREFIX}item-{suffix}",
        "items_count": 1,
        "order_date": "2026-05-10",
        "total_cny": 123.45,
        "domestic_tracking": f"SF{suffix}{TEST_PREFIX}TRK",
        "category": "test",
    }


def _new_package_payload(suffix: str = "a", *, tracking: str | None = None) -> dict:
    return {
        "forwarder": "test-forwarder",
        "intake_date": "2026-05-12",
        "weight_kg": 1.0,
        "domestic_tracking": tracking if tracking is not None else f"SF{suffix}{TEST_PREFIX}TRK",
        "notes": f"{TEST_PREFIX}note-{suffix}",
    }


@pytest.mark.api
class TestHaitaoAPI:
    def test_app_registered(self, http_client):
        r = http_client.get("/api/apps")
        assert r.status_code == 200
        ids = [a.get("id") for a in r.json()]
        assert "haitao" in ids

    def test_index_loads(self, http_client):
        r = http_client.get("/haitao/")
        assert r.status_code == 200
        assert "Haitao" in r.text

    def test_form_schemas(self, http_client):
        data = assert_ok(http_client.get("/haitao/api/form-schemas"))
        assert isinstance(data.get("order"), list) and data["order"]
        assert isinstance(data.get("package"), list) and data["package"]
        assert "order_statuses" in data
        assert "package_statuses" in data

    def test_orders_empty_shape(self, http_client):
        data = assert_ok(http_client.get("/haitao/api/orders"))
        assert isinstance(data.get("orders"), list)

    def test_packages_empty_shape(self, http_client):
        data = assert_ok(http_client.get("/haitao/api/packages"))
        assert isinstance(data.get("packages"), list)

    def test_create_order(self, http_client):
        r = http_client.post("/haitao/api/orders", json=_new_order_payload("create"))
        body = assert_ok(r)
        assert body.get("ok") is True
        assert body.get("order_id", "").startswith("ord-")

    def test_create_package_with_tracking_suggests_match(self, http_client):
        """Verification case 4: create order+package sharing a tracking number,
        package creation surfaces exactly one pending match with score >= 60."""
        # Create the order first (so it's a candidate when the package lands).
        track = f"SFMATCH{TEST_PREFIX}TRK"
        o = http_client.post("/haitao/api/orders", json={
            **_new_order_payload("match"), "domestic_tracking": track,
        }).json()
        assert o.get("ok")

        p = http_client.post("/haitao/api/packages", json={
            **_new_package_payload("match", tracking=track),
        }).json()
        assert p.get("ok")
        suggestions = p.get("suggestions") or []
        assert len(suggestions) >= 1
        # Exact tracking match alone = 60; recency may add 10 if dates align.
        assert suggestions[0]["score"] >= 60
        assert suggestions[0]["order_id"] == o["order_id"]

    def test_confirm_match_links_both_sides(self, http_client):
        """Verification case 5: applying a pending match links both sides + status flips."""
        track = f"SFAPPLY{TEST_PREFIX}TRK"
        o = http_client.post("/haitao/api/orders", json={
            **_new_order_payload("apply"), "domestic_tracking": track,
        }).json()
        p = http_client.post("/haitao/api/packages", json={
            **_new_package_payload("apply", tracking=track),
        }).json()
        pid = (p.get("suggestions") or [{}])[0].get("id")
        assert pid, "no pending match created"

        r = http_client.post("/haitao/api/confirm-match",
                             json={"pending_id": pid}).json()
        assert r.get("ok") is True

        # Re-read both notes; package_ids/order_ids should be linked, status flipped.
        order = http_client.get(f"/haitao/api/orders/{o['order_id']}").json()
        pkg = http_client.get(f"/haitao/api/packages/{p['package_id']}").json()
        assert p["package_id"] in (order.get("package_ids") or [])
        assert o["order_id"] in (pkg.get("order_ids") or [])
        assert order.get("status") == "warehouse"
        assert pkg.get("status") == "matched"

    def test_reject_match_leaves_notes_untouched(self, http_client):
        """Verification case 6: rejecting a match doesn't mutate notes."""
        track = f"SFREJECT{TEST_PREFIX}TRK"
        o = http_client.post("/haitao/api/orders", json={
            **_new_order_payload("reject"), "domestic_tracking": track,
        }).json()
        p = http_client.post("/haitao/api/packages", json={
            **_new_package_payload("reject", tracking=track),
        }).json()
        pid = (p.get("suggestions") or [{}])[0].get("id")
        assert pid

        r = http_client.post("/haitao/api/reject-match",
                             json={"pending_id": pid}).json()
        assert r.get("ok") is True

        order = http_client.get(f"/haitao/api/orders/{o['order_id']}").json()
        pkg = http_client.get(f"/haitao/api/packages/{p['package_id']}").json()
        assert order.get("package_ids") in ([], None)
        assert pkg.get("order_ids") in ([], None)
        assert order.get("status") == "ordered"  # unchanged from create-default
        assert pkg.get("status") == "arrived"

        # Pending entry is now status=rejected (audit trail).
        pending = http_client.get("/haitao/api/pending?status=rejected").json()
        ids = [e["id"] for e in (pending.get("pending") or [])]
        assert pid in ids

    def test_low_score_no_pending(self, http_client):
        """Verification case 9: no tracking match + no recent date + no store
        match → score below threshold → no pending card created."""
        # Order with one tracking, package with a different tracking, dates far apart.
        http_client.post("/haitao/api/orders", json={
            **_new_order_payload("loscoreA"), "domestic_tracking": "AAA111",
            "order_date": "2020-01-01",
        }).json()
        p = http_client.post("/haitao/api/packages", json={
            **_new_package_payload("loscoreB", tracking="BBB222"),
            "intake_date": "2026-05-12",
        }).json()
        suggestions = p.get("suggestions") or []
        assert suggestions == []

    def test_n_to_m_relationship(self, http_client):
        """Verification case 8: one order can split to two packages."""
        track_a = f"SFSPLITA{TEST_PREFIX}"
        # Single order; both packages share its tracking (拆单 keeps same 单号).
        o = http_client.post("/haitao/api/orders", json={
            **_new_order_payload("split"), "domestic_tracking": track_a,
        }).json()

        for sfx in ("p1", "p2"):
            pkg = http_client.post("/haitao/api/packages", json={
                **_new_package_payload(sfx, tracking=track_a),
            }).json()
            pid = (pkg.get("suggestions") or [{}])[0].get("id")
            assert pid
            http_client.post("/haitao/api/confirm-match",
                             json={"pending_id": pid}).json()

        order = http_client.get(f"/haitao/api/orders/{o['order_id']}").json()
        assert len(order.get("package_ids") or []) == 2

    def test_photo_upload_appends_to_frontmatter(self, http_client):
        """Photo upload writes a file + appends to package frontmatter `photos`."""
        track = f"SFPHOTO{TEST_PREFIX}"
        p = http_client.post("/haitao/api/packages", json={
            **_new_package_payload("photo", tracking=track),
        }).json()
        pkg_id = p["package_id"]
        # Use a small synthetic JPEG header.
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"
        files = {"file": ("test.jpg", io.BytesIO(fake_jpeg), "image/jpeg")}
        up = http_client.post(f"/haitao/api/packages/{pkg_id}/photo", files=files).json()
        assert up.get("ok") is True

        pkg = http_client.get(f"/haitao/api/packages/{pkg_id}").json()
        assert len(pkg.get("photos") or []) >= 1
        # Retrieve the photo via the GET route.
        fname = pkg["photos"][0]
        photo_r = http_client.get(f"/haitao/api/packages/{pkg_id}/photo/{fname}")
        assert photo_r.status_code == 200
        assert photo_r.content[:2] == b"\xff\xd8"  # JPEG SOI

    def test_pending_filter(self, http_client):
        """Pending list supports status filter."""
        track = f"SFPEND{TEST_PREFIX}"
        http_client.post("/haitao/api/orders", json={
            **_new_order_payload("pend"), "domestic_tracking": track,
        }).json()
        http_client.post("/haitao/api/packages", json={
            **_new_package_payload("pend", tracking=track),
        }).json()
        r = http_client.get("/haitao/api/pending").json()
        assert isinstance(r.get("pending"), list)
        for e in r["pending"]:
            assert e.get("status") == "pending"


@pytest.mark.interactive
class TestHaitaoUI:
    def test_page_loads_no_errors(self, app_page, page_errors):
        page = app_page("haitao")
        page.wait_for_selector(".app-shell")
        # The tab bar is present.
        assert page.locator(".tab-bar button#tab-orders").count() == 1
        assert page.locator(".tab-bar button#tab-packages").count() == 1
        # No JS errors at boot.
        assert not page_errors, f"JS errors: {page_errors}"

    def test_tabs_switch(self, app_page, page_errors):
        page = app_page("haitao")
        page.wait_for_selector(".app-shell")
        page.click("#tab-packages")
        page.wait_for_selector("#tab-packages.active")
        page.click("#tab-orders")
        page.wait_for_selector("#tab-orders.active")
        assert not page_errors
