"""System app tests: Forge — native-project scaffold + tracking.

The POST /forge/api/projects route actually shells out to
`npm create tauri-app@latest`, which downloads ~hundreds of MB and takes
~2 minutes. We mirror fix-agent's CI posture: cover read-side endpoints
and invalid-input paths in CI, gate the real scaffold/dev/build flow
behind @pytest.mark.slow.

The identifier-helper unit tests below run in CI — they exercise the
pure module without touching the daemon.
"""

from __future__ import annotations

import time

import pytest

from helpers import TEST_PREFIX, assert_dict_response


def _unique_id(suffix: str = "") -> str:
    """Returns a lowercased slug-safe id with the TEST_PREFIX baked in.
    Cleanup sweeps by `TEST_PREFIX.lower() in id`."""
    return f"{TEST_PREFIX.lower()}forge-{int(time.time() * 1000)}{suffix}"


@pytest.mark.api
class TestForgeTargets:
    def test_targets_endpoint(self, http_client):
        data = assert_dict_response(http_client.get("/forge/api/targets"))
        assert "targets" in data and isinstance(data["targets"], list)
        ids = [t.get("id") for t in data["targets"]]
        assert "tauri" in ids, f"Tauri target missing: {ids}"
        assert "cli" in ids, f"CLI target missing: {ids}"

    def test_tauri_preflight_shape(self, http_client):
        data = http_client.get("/forge/api/targets").json()
        tauri = next(t for t in data["targets"] if t["id"] == "tauri")
        assert tauri["coming_soon"] is False
        assert isinstance(tauri["preflight"], list)
        names = {c["name"] for c in tauri["preflight"]}
        assert names >= {"cargo", "rustc", "node", "npm"}, (
            f"missing preflight checks: {names}"
        )
        # Shape: every check has ok + detail.
        for c in tauri["preflight"]:
            assert "ok" in c and "detail" in c

    def test_cli_preflight_shape(self, http_client):
        data = http_client.get("/forge/api/targets").json()
        cli = next(t for t in data["targets"] if t["id"] == "cli")
        assert cli["coming_soon"] is False
        assert isinstance(cli["preflight"], list)
        names = {c["name"] for c in cli["preflight"]}
        assert names >= {"python", "pip", "git"}, (
            f"missing preflight checks: {names}"
        )

    def test_coming_soon_targets_listed(self, http_client):
        data = http_client.get("/forge/api/targets").json()
        coming = [t for t in data["targets"] if t.get("coming_soon")]
        assert coming, "expected at least one coming-soon placeholder"

    def test_default_root_present(self, http_client):
        data = http_client.get("/forge/api/targets").json()
        assert isinstance(data.get("default_root"), str) and data["default_root"]


@pytest.mark.api
class TestForgeProjectsListing:
    """Read-side + error paths only. Scaffold tests live in
    TestForgeRealScaffold (manual)."""

    def test_list_returns_dict_with_projects(self, http_client):
        data = assert_dict_response(http_client.get("/forge/api/projects"))
        assert "projects" in data and isinstance(data["projects"], list)

    def test_get_unknown_returns_error(self, http_client):
        r = http_client.get("/forge/api/projects/zzz-does-not-exist-pid")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_create_rejects_unknown_target(self, http_client):
        pid = _unique_id("-bad")
        r = http_client.post(
            "/forge/api/projects",
            json={"target": "imaginary", "name": pid, "id": pid},
        )
        body = r.json()
        assert "error" in body and "unknown target" in body["error"].lower()

    def test_create_rejects_blank_name(self, http_client):
        r = http_client.post(
            "/forge/api/projects",
            json={"target": "tauri", "name": "", "id": ""},
        )
        body = r.json()
        assert "error" in body

    def test_dev_unknown_project_errors(self, http_client):
        r = http_client.post("/forge/api/projects/zzz-no-pid/dev")
        body = r.json()
        assert "error" in body

    def test_dev_stop_unknown_project_is_ok(self, http_client):
        """Stop is idempotent — stopping something not running is fine."""
        r = http_client.post("/forge/api/projects/zzz-no-pid/dev/stop")
        body = r.json()
        assert body.get("ok") is True

    def test_tail_unknown_project_returns_not_running(self, http_client):
        r = http_client.get("/forge/api/projects/zzz-no-pid/dev/tail")
        body = r.json()
        assert body.get("running") is False

    def test_build_unknown_project_errors(self, http_client):
        r = http_client.post("/forge/api/projects/zzz-no-pid/build")
        body = r.json()
        assert "error" in body

    def test_set_field_rejects_non_settable(self, http_client):
        r = http_client.post(
            "/forge/api/projects/zzz-no-pid/field",
            json={"field": "version", "value": "9.9.9"},
        )
        body = r.json()
        assert "error" in body

    def test_delete_unknown_returns_error(self, http_client):
        r = http_client.request("DELETE", "/forge/api/projects/zzz-no-pid")
        body = r.json()
        assert "error" in body

    def test_release_blank_version_errors(self, http_client):
        r = http_client.post(
            "/forge/api/projects/zzz-no-pid/release",
            json={"version": ""},
        )
        body = r.json()
        assert "error" in body and "version" in body["error"].lower()

    def test_release_unknown_project_errors(self, http_client):
        r = http_client.post(
            "/forge/api/projects/zzz-no-pid/release",
            json={"version": "1.0.0"},
        )
        body = r.json()
        assert "error" in body


