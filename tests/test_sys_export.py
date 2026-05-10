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
            # Capabilities matrix must be present and shaped.
            caps_file = extracted / "_meta" / "capabilities.json"
            assert caps_file.exists(), f"{app_id}: missing _meta/capabilities.json"
            caps = json.loads(caps_file.read_text(encoding="utf-8"))
            assert "vault" in caps and "events" in caps and "viewer" in caps
            # Shim must be present — it's the whole point.
            assert (extracted / "_assets" / "eos-export-shim.js").exists()
            html = (extracted / "index.html").read_text(encoding="utf-8")
            assert "EOS_IS_EXPORT = true" in html, f"{app_id}: bootstrap missing"
            assert "eos-export-shim.js" in html, f"{app_id}: shim not linked"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# Per-bundle weight budget for ``_assets/`` only (uncompressed, KB). Snapshot
# data lives in ``_data/state.json`` and scales with the user's vault — it's
# not what we're guarding here. The budget catches JS/CSS/asset bloat so
# Tier 3's ``[provides.export].assets`` opt-in doesn't silently regress.
#
# Today the full asset set is ~500 KB. The cap below has comfortable headroom
# so ad-hoc additions don't trip the test, but a major regression (e.g. ship-
# all-of-eos.js-twice) would.
ASSETS_BUDGET_KB = 800


def _dir_size_kb(p: Path) -> float:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total / 1024


@pytest.mark.api
class TestExportBudget:
    """Soft guard so Tier 3 (assets opt-in) doesn't silently regress."""

    @pytest.mark.parametrize("app_id", [a["id"] for a in _export_enabled_apps()])
    def test_assets_within_budget(self, app_id, http_client):
        r = http_client.post(f"/api/apps/{app_id}/export?format=zip")
        assert r.status_code == 200, f"{app_id}: {r.text}"
        tmp = Path(tempfile.mkdtemp(prefix=f"eos-budget-{app_id}-"))
        try:
            zf = tmp / "bundle.zip"
            zf.write_bytes(r.content)
            extracted = tmp / "out"
            shutil.unpack_archive(str(zf), str(extracted))
            assets_kb = _dir_size_kb(extracted / "_assets")
            assert assets_kb <= ASSETS_BUDGET_KB, (
                f"{app_id} _assets/ {assets_kb:.0f} KB > budget {ASSETS_BUDGET_KB} KB. "
                f"Either declare [provides.export].assets = [\"minimal\", ...] in "
                f"apps/{app_id}/manifest.toml, or raise ASSETS_BUDGET_KB."
            )
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


# Console errors we ignore in file:// smoke runs — Chromium emits these for
# resource-load failures (Google Fonts CORS preflight under Origin: null,
# missing favicon, etc.) that are environmental, not bundle bugs.
_IGNORED_CONSOLE_PATTERNS = (
    "Failed to load resource",
    "fonts.gstatic.com",
    "fonts.googleapis.com",
    "Access to font at",
    "ERR_FILE_NOT_FOUND",
    "ERR_FAILED",
    "favicon",
)


def _is_real_error(text: str) -> bool:
    return not any(p in text for p in _IGNORED_CONSOLE_PATTERNS)


def _open_export_bundle(http_client, page, app_id: str, prefix: str) -> Path:
    """Build + extract + open ``app_id``'s export bundle. Returns tmp dir.

    Caller is responsible for cleanup. Asserts the shim booted and the
    capabilities matrix is populated; tolerates environmental resource-load
    errors that come from running a bundle under ``file://``.
    """
    r = http_client.post(f"/api/apps/{app_id}/export?format=zip")
    assert r.status_code == 200, f"{app_id}: {r.text}"
    tmp = Path(tempfile.mkdtemp(prefix=f"eos-smoke-{app_id}-"))
    zf = tmp / "bundle.zip"
    zf.write_bytes(r.content)
    shutil.unpack_archive(str(zf), str(tmp / "out"))
    index = (tmp / "out" / "index.html").resolve()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on(
        "console",
        lambda msg: errors.append(f"console.error: {msg.text}")
        if msg.type == "error" and _is_real_error(msg.text)
        else None,
    )
    page.goto("file:///" + str(index).replace("\\", "/"))
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    # Shim must have booted and rendered the offline pill.
    assert page.evaluate("!!document.getElementById('eos-export-pill')"), \
        f"{app_id}: offline pill missing"
    assert page.evaluate("!!window.EOS_EXPORT"), f"{app_id}: EOS_EXPORT not exposed"
    # Capabilities matrix must be populated and contain vault.
    caps_keys = page.evaluate("Object.keys(window.EOS_EXPORT_CAPABILITIES || {})")
    assert "vault" in caps_keys, f"{app_id}: capabilities matrix missing vault"
    if errors:
        raise AssertionError(f"{app_id} console errors: " + "; ".join(errors[:6]))
    return tmp


