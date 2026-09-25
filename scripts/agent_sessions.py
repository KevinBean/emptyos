#!/usr/bin/env python3
"""agent_sessions — one screen answering "which agent needs me?".

Reads every WezTerm pane via `wezterm cli`, classifies each coding-agent
session as NEEDS YOU / WORKING / IDLE, and prints the last few meaningful
lines of each so you never have to open a tab to find out what it wants.

    python scripts/agent_sessions.py            # print once
    python scripts/agent_sessions.py --watch    # live dashboard (Ctrl+C to quit)
    python scripts/agent_sessions.py --json     # machine-readable

WHY PANE TEXT, NOT THE TITLE OR THE HOOK
----------------------------------------
Three signals were on the table and only one is both authoritative and rich:

  * The tab title's spinner glyph (`◐◑◒◓` vs `✳`) is LAGGY — measured
    2026-08-16: pane 8 still advertised `◑` in `wezterm cli list` while its
    own text already read "Cogitated for 22m 16s" with an empty prompt. It
    tracks the title update, not the turn.
  * The `claude_status` user var (written by agent_fleet_hook.py) is
    accurate but reachable only from wezterm's Lua, and carries no content —
    it can say "waiting" but never *what for*, which is the whole question.
  * The pane's own rendered text carries both the state AND the sentence the
    agent is waiting on. That is what this reads.

THE DISCRIMINATOR (measured against 8 live sessions, 2026-08-16)
----------------------------------------------------------------
Claude Code renders one status line directly above its input box:

    ✶ Improvising… (36s · ↓ 2.0k tokens)      <- present participle + timer
    ✻ Cogitated for 22m 16s                   <- past tense, no timer

Present participle + parenthesised elapsed = a turn is RUNNING. Past tense
("Worked for", "Churned for", "Sautéed for") = the turn ENDED and the agent
is holding the prompt. The verb itself is randomised flavour text and is
never matched on — only the tense-carrying shape around it.

A permission gate outranks both: Claude draws a numbered option list
("❯ 1. Yes"), which means it is blocked and nothing moves until you answer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

# ── Claude Code's screen furniture ───────────────────────────────────────────
# Exact codepoints, kept as named constants because they are indistinguishable
# from each other at a glance in a terminal and a mix-up silently breaks a
# whole state.
#: Spinner frames the status line cycles through, MEASURED by sampling three
#: live panes over ~2s (2026-08-16), plus the frames seen in pane titles.
#: Deliberately NOT split into "working" and "done" constants — the SAME
#: frames appear on both lines, so they identify nothing, and naming them that
#: way is exactly what produced the first version's "0 working" bug. They are
#: used only to strip a leading frame, never to decide a state.
SPINNER_FRAMES = (
    "✽", "✻", "✶", "✢", "·",   # ✽ ✻ ✶ ✢ ·  status line
    "✳", "◐", "◓", "◑", "◒", "*",  # ✳ ◐ ◓ ◑ ◒  titles
)
GLY_RECAP = "\u203b"     # ※ recap block
GLY_PROMPT = "\u276f"    # ❯ input caret / selected option
GLY_BULLET = "\u25cf"    # ● assistant action line
GLY_TOOLOUT = "\u23bf"   # ⎿ tool output continuation

# NEITHER of the two status lines may be matched on its leading glyph. That
# glyph is an ANIMATED SPINNER: sampling three live panes over ~2s yielded
# ✽ ✻ ✶ ✢ · (U+273D, U+273B, U+2736, U+2722, U+00B7) on the *working* line —
# and U+273B ✻ is also what a *finished* line happens to be sitting on. So the
# glyph is shared between the two states and carries no information. The first
# version of this file keyed on it and reported "0 working" while three panes
# were provably mid-turn. Match the TEXT SHAPE instead; `(?:\S\s+)?` just eats
# whatever frame the spinner is on.
#
#: `✢ Actualizing… (3m 7s · ↓ 13.0k tokens)` — ellipsis + parenthesised meta.
RE_WORKING = re.compile(r"^\s*(?:\S\s+)?(?P<verb>[^\s(]+?)…\s*\((?P<meta>[^)]*)\)\s*$")
#: `✻ Cogitated for 22m 16s` — past tense, no ellipsis, no parens. The verb is
#: randomised flavour text ("Worked"/"Churned"/"Sautéed") and is never matched.
RE_DONE = re.compile(r"^\s*(?:\S\s+)?(?P<verb>[^\s(]+?)\s+for\s+(?P<dur>\d[\dhms\s]*?)\s*$")
#: Status lines are short. A length guard keeps a prose sentence that happens
#: to end "...waited for 3 hours" from being read as a finished turn.
MAX_STATUS_LEN = 60
#: Re-reads for a pane that came back UNKNOWN. Cheap because only ambiguous
#: panes pay it; a definitive reading stops at the first sample.
RESAMPLE_TRIES = 3
RESAMPLE_DELAY = 0.18
#: How long a definitive reading stays believable once the pane stops showing
#: one. Turn transitions leave a real gap with no status line, and resampling
#: only narrows it — measured 2026-08-16, an active pane still read UNKNOWN on
#: 3 sweeps in 10. Flashing "CAN'T TELL" at a session that was working 400 ms
#: ago is noise, and noise is how a dashboard stops being read. Shown with a
#: `~` so a held value is never mistaken for a fresh one.
#: This is a FLOOR, not the live value: the window has to outlast the refresh
#: cadence or it can never fire (at a 30s sweep a 20s memory is always already
#: expired by the time the next sample lands), so --watch widens it to a few
#: intervals via `_sticky_window`.
STICKY_SECONDS = 20.0
#: Live sticky window. Set once by --watch; the one-shot path uses the floor.
_sticky_window = STICKY_SECONDS
#: pane_id -> (state, detail, monotonic timestamp) of the last definitive read.
_LAST: dict[int, tuple[str, str, float]] = {}
#: `※ recap: ... Next: ...`
RE_RECAP = re.compile(rf"^\s*{GLY_RECAP}\s*recap:\s*(?P<body>.*)")
#: A numbered choice list — `❯ 1. Yes` or `  2. No, and tell Claude...`
RE_OPTION = re.compile(rf"^\s*(?:{GLY_PROMPT}\s*)?(?P<n>\d)\.\s+(?P<text>\S.*)")
#: The horizontal rules that frame the input box.
RE_RULE = re.compile(r"^\s*[\u2500\u2501\u2550]{20,}")

#: A pane the user has SCROLLED UP in renders no status line at all \u2014 Claude
#: draws it at the bottom of the transcript area, which is off-screen once you
#: scroll (the tell is its own "N new messages (ctrl+End)" marker). Reporting
#: that as IDLE is a lie, and a dashboard that lies about one row costs more
#: than one that admits it: you stop trusting every row. Hence a state whose
#: whole meaning is "go look at this one yourself".
RE_SCROLLED = re.compile(r"new messages?\s*\(ctrl\+End\)|\(ctrl\+End\)")

STATE_BLOCKED = "blocked"
STATE_DONE = "done"
STATE_UNKNOWN = "unknown"
STATE_WORKING = "working"
STATE_IDLE = "idle"

#: Sort + display order. Blocked first: it is the only state where nothing
#: at all happens until a human acts.
STATE_ORDER = {STATE_BLOCKED: 0, STATE_DONE: 1, STATE_UNKNOWN: 2,
               STATE_WORKING: 3, STATE_IDLE: 4}
STATE_LABEL = {
    STATE_BLOCKED: "\u25c9 BLOCKED \u2014 waiting on your answer",
    STATE_DONE: "\u25c9 NEEDS YOU \u2014 turn finished, prompt is yours",
    # Header states only what is certain \u2014 that nothing is readable. WHY it
    # is unreadable (scrolled vs mid-turn) differs per row and is said there.
    STATE_UNKNOWN: "? CAN'T TELL \u2014 no status line visible",
    STATE_WORKING: "\u25d3 AI WORKING \u2014 leave it alone",
    STATE_IDLE: "\u00b7 IDLE \u2014 no turn yet",
}
#: Everything above WORKING needs a human. One place, so the summary line and
#: the exit code can never disagree with the grouping.
NEEDS_HUMAN = (STATE_BLOCKED, STATE_DONE)

ANSI = {
    "red": "\033[38;5;203m", "amber": "\033[38;5;179m", "green": "\033[38;5;114m",
    "dim": "\033[38;5;244m", "blue": "\033[38;5;110m", "bold": "\033[1m",
    "off": "\033[0m",
}
STATE_COLOR = {
    STATE_BLOCKED: "red", STATE_DONE: "amber", STATE_UNKNOWN: "dim",
    STATE_WORKING: "blue", STATE_IDLE: "dim",
}


def _wezterm() -> str:
    """Absolute path to wezterm.exe, or bare name if it is on PATH."""
    found = shutil.which("wezterm")
    if found:
        return found
    for guess in (r"C:\Program Files\WezTerm\wezterm.exe",
                  "/usr/bin/wezterm", "/usr/local/bin/wezterm"):
        if os.path.exists(guess):
            return guess
    return "wezterm"


def _run(args: list[str], timeout: float = 5.0) -> str:
    try:
        out = subprocess.run([_wezterm()] + args, capture_output=True,
                             timeout=timeout, check=False)
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def list_panes() -> list[dict]:
    raw = _run(["cli", "list", "--format", "json"])
    try:
        return json.loads(raw) if raw.strip() else []
    except Exception:
        return []


def pane_text(pane_id: int) -> str:
    return _run(["cli", "get-text", "--pane-id", str(pane_id)])


def strip_spinner(title: str) -> str:
    """Drop Claude's leading spinner glyph. Explicit set, not 'leading
    non-ASCII' — the latter would delete a Chinese session topic entirely."""
    for _ in range(3):
        for g in SPINNER_FRAMES:
            if title.startswith(g):
                title = title[len(g):]
        title = title.lstrip()
    return title


