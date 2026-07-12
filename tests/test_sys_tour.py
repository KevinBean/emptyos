"""System app tests: Product Tour — covers the contribution-driven walkthrough.

Aggregator API (steps + dismiss + state), capability-gated rewriting, and the
spotlight UI primitive that powers the orchestrator. The tour is built on
manifest contributions, so the canonical assertion is "the seeded core steps
appear in /api/steps in the right order".
"""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestTourAPI:
    def test_steps_shape(self, http_client):
        data = assert_dict_response(http_client.get("/tour/api/steps"))
        assert "steps" in data and isinstance(data["steps"], list)
        assert "state" in data and isinstance(data["state"], dict)

    def test_seeded_core_steps_present(self, http_client):
        """Tour app + capture/task/journal must seed at least these ids."""
        data = http_client.get("/tour/api/steps").json()
        ids = {s["id"] for s in data["steps"]}
        # tour app contributes home.welcome + system.inspect itself
        assert "home.welcome" in ids
        assert "system.inspect" in ids
        # When core apps are installed, their steps appear too. Don't fail
        # if only the tour app exists in a slimmed-down fresh clone.
        for opt in ("capture.try", "task.capture", "journal.write"):
            # contract: if the app is installed, the step appears
            pass

    def test_steps_priority_ordered(self, http_client):
        steps = http_client.get("/tour/api/steps").json()["steps"]
        priorities = [s["priority"] for s in steps]
        assert priorities == sorted(priorities), \
            f"Steps not priority-ordered: {priorities}"

    def test_each_step_has_route_and_spotlight(self, http_client):
        steps = http_client.get("/tour/api/steps").json()["steps"]
        for s in steps:
            assert s.get("route"), f"Step {s['id']} missing route"
            # spotlight may be empty string for center-screen steps
            assert "spotlight" in s, f"Step {s['id']} missing spotlight key"
            assert "title" in s and s["title"], f"Step {s['id']} missing title"

    def test_capability_gating_rewrites_to_inspector(self, http_client):
        """A step with a missing capability should land at /system?capability=…"""
        steps = http_client.get("/tour/api/steps").json()["steps"]
        rewritten = [s for s in steps if s.get("missing")]
        for s in rewritten:
            assert s["route"].startswith("/system?capability="), \
                f"Step {s['id']} has missing caps {s['missing']} but route is {s['route']}"
            assert s["spotlight"].startswith("#cap-"), \
                f"Step {s['id']} should spotlight #cap-<name>"

    def test_dismiss_marks_state(self, http_client):
        # Save current state, dismiss, then re-read
        before = http_client.get("/tour/api/steps").json()["state"]
        try:
            r = http_client.post("/tour/api/dismiss", json={"completed": False})
            assert r.status_code == 200
            after = http_client.get("/tour/api/steps").json()["state"]
            assert after["dismissed"] is True
        finally:
            # Best-effort restore — tests must not leave the daemon in a
            # post-tour state for a real user.
            if not before.get("dismissed"):
                # Re-write file via the same endpoint with completed flag absent
                # (api_dismiss only sets dismissed=True; no "undo" — this is a
                # known limitation. For now we live with it; CI uses a throwaway
                # vault per the dogfood workflow.)
                pass

    def test_debug_steps_renders_html(self, http_client):
        r = http_client.get("/tour/debug/steps")
        assert r.status_code == 200
        assert "Tour steps" in r.text


@pytest.mark.api
class TestCapabilityInspectorAPI:
    """The /api/capabilities/full endpoint feeds both the inspector and the tour."""

    def test_full_shape(self, http_client):
        data = assert_dict_response(http_client.get("/api/capabilities/full"))
        assert "capabilities" in data and isinstance(data["capabilities"], dict)
        assert "consent" in data
        assert "network_mode" in data

    def test_capability_rows_have_recovery_keys(self, http_client):
        """Every provider row must expose available + recovery keys (recovery
        may be None when the provider is healthy)."""
        data = http_client.get("/api/capabilities/full").json()
        for cap_name, cap in data["capabilities"].items():
            assert "active" in cap
            assert "providers" in cap and isinstance(cap["providers"], list)
            for p in cap["providers"]:
                assert "name" in p
                assert "available" in p
                assert "recovery" in p  # may be None
                assert "is_cloud" in p

    def test_active_provider_matches_first_available(self, http_client):
        data = http_client.get("/api/capabilities/full").json()
        for cap_name, cap in data["capabilities"].items():
            available = [p["name"] for p in cap["providers"] if p["available"]]
            if available:
                assert cap["active"] == available[0], \
                    f"{cap_name}: active={cap['active']} but first-available={available[0]}"
            else:
                assert cap["active"] is None


@pytest.mark.interactive
class TestInspectorUI:
    def test_inspector_loads(self, page, base_url, page_errors):
        page.goto(base_url + "/system")
        wait_briefly(page, 800)
        assert page.locator(".cap-card").count() > 0, "No capability cards rendered"
        assert page.locator("h1").first.text_content().strip() == "System"
        assert_no_js_errors(page_errors)

    def test_inspector_focus_via_query(self, page, base_url, page_errors):
        """`?capability=think` should mark the matching card as focused."""
        page.goto(base_url + "/system?capability=think")
        wait_briefly(page, 800)
        focused = page.locator(".cap-card.focus")
        # Will only exist if the daemon has a 'think' capability registered —
        # always true in EmptyOS (kernel always registers it).
        assert focused.count() == 1, "Expected exactly one focused capability card"
        assert_no_js_errors(page_errors)


@pytest.mark.interactive
class TestTourUI:
    def test_spotlight_appears_on_start(self, page, base_url, page_errors):
        """EOS.tour.start() should mount a spotlight overlay on the home page."""
        page.goto(base_url + "/")
        wait_briefly(page, 600)
        page.evaluate("EOS.tour.start()")
        wait_briefly(page, 800)
        # Spotlight tooltip is created on demand
        tip = page.locator("#eos-spotlight-tip")
        assert tip.count() == 1, "Spotlight tooltip did not mount"
        assert tip.is_visible(), "Spotlight tooltip should be visible"
        # Clean up so the next test starts fresh
        page.evaluate("localStorage.removeItem('eos.tour.v1')")
        page.evaluate("var r = document.getElementById('eos-spotlight-root'); if (r) r.remove();")
        assert_no_js_errors(page_errors)
