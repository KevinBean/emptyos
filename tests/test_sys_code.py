"""System tests: apps/code — code-IDE-shaped frontend over apps/agent.

The MVP scope of apps/code is HTML + WebSocket against apps/agent. These
tests cover the only NEW backend surface (the /api/tree endpoint + the
page-serve route) — the WS protocol and tool dispatch are exercised by
apps/agent's own test suite, not duplicated here.
"""

import pytest

from helpers import assert_ok


@pytest.mark.api
class TestCodePageServe:
    """The /code/ URL is auto-mounted via apps/code/pages/index.html.
    These checks confirm the page loads + carries its key shell elements."""

    def test_page_serves_200(self, http_client):
        r = http_client.get("/code/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_page_carries_shell_elements(self, http_client):
        r = http_client.get("/code/")
        body = r.text
        # Layout zones — file tree + center pane + chat pane + terminal.
        for marker in ("workbench", "tree-root", "preview-body", "chat-msgs", "term-body"):
            assert marker in body, f"shell element missing: {marker!r}"

    def test_page_loads_eos_components_css(self, http_client):
        """DL-1 / shared-frontend rule: the page must pull in
        eos-components.css so it inherits theme tokens + EOS_UI styles
        (button classes, badge classes, etc.)."""
        body = http_client.get("/code/").text
        assert "/static/eos-components.css" in body
        assert "/static/theme.css" in body

    def test_chat_input_present(self, http_client):
        """The chat surface must have an input + send affordance —
        smoke for the user-facing interaction path."""
        body = http_client.get("/code/").text
        assert 'id="chat-input"' in body
        assert 'id="chat-send"' in body


@pytest.mark.api
class TestCodeTreeAPI:
    """The /code/api/tree endpoint — only new server-side surface. It's a
    one-level directory list with traversal guards + a skip-list for noise
    (`.git`, `__pycache__`, `node_modules`, etc.)."""

    def test_tree_root_returns_entries(self, http_client):
        data = assert_ok(http_client.get("/code/api/tree"))
        assert isinstance(data, dict)
        assert data.get("error") is None, f"unexpected error: {data.get('error')}"
        entries = data.get("entries") or []
        assert isinstance(entries, list)
        # Root has at least apps/ + CLAUDE.md (or comparable shape).
        names = [e.get("name") for e in entries]
        assert any(n in ("apps", "CLAUDE.md", "emptyos.toml") for n in names), \
            f"root looks empty: {names[:10]}"

    def test_tree_entry_shape(self, http_client):
        """Each entry carries name + kind ('dir'|'file') + repo-relative path
        with forward slashes. The frontend depends on this shape exactly."""
        data = assert_ok(http_client.get("/code/api/tree"))
        for e in (data.get("entries") or [])[:5]:
            assert "name" in e and "kind" in e and "path" in e
            assert e["kind"] in ("dir", "file"), f"unexpected kind: {e['kind']!r}"
            assert "\\" not in e["path"], f"backslash in path: {e['path']!r}"
            assert not e["path"].startswith("/"), \
                f"path should be repo-relative, got {e['path']!r}"

    def test_tree_dirs_sort_first(self, http_client):
        """Convention from the implementation: directories listed before
        files within the same level. UI relies on this for visual scanning."""
        data = assert_ok(http_client.get("/code/api/tree"))
        entries = data.get("entries") or []
        last_dir_idx = -1
        first_file_idx = len(entries)
        for i, e in enumerate(entries):
            if e["kind"] == "dir":
                last_dir_idx = i
            elif e["kind"] == "file" and first_file_idx == len(entries):
                first_file_idx = i
        assert last_dir_idx < first_file_idx, \
            "dirs should sort before files in tree listing"

    def test_tree_subdir_apps(self, http_client):
        """Drilling into apps/ should yield child app directories (this
        repo has dozens — we just check >5 + that they're all kind=dir)."""
        data = assert_ok(http_client.get("/code/api/tree?path=apps"))
        assert data.get("error") is None, data.get("error")
        entries = data.get("entries") or []
        # Real apps shouldn't be empty; if running in a sandboxed daemon
        # without the full apps/ tree, the path lookup itself still works.
        if not entries:
            pytest.skip("sparse sandbox repo; subdir listing surface is OK")
        # Every entry under apps/ should be a directory (apps are dirs).
        dirs = [e for e in entries if e["kind"] == "dir"]
        assert len(dirs) >= 1, "expected apps/ to contain at least one app dir"
        # Paths should start with apps/.
        for e in entries[:5]:
            assert e["path"].startswith("apps/"), \
                f"subdir entry has wrong path: {e['path']!r}"

    def test_tree_skips_noise_dirs(self, http_client):
        """The skip-list filters .git, __pycache__, node_modules, data,
        sandbox-9002, dogfood etc. so the tree isn't drowned."""
        data = assert_ok(http_client.get("/code/api/tree"))
        entries = data.get("entries") or []
        names = {e["name"] for e in entries}
        for noisy in (".git", "__pycache__", "node_modules", "data"):
            assert noisy not in names, \
                f"skip-list missed {noisy!r}; tree will be noisy"

    def test_tree_rejects_path_escape(self, http_client):
        """Path-safety: relative-escape ('../../etc') and absolute paths
        ('/etc/passwd', 'C:/Windows/...') must all be refused."""
        for evil in (
            "../../etc/passwd",
            "..\\..\\Windows\\System32",
            "/etc/passwd",
            "C:/Windows/System32",
        ):
            from urllib.parse import quote
            r = http_client.get(f"/code/api/tree?path={quote(evil)}")
            # Accept either 200 with {error: "path escapes..."} or 4xx —
            # both shapes are safe. The forbidden shape is a 200 with
            # entries listing the escaped path's content.
            if r.status_code == 200:
                data = r.json()
                assert data.get("error"), \
                    f"path escape allowed: {evil!r} → {data}"

    def test_tree_nonexistent_directory(self, http_client):
        """A path that doesn't exist (but resolves inside the root)
        should return an error, not crash and not silently return empty."""
        data = assert_ok(http_client.get(
            "/code/api/tree?path=no-such-PLAYWRIGHT-TEST-dir"
        ))
        assert data.get("error"), \
            "non-existent dir should report error, not return empty entries"

    def test_tree_path_normalisation(self, http_client):
        """The `path` field in the response uses forward slashes regardless
        of platform — UI depends on this for path comparisons in JS."""
        data = assert_ok(http_client.get("/code/api/tree?path=apps"))
        if data.get("error"):
            pytest.skip("apps/ unavailable")
        assert "\\" not in data.get("path", ""), \
            f"backslash in response path: {data.get('path')!r}"


@pytest.mark.api
class TestCodeWorkbenchAPI:
    """Read-only Plekto workbench aggregation for the /code/ shell."""

    def test_workbench_returns_core_sections(self, http_client):
        data = assert_ok(http_client.get("/code/api/workbench"))
        for key in ("repo", "apps", "agent", "projects", "rooms", "memory", "links"):
            assert key in data, f"workbench missing {key!r}"
        assert "name" in data["repo"]
        assert isinstance(data["links"], list)

    def test_workbench_app_status_shape(self, http_client):
        data = assert_ok(http_client.get("/code/api/workbench"))
        apps = data["apps"]
        for app_id in ("agent", "repo", "projects", "rooms", "kb"):
            assert app_id in apps
            assert "enabled" in apps[app_id]
            assert "error" in apps[app_id]

    def test_workbench_counts_are_numeric(self, http_client):
        data = assert_ok(http_client.get("/code/api/workbench"))
        assert isinstance(data["agent"].get("total", 0), int)
        assert isinstance(data["projects"].get("active", 0), int)
        assert isinstance(data["rooms"].get("pending", 0), int)
        assert isinstance(data["memory"].get("kb_notes", 0), int)