@pytest.mark.api
class TestTauriIdentifier:
    """Pure unit test on the identifier helper — no daemon needed."""

    def test_identifier_basic(self):
        from apps.forge.targets.tauri import _identifier_for
        assert _identifier_for("hello-eos") == "com.eos.helloeos"

    def test_identifier_strips_punctuation(self):
        from apps.forge.targets.tauri import _identifier_for
        assert _identifier_for("my_app.123") == "com.eos.myapp123"

    def test_identifier_leading_digit_prefixed(self):
        """Tauri requires segment to start with a letter — leading digits get prefixed."""
        from apps.forge.targets.tauri import _identifier_for
        out = _identifier_for("123abc")
        # Each segment after split must start with a letter.
        for seg in out.split("."):
            assert seg[0].isalpha(), f"segment '{seg}' starts with non-letter"

    def test_identifier_empty_fallback(self):
        from apps.forge.targets.tauri import _identifier_for
        out = _identifier_for("--")
        assert out.endswith(".app")


@pytest.mark.api
class TestTauriSemver:
    """Semver validation for the release verb — pure function."""

    def test_accepts_canonical(self):
        from apps.forge.targets.tauri import _validate_semver
        for v in ("1.2.3", "0.0.1", "10.20.30"):
            assert _validate_semver(v) is None, f"rejected valid: {v}"

    def test_accepts_prerelease(self):
        from apps.forge.targets.tauri import _validate_semver
        for v in ("1.2.3-rc.1", "1.0.0-beta", "2.0.0-alpha.5"):
            assert _validate_semver(v) is None, f"rejected valid prerelease: {v}"

    def test_rejects_empty(self):
        from apps.forge.targets.tauri import _validate_semver
        assert _validate_semver("") is not None
        assert "empty" in _validate_semver("").lower()

    def test_rejects_v_prefix(self):
        from apps.forge.targets.tauri import _validate_semver
        msg = _validate_semver("v1.2.3")
        assert msg is not None and "'v'" in msg

    def test_rejects_non_semver(self):
        from apps.forge.targets.tauri import _validate_semver
        for v in ("1.2", "1.2.3.4", "abc", "1.2-3"):
            assert _validate_semver(v) is not None, f"accepted invalid: {v}"