@pytest.mark.interactive
class TestExportUISmoke:
    """Per-app smoke for the new export.py hooks. Each verifies the bundle
    boots clean and exercises one offline write that the hook claims to handle."""

    def test_task_offline_add(self, http_client, page):
        tmp = _open_export_bundle(http_client, page, "task", "/task")
        try:
            # Fire the cross-app add the way capture / voice would in a real bundle.
            result = page.evaluate(
                "(async () => await window.EOS.callApp('task', 'add', "
                "{text: 'PLAYWRIGHT-TEST-task-add', project: 'inbox'}))()"
            )
            assert isinstance(result, dict) and result.get("text", "").startswith("PLAYWRIGHT-TEST-")
            # Reading back through the registered list_all should include it.
            rows = page.evaluate("(async () => await window.EOS.callApp('task', 'list_all', {}))()")
            assert any(r.get("text", "").startswith("PLAYWRIGHT-TEST-") for r in rows)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_projects_offline_set_field(self, http_client, page):
        tmp = _open_export_bundle(http_client, page, "projects", "/projects")
        try:
            # Pick the first project in the snapshot and flip its status.
            rows = page.evaluate("(async () => await window.EOS.callApp('projects', 'list_all', {}))()")
            assert isinstance(rows, list) and rows, "projects snapshot is empty"
            target_id = rows[0]["id"]
            res = page.evaluate(
                "(async (id) => await window.EOS.callApp('projects', 'set_field', "
                "{id: id, field: 'status', value: 'shelved'}))('" + target_id.replace("'", "\\'") + "')"
            )
            assert res.get("ok") is True, f"set_field returned {res}"
            # Round-trip via list_all → status reflects the write.
            rows2 = page.evaluate("(async () => await window.EOS.callApp('projects', 'list_all', {}))()")
            updated = next((r for r in rows2 if r["id"] == target_id), None)
            assert updated and updated.get("status") == "shelved"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_people_offline_create(self, http_client, page):
        tmp = _open_export_bundle(http_client, page, "people", "/people")
        try:
            res = page.evaluate(
                "(async () => { var r = await fetch('/people/api/people', "
                "{method: 'POST', headers: {'Content-Type': 'application/json'}, "
                "body: JSON.stringify({name: 'PLAYWRIGHT-TEST-Person', role: 'tester'})}); "
                "return await r.json(); })()"
            )
            assert res.get("ok") is True and res.get("id"), f"create returned {res}"
            # Round-trip — list_all should now include the new person.
            rows = page.evaluate("(async () => await window.EOS.callApp('people', 'list_all', {}))()")
            assert any(r.get("name", "").startswith("PLAYWRIGHT-TEST-") for r in rows)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_quick_action_offline_capture(self, http_client, page):
        tmp = _open_export_bundle(http_client, page, "quick-action", "/quick-action")
        try:
            # POST /api/add — the primary capture path.
            res = page.evaluate(
                "(async () => { var r = await fetch('/quick-action/api/add', "
                "{method: 'POST', headers: {'Content-Type': 'application/json'}, "
                "body: JSON.stringify({text: 'PLAYWRIGHT-TEST-capture'})}); "
                "return await r.json(); })()"
            )
            assert res.get("text", "").startswith("PLAYWRIGHT-TEST-"), f"add returned {res}"
            # GET /api/list should pick it up.
            rows = page.evaluate(
                "(async () => { var r = await fetch('/quick-action/api/list'); return await r.json(); })()"
            )
            assert any(r.get("text", "").startswith("PLAYWRIGHT-TEST-") for r in rows)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
