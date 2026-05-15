"""System app tests: Guideline (parent + clauses) — 14 use cases."""

import pytest

from helpers import TEST_PREFIX, assert_ok
from page_helpers import assert_no_js_errors


@pytest.mark.api
class TestGuidelineAPI:
    def test_list_structure(self, http_client):
        data = assert_ok(http_client.get("/guideline/api/items"))
        assert "items" in data
        assert "count" in data
        assert isinstance(data["items"], list)

    def test_categories_structure(self, http_client):
        data = assert_ok(http_client.get("/guideline/api/categories"))
        assert "categories" in data
        assert isinstance(data["categories"], list)

    def test_add_parent(self, http_client):
        title = TEST_PREFIX + "test parent A"
        r = http_client.post(
            "/guideline/api/items",
            json={"title": title, "category": TEST_PREFIX + "arch"},
        )
        data = assert_ok(r)
        assert data.get("ok") is True
        gid = data.get("id")
        assert gid
        # Newly created parent has zero clauses
        detail = assert_ok(http_client.get(f"/guideline/api/items/{gid}"))
        assert detail.get("clause_count") == 0
        assert detail.get("clauses") == []

    def test_add_requires_title(self, http_client):
        r = http_client.post("/guideline/api/items", json={"title": ""}).json()
        assert "error" in r

    def test_add_clause(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "with clauses", "category": TEST_PREFIX + "arch"},
        ).json()["id"]
        a = http_client.post(
            f"/guideline/api/items/{gid}/clauses",
            json={"heading": "clause one", "body": "body one"},
        ).json()
        assert a.get("ok") is True
        assert a.get("slug") == "clause-one"
        b = http_client.post(
            f"/guideline/api/items/{gid}/clauses",
            json={"heading": "clause two", "body": "body two"},
        ).json()
        assert b.get("ok") is True
        detail = assert_ok(http_client.get(f"/guideline/api/items/{gid}"))
        assert detail["clause_count"] == 2
        slugs = [c["slug"] for c in detail["clauses"]]
        assert slugs == ["clause-one", "clause-two"]
        assert detail["clauses"][0]["body"] == "body one"

    def test_add_clause_requires_heading(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "empty-clause-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        r = http_client.post(f"/guideline/api/items/{gid}/clauses", json={"heading": "", "body": "x"}).json()
        assert "error" in r

    def test_duplicate_clause_rejected(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "dup-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        http_client.post(
            f"/guideline/api/items/{gid}/clauses", json={"heading": "same", "body": "first"},
        )
        r = http_client.post(
            f"/guideline/api/items/{gid}/clauses", json={"heading": "same", "body": "second"},
        ).json()
        assert "error" in r and "already exists" in r["error"]

    def test_update_clause_body(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "update-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        slug = http_client.post(
            f"/guideline/api/items/{gid}/clauses", json={"heading": "edit me", "body": "old body"},
        ).json()["slug"]
        r = http_client.post(
            f"/guideline/api/items/{gid}/clauses/{slug}", json={"body": "new body content"},
        ).json()
        assert r.get("ok") is True
        detail = http_client.get(f"/guideline/api/items/{gid}").json()
        clause = next(c for c in detail["clauses"] if c["slug"] == slug)
        assert clause["body"] == "new body content"
        assert clause["heading"] == "edit me"

    def test_delete_clause(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "delete-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        slug = http_client.post(
            f"/guideline/api/items/{gid}/clauses", json={"heading": "doomed clause", "body": "x"},
        ).json()["slug"]
        r = http_client.delete(f"/guideline/api/items/{gid}/clauses/{slug}").json()
        assert r.get("ok") is True
        detail = http_client.get(f"/guideline/api/items/{gid}").json()
        assert all(c["slug"] != slug for c in detail["clauses"])

    def test_set_field_whitelist(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "whitelist", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        bad = http_client.post(
            f"/guideline/api/items/{gid}/field", json={"field": "body", "value": "x"}
        ).json()
        assert "error" in bad
        ok = http_client.post(
            f"/guideline/api/items/{gid}/field", json={"field": "category", "value": TEST_PREFIX + "moved"}
        ).json()
        assert ok.get("ok") is True

    def test_invalid_status_rejected(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "status-check", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        bad = http_client.post(
            f"/guideline/api/items/{gid}/field", json={"field": "status", "value": "nonsense"}
        ).json()
        assert "error" in bad

    def test_deprecate_parent(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "to-deprecate", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        r = http_client.post(f"/guideline/api/items/{gid}/deprecate").json()
        assert r.get("ok") is True
        d = http_client.get(f"/guideline/api/items/{gid}").json()
        assert d.get("status") == "deprecated"

    def test_detail_missing(self, http_client):
        r = http_client.get("/guideline/api/items/this-id-does-not-exist-zzz").json()
        assert "error" in r

    def test_clause_cite_unresolved(self, http_client):
        """A `[[kb:nonexistent]]` marker resolves with resolved=False."""
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "cite-broken-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        slug = http_client.post(
            f"/guideline/api/items/{gid}/clauses",
            json={"heading": "with broken cite", "body": f"see [[kb:{TEST_PREFIX}-nonexistent]] for context"},
        ).json()["slug"]
        detail = http_client.get(f"/guideline/api/items/{gid}").json()
        clause = next(c for c in detail["clauses"] if c["slug"] == slug)
        cites = clause.get("cites") or []
        assert len(cites) == 1
        assert cites[0]["resolved"] is False
        assert cites[0]["slug"].endswith("-nonexistent")

    def test_delete_parent(self, http_client):
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "to-hard-delete", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        # Add a clause so we know hard-delete drops the whole document
        http_client.post(
            f"/guideline/api/items/{gid}/clauses",
            json={"heading": "doomed", "body": "content"},
        )
        r = http_client.delete(f"/guideline/api/items/{gid}").json()
        assert r.get("ok") is True
        gone = http_client.get(f"/guideline/api/items/{gid}").json()
        assert "error" in gone

    def test_delete_missing(self, http_client):
        r = http_client.delete("/guideline/api/items/this-does-not-exist-zzz").json()
        assert "error" in r

    def test_clause_no_cite_field_when_empty(self, http_client):
        """Clauses without any [[kb:]] markers still get a cites field (empty)."""
        gid = http_client.post(
            "/guideline/api/items",
            json={"title": TEST_PREFIX + "no-cite-test", "category": TEST_PREFIX + "x"},
        ).json()["id"]
        http_client.post(
            f"/guideline/api/items/{gid}/clauses",
            json={"heading": "plain", "body": "no markers here"},
        )
        detail = http_client.get(f"/guideline/api/items/{gid}").json()
        assert detail["clauses"][0]["cites"] == []


@pytest.mark.interactive
class TestGuidelineUI:
    def test_page_and_clause_route(self, page, base_url):
        page.goto(base_url + "/guideline/")
        page.wait_for_selector("#cats", timeout=5000)
        assert_no_js_errors(page)
