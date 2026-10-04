"""System tests for multi-app export groups.

Verifies:
  1. Every group in `export-groups.toml` is discoverable via
     `GET /api/export-groups` with correct member metadata.
  2. Building each group as a ZIP returns a valid archive containing
     `index.html` + one subdir per member + shared `_assets/` + merged
     `_data/state.json` + `_meta/export.json`.
  3. Live-mode `POST /api/apps/{app}/rpc/{method}` dispatches correctly
     (the in-browser EOS.callApp's server-side counterpart for group
     bundles that need a daemon round-trip).
  4. An exported group opens clean in a headless browser: no console
     errors, chooser renders one card per member, clicking a member
     lands on its page with the shim active.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest

from helpers import BASE_URL, assert_ok


def _declared_groups() -> list[dict]:
    """Read declared groups from the live daemon (preferred) or fall back to
    parsing the repo-root toml file directly for collection-time parametrize."""
    try:
        r = httpx.get(f"{BASE_URL}/api/export-groups", timeout=2)
        if r.status_code == 200:
            payload = r.json()
            if isinstance(payload, list):
                return payload
    except Exception:
        pass
    try:
        import tomllib
    except ImportError:
        return []
    toml_path = Path(__file__).resolve().parent.parent / "export-groups.toml"
    if not toml_path.exists():
        return []
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    groups = data.get("group") or []
    return groups if isinstance(groups, list) else [groups]


GROUPS = _declared_groups()


@pytest.mark.api
class TestExportGroupsAPI:
    def test_list_endpoint_returns_groups(self, http_client):
        data = assert_ok(http_client.get("/api/export-groups"))
        assert isinstance(data, list)
        assert any(g.get("id") == "work-os" for g in data), "work-os group missing"

    def test_work_os_members_are_export_enabled(self, http_client):
        data = assert_ok(http_client.get("/api/export-groups"))
        work_os = next(g for g in data if g["id"] == "work-os")
        # Every member MUST declare [provides.export].enabled = true.
        disabled = [m for m in work_os["members_detail"] if not m["export_enabled"]]
        assert not disabled, (
            f"work-os members missing [provides.export].enabled: "
            f"{[m['id'] for m in disabled]}"
        )

    def test_rpc_endpoint_dispatches_to_public_method(self, http_client):
        # Call projects.list_all via RPC — should return a list.
        r = http_client.post("/api/apps/projects/rpc/list_all", json={})
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    def test_rpc_endpoint_blocks_private_methods(self, http_client):
        r = http_client.post("/api/apps/projects/rpc/_find_project_file", json={})
        assert r.status_code == 403

    def test_rpc_endpoint_returns_404_for_unknown_method(self, http_client):
        r = http_client.post("/api/apps/projects/rpc/nonexistent_xyz", json={})
        assert r.status_code == 404


@pytest.mark.api
class TestExportGroupsBuild:
    @pytest.mark.parametrize("group", GROUPS, ids=[g.get("id", "?") for g in GROUPS])
    def test_group_builds_as_zip(self, group, http_client):
        group_id = group["id"]
        # Groups whose members haven't declared [provides.export] yet are
        # declared but not yet buildable — skip rather than fail. Build the
        # member manifests first to make the group buildable.
        detail = http_client.get(f"/api/export-groups").json()
        if isinstance(detail, list):
            this = next((g for g in detail if g.get("id") == group_id), None)
            if this:
                members = this.get("members_detail") or []
                enabled = [m for m in members if m.get("export_enabled")]
                if not enabled:
                    pytest.skip(f"group {group_id!r} has no export-enabled members")
        r = http_client.post(f"/api/export-groups/{group_id}/build?format=zip",
                             timeout=60)
        assert r.status_code == 200, f"{group_id}: {r.text}"
        assert r.headers.get("content-type", "").startswith("application/zip")
        assert r.content[:4] == b"PK\x03\x04", f"{group_id}: not a zip"

        # Extract + inspect.
        tmp = Path(tempfile.mkdtemp(prefix=f"eos-gtest-{group_id}-"))
        try:
            zf = tmp / "bundle.zip"
            zf.write_bytes(r.content)
            extracted = tmp / "out"
            shutil.unpack_archive(str(zf), str(extracted))

            # Chooser shell + shared assets.
            assert (extracted / "index.html").exists(), f"{group_id}: no chooser index"
            assert (extracted / "_assets" / "eos-export-shim.js").exists(), f"{group_id}: shim missing"

            # Meta lists the group + warnings.
            meta_file = extracted / "_meta" / "export.json"
            assert meta_file.exists()
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            assert meta["group_id"] == group_id
            assert isinstance(meta.get("warnings"), list)

            # Every enabled member has its own index.html.
            for member_id in meta["members"]:
                assert (extracted / member_id / "index.html").exists(), \
                    f"{group_id}/{member_id}: index missing"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.interactive
class TestExportGroupsUI:
    def test_work_os_bundle_loads_clean(self, http_client, page):
        """Build the work-os bundle, open its chooser in Chromium, verify every
        member card renders and clicking one mounts the app with the shim active."""
        r = http_client.post("/api/export-groups/work-os/build?format=zip", timeout=60)
        assert r.status_code == 200

        tmp = Path(tempfile.mkdtemp(prefix="eos-gtest-ui-"))
        try:
            zf = tmp / "bundle.zip"
            zf.write_bytes(r.content)
            shutil.unpack_archive(str(zf), str(tmp / "out"))
            index = (tmp / "out" / "index.html").resolve()

            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda msg: errors.append(f"console.error: {msg.text}")
                    if msg.type == "error" else None)

            # Chooser shell.
            page.goto("file:///" + str(index).replace("\\", "/"))
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            card_count = page.evaluate("document.querySelectorAll('.card').length")
            assert card_count >= 1, "chooser has no member cards"

            # Navigate into the first member; shim must be active.
            first_href = page.evaluate("document.querySelector('.card').getAttribute('href')")
            assert first_href, "first card missing href"
            member_index = (tmp / "out" / first_href).resolve()
            page.goto("file:///" + str(member_index).replace("\\", "/"))
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            assert page.evaluate("window.EOS_IS_EXPORT === true")
            assert page.evaluate("typeof window.EOS_EXPORT === 'object'")
            assert page.evaluate("typeof window.EOS_EXPORT.callApp === 'function'")

            # Bundle runs from file:// → external CDN fonts + missing assets emit
            # benign CORS / ERR_FILE_NOT_FOUND console errors. The bundle is
            # designed to render without those resources; filter the well-known
            # noise so we only fail on real JS errors.
            FILE_URL_NOISE = (
                "fonts.gstatic.com",
                "fonts.googleapis.com",
                "ERR_FAILED",
                "ERR_FILE_NOT_FOUND",
                "Access-Control-Allow-Headers",
                "CORS policy",
            )
            real_errors = [e for e in errors
                           if not any(n in e for n in FILE_URL_NOISE)]
            assert not real_errors, "errors during group UI run: " + "; ".join(real_errors[:6])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
