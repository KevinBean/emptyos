"""System app tests: Picture Dictionary — the visual vocabulary trainer.

The unit suite (``test_unit_picture_dict.py``) already covers pack loading, name
resolution and quiz construction with no daemon. These deliberately cover only
what a live daemon adds:

* **A GET returning 200 proves a route registered, not that its body runs.** The
  multi-module split's characteristic failure is a name that is undefined only at
  call time, so every *write* path is exercised — enrol, grade, quiz answer,
  star — not just the listings.
* **The quiz answer key must not cross the wire.** The unit test asserts the
  payload shape; this asserts it over real HTTP, which is where it would leak.
* **A caller-supplied slug reaches the filesystem** (it becomes an image
  filename), so the traversal and reserved-name refusals are first-class tests
  rather than inferred from the code shape.
* **Photos, voice and the neighbouring apps may all be absent.** The tests assert
  the *contract* — the gallery works, the quiz runs, the attempt is scored one
  way or another — never that a photo or a clip exists. A fresh install with zero
  downloads is the first-run state and has to be usable.
"""

from __future__ import annotations

import httpx
import pytest

from helpers import BASE_URL, assert_ok
from page_helpers import assert_no_js_errors

# Absorbed into `dictionary` 2026-08-19 (picture-dict retired). Routes kept
# their shape under an /api/picture/ namespace; the gallery is its own page.
PREFIX = f"{BASE_URL}/dictionary"
# Folded into the dictionary's tab bar 2026-08-19. The picture surfaces are
# a top-level tab now, deep-linked as `#pictures` / `#pictures/<slug>`.
PAGE = f"{BASE_URL}/dictionary/#pictures"


def _app_installed() -> bool:
    """dictionary ships in the EnglishOS tier and sits behind the Store gate,
    so a daemon that has not installed it 404s every route.

    Must send the auth header: on a private-mode daemon an unauthenticated probe
    gets 401 for *every* route, which would skip this suite permanently while
    looking like it passed.
    """
    from conftest import _AUTH_HEADERS

    try:
        r = httpx.get(f"{PREFIX}/api/picture/status", timeout=5, headers=_AUTH_HEADERS)
        return r.status_code == 200
    except Exception:  # noqa: BLE001 — no daemon is the session fixture's problem
        return False


pytestmark = pytest.mark.skipif(
    not _app_installed(), reason="dictionary not installed on this daemon"
)


def _enrolled_slugs() -> set:
    """Slugs currently carrying a review schedule. Used to diff around a test
    that enrols as a side effect, so cleanup removes only what the test added."""
    from conftest import _AUTH_HEADERS

    try:
        r = httpx.get(f"{PREFIX}/api/picture/srs/due?limit=500", timeout=10,
                      headers=_AUTH_HEADERS)
        return {c["slug"] for c in r.json().get("cards", [])}
    except Exception:  # noqa: BLE001 — cleanup must never fail a test
        return set()


def _unenroll(slug: str) -> None:
    from conftest import _AUTH_HEADERS

    try:
        httpx.post(f"{PREFIX}/api/picture/srs/unenroll", json={"slug": slug}, timeout=10,
                   headers=_AUTH_HEADERS)
    except Exception:  # noqa: BLE001
        pass


