"""System app tests: Radio — channels, sequencer, kiosk public surface."""

import pytest

from helpers import assert_dict_response, assert_ok


@pytest.mark.api
class TestRadioAPI:
    def test_channels_list(self, http_client):
        data = assert_dict_response(http_client.get("/radio/api/channels"))
        assert "channels" in data and isinstance(data["channels"], list)
        assert "personas" in data and isinstance(data["personas"], list)
        persona_ids = {p.get("id") for p in data["personas"]}
        assert {"lofi-night", "morning-energy"} & persona_ids

    def test_channels_active_persona(self, http_client):
        data = assert_dict_response(http_client.get("/radio/api/channels"))
        assert data.get("active_persona") in {"lofi-night", "morning-energy"}

    def test_now_endpoint(self, http_client):
        data = assert_ok(http_client.get("/radio/api/now"))
        assert isinstance(data, dict)

    def test_persona_swap(self, http_client):
        r = http_client.post("/radio/api/persona", json={"id": "morning-energy"})
        data = assert_dict_response(r)
        assert data.get("ok") is True
        assert data.get("active_persona") == "morning-energy"
        # restore
        http_client.post("/radio/api/persona", json={"id": "lofi-night"})

    def test_persona_unknown_rejected(self, http_client):
        r = http_client.post("/radio/api/persona", json={"id": "nope"})
        assert r.status_code == 400

    def test_channel_swap(self, http_client):
        r = http_client.post("/radio/api/channel", json={"id": "music"})
        data = assert_dict_response(r)
        assert data.get("ok") is True
        assert data.get("active_channel") == "music"

    def test_channel_unknown_rejected(self, http_client):
        r = http_client.post("/radio/api/channel", json={"id": "noisechannel"})
        assert r.status_code == 400

    def test_feedback_requires_action(self, http_client):
        r = http_client.post("/radio/api/feedback", json={"track_id": "song:foo.md"})
        assert r.status_code == 400

    def test_feedback_records_skip(self, http_client):
        r = http_client.post(
            "/radio/api/feedback",
            json={"track_id": "song:nonexistent.md", "action": "skip"},
        )
        data = assert_dict_response(r)
        assert data.get("ok") is True

    def test_audio_chatter_404_for_missing_key(self, http_client):
        r = http_client.get("/radio/api/audio/chatter/no-such-key")
        assert r.status_code == 404

    def test_kiosk_channels_filtered(self, http_client):
        """The /api/live/channels surface returns only public-allowed channels."""
        data = assert_dict_response(http_client.get("/radio/api/live/channels"))
        assert data.get("public") is True
        ids = {c.get("id") for c in data.get("channels", [])}
        # Built-in defaults: only "music" has public=True
        assert "devlog" not in ids
        assert "podcast" not in ids


