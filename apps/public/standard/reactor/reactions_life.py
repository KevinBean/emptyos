"""Life reactions — body, mood, habits, money, places, people."""

from __future__ import annotations

from datetime import date

from emptyos.runtime import wheel as _wheel
from emptyos.sdk import on_event


class LifeReactionsMixin:
    # Wellbeing-wheel cache invalidation. The wheel is a silent design lens;
    # keeping it fresh on real activity makes the briefing nudge reflect
    # today, not a three-minute-old signal.
    @on_event("capture:saved")
    async def on_capture_wheel_bust(self, event):
        _wheel.invalidate_cache()

    @on_event("journal:entry")
    async def on_journal_wheel_bust(self, event):
        _wheel.invalidate_cache()

    @on_event("habits:checked")
    async def on_habits_wheel_bust(self, event):
        _wheel.invalidate_cache()

    # A single expense at/above this lands a journal breadcrumb — big spends
    # are life events worth a line in the daily note.
    BIG_EXPENSE_AUD = 200

    # Detached: measured at 3.0s on the live daemon — it re-reads the whole
    # month's expenses to check the threshold, on every single add. Pure side
    # effect (journal ripple + a deduped alert); nothing reads its result.
    @on_event("expense:added", background=True)
    async def on_expense(self, event):
        """Budget awareness — journal big single expenses; alert on high months."""
        try:
            amount = float(event.data.get("amount") or 0)
            if amount >= self.BIG_EXPENSE_AUD:
                desc = (event.data.get("description") or "").strip()
                await self._journal_ripple(
                    "💸",
                    f"Big expense: ${amount:,.0f}" + (f" — {desc}" if desc else ""),
                    dim="financial",
                )
        except Exception:
            pass
        try:
            summary = await self.call_app(
                "expense", "list_expenses", month=date.today().strftime("%Y-%m")
            )
            total = sum(e.get("amount", 0) for e in summary)
            if total > 5000:
                msg = f"💰 Monthly expenses: ${total:.0f} — over $5,000 threshold"
                # Per-month dedup — this fires on EVERY expense:added once the
                # month is over threshold (same spam class as the billing
                # budget alert fixed in 35914a85); one alert per month is the
                # honest signal.
                await self._notify(
                    msg, priority="warning", kind="budget",
                    dedup_key=f"reactor-expense-month:{date.today().strftime('%Y-%m')}",
                )
                self._log_action("expense:added", f"budget alert: ${total:.0f}")
        except Exception:
            pass

    @on_event("expense:income-added")
    async def on_income_added(self, event):
        self._log_action("expense:income-added", "income recorded")

    @on_event("nutrition:logged")
    async def on_nutrition(self, event):
        self._log_action("nutrition:logged", "meal logged")

    @on_event("nutrition:weight-logged")
    async def on_weight_logged(self, event):
        weight = event.data.get("weight", "?")
        unit = event.data.get("unit", "kg")
        self._log_action("nutrition:weight-logged", f"{weight} {unit}")
        await self._journal_ripple("⚖️", f"Weight: {weight} {unit}")

    @on_event("healing:mood-logged")
    async def on_mood(self, event):
        mood = event.data.get("mood", "")
        self._log_action("healing:mood-logged", f"mood: {mood}")
        await self._journal_ripple("💭", f"Mood check-in: {mood}", dim="emotional")
        if mood == "bad":
            msg = "💙 Tough day. Remember: this feeling is temporary."
            # Shares the daily wellbeing dedup key with the low-mood journal
            # reaction (reactions_system.py) — one supportive nudge per day.
            await self._notify(
                msg, priority="info", kind="wellbeing",
                dedup_key=self._daily_dedup("wellbeing"),
            )

    @on_event("healing:dream-logged")
    async def on_dream(self, event):
        self._log_action("healing:dream-logged", f"dream id: {event.data.get('id', '')}")
        await self._journal_ripple("🌙", "Dream recorded", dim="emotional")

    @on_event("healing:grounding-logged")
    async def on_grounding(self, event):
        gtype = event.data.get("type", "")
        duration = event.data.get("duration", "")
        self._log_action("healing:grounding-logged", f"{gtype} {duration}min")
        await self._journal_ripple("🌿", f"Grounding: {gtype} {duration}min", dim="emotional")

    @on_event("sleep:logged")
    async def on_sleep_logged(self, event):
        hours = event.data.get("hours", 0)
        quality = event.data.get("quality", "")
        self._log_action("sleep:logged", f"{hours}h {quality}")
        await self._journal_ripple("😴", f"Sleep logged: {hours}h ({quality})")

    @on_event("workout:logged")
    async def on_workout_logged(self, event):
        duration = event.data.get("duration_min", 0)
        self._log_action("workout:logged", f"{duration}min workout")
        await self._journal_ripple("💪", f"Workout: {duration} minutes")

    @on_event("workout:body-logged")
    async def on_workout_body(self, event):
        self._log_action("workout:body-logged", "body metrics logged")

    @on_event("focus:completed")
    async def on_focus_done(self, event):
        self._log_action("focus:completed", "pomodoro done")
        await self._journal_ripple("🍅", "Pomodoro completed")

    @on_event("meditation:completed")
    async def on_meditation(self, event):
        self._log_action("meditation:completed", "session done")
        await self._journal_ripple("🧘", "Meditation session")

    @on_event("contacts:logged")
    async def on_contact_logged(self, event):
        name = event.data.get("name", "")[:30]
        self._log_action("contacts:logged", name)
        await self._journal_ripple("👤", f"Connected with {name}", dim="social")

    @on_event("contacts:created")
    async def on_contact_created(self, event):
        name = event.data.get("name", "")[:30]
        self._log_action("contacts:created", name)
        await self._journal_ripple("👤", f"New person in network: {name}", dim="social")

    @on_event("contacts:edited")
    async def on_contact_edited(self, event):
        self._log_action("contacts:edited", event.data.get("name", "")[:30])

    @on_event("people:created")
    async def on_people_created(self, event):
        name = event.data.get("name", "")[:30]
        self._log_action("people:created", name)
        await self._journal_ripple("👤", f"New person in network: {name}", dim="social")

    @on_event("people:updated")
    async def on_people_updated(self, event):
        self._log_action("people:updated", event.data.get("id", "")[:30])

    @on_event("people:archived")
    async def on_people_archived(self, event):
        self._log_action("people:archived", event.data.get("id", "")[:30])

    @on_event("people:logged")
    async def on_people_logged(self, event):
        name = event.data.get("name", "")[:30]
        self._log_action("people:logged", name)
        await self._journal_ripple("👤", f"Connected with {name}", dim="social")

    @on_event("places:created")
    async def on_place_created(self, event):
        self._log_action("places:created", event.data.get("name", "")[:30])

    @on_event("places:updated")
    async def on_place_updated(self, event):
        self._log_action("places:updated", event.data.get("file", "")[:30])

    @on_event("places:deleted")
    async def on_place_deleted(self, event):
        self._log_action("places:deleted", event.data.get("file", "")[:30])

    @on_event("places:visited")
    async def on_place_visited(self, event):
        self._log_action("places:visited", event.data.get("file", "")[:30])

    @on_event("weather:updated")
    async def on_weather_updated(self, event):
        temp = event.data.get("temp", "?")
        condition = event.data.get("condition", "")
        self._log_action("weather:updated", f"{temp}° {condition}")

    @on_event("divination:cast")
    async def on_divination_cast(self, event):
        self._log_action("divination:cast", f"hexagram #{event.data.get('number', '?')}")

    @on_event("reminders:created")
    async def on_reminder_created(self, event):
        self._log_action("reminders:created", event.data.get("text", "")[:40])

    @on_event("reminders:fired")
    async def on_reminder_fired(self, event):
        text = event.data.get("text", "")[:40]
        self._log_action("reminders:fired", f"reminder: {text}")
        # No nudge here: the reminders app already sent this one through the
        # gate before emitting, so a second _notify only duplicated it.

    @on_event("reminders:completed")
    async def on_reminder_completed(self, event):
        self._log_action("reminders:completed", event.data.get("text", "")[:40])

    # ── Countdowns (wired 2026-08-11: were declared-but-unheard) ──
    # A countdown is a dated commitment the user set by hand — the same shape
    # as a reminder, so it earns the same breadcrumb.

    @on_event("countdown:created")
    async def on_countdown_created(self, event):
        title = (event.data.get("title") or "")[:40]
        when = event.data.get("date") or "?"
        self._log_action("countdown:created", f"{title} -> {when}")

    @on_event("countdown:updated")
    async def on_countdown_updated(self, event):
        fields = ", ".join(event.data.get("fields") or [])[:40]
        self._log_action("countdown:updated", f"{event.data.get('file', '')}: {fields}")

    @on_event("countdown:deleted")
    async def on_countdown_deleted(self, event):
        self._log_action("countdown:deleted", str(event.data.get("file", ""))[:40])

    @on_event("habits:checked")
    async def on_habit_checked(self, event):
        habit = event.data.get("name", event.data.get("habit", ""))[:30]
        self._log_action("habits:checked", f"habit: {habit}")

    @on_event("habits:created")
    async def on_habit_created(self, event):
        self._log_action("habits:created", event.data.get("name", "")[:30])

    @on_event("recipes:created")
    async def on_recipe_created(self, event):
        self._log_action("recipes:created", event.data.get("name", "")[:30])

    @on_event("recipes:cooked")
    async def on_recipe_cooked(self, event):
        self._log_action("recipes:cooked", event.data.get("name", "")[:30])

    # ── Money artifacts (wired 2026-07-30: were declared-but-unheard) ──

    @on_event("expense:recurring_posted")
    async def on_recurring_posted(self, event):
        """A recurring expense auto-posted — money moved without the user acting."""
        desc = str(event.data.get("description") or event.data.get("name") or "")[:40]
        amount = event.data.get("amount")
        detail = f"{desc} ${amount}" if amount is not None else desc
        self._log_action("expense:recurring_posted", detail)
        await self._journal_ripple("🔁", f"Recurring expense posted: {detail}", dim="financial")

    @on_event("expense:report-saved")
    async def on_expense_report_saved(self, event):
        self._log_action("expense:report-saved", str(event.data.get("path") or "")[:60])

    @on_event("finance:report-saved")
    async def on_finance_report_saved(self, event):
        self._log_action("finance:report-saved", str(event.data.get("path") or "")[:60])

    @on_event("mail:routine_completed")
    async def on_mail_routine_completed(self, event):
        routine = event.data.get("routine") or event.data.get("name", "")
        self._log_action("mail:routine_completed", str(routine)[:50])