@pytest.mark.api
class TestPictureDictAPI:
    def test_status_works_before_any_photo_is_downloaded(self, http_client):
        """The first-run state. If this needs the network to answer, a fresh
        install shows a broken app for its first three minutes.

        `len(categories) == 6` was an exact count of a CONTENT PACK, so growing
        the pack broke it — measured 2026-09-13 the status endpoint answers fine
        (`coverage.total` 294, `network.enabled` present) with 25 categories.
        The failure had been recorded as "status breaks before the first photo",
        which is what this test exists to detect and is not what was happening.
        Assert the shape the first-run screen needs, not a number that rises
        every time someone adds pictures.
        """
        d = assert_ok(http_client.get(f"{PREFIX}/api/picture/status"))
        assert d["coverage"]["total"] >= 120
        assert d["categories"], "the picker renders these — an empty list is a broken screen"
        assert "enabled" in d["network"]

    def test_the_catalog_lists_the_whole_pack(self, http_client):
        d = assert_ok(http_client.get(f"{PREFIX}/api/picture/catalog"))
        assert d["count"] == d["items"].__len__() >= 120
        first = d["items"][0]
        for f in ("slug", "name", "chinese", "category", "emoji"):
            assert first.get(f), f"catalog rows must carry {f} — the tile renders it"

    def test_the_category_filter_is_exact(self, http_client):
        d = assert_ok(http_client.get(f"{PREFIX}/api/picture/catalog?category=sea"))
        assert 0 < d["count"] < 130
        assert {i["category"] for i in d["items"]} == {"sea"}

    def test_search_matches_english_and_chinese(self, http_client):
        en = assert_ok(http_client.get(f"{PREFIX}/api/picture/catalog?q=tig"))
        assert any(i["slug"] == "tiger" for i in en["items"])
        zh = assert_ok(http_client.get(f"{PREFIX}/api/picture/catalog?q=%E8%80%81%E8%99%8E"))
        assert any(i["slug"] == "tiger" for i in zh["items"])

    def test_an_unknown_category_answers_in_band(self, http_client):
        r = http_client.get(f"{PREFIX}/api/picture/catalog?category=nope")
        assert r.status_code == 200 and "error" in r.json()

    @pytest.mark.parametrize("probe", ["..%2f..%2femptyos.toml", "nul", "con", "zzz-nope"])
    def test_a_refused_slug_never_becomes_a_500(self, http_client, probe):
        """One user mistake must not wear two shapes: an unknown id and a
        *refused* id both belong in-band. A reserved Windows device name is the
        case a typo never reproduces."""
        r = http_client.get(f"{PREFIX}/api/picture/item/{probe}")
        assert r.status_code != 500, r.text[:200]

    def test_an_image_traversal_is_refused(self, http_client):
        r = http_client.get(f"{PREFIX}/api/picture/image/..%2f..%2femptyos.toml")
        assert r.status_code in (400, 404)

    def test_a_missing_photo_is_a_404_not_a_broken_stream(self, http_client):
        r = http_client.get(f"{PREFIX}/api/picture/image/zzz-definitely-not-real")
        assert r.status_code == 404

    def test_a_quiz_runs_without_leaking_the_answer(self, http_client):
        """The whole game is over if the answer ships with the question."""
        q = assert_ok(http_client.post(f"{PREFIX}/api/picture/quiz/start",
                                  json={"category": "sea", "length": 5}))
        assert q["session"] and len(q["rounds"]) == 5
        for r in q["rounds"]:
            assert "answer" not in r, "the answer key must stay server-side"
            assert len(r["options"]) == 4

    def test_quiz_distractors_stay_inside_the_category(self, http_client):
        q = assert_ok(http_client.post(f"{PREFIX}/api/picture/quiz/start",
                                  json={"category": "birds", "length": 6}))
        birds = {i["slug"] for i in
                 assert_ok(http_client.get(f"{PREFIX}/api/picture/catalog?category=birds"))["items"]}
        for r in q["rounds"]:
            assert {o["slug"] for o in r["options"]} <= birds

    def test_answering_and_finishing_a_quiz_enrols_what_was_missed(self, http_client):
        q = assert_ok(http_client.post(f"{PREFIX}/api/picture/quiz/start",
                                  json={"category": "sea", "length": 3}))
        r0 = q["rounds"][0]
        a = assert_ok(http_client.post(f"{PREFIX}/api/picture/quiz/answer", json={
            "session": q["session"], "round": r0["index"],
            "choice": r0["options"][0]["slug"]}))
        assert "correct" in a and a["answer"]
        fin = assert_ok(http_client.post(f"{PREFIX}/api/picture/quiz/finish",
                                    json={"session": q["session"]}))
        assert "score" in fin and "missed" in fin
        try:
            if not a["correct"]:
                assert a["answer"] in fin["enrolled"], "a miss must enter the review queue"
        finally:
            # A miss enrols the card — that is the behaviour under test, and it
            # is also residue. The suite runs against the user's live daemon, so
            # a card they never chose must not be left sitting in their queue.
            for slug in fin.get("enrolled", []):
                http_client.post(f"{PREFIX}/api/picture/srs/unenroll", json={"slug": slug})

    def test_an_expired_quiz_answers_in_band(self, http_client):
        r = http_client.post(f"{PREFIX}/api/picture/quiz/answer",
                        json={"session": "q-not-real", "round": 0, "choice": "tiger"})
        assert r.status_code == 200 and "error" in r.json()

    def test_due_with_limit_zero_counts_without_loading_cards(self, http_client):
        """This is the shape learn's due-count path and the hub panel call. It
        has to stay cheap, so it must return no cards at all."""
        d = assert_ok(http_client.get(f"{PREFIX}/api/picture/srs/due?limit=0"))
        assert d["cards"] == [] and isinstance(d["due_count"], int)

    def test_enrolling_makes_a_card_due_and_grading_schedules_it_forward(self, http_client):
        slug = "hedgehog"
        assert_ok(http_client.post(f"{PREFIX}/api/picture/srs/unenroll", json={"slug": slug}))
        assert_ok(http_client.post(f"{PREFIX}/api/picture/srs/enroll", json={"slug": slug}))
        due = assert_ok(http_client.get(f"{PREFIX}/api/picture/srs/due?limit=100"))
        assert any(c["slug"] == slug for c in due["cards"])

        g = assert_ok(http_client.post(f"{PREFIX}/api/picture/srs/grade",
                                  json={"slug": slug, "rating": "easy"}))
        assert g["next_review"] and g["review_count"] >= 1
        after = assert_ok(http_client.get(f"{PREFIX}/api/picture/srs/due?limit=100"))
        assert not any(c["slug"] == slug for c in after["cards"]), \
            "a graded card must leave today's queue"
        assert_ok(http_client.post(f"{PREFIX}/api/picture/srs/unenroll", json={"slug": slug}))

    def test_easy_schedules_further_out_than_again(self, http_client):
        def sched(rating):
            http_client.post(f"{PREFIX}/api/picture/srs/unenroll", json={"slug": "otter"})
            http_client.post(f"{PREFIX}/api/picture/srs/enroll", json={"slug": "otter"})
            return assert_ok(http_client.post(f"{PREFIX}/api/picture/srs/grade",
                                         json={"slug": "otter", "rating": rating}))["next_review"]
        again, easy = sched("again"), sched("easy")
        assert easy > again, f"again={again} easy={easy}"
        http_client.post(f"{PREFIX}/api/picture/srs/unenroll", json={"slug": "otter"})

    @pytest.mark.parametrize("body", [
        {"slug": "tiger", "rating": "banana"},
        {"slug": "zzz-nope", "rating": "good"},
        {"slug": "", "rating": "good"},
    ])
    def test_a_bad_grade_is_an_in_band_error(self, http_client, body):
        r = http_client.post(f"{PREFIX}/api/picture/srs/grade", json=body)
        assert r.status_code == 200 and "error" in r.json()

    def test_prefetch_answers_the_request_instead_of_running_it(self, http_client):
        """The long-handler wedge: awaiting a ~4-minute job inside the route
        500s at ~30s while the job keeps running unseen.

        Only cancels a job this test actually started. Cancelling
        unconditionally would abort a download the *user* kicked off — the
        suite runs against a live daemon, so a test must not stop real work it
        did not start.
        """
        import time
        t0 = time.time()
        r = http_client.post(f"{PREFIX}/api/picture/images/prefetch", json={"slugs": ["tiger"]})
        assert r.status_code == 200
        assert time.time() - t0 < 5.0, "the POST must not wait for the download"
        if r.json().get("started"):
            http_client.post(f"{PREFIX}/api/picture/images/cancel", json={})

    def test_prefetch_status_keeps_the_job_counters_separate_from_coverage(self, http_client):
        """Merging coverage into the job dict overwrote `total` and `failed`, so
        the progress bar counted against the catalogue instead of the run."""
        d = assert_ok(http_client.get(f"{PREFIX}/api/picture/images/status"))
        assert isinstance(d.get("coverage"), dict)
        assert {"total", "with_photo", "missing"} <= set(d["coverage"])

    def test_starring_never_500s_even_without_the_dictionary_app(self, http_client):
        """Starring writes a real vocabulary note through the dictionary app, so
        this test cleans up after itself — and only if it was the one that
        created the note. The suite runs against a live daemon holding the
        user's real vault; a test must never delete a word they saved
        themselves."""
        word = "tiger"
        existed = http_client.get(
            f"{BASE_URL}/dictionary/api/vault/{word}").status_code == 200

        r = http_client.post(f"{PREFIX}/api/picture/save-word", json={"slug": word})
        assert r.status_code == 200
        assert "ok" in r.json() or "error" in r.json()

        if not existed and r.json().get("ok"):
            http_client.delete(f"{BASE_URL}/dictionary/api/vault/{word}")
        http_client.post(f"{PREFIX}/api/picture/unsave", json={"slug": word})
        # unsave deliberately keeps the review enrolment (you reviewed it, keep
        # reviewing) — right for the app, but residue for a test.
        http_client.post(f"{PREFIX}/api/picture/srs/unenroll", json={"slug": word})

    def test_unstarring_an_unknown_animal_answers_in_band(self, http_client):
        r = http_client.post(f"{PREFIX}/api/picture/unsave", json={"slug": "zzz-nope"})
        assert r.status_code == 200 and "error" in r.json()

    def test_speaking_the_name_returns_a_clip_or_says_why_not(self, http_client):
        """A voice engine may be absent — the contract is an answer either way."""
        d = assert_ok(http_client.get(f"{PREFIX}/api/picture/say/tiger"))
        assert "audio_url" in d or "error" in d

    def test_a_scoring_attempt_without_audio_is_an_in_band_error(self, http_client):
        r = http_client.post(f"{PREFIX}/api/picture/speak/attempt", json={"slug": "tiger"})
        assert r.status_code == 200 and "error" in r.json()


@pytest.mark.interactive
class TestPictureDictUI:
    def test_the_page_loads_clean(self, page, page_errors):
        page.goto(PAGE)
        page.wait_for_selector("#pd-view", timeout=10000)
        assert_no_js_errors(page_errors)

    def test_the_gallery_renders_before_any_photo_exists(self, page):
        """The emoji-placeholder path is the first-run experience, and the one
        nobody remembers to test."""
        page.goto(PAGE)
        page.wait_for_selector(".pd-card, .eos-empty-state", timeout=15000)
        assert page.locator(".pd-card").count() > 0

    def test_the_category_chips_filter_the_grid(self, page):
        page.goto(PAGE)
        page.wait_for_selector(".pd-card", timeout=15000)
        before = page.locator(".pd-card").count()
        page.locator(".pd-chip", has_text="Sea life").first.click()
        page.wait_for_timeout(900)
        assert page.locator(".pd-card").count() < before

    def test_opening_an_animal_updates_the_hash(self, page):
        page.goto(PAGE)
        page.wait_for_selector(".pd-card", timeout=15000)
        page.locator(".pd-card").first.click()
        page.wait_for_selector(".pd-detail", timeout=8000)
        assert "#" in page.url and len(page.url.split("#")[-1]) > 1

    def test_a_direct_hash_url_opens_that_animal_cold(self, page):
        page.goto(f"{BASE_URL}/dictionary/#pictures/tiger")
        page.wait_for_selector(".pd-detail", timeout=15000)
        assert "tiger" in page.locator(".pd-detail").inner_text().lower()

    def test_the_quiz_runs_a_round_and_gives_feedback(self, page, page_errors):
        """Clicking a wrong option enrols that animal for review — the intended
        behaviour, and a leak here because the browser never finishes the quiz,
        so the finish-time cleanup never runs.

        Cleans up by diffing the enrolled set around the click rather than
        clearing what is due: the suite runs against the user's live daemon, and
        a blanket clear would delete cards they chose themselves.
        """
        before = _enrolled_slugs()

        page.goto(PAGE)
        page.wait_for_selector(".pd-card", timeout=15000)
        page.locator("#tab-quiz").click()
        page.wait_for_selector("button:has-text('Start quiz')", timeout=8000)
        page.locator("button:has-text('Start quiz')").click()
        page.wait_for_selector(".pd-opt", timeout=10000)
        page.locator(".pd-opt").first.click()
        page.wait_for_timeout(700)
        try:
            assert page.locator(".pd-q-feedback").inner_text().strip()
            assert_no_js_errors(page_errors)
        finally:
            for slug in _enrolled_slugs() - before:
                _unenroll(slug)

    def test_the_review_tab_explains_itself_when_nothing_is_due(self, page):
        page.goto(PAGE)
        page.wait_for_selector("#pd-view", timeout=10000)
        page.locator("#tab-review").click()
        page.wait_for_timeout(1200)
        body = page.locator("#pd-view").inner_text()
        assert body.strip(), "the review tab must never render blank"

    def test_the_settings_gear_opens_the_panel_carrying_picture_keys(self, page):
        """One gear, one panel. When the packs were their own page they had a
        second settings panel; folding them into the tab bar made that a
        duplicate, so the dictionary's own gear is now the only one — and it
        must actually carry the picture-dict.* keys, or the fold silently made
        them unreachable."""
        page.goto(PAGE)
        page.wait_for_selector("#pd-view", timeout=10000)
        assert page.locator("#pd-settings-panel").count() == 0, "duplicate panel survived the fold"
        page.locator("button[title='Settings']").first.click()
        page.wait_for_selector("#app-settings-panel", state="visible", timeout=6000)
        body = page.locator("#app-settings-panel").inner_text().lower()
        assert "photo" in body or "picture" in body, body[:400]

    def test_the_page_does_not_overflow_on_a_phone(self, page):
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(PAGE)
        page.wait_for_selector(".pd-card", timeout=15000)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 2, f"horizontal overflow of {overflow}px at 390px wide"


