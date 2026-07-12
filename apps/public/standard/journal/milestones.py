"""journal — milestones, three-things, pins, and off-system activity logging.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The structured-section write surfaces (milestone, three-things), the pins store, and the wheel-blind-spot activity logger. Source of truth for those endpoints + voice_log_activity.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._daily_lock/_ensure_daily/_daily_path/_add_entry (spine) for the locked read-modify-write path.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from emptyos.sdk import dimensions, parse_llm_json, web_route

from .parser import extract_section, parse_entries, replace_section
from .prompts import PROMPTS

if TYPE_CHECKING:
    from .app import JournalApp  # noqa: F401 — for type hints only


# ─── Bind to JournalApp class as ────────────────────────────────
#   api_pin             = _milestones.api_pin
#   api_pins            = _milestones.api_pins
#   api_milestone       = _milestones.api_milestone
#   api_three_things    = _milestones.api_three_things
#   api_draft_three_things = _milestones.api_draft_three_things
#   three_things_filled = _milestones.three_things_filled
#   api_milestones      = _milestones.api_milestones
#   _load_pins          = _milestones._load_pins
#   _save_pins          = _milestones._save_pins
#   _log_activity       = _milestones._log_activity
#   api_activity        = _milestones.api_activity
#   voice_log_activity  = _milestones.voice_log_activity
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("POST", "/api/pin")
async def api_pin(self, request):
    """Pin/unpin a journal entry as highlight."""
    data = await self.read_json(request)
    pins = self._load_pins()
    entry = {
        "date": data.get("date", ""),
        "time": data.get("time", ""),
        "text": data.get("text", ""),
    }
    key = f"{entry['date']}_{entry['time']}"
    if key in pins:
        del pins[key]
        self._save_pins(pins)
        return {"pinned": False, "key": key}
    pins[key] = entry
    self._save_pins(pins)
    return {"pinned": True, "key": key}


@web_route("GET", "/api/pins")
async def api_pins(self, request):
    """Get all pinned journal entries."""
    return list(self._load_pins().values())


@web_route("POST", "/api/milestone")
async def api_milestone(self, request):
    """Set the day's milestone under ### Milestone (one achievement per day).

    Set-semantics, not append: the UI is a single "Today's Milestone" field
    that autosaves on every typing pause, so appending would compound a new
    bullet on each pause. Honours an optional ``date`` so editing a past
    day writes to that day (the frontend posts the viewed date), not today.
    Section is singular ``### Milestone`` — matching the daily-note template,
    ``api_today``'s read, and the related-entries corpus (was ``### Milestones``,
    which orphaned the write from every reader).
    """
    data = await self.read_json(request)
    text = data.get("text", "").strip()
    if not text:
        return {"error": "text required"}
    try:
        target = date.fromisoformat(data["date"]) if data.get("date") else date.today()
    except ValueError:
        return {"error": "date must be YYYY-MM-DD"}

    async with self._daily_lock(target):
        content = await self._ensure_daily(target)
        new_section = f"- 🏆 {text}"
        new_content = replace_section(content, "### Milestone", new_section)
        await self.write(str(self._daily_path(target)), new_content)
    await self.emit("journal:milestone", {"date": target.isoformat(), "text": text})
    return {"ok": True, "date": target.isoformat()}


@web_route("POST", "/api/three-things")
async def api_three_things(self, request):
    """Save the day's three good things.

    Accepts: {"things": ["a", "b", "c"], "date"?: "YYYY-MM-DD"}
    Up to three positional items; missing slots stay blank.
    """
    data = await self.read_json(request)
    # Schema returned on every error so callers don't have to guess
    # alternate key shapes (thing1/first/good/etc.) on retry.
    schema = {
        "expected": '{"things": ["a", "b", "c"]}',
        "also_accepted": [
            '{"thing1": "a", "thing2": "b", "thing3": "c"}',
            '{"first": "a", "second": "b", "third": "c"}',
        ],
        "optional": '"date": "YYYY-MM-DD"',
    }
    things = data.get("things")
    if things is None:
        things = [
            data.get("thing1") or data.get("first") or "",
            data.get("thing2") or data.get("second") or "",
            data.get("thing3") or data.get("third") or "",
        ]
    if not isinstance(things, list):
        return {"error": "things must be a list of strings", "schema": schema}
    things = [(t or "").strip() for t in things[:3]]
    while len(things) < 3:
        things.append("")
    if not any(things):
        return {
            "error": "at least one of things[0..2] must be non-empty",
            "schema": schema,
        }

    try:
        target = date.fromisoformat(data["date"]) if data.get("date") else date.today()
    except ValueError:
        return {"error": "date must be YYYY-MM-DD"}

    async with self._daily_lock(target):
        content = await self._ensure_daily(target)
        new_section = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(things)).rstrip() + "\n"
        new_content = replace_section(content, "#### Three successful things", new_section)
        await self.write(str(self._daily_path(target)), new_content)
    await self.emit("journal:three-things", {"date": target.isoformat()})
    # Echo what was actually saved so the caller can confirm rather than
    # trust a bare ok:true (a dogfood persona flagged the missing echo).
    saved = [t for t in things if t]
    return {"ok": True, "date": target.isoformat(), "things": saved, "saved_count": len(saved)}


@web_route("GET", "/api/three-things/draft")
async def api_draft_three_things(self, request):
    """Draft up to three "things I shipped/moved today" from real activity signals.

    Read-only — never writes the vault. Flips the blank three-things box from
    *write* to *confirm*: gathers today's cross-system activity (completed tasks,
    reactor breadcrumbs for commits/focus/workouts, focus deep-work) and asks the
    model for up to three concrete first-person wins. The UI pre-fills empty slots
    with the result; the existing POST /api/three-things persists on confirm/edit.

    Dark-flagged behind [apps.journal] feature.three-things-draft.enabled.
    Never raises — an empty/degraded response just yields no drafts.
    """
    if not self.app_config("feature.three-things-draft.enabled", False):
        return {"drafts": [], "reason": "disabled"}

    d = request.query_params.get("date", date.today().isoformat())
    try:
        target = date.fromisoformat(d)
    except ValueError:
        target = date.today()
    today_iso = target.isoformat()

    signals: list[str] = []

    # 1. Reactor breadcrumbs already funnel today's cross-system activity into the
    #    daily note as auto:True entries (✅ Completed / 💻 Committed / 🍅 / 💪 / 🌟 …).
    #    Read the daily note directly — this method is bound to the journal app, so
    #    `self` is journal; no call_app round-trip (and api_today needs a request).
    try:
        content = await self.read(str(self._daily_path(target)))
        for e in parse_entries(content):
            if e.get("auto") and e.get("text"):
                signals.append(str(e["text"]).strip())
        mile = (extract_section(content, "### Milestone") or "").strip()
        mile = mile.lstrip("-*🏆 ").strip()
        if mile:
            signals.append(f"Milestone: {mile}")
    except Exception:
        pass

    # 2. Authoritative completed-task text for today (indexer reads the whole vault).
    try:
        done = await self.call_app("task", "list_tasks", done=True)
        for t in done or []:
            dd = getattr(t, "done_date", None) or (t.get("done_date") if isinstance(t, dict) else None)
            txt = getattr(t, "text", None) or (t.get("text") if isinstance(t, dict) else None)
            if dd and str(dd)[:10] == today_iso and txt:
                signals.append(f"Completed: {str(txt).strip()}")
    except Exception:
        pass

    # 3. Deep-work volume (focus sessions today).
    try:
        fs = await self.call_app("focus", "today_stats")
        mins = (fs or {}).get("total_minutes") or 0
        sess = (fs or {}).get("sessions") or 0
        if mins:
            signals.append(f"Focus: {sess} deep-work session(s), {mins} min total")
    except Exception:
        pass

    # De-dup (breadcrumbs and task list can overlap) while preserving order.
    seen: set[str] = set()
    uniq = [s for s in signals if s and not (s in seen or seen.add(s))]
    if not uniq:
        return {"drafts": [], "reason": "no-signals"}

    digest = "Today's activity signals:\n" + "\n".join(f"- {s}" for s in uniq[:40])
    reply = await self.think_safe(
        digest,
        domain="text",
        system=PROMPTS.three_things_draft_system,
        temperature=0.3,
        fallback="",
    )
    if not reply.strip():
        return {"drafts": [], "reason": "no-model"}

    parsed = parse_llm_json(reply)
    drafts: list[str] = []
    if isinstance(parsed, list):
        for item in parsed:
            s = str(item).strip()
            if not s:
                continue
            # keep single-line; strip only a leading list marker ("1. " / "2) ")
            s = re.sub(r"^\s*\d+[.)]\s+", "", s.splitlines()[0]).strip()
            if s:
                drafts.append(s)
    # Provenance so the UI can mark the drafts as AI-authored (DL-7 / authorship
    # boundary) — same shape the AI-reflect path returns.
    return {"drafts": drafts[:3], "signal_count": len(uniq), "provenance": self.last_provenance()}


async def three_things_filled(self, day: str = "") -> bool:
    """True if the day's three-things box holds any real content (not the blank
    ``1.\\n2.\\n3.`` template). A plain call_app-able helper (no request) so other
    apps — the evening-wins nudge — can check "already done?" without api_today."""
    try:
        target = date.fromisoformat(day) if day else date.today()
    except ValueError:
        target = date.today()
    try:
        content = await self.read(str(self._daily_path(target)))
    except Exception:
        return False
    section = extract_section(content, "#### Three successful things") or ""
    return any(
        line.strip().lstrip("0123456789.").strip()
        for line in section.splitlines()
    )


@web_route("GET", "/api/milestones")
async def api_milestones(self, request):
    """List milestones from recent journal entries."""
    days = int(request.query_params.get("days", "90"))
    today = date.today()
    milestones = []
    for i in range(days):
        d = today - timedelta(days=i)
        try:
            content = await self.read(str(self._daily_path(d)))
            section = extract_section(content, "### Milestone")
            if section:
                for line in section.split("\n"):
                    line = line.strip()
                    if line.startswith("- "):
                        milestones.append({"date": d.isoformat(), "text": line[2:].strip()})
        except Exception:
            continue
    return milestones


def _load_pins(self) -> dict:
    p = self.data_dir / "pins.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _save_pins(self, pins: dict):
    (self.data_dir / "pins.json").write_text(json.dumps(pins, indent=2, default=str))


async def _log_activity(self, d: date, activity: str, dimension: str = ""):
    activity = " ".join((activity or "").split()).strip()
    if not activity:
        raise ValueError("activity is empty")
    dim = (dimension or "").strip().lower()
    if dim not in dimensions.DIMENSIONS:
        # Infer the dimension silently from the activity text (wheel stays a
        # silent rubric — no picker, no asking). Physical is the fallback:
        # off-keyboard activity is the primary blind-spot this closes.
        inferred = dimensions.scan_text(activity)
        dim = max(inferred, key=inferred.get) if any(inferred.values()) else "physical"
    icon = dimensions.ICONS.get(dim, "")
    line = f"{icon} {activity} #{dim}".strip()
    await self._add_entry(d, line)
    return dim


@web_route("POST", "/api/activity")
async def api_activity(self, request):
    """Log an off-system activity to today's journal, tagged to a wheel dimension."""
    data = await self.read_json(request)
    activity = data.get("activity") or data.get("text") or ""
    dimension = data.get("dimension") or ""
    d = date.today()
    if data.get("date"):
        try:
            d = date.fromisoformat(data["date"])
        except ValueError:
            pass
    try:
        dim = await self._log_activity(d, activity, dimension)
    except ValueError as e:
        return {"error": str(e)}
    return {"status": "ok", "date": d.isoformat(), "dimension": dim}


async def voice_log_activity(self, activity: str, dimension: str = "") -> dict:
    """Voice intent → log an off-system activity (class, workout, outing)."""
    activity = " ".join((activity or "").split()).strip()
    if not activity:
        return {"say": "What did you do? Try 'log a jumper class'."}
    try:
        await self._log_activity(date.today(), activity, dimension)
    except Exception as e:
        return {"say": f"Couldn't log that — {e}"}
    return {
        "say": f"Logged: {activity}.",
        "link": {"text": "Open today's journal", "href": "/journal/"},
    }
