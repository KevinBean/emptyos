"""Backfill `created` field on app/plugin/engine manifests from git first-seen.

Idempotent: only writes the field when it's missing. Safe to re-run.
Usage: python scripts/backfill-manifest-created.py [--dry-run]
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DRY_RUN = "--dry-run" in sys.argv


def git_first_seen(path: Path) -> str | None:
    """Full UTC ISO timestamp of the commit that first added `path`."""
    import datetime
    try:
        r = subprocess.run(
            ["git", "log", "--follow", "--diff-filter=A", "--format=%aI", "--", str(path)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = [line for line in r.stdout.strip().splitlines() if line]
        if not lines:
            return None
        iso = lines[-1]
        try:
            dt = datetime.datetime.fromisoformat(iso)
            return dt.astimezone(datetime.timezone.utc).isoformat()
        except Exception:
            return iso
    except Exception:
        return None


def backfill_one(manifest_path: Path) -> tuple[str, str | None]:
    """Returns (status, date). status: 'has' | 'wrote' | 'skip' | 'no-git'."""
    try:
        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        return (f"parse-error: {e}", None)

    table = next((t for t in ("app", "plugin", "engine") if t in data), None)
    if not table:
        return ("skip", None)

    if data[table].get("created"):
        return ("has", str(data[table]["created"])[:10])

    date = git_first_seen(manifest_path)
    if not date:
        return ("no-git", None)

    if DRY_RUN:
        return ("would-write", date)

    text = manifest_path.read_text(encoding="utf-8")
    # Insert `created = "YYYY-MM-DD"` right after the [app|plugin|engine] header
    header = f"[{table}]"
    idx = text.find(header)
    if idx < 0:
        return ("no-header", None)
    eol = text.find("\n", idx)
    if eol < 0:
        return ("malformed", None)
    new_text = text[: eol + 1] + f'created = "{date}"\n' + text[eol + 1 :]
    manifest_path.write_text(new_text, encoding="utf-8")
    return ("wrote", date)


def main() -> int:
    targets = []
    for sub in ("apps", "plugins", "engines"):
        root = REPO_ROOT / sub
        if not root.exists():
            continue
        targets.extend(root.glob("**/manifest.toml"))

    counts = {"has": 0, "wrote": 0, "would-write": 0, "skip": 0, "no-git": 0, "other": 0}
    for mf in sorted(targets):
        rel = mf.relative_to(REPO_ROOT)
        status, date = backfill_one(mf)
        bucket = status if status in counts else "other"
        counts[bucket] += 1
        if status in ("wrote", "would-write", "no-git"):
            print(f"  {status:12s} {rel}  {date or ''}")

    print()
    print(f"  has-created: {counts['has']}")
    print(f"  wrote:       {counts['wrote']}")
    if DRY_RUN:
        print(f"  would-write: {counts['would-write']}  (dry-run)")
    print(f"  no-git:      {counts['no-git']}  (uncommitted manifests)")
    print(f"  skipped:     {counts['skip']}  (no [app|plugin|engine] table)")
    if counts["other"]:
        print(f"  other:       {counts['other']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
