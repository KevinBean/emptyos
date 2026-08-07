"""System tests: publish app — static-mirror mode.

Static-mirror is the deploy mode for pre-built HTML sites (brand/plekto/site/
→ plekto.dev via Cloudflare Pages) that don't go through the vault → markdown →
HTML builder. These tests verify the mode dispatches correctly, mirror copy
preserves excluded files (Cloudflare's wrangler.jsonc), and the deploy path
refuses --force.

Unit-level tests using the internal helpers — no real git pushes, no real
network. The end-to-end deploy contract is verified manually via:
    POST /publish/api/deploy {"site_id": "plekto"}
after a daemon restart loads this code.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import app_path

# These tests instantiate the app directly rather than going over HTTP so we
# can exercise the mirror + git helpers without a full kernel boot. They run
# without the daemon — no marker needed (the existing tier markers in
# pyproject.toml all imply a running daemon).


@pytest.fixture
def publish_app(tmp_path, monkeypatch):
    """Spin up just enough of PublishApp to call the static-mirror methods.

    Patches data_dir + kernel.config.path so site_dir + source resolution land
    in tmp_path. Doesn't boot the full kernel.
    """
    import importlib.util
    import sys
    import types

    apps_pkg = types.ModuleType("apps")
    apps_pkg.__path__ = [str(Path(__file__).resolve().parent.parent / "apps")]
    sys.modules.setdefault("apps", apps_pkg)
    pub_pkg = types.ModuleType("apps.publish")
    pub_pkg.__path__ = [str(app_path("publish"))]
    sys.modules.setdefault("apps.publish", pub_pkg)

    pub_root = app_path("publish")
    for sub in ("prompts", "templates", "builder", "chatbot", "media", "deploy", "writer"):
        spec = importlib.util.spec_from_file_location(
            f"apps.publish.{sub}", str(pub_root / f"{sub}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"apps.publish.{sub}"] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location(
        "apps.publish.app", str(pub_root / "app.py")
    )
    app_mod = importlib.util.module_from_spec(spec)
    sys.modules["apps.publish.app"] = app_mod
    spec.loader.exec_module(app_mod)

    PublishApp = app_mod.PublishApp

    # Build a minimal stub for what the methods touch.
    app = PublishApp.__new__(PublishApp)
    app.data_dir = tmp_path / "data"
    app.data_dir.mkdir(parents=True)

    fake_kernel = types.SimpleNamespace()
    fake_config = types.SimpleNamespace()
    fake_config.path = tmp_path / "emptyos.toml"
    fake_kernel.config = fake_config
    app.kernel = fake_kernel

    return app


# ─── Source resolution ─────────────────────────────────────────────

class TestResolveSource:
    def test_relative_path_resolves_against_repo_root(self, publish_app, tmp_path):
        # source_repo_path is relative — should resolve against parent of config.path
        site = {"source_repo_path": "brand/foo"}
        resolved = publish_app._resolve_static_source(site)
        assert resolved == (tmp_path / "brand" / "foo").resolve()

    def test_absolute_path_passes_through(self, publish_app, tmp_path):
        abs_path = tmp_path / "somewhere" / "absolute"
        site = {"source_repo_path": str(abs_path)}
        assert publish_app._resolve_static_source(site) == abs_path.resolve()

    def test_missing_path_raises(self, publish_app):
        with pytest.raises(ValueError, match="source_repo_path"):
            publish_app._resolve_static_source({})


# ─── Mirror copy semantics ─────────────────────────────────────────

class TestMirrorToSiteDir:
    def test_first_run_copies_all_source_files(self, publish_app, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "index.html").write_text("<h1>hi</h1>")
        (source / "style.css").write_text("body{}")
        site_dir = tmp_path / "out"

        stats = publish_app._mirror_to_site_dir(source, site_dir, [])

        assert stats["copied"] == 2
        assert stats["deleted"] == 0
        assert (site_dir / "index.html").read_text() == "<h1>hi</h1>"
        assert (site_dir / "style.css").read_text() == "body{}"

    def test_deletes_target_files_missing_from_source(self, publish_app, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "index.html").write_text("new")
        site_dir = tmp_path / "out"
        site_dir.mkdir()
        (site_dir / "index.html").write_text("old")
        (site_dir / "obsolete.html").write_text("delete me")

        stats = publish_app._mirror_to_site_dir(source, site_dir, [])

        assert stats["copied"] == 1
        assert stats["deleted"] == 1
        assert (site_dir / "index.html").read_text() == "new"
        assert not (site_dir / "obsolete.html").exists()

    def test_exclude_files_preserved_in_target(self, publish_app, tmp_path):
        """The Cloudflare wrangler.jsonc case — preserve target-only files."""
        source = tmp_path / "src"
        source.mkdir()
        (source / "index.html").write_text("new")
        site_dir = tmp_path / "out"
        site_dir.mkdir()
        (site_dir / "wrangler.jsonc").write_text('{"name": "plekto"}')

        stats = publish_app._mirror_to_site_dir(
            source, site_dir, ["wrangler.jsonc"]
        )

        # wrangler.jsonc was in target, not in source, and is in exclude_files →
        # must NOT be deleted.
        assert (site_dir / "wrangler.jsonc").exists()
        assert (site_dir / "wrangler.jsonc").read_text() == '{"name": "plekto"}'
        assert stats["deleted"] == 0
        assert "wrangler.jsonc" in stats["preserved"]

    def test_git_dir_always_preserved(self, publish_app, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "index.html").write_text("hi")
        site_dir = tmp_path / "out"
        site_dir.mkdir()
        git_dir = site_dir / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main")

        publish_app._mirror_to_site_dir(source, site_dir, [])

        # .git is never touched even when not in exclude_files
        assert (git_dir / "HEAD").exists()

    def test_excluded_files_in_source_not_copied(self, publish_app, tmp_path):
        """Files matching exclude_files in source should NOT be copied to target.

        This means the README in brand/plekto/site/ (developer docs for the
        source repo) doesn't ship to plekto-dev/site as a deployed asset.
        """
        source = tmp_path / "src"
        source.mkdir()
        (source / "index.html").write_text("hi")
        (source / "README.md").write_text("# dev docs")
        site_dir = tmp_path / "out"

        publish_app._mirror_to_site_dir(source, site_dir, ["README.md"])

        assert (site_dir / "index.html").exists()
        assert not (site_dir / "README.md").exists()


# ─── Build dispatches on mode ──────────────────────────────────────

class TestBuildDispatch:
    def test_static_mirror_build_validates_source_and_skips_builder(
        self, publish_app, tmp_path
    ):
        source = tmp_path / "brand" / "test" / "site"
        source.mkdir(parents=True)
        (source / "index.html").write_text("hi")
        site = {
            "id": "test",
            "mode": "static-mirror",
            "source_repo_path": "brand/test/site",
        }

        stats = publish_app.build(site=site)

        assert stats["mode"] == "static-mirror"
        assert stats["status"] == "ready"
        assert stats["files"] == 1
        # No builder ran — site_dir should not have been touched
        assert not (publish_app.data_dir / "sites" / "test" / "site").exists()

    def test_static_mirror_build_errors_if_source_missing(
        self, publish_app, tmp_path
    ):
        site = {
            "id": "nope",
            "mode": "static-mirror",
            "source_repo_path": "brand/nonexistent/site",
        }
        result = publish_app.build(site=site)
        assert "error" in result
        assert "not found" in result["error"]


# ─── Deploy refuses --force ────────────────────────────────────────

class TestDeployNoForce:
    """Read the source — no --force token should appear in _deploy_static_mirror."""

    def test_no_force_flag_in_deploy_path(self):
        # Deploy machinery (incl. the static-mirror block) was extracted from
        # app.py to deploy.py — read it from there.
        src = (app_path("publish") / "deploy.py").read_text(encoding="utf-8")

        # Locate the static-mirror block by marker comment
        marker = "Static-mirror mode (mode == \"static-mirror\")"
        idx = src.find(marker)
        assert idx != -1, "static-mirror block marker not found in deploy.py"

        # Find the end — the next top-level route (@web_route POST /api/deploy).
        # This scopes the grep to the static-mirror functions only, excluding
        # the gh-pages deploy() above (which legitimately uses --force) and the
        # Firebase path below.
        end = src.find('@web_route("POST", "/api/deploy")', idx)
        assert end != -1, "api_deploy route marker not found after static-mirror block"

        static_mirror_src = src[idx:end]

        # Hard invariant: no --force in the static-mirror code path
        assert "--force" not in static_mirror_src, (
            "Found '--force' in static-mirror code path — refuse to add. "
            "Static-mirror pushes to long-lived branches with third-party commits "
            "(Cloudflare autoconfig, etc.); --force would clobber them."
        )

        # Hard invariant: pull --ff-only is used (not pull alone or pull --rebase)
        assert "--ff-only" in static_mirror_src, (
            "Static-mirror path should use 'git pull --ff-only' to refuse "
            "divergent state rather than auto-merging."
        )


# ─── End-to-end with a local git repo (no network) ─────────────────

class TestStaticMirrorDeployLocalRepo:
    """Stand up a local 'remote' git repo, point a site at it, deploy. No network.

    This is the closest we can get to a real deploy in unit tests — verifies the
    clone → mirror → commit → push cycle works end-to-end with the same git
    binary the production deploy uses.
    """

    @pytest.fixture
    def local_repo(self, tmp_path):
        """Bare git repo on disk that acts as the 'remote'."""
        bare = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        # Seed with an initial commit so the branch exists
        seed = tmp_path / "seed"
        seed.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
        (seed / "wrangler.jsonc").write_text('{"name": "test"}')
        subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(seed), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "initial"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "remote", "add", "origin", str(bare)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "push", "-u", "origin", "main"],
            check=True, capture_output=True,
        )
        return bare

    @pytest.mark.asyncio
    async def test_first_deploy_clones_mirrors_pushes(
        self, publish_app, tmp_path, local_repo
    ):
        # Patch _save_state + emit so we don't need a real state file / event bus
        publish_app._save_state = lambda data, site: None

        async def _noop_emit(*a, **kw):
            return None

        publish_app.emit = _noop_emit

        # Prepare source files
        source = tmp_path / "brand" / "test" / "site"
        source.mkdir(parents=True)
        (source / "index.html").write_text("<h1>new content</h1>")
        (source / "style.css").write_text("body{color:red}")

        # Point the site at our local bare repo. We have to override
        # _ensure_static_mirror_clone slightly — it uses
        # "https://github.com/<repo>.git" by hardcoded convention. Patch it
        # to use the local bare path instead.
        original_ensure = publish_app._ensure_static_mirror_clone

        async def patched_ensure(site_dir, repo, branch):
            # Same behavior as production but with file:// remote
            if (site_dir / ".git").exists():
                return await original_ensure(site_dir, repo, branch)
            if site_dir.exists():
                shutil.rmtree(site_dir)
            site_dir.parent.mkdir(parents=True, exist_ok=True)
            _, err, code = await publish_app._run_git(
                site_dir.parent, "clone", "--branch", branch,
                "--single-branch", str(local_repo), site_dir.name,
            )
            if code != 0:
                return {"error": err}
            # Set local user for the commits we'll create
            await publish_app._run_git(site_dir, "config", "user.email", "t@t")
            await publish_app._run_git(site_dir, "config", "user.name", "t")
            return {"ok": True}

        publish_app._ensure_static_mirror_clone = patched_ensure

        site = {
            "id": "test",
            "mode": "static-mirror",
            "source_repo_path": "brand/test/site",
            "exclude_files": ["wrangler.jsonc"],
            "branch": "main",
            "repo": "ignored/by-patch",
            "domain": "test.example",
        }

        result = await publish_app._deploy_static_mirror(site)

        assert result.get("status") == "deployed", result
        assert result["url"] == "https://test.example"
        assert result["mirror"]["copied"] == 2

        # Verify the bare repo received the new commit with the right files.
        # Explicit --branch main so the clone doesn't default to whatever
        # init.defaultBranch happens to be on the running Windows git.
        check = tmp_path / "verify"
        subprocess.run(
            ["git", "clone", "--branch", "main", str(local_repo), str(check)],
            check=True, capture_output=True,
        )
        # Source files landed
        assert (check / "index.html").read_text() == "<h1>new content</h1>"
        assert (check / "style.css").read_text() == "body{color:red}"
        # Excluded wrangler.jsonc was preserved (was in target before deploy)
        assert (check / "wrangler.jsonc").exists()
        assert json.loads((check / "wrangler.jsonc").read_text())["name"] == "test"
