"""expense — recurring-expense rules + the cron that posts them.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The recurring rule CRUD, the daily cron registration/teardown, the catch-up run that back-posts missed periods, and the due-date advance. This is the path that auto-posts money without the user acting, so it is deliberately one module.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.add (spine) to write each posted expense; self.load_state / self._default_state / self.save_state (spine) for the rule list; _RECURRING_JOB from .shared (the spine's teardown removes the same job).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, timedelta
from emptyos.sdk import web_route
from .categories import detect_category as _detect_category
from .shared import _RECURRING_JOB
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ExpenseApp  # noqa: F401 — for type hints only


# ─── Bind to ExpenseApp class as ────────────────────────────────
#   _register_recurring_schedule  = _recurring._register_recurring_schedule
#   _scheduled_recurring_check    = _recurring._scheduled_recurring_check
#   api_recurring_reschedule      = _recurring.api_recurring_reschedule
#   api_get_recurring             = _recurring.api_get_recurring
#   api_add_recurring             = _recurring.api_add_recurring
#   api_recurring_check           = _recurring.api_recurring_check
#   _run_recurring_check          = _recurring._run_recurring_check
#   _advance_due                  = _recurring._advance_due  # @staticmethod
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _register_recurring_schedule(self):
    """Daily cron for the recurring-expense check.

    Before this, ``/api/recurring/check`` had exactly one caller: the expense
    page's own JS. So auto-posting silently depended on the user *visiting
    the app* — a recurring mechanism that only runs when observed. Rent went
    unlogged from 2026-06-16 onward for that reason, and the gap was only
    found by reconciling against net worth.
    """
    self.remove_cron_job(_RECURRING_JOB)
    if not self.setting_or_config("expense.recurring.enabled", True):
        return
    cron = str(self.setting_or_config("expense.recurring.cron", "0 6 * * *")).strip()
    ok = self.add_cron_job_logged(
        _RECURRING_JOB, self._scheduled_recurring_check, cron=cron or "0 6 * * *",
        crash_event="recurring_crash",
    )
    self.log_activity({"event": "recurring_schedule_registered" if ok
                       else "recurring_schedule_failed", "cron": cron})


async def _scheduled_recurring_check(self):
    await self._run_recurring_check(trigger="schedule")


@web_route("POST", "/api/recurring/reschedule")
async def api_recurring_reschedule(self, request):
    """Re-read settings and re-register the cron job (call after editing them)."""
    self._register_recurring_schedule()
    return {"ok": True, "next_run": self.get_cron_job_next_fire(_RECURRING_JOB)}


@web_route("GET", "/api/recurring")
async def api_get_recurring(self, request):
    """List all recurring expense rules."""
    state = self.load_state(self._default_state())
    return state.get("recurring", [])


@web_route("POST", "/api/recurring")
async def api_add_recurring(self, request):
    """Add a recurring expense rule.

    Body: {text, frequency: weekly|fortnightly|monthly|yearly, enabled?, next_due?}

    ``next_due`` defaults to today (first post happens on the next check, no
    backfill). Pass a past date to backfill from when the expense actually
    started — e.g. rent that stopped being logged on 2026-06-16 takes
    ``next_due: "2026-06-23"`` and back-posts every missed week on the next
    check, each entry dated to its own period.
    """
    data = await request.json()
    text = data.get("text", "")
    frequency = data.get("frequency", "monthly")
    if not text:
        return {"error": "text required"}
    if frequency not in ("weekly", "fortnightly", "monthly", "yearly"):
        return {"error": f"invalid frequency: {frequency}"}

    today = date.today()
    next_due = str(data.get("next_due") or today.isoformat())
    try:
        date.fromisoformat(next_due)
    except ValueError:
        return {"error": f"invalid next_due (want YYYY-MM-DD): {next_due}"}

    rule = {
        "text": text,
        "frequency": frequency,
        "next_due": next_due,
        "enabled": data.get("enabled", True),
        "last_logged": None,
        "created": today.isoformat(),
    }
    state = self.load_state(self._default_state())
    state.setdefault("recurring", []).append(rule)
    self.save_state(state)
    return {"ok": True, "rule": rule, "count": len(state["recurring"])}


@web_route("POST", "/api/recurring/check")
async def api_recurring_check(self, request):
    """Check and auto-log any due recurring expenses (manual trigger)."""
    return await self._run_recurring_check(trigger="manual")


async def _run_recurring_check(self, *, trigger: str = "manual") -> dict:
    """Post every due occurrence of every enabled rule.

    Called by the page (on open), by the daily cron job, and by tests. It
    **catches up**: a rule 3 weeks overdue posts 3 entries in one pass, each
    dated to the period it belongs to rather than all landing on today. That
    matters because the previous behaviour advanced ``next_due`` by exactly
    one period per call, so a rule only caught up if someone opened the page
    once per period — which is precisely how rent stopped being logged after
    2026-06-16 with nobody noticing.

    MAX_CATCHUP_PERIODS bounds it so a stale rule (or a clock jump) can't
    flood the log; anything beyond the cap is reported, never silently
    dropped.
    """
    today = date.today()
    state = self.load_state(self._default_state())
    rules = state.get("recurring", [])
    logged: list[dict] = []
    capped: list[str] = []

    for rule in rules:
        if not rule.get("enabled"):
            continue

        # "700 rent weekly" -> amount 700, description "rent"
        parts = str(rule.get("text", "")).strip().split(None, 1)
        try:
            amount = float(parts[0])
        except (ValueError, IndexError):
            continue
        desc = parts[1] if len(parts) > 1 else "Recurring"
        cat = _detect_category(desc)
        cat = "Recurring" if cat == "Other" else cat

        try:
            next_due = date.fromisoformat(rule["next_due"])
        except (KeyError, ValueError):
            continue

        fired = 0
        while next_due <= today and fired < self.MAX_CATCHUP_PERIODS:
            # Date the entry to the period it covers, not to "today".
            entry = await self.add(amount, desc, cat, entry_date=next_due.isoformat())
            logged.append(entry)
            rule["last_logged"] = next_due.isoformat()
            next_due = self._advance_due(next_due, rule.get("frequency", "monthly"))
            fired += 1

        rule["next_due"] = next_due.isoformat()
        if fired >= self.MAX_CATCHUP_PERIODS and next_due <= today:
            capped.append(desc)

    self.save_state(state)
    if logged:
        await self.emit("expense:recurring_posted",
                        {"count": len(logged), "trigger": trigger})
    result = {"logged": logged, "count": len(logged), "trigger": trigger}
    if capped:
        # Loud, never silent — a capped rule means entries are still missing.
        result["capped"] = capped
        self.log_activity({"event": "recurring_capped", "rules": capped})
    return result


@staticmethod
def _advance_due(due: date, frequency: str) -> date:
    """Next occurrence after ``due``. Month-end safe (31 Jan -> 28/29 Feb)."""
    if frequency == "weekly":
        return due + timedelta(days=7)
    if frequency == "fortnightly":
        return due + timedelta(days=14)
    if frequency == "yearly":
        try:
            return due.replace(year=due.year + 1)
        except ValueError:            # 29 Feb -> non-leap year
            return due.replace(year=due.year + 1, day=28)
    # monthly (default)
    m = due.month % 12 + 1
    y = due.year + (1 if due.month == 12 else 0)
    day = due.day
    while day > 0:                    # clamp 31 -> 30 -> 29 -> 28
        try:
            return due.replace(year=y, month=m, day=day)
        except ValueError:
            day -= 1
    return due + timedelta(days=30)   # unreachable in practice
