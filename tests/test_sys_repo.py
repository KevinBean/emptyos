"""System tests: Repo — read + grep + edit + write + exec verbs against
the EmptyOS codebase. Parallel to apps/agent's bundled tools — this app
exposes the capabilities as rooms verbs that the [DO:]-gate handles."""

import pytest

from helpers import assert_ok


@pytest.mark.api
class TestRepoAPI:
    def test_info_reports_root(self, http_client):
        data = assert_ok(http_client.get("/repo/api/info"))
        assert isinstance(data, dict)
        assert "root" in data
        assert "rg_available" in data
        # The daemon under test runs from the repo root — info.root must
        # exist on disk.
        from pathlib import Path
        assert Path(data["root"]).exists()

    def test_read_existing_file(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/read",
            json={"path": "emptyos.toml.example"},
        ))
        # File may not exist on every machine; accept either ok or missing
        if not data.get("ok"):
            # Fall back to a file we know is in the repo
            data = assert_ok(http_client.post(
                "/repo/api/read",
                json={"path": "CLAUDE.md"},
            ))
        assert data.get("ok"), f"read failed: {data}"
        assert isinstance(data.get("content"), str)
        assert data.get("total_lines", 0) > 0
        assert data.get("path", "").count("\\") == 0, "paths must use forward slashes"

    def test_read_with_offset_limit(self, http_client):
        full = assert_ok(http_client.post(
            "/repo/api/read",
            json={"path": "CLAUDE.md", "limit": 10},
        ))
        assert full.get("ok")
        assert full.get("returned_lines") <= 10
        slice_ = assert_ok(http_client.post(
            "/repo/api/read",
            json={"path": "CLAUDE.md", "offset": 5, "limit": 10},
        ))
        assert slice_.get("ok")
        assert slice_.get("offset") == 5
        if full.get("returned_lines") > 5:
            assert slice_["content"] != full["content"]

    def test_read_missing_file(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/read",
            json={"path": "no-such-file-PLAYWRIGHT-TEST.txt"},
        ))
        assert data.get("ok") is False
        assert "not found" in (data.get("error") or "").lower()

    def test_read_rejects_path_escape(self, http_client):
        for evil in ("../../etc/passwd", "..\\..\\Windows\\System32\\config\\sam",
                     "/etc/passwd", "C:/Windows/System32/drivers/etc/hosts"):
            data = assert_ok(http_client.post(
                "/repo/api/read",
                json={"path": evil},
            ))
            assert data.get("ok") is False, f"path escape allowed: {evil!r}"

    def test_read_directory_rejected(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/read",
            json={"path": "apps"},
        ))
        assert data.get("ok") is False
        assert "directory" in (data.get("error") or "").lower()

    def test_tree_root_directories(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/tree",
            json={"path": "", "depth": 1, "include_files": False, "limit": 50},
        ))
        assert data.get("ok"), f"tree failed: {data}"
        entries = data.get("entries") or []
        assert any(e.get("path") == "apps" and e.get("type") == "dir" for e in entries)
        assert all(e.get("type") == "dir" for e in entries)
        assert all("\\" not in e.get("path", "") for e in entries)

    def test_tree_includes_files_when_requested(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/tree",
            json={"path": "emptyos/cli", "depth": 1, "include_files": True, "limit": 100},
        ))
        assert data.get("ok"), f"tree failed: {data}"
        entries = data.get("entries") or []
        assert any(e.get("path") == "emptyos/cli/chat.py" and e.get("type") == "file" for e in entries)

    def test_tree_limit_truncates(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/tree",
            json={"path": "", "depth": 3, "include_files": True, "limit": 1},
        ))
        assert data.get("ok"), f"tree failed: {data}"
        assert data.get("count") == 1
        assert data.get("truncated") is True

    def test_tree_missing_file_and_escape_rejected(self, http_client):
        missing = assert_ok(http_client.post(
            "/repo/api/tree",
            json={"path": "no-such-dir-PLAYWRIGHT-TEST"},
        ))
        assert missing.get("ok") is False
        assert "not found" in (missing.get("error") or "").lower()

        file_target = assert_ok(http_client.post(
            "/repo/api/tree",
            json={"path": "CLAUDE.md"},
        ))
        assert file_target.get("ok") is False
        assert "directory" in (file_target.get("error") or "").lower()

        for evil in ("../../etc/passwd", "..\\..\\Windows\\System32", "/etc"):
            escaped = assert_ok(http_client.post(
                "/repo/api/tree",
                json={"path": evil},
            ))
            assert escaped.get("ok") is False, f"path escape allowed: {evil!r}"

    def test_grep_files_mode(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/grep",
            json={"pattern": "class BaseApp", "glob": "**/*.py", "limit": 20},
        ))
        if not data.get("ok") and "unavailable" in (data.get("error") or ""):
            pytest.skip("ripgrep + grep both unavailable on this machine")
        assert data.get("ok"), f"grep failed: {data}"
        assert data.get("mode") == "files_with_matches"
        matches = data.get("matches") or []
        assert len(matches) >= 1
        for m in matches:
            assert "\\" not in m["path"]
            assert not m["path"].startswith("/")

    def test_grep_content_mode(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/grep",
            json={
                "pattern": "class BaseApp",
                "glob": "**/*.py",
                "mode": "content",
                "limit": 10,
            },
        ))
        if not data.get("ok") and "unavailable" in (data.get("error") or ""):
            pytest.skip("ripgrep unavailable")
        assert data.get("ok")
        assert data.get("mode") == "content"
        matches = data.get("matches") or []
        if matches:
            m = matches[0]
            assert "line_number" in m and isinstance(m["line_number"], int)
            assert "text" in m

    def test_grep_empty_pattern(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/grep",
            json={"pattern": ""},
        ))
        assert data.get("ok") is False
        assert "pattern" in (data.get("error") or "").lower()

    def test_grep_path_narrowing(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/grep",
            json={"pattern": "BaseApp", "path": "apps/repo", "limit": 20},
        ))
        if not data.get("ok") and "unavailable" in (data.get("error") or ""):
            pytest.skip("ripgrep unavailable")
        assert data.get("ok")
        for m in data.get("matches") or []:
            assert m["path"].startswith("apps/repo/"), \
                f"match outside narrowed path: {m['path']}"


