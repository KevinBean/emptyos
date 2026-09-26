"""Session board — one view of every dev track, plan and live agent session.

The per-session tools each see one thing: `/eos-session-resume` opens ONE track,
`/eos-session-wrapup` closes ONE session, `check_plan_staleness.py` reads plans,
`reconcile_tracks.py` reads git evidence. Nothing puts them side by side, so with
~115 tracks the answer to "what is in flight, what is waiting on me, what did I
forget" lived in nobody's head. This is the aggregation the `eos-session-board`
skill reads. It computes lanes and never judges; the skill does the judging.

Lanes — each track lands in exactly ONE, first match wins:

  live      a Claude/Codex session touched in the last --live-minutes ACTED on it
            (typed `session-resume <slug>`, called the resume skill with it, or
            edited `_next/<slug>.md` — reading a brief does not count), or a plan
            claiming the track has a non-empty `active_task`.
  parked    frontmatter `parked:` set by this script's `park` command. Ahead of
            kevin/ready on purpose: parking a tagged track has to hide it, or
            park would do nothing visible. Its threads also leave the decision queue.
  kevin     has an open thread tagged `[decision-Kevin]` or `[blocked-human]`.
            Deliberately wider than `dev_tracks.blocked_human`, which matches
            only `[blocked-human]` — on 2026-09-23 that left 20 open
            `[decision-Kevin]` threads out of "waiting on Kevin" (31 in all).
  ready     has an `[open-code]` thread: work Claude can start without him.
  dormant   untouched > 30 days and nothing tagged.
  untagged  everything else — open threads nobody classified.

Mutations (`park` / `unpark` / `close` / `merge`) are DRY-RUN unless `--apply`,
match a track by its EXACT slug only (a substring close once shut three
unrelated entries — CLAUDE.md § Session Housekeeping), and re-read each file at
apply time rather than at plan time. There is no lock: a write another session
lands between that read and ours is lost, so run them when no wrapup is mid-way.
An archive never overwrites — a second close the same day gets a `-2` suffix.

Run:
  python scripts/session_board.py [--json] [--write] [--live-minutes N]
  python scripts/session_board.py park  <slug> --reason "..." [--apply]
  python scripts/session_board.py unpark <slug> [--apply]
  python scripts/session_board.py close <slug> --disposition "..." [--apply]
  python scripts/session_board.py merge <into> <from> [--apply]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from emptyos.frontmatter import set_frontmatter_field  # noqa: E402
from emptyos.runtime.atomic_io import atomic_write_text  # noqa: E402
from emptyos.sdk import dev_tracks as dt  # noqa: E402
from md_frontmatter import parse_frontmatter  # noqa: E402
from plan_table import parse_tasks as parse_task_table  # noqa: E402
from reconcile_tracks import _is_conflict  # noqa: E402
from scanner_lib import emit_json  # noqa: E402

LANES = ("live", "parked", "kevin", "ready", "dormant", "untagged")
KEVIN_TAGS = ("[decision-kevin]", "[blocked-human]")
READY_TAG = "[open-code]"
DORMANT_DAYS = dt.STALE_DAYS  # same ">30d = stale" line devboard's classify_track draws
TAIL_BYTES = 256_000  # Claude Code re-writes ai-title every turn, so the last one is near the end
BOARD_NAME = "_board.md"


def log_dir() -> Path:
    cfg = tomllib.loads((REPO / "emptyos.toml").read_text(encoding="utf-8"))
    return Path(cfg["notes"]["path"]) / "10_Projects/emptyos/log"


def thread_kind(text: str) -> str:
    low = text.lower()
    if any(t in low for t in KEVIN_TAGS):
        return "kevin"
    if READY_TAG in low:
        return "ready"
    return "untagged"


# ── live sessions ────────────────────────────────────────────────────────────

def _tail(path: Path) -> str:
    with path.open("rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - TAIL_BYTES))
        return fh.read().decode("utf-8", errors="replace")


def _json_lines(text: str) -> list[dict]:
    out = []
    for ln in text.splitlines():
        try:
            o = json.loads(ln)
        except ValueError:
            continue  # the tail's first line is usually cut mid-object
        if isinstance(o, dict):
            out.append(o)
    return out


def _actions(objs: list[dict]) -> list[str]:
    """What the session DID, as short strings — never what it merely read.

    Tool results are excluded on purpose: a session that greps or cats `_next/`
    (this board, a wrapup survey) has every slug in its output, and counting
    those mapped a board session onto tracks it only looked at. Measured on the
    first real run. What counts is intent: text the user typed, a Skill call, and
    an Edit/Write target.
    """
    acts: list[str] = []
    for o in objs:
        t = o.get("type")
        if t == "event_msg" and (o.get("payload") or {}).get("type") == "user_message":
            acts.append(str(o["payload"].get("message", "")))
            continue
        content = (o.get("message") or {}).get("content")
        if t == "user" and o.get("isMeta"):
            # Harness-injected text, e.g. a loaded SKILL.md body. The resume skill's
            # own examples say `/eos-session-resume career`, which mapped every
            # session that ran it onto the career track.
            continue
        if t == "user":
            if isinstance(content, str):
                acts.append(content)
            elif isinstance(content, list):
                acts += [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
        elif t == "assistant" and isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                inp = b.get("input") or {}
                if b.get("name") == "Skill":
                    acts.append(f"{inp.get('skill', '')} {inp.get('args', '')}")
                elif b.get("name") in ("Edit", "Write", "NotebookEdit"):
                    acts.append(str(inp.get("file_path", "")).replace("\\", "/"))
    return acts


def _names(act: str, slug: str) -> bool:
    # (?![\w-]) not \b: `\b` sits between `cable` and `-pulling`, so `\b` would
    # read `session-resume cable-pulling` as naming the `cable` track too.
    s = re.escape(slug)
    return bool(re.search(rf"session-resume\s+{s}(?![\w-])", act)
                or re.search(rf"_next/{s}\.md$", act))


def _codex_title(path: Path) -> str | None:
    """First typed prompt — Codex rollouts carry no title line of their own.

    None for a sub-thread (`session_meta.parent_thread_id` set): Codex spawns
    one per auto-review, and listing them turned one session into four rows.
    """
    with path.open("rb") as fh:
        head = fh.read(TAIL_BYTES).decode("utf-8", errors="replace")
    for o in _json_lines(head):
        p = o.get("payload") or {}
        if o.get("type") == "session_meta" and p.get("parent_thread_id"):
            return None
        if o.get("type") == "event_msg" and p.get("type") == "user_message":
            return " ".join(str(p.get("message", "")).split())[:40]
    return ""


def live_sessions(minutes: int, slugs: set[str], home: Path | None = None) -> list[dict]:
    """Transcripts modified in the last `minutes`, with the tracks they name.

    Title comes from Claude Code's own `ai-title` / `custom-title` lines, so it
    is the same label the user sees in their session list.
    """
    home = home or Path.home()
    cutoff = time.time() - minutes * 60
    found: list[dict] = []
    cands = [("claude", p) for p in (home / ".claude/projects").glob("*/*.jsonl")]
    cands += [("codex", p) for p in (home / ".codex/sessions").glob("*/*/*/*.jsonl")]
    for source, p in cands:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        objs = _json_lines(_tail(p))
        if source == "claude":
            title = next((o.get("customTitle") or o.get("aiTitle") for o in reversed(objs)
                          if o.get("type") in ("custom-title", "ai-title")
                          and (o.get("customTitle") or o.get("aiTitle"))), "")
        else:
            title = _codex_title(p)
            if title is None:
                continue
        acts = _actions(objs)
        named = sorted(s for s in slugs if any(_names(a, s) for a in acts))
        found.append({
            "source": source, "session_id": p.stem,
            "project": p.parent.name if source == "claude" else "codex",
            "title": title, "idle_min": int((time.time() - mtime) / 60),
            "tracks": named,
        })
    return sorted(found, key=lambda s: s["idle_min"])


# ── plans ────────────────────────────────────────────────────────────────────

def plans(plans_dir: Path) -> list[dict]:
    out = []
    for f in sorted(plans_dir.glob("*.md")):
        if f.name.startswith("_") or _is_conflict(f):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(text)
        # The same row reading check_plan_staleness uses: any task id, anchored
        # on the status cell. An unreadable row counts as "?", never as a status.
        # A row whose text wraps onto a line without a leading `|` ends the
        # table; the rows below it count as "unseen", status unknown, until the
        # plan file is repaired (the staleness checker reports `table_truncated`).
        rows, _, _, left = parse_task_table(body, 0)
        counts: dict[str, int] = {}
        for r in rows:
            st = r["status"] or "?"
            counts[st] = counts.get(st, 0) + 1
        if left:
            counts["unseen"] = left
        out.append({
            "plan": fm.get("plan") or f.stem, "track": fm.get("track", ""),
            "problem": fm.get("problem", ""), "active_task": str(fm.get("active_task") or "").strip(),
            "tasks": counts, "file": f.name,
        })
    return out


# ── tracks ───────────────────────────────────────────────────────────────────

def theme_map(log: Path) -> dict[str, str]:
    p = log / "_themes.toml"
    if not p.exists():
        return {}
    m: dict[str, str] = {}
    for th in dt.parse_themes(p.read_text(encoding="utf-8")):
        for s in th.tracks:
            m.setdefault(s, th.name)
    return m


def scan(log: Path, live_minutes: int, today: datetime.date | None = None,
         home: Path | None = None) -> dict:
    today = today or datetime.date.today()
    nd = log / "_next"
    themes = theme_map(log)
    raw = []
    conflicts = []
    for f in sorted(nd.glob("*.md")):
        if f.name.startswith("_"):
            continue
        if _is_conflict(f):
            conflicts.append(f.name)
            continue
        fm, body = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        raw.append((f.stem, fm, dt.parse_track_brief(fm, body)))
    slugs = {s for s, _, _ in raw}
    live = live_sessions(live_minutes, slugs, home)
    pl = plans(log / "_plans")
    live_tracks = {t for s in live for t in s["tracks"]}
    claimed = {p["track"] for p in pl if p["active_task"] and p["track"]}

    tracks = []
    for slug, fm, b in raw:
        # A thread OPENING with ~~ is struck out as resolved. Its tag can still sit
        # inside the strike (`~~**[decision-Kevin]** x~~ done`), and counting it
        # kept resolved work in the decision queue.
        threads = [{"text": t["text"], "kind": thread_kind(t["text"])} for t in b.open_threads
                   if not t["text"].startswith("~~")]
        kinds = {t["kind"] for t in threads}
        age = dt.age_days(b.last_session or (fm.get("written") or "")[:10], today)
        age = age if isinstance(age, int) else 999
        if slug in live_tracks or slug in claimed:
            lane = "live"
        elif fm.get("parked"):
            lane = "parked"  # before kevin/ready: parking a tagged track must hide it
        elif "kevin" in kinds:
            lane = "kevin"
        elif "ready" in kinds:
            lane = "ready"
        elif age > DORMANT_DAYS:
            lane = "dormant"
        else:
            lane = "untagged"
        tracks.append({
            "slug": slug, "lane": lane, "theme": themes.get(slug, ""),
            "age_days": age, "last_session": b.last_session,
            "title": b.last_session_title, "purpose": b.purpose,
            "parked": fm.get("parked", ""), "parked_reason": fm.get("parked_reason", ""),
            "threads": threads,
        })
    tracks.sort(key=lambda t: (LANES.index(t["lane"]), t["age_days"]))
    return {"tracks": tracks, "plans": pl, "live": live, "conflict_copies": conflicts,
            "lanes": {ln: sum(t["lane"] == ln for t in tracks) for ln in LANES}}


# ── rendering ────────────────────────────────────────────────────────────────

LANE_HEAD = {
    "live": "🔵 In flight now", "kevin": "🟠 Waiting on Kevin",
    "ready": "🟢 Claude can start", "parked": "⏸ Parked",
    "dormant": "⚪ Dormant (>30 days)", "untagged": "▫ Open but untagged",
}


def _cell(s: str, n: int = 110) -> str:
    s = " ".join(str(s).split()).replace("|", "\\|")
    return s if len(s) <= n else s[: n - 1] + "…"


def render(board: dict, now: str) -> str:
    L = ["---", "type: session-board", "author: ai", f"generated: {now}",
         "generator: scripts/session_board.py --write", "---", "",
         "# Session board", "",
         "Generated — do not edit by hand; rerun `python scripts/session_board.py --write`.", ""]
    L += ["| Lane | Tracks |", "|---|---|"]
    L += [f"| {LANE_HEAD[k]} | {v} |" for k, v in board["lanes"].items()]
    L += ["", "## Live sessions", ""]
    if board["live"]:
        L += ["| Idle | Source | Title | Tracks named |", "|---|---|---|---|"]
        for s in board["live"]:
            L.append(f"| {s['idle_min']}m | {s['source']} | {_cell(s['title'] or s['session_id'][:8])} "
                     f"| {', '.join(s['tracks']) or '—'} |")
    else:
        L.append("None in the window.")
    L += ["", "## Plans", "", "| Plan | Track | Claimed | Tasks |", "|---|---|---|---|"]
    for p in board["plans"]:
        tasks = ", ".join(f"{k} {v}" for k, v in sorted(p["tasks"].items()))
        L.append(f"| [[{p['file'][:-3]}]] | {p['track'] or '—'} | {p['active_task'] or '—'} | {tasks} |")
    kev = [(t["slug"], th["text"]) for t in board["tracks"] if t["lane"] != "parked"
           for th in t["threads"] if th["kind"] == "kevin"]
    L += ["", f"## Decision queue ({len(kev)})", ""]
    L += [f"- **{s}** — {_cell(x, 220)}" for s, x in kev]
    for lane in LANES:
        rows = [t for t in board["tracks"] if t["lane"] == lane]
        if not rows:
            continue
        L += ["", f"## {LANE_HEAD[lane]} ({len(rows)})", "",
              "| Track | Theme | Age | Threads | Last session |", "|---|---|---|---|---|"]
        for t in rows:
            n = len(t["threads"])
            note = t["parked_reason"] if lane == "parked" else t["title"]
            L.append(f"| [[{t['slug']}]] | {_cell(t['theme'], 30) or '—'} | {t['age_days']}d | {n} | {_cell(note)} |")
    if board["conflict_copies"]:
        L += ["", "## Sync-conflict copies (not read)", ""] + [f"- {c}" for c in board["conflict_copies"]]
    return "\n".join(L) + "\n"


# ── mutations ────────────────────────────────────────────────────────────────

def _brief(log: Path, slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slug):
        raise SystemExit(f"not a track slug: {slug!r}")
    p = log / "_next" / f"{slug}.md"
    if not p.exists():
        raise SystemExit(f"no track brief named exactly {slug!r} ({p})")
    return p


_ROW_SPLIT = re.compile(r"(?<=\|)(?=\| \[)")      # `…|| [next](next.md) |` — two rows, one line
_ROW_SLUG = re.compile(r"\|\s*\[[^\]]*\]\(([^)]+?)\.md\)")


def _drop_index_rows(log: Path, slugs: list[str]) -> int:
    """Drop the rows whose FIRST cell links `<slug>.md` — and nothing else.

    Matching anywhere in a line is not enough: the real index has two rows fused
    onto one line (`… || [usecase-audit](usecase-audit.md) | …`), and a row's
    prose can link `[[other]]`. Both made a substring drop take a second track's
    row with it. Each fused line is split into its rows, and only the named
    row goes; any rows sharing its line are kept, one per line.
    """
    idx = log / "_next" / "_index.md"
    if not idx.exists():
        return 0
    out, dropped = [], 0
    for ln in idx.read_text(encoding="utf-8").splitlines():
        segs = _ROW_SPLIT.split(ln)
        keep = []
        for seg in segs:
            m = _ROW_SLUG.match(seg)
            if m and m.group(1) in slugs:
                dropped += 1
            else:
                keep.append(seg)
        out += keep if len(keep) != len(segs) else [ln]
    atomic_write_text(idx, "\n".join(out) + "\n")
    return dropped


def _fm_str(s: str) -> str:
    """A one-line double-quoted YAML scalar that `md_frontmatter` reads back as-is.

    That parser strips the outer quotes and does NOT unescape, so `json.dumps`
    turned `waits "GPU"` into `waits \\"GPU\\"` on the way back. Swap out the two
    characters it cannot carry instead of escaping them.
    """
    return '"' + " ".join(s.split()).replace("\\", "/").replace('"', "'") + '"'


def _archive(log: Path, slug: str, disposition: str, today: str) -> Path:
    src = _brief(log, slug)
    text = set_frontmatter_field(src.read_text(encoding="utf-8"), "closed", today)
    text = set_frontmatter_field(text, "closed_disposition", _fm_str(disposition))
    arch = log / "_next" / "archive"
    arch.mkdir(exist_ok=True)
    dest, n = arch / f"{slug}-{today}.md", 2
    while dest.exists():  # a second close the same day must never overwrite the first
        dest, n = arch / f"{slug}-{today}-{n}.md", n + 1
    atomic_write_text(dest, text)
    src.unlink()
    return dest


def _open_threads_block(body: str) -> list[str]:
    """The raw lines of `## Open threads`, sub-bullets and continuations intact."""
    m = re.search(r"(?m)^## Open threads[^\n]*(?:\n|$)", body)
    if not m:
        return []
    nxt = re.search(r"(?m)^## ", body[m.end():])
    block = body[m.end(): m.end() + nxt.start()] if nxt else body[m.end():]
    return [ln for ln in block.rstrip().splitlines() if ln.strip()]


def mutate(args, log: Path) -> tuple[str, list[str]]:
    today = datetime.date.today().isoformat()
    plan: list[str] = []
    if args.cmd in ("park", "unpark"):
        p = _brief(log, args.slug)
        if args.cmd == "park":
            if not args.reason:
                raise SystemExit("park needs --reason (why it waits, and what would wake it)")
            plan = [f"set parked: {today}, parked_reason: {args.reason!r} on {p.name}"]
        else:
            plan = [f"blank parked / parked_reason on {p.name}"]
        if args.apply:
            text = p.read_text(encoding="utf-8")
            if args.cmd == "park":
                text = set_frontmatter_field(text, "parked", today)
                text = set_frontmatter_field(text, "parked_reason", _fm_str(args.reason))
            else:
                text = set_frontmatter_field(text, "parked", '""')
                text = set_frontmatter_field(text, "parked_reason", '""')
            atomic_write_text(p, text)
    elif args.cmd == "close":
        if not args.disposition:
            raise SystemExit("close needs --disposition (shipped / superseded-by <x> / dropped: why)")
        _brief(log, args.slug)
        plan = [f"archive _next/{args.slug}.md → archive/{args.slug}-{today}.md (closed_disposition)",
                f"drop the {args.slug} row from _index.md"]
        if args.apply:
            _archive(log, args.slug, args.disposition, today)
            _drop_index_rows(log, [args.slug])
    elif args.cmd == "merge":
        if args.into == args.src:
            raise SystemExit("merge needs two different tracks")
        into, src = _brief(log, args.into), _brief(log, args.src)

        def moved_lines() -> list[str]:
            # Top-level bullets get a `(from src)` prefix; sub-bullets and prose
            # continuations travel verbatim, so no detail is lost in the move.
            _, sbody = parse_frontmatter(src.read_text(encoding="utf-8"))
            return [f"- (from {args.src}) {ln[2:]}" if ln.startswith(("- ", "* ")) else ln
                    for ln in _open_threads_block(sbody)]

        n = sum(ln.startswith("- (from ") for ln in moved_lines())
        plan = [f"append {n} open thread(s) from {args.src} into {args.into} '## Open threads'",
                f"archive {args.src} (closed_disposition: merged into {args.into}) + drop its index row"]
        if args.apply:
            # Target first, source second: a crash between them duplicates the
            # threads (visible, harmless) rather than losing them.
            add = "\n".join(moved_lines())  # re-read now, not at plan time
            text = into.read_text(encoding="utf-8")
            if add:
                m = re.search(r"(?m)^## Open threads[^\n]*(?:\n|$)", text)
                if m:
                    nxt = re.search(r"(?m)^## ", text[m.end():])
                    at = m.end() + (nxt.start() if nxt else len(text) - m.end())
                    text = text[:at].rstrip("\n") + "\n" + add + "\n\n" + text[at:].lstrip("\n")
                else:
                    text = text.rstrip("\n") + "\n\n## Open threads\n" + add + "\n"
                atomic_write_text(into, text)
            _archive(log, args.src, f"merged into {args.into}", today)
            _drop_index_rows(log, [args.src])
    return ("applied" if args.apply else "dry-run"), plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true", help=f"render _next/{BOARD_NAME}")
    ap.add_argument("--live-minutes", type=int, default=120)
    ap.add_argument("--log-dir", default="", help="override the vault log dir (testing)")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("park", "unpark", "close"):
        sp = sub.add_parser(name)
        sp.add_argument("slug")
        sp.add_argument("--apply", action="store_true")
        if name == "park":
            sp.add_argument("--reason", default="")
        if name == "close":
            sp.add_argument("--disposition", default="")
    sp = sub.add_parser("merge")
    sp.add_argument("into")
    sp.add_argument("src")
    sp.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    log = Path(args.log_dir) if args.log_dir else log_dir()
    if args.cmd:
        mode, plan = mutate(args, log)
        if args.json:
            return emit_json(True, mode, f"{args.cmd}: {len(plan)} step(s)", {"steps": plan})
        print(f"[{mode}] {args.cmd}")
        for s in plan:
            print("  -", s)
        if mode == "dry-run":
            print("  (nothing written — rerun with --apply)")
        return 0

    board = scan(log, args.live_minutes)
    if args.write:
        atomic_write_text(log / "_next" / BOARD_NAME,
                          render(board, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    if args.json:
        lanes = board["lanes"]
        return emit_json(True, "ok", " · ".join(f"{k} {v}" for k, v in lanes.items()), board)
    print(" · ".join(f"{LANE_HEAD[k]} {v}" for k, v in board["lanes"].items()))
    print(f"live sessions: {len(board['live'])} · plans: {len(board['plans'])} · "
          f"decision queue: {sum(th['kind'] == 'kevin' for t in board['tracks'] for th in t['threads'])}")
    if args.write:
        print(f"wrote {log / '_next' / BOARD_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
