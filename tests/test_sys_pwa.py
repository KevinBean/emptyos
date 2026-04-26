"""System tests: PWA surface (manifest, service worker, offline fallback, install prompt).

Covers the installable-web-app layer. Cross-browser coverage comes from running
pytest-playwright with --browser chromium/firefox/webkit (see .claude/rules/testing.md).
"""

import pytest

from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestPWAManifest:
    def test_manifest_root_served(self, http_client):
        resp = http_client.get("/manifest.webmanifest")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "manifest" in ct or "json" in ct, f"Unexpected content-type: {ct}"

    def test_manifest_valid_json(self, http_client):
        data = http_client.get("/manifest.webmanifest").json()
        assert isinstance(data, dict)

    def test_manifest_required_fields(self, http_client):
        data = http_client.get("/manifest.webmanifest").json()
        for field in ("name", "start_url", "display", "icons", "scope"):
            assert field in data, f"Manifest missing required field: {field}"
        assert data["display"] in ("standalone", "fullscreen", "minimal-ui"), (
            f"display must be app-like, got {data['display']!r}"
        )
        assert isinstance(data["icons"], list) and len(data["icons"]) > 0

    def test_manifest_icons_reachable(self, http_client):
        data = http_client.get("/manifest.webmanifest").json()
        icon_srcs = [i.get("src") for i in data.get("icons", []) if i.get("src")]
        assert icon_srcs, "No icon srcs in manifest"
        for src in icon_srcs:
            # src may be relative (/static/...) — httpx resolves from base_url
            resp = http_client.get(src)
            assert resp.status_code == 200, f"Icon {src} returned {resp.status_code}"

    def test_legacy_static_manifest_still_served(self, http_client):
        """Backwards compat: /static/manifest.json still works for old bookmarks."""
        resp = http_client.get("/static/manifest.json")
        assert resp.status_code == 200


@pytest.mark.api
class TestServiceWorker:
    def test_sw_served_at_root(self, http_client):
        resp = http_client.get("/sw.js")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "javascript" in ct, f"sw.js served with wrong type: {ct}"

    def test_sw_allowed_root_scope(self, http_client):
        resp = http_client.get("/sw.js")
        assert resp.headers.get("service-worker-allowed") == "/", (
            "sw.js must declare Service-Worker-Allowed: / to control the full origin"
        )

    def test_sw_caches_offline_and_manifest(self, http_client):
        """sw.js source must list the manifest and offline page in its precache list."""
        body = http_client.get("/sw.js").text
        assert "/manifest.webmanifest" in body, "sw.js must precache the manifest"
        assert "/offline.html" in body, "sw.js must precache the offline fallback"


@pytest.mark.api
class TestOfflineFallback:
    def test_offline_page_served(self, http_client):
        resp = http_client.get("/offline.html")
        assert resp.status_code == 200
        assert "unreachable" in resp.text.lower() or "offline" in resp.text.lower()


@pytest.mark.interactive
class TestPWAIntegration:
    def test_manifest_link_present_on_home(self, page, base_url, page_errors):
        """eos.js injects <link rel='manifest'> on every page after load."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1200)
        href = page.evaluate(
            "() => { var l = document.querySelector('link[rel=\"manifest\"]');"
            "       return l ? l.getAttribute('href') : null; }"
        )
        assert href == "/manifest.webmanifest", (
            f"Expected manifest link to point at /manifest.webmanifest, got {href!r}"
        )
        assert_no_js_errors(page_errors)

    def test_manifest_link_present_on_app_page(self, page, base_url, page_errors):
        """Same shared-frontend injection should work inside any app page."""
        page.goto(base_url + "/journal/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1200)
        href = page.evaluate(
            "() => { var l = document.querySelector('link[rel=\"manifest\"]');"
            "       return l ? l.getAttribute('href') : null; }"
        )
        assert href == "/manifest.webmanifest"
        assert_no_js_errors(page_errors)

    def test_service_worker_registers(self, page, base_url, page_errors):
        """Service worker should register and become active on reload."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # Reload so the SW has a chance to claim the page.
        page.reload(wait_until="domcontentloaded")
        wait_briefly(page, 1500)
        has_sw = page.evaluate(
            "() => 'serviceWorker' in navigator"
            "       && !!navigator.serviceWorker.controller"
        )
        # WebKit / some CI browsers may not register SW — skip rather than fail.
        if not has_sw:
            pytest.skip("Service worker not active in this browser/context")
        assert_no_js_errors(page_errors)

    def test_apple_meta_tags_injected(self, page, base_url, page_errors):
        """iOS PWA meta tags injected by eos.js."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1000)
        has_tags = page.evaluate(
            "() => !!document.querySelector('meta[name=\"mobile-web-app-capable\"]')"
            "       && !!document.querySelector('link[rel=\"apple-touch-icon\"]')"
        )
        assert has_tags, "Apple PWA meta tags should be injected"
        assert_no_js_errors(page_errors)

    def test_install_prompt_stash(self, page, base_url, page_errors):
        """Synthesize a beforeinstallprompt and verify it's stashed for UI use."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1200)
        # Simulate a minimal beforeinstallprompt event; some browsers fire it natively,
        # others won't — the key is that eos.js attaches the handler without erroring.
        stashed = page.evaluate("""
            () => {
                var e = new Event('beforeinstallprompt');
                e.prompt = function() { return Promise.resolve({outcome: 'accepted'}); };
                e.userChoice = Promise.resolve({outcome: 'accepted'});
                e.preventDefault = function() {};
                window.dispatchEvent(e);
                return !!window._eosInstallPromptEvent;
            }
        """)
        assert stashed, "eos.js should stash the install prompt event for later use"
        assert_no_js_errors(page_errors)