@pytest.mark.api
class TestRepoWriteAPI:
    """Direct call_app surface for edit + write. The agent [DO:] path
    routes these through rooms' review-gate (ALWAYS_GATE_VERBS) which is
    exercised in tests/test_sys_rooms*.py; here we verify the underlying
    methods are sound for the rare direct-CLI flow."""

    TMP_PATH = "data/apps/tests/PLAYWRIGHT-TEST-repo-write.txt"

    def test_write_creates_new_file(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/write",
            json={"path": self.TMP_PATH, "content": "hello v1\n"},
        ))
        assert data.get("ok"), f"write failed: {data}"
        assert data.get("bytes") == len("hello v1\n".encode("utf-8"))
        read = assert_ok(http_client.post(
            "/repo/api/read", json={"path": self.TMP_PATH},
        ))
        assert read.get("ok")
        assert read.get("content") == "hello v1"

    def test_write_overwrites_existing(self, http_client):
        assert_ok(http_client.post(
            "/repo/api/write",
            json={"path": self.TMP_PATH, "content": "v1 content\n"},
        ))
        data = assert_ok(http_client.post(
            "/repo/api/write",
            json={"path": self.TMP_PATH, "content": "v2 content\n"},
        ))
        assert data.get("ok")
        read = assert_ok(http_client.post(
            "/repo/api/read", json={"path": self.TMP_PATH},
        ))
        assert "v2 content" in (read.get("content") or "")

    def test_write_rejects_path_escape(self, http_client):
        for evil in ("../../etc/passwd", "C:/Windows/System32/cmd.exe"):
            data = assert_ok(http_client.post(
                "/repo/api/write",
                json={"path": evil, "content": "x"},
            ))
            assert data.get("ok") is False, f"path escape allowed: {evil!r}"

    def test_edit_unique_match(self, http_client):
        assert_ok(http_client.post(
            "/repo/api/write",
            json={
                "path": self.TMP_PATH,
                "content": "alpha\nbeta\ngamma\n",
            },
        ))
        data = assert_ok(http_client.post(
            "/repo/api/edit",
            json={
                "path": self.TMP_PATH,
                "old": "beta",
                "new": "BETA",
            },
        ))
        assert data.get("ok"), f"edit failed: {data}"
        assert data.get("replaced") == 1
        read = assert_ok(http_client.post(
            "/repo/api/read", json={"path": self.TMP_PATH},
        ))
        assert "BETA" in (read.get("content") or "")
        assert "beta" not in (read.get("content") or "")

    def test_edit_rejects_missing_match(self, http_client):
        assert_ok(http_client.post(
            "/repo/api/write",
            json={"path": self.TMP_PATH, "content": "alpha\nbeta\n"},
        ))
        data = assert_ok(http_client.post(
            "/repo/api/edit",
            json={"path": self.TMP_PATH, "old": "no-such-string", "new": "X"},
        ))
        assert data.get("ok") is False
        assert "not found" in (data.get("error") or "").lower()

    def test_edit_rejects_multi_match(self, http_client):
        assert_ok(http_client.post(
            "/repo/api/write",
            json={"path": self.TMP_PATH, "content": "x\nx\nx\n"},
        ))
        data = assert_ok(http_client.post(
            "/repo/api/edit",
            json={"path": self.TMP_PATH, "old": "x", "new": "Y"},
        ))
        assert data.get("ok") is False
        assert "matches" in (data.get("error") or "").lower()
        assert "3" in (data.get("error") or "")

    def test_edit_rejects_empty_old(self, http_client):
        assert_ok(http_client.post(
            "/repo/api/write",
            json={"path": self.TMP_PATH, "content": "alpha\n"},
        ))
        data = assert_ok(http_client.post(
            "/repo/api/edit",
            json={"path": self.TMP_PATH, "old": "", "new": "X"},
        ))
        assert data.get("ok") is False

    def test_edit_rejects_path_escape(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/edit",
            json={"path": "../../etc/passwd", "old": "root", "new": "x"},
        ))
        assert data.get("ok") is False

    def test_edit_rejects_missing_file(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/edit",
            json={
                "path": "data/apps/tests/PLAYWRIGHT-TEST-no-such-file.txt",
                "old": "x", "new": "y",
            },
        ))
        assert data.get("ok") is False
        assert "not found" in (data.get("error") or "").lower()