def _input_box(lines: list[str]) -> tuple[int, str]:
    """Locate the ❯ input caret and return (index, typed draft).

    The caret sits between two long horizontal rules at the bottom of the
    screen. Searching from the BOTTOM matters: `❯` is also used to mark the
    selected item in an option list, so a top-down scan finds the wrong one.
    """
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith(GLY_PROMPT):
            return i, s[len(GLY_PROMPT):].strip()
    return -1, ""


def classify(text: str) -> dict:
    """Reduce a pane's rendered screen to a state plus the evidence for it."""
    lines = text.split("\n")
    caret_i, draft = _input_box(lines)
    head = lines[:caret_i] if caret_i > 0 else lines

    # Default UNKNOWN, never IDLE. Seeing a status line is definitive evidence
    # of a state; NOT seeing one is not evidence of idleness — the pane may be
    # scrolled, or caught mid-redraw. Measured 2026-08-16 over 15 samples of 8
    # live panes: a working pane read as having no status line 11 times out of
    # 15. Defaulting that to IDLE told the user "nothing is happening" about a
    # session that was mid-turn, which is the exact failure this tool exists to
    # prevent. IDLE is now only concluded from positive evidence (below).
    state, detail, recap = STATE_UNKNOWN, "", ""

    # Scan upward from the input box for the status line. Only the nearest one
    # describes the current turn; older ones are scrollback from earlier turns.
    for line in reversed(head[-25:]):
        s = line.strip()
        if not s or len(s) > MAX_STATUS_LEN:
            continue
        m = RE_WORKING.match(line)
        if m:
            state = STATE_WORKING
            detail = f"{m.group('verb').strip()} · {m.group('meta').strip()}"
            break
        m = RE_DONE.match(line)
        if m:
            state = STATE_DONE
            detail = f"finished after {m.group('dur').strip()}"
            break

    # A permission gate outranks everything: nothing proceeds until answered.
    # Require >=2 numbered options immediately above the caret so an ordinary
    # numbered list in prose can't masquerade as a prompt.
    tail = head[-12:]
    opts = [m.group(0).strip() for m in (RE_OPTION.match(l) for l in tail) if m]
    if len(opts) >= 2:
        state = STATE_BLOCKED
        detail = " / ".join(o.lstrip(GLY_PROMPT).strip() for o in opts[:3])

    if state == STATE_UNKNOWN:
        if any(RE_SCROLLED.search(l) for l in lines[-30:]):
            detail = "scrolled up — press ctrl+End in that pane"
        elif not any(l.lstrip().startswith(GLY_BULLET) for l in head):
            # No assistant action line anywhere on screen: a session that has
            # genuinely not run a turn yet. This is the ONLY positive evidence
            # of idleness we have.
            state, detail = STATE_IDLE, "no turn yet"
        else:
            detail = "no status line on screen"

    for line in reversed(head[-25:]):
        m = RE_RECAP.match(line)
        if m:
            recap = m.group("body").strip()
            break

    return {"state": state, "detail": detail, "recap": recap, "draft": draft,
            "tail": _meaningful_tail(head)}


