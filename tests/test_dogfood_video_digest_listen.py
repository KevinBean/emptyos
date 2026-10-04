"""Dogfood — video-digest Listen mode (Step 5: active listening).

End-to-end narrative: pre-stage a Web-Clip digest + timestamped transcript
sidecar in the vault, simulate a 12-minute Listen session via the synthetic
POST /api/listen/end endpoint, then verify three things land:

  (1) Listen GET returns the timestamped transcript
  (2) Digest note gains a `## Listening Sessions` table row
  (3) english/api/activity shows today's `listening` counter advanced by 1

No LLM, no real YouTube fetch. Lives in the non-LLM dogfood lane
(`-m "dogfood and not llm"`). YouTube IFrame UI is browser-only and not
exercised here — that's a manual Playwright pass.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest
import tomllib

from helpers import TEST_PREFIX


RUN_ID = f"{TEST_PREFIX}listen-dogfood-{uuid.uuid4().hex[:6]}"
FIXTURE_LINES = [
    {"text": "The river flows quietly through the valley.", "start": 0.5, "duration": 3.4},
    {"text": "Three travellers stop to rest on the bank.", "start": 4.0, "duration": 3.0},
    {"text": "They speak of the road ahead.", "start": 7.2, "duration": 2.8},
    {"text": "By dusk they will reach the next town.", "start": 10.5, "duration": 3.0},
]


def _available(http_client, path: str) -> bool:
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


def _vault() -> Path:
    with open("emptyos.toml", "rb") as f:
        cfg = tomllib.load(f)
    return Path(cfg["notes"]["path"])


@pytest.mark.dogfood
class TestActiveListeningLifecycle:
    state: dict = {}

    @pytest.fixture(autouse=True)
    def _app_required(self, http_client):
        if not _available(http_client, "/video-digest/api/queue"):
            pytest.skip("video-digest app not loaded")

    def test_01_stage_fixture_digest(self, http_client):
        """Write a YouTube-source web-clip note + .transcript.json sidecar.
        Both `_scan_existing_digests()` and the Listen route must see it on
        the next call."""
        vault = _vault()
        rel = f"30_Resources/Web-Clips/{RUN_ID}.md"
        json_rel = f"30_Resources/Web-Clips/{RUN_ID}.transcript.json"
        note = vault / rel
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "---\n"
            f'title: "{RUN_ID}"\n'
            "speaker: Test\n"
            "source: https://www.youtube.com/watch?v=PLAYWRGHTQQ\n"
            "type: talk\n"
            "clipped: 2026-05-18\n"
            "tags:\n  - web-clip\n"
            "---\n\n## Thesis\n\nDogfood fixture for Step 5.\n",
            encoding="utf-8",
        )
        (vault / json_rel).write_text(
            json.dumps(
                {
                    "video_id": "PLAYWRGHTQQ",
                    "fetched_at": "2026-05-18T00:00:00",
                    "lines": FIXTURE_LINES,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        TestActiveListeningLifecycle.state["rel"] = rel
        TestActiveListeningLifecycle.state["json_rel"] = json_rel
        # Vault watcher debounce — give the index time to see the note
        time.sleep(1.5)

    def test_02_listen_endpoint_returns_timestamped_transcript(self, http_client):
        rel = self.state["rel"]
        resp = http_client.get("/video-digest/api/listen", params={"digest_path": rel})
        assert resp.status_code == 200, resp.text[:300]
        body = resp.json()
        assert body.get("video_id") == "PLAYWRGHTQQ", body
        assert body.get("source_url"), body
        transcript = body.get("transcript") or []
        assert len(transcript) == len(FIXTURE_LINES), f"expected {len(FIXTURE_LINES)} lines, got {len(transcript)}"
        # Timestamps preserved (this is the whole point of Listen mode)
        assert transcript[0]["start"] == pytest.approx(0.5)
        assert transcript[-1]["text"].startswith("By dusk")

    def test_03_end_session_appends_row_and_counter(self, http_client):
        """Simulate a 12-minute session with two captured words; verify both
        vault and english's daily counter advance."""
        rel = self.state["rel"]
        # Snapshot the english `today.listening` counter BEFORE the POST.
        # `/api/activity` returns the raw event list; daily totals live on
        # `/api/dashboard` under `today.<counter>`.
        before = http_client.get("/english/api/dashboard")
        if before.status_code != 200:
            pytest.skip("english app not loaded")
        before_data = before.json() or {}
        before_listening = (before_data.get("today") or {}).get("listening", 0)

        resp = http_client.post(
            "/video-digest/api/listen/end",
            json={
                "digest_path": rel,
                "seconds": 720,
                "words_captured": ["wend", "paucity"],
            },
        )
        assert resp.status_code == 200, resp.text[:300]
        body = resp.json()
        assert body.get("ok") is True, body
        assert body.get("minutes") == 12, body
        assert body.get("sessions_total", 0) >= 1, body

        # Wait for the asyncio.create_task event emit + english handler to
        # process. event-bus dispatch is async — a short sleep is enough.
        time.sleep(1.0)

        after = http_client.get("/english/api/dashboard").json() or {}
        after_listening = (after.get("today") or {}).get("listening", 0)
        assert after_listening >= before_listening + 1, \
            f"listening counter didn't advance: before={before_listening}, after={after_listening}"

        # Verify the section landed on disk
        content = (_vault() / rel).read_text(encoding="utf-8")
        assert "## Listening Sessions" in content
        assert "| 12 |" in content
        assert "wend" in content and "paucity" in content

    def test_04_cleanup(self, http_client):
        """Best-effort cleanup. cleanup_after_all also handles TEST_PREFIX
        files, but a targeted unlink here avoids leftover fixtures between
        runs."""
        rel = self.state.get("rel")
        json_rel = self.state.get("json_rel")
        for p in [rel, json_rel]:
            if not p:
                continue
            try:
                (_vault() / p).unlink()
            except OSError:
                pass
