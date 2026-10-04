"""System app tests: Sound Check — the perception drill.

The unit suite already plays whole sessions without a daemon, so these tests
deliberately cover only what a live daemon adds and unit tests cannot reach:

* **The answer must not cross the wire.** The unit test asserts the payload
  shape; this asserts it over real HTTP, which is where it would actually leak.
* **A GET returning 200 proves a route registered, not that its body runs.**
  The write paths (start, answer, complete, second-pass) are exercised, since
  the multi-module split's characteristic failure is a name that is undefined
  only at call time.
* **``sid`` reaches the filesystem**, so the traversal refusals are tested as
  first-class behaviour rather than inferred from the code shape.
* **Audio is a capability that may be absent**, so the tests assert the
  contract — a session starts and is playable either way — never that a clip
  exists.
"""

from __future__ import annotations

import httpx
import pytest

from helpers import BASE_URL, assert_ok
from page_helpers import assert_no_js_errors


def _app_installed() -> bool:
    """soundcheck ships in the EnglishOS tier and sits behind the Store gate, so
    a daemon that has not installed it 404s every route.

    Must send the auth header: on a private-mode daemon an unauthenticated probe
    gets 401 for *every* route, which would skip this suite permanently while
    looking like it passed.
    """
    from conftest import _AUTH_HEADERS

    try:
        resp = httpx.get(f"{BASE_URL}/soundcheck/api/bank", timeout=5,
                         headers=_AUTH_HEADERS)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 — no daemon is the session fixture's problem
        return False


pytestmark = pytest.mark.skipif(
    not _app_installed(), reason="soundcheck not installed on this daemon"
)


def _bank_has_items(http_client) -> bool:
    return (http_client.get("/soundcheck/api/bank").json() or {}).get("count", 0) > 0


