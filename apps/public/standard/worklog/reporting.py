"""worklog — windows, AI rollup/draft, and the timesheet PDF.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns the shared reporting window (``?days=`` trailing or
``?from=&to=`` range) and everything built on it: the AI rollup, the
end-of-day Update draft, smart-parse, carry-over, the year list, and the
timesheet PDF. Holds all three think() prompts and the caps that keep an
uncapped range safe.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._all_days / self._project_names (reads),
self._default_employer / self._daily_path (spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, timedelta
from emptyos.sdk import web_route
from emptyos.sdk.utils import parse_llm_json
from .parser import STATUS_EMOJI
from .shared import _parse_date, _unfence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


WEEKLY_ROLLUP_SYSTEM = (
    "You write a factual weekly work summary from a list of dated work items "
    "(format: date | project | status | text). Group by project, past tense, "
    "most substantial project first. End with blocked or in-review items on "
    "their own final line, if any. Keep it under 200 words.\n\n"
    "Do NOT:\n"
    "- Invent work that is not in the list.\n"
    "- Add praise, filler, or self-assessment.\n"
    "- Speculate about outcomes or next steps.\n"
    "- Use headings or bullet lists; short paragraphs only."
)

UPDATE_DRAFT_SYSTEM = (
    "You draft the end-of-day Update for a professional work log, from that "
    "day's work items (format: project | status | text) and, when present, the "
    "morning's Plan.\n"
    "Write 2-4 sentences in the FIRST PERSON, past tense, plain and factual — "
    "the voice of an engineer noting what actually happened. Lead with what "
    "moved. Group related work rather than restating every item. If the day "
    "diverged from the Plan, say so plainly in one clause. If something is "
    "blocked or waiting on someone, put it last and name what it waits on.\n\n"
    "Do NOT:\n"
    "- Invent work, outcomes, or detail that is not in the items.\n"
    "- Add praise, self-assessment, or motivational filler.\n"
    "- Speculate about tomorrow or propose next steps.\n"
    "- Use headings, bullet lists, or markdown emphasis — plain sentences only.\n"
    "- Open with a date, a greeting, or 'Today I'."
)

SMART_LOG_SYSTEM = (
    "You convert ONE natural-language work-log sentence into strict JSON: "
    '{"project": str, "text": str, "status": str, "date": str}.\n'
    "project: choose ONLY from the provided known-projects list; if none "
    'fits, use "General".\n'
    "status: one of complete, in-progress, todo, next, waiting, review, "
    "blocked. Default in-progress.\n"
    'date: YYYY-MM-DD only when the sentence names a day explicitly, else "".\n\n'
    "Do NOT:\n"
    "- Output anything except the JSON object.\n"
    "- Invent a project that is not in the list.\n"
    "- Rewrite the work text beyond removing the project/status/date words."
)

# Statuses that carry over to the next workday (anything not finished).
CARRYOVER_STATUSES = ("in-progress", "blocked", "waiting", "todo", "next")

# Ceiling on how many work items one AI rollup may summarise. The trailing
# ?days= window is capped at 92, but an explicit ?from=&to= range is not, so
# without this an all-time rollup would push the whole corpus into one prompt.
ROLLUP_MAX_ITEMS = 400

# Character budget for the timesheet PDF's Projects cell. It is the widest
# column; left uncapped it starves the Day column until the table hyphen-breaks
# the ISO date across two lines.
_PROJECT_CELL_CHARS = 58


# ─── Bind to WorklogApp class as ────────────────────────────────
#   _window            = _reporting._window
#   api_years          = _reporting.api_years
#   api_rollup         = _reporting.api_rollup
#   api_smart_parse    = _reporting.api_smart_parse
#   draft_update       = _reporting.draft_update
#   api_update_draft   = _reporting.api_update_draft
#   api_carryover      = _reporting.api_carryover
#   api_timesheet_pdf  = _reporting.api_timesheet_pdf
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _window(self, request) -> dict:
    """Resolve the shared reporting window → {start, end, employer, label}.

    Two forms. ``?days=N`` is the trailing window (default 7, capped at 92)
    the rollup has always used. ``?from=&to=`` is an explicit ISO range with
    no cap — an employment record is asked for as "FY2024", which a trailing
    window cannot express, and capping it at a quarter made 927 of this
    vault's 934 recorded hours unreachable from the PDF.

    Both bounds are inclusive. Either side of a range may be omitted (open
    start / open end), and a reversed range is swapped rather than returning
    an empty report — the user's intent is unambiguous.
    """
    params = request.query_params
    employer = params.get("employer", "")
    today = date.today()
    frm, to = (params.get("from") or "").strip(), (params.get("to") or "").strip()
    if frm or to:
        try:
            start = date.fromisoformat(frm) if frm else date(1970, 1, 1)
            end = date.fromisoformat(to) if to else today
        except ValueError:
            return {"error": "bad date — use YYYY-MM-DD"}
        if end < start:
            start, end = end, start
        return {"start": start.isoformat(), "end": end.isoformat(),
                "employer": employer, "days": (end - start).days + 1,
                "label": f"{start.isoformat()} → {end.isoformat()}"}
    try:
        days_n = int(params.get("days", "7"))
    except ValueError:
        days_n = 7
    days_n = max(1, min(days_n, 92))
    start = today - timedelta(days=days_n)
    return {"start": start.isoformat(), "end": today.isoformat(),
            "employer": employer, "days": days_n,
            "label": f"last {days_n} days"}


@web_route("GET", "/api/years")
async def api_years(self, request):
    """Years that actually hold worklog data, for the range picker.

    Offering a bare year list would include gaps — this vault has 2023,
    2024 and 2026 but no 2025, and picking it would render an empty PDF.
    """
    employer = request.query_params.get("employer", "")
    years: dict[str, dict] = {}
    for day in await self._all_days(employer):
        y = day["date"][:4]
        row = years.setdefault(y, {"year": y, "days": 0, "hours": 0.0})
        row["days"] += 1
        row["hours"] += float(day["parsed"].get("hours") or 0)
    out = [{**r, "hours": round(r["hours"], 1)}
           for r in sorted(years.values(), key=lambda r: r["year"], reverse=True)]
    return {"years": out}


@web_route("GET", "/api/rollup")
async def api_rollup(self, request):
    """AI weekly rollup over the window — the timesheet/review artifact.

    Feeds ``date | project | status | text`` lines to the model (own vault
    content to the configured think chain — no aggregation needed here),
    persists each rollup via ``save_calculation`` for the audit trail.
    """
    win = self._window(request)
    if win.get("error"):
        return win
    employer, start, end = win["employer"], win["start"], win["end"]
    lines: list[str] = []
    for day in await self._all_days(employer):
        if day["date"] < start:
            break
        if day["date"] > end:
            continue
        for g in day["parsed"]["projects"]:
            for it in g["items"]:
                st = it["status"] or "note"
                lines.append(f"{day['date']} | {g['project']} | {st} | {it['text']}")
    if not lines:
        return {"error": f"no work items in {win['label']}"}
    lines.reverse()  # oldest first reads chronologically
    # A ?from=&to= range is uncapped by design, so an all-time rollup would
    # otherwise push every item in the vault (1,500+ here) into one prompt.
    # Keep the most RECENT slice — a summary of the tail is the useful half
    # — and report the truncation rather than silently summarising a subset
    # (.claude/rules/audits.md: no silent caps).
    truncated = max(0, len(lines) - ROLLUP_MAX_ITEMS)
    if truncated:
        lines = lines[-ROLLUP_MAX_ITEMS:]
    rollup = await self.think(
        "\n".join(lines), system=WEEKLY_ROLLUP_SYSTEM, domain="text", temperature=0.3
    )
    saved = None
    try:
        saved = self.save_calculation(
            label=f"Work rollup — {win['label']} ending {date.today().isoformat()}",
            inputs={"window": win["label"], "employer": employer or "(all)",
                    "items": len(lines)},
            result={"rollup": rollup},
            method="worklog.rollup",
        )
    except Exception:
        pass  # the rollup itself still returns; the note is best-effort
    return {
        "rollup": rollup,
        "items": len(lines),
        "truncated": truncated,
        "days": win["days"],
        "window": win["label"],
        "saved": (saved or {}).get("path", ""),
        "provenance": self.last_provenance(),
    }


@web_route("POST", "/api/smart-parse")
async def api_smart_parse(self, request):
    """Parse a natural-language log sentence → {project, text, status, date}.

    Returns the parse ONLY — never writes. The form is the confirm surface;
    the user still presses Log.
    """
    data = await self.read_json(request)
    text = (data.get("text") or "").strip()
    if not text:
        return {"error": "text required"}
    known = [n for n, _ in (await self._project_names(data.get("employer", "")))[:40]]
    raw = await self.think(
        f"Known projects: {', '.join(known) or '(none)'}\nSentence: {text}",
        system=SMART_LOG_SYSTEM,
        domain="text",
        temperature=0.1,
    )
    parsed = parse_llm_json(raw, fallback={}) or {}
    project = str(parsed.get("project") or "General").strip() or "General"
    if project != "General" and known and project not in known:
        project = "General"
    status = str(parsed.get("status") or "").strip().lower()
    if status not in STATUS_EMOJI:
        status = self._default_status()
    out_text = str(parsed.get("text") or "").strip() or text
    date_s = str(parsed.get("date") or "").strip()
    try:
        date.fromisoformat(date_s)
    except ValueError:
        date_s = ""
    return {"project": project, "text": out_text, "status": status, "date": date_s,
            "provenance": self.last_provenance()}


async def draft_update(self, date_s: str = "") -> dict:
    """Draft the end-of-day Update from that day's items. NEVER writes.

    The Update section is first-class (API + UI + voice verb) and was used
    on 1 of 213 days, because nobody types a summary at 6pm — while the
    Plan, written when there IS energy, sits at 80%. The material for the
    Update is already in the day's items, so the model assembles it and the
    user edits and saves: propose, don't autofill
    (.claude/rules/proposed-action.md).
    """
    d = _parse_date(date_s)
    day = await self._load_day(self._daily_path(d))
    if not day:
        return {"error": "no worklog for that day"}
    parsed = day["parsed"]
    lines = [
        f"{g['project']} | {it['status'] or 'note'} | {it['text']}"
        for g in parsed["projects"] for it in g["items"]
    ]
    if not lines:
        return {"error": "no work items to summarise yet"}
    parts = []
    if parsed["plan"].strip():
        # The Plan is context, not content — it lets the model notice a
        # divergence, which is the most useful thing an Update can record.
        parts.append("Morning plan:\n" + parsed["plan"].strip())
    parts.append("Work items:\n" + "\n".join(lines))
    # No min_ability: summarising a bounded list is exactly what weak models
    # are good at (.claude/rules/model-ability.md — don't over-gate).
    draft = await self.think(
        "\n\n".join(parts), system=UPDATE_DRAFT_SYSTEM,
        domain="text", temperature=0.3,
    )
    return {"draft": _unfence(draft), "items": len(lines),
            "date": d.isoformat(), "provenance": self.last_provenance()}


@web_route("POST", "/api/update/draft")
async def api_update_draft(self, request):
    data = await self.read_json(request)
    return await self.draft_update(data.get("date", ""))


@web_route("GET", "/api/carryover")
async def api_carryover(self, request):
    """Open items from the most recent logged day before today — the
    standup opener. The client offers to re-log them onto today."""
    employer = request.query_params.get("employer", "")
    today_s = date.today().isoformat()
    for day in await self._all_days(employer):
        if day["date"] >= today_s:
            continue
        items = [
            {"project": g["project"], "text": it["text"], "status": it["status"]}
            for g in day["parsed"]["projects"]
            for it in g["items"]
            if it["status"] in CARRYOVER_STATUSES
        ]
        return {"from": day["date"], "weekday": day["weekday"], "items": items}
    return {"from": "", "items": []}


@web_route("GET", "/api/timesheet.pdf")
async def api_timesheet_pdf(self, request):
    """Styled timesheet PDF over the shared window (``?days=`` or
    ``?from=&to=``, plus ``?employer=``) — recorded hours and per-day item
    counts, rendered via the shared PDF profile.

    Hours come from either convention: an itemised ``## Timesheet`` (per
    project) or the hand-written ``Logged time:`` range (per day). The
    second is what the corpus actually contains — 136 days against 5 — so
    without it this table printed "no rows recorded" on every export.
    ``day_hours`` picks one per day, so a note carrying both can't
    double-count.
    """
    win = self._window(request)
    if win.get("error"):
        return win
    employer, start, end = win["employer"], win["start"], win["end"]
    today_s = date.today().isoformat()
    rows: list[str] = []
    day_lines: list[str] = []
    total_hours = 0.0
    in_window = [d for d in await self._all_days(employer)
                 if start <= d["date"] <= end]
    for day in reversed(in_window):
        parsed = day["parsed"]
        n_items = sum(len(g["items"]) for g in parsed["projects"])
        named = [g["project"] for g in parsed["projects"] if g["items"]]
        # Cap the widest column: an uncapped list starved the Day column
        # until the table hyphen-broke the ISO date across two lines
        # ("2026-05-|04"). Fixed here rather than with a nowrap rule in the
        # shared PDF profile, which would make any table with a prose first
        # column overflow the page instead of wrapping.
        # Capped by CHARACTERS, not just count — real project labels in this
        # vault are whole sentences ("AI insights - organize the data of the
        # project names and calculation names"), so three of them still blew
        # the column even after a count cap.
        projects = ", ".join(named[:3])
        if len(projects) > _PROJECT_CELL_CHARS:
            projects = projects[:_PROJECT_CELL_CHARS - 1].rstrip(" ,") + "…"
        if len(named) > 3:
            projects += f" +{len(named) - 3}"
        hours = float(parsed.get("hours") or 0)
        total_hours += hours
        hours_cell = f"{hours:g}" if hours else ""
        # Weekday only on Sat/Sun. Spelling it out every row pushed the Day
        # column past its width and wrapped mid-date ("2026-05-\n04 Mon");
        # on a timesheet the weekday only carries signal when it's weekend
        # work, which is exactly what a reader scans for.
        weekend = day["weekday"][:3] if day["weekday"] in ("Saturday", "Sunday") else ""
        day_label = f"{day['date']} {weekend}".strip()
        day_lines.append(
            f"| {day_label} | {n_items} | {hours_cell} | {projects} |"
        )
        if parsed.get("timesheet"):
            for t in parsed["timesheet"]:
                try:
                    t_hours = float(t.get("hours") or 0)
                except (TypeError, ValueError):
                    t_hours = 0.0
                rows.append(f"| {day['date']} | {t.get('project', '')} | "
                            f"{t_hours:g} | {t.get('note') or ''} |")
        else:
            for lg in parsed.get("logged_time") or []:
                span = f"{lg['start']}–{lg['end']}" if lg.get("start") else ""
                note = " ".join(x for x in (span, lg.get("note") or "") if x)
                rows.append(f"| {day['date']} | — | {lg['hours']:g} | {note} |")
    # Label from the employers actually IN the window, not the configured
    # default. An unfiltered FY2024 export was headed with the *current*
    # employer while every day in it belonged to a previous one — on a
    # document that may be handed to an employer, that is worse than vague.
    if employer:
        emp_label = employer
    else:
        seen = sorted({d["employer"] for d in in_window if d["employer"]})
        # Naming them is the point for a one- or two-employer range; past
        # that the masthead is a list nobody reads and it wraps.
        if not seen:
            emp_label = "All employers"
        elif len(seen) <= 3:
            emp_label = ", ".join(seen)
        else:
            emp_label = f"{len(seen)} employers"
    md = (
        "```\n"
        f"TIMESHEET\n{emp_label} · {start} → {end}\n"
        f"generated {today_s}\n"
        "```\n\n"
        "## Hours\n\n"
    )
    if rows:
        md += "| Date | Project | Hours | Note |\n|---|---|---|---|\n" + "\n".join(rows)
        md += f"\n\n**Total: {total_hours:g} h**\n"
    else:
        md += ("_No hours recorded in this window — add a `Logged time: 9:00 - 17:30` "
               "line to a day, or an itemised `## Timesheet` bullet._\n")
    md += ("\n## Days\n\n| Day | Items | Hours | Projects |\n|---|---|---|---|\n"
           + "\n".join(day_lines or ["| — | 0 | | |"]))
    # Name by the RANGE, not the generation date: exporting FY2024 and then
    # FY2023 would otherwise both land as `timesheet-<today>.pdf`, so the
    # second overwrites the first and neither filename says what it covers.
    fname = f"timesheet-{start}-to-{end}.pdf"
    out_dir = self.data_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        await self.render_pdf(md, str(out_dir / fname), style="slate")
    except Exception as e:
        return {"error": f"PDF render failed: {e}"}
    return self.serve_data_file("exports", fname, media_type="application/pdf",
                                download_name=fname)
