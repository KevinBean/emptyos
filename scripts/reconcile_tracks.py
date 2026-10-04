"""Reconcile `_next/` track briefs against real git history.

Automates the *evidence gathering* half of the 2026-07-03 hand audit — never
its judgment. Three bands, and only the first is ever acted on:

  clean          both `open_threads` and `threads_carried` are 0, every named
                 commit is an ancestor of main, and last_session is older than
                 --min-age-days. Safe to archive. `--archive` moves these.
  open-unlanded  names >=1 real commit that is NOT an ancestor of main. This is
                 mechanically CERTAIN unfinished work — reported, never touched.
  review         everything else. Needs a human or an agent to read the open
                 threads and check whether another track resolved them.

Why the judgment is not automated (measured 2026-07-25 on 82 briefs):

  * The `[blocked-human]` / `[decision-Kevin]` / `[open-code]` tags look like a
    completion signal but appear on only 8 of 82 briefs — they were introduced
    by the 2026-07-03 pass and never became standard. Classifying on them would
    archive 74 tracks, most with real work left.
  * `threads_carried` says 79 of 82 tracks are open, yet the 2026-07-03 audit
    found 87 of 121 already resolved. Both are true: open threads are never
    cleared retroactively, so a thread resolved by a *different* track's session
    stays listed forever. The brief's own count is therefore the stale data
    being reconciled — it cannot be the signal.

  What is left that is mechanically sound: commit ancestry (219 of 225 hex
  tokens across the corpus resolve to real commits) and file existence.

Read-only by default. `--archive` moves the `clean` band into `_next/archive/`
and rewrites `_index.md`; it refuses to touch anything in the other two bands.

Run: python scripts/reconcile_tracks.py [--json] [--archive] [--min-age-days N]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from emptyos.plan_table import is_sync_conflict  # noqa: E402
from emptyos.sdk import dev_tracks as dt  # noqa: E402
from md_frontmatter import parse_frontmatter  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HEX = re.compile(r"\b([0-9a-f]{8,40})\b")
CODE_PATH = re.compile(
    r"\b((?:apps|emptyos|scripts|tests|plugins|engines|docs)/[\w./\-]+\.\w{1,4})\b"
)
# Sync-conflict siblings carry a STALE snapshot; never treat one as a track.
CONFLICT = ("sync-conflict", ".conflict")


def _is_conflict(p: Path) -> bool:
    return is_sync_conflict(p.stem)  # the one rule, emptyos/plan_table.py


def vault_root() -> Path:
    cfg = tomllib.loads((REPO / "emptyos.toml").read_text(encoding="utf-8"))
    return Path(cfg["notes"]["path"])


def _git(*a: str) -> subprocess.CompletedProcess:
    """Run git, decoding as UTF-8 explicitly.

    `text=True` alone decodes with the Windows locale (cp1252), which mangles
    every commit subject containing an em dash — i.e. most of this repo's
    history. That silently broke the rebased-twin lookup: the mojibake'd
    subject never matched, so healthy rebased commits were misreported as
    orphaned work. Same cp1252 trap as .claude/rules/environment.md, on the
    subprocess *decode* side rather than stdout.
    """
    return subprocess.run(
        ("git",) + a, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=REPO,
    )


def scan(next_dir: Path, min_age_days: int) -> dict:
    cache: dict[str, bool | None] = {}

    def commit_state(h: str) -> str | None:
        """Classify one hash. None when the token isn't a commit at all.

        on-main   already merged — nothing outstanding.
        on-branch reachable from some branch but not main — normal in-flight work.
        rebased   on NO branch, but a commit with the same subject is on one:
                  the brief cites a pre-rebase hash and the content landed. This
                  is evidence the brief is STALE, not that work is missing —
                  getting this backwards turns 4 healthy tracks into a false
                  "unfinished work" alarm (measured 2026-07-25: 3 of 4 matched
                  by subject, the 4th was the v0.5.6 release, on main by tag).
        orphan    on no branch and no subject match — genuinely needs a human.
        """
        if h in cache:
            return cache[h]
        if _git("cat-file", "-e", h + "^{commit}").returncode != 0:
            cache[h] = None
            return None
        if _git("merge-base", "--is-ancestor", h, "main").returncode == 0:
            cache[h] = "on-main"
        elif _git("branch", "-a", "--contains", h).stdout.strip():
            cache[h] = "on-branch"
        else:
            subj = _git("log", "-1", "--format=%s", h).stdout.strip()
            twin = ""
            if subj:
                out = _git("log", "--all", "--format=%h %s", "-F", "--grep", subj).stdout
                twin = "\n".join(
                    ln for ln in out.splitlines() if not ln.startswith(h[:7])
                ).strip()
            cache[h] = "rebased" if twin else "orphan"
        return cache[h]

    tracks, conflicts = [], []
    for f in sorted(next_dir.glob("*.md")):
        if f.name.startswith("_"):
            continue
        if _is_conflict(f):
            conflicts.append(f.name)
            continue
        fm, body = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        brief = dt.parse_track_brief(fm, body)
        landed, in_flight, rebased, orphan = [], [], [], []
        for h in sorted(set(HEX.findall(body))):
            bucket = {"on-main": landed, "on-branch": in_flight,
                      "rebased": rebased, "orphan": orphan}.get(commit_state(h))
            if bucket is not None:
                bucket.append(h)
        dead = sorted(
            p for p in set(CODE_PATH.findall(body)) if not (REPO / p).exists()
        )
        age = dt.age_days(brief.last_session or (fm.get("written") or "")[:10])
        age = age if isinstance(age, int) else 0
        quiet = not brief.open_threads and brief.threads_carried == 0

        if orphan:
            band = "orphan-commit"
        elif in_flight:
            band = "in-flight"
        elif quiet and (landed or rebased) and age >= min_age_days:
            band = "clean"
        else:
            band = "review"

        tracks.append({
            "slug": f.stem, "band": band, "age_days": age,
            "last_session": brief.last_session,
            "title": brief.last_session_title,
            "open_threads": len(brief.open_threads),
            "threads_carried": brief.threads_carried,
            "blocked_human": brief.blocked_human,
            "commits_landed": len(landed), "in_flight": in_flight,
            "rebased": rebased, "orphan": orphan,
            "dead_refs": dead,
        })
    return {"tracks": tracks, "conflict_copies": conflicts}


def archive(next_dir: Path, tracks: list[dict], today: str) -> list[str]:
    """Move ONLY the `clean` band into archive/ and drop its index rows."""
    arch = next_dir / "archive"
    arch.mkdir(exist_ok=True)
    moved = []
    for t in tracks:
        if t["band"] != "clean":
            continue
        src = next_dir / f"{t['slug']}.md"
        if not src.exists():
            continue
        shutil.move(str(src), str(arch / f"{t['slug']}-{today}.md"))
        moved.append(t["slug"])
    if moved:
        idx = next_dir / "_index.md"
        if idx.exists():
            kept = [
                ln for ln in idx.read_text(encoding="utf-8").splitlines()
                if not any(f"({s}.md)" in ln or f"[[{s}]]" in ln for s in moved)
            ]
            idx.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return moved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--archive", action="store_true",
                    help="move the `clean` band to archive/ (never other bands)")
    ap.add_argument("--min-age-days", type=int, default=14)
    ap.add_argument("--today", default="")
    args = ap.parse_args()

    next_dir = vault_root() / "10_Projects/emptyos/log/_next"
    if not next_dir.is_dir():
        print(f"no _next dir at {next_dir}", file=sys.stderr)
        return 2

    res = scan(next_dir, args.min_age_days)
    tracks = res["tracks"]
    bands = {b: [t for t in tracks if t["band"] == b]
             for b in ("clean", "orphan-commit", "in-flight", "review")}

    moved = []
    if args.archive:
        today = args.today or __import__("datetime").date.today().isoformat()
        moved = archive(next_dir, tracks, today)

    if args.json:
        print(json.dumps({
            "ok": True, "code": "ok",
            "message": f"{len(tracks)} tracks · {len(bands['clean'])} clean · "
                       f"{len(bands['in-flight'])} in-flight · "
                       f"{len(bands['review'])} review",
            "data": {**res, "archived": moved},
        }, ensure_ascii=False))
        return 0

    print(f"{len(tracks)} track briefs  (min-age {args.min_age_days}d)")
    if res["conflict_copies"]:
        print(f"  sync-conflict copies (skipped): {res['conflict_copies']}")
    for band, label in (
        ("clean", "CLEAN — no open threads, commits accounted for, aged out"),
        ("orphan-commit", "ORPHAN — cites a commit on no branch with no twin (READ)"),
        ("in-flight", "IN-FLIGHT — commits on a branch, not yet merged to main"),
        ("review", "REVIEW — needs a read; the brief's own thread count is stale"),
    ):
        rows = bands[band]
        print(f"\n{label}: {len(rows)}")
        for t in rows if band != "review" else rows[:0]:
            extra = "".join(
                f" {k}={t[k]}" for k in ("in_flight", "rebased", "orphan") if t[k]
            )
            print(f"  {t['slug']:<28} {t['age_days']:>3}d  "
                  f"open={t['open_threads']} carried={t['threads_carried']}{extra}")
    dead = [t for t in tracks if t["dead_refs"]]
    print(f"\ntracks referencing deleted files: {len(dead)}")
    if moved:
        print(f"\narchived: {len(moved)} -> {', '.join(moved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
