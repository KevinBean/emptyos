"""System tests: Skill registry — list, get, by_slash, rescan, frontmatter parsing.

Two layers:
  TestSkillAPI — HTTP surface via the running daemon.
  TestSkillParser — pure-function tests on _parse_yaml_lite + _parse_file;
                    no daemon required. Imported lazily so the file still
                    collects even when the kernel can't boot.
"""

from pathlib import Path

import pytest

from helpers import assert_ok, app_path


@pytest.mark.api
class TestSkillAPI:
    def test_list_returns_skills(self, http_client):
        data = assert_ok(http_client.get("/skill/api/list"))
        assert isinstance(data, dict)
        skills = data.get("skills") or []
        assert isinstance(skills, list)
        names = [s.get("name") for s in skills]
        assert "wrapup" in names, f"wrapup skill missing: {names}"

    def test_list_omits_body(self, http_client):
        """List is for catalog UIs — bodies are heavy and unwanted there."""
        data = assert_ok(http_client.get("/skill/api/list"))
        for s in data.get("skills") or []:
            assert "body" not in s, f"list returned body for {s.get('name')}"

    def test_list_entries_carry_summary_fields(self, http_client):
        """Catalog UIs need name + slash + description + source per entry —
        no entry should be missing any of these."""
        data = assert_ok(http_client.get("/skill/api/list"))
        for s in data.get("skills") or []:
            for k in ("name", "slash", "description", "source"):
                assert k in s, f"skill list missing {k!r}: {s}"
            assert s["slash"].startswith("/"), \
                f"slash should be leading-slash form: {s['slash']!r}"

    def test_get_returns_full_record(self, http_client):
        data = assert_ok(http_client.get("/skill/api/get/wrapup"))
        assert isinstance(data, dict)
        assert not data.get("error"), f"wrapup not found: {data}"
        assert data.get("name") == "wrapup"
        assert data.get("slash") == "/wrapup"
        assert isinstance(data.get("body"), str) and len(data["body"]) > 50
        assert "server_actions" in data
        sa = data["server_actions"]
        assert "repo" in sa
        assert "read" in sa["repo"] and "grep" in sa["repo"]
        assert "journal" in sa
        assert "add_entry" in sa["journal"]

    def test_get_source_path_is_repo_relative(self, http_client):
        """The `source` field on a skill record must be a repo-relative
        path with forward slashes so UIs (and the multi-platform doc
        renderer) can link to it without OS-specific munging."""
        data = assert_ok(http_client.get("/skill/api/get/wrapup"))
        src = data.get("source", "")
        # Repo-relative + anchored at apps/; skill may live anywhere in the
        # track tree (today apps/extension/dev/skill/library/), so assert the
        # shape, not a fixed flat location.
        assert src.startswith("apps/") and "skill/library/" in src, \
            f"unexpected source path: {src!r}"
        assert "\\" not in src, f"backslash in source path: {src!r}"

    def test_get_missing_returns_error(self, http_client):
        data = assert_ok(http_client.get(
            "/skill/api/get/PLAYWRIGHT-TEST-no-such-skill"
        ))
        assert data.get("error"), "missing skill should report error"

    def test_rescan(self, http_client):
        data = assert_ok(http_client.post("/skill/api/rescan"))
        assert data.get("ok") is True
        assert isinstance(data.get("count"), int)
        assert data["count"] >= 1

    def test_rescan_idempotent(self, http_client):
        """Calling rescan twice should return the same count + not duplicate
        registry entries. Catches a class of bug where _scan() appends
        instead of replaces."""
        first = assert_ok(http_client.post("/skill/api/rescan")).get("count")
        second = assert_ok(http_client.post("/skill/api/rescan")).get("count")
        assert first == second, \
            f"rescan changed count without source change: {first} → {second}"


# ── Pure-function tests on the parser (no daemon needed) ────────────────

def _load_parser():
    """Lazy-import the parser. Bypasses the BaseApp/SDK chain by stubbing
    emptyos.sdk so a bare import doesn't try to pull the kernel in."""
    import importlib.util
    import sys
    import types
    if "emptyos.sdk" not in sys.modules:
        sdk = types.ModuleType("emptyos.sdk")
        sdk.BaseApp = type("BaseApp", (), {})
        sdk.cli_command = lambda *a, **k: (lambda f: f)
        sdk.web_route = lambda *a, **k: (lambda f: f)
        sys.modules.setdefault("emptyos", types.ModuleType("emptyos"))
        sys.modules["emptyos.sdk"] = sdk
    spec = importlib.util.spec_from_file_location(
        "skill_app_under_test",
        app_path("skill") / "app.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSkillParser:
    """Pure-function tests on the YAML-lite parser. These run without
    a daemon so they catch regressions even when :9000 is down."""

    def test_parser_handles_inline_array(self):
        mod = _load_parser()
        fm = mod._parse_yaml_lite("server_actions: [a, b, c]")
        # Top-level inline array on a single key — `_coerce` returns a list.
        assert fm.get("server_actions") == ["a", "b", "c"], fm

    def test_parser_handles_nested_once_dict(self):
        mod = _load_parser()
        text = "server_actions:\n  repo: [read, grep]\n  journal: [add_entry]"
        fm = mod._parse_yaml_lite(text)
        sa = fm.get("server_actions")
        assert isinstance(sa, dict)
        assert sa.get("repo") == ["read", "grep"]
        assert sa.get("journal") == ["add_entry"]

    def test_parser_strips_quotes(self):
        mod = _load_parser()
        fm = mod._parse_yaml_lite('name: "hello"\ndescription: \'world\'')
        assert fm.get("name") == "hello"
        assert fm.get("description") == "world"

    def test_parser_tolerates_blank_and_comment_lines(self):
        mod = _load_parser()
        fm = mod._parse_yaml_lite("\n# a comment\nname: ok\n\n# another\n")
        assert fm.get("name") == "ok"

    def test_frontmatter_regex_extracts_body(self):
        """The full _parse_file path requires --- delimiters; without them
        _FRONTMATTER_RE.match returns None and the loader skips the file."""
        mod = _load_parser()
        m = mod._FRONTMATTER_RE.match(
            "---\nname: foo\n---\nbody here\nmore\n"
        )
        assert m is not None
        assert "name: foo" in m.group("fm")
        assert "body here" in m.group("body")
