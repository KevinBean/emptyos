"""System app tests: Music Library — community-tier browse + lyrics surface.

ComfyUI-driven generation tests live in tests/personal/test_sys_music_studio.py
(needs the personal music-studio app + a configured ComfyUI instance).
"""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestMusicLibraryAPI:
    def test_library_list(self, http_client):
        data = assert_ok(http_client.get("/music-library/api/list"))
        assert isinstance(data, (list, dict))

    def test_library_list_enriched_fields(self, http_client):
        """Each row should carry the enrichment fields the UI table reads."""
        data = assert_ok(http_client.get("/music-library/api/list"))
        if not isinstance(data, list) or not data:
            pytest.skip("Empty library")
        s = data[0]
        for k in ("file", "title", "has_lyrics", "has_audio", "audio_count", "modified"):
            assert k in s, f"row missing '{k}'"

    def test_library_search(self, http_client):
        resp = http_client.get("/music-library/api/search?q=test")
        if resp.status_code == 404:
            pytest.skip("search not available")
        assert resp.status_code == 200

    def test_library_stats(self, http_client):
        assert_dict_response(http_client.get("/music-library/api/stats"))

    def test_library_albums(self, http_client):
        data = assert_ok(http_client.get("/music-library/api/albums"))
        assert isinstance(data, (list, dict))

    def test_song_summary_endpoint(self, http_client):
        """The detail panel's one-shot fetch returns a denormalised blob.
        Music-library's version doesn't include mvs/drafts (those live in the
        personal music-studio app); just detail + audio + cover + copyright."""
        rows = http_client.get("/music-library/api/list").json()
        if not rows:
            pytest.skip("Empty library")
        f = rows[0]["file"]
        data = assert_ok(http_client.get(f"/music-library/api/song-summary/{f}"))
        assert isinstance(data, dict)
        for k in ("detail", "audio", "cover", "song_key"):
            assert k in data, f"summary missing '{k}'"

    def test_lyrics_styles(self, http_client):
        data = assert_ok(http_client.get("/music-library/api/lyrics/styles"))
        assert isinstance(data, (list, dict))

    def test_covers_batch(self, http_client):
        """All covers in one fetch — UI uses this to render the grid."""
        data = assert_dict_response(http_client.get("/music-library/api/covers"))
        # Empty dict is fine when there are no songs.
        for k, v in data.items():
            if v is not None:
                assert v.startswith("/music-library/api/image/"), (
                    f"cover URL should be absolute under music-library prefix, got {v}"
                )


@pytest.mark.interactive
class TestMusicLibraryUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("music-library")
        wait_briefly(page, 1500)
        assert page.locator(".ml-grid, .ml-empty").count() >= 1
        assert_no_js_errors(page_errors)

    def test_ui_search_filters(self, app_page, page_errors):
        page = app_page("music-library")
        wait_briefly(page, 1500)
        search = page.locator("#ml-search")
        if search.count() == 0:
            pytest.skip("Library not loaded")
        search.fill("test")
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)

    def test_ui_lyrics_panel_opens(self, app_page, page_errors):
        page = app_page("music-library")
        wait_briefly(page, 1000)
        # Click + Write Lyrics
        page.locator("button.ml-btn", has_text="Write Lyrics").first.click()
        wait_briefly(page, 400)
        assert page.locator("#ml-lyrics-panel.open").count() == 1
        assert_no_js_errors(page_errors)
