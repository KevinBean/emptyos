"""people — relationship health, reach-out, birthdays, due-list.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The 'soft' relationship side — overdue contacts, reach-out candidates, birthday windows, notification + due/frequency endpoints, and the reach-out / birthday hub panels.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._health_score / self._frequency_days / self._days_since / self.list_people (spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from emptyos.sdk import scheduled, web_route

if TYPE_CHECKING:
    from .app import PeopleApp  # noqa: F401 — for type hints only


# ─── Bind to PeopleApp class as ────────────────────────────────
#   _get_overdue           = _engagement._get_overdue
#   _reach_out_candidates  = _engagement._reach_out_candidates
#   api_frequency          = _engagement.api_frequency
#   api_due                = _engagement.api_due
#   birthdays              = _engagement.birthdays
#   api_birthdays          = _engagement.api_birthdays
#   api_notifications      = _engagement.api_notifications
#   scheduled_birthday_push = _engagement.scheduled_birthday_push
#   panel_reach_out        = _engagement.panel_reach_out
#   panel_birthdays        = _engagement.panel_birthdays
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _get_overdue(self, people: list[dict]) -> list[dict]:
    overdue = []
    for p in people:
        freq_days = self._frequency_days(p.get("contact_frequency", ""))
        if freq_days is None:
            continue
        days = p.get("days_since")
        if days is None:
            overdue.append({**p, "days_overdue": freq_days, "overdue_ratio": 2.0})
            continue
        threshold = freq_days * 1.5
        if days > threshold:
            overdue.append(
                {
                    **p,
                    "days_overdue": int(days - freq_days),
                    "overdue_ratio": round(days / freq_days, 1),
                }
            )
    overdue.sort(key=lambda x: -x["overdue_ratio"])
    return overdue


def _reach_out_candidates(self, people: list[dict]) -> list[dict]:
    """Who to reach out to, for the hub nudge. Two tiers:
    1. Cadence-overdue — has a `contact_frequency` and is past it (1.5×).
    2. Gone quiet — a real relationship with no cadence set whose last
       contact is older than `_COLD_DAYS`. Drains-energy people are skipped
       (the nudge should feed, not deplete).
    Cadence-overdue ranks first (by ratio), then cold (by staleness).
    """
    out: list[dict] = []
    seen: set[str] = set()
    for p in self._get_overdue(people):
        freq = (p.get("contact_frequency") or "").strip()
        if not (p.get("last_contact") or "").strip():
            reason = "never logged" + (f" · {freq}" if freq else "")
        else:
            reason = f"{p['days_overdue']}d overdue" + (f" · {freq}" if freq else "")
        out.append(
            {
                "id": p["id"],
                "name": p["name"],
                "reason": reason,
                "energy": p.get("energy", ""),
                "_sort": (0, -p.get("overdue_ratio", 0)),
            }
        )
        seen.add(p["id"])
    for p in people:
        if p["id"] in seen:
            continue
        if not (p.get("relationship") or "").strip():
            continue  # no declared relationship → not a personal tie to nudge
        if (p.get("energy") or "").lower() == "drains":
            continue
        if self._frequency_days(p.get("contact_frequency", "")) is not None:
            continue  # has a cadence → tier 1 already considered it
        days = p.get("days_since")
        if days is None or days < self._COLD_DAYS:
            continue
        out.append(
            {
                "id": p["id"],
                "name": p["name"],
                "reason": f"no contact in {days}d",
                "energy": p.get("energy", ""),
                "_sort": (1, -days),
            }
        )
    out.sort(key=lambda x: x["_sort"])
    return out


@web_route("GET", "/api/frequency")
async def api_frequency(self, request):
    people = await self.list_people()
    groups: dict[str, list] = {
        "weekly": [],
        "monthly": [],
        "quarterly": [],
        "yearly": [],
        "unset": [],
    }
    for p in people:
        freq = (p.get("contact_frequency", "") or "").lower().strip()
        (groups[freq] if freq in groups else groups["unset"]).append(p)
    return groups


@web_route("GET", "/api/due")
async def api_due(self, request):
    return self._get_overdue(await self.list_people())


async def birthdays(self, days: int = 30) -> list[dict]:
    """Upcoming birthdays within `days`. Public — callable via call_app."""
    people = await self.list_people()
    today = date.today()
    out = []
    for p in people:
        bday = p.get("birthday", "")
        if not bday:
            continue
        try:
            bday_date = datetime.strptime(bday, "%Y-%m-%d").date()
            this_year = bday_date.replace(year=today.year)
            if this_year < today:
                this_year = this_year.replace(year=today.year + 1)
            days_until = (this_year - today).days
            if days_until <= days:
                out.append(
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "birthday": bday,
                        "days_until": days_until,
                        "turning": this_year.year - bday_date.year
                        if bday_date.year < today.year
                        else 0,
                    }
                )
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["days_until"])
    return out


@web_route("GET", "/api/birthdays")
async def api_birthdays(self, request):
    days_ahead = int(request.query_params.get("days", 30))
    out = await self.birthdays(days=days_ahead)
    return out


@web_route("GET", "/api/notifications")
async def api_notifications(self, request):
    days_ahead = int(self.setting("people.birthday_alert_days", 7))
    people = await self.list_people()
    notifications = []
    today = date.today()
    for p in people:
        bday = p.get("birthday", "")
        if not bday:
            continue
        try:
            bday_date = datetime.strptime(bday, "%Y-%m-%d").date()
            this_year = bday_date.replace(year=today.year)
            if this_year < today:
                this_year = this_year.replace(year=today.year + 1)
            days_until = (this_year - today).days
            if days_until <= days_ahead:
                age = today.year - bday_date.year
                notifications.append(
                    {
                        "type": "birthday",
                        "id": p["id"],
                        "name": p["name"],
                        "message": f"Birthday in {days_until}d"
                        + (f" (turning {age})" if days_until > 0 else " (TODAY!)"),
                        "days": days_until,
                        "priority": "high" if days_until <= 1 else "medium",
                    }
                )
        except ValueError:
            continue
    if self.setting("people.overdue_alert", True):
        overdue = self._get_overdue(people)
        for c in overdue[:10]:
            notifications.append(
                {
                    "type": "overdue",
                    "id": c["id"],
                    "name": c["name"],
                    "message": f"Overdue by {c['days_overdue']}d ({c['contact_frequency']})",
                    "days": c["days_overdue"],
                    "priority": "high" if c["overdue_ratio"] >= 2.0 else "medium",
                }
            )
    notifications.sort(key=lambda n: n["days"])
    return notifications


@scheduled("0 8 * * *", id="people-birthday-push")
async def scheduled_birthday_push(self):
    """Push one reminder per upcoming birthday, once per person/year."""
    live = self.setting("people.feature.people-birthday-push.enabled", None)
    enabled = (
        bool(live)
        if live is not None
        else bool(self.app_config("feature.people-birthday-push.enabled", False))
    )
    if not enabled:
        return {"enabled": False, "sent": 0}

    days = int(self.setting("people.birthday_alert_days", 7))
    upcoming = await self.birthdays(days=max(0, days))
    sent = 0
    year = date.today().year
    for person in upcoming:
        delta = int(person.get("days_until", 0))
        when = "today" if delta == 0 else f"in {delta} day{'s' if delta != 1 else ''}"
        age = person.get("turning") or 0
        suffix = f" (turning {age})" if age else ""
        try:
            await self.proactive_notify_or_raw(
                kind="birthday",
                text=f"{person.get('name', 'Someone')}'s birthday is {when}{suffix}.",
                dedup_key=f"birthday:{person.get('id', '')}:{year}",
                priority="high" if delta <= 1 else "info",
                source="people",
            )
            sent += 1
        except Exception:
            pass
    return {"enabled": True, "sent": sent}


async def panel_reach_out(self) -> list[dict] | None:
    """People going quiet — the social-cadence nudge on /hub/.
    Cadence-overdue + relationships gone cold without a set frequency.
    🟢 marks energy='gives' (worth prioritising)."""
    people = await self.list_people(active_only=True)
    cands = self._reach_out_candidates(people)
    if not cands:
        return None
    rows = []
    for c in cands[:5]:
        icon = "🟢" if (c.get("energy") or "").lower() == "gives" else "🤝"
        rows.append(
            {
                "title": f"{icon} {c['name']}",
                "subtitle": c["reason"],
                "href": f"/people/#{c['id']}",
            }
        )
    return rows


async def panel_birthdays(self) -> list[dict] | None:
    """Upcoming birthdays in the next 30 days."""
    people = await self.list_people()
    today = date.today()
    out = []
    for p in people:
        bday = p.get("birthday", "")
        if not bday:
            continue
        try:
            bday_date = datetime.strptime(bday, "%Y-%m-%d").date()
            this_year = bday_date.replace(year=today.year)
            if this_year < today:
                this_year = this_year.replace(year=today.year + 1)
            days = (this_year - today).days
            if days <= 30:
                age = this_year.year - bday_date.year
                out.append(
                    {
                        "title": f"🎂 {p['name']}",
                        "href": f"/people/#{p['id']}",
                        "subtitle": (
                            "TODAY!"
                            if days == 0
                            else f"in {days}d" + (f" · turns {age}" if age > 0 else "")
                        ),
                        "days": days,
                    }
                )
        except (ValueError, TypeError):
            continue
    if not out:
        return None
    out.sort(key=lambda x: x["days"])
    return out[:5]
