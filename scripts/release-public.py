#!/usr/bin/env python3
"""Snapshot the working tree and ship a clean single commit to the public repo.

Usage:
  python scripts/release-public.py v0.2.3
  python scripts/release-public.py v0.2.3 --message "Custom release note"
  python scripts/release-public.py v0.2.3 --dry-run    # do everything except the actual force-push
  python scripts/release-public.py v0.2.3 --no-tag-private   # skip tagging the private HEAD

What it does:
  1. Verify the working tree is clean (no unstaged or staged changes)
  2. Run check-personal.py + check-branding.py against the working tree
  3. git archive HEAD into a fresh temp dir (history-free snapshot of tracked files only)
  4. Defensive sweep: strip known-cruft (caddy.exe, results/, dist/, build/, *.pyc)
  5. Re-run scans inside the snapshot to confirm clean state
  6. Init the snapshot as a fresh git repo, single commit
  7. Force-push to PUBLIC_REMOTE (default: github.com/KevinBean/emptyos)
  8. Tag the snapshot commit AND tag the private HEAD with the same version
  9. Cleanup temp dir

Required state:
  - D:/emptyos (or wherever this script lives) is a git repo with origin pointing at the
    PRIVATE working repo (where you commit freely)
  - The PUBLIC remote must exist on GitHub already (this script doesn't create repos)

Force-push warning:
  This force-pushes to public main and overwrites whatever was there. That's intentional
  (snapshot model — public history is regenerated from working tree at each release). The
  PRIVATE repo is never force-pushed and keeps full WIP history.

Environment overrides:
  EOS_PUBLIC_REMOTE   default: https://github.com/KevinBean/emptyos.git
  EOS_PUBLIC_BRANCH   default: main
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def _force_writable_then_remove(func, path, exc_info):
    """shutil.rmtree onexc handler — Windows leaves git pack files read-only."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass


def rmtree_force(path: Path) -> None:
    """rmtree that handles Windows read-only file annoyance."""
    if not Path(path).exists():
        return
    # Python 3.12+ uses onexc; older uses onerror. Try both.
    try:
        shutil.rmtree(path, onexc=_force_writable_then_remove)
    except TypeError:
        shutil.rmtree(path, onerror=lambda f, p, e: _force_writable_then_remove(f, p, e))

# Windows consoles default to cp1252 which can't encode the unicode arrows /
# checkmarks used in status output. Reconfigure stdout/stderr to utf-8 so the
# script runs the same on Windows / macOS / Linux.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_REMOTE = os.environ.get("EOS_PUBLIC_REMOTE", "https://github.com/KevinBean/emptyos.git")
PUBLIC_BRANCH = os.environ.get("EOS_PUBLIC_BRANCH", "main")

# Defensive cruft sweep — files that should never be in a public release even if
# they slipped past .gitignore at some point. Add to this list when you find new
# offenders; the .gitignore is the primary defense, this is the safety net.
CRUFT_PATHS = [
    "caddy.exe",
    "results",
    "dist",
    "build",
    ".venv",
]

VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[a-z0-9]+)?$")


def fail(msg: str, code: int = 1) -> None:
    print(f"\n  ✗ {msg}\n", file=sys.stderr)
    sys.exit(code)


def step(label: str) -> None:
    print(f"\n  → {label}")


def run(cmd: list[str], cwd: Path | None = None, capture: bool = False) -> str:
    """Run a command, fail loud on non-zero exit, optionally capture stdout."""
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=capture, text=True, check=False,
    )
    if result.returncode != 0:
        out = (result.stdout or "") + (result.stderr or "")
        fail(f"command failed: {' '.join(cmd)}\n{out}")
    return (result.stdout or "").strip() if capture else ""


def verify_clean_tree() -> None:
    step("Verify working tree is clean")
    status = run(["git", "status", "--porcelain"], cwd=ROOT, capture=True)
    if status:
        print(status)
        fail(
            "working tree has uncommitted changes. Commit or stash before releasing — "
            "the public snapshot must reflect a stable state."
        )
    print("    OK: clean tree")


