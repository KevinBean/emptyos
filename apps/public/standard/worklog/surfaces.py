"""worklog — hub panels, voice verbs, CLI.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns every non-HTTP surface: the two hub panels, the four voice
verbs and their fuzzy item resolver, and the ``eos worklog`` CLI command.
Thin wrappers — the work belongs to the spine's writers and to reads.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.log_work / self.set_status / self._set_prose
(spine), self._load_day / self._all_days / self._summarize (reads).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, timedelta
from emptyos.sdk import cli_command
from .parser import STATUS_EMOJI
from .shared import _parse_date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


# ─── Bind to WorklogApp class as ────────────────────────────────
#   panel_active      = _surfaces.panel_active
#   panel_blocked     = _surfaces.panel_blocked
#   voice_log_work    = _surfaces.voice_log_work
#   _resolve_item     = _surfaces._resolve_item
#   voice_set_status  = _surfaces.voice_set_status
#   voice_set_plan    = _surfaces.voice_set_plan
#   voice_set_update  = _surfaces.voice_set_update
#   cmd_worklog       = _surfaces.cmd_worklog
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# -- hub panel --
async def panel_active(self) -> list[dict] | None:
    day = await self._load_day(self._daily_path(date.today()))
    if not day:
        return None
    rows = []
    for g in day["parsed"]["projects"]:
        for it in g["items"]:
            if it["status"] in ("in-progress", "blocked"):
                emoji = STATUS_EMOJI.get(it["status"], "")
                rows.append({"title": f"{emoji} {it['text']}", "subtitle": g["project"],
                             "href": f"/worklog/#{day['date']}"})
    return rows or None


async def panel_blocked(self) -> list[dict] | None:
    """Blocked + in-review items from the last 7 days — the 'waiting on
    someone' glanceable. None (invisible) when nothing is stuck."""
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    rows: list[dict] = []
    for day in await self._all_days():
        if day["date"] < cutoff:
            break
        for g in day["parsed"]["projects"]:
            for it in g["items"]:
                if it["status"] in ("blocked", "review"):
                    emoji = STATUS_EMOJI.get(it["status"], "")
                    rows.append({
                        "title": f"{emoji} {g['project']} — {it['text']}",
                        "subtitle": day["date"],
                        "href": f"/worklog/#{day['date']}",
                    })
    return rows or None


# ── voice ─────────────────────────────────────────────────────────
def _today_link() -> dict:
    """The "go look at what I just changed" affordance every write verb returns.

    A hash link to the record, not ``BaseApp.app_link`` — that builds a query
    string for the calculator shape (.claude/rules/deep-link-to-app.md); the
    record shape has no SDK helper until a second app wants one.
    """
    return {"text": "Open work log", "href": f"/worklog/#{date.today().isoformat()}"}


async def voice_log_work(self, project: str = "", text: str = "", status: str = "") -> dict:
    try:
        await self.log_work("", project, text, status)
    except ValueError as e:
        return {"say": f"Couldn't log that: {e}"}
    return {"say": f"Logged under {project or 'General'}.", "link": _today_link()}


async def _resolve_item(self, date_s: str, project: str, query: str) -> tuple[str, str] | None:
    """Substring-match a work item within a day → (project, exact_text) or None."""
    q = (query or "").strip().lower()
    if not q:
        return None
    proj = (project or "").strip().lower()
    day = await self._load_day(self._daily_path(_parse_date(date_s)))
    if not day:
        return None
    for g in day["parsed"]["projects"]:
        if proj and g["project"].lower() != proj:
            continue
        for it in g["items"]:
            if q in it["text"].lower():
                return g["project"], it["text"]
    return None


async def voice_set_status(self, project: str = "", item_text: str = "", status: str = "") -> dict:
    hit = await self._resolve_item("", project, item_text)
    if not hit:
        return {"say": f"Couldn't find a work item matching “{item_text}” today."}
    proj, exact = hit
    res = await self.set_status("", proj, exact, status)
    if res.get("error"):
        return {"say": f"Couldn't update that: {res['error']}"}
    return {"say": f"Marked “{exact[:48]}” as {status or 'done'}.",
            "link": _today_link()}


async def voice_set_plan(self, text: str = "") -> dict:
    if not (text or "").strip():
        return {"say": "No plan text provided."}
    await self._set_prose("", "Plan", text)
    return {"say": "Plan updated.", "link": _today_link()}


async def voice_set_update(self, text: str = "") -> dict:
    if not (text or "").strip():
        return {"say": "No update text provided."}
    await self._set_prose("", "Update", text)
    return {"say": "Update recorded.", "link": _today_link()}


# ── cli ───────────────────────────────────────────────────────────
@cli_command("worklog", help="Work log operations: today | log | recent")
async def cmd_worklog(self, action: str = "today", project: str = "", text: str = "",
                      status: str = ""):
    if action == "log" and text:
        res = await self.log_work("", project, text, status)
        self.print_rich(f"[green]Logged[/green] {res['project']}: {text}")
        return
    if action == "recent":
        for day in (await self._all_days())[:14]:
            s = self._summarize(day)
            self.print_rich(f"  {s['date']} {s['weekday'][:3]} — {s['item_count']} items "
                            f"· {', '.join(s['projects'][:3])}")
        return
    # today
    day = await self._load_day(self._daily_path(date.today()))
    if not day:
        self.print_rich("[dim]No worklog today.[/dim]")
        return
    for g in day["parsed"]["projects"]:
        self.print_rich(f"[bold]{g['project']}[/bold]")
        for it in g["items"]:
            self.print_rich(f"  {STATUS_EMOJI.get(it['status'] or '', '·')} {it['text']}")
