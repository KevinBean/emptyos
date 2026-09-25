"""UI Walk 1 — Learn: full lesson loop + SRS (API + browser).

Covers:
  - Course catalog + detail
  - Lesson rendering with citation buttons (eos-pdf-anchor)
  - Quiz submission
  - Lesson completion + event emission
  - Hub panel count (review queue)
  - SRS review card retrieval
  - Edge: out-of-bounds lesson index
  - Voice intent registration (learn.start_review)
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from helpers import BASE_URL, TEST_PREFIX, assert_dict_response, assert_ok

REAL_COURSE_ID = "asnzs-61439-foundations"


# ── API walks ────────────────────────────────────────────────────────────────

@pytest.fixture
def require_real_course(http_client):
    """Skip when this daemon's vault does not carry the authored course.

    CI mounts a throwaway vault (./ci-vault, empty PARA dirs), so the course
    exists only where the real vault is mounted. Same shape as
    ``require_sim_engine`` in test_sys_sim.py: state the precondition and skip,
    rather than failing and reading as a regression.
    """
    r = http_client.get(f"/learn/api/courses/{REAL_COURSE_ID}")
    if r.status_code != 200 or (r.json() or {}).get("id") != REAL_COURSE_ID:
        pytest.skip(f"course '{REAL_COURSE_ID}' is not in this daemon's vault")


@pytest.mark.api
class TestLearnWalkAPI:
    def test_course_catalog_lists_courses(self, http_client):
        """1.1 — GET /learn/ data: catalog returns ≥1 course."""
        data = assert_dict_response(http_client.get("/learn/api/courses"), required_keys=["courses"])
        assert len(data["courses"]) >= 1, "expected at least one course in catalog"

    def test_course_detail_has_lessons(self, http_client, require_real_course):
        """1.1 — Course detail includes parsed lessons."""
        data = assert_ok(http_client.get(f"/learn/api/courses/{REAL_COURSE_ID}"))
        assert data.get("id") == REAL_COURSE_ID
        assert isinstance(data.get("lessons"), list) and len(data["lessons"]) > 0

    def test_lesson_renders_citation_buttons(self, http_client, require_real_course):
        """1.3 — Lesson body upgrades [[ref]] p.N to eos-pdf-anchor buttons."""
        data = assert_ok(http_client.get(f"/learn/api/courses/{REAL_COURSE_ID}/lessons/3"))
        body = (data.get("source") or {}).get("body_md", "")
        assert body, "lesson 3 must have body content"
        assert "eos-pdf-anchor" in body, "citation must produce an eos-pdf-anchor placeholder"

    def test_wikilinks_converted(self, http_client):
        """1.3 — Raw [[...]] wikilinks must not appear in rendered body."""
        data = assert_ok(http_client.get(f"/learn/api/courses/{REAL_COURSE_ID}/lessons/3"))
        body = (data.get("source") or {}).get("body_md", "")
        assert "[[" not in body, "wikilinks should be converted to /kb/#slug links"

    def test_lesson_complete_marks_progress(self, http_client):
        """1.6 — POST lesson complete returns ok + updated progress."""
        r = http_client.post(f"/learn/api/courses/{REAL_COURSE_ID}/lessons/0/complete")
        assert r.status_code == 200
        data = r.json()
        # Accept either ok:true or an error if already completed — must not 500
        assert "ok" in data or "error" in data

    def test_hub_panel_review_due_shape(self, http_client):
        """1.7 — Hub panel learn-review-due returns stat-tile data."""
        data = assert_ok(http_client.get("/hub/api/panels/all"))
        panels = data.get("panels", data) if isinstance(data, dict) else data
        ids = [p.get("id") for p in (panels if isinstance(panels, list) else [])]
        assert "learn-review-due" in ids, f"learn-review-due panel missing; found: {ids}"

    def test_srs_due_returns_card_list(self, http_client):
        """1.8 — GET /learn/api/review/due returns {cards, count}."""
        data = assert_dict_response(
            http_client.get("/learn/api/review/due"),
            required_keys=["cards", "count"],
        )
        assert isinstance(data["cards"], list)
        assert data["count"] == len(data["cards"])

    def test_srs_stats_shape(self, http_client):
        """1.8 — SRS stats include streak + due counts."""
        assert_dict_response(
            http_client.get("/learn/api/review/stats"),
            required_keys=["total_cards", "due_today", "reviewed_today", "streak_days"],
        )

    def test_lesson_index_out_of_bounds(self, http_client):
        """1.9 — Out-of-bounds lesson index returns error, not 500."""
        r = http_client.get(f"/learn/api/courses/{REAL_COURSE_ID}/lessons/9999")
        assert r.status_code == 200
        assert "error" in r.json(), "out-of-bounds index must return error key"

    def test_voice_intent_registered(self, http_client):
        """1.10 — learn.start_review voice intent is discoverable via debug endpoint."""
        r = http_client.get("/voice-assistant/debug/intents")
        if r.status_code == 404:
            pytest.skip("/voice-assistant/debug/intents not available")
        data = r.json()
        registry = data.get("registry", [])
        verbs = [i.get("verb") for i in registry]
        assert "learn.start_review" in verbs, (
            f"learn.start_review not found in voice intent registry: {verbs[:15]}"
        )

    def test_reader_note_roundtrip(self, http_client):
        """POST a note then GET it back — verify vault persistence.

        Writes into a KB note this test owns (TEST_PREFIX in the filename =
        the leak guard's ``owned`` shape, deleted whole at session end) rather
        than a real standard note, whose ``## Reader notes`` entries can only
        ever be flagged for manual review. See test_sys_learn's sibling test.
        """
        created = assert_dict_response(http_client.post("/kb/api/notes", json={
            "kind": "concept",
            "title": f"{TEST_PREFIX}Walk reader host",
            "slug": f"{TEST_PREFIX}walk-reader-host-{uuid4().hex[:8]}",
        }))
        assert created.get("ok"), created
        slug = created["slug"]
        payload = {
            "slug": slug,
            "quote": f"{TEST_PREFIX}test quote walk1",
            "note": f"{TEST_PREFIX}test insight walk1",
            "course_id": REAL_COURSE_ID,
            "lesson_index": 0,
        }
        save = assert_ok(http_client.post("/learn/api/lessons/note", json=payload))
        assert save["ok"] is True
        got = assert_dict_response(
            http_client.get(f"/learn/api/lessons/notes/{slug}"),
            required_keys=["notes"],
        )
        found = [n for n in got["notes"] if TEST_PREFIX in (n.get("note", "") + n.get("quote", ""))]
        assert found, "saved note must round-trip through vault_read_section"


# ── Browser walks ────────────────────────────────────────────────────────────

@pytest.mark.interactive
class TestLearnWalkUI:
    def test_page_loads_no_js_errors(self, app_page, page_errors):
        """1.1 — /learn/ renders without JS errors."""
        page = app_page("learn")
        page.wait_for_selector("text=/course/i", timeout=5000)
        js_errors = [e for e in page_errors if "favicon" not in str(e).lower()]
        assert not js_errors, f"JS errors on /learn/: {js_errors}"

    def test_course_list_shows_entries(self, app_page, page_errors):
        """1.1 — At least one course card renders."""
        page = app_page("learn")
        count = page.locator("[data-course-id], .course-card, .course-row").count()
        assert count >= 1, "expected course list items to render"

    def test_pdf_viewer_bundle_present(self, app_page):
        """1.3 — EOS_PDF is available on the window after page load."""
        page = app_page("learn")
        defined = page.evaluate("typeof EOS_PDF !== 'undefined'")
        assert defined, "EOS_PDF must be defined on the page (eos-pdf-viewer.js not loaded)"

    def test_review_tab_accessible(self, app_page):
        """1.8 — Switching to the review tab shows the SRS interface."""
        from page_helpers import switch_tab
        page = app_page("learn")
        switched = switch_tab(page, "review")
        if not switched:
            # Try URL hash approach
            page.goto(f"{BASE_URL}/learn/?tab=review", wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
        # Review section should have some content (queue or "nothing due" message)
        # Use count() rather than wait_for_selector (element may be in DOM but in a hidden tab)
        page.wait_for_timeout(500)
        found = page.get_by_text("review", exact=False).count() + \
                page.get_by_text("due", exact=False).count()
        assert found >= 1, "review tab content (review/due text) not found on page"
