#!/usr/bin/env python3
"""Advisory gap-analysis coverage/staleness scanner.

The deterministic substrate of the standing per-app market gap analysis
(.claude/skills/eos-app-gap-analysis) — the judgment (market research,
benchmarking, gap triage) lives in the skill; this script only answers
"which apps have no gap note, and which notes have gone stale?" so the
registry can't silently decay into a one-off (per
.claude/rules/self-audit-loops.md graduation).

For every active app (apps/ track tree via emptyos/sdk/app_layout.py,
personal excluded by default) it cross-checks the vault registry at
{vault}/30_Resources/EmptyOS/gap-analysis/<app-id>.md:

  fresh    — note exists, last_reviewed within --stale-days (default 90)
  stale    — note exists but last_reviewed older than --stale-days
  missing  — no note yet (the coverage todo surface)
  orphan   — a note whose app id no longer exists in the tree (drift; reported,
             not counted in the exit code)

``grade`` is DERIVED from ``score`` (see GRADE_BANDS) rather than hand-authored:
a letter typed by hand drifts silently because nothing reads it, and by
2026-08-06 the registry held 72→"B" and 72→"B+", plus a 91→"A" ranked above a
95→"A-". Notes whose frontmatter disagrees with the derived letter are reported
as ``grade_drift`` and normalised in place by ``--write-index`` (idempotent,
single-line rewrite). Authors may keep writing ``grade:`` — it is a cache of a
computed value, not an input.

Exit code = min(99, stale + missing) — exit-code-as-signal, advisory
(registered gate=False in scripts/preflight.py --scope apps). Pure file I/O,
no kernel import — safe while the daemon is up.

Usage::

    python scripts/check_gap_freshness.py             # summary + stale/missing lists
    python scripts/check_gap_freshness.py --full      # every app's row
    python scripts/check_gap_freshness.py --json      # agent-cli envelope
    python scripts/check_gap_freshness.py --stale-days 120
    python scripts/check_gap_freshness.py --include-personal
    python scripts/check_gap_freshness.py --write-index   # regenerate _index.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_common import REPO, load_by_path  # noqa: E402
from kb_paths import vault_root  # noqa: E402
from md_frontmatter import parse_fm  # noqa: E402


# Score is the app_optimizer_scan 0–120 total. Bands are the 80/70/60/50/33%
# marks; they were chosen to match the letter the registry had already settled
# on for all but one of its 39 notes, so deriving grade renames almost nothing.
GRADE_BANDS: tuple[tuple[int, str], ...] = (
    (96, "A"),
    (84, "A-"),
    (72, "B+"),
    (60, "B"),
    (40, "C"),
    (0, "D"),
)


def grade_for(score) -> str:
    """Letter for a 0–120 score. Empty string when score isn't a number."""
    try:
        s = int(str(score).strip())
    except (TypeError, ValueError):
        return ""
    for floor, letter in GRADE_BANDS:
        if s >= floor:
            return letter
    return "D"


_GRADE_LINE_RE = re.compile(r"^grade:.*$", re.MULTILINE)


def normalise_grade(text: str, derived: str) -> str:
    """Rewrite the frontmatter ``grade:`` line to ``derived``.

    Only touches the first frontmatter block, and only when a ``grade:`` line is
    already there — this never invents frontmatter for a note that omitted it.
    """
    if not derived:
        return text
    parts = text.split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        return text  # no leading frontmatter block; leave the file alone
    fm, rest = parts[1], parts[2]
    if not _GRADE_LINE_RE.search(fm):
        return text
    return "---" + _GRADE_LINE_RE.sub(f"grade: {derived}", fm, count=1) + "---" + rest


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _age_days(iso: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(iso.strip())).days
    except (ValueError, AttributeError):
        return None