@pytest.mark.api
class TestRadioConfig:
    """Channel + persona CRUD endpoints — the user-facing configuration surface."""

    def test_channels_config_get(self, http_client):
        data = assert_dict_response(http_client.get("/radio/api/channels-config"))
        assert "channels" in data and isinstance(data["channels"], list)
        ids = {c["id"] for c in data["channels"]}
        assert {"music", "devlog", "podcast"} <= ids

    def test_channels_config_put_relabel(self, http_client):
        get_r = assert_dict_response(http_client.get("/radio/api/channels-config"))
        rows = get_r["channels"]
        # Relabel music
        for r in rows:
            if r["id"] == "music":
                r["label"] = "Tunes"
        put_r = http_client.put("/radio/api/channels-config", json={"channels": rows})
        d = assert_dict_response(put_r)
        assert d.get("ok") is True
        # Verify it persisted
        check = assert_dict_response(http_client.get("/radio/api/channels-config"))
        music = next(c for c in check["channels"] if c["id"] == "music")
        assert music["label"] == "Tunes"
        # Restore
        for r in rows:
            if r["id"] == "music":
                r["label"] = "Music"
        http_client.put("/radio/api/channels-config", json={"channels": rows})

    def test_channels_config_unknown_id_dropped(self, http_client):
        get_r = assert_dict_response(http_client.get("/radio/api/channels-config"))
        rows = list(get_r["channels"]) + [{"id": "fakechan", "label": "Fake", "weight": 99, "enabled": True, "public": True}]
        put_r = assert_dict_response(http_client.put("/radio/api/channels-config", json={"channels": rows}))
        ids = {c["id"] for c in put_r["channels"]}
        assert "fakechan" not in ids

    def test_personas_list(self, http_client):
        data = assert_dict_response(http_client.get("/radio/api/personas"))
        ids = {p["id"] for p in data["personas"]}
        assert {"lofi-night", "morning-energy"} <= ids
        for p in data["personas"]:
            if p["id"] in {"lofi-night", "morning-energy"}:
                assert p["builtin"] is True

    def test_persona_get(self, http_client):
        data = assert_dict_response(http_client.get("/radio/api/personas/lofi-night"))
        assert data["id"] == "lofi-night"
        assert "system_prompt" in data
        assert "channel_weights" in data

    def test_persona_create_and_delete(self, http_client):
        payload = {
            "id": "test-dj",
            "name": "Test DJ",
            "voice": "af_sky",
            "language": "en",
            "public": False,
            "chatter_every": 4,
            "system_prompt": "You are a test DJ.",
            "public_system_prompt": "Public test DJ.",
            "channel_weights": {"music": 5, "chatter": 1},
            "mood_bias": ["calm"],
            "opening_template": "Hi.",
            "segue_template": "After {prev_title}, here's {next_title}.",
            "devlog_intro": "Note {date}.",
            "podcast_intro": "Episode.",
            "closing_template": "Bye.",
        }
        r = http_client.put("/radio/api/personas/test-dj", json=payload)
        d = assert_dict_response(r)
        assert d.get("ok") is True
        assert d["persona"]["id"] == "test-dj"
        assert d["persona"]["builtin"] is False
        # Verify it appears in list
        listing = assert_dict_response(http_client.get("/radio/api/personas"))
        assert any(p["id"] == "test-dj" for p in listing["personas"])
        # Delete it
        del_r = assert_dict_response(http_client.delete("/radio/api/personas/test-dj"))
        assert del_r.get("ok") is True
        # Gone
        check = http_client.get("/radio/api/personas/test-dj")
        assert check.status_code == 404

    def test_persona_invalid_id_rejected(self, http_client):
        r = http_client.put("/radio/api/personas/Bad ID!", json={"id": "Bad ID!", "name": "x"})
        assert r.status_code == 400

    def test_delete_builtin_rejected(self, http_client):
        # Built-in personas have no user override file → delete must 400
        r = http_client.delete("/radio/api/personas/lofi-night")
        assert r.status_code == 400

    def test_kiosk_devlog_forbidden(self, http_client):
        """Even guessing the live next URL with channel=devlog must return 403."""
        r = http_client.get("/radio/api/live/next?channel=devlog")
        assert r.status_code == 403

    def test_kiosk_audio_devlog_forbidden(self, http_client):
        """Direct devlog audio fetch in kiosk mode must be denied."""
        r = http_client.get("/radio/api/audio/devlog/2026-01-01?kiosk=1")
        assert r.status_code in (403, 404)


@pytest.mark.interactive
class TestRadioUI:
    def test_page_loads(self, page, base_url):
        page.goto(f"{base_url}/radio/")
        page.wait_for_selector("#audio", state="attached")
        assert page.locator("#title").is_visible()
        assert page.locator("#btn-play").is_visible()
        assert page.locator("#btn-skip").is_visible()
        assert page.locator("#btn-like").is_visible()

    def test_kiosk_strips_chrome(self, page, base_url):
        page.goto(f"{base_url}/radio/live")
        page.wait_for_selector("#audio", state="attached")
        # Banner shows
        banner = page.locator("#kiosk-banner")
        assert banner.is_visible()
        # Body has kiosk class
        cls = page.evaluate("document.body.className")
        assert "rd-kiosk" in cls

    def test_kiosk_query_flag(self, page, base_url):
        page.goto(f"{base_url}/radio/?kiosk=1")
        page.wait_for_selector("#audio", state="attached")
        cls = page.evaluate("document.body.className")
        assert "rd-kiosk" in cls
