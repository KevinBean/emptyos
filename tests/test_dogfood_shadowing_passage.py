"""Dogfood — shadowing passage mode (Step 4 read-aloud).

Two narrative scenarios end-to-end against the running daemon:

1. **Exact-match drill** — paste a passage, drill 4 reps with `attempt_text =
   target`, complete. Verify the passage gained 4 reps + 1 session in its
   frontmatter and that `marked_phones` stays empty (perfect attempts
   shouldn't introduce slips).

2. **Mis-spelled drill** — same passage, drill 4 reps with `attempt_text`
   missing every "the". Without pronounce-plugin scoring (text-only path),
   we still get an LCS score and the per-rep response carries enough to
   prove the rep was captured. We don't assert on phone marks here because
   the text-only path doesn't run pronounce — that's the dedicated `llm`
   variant's job.

No LLM. Lives in the non-LLM dogfood lane (`-m "dogfood and not llm"`).
"""

from __future__ import annotations

import time
import uuid

import pytest

from helpers import TEST_PREFIX


FIXTURE_PASSAGE = (
    "The river flows quietly through the valley. Three travellers stop to "
    "rest on the bank, their breath visible in the cold morning air.\n\n"
    "They light a small fire and share what is left of yesterday's bread. "
    "The eldest passes around a flask, and they speak of the road ahead.\n\n"
    "By dusk they will reach the next town. The path is rough, but they "
    "know it well."
)

RUN_ID_PERFECT = f"{TEST_PREFIX}passage-perfect-{uuid.uuid4().hex[:6]}"
RUN_ID_SLIPS = f"{TEST_PREFIX}passage-slips-{uuid.uuid4().hex[:6]}"


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


def _drop_the(text: str) -> str:
    """Remove every standalone 'the' / 'The' from text, leaving the rest."""
    import re
    return re.sub(r"\b[Tt]he\b\s?", "", text)


@pytest.mark.dogfood
class TestPassagePerfectDrill:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/shadowing/api/passages"):
            pytest.skip("shadowing app not loaded")

    def test_01_create_passage(self, http_client):
        """Paste the fixture as a new passage."""
        resp = http_client.post(
            "/shadowing/api/passages",
            json={
                "source": "paste",
                "payload": {"title": RUN_ID_PERFECT, "text": FIXTURE_PASSAGE},
            },
        )
        assert resp.status_code == 200, resp.text[:300]
        data = resp.json()
        slug = data.get("slug")
        assert slug, f"no slug: {data}"
        self.state["slug"] = slug
        TestPassagePerfectDrill.state = self.state
        # Wait for vault watcher + VaultIndex refresh before downstream reads
        time.sleep(1.5)

    def test_02_passage_appears_in_list(self, http_client):
        listing = http_client.get("/shadowing/api/passages").json()
        slugs = [p["slug"] for p in listing.get("passages", [])]
        assert self.state["slug"] in slugs, f"new passage missing: {slugs[:5]}"

    def test_03_start_session(self, http_client):
        slug = self.state["slug"]
        resp = http_client.post(
            f"/shadowing/api/passages/{slug}/session/start",
            json={"quiet": False, "rep_budget": 4, "speed_ramp": [0.8, 1.0, 1.2]},
        )
        data = resp.json()
        assert data.get("sid"), f"no sid: {data}"
        self.state["sid"] = data["sid"]
        # Speed ramp for budget=4: third=1, so reps 1 slow, 2-3 normal, 4 fast
        speeds = data["speeds_by_rep"]
        assert abs(speeds["1"] - 0.8) < 1e-6
        assert abs(speeds["4"] - 1.2) < 1e-6

    def test_04_record_four_perfect_reps(self, http_client):
        sid = self.state["sid"]
        for i in range(1, 5):
            resp = http_client.post(
                f"/shadowing/api/passages/sessions/{sid}/rep",
                json={"attempt_text": FIXTURE_PASSAGE, "rep_index": i},
            )
            assert resp.status_code == 200, resp.text[:300]
            rep = resp.json()
            assert rep.get("i") == i, f"unexpected rep index: {rep}"
            # Exact match should be near-perfect LCS
            assert rep.get("score", 0) >= 0.95, \
                f"rep {i} expected perfect, got {rep.get('score')}"

    def test_05_complete_session(self, http_client):
        sid = self.state["sid"]
        resp = http_client.post(
            f"/shadowing/api/passages/sessions/{sid}/complete",
            json={"keep_audio": True},
        )
        assert resp.status_code == 200
        done = resp.json()
        assert done.get("completed") is True, f"not completed: {done}"
        assert len(done.get("reps", [])) == 4
        # No slips on perfect text-only attempts (no audio → no pronounce run)
        assert done.get("session_slip_phones") == [], \
            f"perfect attempts shouldn't surface slipped phones: {done.get('session_slip_phones')}"
        # Wait for vault write to settle before frontmatter re-read
        time.sleep(1.5)

    def test_06_passage_frontmatter_updated(self, http_client):
        slug = self.state["slug"]
        p = http_client.get(f"/shadowing/api/passages/{slug}").json()
        assert p["total_reps"] == 4, f"expected 4 reps, got {p['total_reps']}"
        assert p["total_sessions"] == 1, f"expected 1 session, got {p['total_sessions']}"
        assert p["last_drilled"], "last_drilled empty after complete"
        # marked_phones stays empty when text-only attempts match exactly
        assert p["marked_phones"] == [], \
            f"perfect drill shouldn't mark phones: {p['marked_phones']}"

    def test_07_sessions_section_in_body(self, http_client):
        """The complete-handler appends a ## Sessions row block to the body."""
        slug = self.state["slug"]
        p = http_client.get(f"/shadowing/api/passages/{slug}").json()
        sessions_md = p.get("sessions_md", "") or ""
        # One dated session header `### <iso> — 4 reps, live`
        assert "4 reps" in sessions_md, f"sessions block missing rep count: {sessions_md[:200]}"
        assert "live" in sessions_md or "quiet" in sessions_md, \
            f"sessions block missing mode tag: {sessions_md[:200]}"