def run_scans(target_dir: Path | None = None) -> None:
    """Run check-personal + check-branding. If target_dir is given, run them
    inside that dir (the snapshot); otherwise run against working tree."""
    cwd = target_dir or ROOT
    label = "snapshot" if target_dir else "working tree"
    step(f"Run safety scans against {label}")
    for script in ("check-personal.py", "check-branding.py"):
        # Scripts live in ROOT/scripts/ — invoke with absolute path so they
        # can run inside the snapshot dir which has its own copy.
        path = ROOT / "scripts" / script
        run(["python", str(path)], cwd=cwd)
    print(f"    OK: {label} scans clean")


def snapshot_to(temp_dir: Path) -> None:
    """Snapshot HEAD into temp_dir.

    Uses Python's tarfile module (not the system `tar`) for extraction,
    because Windows' Git-Bash `tar` mangles filenames containing special
    chars (e.g. multi-line TOML inline fields parsed as paths).
    """
    import io
    import tarfile

    step(f"Snapshot HEAD via git archive → {temp_dir}")
    result = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=ROOT, capture_output=True, check=False,
    )
    if result.returncode != 0:
        fail(f"git archive failed: {result.stderr.decode(errors='replace')}")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r|") as tar:
        # filter='data' (Python 3.12+) strips dangerous attributes (abs paths,
        # symlinks pointing outside the extraction root). Falls back to the
        # default extraction filter on older Python.
        try:
            tar.extractall(path=temp_dir, filter="data")
        except TypeError:
            tar.extractall(path=temp_dir)
    file_count = sum(1 for _ in temp_dir.rglob("*") if _.is_file())
    print(f"    OK: extracted {file_count} files")


def sweep_cruft(temp_dir: Path) -> None:
    step("Defensive cruft sweep")
    removed = []
    for name in CRUFT_PATHS:
        path = temp_dir / name
        if path.is_file():
            path.unlink()
            removed.append(name)
        elif path.is_dir():
            shutil.rmtree(path)
            removed.append(name + "/")
    # Also strip pycache + pyc anywhere
    for pyc in list(temp_dir.rglob("*.pyc")):
        pyc.unlink()
    for cache in list(temp_dir.rglob("__pycache__")):
        if cache.is_dir():
            shutil.rmtree(cache)
    print(f"    OK: removed {removed or 'no cruft found'}")


def commit_and_push(snapshot_src: Path, version: str, message: str, dry_run: bool) -> None:
    """Append-mode release.

    Clones the existing public repo, wipes its tracked files (preserving .git),
    copies the fresh snapshot in, commits the diff as a single squashed commit
    on top of the existing history, tags it, and pushes. This way public history
    accumulates one commit per release instead of being force-pushed.

    For the very first release into an empty repo, this falls through to a
    plain `git init` + first commit.
    """
    step(f"Clone existing public repo from {PUBLIC_REMOTE}")
    work = snapshot_src.parent / "release-work"
    if work.exists():
        rmtree_force(work)
    clone_result = subprocess.run(
        ["git", "clone", "--depth", "50", PUBLIC_REMOTE, str(work)],
        capture_output=True, text=True,
    )
    is_first_release = clone_result.returncode != 0 or not (work / ".git").exists()
    if is_first_release:
        print("    Public repo is empty or unreachable — initializing first commit")
        if work.exists():
            rmtree_force(work)
        work.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "-q", "-b", PUBLIC_BRANCH], cwd=work)
    else:
        # Switch to the right branch (handle case where remote uses a different default)
        try:
            run(["git", "checkout", PUBLIC_BRANCH], cwd=work)
        except SystemExit:
            run(["git", "checkout", "-b", PUBLIC_BRANCH], cwd=work)
        print(f"    OK: cloned (history depth ~50 commits)")

    # Configure committer identity from the private repo's last committer
    name = run(["git", "log", "-1", "--format=%an"], cwd=ROOT, capture=True)
    email = run(["git", "log", "-1", "--format=%ae"], cwd=ROOT, capture=True)
    run(["git", "config", "user.name", name], cwd=work)
    run(["git", "config", "user.email", email], cwd=work)

    step("Replace tracked files with fresh snapshot")
    # Remove every tracked file (and stage the deletions). .git/ is preserved
    # because it isn't tracked.
    if not is_first_release:
        # `git rm -rf .` only removes tracked files; untracked stay (we have none).
        run(["git", "rm", "-rfq", "."], cwd=work)
    # Now overlay the snapshot
    for item in snapshot_src.iterdir():
        if item.name == ".git":
            continue
        dest = work / item.name
        if dest.exists():
            if dest.is_dir(): shutil.rmtree(dest)
            else: dest.unlink()
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    run(["git", "add", "-A"], cwd=work)

    # Check if there's actually a diff (snapshot may be identical to last release)
    diff_check = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=work, capture_output=True,
    )
    if diff_check.returncode == 0:
        print("    NOTE: snapshot is identical to current public HEAD — skipping commit")
        if dry_run:
            print(f"\n  [DRY RUN] Nothing to push; tag {version} would still be created if missing")
        return

    step(f"Commit snapshot as {version}")
    run(["git", "commit", "-q", "-m", message], cwd=work)

    step(f"Tag snapshot {version}")
    # If the tag already exists locally (from a previous attempt), force-replace
    existing_tag = subprocess.run(["git", "tag", "--list", version], cwd=work, capture_output=True, text=True)
    if existing_tag.stdout.strip():
        run(["git", "tag", "-d", version], cwd=work)
    run(["git", "tag", "-a", version, "-m", f"EmptyOS {version}"], cwd=work)

    if dry_run:
        sha = run(["git", "rev-parse", "HEAD"], cwd=work, capture=True)
        print(f"\n  [DRY RUN] Would push commit {sha[:8]} to {PUBLIC_REMOTE} {PUBLIC_BRANCH} (fast-forward)")
        print(f"  [DRY RUN] Would push tag {version}")
        return

    step(f"Push to {PUBLIC_REMOTE}")
    # First-release path needed an `origin` remote added; clone path already has it.
    if is_first_release:
        run(["git", "remote", "add", "origin", PUBLIC_REMOTE], cwd=work)
        run(["git", "push", "-u", "origin", PUBLIC_BRANCH], cwd=work)
    else:
        run(["git", "push", "origin", PUBLIC_BRANCH], cwd=work)
    # Tags can collide with previous attempts — force-push tag refs only
    run(["git", "push", "-f", "origin", version], cwd=work)
    print(f"    OK: pushed commit + tag {version}")
    # Cleanup the work tree
    rmtree_force(work)


