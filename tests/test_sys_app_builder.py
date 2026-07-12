"""System app tests: App Builder — read-side endpoints + invalid-input rejection.

The /api/run endpoint spawns claude-cli in a worktree (heavy, not for CI), and
the /merge endpoint mutates git state. These tests cover the surfaces that are
safe to hit from CI: status shape, runs list, not-found and invalid-input
paths, and the UI's basic load. Real scaffold-loop smoke is manual + Phase 2
will add the scaffold-smoke dogfood scenario for end-to-end coverage.

Modelled after tests/test_sys_fix_agent.py — app-builder is fix-agent's sibling
and inherits the same validation-shape contract.
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestAppBuilderAPI:
    def test_status_shape(self, http_client):
        """Status answers regardless of worktree / claude-cli presence."""
        data = assert_dict_response(
            http_client.get("/app-builder/api/status"),
            required_keys=["repo_root", "worktree_path", "worktree_exists",
                           "claude_available", "runs", "busy"],
        )
        assert isinstance(data["runs"], list)
        assert isinstance(data["worktree_exists"], bool)
        assert isinstance(data["busy"], bool)

    def test_runs_list(self, http_client):
        data = assert_dict_response(
            http_client.get("/app-builder/api/runs"),
            required_keys=["runs"],
        )
        assert isinstance(data["runs"], list)

    def test_run_detail_not_found(self, http_client):
        resp = http_client.get("/app-builder/api/runs/zzz-nonexistent")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_run_invalid_id_traversal(self, http_client):
        """Path-traversal-shaped run_ids must be rejected — either by the router
        (404 after %2F is URL-decoded to / and the path doesn't match) or by the
        handler (200 with JSON error). Both are valid defenses; what's banned is
        500 or any 2xx-success that returns real data."""
        resp = http_client.get("/app-builder/api/runs/..%2Fetc")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert "error" in resp.json()

    def test_run_missing_spec_path(self, http_client):
        resp = http_client.post("/app-builder/api/run", json={})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_run_invalid_spec_path_absolute(self, http_client):
        """Absolute paths and Windows-drive paths must be rejected — spec_path is
        vault-relative by contract."""
        for bad in ("/abs/path.md", "C:/foo.md", "D:\\foo.md"):
            resp = http_client.post("/app-builder/api/run", json={"spec_path": bad})
            assert resp.status_code == 200
            assert "error" in resp.json(), f"accepted absolute spec_path: {bad!r}"

    def test_run_invalid_spec_path_traversal(self, http_client):
        """`..` segments must be rejected."""
        for bad in ("../etc/passwd.md", "30_Resources/../escape.md", "../../x.md"):
            resp = http_client.post("/app-builder/api/run", json={"spec_path": bad})
            assert resp.status_code == 200
            assert "error" in resp.json(), f"accepted traversal-shaped spec_path: {bad!r}"

    def test_run_invalid_spec_path_not_md(self, http_client):
        """Non-.md paths must be rejected — spec notes are markdown."""
        for bad in ("foo.txt", "30_Resources/EmptyOS/grill/notes.json", "spec"):
            resp = http_client.post("/app-builder/api/run", json={"spec_path": bad})
            assert resp.status_code == 200
            assert "error" in resp.json(), f"accepted non-md spec_path: {bad!r}"

    def test_run_null_spec_path_does_not_crash(self, http_client):
        """{"spec_path": null} is the JSON shape that crashes ``dict.get(K, "").strip()``
        because the default fires only on *absent* keys, not present-but-None.
        The handler must coerce None → "" before calling .strip()."""
        for body in ({"spec_path": None}, {"spec_path": ""}, None):
            resp = http_client.post("/app-builder/api/run", json=body)
            assert resp.status_code == 200, (
                f"body={body!r} returned {resp.status_code} (expected 200 with error body)"
            )
            data = resp.json()
            assert "error" in data, f"body={body!r} did not return a structured error: {data}"

    def test_merge_unknown_run(self, http_client):
        resp = http_client.post("/app-builder/api/runs/zzz-nonexistent/merge")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_verify_unknown_run(self, http_client):
        resp = http_client.post("/app-builder/api/runs/zzz-nonexistent/verify")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_revert_unknown_run(self, http_client):
        resp = http_client.post("/app-builder/api/runs/zzz-nonexistent/revert")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_discard_unknown_run(self, http_client):
        resp = http_client.post("/app-builder/api/runs/zzz-nonexistent/discard")
        assert resp.status_code == 200
        assert "error" in resp.json()


@pytest.mark.api
class TestAppBuilderAcceptanceParser:
    """Direct unit tests for the acceptance-criteria endpoint extractor —
    no daemon needed, calls the module-level helper via a regex re-export.

    We can't import the app module normally (dash in 'app-builder' isn't a
    valid Python identifier). Instead we re-implement the helper by reusing
    the same regex pattern, asserted to match the one in app.py."""

    @pytest.fixture
    def extract(self):
        import re
        # Mirror of _ACCEPTANCE_ENDPOINT_RE + extract_acceptance_endpoints
        # from apps/app-builder/app.py. If the app's regex changes, update
        # here too — covered by test_regex_matches_app.
        ENDPOINT_RE = re.compile(
            r"^\s*[-*]\s+`?(GET|POST|PUT|DELETE|PATCH)\s+(/\S+?)`?(?:\s|$)",
            re.MULTILINE | re.IGNORECASE,
        )

        def _extract(md):
            # Strip frontmatter (same shape as emptyos.sdk.utils.strip_frontmatter)
            if md.startswith("---\n"):
                end = md.find("\n---\n", 4)
                body = md[end + 5:] if end >= 0 else md
            else:
                body = md
            m = re.search(
                r"^##\s+Acceptance criteria.*?(?=^##\s|\Z)",
                body, re.DOTALL | re.MULTILINE | re.IGNORECASE,
            )
            section = m.group(0) if m else body
            return [
                (mm.group(1).upper(), mm.group(2).rstrip(".,;:"))
                for mm in ENDPOINT_RE.finditer(section)
            ]
        return _extract

    def test_regex_matches_app(self):
        """Guard: the regex in this test file must stay in sync with the app's.
        If this fails, both definitions need updating in lockstep.
        The regex lives in constants.py (decomposition target — was app.py)."""
        from helpers import app_path
        constants_py = (app_path("app-builder") / "constants.py").read_text(encoding="utf-8")
        assert r'r"^\s*[-*]\s+`?(GET|POST|PUT|DELETE|PATCH)\s+(/\S+?)`?(?:\s|$)"' in constants_py

    def test_extracts_plain_bullets(self, extract):
        md = """---