def _meaningful_tail(head: list[str]) -> list[str]:
    """The last few lines of actual conversation.

    Drops the status/recap furniture and tool-output noise — those are
    reported separately, and repeating them here would push the sentence the
    agent is actually waiting on off the top.
    """
    out: list[str] = []
    for line in reversed(head):
        s = line.rstrip()
        if not s.strip():
            continue
        # Skip the status line by SHAPE, not by glyph — the same animated
        # spinner that broke classification would slip past a glyph filter
        # here too, and did: every working row's "context" was just its own
        # status line echoed back.
        if RE_RULE.match(s) or RE_WORKING.match(s) or RE_DONE.match(s):
            continue
        if s.lstrip().startswith((GLY_RECAP, GLY_TOOLOUT)):
            continue
        if "(disable recaps in" in s:
            continue
        out.append(s.strip())
        if len(out) >= 3:
            break
    return list(reversed(out))


def collect() -> list[dict]:
    """Every agent session in the mux, ordered by how much it needs you."""
    rows = []
    for p in list_panes():
        title = strip_spinner(p.get("title") or "")
        tab_title = p.get("tab_title") or ""
        # An agent pane is one whose title Claude/Codex has taken over, or
        # one in a tab the launcher named. A plain shell has neither.
        looks_agent = (
            title.startswith(("Claude Code", "Codex"))
            or tab_title.startswith(("CC:", "Codex"))
            or title == "claude"
        )
        if not looks_agent and not (tab_title.startswith("CC") or title in ("claude", "codex")):
            # Fall through to a text probe only for panes we can't rule out
            # cheaply — a title like "Fix YouTube credential loading errors"
            # is a real session topic and matches none of the prefixes above.
            if not tab_title.startswith("CC") and title in ("bash.exe", "cmd.exe", ""):
                continue
        txt = pane_text(int(p["pane_id"]))
        if GLY_PROMPT not in txt and GLY_BULLET not in txt:
            continue  # no agent furniture on screen -> not an agent pane
        info = classify(txt)
        # A single sample can land between redraws. Only UNKNOWN is re-read —
        # a definitive reading is trusted immediately, so the common case
        # still costs exactly one `get-text` per pane.
        for _ in range(RESAMPLE_TRIES):
            if info["state"] != STATE_UNKNOWN:
                break
            time.sleep(RESAMPLE_DELAY)
            txt = pane_text(int(p["pane_id"]))
            info = classify(txt)

        pid = int(p["pane_id"])
        info["stale"] = False
        if info["state"] == STATE_UNKNOWN:
            prev = _LAST.get(pid)
            if prev and (time.monotonic() - prev[2]) < _sticky_window:
                info["state"], info["detail"], info["stale"] = prev[0], prev[1], True
        elif info["state"] != STATE_IDLE:
            _LAST[pid] = (info["state"], info["detail"], time.monotonic())
        cwd = (p.get("cwd") or "").replace("file:///", "").rstrip("/")
        info.update({
            "pane_id": p["pane_id"], "tab_id": p["tab_id"],
            "window_id": p["window_id"],
            "topic": title if title not in ("Claude Code", "claude", "Codex") else "(no topic yet)",
            "tab_title": tab_title,
            "cwd": "/".join(cwd.split("/")[-2:]) if cwd else "",
        })
        rows.append(info)
    rows.sort(key=lambda r: (STATE_ORDER.get(r["state"], 9), r["window_id"], r["tab_id"]))
    return rows


