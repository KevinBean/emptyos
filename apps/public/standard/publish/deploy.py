"""Publish — site deployment: git push, static-mirror, Firebase.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns everything about pushing a built site to a host:

  * `deploy` — vault-built sites → force-push to gh-pages (the default mode).
  * static-mirror mode (`mode == "static-mirror"`) — pre-built HTML mirrored
    into a cloned target repo, pushed non-force on a long-lived branch
    (`_resolve_static_source`, `_validate_static_mirror_source`,
    `_ensure_static_mirror_clone`, `_mirror_to_site_dir`,
    `_deploy_static_mirror`). The non-force-push invariant is pinned by
    tests/test_sys_publish.py::test_no_force_flag_in_deploy_path.
  * `deploy_firebase` — Firebase Hosting.
  * `_run_git` (subprocess wrapper) + `_chatbot_refresh_after_deploy`
    (post-deploy corpus cache bust).
  * the `/api/deploy` + `/api/deploy/firebase` web routes.

Note `_validate_static_mirror_source` is also called by `build()` (which
stays in app.py) for the static-mirror no-op build — resolved via `self`
after the class re-binds these functions.

Functions here are bound onto PublishApp as methods (see the wiring section
in app.py), so they receive `self` as their first argument. Reaches into
other modules via `self.` only (`self._active_site`, `self._site_dir`,
`self._site_config`, `self._save_state`, `self._site_from_request`, …).
Do not import from `.app` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import PublishApp  # noqa: F401 — for type hints only


# ─── Bind to PublishApp class as ─────────────────────────────────────
#   deploy                        = _deploy.deploy
#   _chatbot_refresh_after_deploy = _deploy._chatbot_refresh_after_deploy
#   _run_git                      = _deploy._run_git
#   _resolve_static_source        = _deploy._resolve_static_source
#   _validate_static_mirror_source= _deploy._validate_static_mirror_source
#   _ensure_static_mirror_clone   = _deploy._ensure_static_mirror_clone
#   _mirror_to_site_dir           = _deploy._mirror_to_site_dir
#   _deploy_static_mirror         = _deploy._deploy_static_mirror
#   deploy_firebase               = _deploy.deploy_firebase
#   api_deploy                    = _deploy.api_deploy
#   api_deploy_firebase           = _deploy.api_deploy_firebase
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _date_only_commit_env(today: str) -> dict[str, str]:
    """Pin a deploy commit's author + committer dates to date-only midnight.

    The deployed site repo (gh-pages) is public, so a normal commit timestamp
    would leak the exact clock-time of every deploy — i.e. reveal work done
    during employer work hours. The site's own footer is already date-only;
    this keeps the git metadata consistent, and collapses multiple same-day
    deploys to one indistinguishable date. Privacy floor, not cosmetic.
    """
    stamp = f"{today} 00:00:00"
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


async def deploy(self, site: dict | None = None) -> dict:
    """Deploy site to static hosting via git push.

    Two modes:
      - vault-built (default): build markdown → push to gh-pages with force-push
      - static-mirror: clone target repo, mirror source files in, push to main with normal push (never force)
    """
    s = site or self._active_site()
    if s.get("mode") == "static-mirror":
        return await self._deploy_static_mirror(s)

    site_dir = self._site_dir(s)
    if not site_dir.exists() or not (site_dir / "index.html").exists():
        return {"error": "No built site found. Run build first."}

    config = self._site_config(s)
    repo = config.get("repo", "")
    if not repo:
        return {"error": "No repo configured. Set repo in site settings."}

    remote_url = f"https://github.com/{repo}.git"

    git_dir = site_dir / ".git"
    if not git_dir.exists():
        await self._run_git(site_dir, "init")
        await self._run_git(site_dir, "checkout", "-b", "gh-pages")
        await self._run_git(site_dir, "remote", "add", "origin", remote_url)
    else:
        await self._run_git(site_dir, "remote", "set-url", "origin", remote_url)

    await self._run_git(site_dir, "add", "-A")
    today = datetime.now().strftime("%Y-%m-%d")
    out, err, code = await self._run_git(
        site_dir, "commit", "-m", f"publish: {today}",
        env=_date_only_commit_env(today),
    )
    if code != 0 and "nothing to commit" in (out + err).lower():
        return {"status": "nothing_changed", "message": "Site already up to date."}

    out, err, code = await self._run_git(
        site_dir, "push", "-u", "origin", "gh-pages", "--force"
    )
    if code != 0:
        return {"error": f"Push failed: {err}"}

    self._save_state(
        {
            "last_deploy": datetime.now().isoformat(),
            "deploy_repo": repo,
            "deploy_target": "github",
        },
        s,
    )

    await self.emit(
        "publish:deployed",
        {
            "repo": repo,
            "target": "github",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "site": s["id"],
        },
    )
    # Auto-refresh chatbot corpus cache so the new content is live without
    # waiting for the service's TTL (default 1h). Best-effort — failure
    # here doesn't fail the deploy, since the cache will refresh on TTL.
    await self._chatbot_refresh_after_deploy(s)

    domain = config.get("domain", "")
    url = (
        f"https://{domain}"
        if domain
        else f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[-1]}"
    )
    return {"status": "deployed", "url": url}


async def _chatbot_refresh_after_deploy(self, site: dict) -> None:
    """Fire-and-forget POST /admin/refresh/{site_id} on the chatbot service.
    Silent no-op if site has chatbot disabled or chatbot endpoint not set."""
    cb = site.get("chatbot") or {}
    if not cb.get("enabled"):
        return
    try:
        await self._chatbot_admin_request(
            "POST",
            f"/admin/refresh/{site['id']}",
            site=site,
        )
    except Exception:
        # The proxy already swallows errors — this is belt-and-braces
        # so a failed refresh never prevents the user seeing "Deployed".
        pass


async def _run_git(
    self, cwd: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env={**os.environ, **env} if env else None,
    )
    stdout, stderr = await proc.communicate()
    return (
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        proc.returncode or 0,
    )


# --- Static-mirror mode (mode == "static-mirror") -----------
# Source is pre-built HTML living at site["source_repo_path"] inside the
# EmptyOS repo (e.g. brand/plekto/site/). No markdown build step. Deploy
# clones the target repo, mirrors source files into it, pushes a normal
# (non-force) commit to site["branch"] (default "main"). Files listed in
# site["exclude_files"] are preserved in the target when missing from
# source — required for things like Cloudflare's auto-generated
# wrangler.jsonc that lives only on the deployed side.


def _resolve_static_source(self, site: dict) -> Path:
    """Turn site['source_repo_path'] into an absolute Path.

    Relative paths resolve against the EmptyOS repo root (parent of
    emptyos.toml). Absolute paths pass through.
    """
    raw = (site.get("source_repo_path") or "").strip()
    if not raw:
        raise ValueError("static-mirror site missing 'source_repo_path'")
    p = Path(raw)
    if not p.is_absolute():
        p = self.kernel.config.path.parent / p
    return p.resolve()


def _validate_static_mirror_source(self, site: dict) -> dict:
    """No-op 'build' for static-mirror — source IS the output. Returns stats."""
    try:
        source = self._resolve_static_source(site)
    except ValueError as e:
        return {"error": str(e)}
    if not source.exists():
        return {"error": f"static-mirror source not found: {source}"}
    files = sum(1 for f in source.rglob("*") if f.is_file())
    return {"mode": "static-mirror", "source": str(source), "files": files, "status": "ready"}


async def _ensure_static_mirror_clone(
    self, site_dir: Path, repo: str, branch: str
) -> dict:
    """Make sure site_dir contains a fresh-from-origin clone of repo@branch.

    First run: clone into site_dir (deleting any non-git contents).
    Subsequent runs: fetch + pull --ff-only (refuses divergent state — caller
    must resolve manually rather than force-pushing).
    """
    git_dir = site_dir / ".git"
    if git_dir.exists():
        _, err, code = await self._run_git(site_dir, "fetch", "origin", branch)
        if code != 0:
            return {"error": f"git fetch failed: {err.strip()}"}
        _, err, code = await self._run_git(
            site_dir, "pull", "--ff-only", "origin", branch
        )
        if code != 0:
            return {
                "error": (
                    f"git pull --ff-only failed: {err.strip()}. "
                    f"Clone at {site_dir} has divergent local commits — "
                    "resolve manually before retrying."
                )
            }
        return {"ok": True}

    # No clone yet — start fresh.
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.parent.mkdir(parents=True, exist_ok=True)
    remote_url = f"https://github.com/{repo}.git"
    _, err, code = await self._run_git(
        site_dir.parent,
        "clone",
        "--branch",
        branch,
        "--single-branch",
        remote_url,
        site_dir.name,
    )
    if code != 0:
        return {"error": f"git clone failed: {err.strip()}"}
    return {"ok": True}


def _mirror_to_site_dir(
    self, source: Path, site_dir: Path, exclude_files: list[str]
) -> dict:
    """Copy source → site_dir. Mirror semantics:
      - Files in source get copied into site_dir.
      - Files in site_dir not in source get deleted, EXCEPT:
        * .git/ (always preserved)
        * Anything whose top-level path segment matches an entry in exclude_files
          (e.g. 'wrangler.jsonc' for Cloudflare auto-config).
    """
    excluded = set(exclude_files or [])
    excluded.add(".git")

    # Index source files (relative paths, POSIX-style).
    source_files: set[str] = set()
    if source.exists():
        for f in source.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(source).as_posix()
            top = rel.split("/", 1)[0]
            if top in excluded:
                continue
            source_files.add(rel)

    # Copy each source file into site_dir.
    copied = 0
    for rel in source_files:
        dst = site_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dst)
        copied += 1

    # Delete target files not in source (mirror semantics) — respecting excludes.
    deleted = 0
    if site_dir.exists():
        for f in site_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(site_dir).as_posix()
            top = rel.split("/", 1)[0]
            if top in excluded:
                continue
            if rel not in source_files:
                f.unlink()
                deleted += 1

    return {
        "copied": copied,
        "deleted": deleted,
        "preserved": sorted(excluded - {".git"}),
    }


async def _deploy_static_mirror(self, site: dict) -> dict:
    """Static-mirror deploy: clone target → mirror source → commit + push (never force-push)."""
    repo = (site.get("repo") or "").strip()
    if not repo:
        return {"error": "static-mirror site missing 'repo' field"}
    branch = (site.get("branch") or "main").strip()

    try:
        source = self._resolve_static_source(site)
    except ValueError as e:
        return {"error": str(e)}
    if not source.exists():
        return {"error": f"static-mirror source not found: {source}"}

    site_dir = self._site_dir(site)

    # Step 1: ensure clone is up to date with origin.
    clone_result = await self._ensure_static_mirror_clone(site_dir, repo, branch)
    if "error" in clone_result:
        return clone_result

    # Step 2: mirror source files in (preserving excludes like wrangler.jsonc).
    exclude_files = site.get("exclude_files") or []
    mirror = self._mirror_to_site_dir(source, site_dir, exclude_files)

    # Step 3: stage + commit.
    await self._run_git(site_dir, "add", "-A")
    today = datetime.now().strftime("%Y-%m-%d")
    out, err, code = await self._run_git(
        site_dir, "commit", "-m", f"publish: {today}",
        env=_date_only_commit_env(today),
    )
    if code != 0 and "nothing to commit" in (out + err).lower():
        return {
            "status": "nothing_changed",
            "message": "Site already up to date.",
            "mirror": mirror,
        }
    if code != 0:
        return {"error": f"git commit failed: {err.strip()}", "mirror": mirror}

    # Step 4: push. Never force-push on static-mirror — would clobber
    # third-party commits (Cloudflare auto-config etc.) on the long-lived
    # branch. If push fails, the test test_no_force_flag_in_deploy_path
    # pins this invariant.
    out, err, code = await self._run_git(site_dir, "push", "origin", branch)
    if code != 0:
        return {
            "error": f"git push failed: {err.strip()}",
            "recovery": (
                f"Resolve manually in {site_dir}: "
                f"`git pull --ff-only origin {branch}` then `git push origin {branch}`. "
                "Do NOT force-push on a static-mirror site."
            ),
            "mirror": mirror,
        }

    # Step 5: record state + emit.
    self._save_state(
        {
            "last_deploy": datetime.now().isoformat(),
            "deploy_repo": repo,
            "deploy_target": "github",
            "mode": "static-mirror",
            "branch": branch,
        },
        site,
    )
    await self.emit(
        "publish:deployed",
        {
            "repo": repo,
            "target": "github",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "site": site["id"],
            "mode": "static-mirror",
            "branch": branch,
        },
    )

    domain = (site.get("domain") or "").strip()
    url = f"https://{domain}" if domain else f"https://github.com/{repo}"
    return {"status": "deployed", "url": url, "mirror": mirror}


@web_route("POST", "/api/deploy")
async def api_deploy(self, request):
    """Deploy a site to static hosting. Body: {"site_id": "..."} optional; defaults to active."""
    try:
        site = await self._site_from_request(request)
    except ValueError as e:
        return {"error": str(e)}
    return await self.deploy(site=site)


async def deploy_firebase(self, site: dict | None = None) -> dict:
    """Deploy site to Firebase Hosting."""
    s = site or self._active_site()
    site_dir = self._site_dir(s)
    if not site_dir.exists() or not (site_dir / "index.html").exists():
        return {"error": "No built site found. Run build first."}

    config = self._site_config(s)
    project_id = config.get("firebase_project", "")
    if not project_id:
        svc = self.kernel.services.get_optional("settings")
        if svc:
            project_id = svc.get("publish.firebase_project", "")
    if not project_id:
        return {
            "error": "No Firebase project configured. Set firebase_project in site settings or publish.firebase_project in settings."
        }

    fb_json = site_dir / "firebase.json"
    if not fb_json.exists():
        fb_json.write_text(
            json.dumps(
                {
                    "hosting": {
                        "public": ".",
                        "ignore": ["firebase.json", ".git/**"],
                        "rewrites": [{"source": "**", "destination": "/index.html"}],
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    proc = await asyncio.create_subprocess_exec(
        "firebase",
        "deploy",
        "--only",
        "hosting",
        "--project",
        project_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(site_dir),
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    if proc.returncode != 0:
        return {"error": f"Firebase deploy failed: {err or out}"}

    import re

    url_match = re.search(r"https://[\w.-]+\.web\.app", out + err)
    url = url_match.group(0) if url_match else f"https://{project_id}.web.app"

    self._save_state(
        {
            "last_deploy": datetime.now().isoformat(),
            "deploy_target": "firebase",
            "firebase_project": project_id,
        },
        s,
    )

    await self.emit(
        "publish:deployed",
        {
            "target": "firebase",
            "project": project_id,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "site": s["id"],
        },
    )
    await self._chatbot_refresh_after_deploy(s)

    return {"status": "deployed", "url": url, "target": "firebase"}


@web_route("POST", "/api/deploy/firebase")
async def api_deploy_firebase(self, request):
    """Deploy a site to Firebase Hosting. Body: {"site_id": "..."} optional; defaults to active."""
    try:
        site = await self._site_from_request(request)
    except ValueError as e:
        return {"error": str(e)}
    return await self.deploy_firebase(site=site)