# ─── The word card ↔ pack bridge ─────────────────────────────────────
#
# The point of the merge: a saved vocabulary word that is ALSO in a picture pack
# shows that photograph, with no fetch and no generation. In a real 440-word
# vault 41 words already had a licence-verified photo sitting unused, because
# the packs and the vocabulary were two apps.


@pytest.mark.interactive
class TestWordCardPicture:
    """Both directions: a word that has a photo, and the ~80% that do not."""

    def _lookup(self, page, word):
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f"{BASE_URL}/dictionary/")
        page.wait_for_timeout(1500)
        page.click("#tab-btn-lookup")
        page.wait_for_selector("#lookup-input", state="visible", timeout=15000)
        page.fill("#lookup-input", word)
        page.press("#lookup-input", "Enter")
        return errs

    def test_word_in_a_pack_shows_its_photo(self, page):
        errs = self._lookup(page, "otter")
        page.wait_for_selector("#word-picture img", timeout=25000)
        img = page.locator("#word-picture img")
        assert img.is_visible()
        assert "/dictionary/api/picture/image/otter" in img.get_attribute("src")
        # naturalWidth proves the bytes decoded — an <img> tag pointing at a 404
        # is visible, has a src, and shows nothing.
        assert page.evaluate(
            "() => document.querySelector('#word-picture img').naturalWidth") > 100
        # Captioned with its source, because exact matching still lets a homonym
        # through (`crane` the machine gets the bird) and a labelled photo is
        # visibly wrong rather than silently wrong.
        cap = page.locator("#word-picture .result-label").first.inner_text().lower()
        assert "animals pack" in cap and "otter" in cap, cap
        assert not errs, errs

    def test_abstract_word_shows_nothing(self, page):
        errs = self._lookup(page, "susurration")
        page.wait_for_timeout(3000)
        assert page.locator("#word-picture img").count() == 0
        assert not errs, errs


