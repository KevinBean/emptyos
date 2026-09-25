"""System app tests: Learn — 12 use cases (API only, no LLM).

Covers: course catalog, course detail, lesson load (incl. citation
post-processing), authoring (save + edit), KB picker for the wizard,
SRS endpoints (stats / due / grade), and reader-notes save + fetch.

Quiz generation (`POST /api/courses/.../quiz`) is intentionally NOT
tested here — it hits an LLM, is slow, and the cache makes coverage
non-deterministic. Manual smoke-tested instead.

Test data uses TEST_PREFIX so the session-autouse cleanup catches it.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok


# Fixtures already in the vault (from session 2026-05-24): the first real course
# + the asnzs-61439 concept note + the §10.10 clause. Tests reference these
# stable slugs rather than seeding their own — the vault is authoritative.
def _expected_sources() -> tuple:
    """The unified review queue's sources, read from review_all.py rather than
    hardcoded here.

    These assertions were already stale before picture-dict arrived: soundcheck
    had been added to SOURCES and the literal list was never updated, so the
    suite failed for a reason unrelated to the code under test. Deriving them
    means the next silo added cannot break this.
    """
    import ast

    src = Path(__file__).resolve().parent.parent / "apps/public/standard/learn/review_all.py"
    for node in ast.parse(src.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SOURCES":
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("SOURCES not found in review_all.py")


EXPECTED_SOURCES = _expected_sources()

REAL_COURSE_ID = "asnzs-61439-foundations"
REAL_CONCEPT_SLUG = "asnzs-61439"
REAL_CLAUSE_SLUG = "asnzs-61439-1-10-10"


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


@pytest.fixture
def require_real_concept(http_client):
    """Skip when the authored KB concept note is not in this daemon's vault."""
    r = http_client.get("/learn/api/authoring/kb-notes?kind=concept")
    slugs = {n.get("slug") for n in (r.json() or {}).get("notes", [])} if r.status_code == 200 else set()
    if REAL_CONCEPT_SLUG not in slugs:
        pytest.skip(f"KB concept '{REAL_CONCEPT_SLUG}' is not in this daemon's vault")


@pytest.mark.api
class TestLearnAPI:
    # ── Catalog + detail ───────────────────────────────────────

    def test_courses_list_shape(self, http_client):
        """GET /learn/api/courses returns {courses: [...]} with expected fields."""
        data = assert_dict_response(http_client.get("/learn/api/courses"), required_keys=["courses"])
        assert isinstance(data["courses"], list)
        if data["courses"]:
            c = data["courses"][0]
            for key in ("id", "title", "lesson_count", "completed_count"):
                assert key in c, f"Course missing key {key}: {c}"

    def test_real_course_detail(self, http_client, require_real_course):
        """GET /learn/api/courses/<id> returns the real course with parsed lessons."""
        data = assert_dict_response(http_client.get(f"/learn/api/courses/{REAL_COURSE_ID}"))
        assert data.get("id") == REAL_COURSE_ID
        assert isinstance(data.get("lessons"), list)
        assert len(data["lessons"]) > 0, "expected lessons populated from lessons_json"
        for l in data["lessons"]:
            for key in ("index", "title", "kind", "slug"):
                assert key in l

    def test_lesson_load_with_citations(self, http_client, require_real_course):
        """GET /learn/api/courses/<id>/lessons/3 (§10.10) returns body with
        PDF anchor button + converted wikilinks."""
        data = assert_dict_response(http_client.get(
            f"/learn/api/courses/{REAL_COURSE_ID}/lessons/3"
        ))
        src = data.get("source", {})
        body = src.get("body_md", "")
        assert body, "lesson 3 should have body content"
        # PDF citation [[abb-workbook-iec-61439]] p.67 must be rewritten to a button
        assert "eos-pdf-anchor" in body, "PDF citation should produce a button placeholder"
        # Wikilinks should land as /kb/#<slug>, not literal [[...]]
        assert "[[" not in body or "/kb/#" in body, "wikilinks should be converted"

    def test_lesson_index_bounds(self, http_client):
        """Negative + out-of-range lesson indices return clean errors, not 500."""
        # Negative index
        r = http_client.get(f"/learn/api/courses/{REAL_COURSE_ID}/lessons/-1")
        assert r.status_code == 200
        assert "error" in r.json()
        # Way past end
        r = http_client.get(f"/learn/api/courses/{REAL_COURSE_ID}/lessons/9999")
        assert r.status_code == 200
        assert "error" in r.json()

    # ── Diagnostic (dark flag — disabled contract) ─────────────

    def test_diagnostic_disabled_by_default(self, http_client):
        """The diagnostic feature ships dark. With the flag off, status
        returns {enabled: false} and generate/submit/apply refuse cleanly
        (never 500). This is the regression contract: flag off = inert."""
        base = f"/learn/api/courses/{REAL_COURSE_ID}/diagnostic"
        st = assert_dict_response(http_client.get(base))
        if st.get("enabled"):
            pytest.skip("diagnostic flag is enabled on this daemon")
        assert st == {"enabled": False}
        # Generate refuses with a hint, not a crash.
        gen = http_client.post(base, json={})
        assert gen.status_code == 200
        assert gen.json().get("error") == "disabled"
        # Submit + apply also refuse.
        for path in ("/submit", "/apply"):
            r = http_client.post(base + path, json={"answers": {}, "questions": []})
            assert r.status_code == 200
            assert r.json().get("error") == "disabled"

    # ── Authoring ──────────────────────────────────────────────

    def test_authoring_kb_notes_list(self, http_client, require_real_concept):
        """GET /learn/api/authoring/kb-notes returns filterable list excluding kind:doc."""
        data = assert_dict_response(
            http_client.get("/learn/api/authoring/kb-notes?kind=concept"),
            required_keys=["notes", "count"],
        )
        assert isinstance(data["notes"], list)
        # No doc-kind notes in the picker (would be course definitions themselves).
        for n in data["notes"]:
            assert n.get("kind") != "doc"
        # The real concept note should be present.
        slugs = {n["slug"] for n in data["notes"]}
        assert REAL_CONCEPT_SLUG in slugs, f"expected {REAL_CONCEPT_SLUG} in concept list"

    def test_course_save_create_then_update(self, http_client):
        """POST /api/courses/save creates a course; second POST same slug updates."""
        course_id = f"{TEST_PREFIX}learn-test-course".lower().replace(" ", "-")
        payload = {
            "course_id": course_id,
            "title": f"{TEST_PREFIX}Learn test course",
            "description": "Auto-generated by test_sys_learn.py — safe to delete.",
            "level": "intermediate",
            "domain": "test",
            "topic": "smoke",
            "duration_min": 5,
            "lessons": [
                {"slug": REAL_CONCEPT_SLUG, "title": "First", "kind": "read", "duration_min": 3},
            ],
        }
        # Create
        r1 = assert_dict_response(
            http_client.post("/learn/api/courses/save", json=payload),
            required_keys=["ok", "course_id", "is_update"],
        )
        assert r1["ok"] is True
        assert r1["course_id"] == course_id
        assert r1["is_update"] is False
        # Update — same slug, modified title
        payload["title"] = f"{TEST_PREFIX}Learn test course (edited)"
        payload["lessons"].append(
            {"slug": REAL_CLAUSE_SLUG, "title": "Second", "kind": "read", "duration_min": 5}
        )
        r2 = assert_ok(http_client.post("/learn/api/courses/save", json=payload))
        assert r2["is_update"] is True
        # Verify catalog reflects the edit (2 lessons now)
        detail = assert_ok(http_client.get(f"/learn/api/courses/{course_id}"))
        assert len(detail["lessons"]) == 2

    def test_course_save_validation(self, http_client):
        """Missing title → error; empty lessons → error; missing slug → error."""
        for payload, expect_in_error in [
            ({"title": "", "lessons": [{"slug": "x"}]}, "title"),
            ({"title": "t", "lessons": []}, "lesson"),
            ({"title": "t", "lessons": [{"slug": ""}]}, "slug"),
        ]:
            r = http_client.post("/learn/api/courses/save", json=payload)
            assert r.status_code == 200, "validation returns 200 with error field, not HTTP error"
            body = r.json()
            assert "error" in body, f"expected error key for {payload}"
            assert expect_in_error in body["error"].lower(), (
                f"error '{body['error']}' missing '{expect_in_error}' marker"
            )

    # ── SRS ────────────────────────────────────────────────────

    def test_review_stats_shape(self, http_client):
        """GET /learn/api/review/stats returns the 4-key dict."""
        assert_dict_response(
            http_client.get("/learn/api/review/stats"),
            required_keys=["total_cards", "due_today", "reviewed_today", "streak_days"],
        )

    def test_review_due_returns_card_list(self, http_client):
        """GET /learn/api/review/due returns {cards, count}."""
        data = assert_dict_response(
            http_client.get("/learn/api/review/due"),
            required_keys=["cards", "count"],
        )
        assert isinstance(data["cards"], list)
        assert data["count"] == len(data["cards"])

    def test_review_grade_updates_srs(self, http_client):
        """POST /learn/api/review/grade with score updates SRS entry shape."""
        # Use a stable test slug — grade by score (auto-mapped to quality).
        r = assert_ok(http_client.post(
            "/learn/api/review/grade",
            json={"slug": f"{TEST_PREFIX}srs-test-slug", "score": 80},
        ))
        assert r["ok"] is True
        entry = r["entry"]
        for key in ("s", "d", "review_count", "next_review", "last_score"):
            assert key in entry, f"SRS entry missing {key}: {entry}"
        assert entry["last_score"] == 80

    def test_review_grade_rejects_bad_input(self, http_client):
        """Missing slug or bad quality/score → error, not crash."""
        r = http_client.post("/learn/api/review/grade", json={})
        assert "error" in r.json()
        r = http_client.post("/learn/api/review/grade", json={"slug": "x", "quality": 99})
        assert "error" in r.json()

    # ── Unified review queue ───────────────────────────────────
    #
    # dictionary + media are optional_apps — every assertion below must hold
    # on a public install where neither is present.

    def test_review_all_shape(self, http_client):
        """GET /learn/api/review/all returns the aggregated queue envelope."""
        data = assert_dict_response(
            http_client.get("/learn/api/review/all"),
            required_keys=["cards", "counts", "sources", "total_due"],
        )
        assert isinstance(data["cards"], list)
        for source in EXPECTED_SOURCES:
            assert source in data["counts"], f"counts missing {source}"
            assert isinstance(data["counts"][source], int)
        assert data["total_due"] == sum(data["counts"].values())
        for card in data["cards"]:
            for key in ("app", "id", "kind", "front", "back", "due", "meta"):
                assert key in card, f"card missing {key}: {card}"
            assert card["app"] in EXPECTED_SOURCES
            if card["app"] == "learn":
                # The MCQ is generated lazily per card, never during listing.
                assert card["kind"] == "quiz"
                assert card["back"] == ""

    def test_review_all_learn_source_always_present(self, http_client):
        """Learn's own source never degrades, even with the optional apps gone."""
        data = assert_ok(http_client.get("/learn/api/review/all"))
        assert data["sources"]["learn"]["available"] is True
        for source in ("dictionary", "media"):
            assert isinstance(data["sources"][source]["available"], bool)

    def test_grade_item_learn_score(self, http_client):
        """POST grade-item with an auto-scored MCQ result."""
        r = assert_ok(http_client.post(
            "/learn/api/review/grade-item",
            json={"app": "learn", "id": f"{TEST_PREFIX}srs-unified", "score": 80},
        ))
        assert r["ok"] is True
        assert r["app"] == "learn"
        assert r["next_review"]

    def test_grade_item_learn_rating(self, http_client):
        """POST grade-item with an Anki-style rating instead of a score."""
        r = assert_ok(http_client.post(
            "/learn/api/review/grade-item",
            json={"app": "learn", "id": f"{TEST_PREFIX}srs-unified", "rating": "good"},
        ))
        assert r["ok"] is True
        assert r["next_review"]

    @pytest.mark.parametrize("payload", [
        {},
        {"app": "nope", "id": "x", "rating": "good"},
        {"app": "learn", "rating": "good"},
        {"app": "learn", "id": "x", "rating": "meh"},
        {"app": "dictionary", "id": "x", "score": 80},
    ])
    def test_grade_item_rejects_bad_input(self, http_client, payload):
        """Bad app / id / rating → {error}, HTTP 200, never a 500."""
        r = http_client.post("/learn/api/review/grade-item", json=payload)
        assert r.status_code == 200
        assert "error" in r.json()

    @pytest.mark.parametrize("app_id", ["dictionary", "media"])
    def test_grade_item_optional_app_soft(self, http_client, app_id):
        """Optional-app grades resolve either way — installed or absent."""
        r = http_client.post(
            "/learn/api/review/grade-item",
            json={"app": app_id, "id": f"{TEST_PREFIX}nonexistent", "rating": "good"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "ok" in body or "error" in body

    def test_review_stats_unified_key(self, http_client):
        """Stats gains `unified` without losing its original four keys."""
        data = assert_dict_response(
            http_client.get("/learn/api/review/stats"),
            required_keys=["total_cards", "due_today", "reviewed_today", "streak_days", "unified"],
        )
        assert isinstance(data["unified"]["total_due"], int)
        assert set(data["unified"]["counts"]) == set(EXPECTED_SOURCES)

    # ── Reader notes ───────────────────────────────────────────

    def test_save_then_read_reader_note(self, http_client):
        """POST a note then GET it back — round-trip via vault_append_section.

        Targets a KB note this test creates, never a real one. A reader note is
        a multi-line entry (``- **date** · [[slug]]`` + an indented ``>`` quote
        + an insight), so the leak guard can only flag the continuation lines
        for manual review — stripping them would orphan the clean leader
        bullet. Owning the whole note makes it one deletable unit instead:
        TEST_PREFIX in the filename is the guard's ``owned`` shape.
        """
        slug = f"{TEST_PREFIX}reader-host-{uuid4().hex[:8]}"
        created = assert_dict_response(http_client.post("/kb/api/notes", json={
            "kind": "concept", "title": f"{TEST_PREFIX}Reader note host", "slug": slug,
        }))
        assert created.get("ok"), created
        slug = created["slug"]

        payload = {
            "slug": slug,
            "quote": f"{TEST_PREFIX}quoted phrase",
            "note": f"{TEST_PREFIX}my insight about the standard",
            "course_id": REAL_COURSE_ID,
            "lesson_index": 0,
        }
        save = assert_dict_response(
            http_client.post("/learn/api/lessons/note", json=payload),
            required_keys=["ok"],
        )
        assert save["ok"] is True
        # Read back — note should appear in the parsed list.
        got = assert_dict_response(
            http_client.get(f"/learn/api/lessons/notes/{slug}"),
            required_keys=["notes"],
        )
        found = [n for n in got["notes"] if TEST_PREFIX in (n.get("note", "") + n.get("quote", ""))]
        assert found, "saved note should round-trip through vault_read_section parser"


@pytest.mark.api
class TestTutorialVerifyResult:
    """POST /learn/api/tutorial/verify-result — the Lathe-borrow verify loop.

    Dark-flagged behind learn.feature.tutorial.enabled; tests flip the setting
    explicitly and restore it to false at the end (false == unset behaviour).
    """

    COURSE_ID = f"{TEST_PREFIX}tutorial-verify-course".lower()

    def _set_flag(self, http_client, value):
        r = http_client.post(
            "/settings/api/set",
            json={"key": "learn.feature.tutorial.enabled", "value": value},
        )
        assert r.status_code == 200

    def _seed_course(self, http_client):
        payload = {
            "course_id": self.COURSE_ID,
            "title": f"{TEST_PREFIX}Tutorial verify course",
            "description": "Auto-generated by test_sys_learn.py — safe to delete.",
            "level": "beginner",
            "domain": "test",
            "topic": "smoke",
            "tutorial": True,
            "lessons": [
                {"slug": REAL_CONCEPT_SLUG, "title": "Part 1", "kind": "read"},
            ],
        }
        assert_ok(http_client.post("/learn/api/courses/save", json=payload))

    def test_disabled_by_default(self, http_client):
        """Flag off → endpoint refuses with 'disabled', writes nothing."""
        self._set_flag(http_client, False)
        r = http_client.post(
            "/learn/api/tutorial/verify-result",
            json={"course_id": self.COURSE_ID, "status": "verified"},
        )
        assert r.status_code == 200
        assert r.json().get("error") == "disabled"

    def test_status_roundtrip_and_clear(self, http_client):
        """verifying → failed (detail recorded) → verified (failure cleared)."""
        self._set_flag(http_client, True)
        try:
            self._seed_course(http_client)
            # In-flight marker
            r = assert_ok(http_client.post(
                "/learn/api/tutorial/verify-result",
                json={"course_id": self.COURSE_ID, "status": "verifying"},
            ))
            assert r["status"] == "verifying"
            detail = assert_ok(http_client.get(f"/learn/api/courses/{self.COURSE_ID}"))
            assert detail["tutorial"] is True
            assert detail["verify"]["status"] == "verifying"
            # Failure carries part/step/error
            assert_ok(http_client.post(
                "/learn/api/tutorial/verify-result",
                json={
                    "course_id": self.COURSE_ID, "status": "failed",
                    "part": "part-01", "failed_step": 3,
                    "error": f"{TEST_PREFIX}expected 'pebble', got ''",
                },
            ))
            v = assert_ok(http_client.get(f"/learn/api/courses/{self.COURSE_ID}"))["verify"]
            assert v["status"] == "failed"
            assert v["part"] == "part-01"
            assert v["step"] == 3
            assert TEST_PREFIX in v["error"]
            # Verified clears stale failure detail
            assert_ok(http_client.post(
                "/learn/api/tutorial/verify-result",
                json={"course_id": self.COURSE_ID, "status": "verified"},
            ))
            v = assert_ok(http_client.get(f"/learn/api/courses/{self.COURSE_ID}"))["verify"]
            assert v["status"] == "verified"
            assert v["error"] == "" and v["part"] == ""
        finally:
            self._set_flag(http_client, False)

    def test_rejects_bad_input(self, http_client):
        """Invalid status + unknown course → clean errors, not 500."""
        self._set_flag(http_client, True)
        try:
            r = http_client.post(
                "/learn/api/tutorial/verify-result",
                json={"course_id": self.COURSE_ID, "status": "broken"},
            )
            assert "invalid status" in r.json().get("error", "")
            r = http_client.post(
                "/learn/api/tutorial/verify-result",
                json={"course_id": "no-such-course-xyz", "status": "verified"},
            )
            assert "not found" in r.json().get("error", "")
        finally:
            self._set_flag(http_client, False)


@pytest.mark.api
class TestLessonVideoAPI:
    """Lesson video routes — the gate and input validation only.

    Generating a video hits an LLM, TTS, and headless Chromium; that path is
    verified live on a sandbox member. These tests pin what must hold on any
    daemon: off means refused, and bad ids answer in-band instead of 500.
    """

    KEY = "learn.feature.lesson-video.enabled"
    DISABLED = "lesson video is disabled"

    def _get_flag(self, http_client):
        r = http_client.get("/settings/api/get", params={"key": self.KEY})
        assert r.status_code == 200
        return r.json().get("value")

    def _set_flag(self, http_client, value):
        r = http_client.post("/settings/api/set", json={"key": self.KEY, "value": value})
        assert r.status_code == 200

    def test_disabled_refuses_every_route(self, http_client):
        # Restore whatever the user had — a test run must never wipe their toggle.
        prior = self._get_flag(http_client)
        self._set_flag(http_client, False)
        try:
            if http_client.get("/learn/api/courses/any-course/lessons/0/video").json().get("enabled"):
                pytest.skip("lesson video is forced on by this daemon's emptyos.toml")
            r = http_client.post("/learn/api/courses/any-course/lessons/0/video")
            assert r.status_code == 200 and r.json().get("error") == self.DISABLED
            assert http_client.get("/learn/api/video-runs/some-run").json().get("error") == self.DISABLED
            assert http_client.post("/learn/api/video-runs/some-run/resume").json().get("error") == self.DISABLED
            assert http_client.post("/learn/api/courses/any-course/videos").json().get("error") == self.DISABLED
            r = http_client.get("/learn/api/videos/any-course/0")
            # 404 alone would also be "no video for this lesson" — pin the gate's own body.
            assert r.status_code == 404 and r.json().get("error") == self.DISABLED
        finally:
            self._set_flag(http_client, prior)

    def test_bad_ids_answer_in_band(self, http_client):
        prior = self._get_flag(http_client)
        self._set_flag(http_client, True)
        try:
            r = http_client.post("/learn/api/courses/nul/lessons/0/video")
            assert r.status_code == 200 and r.json().get("error", "").startswith("invalid course id")
            r = http_client.post("/learn/api/courses/no-such-course-xyz/lessons/0/video")
            assert r.json().get("error") == "course not found"
            r = http_client.post("/learn/api/courses/no-such-course-xyz/lessons/abc/video")
            assert "integer" in r.json().get("error", "")
            assert http_client.get("/learn/api/video-runs/no-such-run-xyz").json().get("error") == "run not found"
            assert http_client.get("/learn/api/video-runs/nul").json().get("error", "").startswith("invalid run id")
            r = http_client.get("/learn/api/videos/no-such-course-xyz/0")
            assert r.status_code == 404 and r.json().get("error") == "no video for this lesson"
            r = http_client.get("/learn/api/videos/nul/0")
            assert r.status_code == 400 and r.json().get("error", "").startswith("invalid course id")
        finally:
            self._set_flag(http_client, prior)


# Notes for the maintainer:
#
# - LLM-touching endpoints (quiz generation) skipped intentionally —
#   slow, costly, non-deterministic with caching. Smoke-tested by hand.
#
# - Reader-note test leaves a TEST_PREFIX entry in the real concept note's
#   `## Reader notes` section. The conftest cleanup doesn't strip vault
#   note contents (only created files); periodically prune by hand or add
#   a section-cleanup pass to conftest if accumulation becomes painful.
#
# - SRS grade test leaves a TEST_PREFIX entry in data/apps/learn/srs.json
#   (the unified grade-item tests do the same, and on a machine with the
#   dictionary app installed the optional-app soft test leaves a ghost word
#   entry in data/apps/dictionary/srs.json). Neither app has a delete
#   endpoint, so the API sweeps can't reach these; conftest's session-end
#   SRS-store backstop drops them by key instead. Was "acceptable noise,
#   clean by hand" until 2026-08-06, by which point nobody had.