def scan(stale_days: int, include_personal: bool) -> dict:
    layout = load_by_path("gap_app_layout", "emptyos/sdk/app_layout.py")
    apps_root = REPO / "apps"
    apps: dict[str, dict] = {}
    for aid, adir in layout.iter_app_dirs(apps_root, include_personal=include_personal):
        track = layout.track_of(adir, apps_root)
        group = layout.group_of(adir, apps_root)
        apps[aid] = {"id": aid, "track": f"{track}/{group}".strip("/")}

    gap_dir = vault_root() / "30_Resources" / "EmptyOS" / "gap-analysis"
    today = date.today()
    notes: dict[str, dict] = {}
    orphans: list[str] = []
    if gap_dir.is_dir():
        for f in sorted(gap_dir.glob("*.md")):
            if f.name.startswith("_"):
                continue  # _index.md and friends
            fm = parse_fm(_read(f))
            aid = str(fm.get("app") or f.stem)
            reviewed = str(fm.get("last_reviewed") or "")
            age = _age_days(reviewed, today)
            score = fm.get("score", "")
            derived = grade_for(score)
            authored = str(fm.get("grade") or "").strip()
            row = {
                "app": aid,
                "market": fm.get("market", ""),
                "score": score,
                "grade": derived or authored,
                "grade_authored": authored,
                "grade_drift": bool(derived) and bool(authored) and derived != authored,
                "open_gaps": fm.get("open_gaps", ""),
                "last_reviewed": reviewed,
                "age_days": age,
                "note": f.name,
                "path": str(f),
            }
            if aid not in apps:
                orphans.append(aid)
            notes[aid] = row

    rows = []
    for aid, meta in sorted(apps.items()):
        n = notes.get(aid)
        if n is None:
            state = "missing"
            rows.append({**meta, "state": state})
            continue
        state = "stale" if (n["age_days"] is None or n["age_days"] > stale_days) else "fresh"
        rows.append({**meta, **n, "state": state})

    # Drift is a property of the notes, not of the app tree — an orphan note's
    # grade is still worth normalising, so read it off `notes`, not `rows`.
    drift = sorted(
        (n for n in notes.values() if n.get("grade_drift")),
        key=lambda n: n["app"],
    )
    counts = {
        "apps": len(apps),
        "fresh": sum(1 for r in rows if r["state"] == "fresh"),
        "stale": sum(1 for r in rows if r["state"] == "stale"),
        "missing": sum(1 for r in rows if r["state"] == "missing"),
        "orphans": len(orphans),
        "grade_drift": len(drift),
    }
    return {
        "rows": rows,
        "orphans": orphans,
        "grade_drift": drift,
        "counts": counts,
        "gap_dir": str(gap_dir),
    }