@pytest.mark.api
class TestSoundcheckAPI:

    def test_bank_reports_what_is_loadable_and_what_is_pending(self, http_client):
        data = assert_ok(http_client.get("/soundcheck/api/bank"))
        assert isinstance(data["count"], int)
        assert isinstance(data["pending_review"], int)
        assert isinstance(data["dimensions"], dict)

    def test_pending_items_are_never_counted_as_loadable(self, http_client):
        """The review gate. A generated-but-unreviewed item must be unservable,
        not merely discouraged."""
        data = http_client.get("/soundcheck/api/bank").json()
        loadable = sum(data["dimensions"].values())
        assert loadable == data["count"]

    def test_dimensions_lists_all_six_axes(self, http_client):
        data = assert_ok(http_client.get("/soundcheck/api/dimensions"))
        ids = {d["id"] for d in data["dimensions"]}
        assert ids == {"vowel", "consonant", "syllable", "stress",
                       "connected", "spelling"}
        eye = {d["id"] for d in data["dimensions"] if d["eye_playable"]}
        assert eye, "something must remain playable without a voice engine"

    def test_review_separates_real_slips_from_accent_and_artifacts(self, http_client):
        """Works on an empty store too — a daemon with no pronunciation history
        must return the three empty buckets rather than an error."""
        data = assert_ok(http_client.get("/soundcheck/api/review"))
        assert set(data["occurrences"]) == {"error", "accent-split", "artifact"}
        assert set(data["rows"]) == {"error", "accent-split", "artifact"}

    def test_review_stays_silent_when_there_is_nothing_to_say(self, http_client):
        data = http_client.get("/soundcheck/api/review").json()
        if sum(data["occurrences"].values()) == 0:
            assert data["headline"] == ""

    # ── a real session ────────────────────────────────────────────

    def test_a_session_starts_and_serves_a_round(self, http_client):
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        data = assert_ok(http_client.post("/soundcheck/api/session/start", json={"mode": "quick"}))
        assert data["sid"].startswith("sc_")
        assert data["round"]["shape"]
        assert data["round"]["options"]

    def test_the_answer_never_crosses_the_wire(self, http_client):
        """The one that matters. Truth stays server-side — it would otherwise be
        one devtools panel away, and the confusion matrix needs a server-side
        answer regardless."""
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        data = http_client.post("/soundcheck/api/session/start", json={"mode": "quick"}).json()
        rnd = data["round"]
        assert "answer" not in rnd
        assert "miss_maps_to" not in str(rnd)

    def test_answering_grades_and_serves_the_next_round_in_one_trip(self, http_client):
        """The next round rides on the verdict so the page can preload its audio
        while the learner reads the verdict for this one."""
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        start = http_client.post("/soundcheck/api/session/start",
                         json={"mode": "quick"}).json()
        rnd = start["round"]
        res = assert_ok(http_client.post(
            f"/soundcheck/api/session/{start['sid']}/answer",
            json={"rid": rnd["rid"], "choice": [rnd["options"][0]["id"]],
                  "elapsed_ms": 1200}))
        assert isinstance(res["correct"], bool)
        assert res["answer"]
        assert res["next"] is not None or res["done"]
        assert res["progress"]["asked"] == 1

    def test_every_listening_round_actually_has_a_clip(self, http_client):
        """A round whose stimulus says "audio" and carries an empty URL renders
        as "no clip", which reads as a broken voice engine rather than what it
        is — a prewarm that guessed the wrong items before selection ran.
        Measured once at one clip in six rounds, with nothing failing."""
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        start = http_client.post("/soundcheck/api/session/start",
                                 json={"mode": "quick", "length": 6,
                                       "lives": 0}).json()
        if not start.get("audio_ok"):
            pytest.skip("no voice engine on this daemon")

        sid, rnd, checked = start["sid"], start["round"], 0
        while rnd:
            stim = rnd.get("stimulus") or {}
            if stim.get("mode") in ("audio", "both"):
                for entry in stim.get("items") or []:
                    assert entry.get("audio"), (
                        f"round {rnd['rid']} ({rnd['shape']}) is a listening "
                        "round with no clip"
                    )
                    checked += 1
            res = http_client.post(f"/soundcheck/api/session/{sid}/answer",
                                   json={"rid": rnd["rid"],
                                         "choice": [rnd["options"][0]["id"]],
                                         "elapsed_ms": 1000}).json()
            rnd = res.get("next")
        assert checked, "no listening round appeared in six tries"

    def test_a_round_cannot_be_answered_twice(self, http_client):
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        start = http_client.post("/soundcheck/api/session/start",
                         json={"mode": "quick"}).json()
        rid = start["round"]["rid"]
        body = {"rid": rid, "choice": [start["round"]["options"][0]["id"]]}
        http_client.post(f"/soundcheck/api/session/{start['sid']}/answer", json=body)
        again = http_client.post(f"/soundcheck/api/session/{start['sid']}/answer",
                         json=body).json()
        assert again["ok"] is False

    def test_a_finished_session_returns_a_confusion_matrix(self, http_client):
        """The artifact no other app in the suite produces: which wrong bucket
        you fell into, not merely that you were wrong."""
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        start = http_client.post("/soundcheck/api/session/start",
                         json={"mode": "quick", "length": 3, "lives": 0}).json()
        sid, rnd = start["sid"], start["round"]
        while rnd:
            res = http_client.post(f"/soundcheck/api/session/{sid}/answer",
                           json={"rid": rnd["rid"],
                                 "choice": [rnd["options"][0]["id"]],
                                 "elapsed_ms": 1000}).json()
            rnd = res.get("next")
        summary = res["summary"]
        assert summary is not None
        assert "matrix" in summary and "labels" in summary["matrix"]
        assert summary["asked"] == 3

    def test_completing_is_idempotent(self, http_client):
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        sid = http_client.post("/soundcheck/api/session/start",
                       json={"mode": "quick"}).json()["sid"]
        first = assert_ok(http_client.post(f"/soundcheck/api/session/{sid}/complete"))
        second = assert_ok(http_client.post(f"/soundcheck/api/session/{sid}/complete"))
        assert first["summary"]["sid"] == second["summary"]["sid"]

    def test_abandoning_marks_the_session_and_does_not_error(self, http_client):
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        sid = http_client.post("/soundcheck/api/session/start",
                       json={"mode": "quick"}).json()["sid"]
        assert_ok(http_client.post(f"/soundcheck/api/session/{sid}/abandon"))
        rows = http_client.get("/soundcheck/api/history").json()["sessions"]
        assert any(r["sid"] == sid and r["abandoned"] for r in rows)

    def test_history_reports_sessions_and_a_heatmap(self, http_client):
        data = assert_ok(http_client.get("/soundcheck/api/history"))
        assert isinstance(data["sessions"], list)
        assert isinstance(data["heatmap"], dict)

    # ── refusals ──────────────────────────────────────────────────

    def test_an_unknown_session_is_refused_not_crashed(self, http_client):
        res = http_client.get("/soundcheck/api/session/sc_doesnotexist").json()
        assert res["ok"] is False

    # Percent-encoded, because httpx resolves a literal "../.." against the base
    # URL before the request ever leaves the client — a test written that way
    # measures the HTTP library rather than the server, and passes for the wrong
    # reason or fails for one.
    @pytest.mark.parametrize("sid", [
        "%2e%2e", "%2e%2e%2fetc", "a%2fb", "a%5Cb", "nul", "sc_%2e%2e",
    ])
    def test_a_traversal_in_the_session_id_never_reaches_the_filesystem(
        self, http_client, sid
    ):
        """``sid`` is joined into a path and the loader gates on file existence
        rather than an index lookup — the exact shape that let a traversal
        survive in a sibling app.

        A rejected id must also not read as a server fault. A client can send
        anything here, so a 500 blames the daemon for a bad request and fills
        syslog with entries that look like crashes.
        """
        resp = http_client.get(f"/soundcheck/api/session/{sid}")
        assert resp.status_code != 500, "a bad id must not read as a server fault"
        assert resp.status_code in (200, 400, 404, 422)
        if resp.status_code == 200:
            body = resp.json()
            assert body.get("ok") is False or "session" not in body

    def test_an_unknown_audio_clip_is_a_404(self, http_client):
        assert http_client.get("/soundcheck/api/audio/nope.mp3").status_code == 404

    def test_an_encoded_traversal_in_a_clip_name_serves_nothing(self, http_client):
        resp = http_client.get("/soundcheck/api/audio/%2e%2e%2f%2e%2e%2femptyos.toml")
        assert resp.status_code in (400, 404, 422)
        assert "auth_token" not in resp.text

    def test_a_second_pass_needs_a_finished_diagnostic(self, http_client):
        """The route must refuse an ordinary adaptive set rather than inventing
        an order for it — two passes over different questions would look like a
        comparison and measure nothing."""
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        sid = http_client.post("/soundcheck/api/session/start",
                       json={"mode": "quick"}).json()["sid"]
        res = http_client.post(f"/soundcheck/api/session/{sid}/second-pass").json()
        assert res["ok"] is False