@pytest.mark.dogfood
class TestPassageSlipsDrill:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/shadowing/api/passages"):
            pytest.skip("shadowing app not loaded")

    def test_01_create_and_start(self, http_client):
        c = http_client.post(
            "/shadowing/api/passages",
            json={
                "source": "paste",
                "payload": {"title": RUN_ID_SLIPS, "text": FIXTURE_PASSAGE},
            },
        ).json()
        slug = c.get("slug")
        assert slug, f"no slug: {c}"
        self.state["slug"] = slug
        TestPassageSlipsDrill.state = self.state
        time.sleep(1.2)
        s = http_client.post(
            f"/shadowing/api/passages/{slug}/session/start",
            json={"quiet": True, "rep_budget": 4},
        ).json()
        assert s.get("sid"), f"no sid: {s}"
        self.state["sid"] = s["sid"]

    def test_02_record_four_lossy_reps(self, http_client):
        """Submit reps with every 'the' removed — text-only LCS will score
        these mid-range and slips will be detectable. Without pronounce
        the marked_phones list stays empty (that's expected for the text
        path — the audio path runs pronounce, see the llm variant)."""
        sid = self.state["sid"]
        lossy = _drop_the(FIXTURE_PASSAGE)
        assert "the" not in lossy.lower().split(), "the-stripper missed some 'the's"
        for i in range(1, 5):
            resp = http_client.post(
                f"/shadowing/api/passages/sessions/{sid}/rep",
                json={"attempt_text": lossy, "rep_index": i},
            )
            assert resp.status_code == 200, resp.text[:300]
            rep = resp.json()
            # Quiet mode redacts score from the response
            assert rep.get("quiet") is True, f"expected quiet response: {rep}"

    def test_03_complete_and_verify(self, http_client):
        sid = self.state["sid"]
        done = http_client.post(
            f"/shadowing/api/passages/sessions/{sid}/complete",
            json={"keep_audio": True},
        ).json()
        assert done.get("completed") is True
        assert len(done.get("reps", [])) == 4
        # Reps in the *complete* payload have full data (no redaction)
        for r in done["reps"]:
            assert "score" in r, f"complete should reveal scores: {r}"
            assert r["score"] < 0.99, \
                f"lossy attempt should score below perfect: {r['score']}"
        time.sleep(1.2)

    def test_04_passage_state_after_drill(self, http_client):
        slug = self.state["slug"]
        p = http_client.get(f"/shadowing/api/passages/{slug}").json()
        assert p["total_reps"] == 4
        assert p["total_sessions"] == 1
