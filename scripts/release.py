#!/usr/bin/env python3
"""Unified EmptyOS release engine.

    python scripts/release.py <target> [--dry-run] [--yes]

Targets are declared in `release.toml` under `[targets.<name>]` — each pairs a
release tier with a destination repo + the gates/transform to apply. This is the
single entry point for *daemon* releases (code → git repo → live daemon).

  - `public`  delegates to the proven scripts/release-public.py (unchanged OSS flow).
  - other targets (e.g. `portfolio`) are built natively here:
        snapshot working tree → tier-filter apps/plugins → run gates
        → bundle portfolio/ + standalone compose → push to the target repo.

Static-site publishing (`eos publish deploy <site>`) and service deploys
(`deploy-service.sh`) are the other two publish families — see docs/PUBLISHING.md.

Safe by default: `--dry-run` builds the snapshot and prints a summary without
creating/pushing anything. A real push prompts for confirmation unless `--yes`.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fail(msg: str) -> None:
    print(f"\n  x {msg}\n", file=sys.stderr)
    sys.exit(1)


def step(label: str) -> None:
    print(f"\n  -> {label}")


def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, text=True)
    if r.returncode != 0:
        fail(f"command failed: {' '.join(cmd)}")


# --- config -----------------------------------------------------------------

def load_release_toml() -> dict:
    with open(ROOT / "release.toml", "rb") as f:
        return tomllib.load(f)


def _load_sdk_module(name: str):
    """Load `emptyos/sdk/<name>.py` by path, bypassing `emptyos.sdk.__init__`.

    The package initialiser pulls in starlette/pydantic and fails in a bare CI
    checkout — the same trick scripts/check-personal.py uses. Every SDK module
    reached this way must therefore stay free of module-level emptyos imports.
    """
    import importlib.util
    mod_name = f"eos_release_{name}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "emptyos" / "sdk" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves cls.__module__ via sys.modules.
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


tier_union = _load_sdk_module("release_tiers").tier_union
_release_filter = _load_sdk_module("release_filter")


def rmtree(p: Path) -> None:
    """Windows-safe removal (git packs land read-only)."""
    _release_filter.rmtree_forgiving(p)


# --- gates ------------------------------------------------------------------

def run_gate(script: str, label: str) -> None:
    """Run a check-*.py gate against the working tree; abort on non-zero."""
    step(f"Gate: {label}")
    p = ROOT / "scripts" / script
    if not p.is_file():
        fail(f"gate script missing: {script}")
    r = subprocess.run([sys.executable, str(p)], cwd=ROOT, text=True)
    if r.returncode != 0:
        fail(f"{label} found violations — fix before releasing.")
    print(f"    OK: {label} clean")


# --- snapshot ---------------------------------------------------------------

# Tracked-but-unwanted paths removed after the git-driven copy.
_PRUNE_PATHS = [
    "emptyos.toml",            # root machine config — personal path + auth token
    "apps/personal",
    "engines/personal",
    "tests/personal",
    "demo",                    # public demo's vault/config — not needed here
    "brand",                   # branded-distribution assets (plekto etc.)
    "skills",                  # Claude dev skills — not a daemon runtime dep
    ".github",                 # CI workflows would run against private content
    "docker-compose.yml",      # dev compose — replaced by the standalone one
    "docker-compose.demo.yml", # demo compose — not for this target
    "data",
    "results",                 # test-output dir (a tracked stub keeps it in ls-files)
    "services",                # Lane-1 services (not used by the daemon image);
                               # holds gitignored multi-hundred-MB voice models
]

# Worktree copy ignores .gitignore, so large gitignored binaries can sneak in.
# GitHub rejects files >100 MB; strip anything over this ceiling defensively.
_MAX_FILE_BYTES = 95 * 1024 * 1024


def _git_lines(*args: str) -> list[str]:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def snapshot_worktree(temp: Path, force_include: tuple[str, ...] = ()) -> None:
    """Copy the working-tree content of git-known files (honors .gitignore).

    Includes tracked files (with current WIP edits) + untracked-but-not-ignored
    files (captures new apps like bess-analyser), so caches, binaries,
    screenshots, and stale dirs that .gitignore already excludes never leak in.
    `force_include` dirs are copied even if gitignored (e.g. portfolio/).
    """
    step(f"Snapshot working tree (git-driven) -> {temp}")
    rels = set(_git_lines("ls-files"))
    rels |= set(_git_lines("ls-files", "--others", "--exclude-standard"))
    for inc in force_include:
        base = ROOT / inc
        if base.is_dir():
            for f in base.rglob("*"):
                if f.is_file():
                    rels.add(str(f.relative_to(ROOT)).replace("\\", "/"))
    for rel in rels:
        src = ROOT / rel
        if not src.is_file():
            continue
        dst = temp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except (PermissionError, OSError):
            continue  # locked/transient file — skip rather than abort
    for rel in _PRUNE_PATHS:
        p = temp / rel
        if p.is_dir():
            rmtree(p)
        elif p.exists():
            p.unlink()
    # Defensive large-file sweep (worktree copy ignores .gitignore).
    for f in temp.rglob("*"):
        if f.is_file() and f.stat().st_size > _MAX_FILE_BYTES:
            print(f"    strip large file ({f.stat().st_size // 1024 // 1024} MB): "
                  f"{f.relative_to(temp)}")
            f.unlink()
    n = sum(1 for _ in temp.rglob("*") if _.is_file())
    print(f"    OK: {n} files")


def snapshot_head(temp: Path) -> None:
    import io
    import tarfile
    step(f"Snapshot HEAD (git archive) -> {temp}")
    r = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT,
                       capture_output=True)
    if r.returncode != 0:
        fail(f"git archive failed: {r.stderr.decode(errors='replace')}")
    with tarfile.open(fileobj=io.BytesIO(r.stdout), mode="r|") as tar:
        try:
            tar.extractall(path=temp, filter="data")
        except TypeError:
            tar.extractall(path=temp)


def tier_filter(temp: Path, apps: set[str], plugins: set[str]) -> None:
    step(f"Tier filter: keep {len(apps)} apps, {len(plugins)} plugins")
    prune_snapshot = _release_filter.prune_snapshot
    drop_tests_bound_to = _release_filter.drop_tests_bound_to

    report = prune_snapshot(
        temp,
        allowed_apps=apps,
        allowed_plugins=plugins,
        allowed_engines=None,
        drop_tracks=("personal",),
    )
    dropped = list(report.dropped_apps)
    dropped.extend(f"plugin:{plugin_id}" for plugin_id in report.dropped_plugins)
    if dropped:
        print(f"    dropped {len(dropped)}: {', '.join(dropped[:12])}"
              + (" ..." if len(dropped) > 12 else ""))

    dropped_tests = drop_tests_bound_to(temp, apps, allowed_engines=None)
    if dropped_tests:
        step(f"Drop tests bound to dropped apps: {len(dropped_tests)}")
        for dropped_test in dropped_tests:
            print(f"    {dropped_test}")


# --- assemble + push --------------------------------------------------------

def write_repo_gitignore(temp: Path) -> None:
    (temp / ".gitignore").write_text(
        "data/\n__pycache__/\n*.pyc\n*.log\n.env\n.venv/\n", encoding="utf-8"
    )


def assemble_portfolio(temp: Path) -> None:
    """include = code+portfolio: standalone compose + repo README."""
    step("Assemble portfolio extras")
    standalone = temp / "portfolio" / "docker-compose.standalone.yml"
    if not standalone.is_file():
        fail("portfolio/docker-compose.standalone.yml missing from snapshot")
    shutil.copy2(standalone, temp / "docker-compose.yml")
    (temp / "README.md").write_text(
        "# emptyos-portfolio (private)\n\n"
        "Release snapshot powering the live engineering portfolio at "
        "os.binbian.net. Built by `scripts/release.py portfolio` from the main "
        "EmptyOS repo — do not edit here; edit upstream and re-release.\n\n"
        "## Deploy (VPS)\n\n"
        "```bash\n"
        "git pull\n"
        "docker compose --env-file .env up -d --build\n"
        "```\n\n"
        "`.env` needs `EOS_PORTFOLIO_AUTH_TOKEN`, `EOS_PORTFOLIO_PASSWORD`, "
        "`OPENAI_API_KEY`. Caddy maps os.binbian.net -> 127.0.0.1:9001.\n",
        encoding="utf-8",
    )
    print("    OK: docker-compose.yml (standalone) + README")


def push(temp: Path, repo: str, version: str, target: str) -> None:
    remote = f"https://github.com/{repo}.git"
    tag = f"v{version}-{target}"
    step(f"git init + push -> {repo} (tag {tag})")
    run(["git", "init", "-q"], cwd=temp)
    run(["git", "checkout", "-q", "-b", "main"], cwd=temp)
    run(["git", "add", "-A"], cwd=temp)
    run(["git", "commit", "-q", "-m", f"release({target}): v{version}"], cwd=temp)
    run(["git", "remote", "add", "origin", remote], cwd=temp)
    run(["git", "push", "-u", "--force", "origin", "main"], cwd=temp)
    run(["git", "tag", "-f", tag], cwd=temp)
    run(["git", "push", "-f", "origin", tag], cwd=temp)
    print(f"    OK: pushed main + {tag}")


# --- main -------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Unified EmptyOS release engine.")
    ap.add_argument("target", help="target name from release.toml [targets.*]")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the snapshot + print summary; do not push")
    ap.add_argument("--yes", action="store_true", help="skip confirmation")
    args, passthrough = ap.parse_known_args()

    data = load_release_toml()
    version = data.get("release", {}).get("version", "0.0.0")
    targets = data.get("targets", {}) or {}
    tiers = data.get("tiers", {}) or {}

    if args.target not in targets:
        fail(f"unknown target '{args.target}'. Known: {', '.join(sorted(targets)) or '(none)'}")
    t = targets[args.target]

    # `public` keeps its battle-tested dedicated script.
    if args.target == "public":
        step("Delegating to scripts/release-public.py (proven OSS flow)")
        run([sys.executable, str(ROOT / "scripts" / "release-public.py"), *passthrough])
        return

    repo = t.get("repo") or fail("target missing 'repo'")
    tier = t.get("tier") or fail("target missing 'tier'")
    print(f"\n  EmptyOS release :: target={args.target} tier={tier} "
          f"repo={repo} visibility={t.get('visibility', '?')} v{version}")

    # 1. Gates (working tree).
    if t.get("strip_personal"):
        run_gate("check-personal.py", "personal-data scan")
    else:
        print("    (skipping personal scan — private target)")
    if t.get("strip_branding"):
        run_gate("check-branding.py", "third-party branding scan")

    # 2. Snapshot.
    temp = ROOT.parent / f".release-{args.target}"
    rmtree(temp)
    force_include = ("portfolio",) if t.get("include") == "code+portfolio" else ()
    if t.get("snapshot") == "head":
        snapshot_head(temp)
    else:
        snapshot_worktree(temp, force_include=force_include)

    # 3. Tier filter.
    tier_filter(temp, tier_union(tiers, [tier], "apps"),
                tier_union(tiers, [tier], "plugins"))

    # 4. Assemble + repo gitignore.
    write_repo_gitignore(temp)
    if t.get("include") == "code+portfolio":
        assemble_portfolio(temp)

    apps_present = sorted(p.name for p in (temp / "apps").iterdir() if p.is_dir()) \
        if (temp / "apps").is_dir() else []
    print(f"\n  Snapshot ready at {temp}")
    print(f"  Apps ({len(apps_present)}): {', '.join(apps_present)}")

    if args.dry_run:
        step("DRY RUN — not pushing. Inspect the snapshot above.")
        return

    if not args.yes:
        resp = input(f"\n  Push this snapshot to {repo} (force)? [y/N] ").strip().lower()
        if resp != "y":
            fail("aborted by user")

    # 5. Push.
    push(temp, repo, version, args.target)
    step("Done")
    print(f"    {repo} updated. On the VPS: git pull && docker compose "
          f"--env-file .env up -d --build")


if __name__ == "__main__":
    main()
