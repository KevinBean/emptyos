"""Calendar App — unified schedule view across EmptyOS.

Aggregates dated items from task, reminders, bookme, and scheduler into a
month grid. Today's agenda surfaces on the hub panel and as Aura voice
context. Reads only — never writes; mutations happen in source apps.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.utils import clamp_days, normalize_relative_date

from . import ics as _ics

CALENDAR_BRIEF_SYSTEM = (
    "You are a morning-brief writer. Given today's and tomorrow's agenda "
    "items, write 2-3 sentences: timed items first, then the most important "
    "due tasks. Name at most three things in total.\n\n"
    "Do NOT:\n"
    "- Invent events that are not in the list.\n"
    "- Enumerate every item.\n"
    "- Use bullet points, headings, or times the list doesn't contain.\n"
    "- Moralise about being busy or productive."
)


def _ics_escape(s: str) -> str:
    """Escape TEXT per RFC 5545 §3.3.11."""
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


class CalendarApp(BaseApp):
    async def setup(self):
        await super().setup()
        # Warm an empty cache after explicit opt-in without delaying app boot.
        if self._ics_enabled() and self._ics_sources() and not self._ics_cache_path().exists():
            self.spawn_background(self.refresh_ics_feeds())

    def _show(self, key: str) -> bool:
        """Source-toggle from the ⚙ panel; defaults on. String-safe read."""
        v = self.setting(f"calendar.{key}", True)
        if isinstance(v, str):
            return v.strip().lower() not in ("false", "0", "no", "off", "")
        return bool(v)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    async def _collect_month(self, year: int, month: int) -> dict[str, list[dict]]:
        """Return {YYYY-MM-DD: [item, ...]} covering the named month plus
        a small lead/lag so the 7×6 grid's other-month cells are also populated."""
        first = datetime.date(year, month, 1)
        # Lead/lag: pull 7 days either side so other-month cells show density too.
        start = first - datetime.timedelta(days=7)
        if month == 12:
            end = datetime.date(year + 1, 1, 1) + datetime.timedelta(days=7)
        else:
            end = datetime.date(year, month + 1, 1) + datetime.timedelta(days=7)

        out: dict[str, list[dict]] = {}

        def _push(date_str: str, item: dict) -> None:
            ds = (date_str or "")[:10]
            if not ds or ds < start.isoformat() or ds >= end.isoformat():
                return
            out.setdefault(ds, []).append(item)

        # Tasks with due dates — pull both open and done so the calendar shows
        # what was completed on a given day too (and the user can toggle either way).
        for done_flag in (False, True):
            if not self._show("show_tasks"):
                break
            if done_flag and not self._show("show_done_tasks"):
                continue
            try:
                tasks = await self.call_app("task", "list_tasks", overdue_only=False, done=done_flag)
                for t in tasks or []:
                    def _f(name: str, _t=t) -> str:
                        return getattr(_t, name, None) or (_t.get(name, "") if isinstance(_t, dict) else "") or ""
                    due = _f("due")
                    if not due:
                        continue
                    today = datetime.date.today().isoformat()
                    ds = due[:10]
                    if done_flag:
                        tone = "done"
                    elif ds < today:
                        tone = "overdue"
                    elif ds == today:
                        tone = "today"
                    else:
                        tone = None
                    fpath = _f("file").replace("\\", "/")
                    _push(due, {
                        "type": "task",
                        "title": _f("text"),
                        "time": "",
                        "tone": tone,
                        "source": "task",
                        "file": fpath,
                        "line": getattr(t, "line", None) or (t.get("line") if isinstance(t, dict) else None),
                        "done": done_flag,
                    })
            except Exception:
                pass

        # Reminders — uses the public list_all() that the boards integration also relies on.
        if self._show("show_reminders"):
            try:
                rems = await self.call_app("reminders", "list_all")
                today = datetime.date.today().isoformat()
                for r in rems or []:
                    if r.get("status") != "active":
                        continue
                    due = r.get("due") or ""
                    if not due:
                        continue
                    ds = due[:10]
                    tone = "overdue" if ds < today else ("today" if ds == today else None)
                    if r.get("priority") == "high" and tone is None:
                        tone = "today"
                    _push(due, {
                        "type": "reminder",
                        "title": r.get("text", ""),
                        "time": due[11:16] if len(due) > 10 else "",
                        "tone": tone,
                        "source": "reminders",
                        "id": r.get("id", ""),
                        "href": "/reminders/",
                    })
            except Exception:
                pass

        # BookMe confirmed bookings — iterate days in window
        if self._show("show_bookings"):
            try:
                d = start
                while d < end:
                    ds = d.isoformat()
                    try:
                        booked = await self.call_app("bookme", "list_day", ds)
                        for b in booked or []:
                            _push(ds, {
                                "type": "booking",
                                "title": b.get("title") or "Booking",
                                "time": b.get("time") or "",
                                "tone": None,
                                "source": "bookme",
                                "id": b.get("id", ""),
                                "href": "/bookme/",
                            })
                    except Exception:
                        pass
                    d += datetime.timedelta(days=1)
            except Exception:
                pass

        # Worklog days — one item per project logged that day, deep-linked to
        # the day's detail view. Soft dependency: absent worklog degrades clean.
        if self._show("show_worklog"):
            wl, _err = await self.try_call_app(
                "worklog", "month_cells", month=f"{year:04d}-{month:02d}"
            )
            for cell in (wl or {}).get("cells", []):
                for it in cell.get("items", []):
                    _push(cell.get("date", ""), {
                        "type": "worklog",
                        "title": it.get("label", ""),
                        "time": "",
                        "tone": it.get("tone") or None,
                        "source": "worklog",
                        "href": f"/worklog/#{cell.get('date', '')}",
                    })

        # Cached subscribed calendars. Rendering never fetches the network;
        # refresh is explicit or daily and imported events remain read-only.
        if self._show("show_ics"):
            for event in self._cached_ics_events(start, end):
                _push(event.get("date", ""), {
                    "type": "event",
                    "title": event.get("title", ""),
                    "time": event.get("time", ""),
                    "tone": None,
                    "source": event.get("source", "Subscribed calendar"),
                    "id": event.get("id", ""),
                    "href": event.get("href", ""),
                    "read_only": True,
                })

        return out

    async def get_agenda(self, target_date: str) -> list[dict]:
        """Aggregate items for one date — used by hub panel + voice + side panel."""
        # Parse year/month from the date so we reuse _collect_month
        try:
            d = datetime.date.fromisoformat(target_date[:10])
        except Exception:
            return []
        by_day = await self._collect_month(d.year, d.month)
        items = by_day.get(target_date[:10], [])
        # Shape kept for backwards compat with existing hub-panel renderer.
        return [
            {
                "time": it.get("time") or "All Day",
                "title": it.get("title", ""),
                "type": it.get("type", "task"),
                "source": it.get("source", ""),
                "file": it.get("file", ""),
                "line": it.get("line"),
                "id": it.get("id", ""),
                "href": it.get("href", ""),
                "done": it.get("done", False),
            }
            for it in items
        ]

    async def timeline_items(self, days: int = 1) -> list[dict]:
        """Life-suite timeline contribution ([[contributes.life.timeline]]).

        Flagged in gap analysis (life-three-contributors): `calendar` owns
        day-shaped events but declared nothing. One item per agenda entry
        over the last ``days`` days (today's events + recent history), most
        recent first. Item shape (suite contract,
        docs/suites/life-cohesion.md): {ts, title, kind, href}.
        """
        n = clamp_days(days)
        today = datetime.date.today()
        items: list[dict] = []
        for i in range(n):
            d = today - datetime.timedelta(days=i)
            for it in await self.get_agenda(d.isoformat()):
                t = it.get("time") or ""
                hhmm = t if len(t) == 5 and t[2] == ":" else "00:00"
                items.append({
                    "ts": f"{d.isoformat()}T{hhmm}:00",
                    "title": it.get("title", ""),
                    "kind": f"calendar-{it.get('type', 'event')}",
                    "href": "/calendar/",
                })
        return items

    async def busy_intervals(self, date: str = "", default_min: int = 0) -> list[dict]:
        """Time ranges on ``date`` where the owner is genuinely occupied.

        Consumed by scheduling surfaces that must not offer a slot during an
        existing commitment — `bookme` is the first caller. Deliberately narrow:

        * **Only subscribed-calendar (ICS) events count.** They are the one
          source that means "you are in something". Tasks and worklog entries
          are all-day markers, not blocks; reminders are point-in-time nudges;
          and `bookme` knows its own bookings exactly, so re-reading them here
          would double-count against a guessed duration.
        * Reads the local ICS cache directly rather than going through
          ``_collect_month`` — that fans out across four apps and ~45 days of
          ``bookme.list_day``, which a caller holding a booking lock must not
          pay for on every public slot request.

        ``default_min`` covers events whose length we cannot know (an older
        cache entry, an unparseable DTEND); 0 skips them rather than inventing
        a block. Returns ``[{start, end, title, source}]`` with ``HH:MM`` times.
        """
        day = (date or "")[:10]
        if not day or not self._show("show_ics"):
            return []
        try:
            target = datetime.date.fromisoformat(day)
        except ValueError:
            return []

        out: list[dict] = []
        skipped_no_end = 0
        for event in self._cached_ics_events(target, target + datetime.timedelta(days=1)):
            start = (event.get("time") or "").strip()
            if not start:
                continue  # all-day event — occupies the date, not a time range
            end = (event.get("end") or "").strip()
            if not end:
                if default_min <= 0:
                    skipped_no_end += 1
                    continue
                try:
                    start_dt = datetime.datetime.strptime(f"{day} {start}", "%Y-%m-%d %H:%M")
                except ValueError:
                    continue
                end_dt = start_dt + datetime.timedelta(minutes=default_min)
                end = "23:59" if end_dt.date() > target else end_dt.strftime("%H:%M")
            if end <= start:
                continue
            out.append({
                "start": start,
                "end": end,
                "title": event.get("title") or "",
                "source": event.get("source") or "Subscribed calendar",
            })
        if skipped_no_end:
            # Never drop coverage silently — a scheduling surface that quietly
            # ignores commitments looks identical to one with nothing to ignore,
            # which is how the double-booking bug hid in the first place. This
            # line is the evidence for whether `external_busy_default_min` is
            # worth raising on this machine's feeds.
            self.log(
                f"busy_intervals({day}): ignored {skipped_no_end} timed event(s) "
                f"with no end time — raise the caller's default duration to block them",
                level="warning",
                data={"date": day, "skipped_no_end": skipped_no_end},
            )
        return sorted(out, key=lambda r: r["start"])

    async def voice_today_agenda(self, day: str = "") -> dict:
        """Voice verb — speak one day's agenda + a task-list card.

        `day` is an ISO date or a relative word (today / tomorrow / 明天);
        empty means today. get_agenda keys on an exact YYYY-MM-DD, so a word
        passed straight through found nothing and said "Nothing on your agenda
        today" — the phone baseline's q01 (2026-09-30). An unreadable day is
        refused out loud rather than silently read as today.
        """
        raw = (day or "").strip()
        today = datetime.date.today().isoformat()
        target = normalize_relative_date(raw) if raw else today
        if raw and not target:
            return {"say": f"I couldn't read the date '{raw}' — try today, tomorrow, or YYYY-MM-DD.",
                    "link": {"text": "Open calendar", "href": "/calendar/"}}
        try:
            agenda = await self.get_agenda(target)
        except Exception:
            agenda = []
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        when = "today" if target == today else ("tomorrow" if target == tomorrow else f"on {target}")
        if not agenda:
            return {"say": f"Nothing on your agenda {when}.",
                    "link": {"text": "Open calendar", "href": "/calendar/"}}
        rows = []
        for it in agenda[:8]:
            tone = "done" if it.get("done") else (it.get("type") == "reminder" and "today" or "")
            label = it.get("title", "")
            t = it.get("time")
            rows.append({"text": (f"{t} — {label}" if t and t != "All Day" else label),
                         "tone": tone, "tag": it.get("type", "")})
        return {
            "say": f"You have {len(agenda)} item(s) on your agenda {when}.",
            "card": {"renderer": "task-list", "title": f"Agenda · {when}", "data": rows},
            "link": {"text": "Open calendar", "href": "/calendar/"},
        }

    # ------------------------------------------------------------------
    # Hub Panel contribution
    # ------------------------------------------------------------------
    async def panel_agenda(self) -> list[dict] | None:
        today = datetime.date.today().isoformat()
        agenda = await self.get_agenda(today)
        if not agenda:
            return None
        out = []
        for item in agenda[:5]:
            out.append(
                {
                    "text": item["title"],
                    "tag": item["time"],
                    "tag_tone": "blue",
                    "href": "/calendar/",
                }
            )
        return out

    # ------------------------------------------------------------------
    # Voice Assistant contribution
    # ------------------------------------------------------------------
    async def assistant_context(self) -> str | None:
        today = datetime.date.today().isoformat()
        agenda = await self.get_agenda(today)
        if not agenda:
            return "There are no scheduled events or task deadlines for today."
        out = "Today's Agenda:\n"
        for item in agenda:
            out += f"- [{item['time']}] {item['title']} ({item['type']})\n"
        return out

    # ------------------------------------------------------------------
    # Web API
    # ------------------------------------------------------------------
    @web_route("GET", "/api/today")
    async def api_today(self, request):
        today = datetime.date.today().isoformat()
        return await self.get_agenda(today)

    @web_route("GET", "/api/month")
    async def api_month(self, request):
        """Return {cells: [{date, items}]} for one month."""
        ym = request.query_params.get("month", "")
        try:
            year, month = (int(x) for x in ym.split("-")[:2])
        except Exception:
            today = datetime.date.today()
            year, month = today.year, today.month
        by_day = await self._collect_month(year, month)
        cells = [{"date": ds, "items": items} for ds, items in sorted(by_day.items())]
        return {"month": f"{year:04d}-{month:02d}", "cells": cells}

    @web_route("GET", "/api/day")
    async def api_day(self, request):
        """Return agenda for one ISO date — used by the side panel."""
        date_str = request.query_params.get("date", "")
        if not date_str:
            date_str = datetime.date.today().isoformat()
        return {"date": date_str, "items": await self.get_agenda(date_str)}

    @web_route("POST", "/api/quick-add")
    async def api_quick_add(self, request):
        """Create a dated item without leaving the calendar.

        Calendar-quick-add (calendar-quick-add gap): the app itself never
        writes — this **routes** the create to the owning app (task.add /
        reminders.add), preserving the read-only-aggregator posture exactly.
        Body: {kind: "task"|"reminder", text, due, time?}.
        """
        body = await request.json()
        kind = (body.get("kind") or "reminder").strip().lower()
        text = (body.get("text") or "").strip()
        due = (body.get("due") or "").strip()
        time_ = (body.get("time") or "").strip()
        if not text:
            return {"error": "text is required"}
        if not due:
            return {"error": "a date is required"}

        if kind == "task":
            try:
                await self.call_app("task", "add", text=text, due=due)
            except (KeyError, AttributeError):
                return {"error": "the task app is not installed"}
            except Exception as exc:
                return {"error": str(exc) or "could not create the task"}
            return {"ok": True, "type": "task"}

        if kind == "reminder":
            due_value = f"{due}T{time_}:00" if time_ else due
            try:
                result = await self.call_app("reminders", "add", text=text, due=due_value, time=time_)
            except (KeyError, AttributeError):
                return {"error": "the reminders app is not installed"}
            except Exception as exc:
                return {"error": str(exc) or "could not create the reminder"}
            if isinstance(result, dict) and result.get("error"):
                return {"error": result["error"]}
            return {"ok": True, "type": "reminder"}

        return {"error": f"unknown kind '{kind}' — use 'task' or 'reminder'"}

    @web_route("GET", "/api/export.ics")
    async def api_export_ics(self, request):
        """iCalendar feed over a rolling 3-month window (prev/current/next).

        All-day VEVENTs, one per aggregated item; subscribe from any external
        calendar client. Done tasks are excluded — the feed is forward-facing.
        """
        from starlette.responses import Response

        today = datetime.date.today()
        y, m = today.year, today.month
        window = [
            (y - 1, 12) if m == 1 else (y, m - 1),
            (y, m),
            (y + 1, 1) if m == 12 else (y, m + 1),
        ]
        seen: set[tuple] = set()
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//EmptyOS//Calendar//EN"]
        for yy, mm in window:
            by_day = await self._collect_month(yy, mm)
            for ds, items in sorted(by_day.items()):
                for it in items:
                    if it.get("done"):
                        continue
                    key = (ds, it.get("type"), it.get("title"))
                    if key in seen:  # months' lead/lag windows overlap
                        continue
                    seen.add(key)
                    # Stable UID across restarts so subscribed clients dedupe.
                    uid = hashlib.md5(repr(key).encode("utf-8")).hexdigest()[:16] + "@emptyos"
                    dt = ds.replace("-", "")
                    lines += [
                        "BEGIN:VEVENT",
                        f"UID:{uid}",
                        f"DTSTART;VALUE=DATE:{dt}",
                        f"SUMMARY:{_ics_escape(it.get('title', ''))}",
                        f"CATEGORIES:{_ics_escape((it.get('type') or '').upper())}",
                        "END:VEVENT",
                    ]
        lines.append("END:VCALENDAR")
        return Response(
            "\r\n".join(lines) + "\r\n",
            media_type="text/calendar",
            headers={"Content-Disposition": 'attachment; filename="emptyos-calendar.ics"'},
        )

    @web_route("GET", "/api/brief")
    async def api_brief(self, request):
        """AI morning brief over today + tomorrow's agenda. Cached per
        (day, agenda content); ``?live=1`` forces a fresh model call."""
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)
        t_items = await self.get_agenda(today.isoformat())
        tm_items = await self.get_agenda(tomorrow.isoformat())
        if not t_items and not tm_items:
            return {"brief": "Nothing scheduled today or tomorrow.", "from_cache": False}

        def _fmt(items: list[dict]) -> str:
            return "\n".join(
                f"- [{i['time']}] {i['title']} ({i['type']})" for i in items if not i.get("done")
            ) or "(nothing)"

        prompt = f"Today {today.isoformat()}:\n{_fmt(t_items)}\n\nTomorrow:\n{_fmt(tm_items)}"
        brief, from_cache = await self.think_cached(
            prompt,
            key={"kind": "brief", "date": today.isoformat(), "agenda": prompt},
            system=CALENDAR_BRIEF_SYSTEM,
            domain="text",
            temperature=0.4,
            force_live=request.query_params.get("live") == "1",
        )
        return {
            "brief": brief,
            "from_cache": from_cache,
            "provenance": self.last_provenance() if not from_cache else None,
        }

    # ── Cached ICS subscriptions (extracted to ics.py) ──
    _ics_enabled = _ics._ics_enabled
    _ics_sources = _ics._ics_sources
    _ics_cache_path = _ics._ics_cache_path
    _load_ics_cache = _ics._load_ics_cache
    _save_ics_cache = _ics._save_ics_cache
    _fetch_ics = _ics._fetch_ics
    refresh_ics_feeds = _ics.refresh_ics_feeds
    _cached_ics_events = _ics._cached_ics_events
    scheduled_ics_refresh = _ics.scheduled_ics_refresh
    api_ics_refresh = _ics.api_ics_refresh
    api_ics_status = _ics.api_ics_status
