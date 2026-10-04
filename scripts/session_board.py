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
            on the track has a claimed row (`active`, session cell
            `YYYY-MM-DD #sid8`) or a legacy non-empty `active_task`. A live
            transcript whose id starts with a claim's sid8 is that claim's
            session, so it names the plan's track without having typed it.
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

Projects (plan project-session-integration P3): every `tag: project` note under
`10_Projects/` except area homes and completed/archived ones, in one table by
deadline — progress (plan rows, checkboxes, the open milestone, subprojects),
the next session task, the next personal task and who holds a claim. Tracks map
to projects through a project's `tracks:`; a track no project names is unowned.
Reviewer sessions (an auto security review, known by its first prompt; a Codex
sub-thread) are listed apart and never counted as work in progress or used to
put a track in a lane — but a claim they hold still counts as held.
Orphan inbox tasks: human (non-machine) open inbox lines unchanged for more
than 7 days by `git blame -w -M` (the last edit, not the creation — an edited
task reads younger); without git their age is unknown, never assumed fresh.

Dispatch (P4): `dispatch [--project X] [--slots N]` walks each project's open
plans into dependency waves (wave 1 can start now; one wave runs in parallel),
prints `/eos-session-resume <track> <plan>:<id>` — the form resume claims that
exact row from — tags the steps that wait on Kevin, and fills only the free
session slots, earliest deadline first. Two gates decide how many slots are
free (Kevin, 2026-09-28): at most WIP_CAP working Claude sessions (every Claude
session in the live window; Codex, auto reviews and any `--not-counted` sid —
the supervisor — excluded), and no new session at all while the machine's
commit charge is at or above COMMIT_LIMIT_GB.

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
import ctypes
import datetime
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from emptyos.frontmatter import parse_frontmatter as parse_fm  # noqa: E402
from emptyos.frontmatter import set_frontmatter_field  # noqa: E402
from emptyos.runtime.atomic_io import atomic_write_text  # noqa: E402
from emptyos.sdk import dev_tracks as dt  # noqa: E402
from emptyos.sdk.project_meta import checkbox_counts, milestones, parse_project  # noqa: E402
from md_frontmatter import parse_frontmatter  # noqa: E402
from emptyos.plan_table import parse_tasks as parse_task_table  # noqa: E402
from emptyos.plan_table import parse_claim, row_claims  # noqa: E402
from reconcile_tracks import _is_conflict  # noqa: E402
from scanner_lib import emit_json  # noqa: E402
from triage_inbox import MACHINE_LINE  # noqa: E402

LANES = ("live", "parked", "kevin", "ready", "dormant", "untagged")
KEVIN_TAGS = ("[decision-kevin]", "[blocked-human]")
READY_TAG = "[open-code]"
DORMANT_DAYS = dt.STALE_DAYS  # same ">30d = stale" line devboard's classify_track draws
TAIL_BYTES = 256_000  # Claude Code re-writes ai-title every turn, so the last one is near the end
BOARD_NAME = "_board.md"
# An auto review is not work in progress: half of 32 "live" sessions were these
# on 2026-09-26, and counting them made the WIP cap meaningless. Recognised by
# the prompt the review harness always opens with, not by the title — a user can
# title a real work session "Fix security review findings".
REVIEWER_PROMPT = "Review this change for security vulnerabilities."
HIDDEN_STATUSES = ("completed", "archived")
ORPHAN_DAYS = 7  # areas.md: a task may sit in the inbox for at most 7 days


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


# Measured 2026-09-28 over the 400 newest transcripts: the first prompt is always
# within the first few lines. The bound only stops a malformed file being read
# to the end; it is far above anything real.
FIRST_PROMPT_LINES = 2000


def _first_prompt(path: Path) -> str:
    """The first thing typed into a Claude transcript.

    Read line by line from the top, not as a fixed-size head: an auto review
    opens with a `queue-operation` line carrying its whole diff, and on
    2026-09-28 (edcfab14) that pushed the first `user` line past a 256 KB head,
    so the review counted as work. The enqueue line carries the prompt itself
    (it equalled the first user prompt in all 319 of 400 transcripts that had
    one), so it is read too."""
    with path.open("rb") as fh:
        for n, raw in enumerate(fh):
            if n >= FIRST_PROMPT_LINES:
                break
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            if (o.get("type") == "queue-operation" and o.get("operation") == "enqueue"
                    and isinstance(o.get("content"), str)):
                return o["content"]
            if o.get("type") != "user" or o.get("isMeta"):
                continue
            c = (o.get("message") or {}).get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c
                                if isinstance(b, dict) and b.get("type") == "text")
    return ""


_STILL_ACTIVE = 259


