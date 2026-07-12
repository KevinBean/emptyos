"""System app tests: Viz — single-file HTML artifact builder."""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok


@pytest.mark.api
class TestVizAPI:
    """API smoke — no LLM calls (those land on @pytest.mark.llm in a sibling suite)."""

    def test_list_returns_array(self, http_client):
        data = assert_list_response(http_client.get("/viz/api/list"))
        # Shape check on existing rows if any
        for row in data:
            assert "id" in row
            assert "shape" in row
            assert "prompt" in row

    def test_get_nonexistent_returns_error(self, http_client):
        r = http_client.get("/viz/api/get/nonexistent-id-xyz")
        data = assert_dict_response(r)
        assert data.get("ok") is False
        assert "no artifact" in data.get("error", "").lower()

    def test_iterate_nonexistent_returns_error(self, http_client):
        r = http_client.post("/viz/api/iterate", json={"id": "nonexistent", "prompt": TEST_PREFIX + "x"})
        data = assert_dict_response(r)
        assert data.get("ok") is False

    def test_generate_rejects_empty_prompt(self, http_client):
        r = http_client.post("/viz/api/generate", json={"prompt": ""})
        data = assert_dict_response(r)
        assert data.get("ok") is False
        assert "prompt" in data.get("error", "").lower()

    def test_generate_rejects_unknown_shape(self, http_client):
        r = http_client.post("/viz/api/generate", json={"prompt": TEST_PREFIX + "test", "shape": "not-a-shape"})
        data = assert_dict_response(r)
        assert data.get("ok") is False
        assert "shape" in data.get("error", "").lower()

    def test_iterate_rejects_missing_id(self, http_client):
        r = http_client.post("/viz/api/iterate", json={"prompt": "tweak"})
        data = assert_dict_response(r)
        assert data.get("ok") is False

    def test_rendered_event_endpoint(self, http_client):
        # Fires event without persisting state — safe to call with any id.
        r = http_client.post("/viz/api/rendered", json={"id": "any-id"})
        data = assert_dict_response(r)
        assert data.get("ok") is True

    def test_html_endpoint_404_when_missing(self, http_client):
        r = http_client.get("/viz/api/html/nonexistent-xyz")
        assert r.status_code == 404

    def test_delete_nonexistent_returns_error(self, http_client):
        r = http_client.delete("/viz/api/items/nonexistent-xyz")
        data = assert_dict_response(r)
        assert data.get("ok") is False

    def test_game_2d_shape_registered(self, http_client):
        """game-2d is offered, gated as a strong shape, and excluded from
        element-edit (it renders to a <canvas>)."""
        data = assert_dict_response(http_client.get("/viz/api/shapes"))
        shapes = {s["shape"]: s for s in data.get("shapes", [])}
        assert "game-2d" in shapes, "game-2d shape not registered"
        g = shapes["game-2d"]
        assert g["min_ability"] == "strong"
        assert g["supports_edit"] is False

    def test_examples_endpoint_returns_items(self, http_client):
        """KB pattern-note few-shot picker should list available `kind: pattern` notes."""
        r = http_client.get("/viz/api/examples")
        data = assert_dict_response(r)
        assert "items" in data
        assert isinstance(data["items"], list)
        # Each row, if present, has slug + title fields.
        for it in data["items"]:
            assert "slug" in it
            assert "title" in it

    def test_shapes_endpoint_lists_immersive_and_3d(self, http_client):
        """`/api/shapes` drives the picker + ability gate; both 3D shapes must
        be present and gated to a strong model."""
        data = assert_dict_response(http_client.get("/viz/api/shapes"))
        shapes = {s["shape"]: s for s in data.get("shapes", [])}
        assert "3d-scene" in shapes
        assert "immersive-scene" in shapes
        assert shapes["immersive-scene"]["min_ability"] == "strong"
        assert shapes["immersive-scene"]["label"]

    def test_generate_stream_rejects_empty_prompt(self, http_client):
        """Streaming endpoint should yield an error event for empty prompt without spawning LLM."""
        r = http_client.post("/viz/api/generate-stream", json={"prompt": ""})
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/x-ndjson")
        # NDJSON body — last line should be the error event.
        import json
        events = [json.loads(line) for line in r.text.strip().split("\n") if line.strip()]
        assert any(e.get("type") == "error" for e in events)

    def test_generate_stream_rejects_unknown_shape(self, http_client):
        r = http_client.post(
            "/viz/api/generate-stream",
            json={"prompt": TEST_PREFIX + "x", "shape": "not-a-shape"},
        )
        assert r.status_code == 200
        import json
        events = [json.loads(line) for line in r.text.strip().split("\n") if line.strip()]
        err = next((e for e in events if e.get("type") == "error"), None)
        assert err is not None
        assert "unknown shape" in err.get("error", "").lower()

    def test_iterate_stream_rejects_missing_id(self, http_client):
        r = http_client.post("/viz/api/iterate-stream", json={"prompt": "x"})
        assert r.status_code == 200
        import json
        events = [json.loads(line) for line in r.text.strip().split("\n") if line.strip()]
        assert any(e.get("type") == "error" for e in events)

    def test_iterate_stream_rejects_nonexistent_id(self, http_client):
        r = http_client.post(
            "/viz/api/iterate-stream",
            json={"id": "nonexistent-xyz", "prompt": TEST_PREFIX + "tweak"},
        )
        assert r.status_code == 200
        import json
        events = [json.loads(line) for line in r.text.strip().split("\n") if line.strip()]
        err = next((e for e in events if e.get("type") == "error"), None)
        assert err is not None
        assert "no artifact" in err.get("error", "").lower()


