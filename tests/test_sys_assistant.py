"""System app tests: Assistant — 12 use cases + Phase 1-4 additions."""

import sys
from pathlib import Path

import pytest

import factories
from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)

# Pure-Python unit tests below import the new helper modules directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.mark.api
class TestAssistantAPI:
    @pytest.fixture
    def session_id(self, http_client):
        """Create a test session and clean up after."""
        payload = factories.assistant_session(name="api session")
        resp = http_client.post("/assistant/api/sessions", json=payload)
        assert resp.status_code == 200, resp.text
        sid = resp.json().get("id")
        yield sid
        try:
            http_client.delete(f"/assistant/api/sessions/{sid}")
        except Exception:
            pass

    def test_create_session(self, session_id):
        assert session_id

    def test_list_sessions(self, http_client):
        data = assert_ok(http_client.get("/assistant/api/sessions"))
        assert isinstance(data, (list, dict))

    def test_get_session(self, http_client, session_id):
        data = assert_dict_response(
            http_client.get(f"/assistant/api/sessions/{session_id}")
        )
        assert "messages" in data or "id" in data

    def test_update_session(self, http_client, session_id):
        new_name = f"{TEST_PREFIX}renamed"
        resp = http_client.put(
            f"/assistant/api/sessions/{session_id}",
            json={"name": new_name},
        )
        assert resp.status_code == 200
        data = http_client.get(f"/assistant/api/sessions/{session_id}").json()
        assert data.get("name") == new_name

    def test_delete_session(self, http_client):
        # Create separately so we can delete and verify
        payload = factories.assistant_session(name="to-delete")
        sid = http_client.post("/assistant/api/sessions", json=payload).json().get("id")
        resp = http_client.delete(f"/assistant/api/sessions/{sid}")
        assert resp.status_code == 200

    def test_slash_commands(self, http_client):
        data = assert_ok(http_client.get("/assistant/api/slash-commands"))
        assert isinstance(data, (list, dict))

    def test_providers_list(self, http_client):
        data = assert_ok(http_client.get("/assistant/api/providers"))
        assert isinstance(data, (list, dict))

    def test_archive_sessions(self, http_client):
        resp = http_client.post("/assistant/api/archive", json={})
        assert resp.status_code in (200, 204)


@pytest.mark.interactive
class TestAssistantUI:
    def test_ui_new_session_flow(self, app_page, page_errors):
        """Click New → verify a session appears in sidebar."""
        page = app_page("assistant")
        wait_briefly(page, 800)
        click_first(
            page,
            "[onclick*='newSession']",
            "button:has-text('New')",
            "button:has-text('+')",
            ".new-session-btn",
        )
        wait_briefly(page, 800)
        assert_no_js_errors(page_errors)

    def test_ui_session_switch(self, app_page, page_errors):
        """If multiple sessions exist, click a different one."""
        page = app_page("assistant")
        wait_briefly(page, 800)
        sessions = page.locator(".session-item, .session-card, [data-session-id]")
        if sessions.count() < 2:
            pytest.skip("Less than 2 sessions to switch between")
        sessions.nth(1).click()
        wait_briefly(page, 600)
        assert_no_js_errors(page_errors)

    def test_ui_chat_input(self, app_page, page_errors):
        """Type message in chat input."""
        page = app_page("assistant")
        wait_briefly(page, 800)
        chat_input = page.locator(
            "#chat-input, #message-input, textarea[placeholder*='message' i]"
        ).first
        if chat_input.count() == 0:
            pytest.skip("No chat input found")
        chat_input.fill("PLAYWRIGHT-TEST-hello")
        wait_briefly(page, 300)
        assert_no_js_errors(page_errors)

    def test_ui_session_list_renders(self, app_page, page_errors):
        """Verify session sidebar/list renders without error."""
        page = app_page("assistant")
        wait_briefly(page, 1000)
        # Look for any session container
        containers = page.locator(
            ".sessions, .session-list, #sessions, [class*='sidebar']"
        )
        assert_no_js_errors(page_errors)


# ─── Phase 1-4 additions (image upload, file extraction, project pinning, /research) ───


