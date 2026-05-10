"""Per-app release-readiness scorecard.

With --feature-signals, also scan five "is the feature good enough" proxies:
    use      syslog rows from this app in the last 30 days (weak — many apps
             don't write to syslog even when used)
    aff      UI affordances in pages/index.html — empty-state, error-state,
             settings-panel button (when [provides.settings] declared),
             hash-route (when showDetail used), provenance chip (when
             self.think called). Score = present / required.
    emit     declared events in [provides.events].emits actually fire as
             self.emit("X", ...) somewhere in app.py
    dt (T)   tests/test_dogfood_<app>.py exists — programmatic regression
             check for cross-app sequences
    do (O)   open agent issues attributed to this app via dogfood-agent
             fix-prompt frontmatter `app:` field. Any open issue downgrades
             verdict to `polish-blocked` regardless of other signals — the
             agent is the source of truth for "is this app good enough".


Joins signals already present in the repo — manifest, release.toml tiers,
tests/, pages/, git history vs. the latest release tag — into one table so
you can decide what to put in the next public release.

Usage:
    python scripts/release-readiness.py            # human table
    python scripts/release-readiness.py --json     # machine output
    python scripts/release-readiness.py --verdict ready-new,ready-update
    python scripts/release-readiness.py --since v0.2.50

Verdict legend:
    private        manifest [app] private = true — never ships
    wip            no tier, no tests — not a candidate
    ready-new      not in any tier, but has tests + pages + version ≥ 0.1
    candidate      not in any tier, partial signals (tests OR pages, not both)
    ready-update   in a tier AND commits since last release tag
    current        in a tier AND no commits since last release tag

The verdict is a heuristic. A human still decides whether a "ready-new" app
belongs in `standard` or stays personal.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories under apps/ that are not real, shippable apps.
SKIP_DIRS = {"personal", "_retired", "_example", "test-app", "tmpl"}


def latest_release_tag() -> str | None:
    r = subprocess.run(
        ["git", "tag", "--sort=-creatordate"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    for line in r.stdout.splitlines():
        line = line.strip()
        if re.match(r"^v\d+\.\d+\.\d+", line):
            return line
    return None


def commits_since(tag: str, path: str) -> int:
    r = subprocess.run(
        ["git", "log", f"{tag}..HEAD", "--oneline", "--", path],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    return sum(1 for ln in r.stdout.splitlines() if ln.strip())


def load_tiers() -> dict[str, set[str]]:
    """Map app_id → set of tier names that include it (after `extends` resolution)."""
    from emptyos.sdk.release_tiers import reverse_index

    rt = REPO_ROOT / "release.toml"
    with open(rt, "rb") as f:
        data = tomllib.load(f)
    return reverse_index(data.get("tiers", {}), "apps")


def dogfood_keyword_index() -> dict[str, str]:
    """Build {keyword: app_id} from on-disk manifests, mirroring the in-process
    index the dogfood-agent uses for friction attribution. Includes id +
    aliases + web prefix + CLI commands. Used to scan run transcripts.
    """
    idx: dict[str, str] = {}
    apps_dir = REPO_ROOT / "apps"
    if not apps_dir.exists():
        return idx
    for child in apps_dir.iterdir():
        if not child.is_dir() or child.name in SKIP_DIRS:
            continue
        mp = child / "manifest.toml"
        if not mp.exists():
            continue
        try:
            with open(mp, "rb") as f:
                m = tomllib.load(f)
        except Exception:
            continue
        app = m.get("app", {})
        app_id = app.get("id") or child.name
        for kw in [app_id, *(app.get("aliases", []) or [])]:
            idx[(kw or "").lower()] = app_id
        provides = m.get("provides", {}) or {}
        prefix = ((provides.get("web") or {}).get("prefix") or "").strip("/").lower()
        if prefix:
            idx[prefix] = app_id
        for cmd in (provides.get("cli") or {}).get("commands", []) or []:
            idx[cmd.lower()] = app_id
    idx.pop("", None)
    return idx


def dogfood_transcript_hits() -> dict[str, int]:
    """Scan every dogfood-log.md across all runs, count keyword mentions per
    app. Returns {app_id: hits_across_all_runs}. Apps with hits > 0 were
    visited by the persona at some point, even if no friction was logged.
    """
    runs_dir = REPO_ROOT / "data" / "apps" / "dogfood-agent" / "runs"
    if not runs_dir.exists():
        return {}
    kw = dogfood_keyword_index()
    if not kw:
        return {}
    hits: dict[str, int] = {}
    for run in runs_dir.iterdir():
        if not run.is_dir():
            continue
        log = run / "dogfood-log.md"
        if not log.exists():
            continue
        try:
            text = log.read_text(encoding="utf-8", errors="replace").lower()
        except Exception:
            continue
        for keyword, app_id in kw.items():
            if not keyword:
                continue
            pattern = rf"\b{re.escape(keyword)}\b"
            if re.search(pattern, text):
                hits[app_id] = hits.get(app_id, 0) + 1
    return hits


def dogfood_done_issues() -> dict[str, int]:
    """Same shape as dogfood_open_issues but reads fix-prompts/done/. Used to
    bucket apps as `had-friction` even after the issue was fixed.
    """
    fp = REPO_ROOT / "data" / "apps" / "dogfood-agent" / "fix-prompts" / "done"
    out: dict[str, int] = {}
    if not fp.exists():
        return out
    for md in fp.glob("*.md"):
        if md.name.startswith("_"):
            continue
        try:
            head = md.read_text(encoding="utf-8", errors="replace").splitlines()[:25]
        except Exception:
            continue
        for line in head:
            if line.startswith("---"):
                continue
            if line.startswith("app:"):
                app_id = line.split(":", 1)[1].strip() or "_unattributed_"
                out[app_id] = out.get(app_id, 0) + 1
                break
        else:
            out["_unattributed_"] = out.get("_unattributed_", 0) + 1
    return out


def dogfood_open_issues() -> dict[str, list[dict]]:
    """Read every open fix-prompt under data/apps/dogfood-agent/fix-prompts/
    and group by the `app:` frontmatter field. Returns {} when the agent
    hasn't run yet or the directory is empty.

    Issues without an `app:` field (older entries written before attribution
    landed) are bucketed under `_unattributed_` so they're visible but don't
    inflate any single app's gap count.
    """
    fp = REPO_ROOT / "data" / "apps" / "dogfood-agent" / "fix-prompts"
    out: dict[str, list[dict]] = {}
    if not fp.exists():
        return out
    for md in fp.glob("*.md"):
        if md.name.startswith("_"):
            continue
        try:
            head = md.read_text(encoding="utf-8", errors="replace").splitlines()[:25]
        except Exception:
            continue
        meta: dict[str, str] = {}
        for line in head:
            if line.startswith("---"):
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        app_id = meta.get("app", "") or "_unattributed_"
        out.setdefault(app_id, []).append({
            "file": md.name,
            "kind": meta.get("kind", "?"),
            "count": int(meta.get("count", "1") or 1),
            "last_seen": meta.get("last_seen", ""),
        })
    return out


def syslog_use_count(app_id: str, days: int = 30) -> int | None:
    db = REPO_ROOT / "data" / "syslog.db"
    if not db.exists():
        return None
    import sqlite3
    import time
    cutoff = time.time() - days * 86400
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        n = c.execute(
            "SELECT count(*) FROM syslog WHERE source=? AND ts>?",
            (app_id, cutoff),
        ).fetchone()[0]
        c.close()
        return int(n)
    except Exception:
        return None


def feature_signals(app_dir: Path, app_id: str, m: dict, dogfood_index: dict[str, list[dict]] | None = None) -> dict:
    """Cheap heuristic checks for UX/contract completeness."""
    app_py = app_dir / "app.py"
    pages = app_dir / "pages" / "index.html"
    py_text = app_py.read_text(encoding="utf-8", errors="replace") if app_py.exists() else ""
    page_text = pages.read_text(encoding="utf-8", errors="replace") if pages.exists() else ""

    provides = m.get("provides", {})
    has_settings = bool(provides.get("settings"))
    has_show_detail = "showDetail(" in page_text or "showDetail =" in page_text
    calls_think = "self.think(" in py_text or "self.think_stream(" in py_text

    aff_required: list[tuple[str, bool]] = []
    if pages.exists():
        aff_required.append(("empty_state", "empty-state" in page_text or "emptyState" in page_text))
        aff_required.append(("error_state", "error-state" in page_text or "errorState" in page_text))
        if has_settings:
            aff_required.append(("settings_btn", "settingsPanel" in page_text or "openAppSettings" in page_text))
        if has_show_detail:
            aff_required.append(("hash_route", "hashRoute" in page_text))
        if calls_think:
            aff_required.append(("provenance", "eos-badge-provenance" in page_text or "provenance" in page_text))
    aff_score = (sum(1 for _, ok in aff_required if ok), len(aff_required))

    declared = set((provides.get("events", {}) or {}).get("emits", []) or [])
    emitted = {ev for ev in declared if f'emit("{ev}"' in py_text or f"emit('{ev}'" in py_text}
    emit_score = (len(emitted), len(declared))

    has_dogfood_test = (REPO_ROOT / "tests" / f"test_dogfood_{app_id}.py").exists() or \
                       (REPO_ROOT / "tests" / f"test_dogfood_{app_id.replace('-', '_')}.py").exists()
    use = syslog_use_count(app_id)
    open_issues = (dogfood_index or {}).get(app_id, [])

    return {
        "aff": aff_score,
        "emit": emit_score,
        "dogfood_test": has_dogfood_test,
        "dogfood_open": len(open_issues),
        "dogfood_issues": open_issues,
        "use_30d": use,
        "missing_affordances": [k for k, ok in aff_required if not ok],
        "missing_emits": sorted(declared - emitted),
    }


def version_ge(v: str, threshold: tuple[int, int, int]) -> bool:
    parts = re.findall(r"\d+", v or "")
    nums = tuple(int(p) for p in parts[:3]) + (0,) * (3 - len(parts[:3]))
    return nums >= threshold


def scan_app(app_dir: Path, tier_index: dict[str, set[str]], tag: str | None, *, with_features: bool = False, dogfood_index: dict[str, list[dict]] | None = None) -> dict | None:
    manifest_path = app_dir / "manifest.toml"
    if not manifest_path.exists():
        return None
    try:
        with open(manifest_path, "rb") as f:
            m = tomllib.load(f)
    except Exception as e:
        return {"id": app_dir.name, "error": f"manifest parse: {e}"}

    app = m.get("app", {})
    app_id = app.get("id") or app_dir.name
    version = app.get("version", "")
    private = bool(app.get("private", False))

    has_tests = (REPO_ROOT / "tests" / f"test_sys_{app_id}.py").exists() or \
                (REPO_ROOT / "tests" / f"test_sys_{app_id.replace('-', '_')}.py").exists()
    has_pages = (app_dir / "pages" / "index.html").exists()

    tiers = sorted(tier_index.get(app_id, set()))
    rel_path = app_dir.relative_to(REPO_ROOT).as_posix()
    changes = commits_since(tag, rel_path) if tag else 0

    if private:
        verdict = "private"
    elif tiers:
        verdict = "ready-update" if changes > 0 else "current"
    elif has_tests and has_pages and version_ge(version, (0, 1, 0)):
        verdict = "ready-new"
    elif has_tests or has_pages:
        verdict = "candidate"
    else:
        verdict = "wip"

    out = {
        "id": app_id,
        "version": version,
        "private": private,
        "tiers": tiers,
        "tests": has_tests,
        "pages": has_pages,
        "changes_since_tag": changes,
        "verdict": verdict,
    }

    if with_features:
        sig = feature_signals(app_dir, app_id, m, dogfood_index=dogfood_index)
        out.update(sig)
        # Count gaps to downgrade verdict if signals are weak.
        gaps = 0
        gaps += 1 if sig["aff"][1] and sig["aff"][0] < sig["aff"][1] else 0
        gaps += 1 if sig["emit"][1] and sig["emit"][0] < sig["emit"][1] else 0
        # dogfood test absence is one gap; open agent issues are a stronger
        # signal — any open issue blocks release regardless of other gaps.
        gaps += 0 if sig["dogfood_test"] else 1
        out["gaps"] = gaps
        if sig["dogfood_open"] > 0 and verdict in ("ready-new", "ready-update", "current"):
            out["verdict"] = "polish-blocked"
        elif verdict in ("ready-new", "ready-update") and gaps >= 2:
            out["verdict"] = "needs-polish"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--verdict", help="comma-separated verdicts to filter (e.g. ready-new,ready-update)")
    ap.add_argument("--since", help="release tag to diff against (default: latest v*)")
    ap.add_argument("--include-personal", action="store_true", help="also scan apps/personal/")
    ap.add_argument("--feature-signals", action="store_true", help="also score affordances, emit honesty, dogfood, syslog use")
    ap.add_argument("--coverage", action="store_true", help="print agent coverage view: per-app bucket of polish-blocked / had-friction / touched / untouched")
    ap.add_argument("--explain", help="comma-separated app ids — print missing affordances + emits for each")
    args = ap.parse_args()

    tag = args.since or latest_release_tag()
    tier_index = load_tiers()
    df_idx = dogfood_open_issues() if (args.feature_signals or args.coverage) else None

    apps_dir = REPO_ROOT / "apps"
    rows: list[dict] = []
    for child in sorted(apps_dir.iterdir()):
        if not child.is_dir() or child.name in SKIP_DIRS:
            continue
        row = scan_app(child, tier_index, tag, with_features=args.feature_signals, dogfood_index=df_idx)
        if row:
            rows.append(row)

    if args.include_personal:
        personal = apps_dir / "personal"
        if personal.exists():
            for child in sorted(personal.iterdir()):
                if not child.is_dir() or child.name.startswith("_"):
                    continue
                row = scan_app(child, tier_index, tag, with_features=args.feature_signals, dogfood_index=df_idx)
                if row:
                    row["personal"] = True
                    rows.append(row)

    if args.verdict:
        wanted = {v.strip() for v in args.verdict.split(",")}
        rows = [r for r in rows if r.get("verdict") in wanted]

    if args.coverage:
        # Each app in one of four buckets:
        #   polish-blocked — has open agent issues right now
        #   had-friction   — issue closed (in done/), agent has hit it
        #   touched        — appeared in a run transcript, no friction logged
        #   untouched      — agent has never visited this app
        # The last is the coverage gap — these apps need a scenario.
        open_idx = df_idx if df_idx is not None else dogfood_open_issues()
        done_idx = dogfood_done_issues()
        touched = dogfood_transcript_hits()
        buckets: dict[str, list[dict]] = {
            "polish-blocked": [], "had-friction": [], "touched": [], "untouched": [],
        }
        for r in rows:
            app_id = r["id"]
            if app_id in open_idx and open_idx[app_id]:
                bucket = "polish-blocked"
                detail = f"{len(open_idx[app_id])} open"
            elif done_idx.get(app_id):
                bucket = "had-friction"
                detail = f"{done_idx[app_id]} closed"
            elif touched.get(app_id):
                bucket = "touched"
                detail = f"{touched[app_id]} runs"
            else:
                bucket = "untouched"
                detail = "no signal"
            buckets[bucket].append({"id": app_id, "tier": ",".join(r["tiers"]) or "-", "detail": detail})

        print(f"Agent coverage vs. {tag or '(no tag)'}")
        print()
        for bucket in ("polish-blocked", "had-friction", "touched", "untouched"):
            items = buckets[bucket]
            print(f"== {bucket} ({len(items)}) ==")
            if not items:
                print("  (none)")
            for it in items:
                print(f"  {it['id']:<22} {it['tier']:<18} {it['detail']}")
            print()

        unattributed_open = open_idx.get("_unattributed_", [])
        unattributed_done = done_idx.get("_unattributed_", 0)
        if unattributed_open or unattributed_done:
            print(
                f"Unattributed: {len(unattributed_open)} open, {unattributed_done} closed "
                f"-- fix-prompts written before app: frontmatter landed."
            )
        return 0

    if args.json:
        print(json.dumps({"tag": tag, "apps": rows}, indent=2))
        return 0

    print(f"Release readiness vs. {tag or '(no tag found)'}")
    print()
    if args.feature_signals:
        header = (
            f"{'app':<22} {'ver':<8} {'tier':<18} {'T':<2} {'P':<2} {'d':<4} "
            f"{'aff':<6} {'emit':<6} {'dt':<3} {'do':<3} {'use':<5} verdict"
        )
    else:
        header = f"{'app':<22} {'ver':<8} {'tier':<18} {'T':<2} {'P':<2} {'d':<4} verdict"
    print(header)
    print("-" * len(header))
    order = {
        "polish-blocked": 0, "ready-update": 1, "ready-new": 2, "needs-polish": 3,
        "candidate": 4, "current": 5, "wip": 6, "private": 7,
    }
    for r in sorted(rows, key=lambda x: (order.get(x.get("verdict", ""), 9), x.get("id", ""))):
        if "error" in r:
            print(f"{r['id']:<22} ERROR: {r['error']}")
            continue
        tiers_s = ",".join(r["tiers"]) or "-"
        line = (
            f"{r['id']:<22} "
            f"{r['version']:<8} "
            f"{tiers_s:<18} "
            f"{'y' if r['tests'] else '-':<2} "
            f"{'y' if r['pages'] else '-':<2} "
            f"{r['changes_since_tag']:<4} "
        )
        if args.feature_signals:
            aff = r.get("aff", (0, 0))
            emit = r.get("emit", (0, 0))
            aff_s = f"{aff[0]}/{aff[1]}" if aff[1] else "-"
            emit_s = f"{emit[0]}/{emit[1]}" if emit[1] else "-"
            use = r.get("use_30d")
            use_s = "n/a" if use is None else str(use)
            do_open = r.get("dogfood_open", 0)
            do_s = f"{do_open}!" if do_open else "-"
            line += (
                f"{aff_s:<6} {emit_s:<6} "
                f"{'y' if r.get('dogfood_test') else '-':<3} "
                f"{do_s:<3} {use_s:<5} "
            )
        line += r["verdict"]
        print(line)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("verdict", "?")] = counts.get(r.get("verdict", "?"), 0) + 1
    print()
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if args.explain:
        wanted = {a.strip() for a in args.explain.split(",")}
        explained = [r for r in rows if r.get("id") in wanted]
        if explained:
            print()
            print("=== Explain ===")
            for r in explained:
                print(f"\n{r['id']} ({r.get('verdict', '?')}):")
                if r.get("missing_affordances"):
                    print(f"  missing affordances: {', '.join(r['missing_affordances'])}")
                if r.get("missing_emits"):
                    print(f"  declared but not emitted: {', '.join(r['missing_emits'])}")
                if not r.get("dogfood_test"):
                    print("  no dogfood test (tests/test_dogfood_<id>.py)")
                if r.get("dogfood_open"):
                    print(f"  open agent issues ({r['dogfood_open']}):")
                    for it in r.get("dogfood_issues", []):
                        print(f"    - [{it['kind']}] {it['file']} (seen {it['count']}x, last {it['last_seen']})")
                if r.get("use_30d") == 0:
                    print("  zero syslog activity in last 30 days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