app_id: foo
---

## Acceptance criteria
- GET /foo/api/status
- POST /foo/api/add
- DELETE /foo/api/items/{id}
"""
        assert extract(md) == [
            ("GET", "/foo/api/status"),
            ("POST", "/foo/api/add"),
            ("DELETE", "/foo/api/items/{id}"),
        ]

    def test_extracts_backtick_wrapped(self, extract):
        md = """## Acceptance criteria
- `GET /foo/api/status`
- `POST /foo/api/add`
"""
        result = extract(md)
        assert ("GET", "/foo/api/status") in result
        assert ("POST", "/foo/api/add") in result

    def test_ignores_prose_lines(self, extract):
        md = """## Acceptance criteria
- The app must respond within 200ms
- GET /foo/api/status returns 2xx
- the route should not 404
"""
        result = extract(md)
        # Only the bullet that starts with a verb is matched.
        assert result == [("GET", "/foo/api/status")]

    def test_returns_empty_when_section_missing(self, extract):
        md = "## Why\nBecause we need it.\n"
        # No acceptance section → scans the whole body, finds nothing.
        assert extract(md) == []

    def test_handles_asterisk_bullets(self, extract):
        md = """## Acceptance criteria
* GET /foo/api/x
* POST /foo/api/y
"""
        result = extract(md)
        assert ("GET", "/foo/api/x") in result
        assert ("POST", "/foo/api/y") in result


@pytest.mark.api
class TestAppBuilderDraftEndpoint:
    """Draft endpoint validation — fires LLM only when given a valid project,
    so we test the rejection paths here. The happy path needs a real project
    note in the vault + an LLM call, which is expensive and lives in the
    manual e2e smoke below."""

    def test_draft_missing_project_id(self, http_client):
        resp = http_client.post("/app-builder/api/draft_from_project", json={})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_draft_invalid_project_id(self, http_client):
        for bad in ("Foo", "../etc", "with space", "UPPER", ""):
            resp = http_client.post(
                "/app-builder/api/draft_from_project",
                json={"project_id": bad},
            )
            assert resp.status_code == 200
            assert "error" in resp.json(), f"accepted invalid project_id: {bad!r}"

    def test_draft_nonexistent_project(self, http_client):
        """Valid-shape project_id but no such project — clean error, no LLM call.
        Projects' get_project_content returns empty content for missing files;
        we map that to a 'not found or empty' error."""
        resp = http_client.post(
            "/app-builder/api/draft_from_project",
            json={"project_id": "zzz-no-such-project-xyz"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert "not found" in data["error"].lower() or "empty" in data["error"].lower()


@pytest.mark.skip(reason="end-to-end loop spawns claude-cli + costs tokens; run manually after Phase 3 install")
class TestAppBuilderEndToEnd:
    """End-to-end smoke for the full Phase 1+2+3 loop.

    Deliberately skip-marked: each test spawns claude-cli (~$0.10-0.50) and
    requires a clean git state + the dogfood daemon at :9001. Run by hand
    after a fresh Phase 3 install to confirm the loop closes:

        python -m pytest tests/test_sys_app_builder.py::TestAppBuilderEndToEnd -v --no-skip

    (Use a custom conftest marker to override the skip when ready.)
    """

    def test_draft_to_scaffold_to_merge_to_verify(self, http_client):
        # 1. Seed a tiny project in the vault with status: spec-ready
        # 2. POST /api/draft_from_project — wait for spec_path in response
        # 3. POST /api/run with that spec_path — wait for status=ready
        # 4. POST /api/runs/{rid}/merge — assert ok
        # 5. (Manual: restart + install via store)
        # 6. POST /api/runs/{rid}/verify — assert verified
        # 7. POST /api/runs/{rid}/discard for the seeded project
        # Stub — implement when the e2e harness is needed.
        pytest.skip("manual e2e — see docstring")


@pytest.mark.interactive
class TestAppBuilderUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("app-builder")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("app-builder")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical
