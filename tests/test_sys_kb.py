"""System app tests: Knowledge Base."""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestKBAPI:
    def test_domains(self, http_client):
        data = assert_ok(http_client.get("/kb/api/domains"))
        assert isinstance(data, dict)
        assert "domains" in data
        assert "total_notes" in data
        assert isinstance(data["domains"], list)

    def test_notes_list(self, http_client):
        data = assert_ok(http_client.get("/kb/api/notes"))
        assert isinstance(data, dict)
        assert "notes" in data
        assert "count" in data
        assert isinstance(data["notes"], list)

    def test_notes_filter_by_domain(self, http_client):
        data = assert_ok(http_client.get("/kb/api/notes?domain=power-systems"))
        for n in data["notes"]:
            assert n["domain"] == "power-systems"

    def test_notes_filter_by_kind(self, http_client):
        data = assert_ok(http_client.get("/kb/api/notes?kind=formula"))
        for n in data["notes"]:
            assert n["kind"] == "formula"

    def test_note_detail_missing(self, http_client):
        resp = http_client.get("/kb/api/notes/this-slug-does-not-exist-zzz")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_note_detail_real_when_seeded(self, http_client):
        # If the vault has seed notes, exercise the detail endpoint;
        # otherwise this test no-ops cleanly.
        notes = http_client.get("/kb/api/notes").json().get("notes", [])
        if not notes:
            pytest.skip("no kb notes in vault")
        slug = notes[0]["slug"]
        data = assert_ok(http_client.get(f"/kb/api/notes/{slug}"))
        assert data.get("slug") == slug
        assert "properties" in data
        assert "backlinks" in data
        assert "implemented_in_status" in data

    def test_health(self, http_client):
        data = assert_ok(http_client.get("/kb/api/health"))
        assert "broken_implemented_in" in data
        assert "formulas_missing_verification" in data
        assert "orphans" in data

    def test_blocks_api_returns_404(self, http_client):
        """The legacy `/kb/api/blocks*` surface is retired after the unification."""
        r = http_client.get("/kb/api/blocks")
        assert r.status_code == 404
        r = http_client.get("/kb/api/blocks/anything")
        assert r.status_code == 404

    def test_docs_list_shape(self, http_client):
        """`/kb/api/docs` returns {docs: [...], count}."""
        data = assert_ok(http_client.get("/kb/api/docs"))
        assert "docs" in data
        assert "count" in data
        assert isinstance(data["docs"], list)

    def test_list_docs_returns_kind_doc_notes(self, http_client):
        """After unification, list_docs() filters `tag: kb` by `kind: doc`."""
        # Create a fresh doc and confirm it appears in the docs listing.
        slug = "playwright-test-doc-kind-filter"
        http_client.request("DELETE", f"/kb/api/docs/{slug}")
        r = http_client.post("/kb/api/docs", json={
            "title": "PLAYWRIGHT-TEST-doc-kind-filter",
            "paragraphs": [],
        }).json()
        assert r.get("slug") == slug
        # The note should also be visible via /kb/api/notes?kind=doc.
        notes = http_client.get("/kb/api/notes?kind=doc").json().get("notes", [])
        assert any(n["slug"] == slug for n in notes), "kind=doc filter missed the new doc"
        # …and via /kb/api/docs (specialized listing).
        docs = http_client.get("/kb/api/docs").json().get("docs", [])
        assert any(d["slug"] == slug for d in docs)

    def test_doc_lifecycle(self, http_client):
        """Create a doc, render via paragraph-resolution endpoint."""
        title = "PLAYWRIGHT-TEST-doc-kb-cycle"
        slug = "playwright-test-doc-kb-cycle"
        # Idempotent setup
        http_client.request("DELETE", f"/kb/api/docs/{slug}")
        r = http_client.post("/kb/api/docs", json={"title": title, "paragraphs": []}).json()
        assert r.get("slug") == slug
        # Update paragraphs with a missing noteRef — exercises render path
        upd = http_client.post(f"/kb/api/docs/{slug}/update", json={
            "paragraphs": [{"title": "P1", "noteRefs": ["nonexistent-note"], "content": ""}],
        }).json()
        assert upd.get("ok") is True
        rendered = http_client.get(f"/kb/api/docs/{slug}").json()
        assert "paragraphs" in rendered
        assert rendered["paragraphs"][0].get("rendered", [{}])[0].get("missing") is True

    def test_render_doc_resolves_note_section_anchor(self, http_client):
        """`noteRef: slug#section` resolves to the named heading via section endpoint."""
        # Use any existing note with a real ## heading to test against. Skip if
        # none in the vault — environments without seeded content should still pass.
        notes = http_client.get("/kb/api/notes").json().get("notes", [])
        target = None
        for n in notes:
            if n.get("kind") in ("concept", "formula", "case", "lesson", "reference"):
                # Pick the first one that has at least one section
                detail = http_client.get(f"/kb/api/notes/{n['slug']}").json()
                body = detail.get("body") or ""
                # Find a ## heading we can target
                import re as _re
                m = _re.search(r"^##\s+(.+)$", body, _re.MULTILINE)
                if m:
                    target = (n["slug"], m.group(1).strip())
                    break
        if not target:
            pytest.skip("no KB note with a ## heading available in vault")
        slug, section = target
        # Hit the section endpoint directly
        sec = http_client.get(f"/kb/api/notes/{slug}/section/{section}").json()
        assert sec.get("body"), f"section body empty for {slug}#{section}"
        assert sec.get("section") == section

    def test_docs_page_loads(self, http_client):
        r = http_client.get("/kb/pages/docs.html")
        assert r.status_code == 200
        assert "KB Documents" in r.text

    def test_implementations_endpoint(self, http_client):
        """`/kb/api/implementations/<path>` returns formulas declaring the path in `implemented_in:`."""
        # The path doesn't have to exist on disk — the endpoint reflects what
        # frontmatter declares. Try a known path; if no notes declare it, the
        # response is just an empty list (still a 200).
        r = http_client.get("/kb/api/implementations/engines/thermal/iec60287/conductor_losses.py")
        assert r.status_code == 200
        data = r.json()
        assert "slugs" in data
        assert "count" in data
        assert isinstance(data["slugs"], list)

    def test_references_endpoint_shape(self, http_client):
        """`/kb/api/references` returns {references: [...], count}."""
        data = assert_ok(http_client.get("/kb/api/references"))
        assert "references" in data
        assert "count" in data
        assert isinstance(data["references"], list)
        # Each entry is {standard, edition, clause, slug}
        for r in data["references"]:
            assert "standard" in r and "slug" in r

    def test_health_includes_uncited_references(self, http_client):
        """The health response gains an `uncited_references` bucket."""
        data = assert_ok(http_client.get("/kb/api/health"))
        assert "uncited_references" in data
        assert isinstance(data["uncited_references"], list)

    def test_note_detail_references_structured(self, http_client):
        """get_note() resolves `references:` strings into {text, target_slug, in_kb} dicts."""
        notes = http_client.get("/kb/api/notes").json().get("notes", [])
        # Find any note with references in frontmatter
        for note in notes:
            data = http_client.get(f"/kb/api/notes/{note['slug']}").json()
            refs = (data.get("properties") or {}).get("references")
            if not refs:
                continue
            # Each entry must be the new structured shape
            for r in refs:
                if isinstance(r, str):
                    # Pure legacy passthrough — acceptable but flag for follow-up
                    continue
                assert "text" in r, f"reference missing text: {r}"
                assert "in_kb" in r, f"reference missing in_kb flag: {r}"
            return
        pytest.skip("no kb notes with references in vault")


@pytest.mark.api
class TestKBCitationParser:
    """Unit-test the citation regex + normalizers via in-process import.

    These tests don't hit the running daemon — they verify the parsing primitives.
    """

    def test_norm_standard_handles_hyphen_variants(self):
        from apps.kb.app import _norm_standard
        assert _norm_standard("IEC 60287-1-1") == "IEC 60287-1-1"
        assert _norm_standard("IEC60287-1-1") == "IEC 60287-1-1"
        assert _norm_standard("iec 60287‑1‑1") == "IEC 60287-1-1"  # NB hyphen
        assert _norm_standard("CIGRE TB 880") == "CIGRE TB 880"
        assert _norm_standard("AS/NZS 3008.1.1") == "AS/NZS 3008.1.1"

    def test_norm_clause_strips_section_marker(self):
        from apps.kb.app import _norm_clause
        assert _norm_clause("§5.1.3") == "5.1.3"
        assert _norm_clause("§ 5.1.3") == "5.1.3"
        assert _norm_clause("5.1.3") == "5.1.3"
        assert _norm_clause(None) is None
        assert _norm_clause("") is None

    def test_parse_citation_full_triple(self):
        from apps.kb.app import _parse_citation
        assert _parse_citation("IEC 60287-1-1:2023 §5.1.3 (description)") == (
            "IEC 60287-1-1", "2023", "5.1.3",
        )

    def test_parse_citation_standard_only(self):
        from apps.kb.app import _parse_citation
        # No clause → clause is None; this is a whole-standard cite.
        result = _parse_citation("IEC 60228 (DC resistance R_0 at 20 °C)")
        assert result == ("IEC 60228", None, None)

    def test_parse_citation_cigre_no_edition(self):
        from apps.kb.app import _parse_citation
        assert _parse_citation("CIGRE TB 880 §4.6.4") == (
            "CIGRE TB 880", None, "4.6.4",
        )

    def test_parse_citation_range_picks_lower_bound(self):
        from apps.kb.app import _parse_citation
        # Range "§5.1.4–5.1.5" links to the lower bound only (V1 semantics).
        std, ed, cl = _parse_citation("IEC 60287-1-1:2023 §5.1.4–5.1.5")
        assert std == "IEC 60287-1-1"
        assert ed == "2023"
        assert cl == "5.1.4"

    def test_parse_citation_unmatchable(self):
        from apps.kb.app import _parse_citation
        # Anders textbook — no standard pattern at the start of the string.
        assert _parse_citation("Anders, Rating of Electric Power Cables — §3.4") is None


