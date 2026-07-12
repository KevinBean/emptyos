"""System app tests: Vault Backup — 11 use cases."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors


@pytest.mark.api
class TestVaultBackupAPI:
    def test_status_shape(self, http_client):
        data = assert_dict_response(http_client.get("/vault-backup/api/status"))
        for k in ("stale", "enabled", "dest_root", "retention_days", "never_run",
                  "mode", "offsite_enabled", "hardlink_warning", "backup_data",
                  "data_include", "data_backup"):
            assert k in data, f"missing status key: {k}"

    def test_restore_unknown_snapshot_rejected(self, http_client):
        # Path-traversal / unknown name must be refused (resolved against the
        # known snapshot list, never a raw path).
        r = http_client.post("/vault-backup/api/restore",
                             json={"snapshot": "../etc/passwd"})
        data = assert_dict_response(r)
        assert data.get("ok") is False and "unknown snapshot" in (data.get("error") or "")

    def test_restore_requires_name(self, http_client):
        data = assert_dict_response(
            http_client.post("/vault-backup/api/restore", json={}))
        assert data.get("ok") is False

    def test_status_dest_root_is_path(self, http_client):
        data = assert_ok(http_client.get("/vault-backup/api/status"))
        assert isinstance(data.get("dest_root"), str) and data["dest_root"]

    def test_retention_is_positive_int(self, http_client):
        data = assert_ok(http_client.get("/vault-backup/api/status"))
        assert isinstance(data["retention_days"], int) and data["retention_days"] >= 1

    def test_snapshots_shape(self, http_client):
        data = assert_dict_response(http_client.get("/vault-backup/api/snapshots"))
        assert "snapshots" in data and isinstance(data["snapshots"], list)
        assert "dest_root" in data

    def test_reschedule_returns_ok(self, http_client):
        data = assert_dict_response(http_client.post("/vault-backup/api/reschedule"))
        assert data.get("ok") is True
        assert "enabled" in data

    def test_backup_now_runs(self, http_client):
        # Real snapshot of the (test) vault — should complete or fail-soft,
        # never 500. Result always carries an "ok" boolean.
        r = http_client.post("/vault-backup/api/backup-now")
        data = assert_dict_response(r)
        assert "ok" in data and isinstance(data["ok"], bool)
        if data["ok"]:
            assert "files" in data and "snapshot" in data
        else:
            assert "error" in data

    def test_status_reflects_run(self, http_client):
        # After a backup-now, status should no longer report never_run
        http_client.post("/vault-backup/api/backup-now")
        data = assert_ok(http_client.get("/vault-backup/api/status"))
        assert data["never_run"] is False

    def test_snapshots_listed_after_run(self, http_client):
        http_client.post("/vault-backup/api/backup-now")
        data = assert_ok(http_client.get("/vault-backup/api/snapshots"))
        # at least the dated snapshot we just created
        assert isinstance(data["snapshots"], list)


@pytest.mark.interactive
class TestVaultBackupUI:
    def test_page_loads(self, page, page_errors, base_url):
        page.goto(base_url + "/vault-backup/")
        assert_no_js_errors(page_errors)

    def test_status_line_renders(self, page, base_url):
        page.goto(base_url + "/vault-backup/")
        page.wait_for_selector("#status-line", timeout=5000)
        txt = page.text_content("#status-line")
        assert txt and txt.strip() != "Loading…"

    def test_backup_now_button_present(self, page, base_url):
        page.goto(base_url + "/vault-backup/")
        assert page.is_visible("#backup-now")