def render_index(res: dict, today: str) -> str:
    """Markdown for _index.md — analyzed apps first, then the unanalyzed todo tail."""
    c = res["counts"]
    lines = [
        "---",
        "tags:",
        "  - gap-analysis-index",
        "author: ai",
        f"as_of: {today}",
        "lifecycle: snapshot",
        "---",
        "",
        f"# App gap-analysis — coverage index ({today})",
        "",
        f"**{c['apps']} apps · {c['fresh']} analyzed · {c['stale']} stale (>90d) · "
        f"{c['missing']} unanalyzed.**",
        "Regenerated by `/eos-app-gap-analysis` (or `check_gap_freshness.py --write-index`); "
        "coverage checked in preflight.",
        "",
        "| App | Track | Market | Score | Open gaps | Last reviewed |",
        "|---|---|---|---|---|---|",
    ]
    def _key(r):
        return (0 if r["state"] != "missing" else 1, r["track"], r["id"])
    for r in sorted(res["rows"], key=_key):
        if r["state"] == "missing":
            lines.append(f"| {r['id']} | {r['track']} | — | — | — | — |")
        else:
            lines.append(
                f"| [[{r['app']}]] | {r['track']} | {r.get('market','')} | {r.get('score','')} "
                f"| {r.get('open_gaps','')} | {r.get('last_reviewed','')} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="gap-analysis registry coverage/staleness")
    ap.add_argument("--stale-days", type=int, default=90)
    ap.add_argument("--include-personal", action="store_true")
    ap.add_argument("--full", action="store_true", help="print every app's row")
    ap.add_argument("--json", action="store_true", help="agent-cli envelope")
    ap.add_argument("--write-index", action="store_true",
                    help="regenerate {gap_dir}/_index.md from the scan")
    args = ap.parse_args(argv)

    res = scan(args.stale_days, args.include_personal)
    if args.write_index:
        for n in res["grade_drift"]:
            p = Path(n["path"])
            fixed = normalise_grade(_read(p), n["grade"])
            if fixed:
                p.write_text(fixed, encoding="utf-8")
                print(f"grade {n['app']}: {n['grade_authored']} -> {n['grade']} (score {n['score']})")
        if res["grade_drift"]:
            res = scan(args.stale_days, args.include_personal)  # re-read so the index is post-fix
        idx = Path(res["gap_dir"]) / "_index.md"
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(render_index(res, date.today().isoformat()), encoding="utf-8")
        print(f"wrote {idx}")
    c = res["counts"]
    n_bad = c["stale"] + c["missing"]
    exit_code = min(99, n_bad)

    if args.json:
        print(json.dumps({
            "ok": n_bad == 0,
            "code": "ok" if n_bad == 0 else "incomplete",
            "message": (
                f"{c['apps']} apps: {c['fresh']} fresh, {c['stale']} stale "
                f"(>{args.stale_days}d), {c['missing']} missing, {c['orphans']} orphan notes"
            ),
            "data": res["counts"] | {
                "stale_apps": [r["id"] for r in res["rows"] if r["state"] == "stale"],
                "missing_apps": [r["id"] for r in res["rows"] if r["state"] == "missing"],
                "orphan_notes": res["orphans"],
                "grade_drift_apps": [
                    {"app": n["app"], "score": n["score"],
                     "authored": n["grade_authored"], "derived": n["grade"]}
                    for n in res["grade_drift"]
                ],
            },
        }))
        return exit_code

    pct = (100 * c["fresh"] // c["apps"]) if c["apps"] else 0
    print(f"Gap-analysis coverage — {c['apps']} apps, {c['fresh']} fresh ({pct}%), "
          f"{c['stale']} stale (>{args.stale_days}d), {c['missing']} missing")
    print(f"registry: {res['gap_dir']}")
    stale = [r for r in res["rows"] if r["state"] == "stale"]
    if stale:
        print("\nstale:")
        for r in stale:
            print(f"  {r['id']:24} last_reviewed {r.get('last_reviewed') or '?'} ({r.get('age_days','?')}d)")
    if res["orphans"]:
        print("\norphan notes (app id no longer in tree):")
        for aid in res["orphans"]:
            print(f"  {aid}")
    if res["grade_drift"]:
        print(f"\ngrade drift ({len(res['grade_drift'])}) — run --write-index to normalise:")
        for n in res["grade_drift"]:
            print(f"  {n['app']:24} score {str(n['score']):>4}  "
                  f"{n['grade_authored']} -> {n['grade']}")
    missing = [r for r in res["rows"] if r["state"] == "missing"]
    if missing:
        shown = missing if args.full else missing[:20]
        print(f"\nmissing ({len(missing)}):")
        for r in shown:
            print(f"  {r['id']:24} {r['track']}")
        if len(shown) < len(missing):
            print(f"  … +{len(missing) - len(shown)} more (--full to list)")
    if args.full:
        fresh = [r for r in res["rows"] if r["state"] == "fresh"]
        if fresh:
            print("\nfresh:")
            for r in fresh:
                print(f"  {r['id']:24} score {r.get('score','?'):>4}  open {r.get('open_gaps','?'):>2}  "
                      f"reviewed {r.get('last_reviewed','?')}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