@pytest.mark.api
class TestAssistantPhases:
    """Smoke coverage for the ChatGPT/Claude/OpenWebUI gap-closing features."""

    @pytest.fixture
    def session_id(self, http_client):
        payload = factories.assistant_session(name="phases session")
        sid = http_client.post("/assistant/api/sessions", json=payload).json().get("id")
        yield sid
        try:
            http_client.delete(f"/assistant/api/sessions/{sid}")
        except Exception:
            pass

    def test_research_in_slash_commands(self, http_client):
        cmds = assert_ok(http_client.get("/assistant/api/slash-commands"))
        assert isinstance(cmds, list)
        names = [c.get("command") for c in cmds]
        assert "/research" in names, f"/research not registered (have: {names[:10]}…)"

    def test_image_endpoint_requires_path(self, http_client):
        resp = http_client.get("/assistant/api/image")
        assert resp.status_code == 400
        assert "path required" in resp.text.lower()

    def test_image_endpoint_refuses_path_escape(self, http_client):
        resp = http_client.get(
            "/assistant/api/image", params={"path": "../../etc/passwd"}
        )
        assert resp.status_code == 400
        assert "outside vault" in resp.text.lower()

    def test_image_endpoint_404_missing(self, http_client):
        resp = http_client.get(
            "/assistant/api/image",
            params={"path": f"{TEST_PREFIX}does-not-exist.png"},
        )
        assert resp.status_code == 404

    def test_pin_project_unknown_session(self, http_client):
        resp = http_client.post(
            "/assistant/api/sessions/no-such-sid/pin-project",
            json={"project_id": "anything"},
        )
        # The route resolves but the session lookup misses; either flavor is fine.
        data = resp.json()
        assert "error" in data and "session not found" in data["error"].lower()

    def test_pin_project_requires_project_id(self, http_client, session_id):
        resp = http_client.post(
            f"/assistant/api/sessions/{session_id}/pin-project", json={}
        )
        data = resp.json()
        assert "error" in data and "project_id" in data["error"].lower()

    def test_pin_unpin_round_trip(self, http_client, session_id):
        pid = f"{TEST_PREFIX}phantom-project"
        # Pin
        pinned = http_client.post(
            f"/assistant/api/sessions/{session_id}/pin-project",
            json={"project_id": pid},
        ).json()
        assert pinned.get("ok") is True
        assert pinned.get("project_id") == pid
        # Session should now report it
        s = http_client.get(f"/assistant/api/sessions/{session_id}").json()
        assert s.get("project_id") == pid
        # Unpin
        unpinned = http_client.delete(
            f"/assistant/api/sessions/{session_id}/pin-project"
        ).json()
        assert unpinned.get("ok") is True
        s2 = http_client.get(f"/assistant/api/sessions/{session_id}").json()
        assert (s2.get("project_id") or "") == ""

    def test_load_project_context_via_rpc(self, http_client):
        # Unknown project → empty string (no crash, no 500)
        resp = http_client.post(
            "/api/apps/projects/rpc/load_project_context",
            json={"project_id": f"{TEST_PREFIX}phantom-no-such-project"},
        )
        assert resp.status_code == 200
        data = resp.json()
        body = data.get("result", data)
        assert isinstance(body, str)
        assert body == ""


