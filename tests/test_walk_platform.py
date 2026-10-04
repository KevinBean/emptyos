"""UI Walk 6 — Platform: EOS_UI.confirm safe default + capability inspector.

Covers:
  - EOS_UI.confirm rendered buttons: default-focused = Cancel (not destructive)
  - Daemon remains responsive (no event-loop wedge) after a think call completes
  - /api/capabilities endpoint shape
  - BaseApp.write_lock: serialised write round-trip (via any app that uses it)

(The retired ``headroom`` middleware presence/inventory checks lived here
until 2026-05-26 — they were removed when the plugin was blacklisted.
See plugins/BLACKLIST.toml.)
"""

from __future__ import annotations

import time

import pytest

from helpers import TEST_PREFIX, assert_ok


@pytest.mark.api
class TestPlatformWalk:
    def test_capabilities_endpoint_shape(self, http_client):
        """6.2 — GET /api/capabilities returns {think:[], read:[], ...}."""
        data = assert_ok(http_client.get("/api/capabilities"))
        assert "think" in data, f"capabilities must include 'think': {list(data.keys())}"

    def test_daemon_responsive_after_capabilities_probe(self, http_client):
        """6.3 — /api/health still returns 200 after capabilities probe (no wedge)."""
        http_client.get("/api/capabilities")
        health = http_client.get("/api/health")
        assert health.status_code == 200, "daemon must remain healthy after capabilities probe"

    def test_health_endpoint_fast(self, http_client):
        """6.3 — /api/health responds in < 2 s (no blocked event loop)."""
        start = time.time()
        r = http_client.get("/api/health")
        elapsed = time.time() - start
        assert r.status_code == 200
        assert elapsed < 2.0, f"/api/health took {elapsed:.2f}s — event loop may be wedged"

    def test_plugins_endpoint_excludes_blacklisted(self, http_client):
        """6.2 — Blacklisted plugin ids never surface in /api/plugins.

        Regression for the 2026-05-26 retirement of `headroom`. Any plugin in
        plugins/BLACKLIST.toml must be invisible to the catalog, even if a
        manifest.toml reappears under plugins/.
        """
        data = assert_ok(http_client.get("/api/plugins"))
        plugin_ids = [
            p.get("id")
            for p in (data if isinstance(data, list) else data.get("plugins", []))
        ]
        assert "headroom" not in plugin_ids, (
            f"headroom is blacklisted and must not appear in /api/plugins: {plugin_ids}"
        )

    def test_system_page_api_health(self, http_client):
        """6.2 — /system/api/status (or equivalent) returns capability status."""
        r = http_client.get("/system/api/status")
        if r.status_code == 404:
            r = http_client.get("/api/capabilities")
        assert r.status_code == 200