@pytest.mark.api
class TestTauriVersionBumpers:
    """JSON + Cargo.toml in-place version bumpers — touch only the right field."""

    def test_json_bump_preserves_sibling_fields(self, tmp_path):
        import json
        from apps.forge.targets.tauri import _bump_json_version

        p = tmp_path / "tauri.conf.json"
        p.write_text(
            json.dumps({
                "productName": "hello", "version": "0.0.1",
                "identifier": "com.eos.hello",
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        assert _bump_json_version(p, "0.2.0") is True
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["version"] == "0.2.0"
        assert data["productName"] == "hello"
        assert data["identifier"] == "com.eos.hello"
        # Trailing newline preserved (release diff stays minimal).
        assert p.read_text(encoding="utf-8").endswith("\n")

    def test_json_bump_idempotent(self, tmp_path):
        import json
        from apps.forge.targets.tauri import _bump_json_version
        p = tmp_path / "package.json"
        p.write_text(json.dumps({"name": "x", "version": "1.2.3"}, indent=2), encoding="utf-8")
        assert _bump_json_version(p, "1.2.3") is False  # no-op

    def test_cargo_bump_only_package_section(self, tmp_path):
        from apps.forge.targets.tauri import _bump_cargo_toml_version
        p = tmp_path / "Cargo.toml"
        p.write_text(
            '[package]\nname = "hello"\nversion = "0.1.0"\nedition = "2021"\n'
            '\n[dependencies]\nserde = { version = "1.0" }\ntauri = { version = "2.0" }\n',
            encoding="utf-8",
        )
        assert _bump_cargo_toml_version(p, "0.5.0") is True
        text = p.read_text(encoding="utf-8")
        # Package version bumped:
        assert 'version = "0.5.0"' in text
        # Dependencies NOT bumped:
        assert 'serde = { version = "1.0" }' in text
        assert 'tauri = { version = "2.0" }' in text

    def test_cargo_bump_idempotent(self, tmp_path):
        from apps.forge.targets.tauri import _bump_cargo_toml_version
        p = tmp_path / "Cargo.toml"
        p.write_text('[package]\nname = "x"\nversion = "1.0.0"\n', encoding="utf-8")
        assert _bump_cargo_toml_version(p, "1.0.0") is False

    def test_cargo_bump_missing_package_returns_false(self, tmp_path):
        from apps.forge.targets.tauri import _bump_cargo_toml_version
        p = tmp_path / "Cargo.toml"
        p.write_text('[workspace]\nmembers = ["a", "b"]\n', encoding="utf-8")
        assert _bump_cargo_toml_version(p, "1.0.0") is False


@pytest.mark.api
class TestCliTargetHelpers:
    """CLI target shape — package-name slug + pyproject bumper + scaffold tree."""

    def test_package_name_slug(self):
        from apps.forge.targets.cli import _package_name
        assert _package_name("hello-eos") == "hello_eos"
        assert _package_name("my_app") == "my_app"
        assert _package_name("UPPER-Case") == "upper_case"
        assert _package_name("a.b.c") == "a_b_c"

    def test_package_name_leading_digit_prefixed(self):
        from apps.forge.targets.cli import _package_name
        out = _package_name("123abc")
        assert out[0].isalpha(), f"package name must not start with digit: {out}"
        assert "123abc" in out  # original chars preserved

    def test_package_name_empty_fallback(self):
        from apps.forge.targets.cli import _package_name
        assert _package_name("") == "app"
        assert _package_name("---") == "app"

    def test_pyproject_bumper(self, tmp_path):
        from apps.forge.targets.cli import _bump_pyproject_version
        p = tmp_path / "pyproject.toml"
        p.write_text(
            '[project]\nname = "x"\nversion = "0.1.0"\ndescription = "y"\n',
            encoding="utf-8",
        )
        assert _bump_pyproject_version(p, "1.0.0") is True
        assert 'version = "1.0.0"' in p.read_text(encoding="utf-8")
        assert _bump_pyproject_version(p, "1.0.0") is False  # idempotent

    def test_scaffold_tree_writes_expected_files(self, tmp_path):
        from apps.forge.targets import TARGETS, ScaffoldCtx
        cli = TARGETS["cli"]
        ctx = ScaffoldCtx(project_id="my-cli", name="My CLI", root=tmp_path)
        cli._write_tree(tmp_path / "my-cli", ctx)
        repo = tmp_path / "my-cli"
        expected = {
            "pyproject.toml",
            "README.md",
            ".gitignore",
            "src/my_cli/__init__.py",
            "src/my_cli/cli.py",
            "tests/test_cli.py",
            ".github/workflows/test.yml",
        }
        actual = {
            str(p.relative_to(repo)).replace("\\", "/")
            for p in repo.rglob("*") if p.is_file()
        }
        assert expected <= actual, f"missing files: {expected - actual}"

    def test_scaffold_pyproject_has_console_script(self, tmp_path):
        from apps.forge.targets import TARGETS, ScaffoldCtx
        cli = TARGETS["cli"]
        ctx = ScaffoldCtx(project_id="my-cli", name="My CLI", root=tmp_path)
        cli._write_tree(tmp_path / "my-cli", ctx)
        pp = (tmp_path / "my-cli" / "pyproject.toml").read_text(encoding="utf-8")
        assert 'my-cli = "my_cli.cli:main"' in pp


@pytest.mark.api
class TestForgeReplaceSection:
    """The note-section replacer used by update_design."""

    def test_replace_existing_section(self):
        from apps.forge.app import _replace_section
        md = (
            "## Design\n\nold body\n\n## Changelog\n\n- 2026 done\n"
        )
        out = _replace_section(md, "Design", "new body text")
        assert "new body text" in out
        assert "old body" not in out
        # Changelog preserved:
        assert "Changelog" in out and "2026 done" in out

    def test_append_missing_section(self):
        from apps.forge.app import _replace_section
        md = "# Heading\n\nSome prose.\n"
        out = _replace_section(md, "Design", "first design notes")
        assert "## Design" in out
        assert "first design notes" in out


@pytest.mark.slow
class TestForgeRealScaffold:
    """Real Tauri scaffold — pulls hundreds of MB, ~2 min wall-clock. Gated
    behind -m slow so CI skips it.

        python -m pytest tests/test_sys_forge.py -m slow -v
    """

    def test_full_scaffold_round_trip(self, http_client):
        pid = _unique_id("-real")
        r = http_client.post(
            "/forge/api/projects",
            json={"target": "tauri", "name": "Real Tauri Scaffold", "id": pid},
            timeout=720,
        )
        body = r.json()
        assert body.get("ok") is True, f"scaffold failed: {body}"

        # Detail view shows the project.
        detail = http_client.get(f"/forge/api/projects/{pid}").json()
        assert detail["frontmatter"]["id"] == pid
        assert detail["frontmatter"]["status"] == "scaffolded"

        # Listing contains it.
        listing = http_client.get("/forge/api/projects").json()
        assert any(p["id"] == pid for p in listing["projects"])