def _process_started(pid: int) -> int | None:
    """None when the process is not running; otherwise its creation time as a
    Windows FILETIME, or 0 when it is running but the time can't be read
    (non-Windows, or GetProcessTimes failed)."""
    if not 0 < pid < 2 ** 32:
        return None
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except (OSError, ValueError, OverflowError):
            return None
        return 0
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        # An exited process can still be opened while anyone holds a handle to
        # it, and it keeps its creation time — the exit code is what says so.
        code = wintypes.DWORD()
        if k32.GetExitCodeProcess(wintypes.HANDLE(h), ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return None
        t = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(wintypes.HANDLE(h), *(ctypes.byref(x) for x in t)):
            return 0
        return (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


def _is_reviewer_session(home: Path, session_id: str) -> bool:
    """Whether this session's own transcript opens with the review prompt —
    checked for every open session, not only those inside the live window."""
    for f in (home / ".claude" / "projects").glob(f"*/{session_id}.jsonl"):
        try:
            return _first_prompt(f).startswith(REVIEWER_PROMPT)
        except OSError:
            return False
    return False


def open_claude_sessions(home: Path | None = None) -> dict[str, dict] | None:
    """`{sid8: {pid, status, name, entrypoint, reviewer}}` for every Claude Code
    session still open, or None when there is no registry to read.

    Claude Code writes `~/.claude/sessions/<pid>.json` (sessionId, status
    idle/busy/waiting, entrypoint cli/claude-desktop, procStart = the process's
    creation FILETIME), and the file can outlive a crash. An entry counts as
    open when its pid is running and — where both are known — started at
    `procStart`, so a reused pid is not the session.

    Every doubt resolves toward OPEN, because the two errors are not equal: an
    extra session only holds back a slot, while a missing one lets dispatch open
    too many. So an entry that can't be parsed (the registry is Claude Code's
    internal format and may drift) counts as open with status `unreadable`, and
    a missing `procStart` or an unreadable start time does not rule a live pid
    out."""
    home = home or Path.home()
    reg = home / ".claude" / "sessions"
    if not reg.is_dir():
        return None
    out = {}
    for f in reg.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8-sig"))
            pid, sid = int(d["pid"]), str(d["sessionId"])
        except (OSError, ValueError, KeyError, TypeError):
            out[f"?{f.stem}"[:8].lower()] = {"pid": None, "status": "unreadable", "name": f.name,
                                             "entrypoint": "", "reviewer": False}
            continue
        started = _process_started(pid)
        if started is None:
            continue
        want = str(d.get("procStart") or "")
        if started and want.isdigit() and int(want) != started:
            continue  # the pid now belongs to another process
        out[sid[:8].lower()] = {"pid": pid, "status": d.get("status", ""), "name": d.get("name", ""),
                                "entrypoint": d.get("entrypoint", ""),
                                "reviewer": _is_reviewer_session(home, sid)}
    return out


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
            reviewer = _first_prompt(p).startswith(REVIEWER_PROMPT)
        else:
            title = _codex_title(p)
            reviewer = title is None  # a sub-thread: Codex spawns one per auto-review
            title = title or ""
        acts = _actions(objs)
        named = sorted(s for s in slugs if any(_names(a, s) for a in acts))
        found.append({
            "source": source, "session_id": p.stem,
            "project": p.parent.name if source == "claude" else "codex",
            "title": title, "idle_min": int((time.time() - mtime) / 60),
            "tracks": named, "reviewer": reviewer,
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
        active = str(fm.get("active_task") or "").strip()
        # Claims live on the rows, so one plan can hold several at once. An
        # `active` row with no stamp is a half-written claim, not a claim —
        # check_plan_staleness reports it; the board does not act on it.
        claims = [c for c in row_claims(rows, active) if c["stamped"]]
        out.append({
            "plan": fm.get("plan") or f.stem, "track": fm.get("track", ""),
            "problem": fm.get("problem", ""), "active_task": active,
            "claims": claims, "tasks": counts, "file": f.name, "_rows": rows,
        })
    return out


# ── projects ─────────────────────────────────────────────────────────────────

def vault_of(log: Path) -> Path | None:
    """The vault root when `log` is `{vault}/10_Projects/emptyos/log`, else None
    (a test log dir, or an override pointing somewhere else)."""
    if log.parent.name == "emptyos" and log.parents[1].name == "10_Projects":
        return log.parents[2]
    return None


def _is_project(fm: dict) -> bool:
    # emptyos.frontmatter reads both `tags: [project]` and the block form.
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return "project" in [str(t).strip() for t in tags]


def _norm(v: str | None) -> str:
    """kind/status compared the way the Projects app compares kind (P2
    `is_area_home`): trimmed, lower-case, `_` read as `-`."""
    return (v or "").strip().lower().replace("_", "-")


def _plan_rows(plans_dir: Path, name: str) -> list[dict] | None:
    """Rows of a closed plan in `_plans/done/`, or None if no such plan."""
    f = plans_dir / "done" / f"{name}.md"
    if not f.is_file():
        return None
    _, body = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
    return parse_task_table(body, 0)[0]


def _next_session_task(rows_by_plan: list[tuple[str, list[dict]]]) -> str:
    """First `queued` row, in plan order, whose every dependency is `done`."""
    for plan, rows in rows_by_plan:
        done = {r["id"] for r in rows if r["status"] == "done"}
        for r in rows:
            if r["status"] == "queued" and all(d in done for d in r["deps"]):
                return f"{plan} · {r['id']}: {r.get('task', '')}"
    return ""


def _progress(p: dict) -> str:
    parts = []
    if p["sessions"][1]:
        more = f" +{p['sessions_unseen']} unread" if p.get("sessions_unseen") else ""
        parts.append(f"sessions {p['sessions'][0]}/{p['sessions'][1]}{more}")
    if p.get("missing_plans"):
        parts.append("no such plan: " + ", ".join(p["missing_plans"]))
    if p["tasks"][1]:
        parts.append(f"tasks {p['tasks'][0]}/{p['tasks'][1]}")
    if p["milestone"]:
        parts.append(p["milestone"])
    if p["subprojects"][1]:
        parts.append(f"sub {p['subprojects'][0]}/{p['subprojects'][1]}")
    return " · ".join(parts)


def projects(vault: Path, plan_list: list[dict], today: datetime.date,
             plans_dir: Path | None = None) -> dict:
    """The Projects section: listed rows plus counts of what was left out."""
    root = vault / "10_Projects"
    by_plan = {p["plan"]: p for p in plan_list}
    rows, hidden, homes = [], {}, 0
    notes = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        f = d / f"{d.name}.md"
        if not d.is_dir() or d.name.startswith((".", "_")) or not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if _is_project(parse_fm(text)):
            notes.append((parse_project(text, d.name), parse_frontmatter(text)[1]))
    children: dict[str, list[str]] = {}
    # Ownership reads EVERY project note: a track named by a completed project
    # or an area home is owned, even though that note is not listed below.
    owners: dict[str, list[str]] = {}
    for meta, _ in notes:
        if meta["parent"]:
            children.setdefault(meta["parent"], []).append(meta["status"] or "")
        for t in meta["tracks"]:
            owners.setdefault(t, []).append(meta["id"])
    for meta, body in notes:
        if _norm(meta["kind"]) == "area-home":
            homes += 1
            continue
        if _norm(meta["status"]) in HIDDEN_STATUSES:
            hidden[_norm(meta["status"])] = hidden.get(_norm(meta["status"]), 0) + 1
            continue
        plans_here = [by_plan[x] for x in meta["plans"] if x in by_plan]
        closed = {x: _plan_rows(plans_dir, x) for x in meta["plans"]
                  if x not in by_plan and plans_dir is not None}
        missing = [x for x in meta["plans"] if x not in by_plan and closed.get(x) is None]
        prow = [r for pl in plans_here for r in pl["_rows"]]
        prow += [r for rs in closed.values() if rs for r in rs]
        unseen = sum(pl["tasks"].get("unseen", 0) for pl in plans_here)
        boxes = checkbox_counts(body)
        open_ms = next((m for m in milestones(body) if m["status"] != "closed"), None)
        ms = ""
        if open_ms:
            c = checkbox_counts(body, open_ms["id"])
            total = c["done"] + c["open"]
            # A milestone with no linked boxes is still worth naming, never "0/0".
            ms = f"{open_ms['id']} {c['done']}/{total}" if total else open_ms["id"]
        kids = children.get(meta["id"], [])
        first_open = re.search(r"(?m)^\s*- \[ \] (.+)$", body)
        left = dt.age_days(meta["deadline"] or "", today) if meta["deadline"] else None
        rows.append({
            "id": meta["id"], "area": meta["area"], "status": meta["status"],
            "deadline": meta["deadline"],
            # age_days is days SINCE a date, so a future deadline comes back negative.
            "days_left": -left if isinstance(left, int) else None,
            "sessions": (sum(r["status"] == "done" for r in prow), len(prow)),
            "sessions_unseen": unseen,
            "missing_plans": missing,
            "tasks": (boxes["done"], boxes["done"] + boxes["open"]),
            "milestone": ms,
            "subprojects": (sum(s == "completed" for s in kids), len(kids)),
            "next_session_task": _next_session_task([(pl["plan"], pl["_rows"]) for pl in plans_here]),
            "next_personal_task": first_open.group(1).strip() if first_open else "",
            "holders": [f"{pl['plan']} · {c['id']}" + (f" #{c['sid']}" if c["sid"] else "")
                        + ("" if c.get("holder_live") else " (no live session)")
                        for pl in plans_here for c in pl["claims"]],
            "tracks": meta["tracks"], "plans": meta["plans"],
        })
    # Deadline first; undated last; ties by id so the order is stable.
    rows.sort(key=lambda r: (r["deadline"] is None, r["deadline"] or "", r["id"]))
    return {"rows": rows, "hidden": hidden, "area_homes": homes, "owners": owners}


def inbox_ages(vault: Path, today: datetime.date) -> dict:
    """Open inbox tasks split into machine output and human tasks, and the human
    ones older than ORPHAN_DAYS by `git blame` author time. `orphans` is None
    when the age cannot be read (no git, no file history) — unknown, not zero."""
    inbox = vault / "10_Projects" / "inbox" / "inbox.md"
    out = {"human_open": 0, "machine_open": 0, "orphans": None}
    if not inbox.exists():
        return out
    lines = inbox.read_text(encoding="utf-8", errors="replace").splitlines()
    human = []
    for n, ln in enumerate(lines, 1):
        if not re.match(r"\s*- \[ \] ", ln):
            continue
        if MACHINE_LINE.search(re.sub(r"^\s*- \[ \] ", "", ln)):
            out["machine_open"] += 1
        else:
            human.append(n)
    out["human_open"] = len(human)
    try:
        blame = subprocess.run(
            ["git", "-C", str(vault), "blame", "-w", "-M", "--line-porcelain", "--",
             str(inbox.relative_to(vault)).replace("\\", "/")],
            capture_output=True, timeout=30, check=True).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return out
    when: dict[int, int] = {}
    line_no = 0
    for ln in blame.splitlines():
        m = re.match(r"^[0-9a-f]{40} \d+ (\d+)", ln)
        if m:
            line_no = int(m.group(1))
        elif ln.startswith("author-time ") and line_no:
            when[line_no] = int(ln.split()[1])
    if not when:
        return out
    cutoff = datetime.datetime.combine(today, datetime.time()).timestamp() - ORPHAN_DAYS * 86400
    # Blame dates an uncommitted line "now" (Not Committed Yet), so a task added
    # since the last vault commit is new, never an orphan.
    out["orphans"] = sum(1 for n in human if n in when and when[n] < cutoff)
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
         home: Path | None = None, vault: Path | None = None) -> dict:
    today = today or datetime.date.today()
    vault = vault if vault is not None else vault_of(log)
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
    seen = live_sessions(live_minutes, slugs, home)
    live = [s for s in seen if not s["reviewer"]]
    reviewers = [s for s in seen if s["reviewer"]]
    pl = plans(log / "_plans")
    # A live transcript is matched to the task it claimed by exact session id —
    # the file stem is the full id, the claim carries its first 8 characters.
    # Every session in the window, reviewers included: a claim held by a session
    # that is also a reviewer is still held, not "(no live session)".
    live_sids = {s["session_id"][:8].lower() for s in seen if s["source"] == "claude"}
    for p in pl:
        for c in p["claims"]:
            # False means no transcript with this sid in the window: idle or
            # dead, which the board cannot tell apart — widen --live-minutes.
            c["holder_live"] = bool(c["sid"]) and c["sid"] in live_sids
    for s in live:
        sid = s["session_id"][:8].lower() if s["source"] == "claude" else ""
        s["claims"] = [f"{p['plan']} · {c['id']}" for p in pl for c in p["claims"]
                       if sid and c["sid"] == sid]
        s["tracks"] = sorted(set(s["tracks"]) | {p["track"] for p in pl for c in p["claims"]
                                                 if sid and c["sid"] == sid and p["track"]})
    live_tracks = {t for s in live for t in s["tracks"]}
    claimed = {p["track"] for p in pl if (p["claims"] or p["active_task"]) and p["track"]}

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
    proj = projects(vault, pl, today, log / "_plans") if vault else {
        "rows": [], "hidden": {}, "area_homes": 0, "owners": {}}
    for t in tracks:
        t["projects"] = proj["owners"].get(t["slug"], [])
    for p in pl:
        p.pop("_rows", None)  # parser rows are an input to the table, not board output
    return {"tracks": tracks, "plans": pl, "live": live, "reviewers": reviewers,
            "wip": len(live), "projects": proj,
            "unowned": [t["slug"] for t in tracks if not t["projects"]],
            "inbox": inbox_ages(vault, today) if vault else None,
            "conflict_copies": conflicts,
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


def _days_cell(r: dict) -> str:
    d = r["days_left"]
    if d is None:
        return "—"
    return f"⚠ {-d}d overdue" if d < 0 else f"{d}d"


def _render_projects(board: dict) -> list[str]:
    proj = board["projects"]
    L = ["", f"## Projects ({len(proj['rows'])})", ""]
    if proj["rows"]:
        L += ["| Deadline | Left | Area | Project | Progress | Next session task "
              "| Next personal task | Holder |", "|---|---|---|---|---|---|---|---|"]
        for r in proj["rows"]:
            L.append(f"| {r['deadline'] or '—'} | {_days_cell(r)} | {r['area'] or '—'} "
                     f"| [[{_cell(r['id'], 60)}]] | {_cell(_progress(r), 90) or '—'} | {_cell(r['next_session_task'], 70) or '—'} "
                     f"| {_cell(r['next_personal_task'], 60) or '—'} | {', '.join(r['holders']) or '—'} |")
    else:
        L.append("No project notes found.")
    left_out = [f"{v} {k}" for k, v in sorted(proj["hidden"].items())]
    if proj["area_homes"]:
        left_out.append(f"{proj['area_homes']} area homes")
    if left_out:
        L += ["", "Not listed: " + ", ".join(left_out) + "."]
    ib = board.get("inbox")
    if ib:
        age = "age unknown (no git history)" if ib["orphans"] is None else \
            f"{ib['orphans']} older than {ORPHAN_DAYS} days"
        L += ["", f"Inbox: {ib['human_open']} open human tasks ({age}); "
              f"{ib['machine_open']} machine lines."]
    return L


def render(board: dict, now: str) -> str:
    L = ["---", "type: session-board", "author: ai", f"generated: {now}",
         "generator: scripts/session_board.py --write", "---", "",
         "# Session board", "",
         "Generated — do not edit by hand; rerun `python scripts/session_board.py --write`.", ""]
    L += ["| Lane | Tracks |", "|---|---|"]
    L += [f"| {LANE_HEAD[k]} | {v} |" for k, v in board["lanes"].items()]
    L += _render_projects(board)
    L += ["", f"## Live sessions ({board['wip']} in the window)", ""]
    if board["live"]:
        L += ["| Idle | Source | Title | Tracks named | Claimed task |", "|---|---|---|---|---|"]
        for s in board["live"]:
            L.append(f"| {s['idle_min']}m | {s['source']} | {_cell(s['title'] or s['session_id'][:8])} "
                     f"| {', '.join(s['tracks']) or '—'} | {', '.join(s.get('claims', [])) or '—'} |")
    else:
        L.append("None in the window.")
    if board["reviewers"]:
        titles = ", ".join(_cell(s["title"] or s["session_id"][:8], 40) for s in board["reviewers"])
        L += ["", f"Reviewers ({len(board['reviewers'])}, not counted as work in progress): {titles}"]
    L += ["", "## Plans", "", "| Plan | Track | Claimed | Tasks |", "|---|---|---|---|"]
    for p in board["plans"]:
        tasks = ", ".join(f"{k} {v}" for k, v in sorted(p["tasks"].items()))
        held = ", ".join(
            f"{c['id']}{' #' + c['sid'] if c['sid'] else ''}"
            f"{'' if c['holder_live'] else ' (no live session)'}" for c in p["claims"])
        L.append(f"| [[{p['file'][:-3]}]] | {p['track'] or '—'} | {held or p['active_task'] or '—'} | {tasks} |")
    kev = [(t["slug"], th["text"]) for t in board["tracks"] if t["lane"] != "parked"
           for th in t["threads"] if th["kind"] == "kevin"]
    L += ["", f"## Decision queue ({len(kev)})", ""]
    L += [f"- **{s}** — {_cell(x, 220)}" for s, x in kev]
    for lane in LANES:
        rows = [t for t in board["tracks"] if t["lane"] == lane]
        if not rows:
            continue
        L += ["", f"## {LANE_HEAD[lane]} ({len(rows)})", "",
              "| Track | Project | Theme | Age | Threads | Last session |", "|---|---|---|---|---|---|"]
        for t in rows:
            n = len(t["threads"])
            note = t["parked_reason"] if lane == "parked" else t["title"]
            proj = ", ".join(f"[[{x}]]" for x in t["projects"]) or "—"
            L.append(f"| [[{t['slug']}]] | {proj} | {_cell(t['theme'], 30) or '—'} | {t['age_days']}d "
                     f"| {n} | {_cell(note)} |")
    active_unowned = [t["slug"] for t in board["tracks"]
                      if not t["projects"] and t["lane"] not in ("dormant", "parked")]
    if board["unowned"]:
        L += ["", f"## Tracks no project owns ({len(board['unowned'])})", "",
              f"Listed: the {len(active_unowned)} not dormant or parked; the other "
              f"{len(board['unowned']) - len(active_unowned)} are only counted. "
              "Own one by adding it to a project's `tracks:`.", ""]
        if active_unowned:
            L.append(", ".join(f"[[{s}]]" for s in active_unowned))
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


# ── dispatch ─────────────────────────────────────────────────────────────────

WIP_CAP = 4             # working Claude sessions at once (Kevin, 2026-09-28)
COMMIT_LIMIT_GB = 90    # no new session at or above this commit charge (Kevin, 2026-09-28)
# A step that waits on Kevin: the plan contract's tags; the gates the north star
# keeps human — deploy, publish, spend, and outbound messages (CLAUDE.md § north
# star), in English and Chinese; and any task that names him ("on Kevin's
# approval"). Errs toward gating: a gated step is only left out of "open now",
# never hidden from the roadmap, and resume can still claim it when named.
KEVIN_GATE = re.compile(
    r"\[(?:decision-kevin|blocked-human)\]|\bKevin\b"
    r"|(?:deploy|publish|purchas|spend|payment|billing|outbound|\bsend|\bemail|\bbuy\b)"
    r"|发布|部署|购买|付费|付款|发送",
    re.IGNORECASE)


def commit_charge_gb() -> float | None:
    """This machine's commit charge in GB, or None where it can't be read
    (non-Windows, or the call failed) — the gate then says unknown, never 0.
    Same number as Task Manager's "Committed"."""
    if sys.platform != "win32":
        return None

    class _PerfInfo(ctypes.Structure):  # PERFORMANCE_INFORMATION (psapi.h)
        _fields_ = ([("cb", ctypes.c_uint32)]
                    + [(n, ctypes.c_size_t) for n in (
                        "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal",
                        "PhysicalAvailable", "SystemCache", "KernelTotal", "KernelPaged",
                        "KernelNonpaged", "PageSize")]
                    + [(n, ctypes.c_uint32) for n in ("HandleCount", "ProcessCount", "ThreadCount")])

    info = _PerfInfo()
    info.cb = ctypes.sizeof(info)
    try:
        ok = ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb)
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    return info.CommitTotal * info.PageSize / 1024 ** 3


def open_plan_rows(plans_dir: Path) -> dict[str, dict]:
    """`{plan: {track, rows}}` for every open plan (sync-conflict copies skipped)."""
    out = {}
    for f in sorted(plans_dir.glob("*.md")):
        if f.name.startswith("_") or _is_conflict(f):
            continue
        fm, body = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        out[fm.get("plan") or f.stem] = {"track": fm.get("track", ""),
                                         "rows": parse_task_table(body, 0)[0]}
    return out


def waves(rows: list[dict]) -> tuple[list[list[dict]], list[dict]]:
    """Unfinished rows levelled by `depends_on`: wave 1 depends only on done
    rows; wave n on rows of earlier waves. A `blocked` row sits in its wave but
    unblocks nothing — its dependents are stuck behind it, not shown as the
    next wave. Everything that can't be placed comes back as `stuck` with why:
    an unreadable row, an unknown id, a dependency cycle, or a stuck/blocked row."""
    done = {r["id"] for r in rows if r["status"] == "done"}
    known = {r["id"] for r in rows}
    todo = [r for r in rows if r["status"] != "done" and not r.get("unreadable")]
    placed: set[str] = set()   # in a wave
    passes: set[str] = set()   # in a wave AND able to finish (not blocked)
    out: list[list[dict]] = []
    while True:
        wave = [r for r in todo if r["id"] not in placed
                and all(d in done or d in passes for d in r["deps"])]
        if not wave:
            break
        out.append(wave)
        placed |= {r["id"] for r in wave}
        passes |= {r["id"] for r in wave if r["status"] != "blocked"}
    left = {r["id"]: r for r in rows if r["status"] != "done" and r["id"] not in placed}

    def in_cycle(start: str) -> bool:
        seen, stack = set(), [start]
        while stack:
            for d in left.get(stack.pop(), {}).get("deps", []):
                if d == start:
                    return True
                if d in left and d not in seen:
                    seen.add(d)
                    stack.append(d)
        return False

    stuck = []
    for r in left.values():
        blockers = [d for d in r["deps"] if d not in done and d not in passes]
        if r.get("unreadable"):
            why = "row is unreadable (no status cell) — repair the plan table"
        elif any(d not in known for d in r["deps"]):
            why = "depends on an unknown id: " + ", ".join(d for d in r["deps"] if d not in known)
        elif in_cycle(r["id"]):
            why = "in a dependency cycle: " + ", ".join(blockers)
        else:
            why = "waits on a stuck or blocked row: " + ", ".join(blockers)
        stuck.append({**r, "why": why})
    return out, stuck


def _step(plan: str, track: str, r: dict, wave: int, live_sids: set[str]) -> dict:
    # A blocked row waits on Kevin unless its own tag says Claude can do it.
    gated = bool(KEVIN_GATE.search(r.get("task", ""))) or (
        r["status"] == "blocked" and "[open-code]" not in r.get("task", "").lower())
    sid = parse_claim(r["session"])[1] if r["status"] == "active" else ""
    return {"plan": plan, "id": r["id"], "task": r.get("task", ""), "track": track,
            "status": r["status"], "wave": wave, "kevin": gated,
            "holder": f"#{sid}" if sid else "", "holder_live": bool(sid) and sid in live_sids,
            # The row itself, so resume claims exactly this step (its Step 1).
            "command": f"/eos-session-resume {track} {plan}:{r['id']}" if track else "",
            "why": r.get("why", "")}


def dispatch(board: dict, plan_rows: dict[str, dict], *, project: str | None = None,
             slots: int | None = None, cap: int = WIP_CAP, commit_gb: float | None = None,
             limit_gb: float = COMMIT_LIMIT_GB, not_counted: tuple[str, ...] = (),
             open_sessions: dict[str, dict] | None = None) -> dict:
    """The session roadmap per project and which new sessions to open now.

    Working = every OPEN Claude session (`open_claude_sessions`: idle ones too —
    an open session holding work still occupies a slot), minus auto reviews and
    the `not_counted` sids. Codex never counts. Without the session registry
    the count falls back to transcripts touched in the live window, and says so:
    that count includes windows already closed."""
    skip = {x.strip().lstrip("#").lower()[:8] for x in not_counted}
    reviewers = {s["session_id"][:8].lower() for s in board.get("reviewers", [])}
    if open_sessions is not None:
        working = [{"sid": sid, "status": " ".join(x for x in (
                        v.get("status", ""), "desktop" if v.get("entrypoint") == "claude-desktop" else "") if x)}
                   for sid, v in sorted(open_sessions.items())
                   if sid not in skip and sid not in reviewers and not v.get("reviewer")]
    else:
        working = [{"sid": s["session_id"][:8].lower(), "status": "touched"} for s in board["live"]
                   if s["source"] == "claude" and s["session_id"][:8].lower() not in skip]
    memory_hold = commit_gb is not None and commit_gb >= limit_gb
    free = 0 if memory_hold else max(0, cap - len(working))
    if slots is not None:
        free = min(free, max(0, slots))
    live_tracks = {t for s in board["live"] for t in s["tracks"]}
    live_sids = {s["session_id"][:8].lower()
                 for s in board["live"] + board.get("reviewers", []) if s["source"] == "claude"}
    # A claim held by an open session that has idled past the window is still held.
    live_sids |= set(open_sessions or {})
    lane = {t["slug"]: t["lane"] for t in board["tracks"]}

    roadmap, candidates = [], []
    rows = board["projects"]["rows"]
    if project:
        rows = [r for r in rows if r["id"] == project]
    for pr in rows:  # already deadline-sorted
        entry = {"project": pr["id"], "deadline": pr["deadline"], "days_left": pr["days_left"],
                 "waves": [], "stuck": [], "tracks_without_plan": [], "unknown_tracks": [],
                 "missing_plans": list(pr.get("missing_plans", [])), "note": ""}
        plan_tracks = set()
        for name in pr["plans"]:
            info = plan_rows.get(name)
            if not info:
                continue  # closed (in done/) — or unknown, reported via missing_plans
            plan_tracks.add(info["track"])
            ws, stuck = waves(info["rows"])
            for n, wave in enumerate(ws, 1):
                while len(entry["waves"]) < n:
                    entry["waves"].append([])
                entry["waves"][n - 1] += [_step(name, info["track"], r, n, live_sids) for r in wave]
            entry["stuck"] += [_step(name, info["track"], r, 0, live_sids) for r in stuck]
        for t in pr["tracks"]:
            if t in plan_tracks:
                continue
            # Only a track with a brief can be resumed; anything else is a typo.
            (entry["tracks_without_plan"] if t in lane else entry["unknown_tracks"]).append(t)
        if not entry["waves"] and not entry["stuck"]:
            if entry["tracks_without_plan"]:
                entry["note"] = "no plan: open a session on the track"
            else:
                nxt = pr["next_personal_task"]
                entry["note"] = "no session work" + (f"; your next task: {nxt}" if nxt else "")
        roadmap.append(entry)
        # Candidates to start now: first-wave, unclaimed, not waiting on Kevin.
        for st in (entry["waves"][0] if entry["waves"] else []):
            if st["status"] == "queued" and not st["kevin"] and st["track"]:
                candidates.append({**st, "project": pr["id"], "deadline": pr["deadline"]})
        for t in entry["tracks_without_plan"]:
            if lane.get(t) == "ready":  # an [open-code] thread Claude can start
                candidates.append({"project": pr["id"], "deadline": pr["deadline"], "plan": "",
                                   "id": "", "task": "open-code thread", "track": t,
                                   "command": f"/eos-session-resume {t}"})
    picks, used = [], set(live_tracks)
    for c in candidates:
        if len(picks) >= free:
            break
        if c["track"] in used:  # one session per track: two on one collide at commit
            continue
        used.add(c["track"])
        picks.append(c)
    return {"cap": cap, "working": [w["sid"] for w in working],
            "working_status": {w["sid"]: w["status"] for w in working},
            "count_basis": "open" if open_sessions is not None else "touched in window",
            "commit_gb": round(commit_gb, 1) if commit_gb is not None else None,
            "limit_gb": limit_gb, "memory_hold": memory_hold, "free": free,
            "open_now": picks, "roadmap": roadmap}


def render_dispatch(d: dict) -> str:
    mem = ("unknown" if d["commit_gb"] is None
           else f"{d['commit_gb']} GB of {d['limit_gb']} GB limit")
    who = ", ".join(f"#{w} {d['working_status'].get(w, '')}".rstrip() for w in d["working"]) or "none"
    basis = ("open" if d["count_basis"] == "open"
             else "touched in the window — no session registry, so closed windows count too")
    L = [f"Working Claude sessions ({basis}): {len(d['working'])} of {d['cap']} ({who}). "
         f"Commit charge: {mem}."]
    if d["memory_hold"]:
        L.append("HOLD: commit charge is at or above the limit — open no new session.")
    L.append(f"Free slots: {d['free']}.")
    L += ["", "Open now:" if d["open_now"] else "Open now: nothing."]
    for c in d["open_now"]:
        what = f"{c['plan']} · {c['id']}: {_cell(c['task'], 60)}" if c["plan"] else c["task"]
        L.append(f"  {c['command']}   ({c['project']}, due {c['deadline'] or '—'}) — {what}")
    for e in d["roadmap"]:
        due = f"due {e['deadline']}" if e["deadline"] else "no deadline"
        L += ["", f"## {e['project']} ({due})"]
        for n, wave in enumerate(e["waves"], 1):
            L.append(f"  wave {n}:")
            for st in wave:
                if st["status"] == "active":
                    state = (f" [running {st['holder']}]" if st["holder_live"]
                             else f" [claimed {st['holder'] or 'unstamped'}, no live session]")
                else:
                    state = {"queued": "", "blocked": " [blocked]"}.get(st["status"], f" [{st['status']}]")
                gate = " [Kevin]" if st["kevin"] else ""
                L.append(f"    {st['command'] or '(plan has no track)'}{state}{gate}: {_cell(st['task'], 80)}")
        for st in e["stuck"]:
            L.append(f"  stuck: {st['plan']} · {st['id']} — {st['why']}")
        for t in e["tracks_without_plan"]:
            L.append(f"  track without a plan: /eos-session-resume {t}")
        for t in e["unknown_tracks"]:
            L.append(f"  tracks: names {t!r}, which has no brief in _next/")
        for x in e["missing_plans"]:
            L.append(f"  plans: names {x!r}, which is neither open nor in _plans/done/")
        if e["note"]:
            L.append(f"  {e['note']}")
    return "\n".join(L) + "\n"


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
    sp = sub.add_parser("dispatch")
    # Accepted after the subcommand as well; SUPPRESS keeps the top-level value.
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sp.add_argument("--live-minutes", type=int, default=argparse.SUPPRESS)
    sp.add_argument("--project", default=None)
    sp.add_argument("--slots", type=int, default=None, help="open at most N (never above the free slots)")
    sp.add_argument("--not-counted", action="append", default=[], metavar="SID8",
                    help="a session that is not work in progress (the supervisor); repeatable")
    args = ap.parse_args()

    log = Path(args.log_dir) if args.log_dir else log_dir()
    if args.cmd == "dispatch":
        board = scan(log, args.live_minutes)
        d = dispatch(board, open_plan_rows(log / "_plans"), project=args.project, slots=args.slots,
                     commit_gb=commit_charge_gb(), not_counted=tuple(args.not_counted),
                     open_sessions=open_claude_sessions())
        if args.project and not d["roadmap"]:
            raise SystemExit(f"no listed project named exactly {args.project!r}")
        if args.json:
            return emit_json(True, "ok", f"{len(d['open_now'])} to open · {d['free']} free", d)
        print(render_dispatch(d), end="")
        return 0
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