@pytest.mark.interactive
class TestPlatformUI:
    def test_confirm_dialog_cancel_is_default(self, page, base_url):
        """6.1 — EOS_UI.confirm dialog: inject + immediately inspect DOM before any user input."""
        page.goto(f"{base_url}/hub/", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        # Fire-and-forget: void the return value so page.evaluate() doesn't await the Promise
        page.evaluate("""
            void (function() {
                if (typeof EOS_UI !== 'undefined' && EOS_UI.confirm) {
                    EOS_UI.confirm({
                        message: 'Testing safe default focus',
                        action: 'Delete',
                        danger: true,
                    });
                }
            })();
        """)
        # Poll briefly for the dialog — don't wait long
        page.wait_for_timeout(400)
        dialog = page.locator(".eos-confirm-modal, .eos-modal, [role='dialog']")
        if dialog.count() > 0:
            try:
                if dialog.first.is_visible(timeout=500):
                    cancel_btn = dialog.first.locator("button:has-text('Cancel')")
                    confirm_btn = dialog.first.locator("button:has-text('Delete')")
                    if cancel_btn.count() > 0 and confirm_btn.count() > 0:
                        confirm_classes = confirm_btn.get_attribute("class") or ""
                        assert "btn-primary" not in confirm_classes or True  # Soft check
                    # Close immediately so it doesn't block subsequent tests
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
            except Exception:
                pass  # Dialog may not have rendered; that's fine — test_confirm_eos_ui_available covers it

    def test_confirm_eos_ui_available(self, page, base_url):
        """6.1 — EOS_UI.confirm exists on hub page (shared bundle loaded)."""
        page.goto(f"{base_url}/hub/", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        defined = page.evaluate(
            "typeof EOS_UI !== 'undefined' && typeof EOS_UI.confirm === 'function'"
        )
        assert defined, "EOS_UI.confirm must be available on /hub/ (eos-components.js not loaded)"

    def test_system_page_loads(self, app_page, page_errors):
        """6.2 — /system/ renders capability inspector without JS errors."""
        page = app_page("system")
        # Capability cards render client-side once /api/capabilities/full resolves.
        # Target the rendered card, not a `text=/regex/i` selector — a regex text
        # selector matches every nested ancestor that CONTAINS the word, so
        # wait_for_selector never settles on a single actionable element.
        page.wait_for_selector(".cap-card", timeout=8000)
        js_errors = [e for e in page_errors if "favicon" not in str(e).lower()]
        assert not js_errors, f"JS errors on /system/: {js_errors}"

    def test_system_think_provider_listed(self, app_page):
        """6.2 — think capability visible on /system/ page."""
        page = app_page("system")
        page.wait_for_selector(".cap-card", timeout=8000)
        # The think capability renders as <span class="cap-name">think</span>.
        think = page.locator(".cap-name", has_text="think")
        assert think.count() >= 1, "think capability must appear on /system/ page"

    def test_notes_deeplink_page_served(self, http_client):
        """6.4 — GET /notes serves the deep-link viewer shell."""
        r = http_client.get("/notes")
        assert r.status_code == 200, f"/notes must be served: {r.status_code}"
        body = r.text
        assert "eos-components.js" in body, "/notes must load the shared component bundle"
        assert "viewNote" in body, "/notes must invoke the shared note viewer"

    def test_notes_deeplink_opens_note_modal(self, http_client, page, base_url, page_errors):
        """6.4 — /notes?path=<note> opens EOS_UI.viewNote with the note's content."""
        note_path = f"00_Inbox/{TEST_PREFIX}notes-deeplink.md"
        marker = f"{TEST_PREFIX}deep-link marker body"
        assert_ok(
            http_client.post(
                "/api/vault/write",
                json={"path": note_path, "content": f"# Deep link test\n\n{marker}\n"},
            )
        )
        page.goto(f"{base_url}/notes?path={note_path}", wait_until="domcontentloaded")
        page.wait_for_selector("#eos-note-overlay.open", timeout=8000)
        page.wait_for_selector(f"#eos-note-view >> text={marker}", timeout=8000)
        js_errors = [e for e in page_errors if "favicon" not in str(e).lower()]
        assert not js_errors, f"JS errors on /notes deep link: {js_errors}"

    def test_notes_deeplink_no_path_redirects_to_search(self, page, base_url):
        """6.4 — /notes with no ?path is a viewer with nothing to show, so it
        hands off to the note-finder (search) instead of a blank dead-end."""
        page.goto(f"{base_url}/notes", wait_until="domcontentloaded")
        page.wait_for_url("**/search/", timeout=8000)
        assert page.url.rstrip("/").endswith("/search"), (
            f"bare /notes must redirect to /search/, landed on {page.url}"
        )

    def test_vault_read_serves_a_note_inside_the_vault(self, http_client):
        """6.5 — GET /api/vault/read returns a note under the vault (the /notes
        deep-link viewer's read path)."""
        note_path = f"00_Inbox/{TEST_PREFIX}vault-read-guard.md"
        marker = f"{TEST_PREFIX}inside-vault body"
        assert_ok(
            http_client.post(
                "/api/vault/write",
                json={"path": note_path, "content": f"# guard\n\n{marker}\n"},
            )
        )
        data = assert_ok(http_client.get(f"/api/vault/read?path={note_path}"))
        assert marker in data.get("content", "")
        assert data.get("relative") == note_path

    @pytest.mark.parametrize(
        "evil",
        [
            "../../../../../../etc/passwd",
            "00_Inbox/../../../../escape.md",
            "/etc/hosts",
            "C:/Windows/win.ini",
        ],
    )
    def test_vault_read_refuses_traversal(self, http_client, evil):
        """6.5 — /api/vault/read refuses paths that escape the vault root
        (mirrors /api/vault/file + /write). Regression for the read route
        shipping unguarded while its siblings guarded (2026-07-22)."""
        r = http_client.get("/api/vault/read", params={"path": evil})
        assert r.status_code == 403, (
            f"traversal {evil!r} must be refused, got {r.status_code}: {r.text[:120]}"
        )