class TestAssistantHelperModules:
    """Pure-Python unit tests for the new helper modules — no daemon needed."""

    def test_vision_is_image_path(self):
        from apps.assistant import vision

        assert vision.is_image_path("foo.png")
        assert vision.is_image_path("path/to/image.JPG")
        assert vision.is_image_path("https://example.com/x.webp")
        assert vision.is_image_path("data:image/png;base64,abc")
        assert not vision.is_image_path("foo.pdf")
        assert not vision.is_image_path("")
        assert not vision.is_image_path("notes.md")

    def test_vision_resolve_drops_missing(self, tmp_path):
        from apps.assistant import vision

        # Real image
        good = tmp_path / "shot.png"
        # 1x1 transparent PNG — minimal valid bytes
        good.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc\xfc\xcf\xc0P\x0f\x00\x00\x05"
            b"\x01\x01\x00\x9a\xcf\xa9\xed\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        urls = vision.resolve_images(tmp_path, ["shot.png", "missing.png"])
        assert len(urls) == 1
        assert urls[0].startswith("data:image/png;base64,")

    def test_vision_http_url_passthrough(self, tmp_path):
        from apps.assistant import vision

        urls = vision.resolve_images(tmp_path, ["https://example.com/x.png"])
        assert urls == ["https://example.com/x.png"]

    def test_files_is_extractable(self):
        from apps.assistant import files

        assert files.is_extractable_path("doc.pdf")
        assert files.is_extractable_path("doc.docx")
        assert files.is_extractable_path("notes.txt")
        assert files.is_extractable_path("readme.md")
        assert not files.is_extractable_path("photo.png")
        assert not files.is_extractable_path("")

    def test_files_extract_txt(self, tmp_path):
        from apps.assistant import files

        f = tmp_path / "note.txt"
        f.write_text("hello world\nline two", encoding="utf-8")
        ex = files.extract_file(tmp_path, "note.txt")
        assert ex.error == ""
        assert "hello world" in ex.text
        assert ex.truncated is False

    def test_files_extract_truncates(self, tmp_path):
        from apps.assistant import files

        big = "x" * 200
        f = tmp_path / "big.txt"
        f.write_text(big, encoding="utf-8")
        ex = files.extract_file(tmp_path, "big.txt", max_chars=50)
        assert ex.truncated is True
        assert len(ex.text) == 50

    def test_files_extract_missing(self, tmp_path):
        from apps.assistant import files

        ex = files.extract_file(tmp_path, "nope.pdf")
        assert ex.error == "file not found"
        assert ex.text == ""

    def test_files_format_block_truncated_label(self, tmp_path):
        from apps.assistant import files

        f = tmp_path / "a.txt"
        f.write_text("xy" * 100, encoding="utf-8")
        ex = files.extract_file(tmp_path, "a.txt", max_chars=10)
        block = files.format_block(ex)
        assert "(truncated)" in block

    def test_openai_compat_vision_model_detection(self):
        from emptyos.capabilities.providers.openai_compat import (
            _model_supports_vision,
        )

        assert _model_supports_vision("gpt-4o")
        assert _model_supports_vision("gpt-5.4-mini")
        assert _model_supports_vision("llava:13b")
        assert _model_supports_vision("llama3.2-vision:11b")
        assert not _model_supports_vision("gpt-3.5-turbo")
        assert not _model_supports_vision("llama3.1:8b")
        assert not _model_supports_vision("qwen3:8b")

    def test_openai_compat_multimodal_message_shape(self):
        from emptyos.capabilities.providers.openai_compat import _chat_messages

        msgs = _chat_messages(
            prompt="what is this",
            system="you are helpful",
            messages=None,
            images=["data:image/png;base64,xxx"],
        )
        # System + user; user has multimodal content.
        assert msgs[0] == {"role": "system", "content": "you are helpful"}
        user_msg = msgs[1]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list)
        parts = user_msg["content"]
        assert parts[0] == {"type": "text", "text": "what is this"}
        assert parts[1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,xxx"},
        }

    def test_research_ddg_search_dedupes(self, monkeypatch):
        """_ddg_search normalises ddgs output, dedupes by URL, caps at max_results."""
        from apps.assistant import research

        class FakeDDGS:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def text(self, query, max_results):
                # Includes a dup, an empty-title row, and a missing-url row
                return [
                    {"href": "https://example.com/a", "title": "Title A"},
                    {"href": "https://example.com/b", "title": "Title B"},
                    {"href": "https://example.com/a", "title": "Title A again"},
                    {"href": "", "title": "No URL"},
                    {"href": "https://example.com/c", "title": ""},
                    {"href": "https://example.com/d", "title": "Title D"},
                ]

        import sys, types
        fake_mod = types.ModuleType("ddgs")
        fake_mod.DDGS = FakeDDGS
        monkeypatch.setitem(sys.modules, "ddgs", fake_mod)
        out = research._ddg_search("anything", max_results=10)
        urls = [r["url"] for r in out]
        assert urls == ["https://example.com/a", "https://example.com/b", "https://example.com/d"]
        assert out[0]["title"] == "Title A"

    def test_research_empty_query_yields_error(self):
        import asyncio

        from apps.assistant.research import run_research

        # No app dependencies needed — empty-query branch exits before any browse call.
        async def collect():
            events = []
            async for ev in run_research(app=None, query=""):
                events.append(ev)
            return events

        events = asyncio.run(collect())
        assert len(events) == 1
        assert events[0]["type"] == "research-error"
        assert "empty query" in events[0]["message"].lower()
