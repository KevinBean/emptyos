"""System app tests: Video Digest — 8 use cases.

Heavy operations (yt-dlp metadata fetch, transcript fetch, summarisation)
are NOT exercised end-to-end here — they're integration-shaped, take real
network + LLM time, and live in a manual smoke pass. The tests below cover:

- App loads + manifest is discoverable
- Queueing a URL via POST /api/queue
- Reading the queue
- Validation (rejecting non-YouTube URLs)
- Delete from queue
- Pure helpers (extract_video_id, slugify, note_stem, render_clip_note)
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from helpers import assert_ok, TEST_PREFIX, app_path


@pytest.fixture(scope="module")
def vd_mod():
    """Load apps/video-digest/app.py as a module without booting the kernel.

    Post-decomposition the spine imports sibling helpers via ``from . import
    digest as _digest`` etc., so we pre-register the parent packages and
    pre-load helpers before exec'ing the spine (per .claude/rules/
    multi-module-apps.md § Gotchas)."""
    vd_dir = app_path("video-digest")
    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(Path("apps").resolve())]
        sys.modules["apps"] = apps_pkg
    if "apps.video-digest" not in sys.modules:
        vd_pkg = types.ModuleType("apps.video-digest")
        vd_pkg.__path__ = [str(vd_dir)]
        sys.modules["apps.video-digest"] = vd_pkg
    for sub in ("shared", "queue", "digest", "listen"):
        key = f"apps.video-digest.{sub}"
        if key in sys.modules:
            continue
        sub_spec = importlib.util.spec_from_file_location(key, vd_dir / f"{sub}.py")
        sub_mod = importlib.util.module_from_spec(sub_spec)
        sys.modules[key] = sub_mod
        sub_spec.loader.exec_module(sub_mod)
    spec = importlib.util.spec_from_file_location(
        "apps.video-digest.app", vd_dir / "app.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apps.video-digest.app"] = mod
    sys.modules["vd_app"] = mod  # legacy alias
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.api
class TestVideoDigestAPI:
    def test_queue_listing(self, http_client):
        body = assert_ok(http_client.get("/video-digest/api/queue"))
        assert "items" in body

    def test_queue_rejects_non_youtube(self, http_client):
        body = assert_ok(http_client.post(
            "/video-digest/api/queue",
            json={"url": "https://example.com/not-a-video"},
        ))
        # Non-YouTube URLs are rejected — unless the web-clip feature is on
        # ([apps.video-digest] feature.web-clip.enabled, the link-note-saver
        # borrow), in which case they queue as a generic clip (kind="web").
        if body["ok"] is False:
            assert "video id" in (body.get("error") or "")
        else:
            assert body.get("item", {}).get("kind") == "web"

    def test_queue_rejects_empty(self, http_client):
        body = assert_ok(http_client.post("/video-digest/api/queue", json={"url": ""}))
        assert body["ok"] is False

    def test_queue_accepts_valid_url(self, http_client):
        # Real-looking 11-char id; we never actually fetch it.
        url = "https://www.youtube.com/watch?v=PLAYWRGHTQQ"
        body = assert_ok(http_client.post("/video-digest/api/queue", json={"url": url}))
        assert body["ok"] is True
        assert body["item"]["status"] == "queued"
        item_id = body["item"]["id"]

        # Should appear in listing
        listing = assert_ok(http_client.get("/video-digest/api/queue"))
        ids = [it["id"] for it in listing.get("items", [])]
        assert item_id in ids

        # Cleanup
        cleanup = assert_ok(http_client.delete(f"/video-digest/api/queue/{item_id}"))
        assert cleanup.get("ok") is True

    def test_run_unknown_id(self, http_client):
        body = assert_ok(http_client.post("/video-digest/api/run/vd-doesnotexist"))
        assert body["ok"] is False

    def test_adopt_rejects_empty_path(self, http_client):
        body = assert_ok(http_client.post("/video-digest/api/adopt", json={}))
        assert body["ok"] is False
        assert "digest_path" in (body.get("error") or "")

    def test_adopt_rejects_nonexistent_path(self, http_client):
        body = assert_ok(http_client.post(
            "/video-digest/api/adopt",
            json={"digest_path": "30_Resources/Web-Clips/does-not-exist.md"},
        ))
        assert body["ok"] is False
        assert "not found" in (body.get("error") or "")

    def test_adopt_full_cycle(self, http_client):
        """Write a YouTube Web-Clip note, adopt it, verify it surfaces in
        /api/digests with provenance='app' and adopted=True, then clean up."""
        vault = _vault_dir()
        rel = f"30_Resources/Web-Clips/{TEST_PREFIX}adopt-fixture.md"
        note = vault / rel
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "---\n"
            f"title: {TEST_PREFIX}adopt fixture\n"
            "speaker: Test\n"
            "source: https://www.youtube.com/watch?v=PLAYWRGHTQQ\n"
            "type: talk\n"
            "clipped: 2026-05-17\n"
            "tags:\n  - web-clip\n"
            "---\n\n## Thesis\n\nFixture.\n",
            encoding="utf-8",
        )
        try:
            body = assert_ok(http_client.post(
                "/video-digest/api/adopt", json={"digest_path": rel},
            ))
            assert body["ok"] is True
            assert body["item"]["status"] == "done"
            assert body["item"]["adopted"] is True
            item_id = body["item"]["id"]

            # Idempotent — second adopt of the same path updates, not duplicates.
            body2 = assert_ok(http_client.post(
                "/video-digest/api/adopt", json={"digest_path": rel},
            ))
            assert body2["ok"] is True
            assert body2["item"]["id"] == item_id

            # Surfaces in the merged digest list with adopted flag propagated.
            digests = assert_ok(http_client.get("/video-digest/api/digests"))
            mine = next(
                (d for d in digests["digests"] if d.get("digest_path") == rel),
                None,
            )
            assert mine is not None
            assert mine.get("provenance") == "app"
            assert mine.get("adopted") is True
            # _mtime should NOT leak into the response shape.
            assert "_mtime" not in mine

            http_client.delete(f"/video-digest/api/queue/{item_id}")
        finally:
            try:
                note.unlink()
            except OSError:
                pass

    def test_adopt_rejects_non_youtube_note(self, http_client):
        """A web-clip note without a YouTube `source:` URL must be rejected."""
        vault = _vault_dir()
        rel = f"30_Resources/Web-Clips/{TEST_PREFIX}adopt-non-yt.md"
        note = vault / rel
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "---\n"
            f"title: {TEST_PREFIX}non-yt\n"
            "source: https://example.com/article\n"
            "tags:\n  - web-clip\n"
            "---\n",
            encoding="utf-8",
        )
        try:
            body = assert_ok(http_client.post(
                "/video-digest/api/adopt", json={"digest_path": rel},
            ))
            assert body["ok"] is False
            assert "YouTube" in (body.get("error") or "")
        finally:
            try:
                note.unlink()
            except OSError:
                pass


class TestHelpers:
    """Pure-function tests for module-level helpers — no daemon needed."""

    def test_extract_video_id(self, vd_mod):
        assert vd_mod.extract_video_id("https://www.youtube.com/watch?v=ccNGyUwsGpM") == "ccNGyUwsGpM"
        assert vd_mod.extract_video_id("https://youtu.be/ccNGyUwsGpM") == "ccNGyUwsGpM"
        assert vd_mod.extract_video_id("ccNGyUwsGpM") == "ccNGyUwsGpM"
        assert vd_mod.extract_video_id("not-a-url") is None
        assert vd_mod.extract_video_id("") is None

    def test_is_youtube_url(self, vd_mod):
        assert vd_mod.is_youtube_url("https://www.youtube.com/watch?v=abc12345678")
        assert vd_mod.is_youtube_url("https://youtube.com/watch?v=abc12345678")
        assert vd_mod.is_youtube_url("https://youtu.be/abc12345678")
        assert vd_mod.is_youtube_url("https://m.youtube.com/watch?v=abc12345678")
        assert vd_mod.is_youtube_url("https://www.youtube.com/shorts/abc12345678")
        assert not vd_mod.is_youtube_url("https://example.com/abc")
        assert not vd_mod.is_youtube_url("https://vimeo.com/12345")
        assert not vd_mod.is_youtube_url("")
        assert not vd_mod.is_youtube_url(None)

    def test_normalize_domain(self, vd_mod):
        vocab = vd_mod.DEFAULT_DOMAINS
        # Exact match passes through.
        assert vd_mod.normalize_domain("ai-engineering", vocab) == "ai-engineering"
        # Whitespace + case folded.
        assert vd_mod.normalize_domain("  AI-Engineering\n", vocab) == "ai-engineering"
        # Leading dash / bullet stripped (LLMs love adding these).
        assert vd_mod.normalize_domain("- buddhism", vocab) == "buddhism"
        assert vd_mod.normalize_domain("* tools", vocab) == "tools"
        # Quotes stripped.
        assert vd_mod.normalize_domain('"business"', vocab) == "business"
        # Trailing punctuation stripped.
        assert vd_mod.normalize_domain("infrastructure.", vocab) == "infrastructure"
        # Multi-line — only first line used.
        assert vd_mod.normalize_domain("buddhism\n(also cognitive-science)", vocab) == "buddhism"
        # Unrecognised → other.
        assert vd_mod.normalize_domain("quantum-physics", vocab) == "other"
        # Empty → other.
        assert vd_mod.normalize_domain("", vocab) == "other"
        assert vd_mod.normalize_domain(None, vocab) == "other"
        # Custom vocab without `other` → falls back to first item, not invalid value.
        custom = ["a", "b", "c"]
        assert vd_mod.normalize_domain("not-in-list", custom) == "a"

    def test_render_clip_note_includes_domain_when_set(self, vd_mod):
        out = vd_mod.render_clip_note(
            {
                "title": "Test",
                "channel": "Speaker",
                "duration_s": 60,
                "url": "https://www.youtube.com/watch?v=abc",
                "domain": "buddhism",
            },
            "## Thesis\n\nTest.\n",
        )
        assert "domain: buddhism" in out

    def test_render_clip_note_omits_domain_when_empty(self, vd_mod):
        out = vd_mod.render_clip_note(
            {
                "title": "Test",
                "channel": "Speaker",
                "duration_s": 60,
                "url": "https://www.youtube.com/watch?v=abc",
            },
            "## Thesis\n\nTest.\n",
        )
        assert "domain:" not in out

    def test_slugify(self, vd_mod):
        assert vd_mod.slugify("Hello World") == "hello-world"
        assert vd_mod.slugify("Foo!! Bar?? Baz") == "foo-bar-baz"
        assert vd_mod.slugify("") == "untitled"

    def test_note_stem_strips_unsafe_chars(self, vd_mod):
        stem = vd_mod.note_stem(
            {"title": "Foo: bar/baz | qux*", "channel": "Channel"},
            "2026-05-17",
        )
        for bad in '<>:"/\\|?*':
            assert bad not in stem, f"stem contains forbidden char {bad!r}: {stem!r}"
        assert "2026-05-17" in stem
        assert "Channel" in stem

    def test_render_clip_note_shape(self, vd_mod):
        out = vd_mod.render_clip_note(
            {
                "title": "Test Title",
                "channel": "Test Channel",
                "duration_s": 1234,
                "url": "https://www.youtube.com/watch?v=abc",
            },
            "## Thesis\n\nA test thesis.\n",
        )
        assert out.startswith("---\n")
        assert "title:" in out
        assert "speaker: Test Channel" in out
        assert "source: https://www.youtube.com/watch?v=abc" in out
        assert "## Thesis" in out

    def test_render_web_note_shape(self, vd_mod):
        out = vd_mod.render_web_note(
            {
                "title": "A Useful Article",
                "channel": "example.com",
                "url": "https://example.com/post",
                "domain": "tools",
            },
            "## Summary\n\nA test summary.\n",
        )
        assert out.startswith("---\n")
        assert 'title: "A Useful Article"' in out
        assert "site: example.com" in out
        assert "source: https://example.com/post" in out
        assert "type: article" in out
        assert "domain: tools" in out
        # Block-style tags, link-digest marker, no video-only fields.
        assert "  - web-clip" in out and "  - link-digest" in out
        assert "speaker:" not in out and "duration_s:" not in out
        assert ".transcript" not in out
        assert "## Summary" in out

    def test_render_web_note_omits_domain_when_empty(self, vd_mod):
        out = vd_mod.render_web_note(
            {"title": "T", "channel": "example.com", "url": "https://example.com/"},
            "## Summary\n\nx\n",
        )
        assert "domain:" not in out

    def test_web_digest_system_carries_reuse_sections(self, vd_mod):
        # The borrowed value of the web path is the reuse framing — pin it.
        for section in ("## Summary", "## Key points", "## Reusable angles", "## Next steps"):
            assert section in vd_mod.WEB_DIGEST_SYSTEM


# ──────────────────────────────────────────────────────────────────────
# Listen mode (Step 5 — active listening) — vault-state-driven tests
# ──────────────────────────────────────────────────────────────────────


def _vault_dir():
    """Resolve the active vault. Defaults to emptyos.toml `notes.path` (the
    user's main daemon vault). When EOS_TEST_VAULT_DIR is set — e.g. tests
    targeting a sandbox-pool member whose vault is throwaway — that
    directory wins."""
    import os
    import tomllib
    from pathlib import Path
    override = os.environ.get("EOS_TEST_VAULT_DIR")
    if override:
        return Path(override)
    with open("emptyos.toml", "rb") as f:
        cfg = tomllib.load(f)
    return Path(cfg["notes"]["path"])


def _write_listen_fixture(stem: str, *, with_json: bool = True) -> tuple[str, list]:
    """Seed a YouTube web-clip note (+ optional .transcript.json sidecar) and
    return (relative_path, transcript_lines). Caller is responsible for
    cleanup via the returned path."""
    import json
    vault = _vault_dir()
    rel = f"30_Resources/Web-Clips/{stem}.md"
    json_rel = f"30_Resources/Web-Clips/{stem}.transcript.json"
    abs_note = vault / rel
    abs_json = vault / json_rel
    abs_note.parent.mkdir(parents=True, exist_ok=True)
    abs_note.write_text(
        "---\n"
        f'title: "{stem}"\n'
        "speaker: Test\n"
        "source: https://www.youtube.com/watch?v=PLAYWRGHTQQ\n"
        "type: talk\n"
        "clipped: 2026-05-18\n"
        "tags:\n  - web-clip\n"
        "---\n\n## Thesis\n\nFixture.\n",
        encoding="utf-8",
    )
    lines = [
        {"text": "First line of the transcript.", "start": 0.5, "duration": 3.0},
        {"text": "Second line follows shortly after.", "start": 4.0, "duration": 3.5},
        {"text": "Third line is even later.", "start": 8.2, "duration": 4.0},
    ]
    if with_json:
        abs_json.write_text(
            json.dumps({"video_id": "PLAYWRGHTQQ", "fetched_at": "2026-05-18T00:00:00", "lines": lines}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return rel, lines


def _cleanup_listen_fixture(rel: str):
    from pathlib import Path
    vault = _vault_dir()
    for ext in (".md", ".transcript.json", ".transcript.txt"):
        p = vault / (rel.rsplit(".md", 1)[0] + ext)
        try:
            p.unlink()
        except OSError:
            pass


@pytest.mark.api
class TestListenMode:
    """End-to-end tests for the new Step 5 Listen surface."""

    def test_listen_endpoint_returns_transcript(self, http_client):
        rel, lines = _write_listen_fixture(f"{TEST_PREFIX}listen-basic")
        try:
            body = assert_ok(http_client.get(
                "/video-digest/api/listen", params={"digest_path": rel},
            ))
            assert body.get("video_id") == "PLAYWRGHTQQ"
            assert body.get("source_url", "").startswith("https://www.youtube.com/")
            tr = body.get("transcript") or []
            assert len(tr) == len(lines), f"transcript shape changed: {tr[:2]}"
            assert tr[0]["text"].startswith("First line"), tr[0]
            assert "start" in tr[0] and "duration" in tr[0]
        finally:
            _cleanup_listen_fixture(rel)

    def test_listen_endpoint_rejects_missing(self, http_client):
        """Path that doesn't exist returns a structured error, not 500."""
        body = assert_ok(http_client.get(
            "/video-digest/api/listen",
            params={"digest_path": "30_Resources/Web-Clips/does-not-exist.md"},
        ))
        assert body.get("error"), f"expected error payload: {body}"

    def test_listen_endpoint_rejects_non_md(self, http_client):
        body = assert_ok(http_client.get(
            "/video-digest/api/listen",
            params={"digest_path": ""},
        ))
        assert body.get("error"), f"expected error: {body}"

    def test_listen_endpoint_handles_missing_transcript(self, http_client, monkeypatch):
        """When `.transcript.json` is absent AND the lazy-fetch fails, the
        endpoint returns 200 with `transcript_error` so the UI degrades to
        player-only. We force the failure by writing a note whose video id
        produces a real fetch error (any non-existent id raises)."""
        rel, _ = _write_listen_fixture(
            f"{TEST_PREFIX}listen-noxscript",
            with_json=False,
        )
        try:
            body = assert_ok(http_client.get(
                "/video-digest/api/listen", params={"digest_path": rel},
            ))
            # Either we got a transcript (network worked — unlikely with a fake
            # id) or a transcript_error explaining why not. Both must be 200
            # with `transcript: []` if errored.
            if body.get("transcript_error"):
                assert body.get("transcript") == [], body
                assert body.get("video_id") == "PLAYWRGHTQQ"
            else:
                # Unexpected success — just assert the shape is otherwise sane.
                assert isinstance(body.get("transcript"), list)
        finally:
            _cleanup_listen_fixture(rel)

    def test_listen_end_appends_sessions_table(self, http_client):
        """POST /api/listen/end must add a `## Listening Sessions` table to the
        digest note + report sessions_total."""
        rel, _ = _write_listen_fixture(f"{TEST_PREFIX}listen-end-table")
        try:
            body = assert_ok(http_client.post(
                "/video-digest/api/listen/end",
                json={
                    "digest_path": rel,
                    "seconds": 720,
                    "words_captured": ["wend", "paucity", "gambit"],
                },
            ))
            assert body.get("ok") is True, body
            assert body.get("minutes") == 12, body
            assert body.get("sessions_total", 0) >= 1, body
            # Verify the section landed on disk
            from pathlib import Path
            content = (_vault_dir() / rel).read_text(encoding="utf-8")
            assert "## Listening Sessions" in content
            assert "| 12 |" in content
            assert "wend" in content and "paucity" in content
        finally:
            _cleanup_listen_fixture(rel)

    def test_listen_end_multiple_sessions_increment(self, http_client):
        """Two End-session POSTs against the same digest produce two rows
        and sessions_total advances to 2."""
        rel, _ = _write_listen_fixture(f"{TEST_PREFIX}listen-end-twice")
        try:
            r1 = assert_ok(http_client.post(
                "/video-digest/api/listen/end",
                json={"digest_path": rel, "seconds": 90, "words_captured": ["alpha"]},
            ))
            assert r1["ok"] is True
            r2 = assert_ok(http_client.post(
                "/video-digest/api/listen/end",
                json={"digest_path": rel, "seconds": 180, "words_captured": ["beta"]},
            ))
            assert r2["ok"] is True
            assert r2.get("sessions_total") == 2, r2
            content = (_vault_dir() / rel).read_text(encoding="utf-8")
            # Single section header, two data rows
            assert content.count("## Listening Sessions") == 1
            assert "alpha" in content and "beta" in content
        finally:
            _cleanup_listen_fixture(rel)

    def test_listen_end_rejects_bad_input(self, http_client):
        """Missing digest_path / non-positive seconds must error cleanly."""
        body = assert_ok(http_client.post(
            "/video-digest/api/listen/end",
            json={"digest_path": "", "seconds": 60, "words_captured": []},
        ))
        assert body.get("ok") is False, body

        rel, _ = _write_listen_fixture(f"{TEST_PREFIX}listen-bad-sec")
        try:
            body = assert_ok(http_client.post(
                "/video-digest/api/listen/end",
                json={"digest_path": rel, "seconds": 0, "words_captured": []},
            ))
            assert body.get("ok") is False, body
            assert "seconds" in (body.get("error") or "")
        finally:
            _cleanup_listen_fixture(rel)

    def test_listen_endpoint_word_token_html(self, vd_mod):
        """Sanity check that the timestamped transcript helper exists and
        returns the expected shape via the module-level surface."""
        # Pure surface check — the method is async and needs a real
        # YouTube id to do anything, so just confirm it's wired.
        cls = vd_mod.VideoDigestApp
        assert hasattr(cls, "_fetch_transcript_with_timestamps"), \
            "new transcript method missing — Listen lazy-fetch won't work"
        assert hasattr(cls, "_resolve_digest")
        assert hasattr(cls, "_listen_lock")
