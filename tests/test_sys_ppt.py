"""System app tests: Presentations (ppt) — markdown deck CRUD + parse + export."""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok


def _unique_title(suffix: str = "") -> str:
    import time

    return f"{TEST_PREFIX}deck-{int(time.time() * 1000)}{suffix}"


@pytest.mark.api
class TestPptAPI:
    def test_list_returns_list(self, http_client):
        data = assert_list_response(http_client.get("/ppt/api/decks"))
        if data:
            assert "id" in data[0]
            assert "title" in data[0]
            assert "slide_count" in data[0]

    def test_create_starter(self, http_client):
        title = _unique_title()
        r = http_client.post("/ppt/api/decks", json={"title": title})
        data = assert_dict_response(r)
        assert data.get("title") == title
        assert "id" in data and data["id"].startswith(TEST_PREFIX.lower().rstrip("-"))

    def test_create_then_get(self, http_client):
        title = _unique_title("-rt")
        created = http_client.post("/ppt/api/decks", json={"title": title}).json()
        deck = assert_dict_response(http_client.get(f"/ppt/api/decks/{created['id']}"))
        assert deck["frontmatter"]["title"] == title
        assert isinstance(deck["slides"], list)
        assert len(deck["slides"]) >= 2  # starter has title + at least one section
        assert "html" in deck["slides"][0]

    def test_save_overwrites(self, http_client):
        title = _unique_title("-save")
        created = http_client.post("/ppt/api/decks", json={"title": title}).json()
        new_raw = (
            "---\ntitle: " + title + "\ntype: deck\ntags:\n  - deck\n---\n\n"
            "# Hello\n\n---\n\n## Two\n- a\n- b\n"
        )
        r = http_client.put(f"/ppt/api/decks/{created['id']}", json={"raw": new_raw})
        assert_ok(r)
        deck = http_client.get(f"/ppt/api/decks/{created['id']}").json()
        assert len(deck["slides"]) == 2
        assert "Hello" in deck["slides"][0]["html"]

    def test_preview_does_not_persist(self, http_client):
        title = _unique_title("-prev")
        created = http_client.post("/ppt/api/decks", json={"title": title}).json()
        r = http_client.post(
            f"/ppt/api/decks/{created['id']}/preview",
            json={"raw": "# Untouched\n\n---\n\n## Two\n"},
        )
        prev = assert_dict_response(r)
        assert len(prev["slides"]) == 2
        # Original deck unchanged
        deck = http_client.get(f"/ppt/api/decks/{created['id']}").json()
        assert "Hello" not in deck["raw"]

    def test_get_missing(self, http_client):
        r = http_client.get("/ppt/api/decks/this-deck-does-not-exist-zzz")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_speaker_notes_extracted(self, http_client):
        title = _unique_title("-notes")
        created = http_client.post("/ppt/api/decks", json={"title": title}).json()
        raw = (
            "---\ntitle: " + title + "\ntags:\n  - deck\n---\n\n"
            "# T\n\n---\n\n## S\n- a\n\nNotes: speaker tip here\n"
        )
        http_client.put(f"/ppt/api/decks/{created['id']}", json={"raw": raw})
        deck = http_client.get(f"/ppt/api/decks/{created['id']}").json()
        notes = [s["notes"] for s in deck["slides"] if s.get("notes")]
        assert any("speaker tip" in n for n in notes)
        # Notes line stripped from rendered HTML
        assert "speaker tip" not in deck["slides"][1]["html"]

    def test_export_writes_html(self, http_client):
        title = _unique_title("-exp")
        created = http_client.post("/ppt/api/decks", json={"title": title}).json()
        r = http_client.post(f"/ppt/api/decks/{created['id']}/export")
        data = assert_dict_response(r)
        assert data.get("ok") is True
        assert data["rel"].startswith("30_Resources/Published/decks/")
        assert data["rel"].endswith(".html")

    def test_intents_endpoint(self, http_client):
        data = assert_dict_response(http_client.get("/ppt/api/intents"))
        intents = data.get("intents") or []
        ids = {i["id"] for i in intents}
        assert ids >= {"teach", "persuade", "story", "decide", "status", "inspire"}
        for i in intents:
            assert i.get("label") and i.get("guidance")

    def test_elements_endpoint_lists_new_surfaces(self, http_client):
        data = assert_dict_response(http_client.get("/ppt/api/elements"))
        all_elements = set(data.get("all") or [])
        assert all_elements >= {"mermaid", "chart", "narration", "audio", "video"}
        labels = data.get("labels") or {}
        for surface in ("mermaid", "chart", "narration", "audio", "video"):
            assert surface in labels and labels[surface]

    def test_plan_rejects_empty_title(self, http_client):
        r = http_client.post("/ppt/api/plan", json={"title": ""})
        # Either an error payload or a 4xx is acceptable; both signal validation.
        if r.status_code == 200:
            assert "error" in r.json()
        else:
            assert r.status_code >= 400

    def test_generate_from_plan_rejects_empty_plan(self, http_client):
        r = http_client.post("/ppt/api/generate-from-plan", json={"plan": {}})
        if r.status_code == 200:
            assert "error" in r.json()
        else:
            assert r.status_code >= 400

    def test_narrate_missing_deck_returns_error(self, http_client):
        r = http_client.post("/ppt/api/decks/this-deck-does-not-exist-zzz/narrate")
        # Should fail fast without invoking TTS
        if r.status_code == 200:
            assert "error" in r.json()
        else:
            assert r.status_code >= 400


# Pure-function unit tests for the parser — no daemon needed.
class TestPptParser:
    def test_split_on_hr(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("# A\n\n---\n\n## B\n- x\n")
        assert len(out["slides"]) == 2

    def test_frontmatter_parsed(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("---\ntitle: Test\ntheme: light\n---\n\n# A\n")
        assert out["frontmatter"]["title"] == "Test"
        assert out["frontmatter"]["theme"] == "light"

    def test_notes_html_comment(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("# A\n<!-- notes: hidden tip -->\nVisible body\n")
        assert "hidden tip" in out["slides"][0]["notes"]
        assert "hidden tip" not in out["slides"][0]["html"]

    def test_empty_deck_safe(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("")
        assert out["slides"] == []

    def test_audio_placeholder_renders_audio_tag(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("## a\n\n![audio: clip.mp3]\n", asset_url_prefix="/ppt/api/asset/d")
        html = out["slides"][0]["html"]
        assert "<audio" in html and "ppt-audio" in html
        assert "/ppt/api/asset/d/clip.mp3" in html

    def test_video_placeholder_youtube_to_embed(self):
        from apps.ppt.app import parse_deck

        out = parse_deck(
            "## a\n\n![video: https://www.youtube.com/watch?v=abc123XYZ]\n"
        )
        html = out["slides"][0]["html"]
        assert "youtube.com/embed/abc123XYZ" in html
        assert "ppt-video-yt" in html

    def test_video_placeholder_mp4_renders_video_tag(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("## a\n\n![video: clip.mp4]\n", asset_url_prefix="/ppt/api/asset/d")
        html = out["slides"][0]["html"]
        assert "<video" in html and "ppt-video" in html
        assert "/ppt/api/asset/d/clip.mp4" in html

    def test_wikilink_audio_renders_audio_tag(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("## a\n\n![[song.mp3]]\n", asset_url_prefix="/ppt/api/asset/d")
        html = out["slides"][0]["html"]
        assert "<audio" in html
        assert "/ppt/api/asset/d/song.mp3" in html

    def test_wikilink_video_renders_video_tag(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("## a\n\n![[demo.mp4]]\n", asset_url_prefix="/ppt/api/asset/d")
        html = out["slides"][0]["html"]
        assert "<video" in html
        assert "/ppt/api/asset/d/demo.mp4" in html

    def test_default_elements_include_new_surfaces(self):
        from apps.ppt.parser import DEFAULT_ELEMENTS

        for s in ("mermaid", "chart", "narration", "audio", "video"):
            assert s in DEFAULT_ELEMENTS

    def test_intents_have_guidance(self):
        from apps.ppt.parser import INTENTS

        assert set(INTENTS.keys()) == {"teach", "persuade", "story", "decide", "status", "inspire"}
        for k, v in INTENTS.items():
            assert v.get("label") and v.get("guidance")

    def test_embed_base_default_is_current_host(self):
        from apps.ppt.app import parse_deck

        out = parse_deck("## a\n\n![embed: /journal/]\n")
        assert 'src="/journal/?demo=1"' in out["slides"][0]["html"]

    def test_embed_base_prepends_to_relative(self):
        from apps.ppt.app import parse_deck

        out = parse_deck(
            "## a\n\n![embed: /journal/]\n",
            embed_base="http://localhost:9001",
        )
        assert 'src="http://localhost:9001/journal/?demo=1"' in out["slides"][0]["html"]

    def test_embed_base_preserves_existing_query(self):
        from apps.ppt.app import parse_deck

        out = parse_deck(
            "## a\n\n![embed: /journal/?tab=mood]\n",
            embed_base="https://demo.binbian.net",
        )
        # &amp; is the HTML-escaped form of & — correct for an attribute value.
        assert 'src="https://demo.binbian.net/journal/?tab=mood&amp;demo=1"' in out["slides"][0]["html"]

    def test_embed_base_leaves_external_urls_alone(self):
        from apps.ppt.app import parse_deck

        out = parse_deck(
            "## a\n\n![embed: https://example.com/foo]\n",
            embed_base="http://localhost:9001",
        )
        assert 'src="https://example.com/foo"' in out["slides"][0]["html"]


@pytest.mark.interactive
class TestPptUI:
    def test_page_loads(self, page, base_url):
        from page_helpers import assert_no_js_errors

        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base_url + "/ppt/")
        page.wait_for_selector(".ppt-shell", timeout=8000)
        assert_no_js_errors(errors)

    def test_settings_panel_opens(self, page, base_url):
        page.goto(base_url + "/ppt/")
        page.wait_for_selector(".ppt-shell", timeout=8000)
        page.click("button[title='Settings']")
        page.wait_for_selector(".eos-settings-panel.open, #ppt-settings-panel.open", timeout=4000)
