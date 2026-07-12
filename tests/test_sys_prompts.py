"""System tests for the prompts app — the prompt-management registry surface.

Covers: registry listing (with lazy module import via ?load=1), detail fetch,
validated override set/reset roundtrip (restored afterwards so the test never
leaves a real override behind), drift sweep envelope, and the read-only
personas aggregation. UI smoke: page loads without JS errors.
"""

import pytest


# A prompt guaranteed to be registered — the agent app adopted declare_prompts.
KNOWN_KEY = "agent.classify_system"


@pytest.mark.api
class TestPromptsAPI:
    def test_list_registers_declaring_apps(self, http_client):
        resp = http_client.get("/prompts/api/prompts?load=1")
        assert resp.status_code == 200
        data = resp.json()
        app_ids = {g["app_id"] for g in data.get("apps", [])}
        # The three reference adopters must all appear after a forced load.
        for expected in ("agent", "publish", "voice-assistant"):
            assert expected in app_ids, f"{expected} missing from registry"
        assert expected in data.get("declaring_apps", [])
        # Row shape
        row = data["apps"][0]["prompts"][0]
        for k in ("key", "name", "constant", "preview", "placeholders", "overridden"):
            assert k in row

    def test_detail_shape(self, http_client):
        http_client.get("/prompts/api/prompts?load=1")
        resp = http_client.get(f"/prompts/api/prompts/{KNOWN_KEY}")
        assert resp.status_code == 200
        d = resp.json()
        assert d["key"] == KNOWN_KEY
        assert d["constant"] == "CLASSIFY_SYSTEM"
        assert "task classifier" in d["default"]

    def test_detail_unknown_key(self, http_client):
        resp = http_client.get("/prompts/api/prompts/nope.missing")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_override_set_resolve_reset_roundtrip(self, http_client):
        http_client.get("/prompts/api/prompts?load=1")
        # Preserve any pre-existing override so the test is non-destructive.
        before = http_client.get(f"/prompts/api/prompts/{KNOWN_KEY}").json()
        prior = before.get("override")
        marker = "PLAYWRIGHT-TEST override: classify tasks, output only JSON."
        try:
            r = http_client.put(
                f"/prompts/api/prompts/{KNOWN_KEY}/override",
                json={"text": marker, "note": "sys-test"},
            )
            assert r.status_code == 200 and r.json().get("ok") is True

            d = http_client.get(f"/prompts/api/prompts/{KNOWN_KEY}").json()
            assert d["override"]["text"] == marker
            assert d["override"]["active"] is True

            # Listing shows the overridden badge flag.
            data = http_client.get("/prompts/api/prompts").json()
            agent_rows = next(g["prompts"] for g in data["apps"] if g["app_id"] == "agent")
            row = next(p for p in agent_rows if p["key"] == KNOWN_KEY)
            assert row["overridden"] is True and row["stale"] is False

            r = http_client.delete(f"/prompts/api/prompts/{KNOWN_KEY}/override")
            assert r.status_code == 200 and r.json().get("existed") is True
            d = http_client.get(f"/prompts/api/prompts/{KNOWN_KEY}").json()
            assert d["override"] is None
        finally:
            # Restore whatever was there before the test.
            http_client.delete(f"/prompts/api/prompts/{KNOWN_KEY}/override")
            if prior:
                http_client.put(
                    f"/prompts/api/prompts/{KNOWN_KEY}/override",
                    json={"text": prior["text"], "note": prior.get("note", "")},
                )

    def test_override_placeholder_mismatch_rejected(self, http_client):
        http_client.get("/prompts/api/prompts?load=1")
        # classify_prompt is a real template ({user_text}); an override without
        # the placeholder must be rejected with the expected list.
        r = http_client.put(
            "/prompts/api/prompts/agent.classify_prompt/override",
            json={"text": "no placeholders here"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is False
        assert "user_text" in (body.get("expected") or [])

    def test_override_unregistered_key_rejected(self, http_client):
        r = http_client.put(
            "/prompts/api/prompts/ghost.nothing/override",
            json={"text": "hello"},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is False

    def test_sweep_envelope(self, http_client):
        resp = http_client.get("/prompts/api/sweep")
        assert resp.status_code == 200
        rep = resp.json()
        for k in ("orphans", "stale", "corrupt", "registered", "overridden"):
            assert k in rep
        assert rep["registered"] > 0

    def test_personas_degrades_gracefully(self, http_client):
        resp = http_client.get("/prompts/api/personas")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("personas"), list)
        assert isinstance(data.get("skipped"), list)
        for p in data["personas"]:
            assert p["source"] in ("rooms", "staff", "voice-assistant")
            assert "edit_url" in p


@pytest.mark.interactive
class TestPromptsUI:
    def test_page_loads_and_lists(self, page, base_url):
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base_url}/prompts/")
        page.wait_for_selector(".pr-item", timeout=15000)
        assert page.locator(".pr-item").count() > 0
        assert not errors, f"JS errors: {errors}"

    def test_detail_opens_on_click(self, page, base_url):
        page.goto(f"{base_url}/prompts/")
        page.wait_for_selector(".pr-item", timeout=15000)
        page.locator(".pr-item").first.click()
        page.wait_for_selector(".pr-detail-card", timeout=10000)
        assert page.locator(".pr-default").count() == 1
        # Hash route is set for bookmarkability.
        assert "#" in page.url
