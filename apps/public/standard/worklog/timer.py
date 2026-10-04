"""worklog — start/stop timer, writing the existing `Logged time:` convention.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). A single active timer, server-timestamped (mirrors
apps/public/standard/focus/app.py's start_session/complete_session shape —
load_state/save_state, never a hand-rolled data_dir JSON file). On stop,
appends a `> Logged time: HH:MM-HH:MM` line under `## Work` via the SAME
convention parser.py::parse_logged_time already reads (138/213 existing
notes carry this by hand) — zero parser changes needed.

Considered and rejected: piggybacking on focus's `active_session` state
instead of a second timer. focus is a fixed-duration pomodoro sprint (breaks,
distraction tracking); this is a plain clock-in/clock-out tied to a project —
a different moment in the user's day. A `focus:completed` -> "log this to
worklog" soft integration is a reasonable future addition, not built here.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from .parser import append_section

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


# ─── Bind to WorklogApp class as ────────────────────────────────
#   timer_status     = _timer.timer_status
#   timer_start      = _timer.timer_start
#   timer_stop       = _timer.timer_stop
#   timer_cancel     = _timer.timer_cancel
#   api_timer        = _timer.api_timer
#   api_timer_start  = _timer.api_timer_start
#   api_timer_stop   = _timer.api_timer_stop
#   api_timer_cancel = _timer.api_timer_cancel
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


async def timer_status(self) -> dict:
    state = self.load_state({})
    active = state.get("timer") or {}
    if not active.get("running"):
        return {"running": False}
    return {
        "running": True,
        "project": active.get("project", ""),
        "employer": active.get("employer", ""),
        "started_at": active.get("started_at", ""),
    }


async def timer_start(self, project: str = "", employer: str = "") -> dict:
    state = self.load_state({})
    active = state.get("timer") or {}
    if active.get("running"):
        return {"error": f"timer already running for {active.get('project') or 'General'}"}
    now = _now()
    state["timer"] = {
        "running": True,
        "project": (project or "General").strip(),
        "employer": (employer or "").strip(),
        "started_at": now.isoformat(),
        "started_date": now.date().isoformat(),
    }
    self.save_state(state)
    return {"ok": True, **state["timer"]}


async def timer_stop(self, note: str = "") -> dict:
    state = self.load_state({})
    active = state.get("timer") or {}
    if not active.get("running"):
        return {"error": "no timer running"}
    try:
        started = datetime.fromisoformat(active["started_at"])
    except (KeyError, ValueError):
        state["timer"] = {"running": False}
        self.save_state(state)
        return {"error": "timer state was corrupt — cleared, please start again"}
    now = _now()
    # A day note's `Logged time:` convention is a plain local HH:MM-HH:MM
    # range with no date component. parser.py's own _TIME_RANGE guard
    # silently DROPS a range that doesn't move forward — so a session
    # spanning midnight (or a same-day zero/negative duration from clock
    # skew or a double-click) would vanish from day_hours() with no error
    # ever surfaced. Refuse those explicitly instead; leave state running
    # so the start time isn't lost.
    if now.date() != started.date():
        return {"error": "timer spans past midnight — stop and log manually, "
                          "or split into two sessions"}
    started_total = started.hour * 60 + started.minute
    now_total = now.hour * 60 + now.minute
    if now_total <= started_total:
        return {"error": "timer duration is zero — try again in a minute"}
    start_hhmm = started.strftime("%H:%M")
    end_hhmm = now.strftime("%H:%M")
    started_date = started.date()
    employer = active.get("employer") or ""
    project = active.get("project") or "General"
    try:
        async with self._daily_lock(started_date):
            content = await self._ensure_daily(started_date, employer)
            # parse_logged_time captures everything after the time range as
            # the note and only strips a LEADING COMMA (parser.py:150) — the
            # UI re-adds its own " — " separator when it displays a parsed
            # note (pages/index.html renderDetail). Writing a "— " prefix
            # here would round-trip into a doubled "— — note" on display;
            # a comma matches what the parser is actually built to strip.
            suffix = f", {note.strip()}" if note.strip() else ""
            new_content = append_section(
                content, "Work", f"> Logged time: {start_hhmm}-{end_hhmm}{suffix}")
            await self.write(str(self._daily_path(started_date)), new_content)
    except ValueError as e:
        return {"error": str(e)}
    state["timer"] = {"running": False}
    self.save_state(state)
    # Every other write path in this app (log_work, set_status, _ensure_daily's
    # create) emits worklog:logged/-created/-status-changed; this one writes to
    # the same day note and should carry the same signal, not go silent.
    await self.emit("worklog:logged",
                    {"date": started_date.isoformat(), "project": project,
                     "text": f"Logged time: {start_hhmm}-{end_hhmm}",
                     "status": "note", "employer": employer})
    return {"ok": True, "date": started_date.isoformat(), "project": project,
            "start": start_hhmm, "end": end_hhmm}


async def timer_cancel(self) -> dict:
    """Discard the running timer without logging anything — the recovery
    path for a mis-started timer (wrong project, started by accident).
    State-only; never touches the vault."""
    state = self.load_state({})
    if not (state.get("timer") or {}).get("running"):
        return {"error": "no timer running"}
    state["timer"] = {"running": False}
    self.save_state(state)
    return {"ok": True}


@web_route("GET", "/api/timer")
async def api_timer(self, request):
    return await self.timer_status()


@web_route("POST", "/api/timer/start")
async def api_timer_start(self, request):
    data = await self.read_json(request)
    return await self.timer_start(data.get("project", ""), data.get("employer", ""))


@web_route("POST", "/api/timer/stop")
async def api_timer_stop(self, request):
    data = await self.read_json(request)
    return await self.timer_stop(data.get("note", ""))


@web_route("POST", "/api/timer/cancel")
async def api_timer_cancel(self, request):
    return await self.timer_cancel()