def tag_private(version: str) -> None:
    step(f"Tag private HEAD with {version}")
    # Check if tag exists; if so, force-replace
    existing = run(["git", "tag", "--list", version], cwd=ROOT, capture=True)
    if existing:
        print(f"    NOTE: tag {version} already exists in private repo, replacing")
        run(["git", "tag", "-d", version], cwd=ROOT)
    run(["git", "tag", "-a", version, "-m", f"EmptyOS {version} (private HEAD at release)"], cwd=ROOT)
    # Push to private origin
    run(["git", "push", "origin", "-f", version], cwd=ROOT)
    print(f"    OK: tagged + pushed {version} to private origin")


def default_message(version: str) -> str:
    return f"""EmptyOS {version}

Snapshot release from working tree. See full changelog and history in the
private dev repo (not public). Each public release is a single squashed
commit; intermediate WIP is not part of public history.

Tag: {version}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Ship a clean snapshot release to public.")
    parser.add_argument("version", help="Version tag, e.g. v0.2.3")
    parser.add_argument("--message", "-m", default=None, help="Override the default commit message")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except the force-push")
    parser.add_argument("--no-tag-private", action="store_true", help="Skip tagging the private HEAD")
    args = parser.parse_args()

    if not VERSION_RE.match(args.version):
        fail(f"version must look like v0.2.3 (got: {args.version})")

    print(f"\n  Releasing {args.version} → {PUBLIC_REMOTE} ({PUBLIC_BRANCH})")
    if args.dry_run:
        print("  [DRY RUN — no push will happen]")

    verify_clean_tree()
    run_scans()  # working tree

    with tempfile.TemporaryDirectory(prefix="eos-snap-") as tmp:
        temp_dir = Path(tmp)
        snapshot_to(temp_dir)
        sweep_cruft(temp_dir)
        run_scans(temp_dir)  # snapshot
        message = args.message or default_message(args.version)
        commit_and_push(temp_dir, args.version, message, args.dry_run)

    if not args.no_tag_private and not args.dry_run:
        tag_private(args.version)

    print(f"\n  ✓ Done. Public release: {PUBLIC_REMOTE.replace('.git', '')}/releases/tag/{args.version}\n")


if __name__ == "__main__":
    main()