# ─── The fold ────────────────────────────────────────────────────────
#
# The packs were a standalone page for about a day. Folding them into the
# dictionary's tab bar had two things worth pinning: the boot must stay lazy
# (loadStatus is what starts the 130-photo prefetch, so a learner who never
# opens this tab must never pay for it), and the hash had to be namespaced,
# because the standalone page routed on a bare `#slug` and `#quiz` / `#review`
# already mean something else here.


@pytest.mark.interactive
class TestPicturesFoldedIntoTheTabBar:
    def test_pictures_are_not_booted_until_the_tab_is_opened(self, page):
        calls = []
        page.on("request", lambda r: calls.append(r.url))
        page.goto(f"{BASE_URL}/dictionary/")
        page.wait_for_selector("#tab-btn-pictures", timeout=15000)
        page.wait_for_timeout(3500)
        pic = [u for u in calls if "/api/picture/" in u]
        assert not pic, f"pictures booted on a plain page load: {pic}"

        page.click("#tab-btn-pictures")
        page.wait_for_selector(".pd-card", timeout=20000)
        pic = [u for u in calls if "/api/picture/" in u]
        assert any("/api/picture/status" in u for u in pic), pic
        assert any("/api/picture/catalog" in u for u in pic), pic

    def test_deep_link_opens_the_tab_and_the_detail(self, page):
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f"{BASE_URL}/dictionary/#pictures/tiger")
        page.wait_for_selector(".pd-detail", timeout=25000)
        assert page.locator("#tab-btn-pictures").get_attribute("aria-selected") == "true"
        assert "tiger" in page.locator(".pd-detail").inner_text().lower()
        assert not errs, errs

    def test_the_old_page_redirects(self, page):
        page.goto(f"{BASE_URL}/dictionary/pages/pictures.html#heron")
        page.wait_for_selector(".pd-detail", timeout=25000)
        assert "#pictures/heron" in page.url, page.url