@pytest.mark.api
class TestThinkEffective:
    """Smoke for the shared model-pill backend (`/api/capabilities/think/effective`)."""

    def test_returns_effective_provider(self, http_client):
        r = http_client.get("/api/capabilities/think/effective?app=viz&domain=code")
        data = assert_dict_response(r)
        # Shape contract: pill consumes provider + source + chain + providers.
        assert "provider" in data
        assert "source" in data
        assert "chain" in data and isinstance(data["chain"], list)
        assert "providers" in data and isinstance(data["providers"], list)
        # When the daemon has any think provider, chain should not be empty.
        assert len(data["chain"]) >= 1
        # Each listed provider has the fields the pill reads.
        for p in data["providers"]:
            assert "name" in p
            assert "available" in p

    def test_handles_unknown_app(self, http_client):
        # Pill mounts before the user has ever pinned anything — should still
        # resolve a provider (via domain override if set, else the chain).
        # The app override path is absent (no `think.app.does-not-exist` key)
        # so `source` must be one of {chain, override-domain}.
        r = http_client.get("/api/capabilities/think/effective?app=does-not-exist&domain=code")
        data = assert_dict_response(r)
        assert data.get("source") in ("chain", "override-domain")
        assert data.get("provider"), "must resolve to some provider"


@pytest.mark.interactive
class TestVizUI:
    """UI smoke — no LLM generation (would be a separate @pytest.mark.llm suite)."""

    def test_page_loads(self, page, base_url):
        page.goto(base_url + "/viz/")
        page.wait_for_load_state("networkidle")
        assert page.locator("h1").first.is_visible()

    def test_shape_selector_present(self, page, base_url):
        page.goto(base_url + "/viz/")
        page.wait_for_load_state("networkidle")
        assert page.locator("select#shape").is_visible()
        options = page.locator("select#shape option").all_text_contents()
        assert any("3D scene" in o for o in options)
        assert any("Immersive" in o for o in options)
        assert any("SVG" in o for o in options)
        assert any("Chart" in o for o in options)

    def test_prompt_textarea_focusable(self, page, base_url):
        page.goto(base_url + "/viz/")
        page.wait_for_load_state("networkidle")
        page.locator("textarea#prompt").click()
        page.keyboard.type(TEST_PREFIX + "test brief")
        assert TEST_PREFIX in page.locator("textarea#prompt").input_value()

    def test_settings_panel_opens(self, page, base_url):
        page.goto(base_url + "/viz/")
        page.wait_for_load_state("networkidle")
        page.locator("button.btn-settings").click()
        # Settings panel should be visible after click
        page.wait_for_selector("#viz-settings-panel", state="visible", timeout=2000)

    def test_iframe_hidden_on_first_load(self, page, base_url):
        page.goto(base_url + "/viz/")
        page.wait_for_load_state("networkidle")
        # Empty state visible, preview iframe hidden
        assert page.locator("#empty").is_visible()
        assert page.locator("#preview").is_hidden()


