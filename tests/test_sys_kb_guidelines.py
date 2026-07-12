"""System app tests: KB guidelines (parent + clauses).

Absorbed from the retired `guideline` app 2026-07-10 — routes moved from
`/guideline/api/items` to `/kb/api/guidelines`; vault storage unchanged.
"""

import pytest

from helpers import TEST_PREFIX, assert_ok
from page_helpers import assert_no_js_errors


@pytest.mark.api
class TestKBGuidelinesAPI:
    def test_list_structure(self, http_client):
        data = assert_ok(http_client.get("/kb/api/guidelines"))
        assert "items" in data
        assert "count" in data
        assert isinstance(data["items"], list)

    def test_categories_structure(self, http_client):
        data = assert_ok(http_client.get("/kb/api/guidelines/categories"))
        assert "categories" in data
        assert isinstance(data["categories"], list)

    def test_add_parent(self, http_client):
        title = TEST_PREFIX + "test parent A"
        r = http_client.post(
            "/kb/api/guidelines",
            json={"title": title, "category": TEST_PREFIX + "arch"},
        )
        data = assert_ok(r)
        assert data.get("ok") is True
        gid = data.get("id")
        assert gid
        # Newly created parent has zero clauses
        detail = assert_ok(http_client.get(f"/kb/api/guidelines/{gid}"))
        assert detail.get("clause_count") == 0
        assert detail.get("clauses") == []

    def test_add_requires_title(self, http_client):
        r = http_client.post("/kb/api/guidelines", json={"title": ""}).json()
        assert "error" in r

    def test_add_clause(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "with clauses", "category": TEST_PREFIX + "arch"},
        ).json()["id"]
        a = http_client.post(
            f"/kb/api/guidelines/{gid}/clauses",
            json={"heading": "clause one", "body": "body one"},
        ).json()
        assert a.get("ok") is True
        assert a.get("slug") == "clause-one"
        b = http_client.post(
            f"/kb/api/guidelines/{gid}/clauses",
            json={"heading": "clause two", "body": "body two"},
        ).json()
        assert b.get("ok") is True
        detail = assert_ok(http_client.get(f"/kb/api/guidelines/{gid}"))
        assert detail["clause_count"] == 2
        slugs = [c["slug"] for c in detail["clauses"]]
        assert slugs == ["clause-one", "clause-two"]
        assert detail["clauses"][0]["body"] == "body one"

    def test_add_clause_requires_heading(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "empty-clause-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        r = http_client.post(f"/kb/api/guidelines/{gid}/clauses", json={"heading": "", "body": "x"}).json()
        assert "error" in r

    def test_duplicate_clause_rejected(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "dup-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        http_client.post(
            f"/kb/api/guidelines/{gid}/clauses", json={"heading": "same", "body": "first"},
        )
        r = http_client.post(
            f"/kb/api/guidelines/{gid}/clauses", json={"heading": "same", "body": "second"},
        ).json()
        assert "error" in r and "already exists" in r["error"]

    def test_update_clause_body(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "update-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        slug = http_client.post(
            f"/kb/api/guidelines/{gid}/clauses", json={"heading": "edit me", "body": "old body"},
        ).json()["slug"]
        r = http_client.post(
            f"/kb/api/guidelines/{gid}/clauses/{slug}", json={"body": "new body content"},
        ).json()
        assert r.get("ok") is True
        detail = http_client.get(f"/kb/api/guidelines/{gid}").json()
        clause = next(c for c in detail["clauses"] if c["slug"] == slug)
        assert clause["body"] == "new body content"
        assert clause["heading"] == "edit me"

    def test_delete_clause(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "delete-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        slug = http_client.post(
            f"/kb/api/guidelines/{gid}/clauses", json={"heading": "doomed clause", "body": "x"},
        ).json()["slug"]
        r = http_client.delete(f"/kb/api/guidelines/{gid}/clauses/{slug}").json()
        assert r.get("ok") is True
        detail = http_client.get(f"/kb/api/guidelines/{gid}").json()
        assert all(c["slug"] != slug for c in detail["clauses"])

    def test_set_field_whitelist(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "whitelist", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        bad = http_client.post(
            f"/kb/api/guidelines/{gid}/field", json={"field": "body", "value": "x"}
        ).json()
        assert "error" in bad
        ok = http_client.post(
            f"/kb/api/guidelines/{gid}/field", json={"field": "category", "value": TEST_PREFIX + "moved"}
        ).json()
        assert ok.get("ok") is True

    def test_invalid_status_rejected(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "status-check", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        bad = http_client.post(
            f"/kb/api/guidelines/{gid}/field", json={"field": "status", "value": "nonsense"}
        ).json()
        assert "error" in bad

    def test_deprecate_parent(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "to-deprecate", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        r = http_client.post(f"/kb/api/guidelines/{gid}/deprecate").json()
        assert r.get("ok") is True
        d = http_client.get(f"/kb/api/guidelines/{gid}").json()
        assert d.get("status") == "deprecated"

    def test_detail_missing(self, http_client):
        r = http_client.get("/kb/api/guidelines/this-id-does-not-exist-zzz").json()
        assert "error" in r

    def test_clause_cite_unresolved(self, http_client):
        """A `[[kb:nonexistent]]` marker resolves with resolved=False."""
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "cite-broken-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        # Marker slug must be lowercase kebab — CITE_RE's slug grammar; the
        # TEST_PREFIX-titled parent item is what the cleanup sweep keys on.
        slug = http_client.post(
            f"/kb/api/guidelines/{gid}/clauses",
            json={"heading": "with broken cite", "body": "see [[kb:playwright-test-nonexistent]] for context"},
        ).json()["slug"]
        detail = http_client.get(f"/kb/api/guidelines/{gid}").json()
        clause = next(c for c in detail["clauses"] if c["slug"] == slug)
        cites = clause.get("cites") or []
        assert len(cites) == 1
        assert cites[0]["resolved"] is False
        assert cites[0]["slug"].endswith("-nonexistent")

    def test_delete_parent(self, http_client):
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "to-hard-delete", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        # Add a clause so we know hard-delete drops the whole document
        http_client.post(
            f"/kb/api/guidelines/{gid}/clauses",
            json={"heading": "doomed", "body": "content"},
        )
        r = http_client.delete(f"/kb/api/guidelines/{gid}").json()
        assert r.get("ok") is True
        gone = http_client.get(f"/kb/api/guidelines/{gid}").json()
        assert "error" in gone

    def test_delete_missing(self, http_client):
        r = http_client.delete("/kb/api/guidelines/this-does-not-exist-zzz").json()
        assert "error" in r

    def test_clause_no_cite_field_when_empty(self, http_client):
        """Clauses without any [[kb:]] markers still get a cites field (empty)."""
        gid = http_client.post(
            "/kb/api/guidelines",
            json={"title": TEST_PREFIX + "no-cite-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        http_client.post(
            f"/kb/api/guidelines/{gid}/clauses",
            json={"heading": "plain", "body": "no markers here"},
        )
        detail = http_client.get(f"/kb/api/guidelines/{gid}").json()
        assert detail["clauses"][0]["cites"] == []


@pytest.mark.interactive
class TestKBGuidelinesUI:
    def test_page_and_clause_route(self, page, base_url, page_errors):
        page.goto(base_url + "/kb/pages/guidelines.html")
        page.wait_for_selector("#cats", timeout=5000)
        assert_no_js_errors(page_errors)