def _c(name: str, s: str, color: bool) -> str:
    return f"{ANSI[name]}{s}{ANSI['off']}" if color else s


def render(rows: list[dict], width: int = 100, color: bool = True,
           lines_needs: int = 3, lines_busy: int = 1) -> str:
    if not rows:
        return _c("dim", "No agent sessions found.", color)
    out: list[str] = []
    need = sum(1 for r in rows if r["state"] in NEEDS_HUMAN)
    work = sum(1 for r in rows if r["state"] == STATE_WORKING)
    unk = sum(1 for r in rows if r["state"] == STATE_UNKNOWN)
    summary = f"  {need} need you   {work} working"
    if unk:
        summary += f"   {unk} unreadable"
    out.append(_c("bold", summary + f"   {len(rows)} sessions", color))
    out.append("")
    current = None
    for r in rows:
        if r["state"] != current:
            current = r["state"]
            label = STATE_LABEL[current]
            out.append(_c(STATE_COLOR[current], label + " " +
                          "\u2500" * max(0, width - len(label) - 2), color))
        head = f"  {r['window_id']}:{r['tab_id']:<3} {r['topic'][:44]:<44}"
        detail = r["detail"]
        if r.get("stale"):
            detail = "~ " + detail   # held from the last definitive read
        out.append(_c(STATE_COLOR[r["state"]], head, color) + " " +
                   _c("dim", detail[:width - len(head) - 3], color))
        if r["draft"]:
            out.append("      " + _c("amber", f"\u276f {r['draft'][:width - 10]}", color) +
                       _c("dim", "   \u2190 unsubmitted", color))
        # A session that wants something gets more of its own words; one that
        # is busy gets a single line of "what it is doing" so the panel stays
        # scannable instead of becoming a wall you have to read.
        n = lines_needs if r["state"] in NEEDS_HUMAN or r["state"] == STATE_BLOCKED else lines_busy
        for line in r["tail"][-n:] if n else []:
            out.append("      " + _c("dim", line[:width - 8], color))
        out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--watch", action="store_true", help="refresh until Ctrl+C")
    # 30s, not a few seconds: every sweep shells out `wezterm cli get-text`
    # once per agent pane, and the thing being watched — "is this one waiting
    # on me" — changes on the scale of a turn, not a frame. A faster loop buys
    # no answer sooner and repaints the whole screen under your eyes.
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--lines", type=int, default=3,
                    help="context lines for sessions needing you (default 3)")
    ap.add_argument("--busy-lines", type=int, default=1,
                    help="context lines for busy sessions (default 1)")
    args = ap.parse_args(argv[1:])

    if args.json:
        print(json.dumps(collect(), ensure_ascii=False, indent=2))
        return 0

    color = not args.no_color
    if not args.watch:
        rows = collect()
        print(render(rows, shutil.get_terminal_size((100, 40)).columns, color,
                     args.lines, args.busy_lines))
        # Exit code = how many need you, so a shell prompt or a hook can react
        # without parsing the output.
        return min(sum(1 for r in rows if r["state"] in NEEDS_HUMAN), 125)

    # A held reading has to survive the gap between sweeps, or the stickiness
    # is dead code at any interval above its floor.
    global _sticky_window
    _sticky_window = max(STICKY_SECONDS, args.interval * 2.5)

    # Name the pane so the WezTerm binding can find and re-focus this
    # dashboard instead of spawning a second one on every keypress.
    sys.stdout.write("\033]2;EOS sessions\007")
    try:
        while True:
            rows = collect()
            w = shutil.get_terminal_size((100, 40)).columns
            body = render(rows, w, color, args.lines, args.busy_lines)
            sys.stdout.write("\033[H\033[J" + body + "\n" +
                             _c("dim", f"  refreshed {time.strftime('%H:%M:%S')}"
                                       f" \u00b7 every {args.interval:g}s"
                                       f" \u00b7 Ctrl+C to quit", color) + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # never leave a dashboard pane with a traceback
        print(f"agent_sessions: {exc}", file=sys.stderr)
        sys.exit(1)