@pytest.mark.interactive
class TestSoundcheckUI:

    def test_the_page_loads_clean(self, page, page_errors):
        page.goto(f"{BASE_URL}/soundcheck/")
        page.wait_for_load_state("networkidle")
        assert page.locator("#page-title").count() == 1
        assert_no_js_errors(page_errors)

    def test_the_six_dimensions_render(self, page):
        page.goto(f"{BASE_URL}/soundcheck/")
        page.wait_for_selector(".sc-dim, .eos-empty-state", timeout=15000)
        cards = page.locator(".sc-dim").count()
        assert cards in (0, 6), f"expected six dimension cards or none, got {cards}"

    def test_playing_a_round_renders_options_and_grades(
        self, page, http_client, page_errors
    ):
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        page.goto(f"{BASE_URL}/soundcheck/")
        page.wait_for_selector("#sc-go:not([disabled])", timeout=15000)
        page.click("#sc-go")
        page.wait_for_selector("#sc-round .sc-opt", timeout=25000)
        assert page.locator("#sc-round .sc-opt").count() >= 2
        page.locator("#sc-round .sc-opt").first.click()
        page.wait_for_selector("#sc-verdict:not([hidden])", timeout=20000)
        assert page.locator(".sc-right").count() >= 1
        assert_no_js_errors(page_errors)

    def test_the_tab_bar_hides_during_a_round(self, page, http_client):
        """A tab bar during a timed round is an invitation to lose the round.
        `hidden` alone does not do it — an author `display` beats the UA rule."""
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        page.goto(f"{BASE_URL}/soundcheck/")
        page.wait_for_selector("#sc-go:not([disabled])", timeout=15000)
        page.click("#sc-go")
        page.wait_for_selector("#sc-round .sc-opt", timeout=25000)
        assert page.locator("#sc-tabs").is_hidden()

    def test_the_words_under_test_are_not_translated(self, page, http_client):
        """The UI-chrome translation layer walks visible text. Left alone it
        renders glass/grass as two Chinese words, which removes the entire thing
        the learner is being asked to discriminate."""
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        page.goto(f"{BASE_URL}/soundcheck/")
        page.wait_for_selector("#sc-go:not([disabled])", timeout=15000)
        page.click("#sc-go")
        page.wait_for_selector("#sc-round .sc-opt-label", timeout=25000)
        labels = page.locator("#sc-round .sc-opt-label").all_inner_texts()
        assert labels, "a round must offer options"
        for text in labels:
            assert not any("一" <= ch <= "鿿" for ch in text), (
                f"a word under test was translated: {text!r}"
            )

    def test_the_page_does_not_overflow_on_a_phone(self, page, http_client):
        page.set_viewport_size({"width": 390, "height": 780})
        page.goto(f"{BASE_URL}/soundcheck/")
        page.wait_for_load_state("networkidle")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth + 2"
        )
        assert not overflow

    def test_the_chrome_classes_actually_resolve_to_styling(self, page):
        """A class name that is defined nowhere is invisible to every gate.

        This page once shipped three of them at once — `.tab-btn` (so the active
        tab was indistinguishable), `.eos-btn primary` instead of
        `.eos-btn-primary` (so the CTA rendered as a plain outline button), and
        `.container` (styled nowhere, so the page ran edge-to-edge). All three
        passed review, the scanners and the suite, because a missing rule is not
        a syntax error. Assert the *effect*, not the class string.
        """
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{BASE_URL}/soundcheck/")
        page.wait_for_selector("#sc-tabs .eos-tab", timeout=15000)

        looks = page.evaluate(
            """() => {
                const tabs = [...document.querySelectorAll('#sc-tabs .eos-tab')];
                const on = tabs.find(t => t.classList.contains('active'));
                const off = tabs.find(t => !t.classList.contains('active'));
                const cta = document.getElementById('sc-go');
                const plain = document.querySelector('.sc-start .eos-btn:not(.eos-btn-primary)');
                const paint = el => {
                    const s = getComputedStyle(el);
                    return s.color + '|' + s.backgroundColor + '|' + s.backgroundImage
                         + '|' + s.borderBottomColor;
                };
                return {
                    active: paint(on), inactive: paint(off),
                    cta: paint(cta), plain: paint(plain),
                    title_left: document.getElementById('page-title')
                                  .getBoundingClientRect().left,
                };
            }"""
        )
        assert looks["active"] != looks["inactive"], (
            "the selected tab is painted identically to an unselected one — "
            "the tab class resolves to no rule"
        )
        assert looks["cta"] != looks["plain"], (
            "the primary CTA is painted identically to a secondary button — "
            "check it is `eos-btn eos-btn-primary`, not `eos-btn primary`"
        )
        assert looks["title_left"] >= 8, (
            f"page title sits at x={looks['title_left']} — the page wrapper "
            "supplies no edge padding (use .page, not an unstyled .container)"
        )

    def test_every_dimension_card_binds_its_click_handler(self, page, http_client):
        """`JSON.stringify` inside an ``onclick=""`` emits real double quotes,
        which close the attribute early and leave the handler unbound — the card
        renders perfectly and does nothing when clicked. Assert the binding, not
        the markup; the broken form still produces a plausible-looking button.
        """
        if not _bank_has_items(http_client):
            pytest.skip("bank is empty on this daemon")
        page.goto(f"{BASE_URL}/soundcheck/")
        page.wait_for_selector(".sc-dim", timeout=15000)
        unbound = page.evaluate(
            """() => [...document.querySelectorAll('.sc-dim')]
                 .filter(el => !el.disabled && typeof el.onclick !== 'function')
                 .map(el => (el.querySelector('.sc-dim-label') || {}).textContent)"""
        )
        assert unbound == [], f"dimension cards render but do not click: {unbound}"