@pytest.mark.interactive
class TestKBUI:
    def test_index_loads(self, page, page_errors, base_url):
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        assert "Knowledge Base" in page.content()
        assert_no_js_errors(page_errors)

    def test_filter_kind(self, page, page_errors, base_url):
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        page.select_option("#filter-kind", "formula")
        wait_briefly(page)
        assert_no_js_errors(page_errors)

    def test_search_input(self, page, page_errors, base_url):
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        page.fill("#filter-q", "carson")
        wait_briefly(page)
        assert_no_js_errors(page_errors)

    def test_click_note_opens_detail(self, page, page_errors, base_url):
        """Clicking a note row navigates to detail without requiring a refresh."""
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        # First note row in the list
        first = page.locator(".note-row").first
        first.wait_for(state="visible", timeout=10000)
        slug = first.get_attribute("data-slug")
        assert slug, "no data-slug on first note row"
        first.click()
        wait_briefly(page)
        # Detail view shows up + URL hash reflects the slug
        assert page.locator("#detail-wrap.show").count() == 1, "detail view did not open"
        assert page.locator("#detail-head h1").is_visible(), "detail head missing"
        assert slug in page.url, f"hash not set to {slug}, url={page.url}"
        assert_no_js_errors(page_errors)

    def test_back_button_returns_to_list(self, page, page_errors, base_url):
        """The Back button clears the hash and re-shows the list view."""
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        page.locator(".note-row").first.click()
        wait_briefly(page)
        page.locator("#detail-back").click()
        wait_briefly(page)
        assert page.locator("#detail-wrap.show").count() == 0, "detail view still showing"
        assert "#" not in page.url or page.url.endswith("#"), f"hash not cleared: {page.url}"
        assert_no_js_errors(page_errors)
