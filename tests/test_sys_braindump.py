"""System app tests: Brain Dump — triggered (not ambient) thought-sorting pipeline.

The app is dark-default (feature flag off), so on a stock :9000 the pipeline path
returns {error:"disabled"} and these tests assert the gate contract + the page
shell. The full transcribe→summarize→extract→propose round-trip is verified on a
sandbox-pool member with the flag flipped (see the plan's verification section /
.claude/rules/sandbox-driven-testing.md). Tests skip cleanly when the app isn't
installed on the target daemon (new apps need a Store install).
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly

PREFIX = "/braindump"


def _installed(http_client) -> bool:
    return http_client.get(PREFIX + "/api/config").status_code == 200


@pytest.mark.api
class TestBrainDumpAPI:
    def test_config_envelope(self, http_client):
        if not _installed(http_client):
            pytest.skip("braindump not installed on this daemon")
        data = assert_dict_response(http_client.get(PREFIX + "/api/config"))
        assert "enabled" in data, f"config missing 'enabled': {list(data.keys())}"

    def test_config_enabled_is_bool(self, http_client):
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        data = http_client.get(PREFIX + "/api/config").json()
        assert isinstance(data["enabled"], bool), f"enabled not bool: {data['enabled']!r}"

    def test_start_text_respects_gate(self, http_client):
        """When the feature is off, /api/start refuses with a clean error and
        never runs the pipeline. The enabled path makes a live LLM call (slow,
        flaky in a smoke test) so it is verified on a sandbox member instead —
        see the plan's verification section."""
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        if http_client.get(PREFIX + "/api/config").json().get("enabled"):
            pytest.skip("feature enabled — live-LLM start path covered on the sandbox")
        data = http_client.post(
            PREFIX + "/api/start",
            data={"mode": "text", "text": "PLAYWRIGHT-TEST- call the electrician tomorrow"},
        ).json()
        assert data.get("error") == "disabled", f"expected disabled gate, got {data}"

    def test_start_text_empty_rejected(self, http_client):
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        enabled = http_client.get(PREFIX + "/api/config").json().get("enabled")
        if not enabled:
            pytest.skip("feature dark — empty-text path only reachable when enabled")
        data = http_client.post(PREFIX + "/api/start", data={"mode": "text", "text": "  "}).json()
        assert data.get("error") == "no text", f"empty text should be rejected: {data}"

    def test_discard_unknown_run(self, http_client):
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        data = http_client.post(PREFIX + "/api/runs/PLAYWRIGHT-TEST-nope/discard").json()
        assert data.get("error") == "unknown run", f"discard of unknown run: {data}"

    def test_continue_respects_gate(self, http_client):
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        enabled = http_client.get(PREFIX + "/api/config").json().get("enabled")
        if enabled:
            pytest.skip("gate-off path only; enabled continue is covered on the sandbox")
        data = http_client.post(PREFIX + "/api/runs/PLAYWRIGHT-TEST-x/continue", json={}).json()
        assert data.get("error") == "disabled", f"continue must honor the gate: {data}"

    def test_meeting_capabilities_envelope(self, http_client):
        """The Meeting surface probe always answers with a shape the UI can read,
        even when the meeting-capture plugin (or its soundcard dep) is absent —
        available=False just hides the tab, never 500s."""
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        data = assert_dict_response(http_client.get(PREFIX + "/api/meeting/capabilities"))
        assert "available" in data and isinstance(data["available"], bool)
        assert "sources" in data and isinstance(data["sources"], list)

    def test_meeting_start_respects_gate(self, http_client):
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        if http_client.get(PREFIX + "/api/config").json().get("enabled"):
            pytest.skip("feature enabled — live capture path covered on the sandbox")
        data = http_client.post(PREFIX + "/api/meeting/start", json={}).json()
        assert data.get("error") == "disabled", f"meeting start must honor the gate: {data}"

    def test_meeting_status_never_errors(self, http_client):
        """Status is a plain read — safe to poll whether or not a capture or the
        plugin exists; it reports recording=False rather than failing."""
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        data = assert_dict_response(http_client.get(PREFIX + "/api/meeting/status"))
        assert "recording" in data

    def test_review_gate_source_reachable(self, http_client):
        """Proposed items land in the rooms pending queue — that surface (which
        owns Apply/Reject) must exist for the extract stage to be meaningful."""
        if not _installed(http_client):
            pytest.skip("braindump not installed")
        assert http_client.get("/rooms/api/pending").status_code == 200


@pytest.mark.interactive
class TestBrainDumpUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        resp = page.goto(base_url + PREFIX + "/", wait_until="domcontentloaded", timeout=15000)
        if resp.status == 404:
            pytest.skip("braindump not installed")
        assert resp.status == 200
        wait_briefly(page, 1200)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_gate_matches_config(self, page, base_url, page_errors):
        resp = page.goto(base_url + PREFIX + "/", wait_until="domcontentloaded", timeout=15000)
        if resp.status == 404:
            pytest.skip("braindump not installed")
        wait_briefly(page, 1500)
        enabled = page.evaluate(
            "() => fetch('/braindump/api/config').then(r => r.json()).then(d => !!d.enabled)"
        )
        gate_shown = page.evaluate(
            "() => { var g = document.getElementById('gate');"
            " return !!g && !g.classList.contains('hidden'); }"
        )
        app_shown = page.evaluate(
            "() => { var a = document.getElementById('app');"
            " return !!a && !a.classList.contains('hidden'); }"
        )
        assert gate_shown == (not enabled), f"gate visibility {gate_shown} vs enabled {enabled}"
        assert app_shown == bool(enabled), f"app visibility {app_shown} vs enabled {enabled}"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_mode_toggle_present(self, page, base_url, page_errors):
        resp = page.goto(base_url + PREFIX + "/", wait_until="domcontentloaded", timeout=15000)
        if resp.status == 404:
            pytest.skip("braindump not installed")
        wait_briefly(page, 800)
        assert page.locator(".cs-mode[data-mode='text']").count() == 1
        assert page.locator(".cs-mode[data-mode='audio']").count() == 1
        assert page.locator("#start-capture").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