def _viz_embed_enabled(http_client) -> bool:
    """Live probe of [apps.viz] feature.artifact-embed.enabled on the daemon
    under test — the flag-off tests and TestVizEmbedDurable split on it."""
    try:
        return bool(http_client.get("/viz/api/shapes").json().get("embed_enabled"))
    except Exception:
        return False


@pytest.mark.api
class TestVizEmbed:
    """Durable artifact embeds — flag-off regression + serve guards.

    The flag-on durability path (bake → serve survives source delete, reference
    delete gate) lives in TestVizEmbedDurable, which self-skips when the feature
    is disabled (the dark default) — see .claude/rules/sandbox-driven-testing.md.
    The two *_disabled_by_default tests are the mirror image: they assert the
    dark default and skip on a daemon whose operator flipped the flag on."""

    def test_bake_disabled_by_default(self, http_client):
        if _viz_embed_enabled(http_client):
            pytest.skip("artifact-embed enabled on this daemon — flag-on path covered by TestVizEmbedDurable")
        r = http_client.post("/viz/api/embed/bake", json={"viz_id": "x", "mode": "snapshot"})
        data = assert_dict_response(r)
        assert data.get("ok") is False
        assert "disabled" in data.get("error", "").lower()

    def test_serve_404_when_missing(self, http_client):
        r = http_client.get("/viz/api/embed/nonexistent-embed-xyz")
        assert r.status_code == 404

    def test_serve_rejects_path_traversal(self, http_client):
        # An id that would escape the embeds dir must never resolve to a file.
        r = http_client.get("/viz/api/embed/..%2F..%2Femptyos.toml")
        assert r.status_code == 404

    def test_refs_endpoint_shape(self, http_client):
        # Reverse-index endpoint always answers (empty when nothing references it).
        data = assert_dict_response(http_client.get("/viz/api/embed/refs/nonexistent-id"))
        assert data.get("ok") is True
        assert isinstance(data.get("notes"), list)

    def test_resync_propose_disabled_by_default(self, http_client):
        if _viz_embed_enabled(http_client):
            pytest.skip("artifact-embed enabled on this daemon — flag-on path covered by TestVizEmbedDurable")
        r = http_client.post("/viz/api/embed/resync/propose", json={"embed_id": "x"})
        data = assert_dict_response(r)
        assert data.get("ok") is False


@pytest.mark.api
class TestVizEmbedDurable:
    """Flag-on end-to-end (run on a sandbox member with the feature enabled)."""

    def _enabled(self, http_client) -> bool:
        return _viz_embed_enabled(http_client)

    def test_snapshot_survives_source_delete(self, http_client):
        if not self._enabled(http_client):
            pytest.skip("artifact-embed feature disabled (default on :9000)")
        # This test DELETES its source artifact, so it must own it: only a
        # TEST_PREFIX-prompted artifact qualifies (seed one via /viz/api/generate
        # on a sandbox member — see .claude/rules/sandbox-driven-testing.md).
        # Harvesting the user's newest real artifact here cost two artifacts on
        # :9000 (2026-07-03) once the flag was flipped on locally.
        rows = http_client.get("/viz/api/list").json()
        owned = [r for r in rows if (r.get("prompt") or "").startswith(TEST_PREFIX)]
        if not owned:
            pytest.skip("no TEST_PREFIX-owned viz artifact to delete (seed one on a sandbox member)")
        viz_id = owned[0]["id"]
        baked = http_client.post("/viz/api/embed/bake",
                                 json={"viz_id": viz_id, "mode": "snapshot"}).json()
        assert baked.get("ok"), baked
        embed_id = baked["embed_id"]
        # Snapshot serves the frozen bytes...
        assert http_client.get(f"/viz/api/embed/{embed_id}").status_code == 200
        # ...and KEEPS serving after the source artifact is deleted (the headline).
        http_client.delete(f"/viz/api/items/{viz_id}?force=1")
        assert http_client.get(f"/viz/api/embed/{embed_id}").status_code == 200
