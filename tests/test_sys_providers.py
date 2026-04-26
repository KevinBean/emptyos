"""System tests for the Providers app — seeded table, consent bar, LLM-scan bar.

The API surface for the consent endpoints themselves is covered in
`test_sys_cloud_consent.py`. This file targets the pieces that ship with the
Providers UI specifically:

  - Seeded providers endpoint (`/providers/api/providers/seeded`)
  - LLM-scan config endpoint (`/api/cloud/llm-scan`)
  - The three-button consent bar UI round-trip (ask / always / never)
  - The three-button LLM-scan mode bar UI round-trip (off / classify / redact)

Each UI test that mutates daemon state snapshots the original value and restores
it in a `finally` block so the suite is re-runnable and order-independent.
"""

from __future__ import annotations

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly, wait_for_toast


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.mark.api
class TestProvidersAPI:
    def test_user_providers_shape(self, http_client):
        data = assert_dict_response(http_client.get("/providers/api/providers"))
        assert "providers" in data
        assert isinstance(data["providers"], list)

    def test_seeded_providers_shape(self, http_client):
        """Seeded view exposes providers declared in emptyos.toml `[capabilities.think]`."""
        data = assert_dict_response(http_client.get("/providers/api/providers/seeded"))
        assert "providers" in data
        assert isinstance(data["providers"], list)
        for row in data["providers"]:
            assert isinstance(row, dict)
            # Rows render through _row() — at minimum id + provider
            assert "id" in row

    def test_llm_scan_config_shape(self, http_client):
        data = assert_dict_response(http_client.get("/api/cloud/llm-scan"))
        assert "config" in data and isinstance(data["config"], dict)
        cfg = data["config"]
        assert cfg.get("mode") in ("off", "classify", "redact")
        assert cfg.get("on_flag") in ("warn", "block")
        # providers is an optional list of local think provider variant_ids
        if "providers" in data:
            assert isinstance(data["providers"], list)

    def test_llm_scan_set_and_restore(self, http_client):
        """Flip mode=classify, verify persistence, restore original."""
        original = http_client.get("/api/cloud/llm-scan").json().get("config", {})
        try:
            # POST returns {ok: true}; the authoritative state lives in GET
            assert_ok(http_client.post("/api/cloud/llm-scan", json={"mode": "classify"}))
            after = http_client.get("/api/cloud/llm-scan").json()
            assert after["config"]["mode"] == "classify"
        finally:
            http_client.post(
                "/api/cloud/llm-scan",
                json={k: original.get(k) for k in ("mode", "on_flag", "provider", "max_chars") if k in original},
            )

    def test_llm_scan_invalid_mode_rejected(self, http_client):
        resp = http_client.post("/api/cloud/llm-scan", json={"mode": "shred"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# UI — Consent bar (the three-button policy round-trip)
# ---------------------------------------------------------------------------

@pytest.mark.interactive
class TestProvidersConsentBarUI:
    @pytest.fixture(autouse=True)
    def _restore_policy(self, http_client):
        """Snapshot policy and restore after each test so the suite is re-runnable."""
        original = http_client.get("/api/cloud/status").json().get("policy", "ask")
        yield
        http_client.post("/api/cloud/policy", json={"policy": original})

    def test_page_renders_consent_bar(self, app_page, page_errors):
        page = app_page("providers")
        wait_briefly(page, 400)
        assert page.locator("#consent-bar").count() == 1
        # Three policy buttons
        btns = page.locator("#consent-bar .consent-btn")
        assert btns.count() == 3
        for policy in ("ask", "always", "never"):
            assert page.locator(f"#consent-bar .consent-btn[data-policy='{policy}']").count() == 1
        assert_no_js_errors(page_errors)

    def test_initial_active_button_matches_policy(self, app_page, http_client, page_errors):
        """The button whose data-policy matches GET /api/cloud/status should be .active."""
        policy = http_client.get("/api/cloud/status").json().get("policy", "ask")
        page = app_page("providers")
        # loadConsent() fires after page ready; give it a moment
        wait_briefly(page, 800)
        active = page.locator("#consent-bar .consent-btn.active")
        assert active.count() == 1, f"expected exactly 1 active consent button, got {active.count()}"
        assert active.first.get_attribute("data-policy") == policy
        assert_no_js_errors(page_errors)

    def test_click_always_roundtrips(self, app_page, http_client, page_errors):
        """Click Always allow → toast appears → API reports 'always' → UI reflects it."""
        page = app_page("providers")
        wait_briefly(page, 600)
        page.locator("#consent-bar .consent-btn[data-policy='always']").click()
        toast = wait_for_toast(page, expected_substring="always", timeout=3000)
        assert toast, "expected consent toast after click"
        # Server state
        assert http_client.get("/api/cloud/status").json().get("policy") == "always"
        # UI state
        wait_briefly(page, 400)
        active = page.locator("#consent-bar .consent-btn.active")
        assert active.count() == 1
        assert active.first.get_attribute("data-policy") == "always"
        assert_no_js_errors(page_errors)

    def test_click_never_roundtrips(self, app_page, http_client, page_errors):
        page = app_page("providers")
        wait_briefly(page, 600)
        page.locator("#consent-bar .consent-btn[data-policy='never']").click()
        wait_for_toast(page, expected_substring="never", timeout=3000)
        assert http_client.get("/api/cloud/status").json().get("policy") == "never"
        wait_briefly(page, 400)
        assert (
            page.locator("#consent-bar .consent-btn.active").first.get_attribute("data-policy")
            == "never"
        )
        assert_no_js_errors(page_errors)

    def test_click_ask_roundtrips(self, app_page, http_client, page_errors):
        page = app_page("providers")
        wait_briefly(page, 600)
        # Start somewhere else so the Ask click is observable
        http_client.post("/api/cloud/policy", json={"policy": "always"})
        wait_briefly(page, 400)
        page.locator("#consent-bar .consent-btn[data-policy='ask']").click()
        wait_for_toast(page, expected_substring="ask", timeout=3000)
        assert http_client.get("/api/cloud/status").json().get("policy") == "ask"
        wait_briefly(page, 400)
        assert (
            page.locator("#consent-bar .consent-btn.active").first.get_attribute("data-policy")
            == "ask"
        )
        assert_no_js_errors(page_errors)

    def test_hint_text_updates_after_click(self, app_page, page_errors):
        """Hint copy changes per policy — catches broken _consentHints lookup."""
        page = app_page("providers")
        wait_briefly(page, 600)

        def _wait_for_hint_to_contain(keyword: str):
            page.wait_for_function(
                "kw => (document.getElementById('consent-hint').textContent || '').toLowerCase().includes(kw)",
                arg=keyword,
                timeout=4000,
            )

        page.locator("#consent-bar .consent-btn[data-policy='always']").click()
        _wait_for_hint_to_contain("without prompting")
        hint_always = (page.locator("#consent-hint").text_content() or "").strip().lower()

        page.locator("#consent-bar .consent-btn[data-policy='never']").click()
        _wait_for_hint_to_contain("skipped")
        hint_never = (page.locator("#consent-hint").text_content() or "").strip().lower()

        assert hint_always and hint_never
        assert hint_always != hint_never, "hint copy must differ across policies"
        assert_no_js_errors(page_errors)


# ---------------------------------------------------------------------------
# UI — LLM scan bar (same three-button pattern, different slot)
# ---------------------------------------------------------------------------

@pytest.mark.interactive
class TestProvidersLlmScanBarUI:
    @pytest.fixture(autouse=True)
    def _restore_scan(self, http_client):
        original = http_client.get("/api/cloud/llm-scan").json().get("config", {})
        yield
        http_client.post(
            "/api/cloud/llm-scan",
            json={k: original.get(k) for k in ("mode", "on_flag", "provider", "max_chars") if k in original},
        )

    def test_page_renders_llm_scan_bar(self, app_page, page_errors):
        page = app_page("providers")
        wait_briefly(page, 400)
        assert page.locator("#llm-scan-bar").count() == 1
        btns = page.locator("#llm-scan-bar .consent-btn")
        assert btns.count() == 3
        for mode in ("off", "classify", "redact"):
            assert page.locator(f"#llm-scan-bar .consent-btn[data-scan-mode='{mode}']").count() == 1
        assert_no_js_errors(page_errors)

    def test_click_classify_roundtrips(self, app_page, http_client, page_errors):
        page = app_page("providers")
        wait_briefly(page, 600)
        page.locator("#llm-scan-bar .consent-btn[data-scan-mode='classify']").click()
        wait_briefly(page, 600)
        assert http_client.get("/api/cloud/llm-scan").json()["config"]["mode"] == "classify"
        active = page.locator("#llm-scan-bar .consent-btn.active")
        assert active.count() == 1
        assert active.first.get_attribute("data-scan-mode") == "classify"
        assert_no_js_errors(page_errors)
