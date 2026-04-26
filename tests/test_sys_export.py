"""System tests for the standalone-export architecture.

Three layers:

1. **Discovery** — every app with ``[provides.export].enabled = true`` is picked
   up by the manifest parser and surfaced via ``GET /api/apps/{id}``.

2. **Build** — the exporter produces a valid directory for every export-enabled
   app, with the required structure (`index.html` + `_assets/` + `_meta/export.json`).

3. **Runtime (UI)** — the ``boards`` bundle, loaded via ``file://`` in Chromium,
   renders with no console errors, exposes the offline pill, intercepts `/api/`
   calls from the inlined snapshot, and lets the user create a board without a
   daemon.

The UI layer doubles as a smoke test for the shim + boards `client_overrides`.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest

from helpers import BASE_URL, assert_ok


def _export_enabled_apps() -> list[dict]:
    """Walk the live daemon's app list and filter to export-enabled apps.

    Called at parametrize-time. When the daemon isn't running (e.g. during
    pytest --collect-only in CI), falls back to scanning manifest files
    directly so collection still succeeds.
    """
    try:
        apps = httpx.get(f"{BASE_URL}/api/apps", timeout=2).json()
        out: list[dict] = []
        for entry in apps:
            detail = httpx.get(f"{BASE_URL}/api/apps/{entry['id']}", timeout=2).json()
            exp = detail.get("export") or {}
            if exp.get("enabled"):
                out.append({"id": entry["id"], "export": exp})
        return out
    except Exception:
        # Fallback: scan manifests directly (CI --collect-only, no daemon).
        import tomllib

        root = Path(__file__).resolve().parent.parent / "apps"
        out = []
        if not root.exists():
            return out
        for manifest in sorted(root.glob("*/manifest.toml")):
            try:
                with open(manifest, "rb") as f:
                    data = tomllib.load(f)
            except Exception:
                continue
            exp = data.get("provides", {}).get("export", {}) or {}
            if exp.get("enabled"):
                out.append({"id": data.get("app", {}).get("id", manifest.parent.name), "export": exp})
        return out


@pytest.mark.api
class TestExportAPI:
    def test_api_detail_surfaces_export_block(self, http_client):
        """Every app detail response must include an ``export`` key (even if disabled)."""
        data = assert_ok(http_client.get("/api/apps/boards"))
        assert "export" in data
        assert data["export"]["enabled"] is True
        assert data["export"]["mode"] == "standalone"
        assert isinstance(data["export"]["fallbacks"], list)
        assert "vault:indexeddb" in data["export"]["fallbacks"]

    def test_disabled_app_reports_export_false(self, http_client):
        """An app without ``[provides.export]`` must show enabled=false, not 500."""
        data = assert_ok(http_client.get("/api/apps/settings"))
        assert data["export"]["enabled"] is False

    def test_post_export_rejects_disabled_app(self, http_client):
        """POST /api/apps/settings/export must 4xx with a clear reason."""
        r = http_client.post("/api/apps/settings/export?format=zip")
        assert r.status_code in (400, 404), r.text
        body = r.json()
        assert "export" in body.get("error", "").lower()

    def test_post_export_rejects_unknown_format(self, http_client):
        r = http_client.post("/api/apps/boards/export?format=pdf")
        assert r.status_code == 400

    def test_post_export_zip_attachment(self, http_client):
        """POST /api/apps/boards/export?format=zip must return a ZIP body."""
        r = http_client.post("/api/apps/boards/export?format=zip")
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/zip")
        assert r.content[:4] == b"PK\x03\x04", "not a zip file"


@pytest.mark.api
class TestExportBuild:
    """Exporter produces a well-formed bundle for every export-enabled app."""

    @pytest.mark.parametrize("app_id", [a["id"] for a in _export_enabled_apps()])
    def test_dir_format_structure(self, app_id, http_client):
        r = http_client.post(f"/api/apps/{app_id}/export?format=zip")
        assert r.status_code == 200, f"{app_id}: {r.text}"
        # Extract the zip and assert the shape.
        tmp = Path(tempfile.mkdtemp(prefix=f"eos-test-{app_id}-"))
        try:
            zf = tmp / "bundle.zip"
            zf.write_bytes(r.content)
            extracted = tmp / "out"
            shutil.unpack_archive(str(zf), str(extracted))
            # After unpack we get a sibling dir (shutil auto-creates 'out/')
            # with the exporter's layout.
            assert (extracted / "index.html").exists(), f"{app_id}: missing index.html"
            assert (extracted / "_assets").is_dir(), f"{app_id}: missing _assets/"
            meta_file = extracted / "_meta" / "export.json"
            assert meta_file.exists(), f"{app_id}: missing _meta/export.json"
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            assert meta["app_id"] == app_id
            # Shim must be present — it's the whole point.
            assert (extracted / "_assets" / "eos-export-shim.js").exists()
            html = (extracted / "index.html").read_text(encoding="utf-8")
            assert "EOS_IS_EXPORT = true" in html, f"{app_id}: bootstrap missing"
            assert "eos-export-shim.js" in html, f"{app_id}: shim not linked"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.interactive
class TestExportUI:
    """The exported boards bundle must run standalone in Chromium."""

    def test_boards_bundle_loads_clean(self, http_client, page):
        """Export boards, open via file://, assert no console errors and the UI renders."""
        r = http_client.post("/api/apps/boards/export?format=zip")
        assert r.status_code == 200

        tmp = Path(tempfile.mkdtemp(prefix="eos-test-boards-ui-"))
        try:
            zf = tmp / "boards.zip"
            zf.write_bytes(r.content)
            shutil.unpack_archive(str(zf), str(tmp / "out"))
            index = (tmp / "out" / "index.html").resolve()

            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None)

            page.goto("file:///" + str(index).replace("\\", "/"))
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            # Structural: the Monday-class shell must be present.
            assert page.evaluate("!!document.getElementById('boards-nav')")
            assert page.evaluate("!!document.getElementById('board-detail')")
            assert page.evaluate("!!document.getElementById('view-timeline')")
            # Offline pill.
            assert page.evaluate("!!document.getElementById('eos-export-pill')")
            # Presets render from the inlined snapshot.
            assert page.evaluate("document.querySelectorAll('#presets-grid .board-card').length") >= 1

            # Write-path: create a board from a preset → POST is IndexedDB-backed.
            page.evaluate("createFromPreset('project-tracker')")
            page.wait_for_timeout(400)
            title = page.evaluate("document.getElementById('board-title').textContent")
            assert title == "Project Tracker"
            # Nav list must have one item now.
            assert page.evaluate("document.querySelectorAll('#boards-nav-list .boards-nav-item').length") >= 1

            # No console errors at all.
            assert not errors, "errors during export UI run: " + "; ".join(errors[:6])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