@pytest.mark.api
class TestRepoExecAPI:
    """Direct call_app surface for exec. Agent [DO:] route hits the
    rooms review-gate (ALWAYS_GATE_VERBS); the underlying method tested
    here is what apply_pending dispatches to after the user clicks Apply."""

    def test_exec_echo_roundtrip(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/exec",
            json={"cmd": "echo hello-from-eos", "timeout": 10},
        ))
        if not data.get("ok") and "no usable shell" in (data.get("error") or ""):
            pytest.skip("no shell on PATH")
        assert data.get("ok"), f"echo failed: {data}"
        assert data.get("exit_code") == 0
        assert "hello-from-eos" in (data.get("stdout") or "")
        assert isinstance(data.get("duration_s"), (int, float))
        assert "exit=0" in (data.get("summary") or "")

    def test_exec_nonzero_exit_code(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/exec",
            json={"cmd": "exit 7", "timeout": 10},
        ))
        if "no usable shell" in (data.get("error") or ""):
            pytest.skip("no shell on PATH")
        assert data.get("ok") is False
        assert data.get("exit_code") == 7

    def test_exec_timeout_kills_process(self, http_client):
        import time as _t
        started = _t.monotonic()
        data = assert_ok(http_client.post(
            "/repo/api/exec",
            json={"cmd": "sleep 10", "timeout": 1},
        ))
        elapsed = _t.monotonic() - started
        if "no usable shell" in (data.get("error") or ""):
            pytest.skip("no shell on PATH")
        if not data.get("ok") and "spawn failed" in (data.get("error") or ""):
            pytest.skip("sleep not available in this shell")
        assert data.get("ok") is False
        assert "timeout" in (data.get("error") or "").lower()
        assert data.get("exit_code") == -1
        assert elapsed < 6, f"timeout did not kill quickly enough: {elapsed:.2f}s"

    def test_exec_rejects_empty_cmd(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/exec",
            json={"cmd": "", "timeout": 10},
        ))
        assert data.get("ok") is False
        assert "required" in (data.get("error") or "").lower()

    def test_exec_rejects_bad_timeout(self, http_client):
        for bad in (-1, 0, 601, 99999):
            data = assert_ok(http_client.post(
                "/repo/api/exec",
                json={"cmd": "echo x", "timeout": bad},
            ))
            assert data.get("ok") is False, f"timeout {bad} accepted"
            assert "timeout" in (data.get("error") or "").lower() \
                or "1..600" in (data.get("error") or "")

    def test_exec_rejects_cwd_escape(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/exec",
            json={"cmd": "echo x", "cwd": "../../etc"},
        ))
        assert data.get("ok") is False
        assert "cwd" in (data.get("error") or "").lower()

    def test_exec_stderr_captured(self, http_client):
        data = assert_ok(http_client.post(
            "/repo/api/exec",
            json={"cmd": "echo to-stderr 1>&2", "timeout": 10},
        ))
        if "no usable shell" in (data.get("error") or ""):
            pytest.skip("no shell on PATH")
        assert "stderr" in data
        assert "stdout" in data
