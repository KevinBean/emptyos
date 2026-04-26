"""Dashboard — aggregated life view.

Pulls data from other apps via call_app() and synthesizes
an overview with optional AI narrative. This is a pure
composition app — it creates nothing, only reads from others.
"""

from __future__ import annotations

from datetime import date, timedelta

from emptyos.sdk import BaseApp, cli_command, web_route

NARRATIVE_SYSTEM = """You are a concise personal briefing writer. Given structured data about someone's day, write a 3-4 sentence status narrative.

## Voice
- Direct and honest — like a trusted advisor who respects your time.
- Lead with the most important signal (overdue tasks? streak broken? good momentum?).
- Use concrete numbers, not vague encouragement ("3 of 5 frogs done" not "making progress").

## Structure
1. Open with the headline: what's the single most notable thing today?
2. Give context: how does today compare to the recent trend?
3. Close with one forward-looking sentence about what to focus on.

## DO NOT:
- Start with "Good morning!" or "Here's your daily summary" — the user knows what this is.
- Use motivational clichés ("You've got this!", "Keep up the great work!").
- Mention data that's zero or missing — skip empty sections silently.
- List all sections mechanically. Synthesize — find the story in the numbers."""


class DashboardApp(BaseApp):

    async def generate(self) -> dict:
        """Generate the full dashboard by calling other apps."""
        sections = {}

        # Frogs from briefing
        try:
            sections["frogs"] = await self.call_app("briefing", "_check_frogs")
        except Exception:
            sections["frogs"] = {}

        # Open tasks
        try:
            tasks = await self.call_app("task", "list_tasks")
            sections["tasks"] = {
                "total": len(tasks),
                "overdue": len([t for t in tasks if t.due and t.due < str(date.today())]),
                "items": [t.to_dict() for t in tasks[:10]],
            }
        except Exception:
            sections["tasks"] = {"total": 0, "overdue": 0, "items": []}

        # Expense this month
        try:
            sections["expense"] = await self.call_app("expense", "summary")
        except Exception:
            sections["expense"] = {"total": 0, "count": 0}

        # Contacts count
        try:
            contacts = await self.call_app("contacts", "list_contacts")
            sections["contacts"] = {"total": len(contacts)}
        except Exception:
            sections["contacts"] = {"total": 0}

        # Recent journal
        try:
            recent = await self.call_app("journal", "_recent_days", n=7)
            sections["journal"] = {
                "days_with_entries": len([d for d in recent if d["entries"] > 0]),
                "total_entries": sum(d["entries"] for d in recent),
                "recent": recent[:5],
            }
        except Exception:
            sections["journal"] = {"days_with_entries": 0, "total_entries": 0, "recent": []}

        return {
            "date": date.today().isoformat(),
            "sections": sections,
        }

    async def narrative(self) -> str:
        """Generate an AI narrative summary of the dashboard."""
        data = await self.generate()
        s = data["sections"]

        frogs = s.get("frogs", {})
        frog_str = ", ".join(
            f"{k}: {'done' if v.get('done') else 'not done'}"
            for k, v in frogs.items()
        ) if frogs else "no data"

        tasks = s.get("tasks", {})
        expense = s.get("expense", {})
        journal = s.get("journal", {})

        user_msg = (
            f"Date: {data['date']}\n"
            f"Frogs: {frog_str}\n"
            f"Tasks: {tasks.get('total', 0)} open, {tasks.get('overdue', 0)} overdue\n"
            f"Expense this month: ${expense.get('total', 0):.2f} ({expense.get('count', 0)} entries)\n"
            f"Journal: {journal.get('days_with_entries', 0)} days journaled in last 7 days, "
            f"{journal.get('total_entries', 0)} entries\n"
            f"Contacts: {s.get('contacts', {}).get('total', 0)} people\n"
        )
        return await self.think(user_msg, system=NARRATIVE_SYSTEM, domain="text", temperature=0.5)

    @cli_command("dashboard", help="Life dashboard — aggregated view from all apps")
    async def cmd_dashboard(self, action: str = "show"):
        if action == "show":
            data = await self.generate()
            s = data["sections"]

            print(f"\n  Dashboard — {data['date']}")
            print()

            # Frogs
            frogs = s.get("frogs", {})
            done = sum(1 for f in frogs.values() if f.get("done"))
            print(f"  Frogs: {done}/{len(frogs)}")
            for name, f in frogs.items():
                icon = "[x]" if f.get("done") else "[ ]"
                print(f"    {icon} {name}: {f.get('detail', '')}")

            # Tasks
            tasks = s.get("tasks", {})
            print(f"\n  Tasks: {tasks.get('total', 0)} open, {tasks.get('overdue', 0)} overdue")

            # Expense
            exp = s.get("expense", {})
            print(f"  Expense: ${exp.get('total', 0):.2f} ({exp.get('count', 0)} entries)")

            # Journal
            j = s.get("journal", {})
            print(f"  Journal: {j.get('days_with_entries', 0)}/7 days, {j.get('total_entries', 0)} entries")

            # Contacts
            print(f"  Contacts: {s.get('contacts', {}).get('total', 0)} people")
            print()

        elif action == "narrative":
            text = await self.narrative()
            print(f"\n  {text}\n")

    @web_route("GET", "/api/dashboard")
    async def api_dashboard(self, request):
        data = await self.generate()
        await self.emit("dashboard:generated", {"date": data["date"]})
        return data

    @web_route("GET", "/api/narrative")
    async def api_narrative(self, request):
        text = await self.narrative()
        return {"narrative": text, "date": date.today().isoformat()}

    @web_route("GET", "/api/goals")
    async def api_goals(self, request):
        """Goal progress bars for key life areas."""
        today = date.today()
        items = []

        # Countdown-based goals from settings
        for cd in self.get_countdown_items():
            try:
                target = date.fromisoformat(cd["date"])
                direction = cd.get("direction", "down")
                if direction == "up":
                    days = (today - target).days
                    pct = min(95, round(days / 730 * 100))
                    items.append({"name": cd["label"], "progress": pct, "detail": f"{days} days waiting", "icon": "passport"})
                else:
                    days = (target - today).days
                    total_span = max(1, (target - date(2025, 1, 1)).days)
                    pct = round((1 - days / total_span) * 100)
                    items.append({"name": cd["label"], "progress": max(0, pct), "detail": f"{days} days left", "icon": "calendar"})
            except Exception:
                continue

        # Savings: try expense app for monthly total vs target
        savings_pct = 0
        savings_detail = "no data"
        try:
            expense = await self.call_app("expense", "summary")
            monthly_spend = expense.get("total", 0)
            savings_pct = max(0, min(100, round((1 - monthly_spend / 4000) * 100)))
            savings_detail = f"${monthly_spend:.0f} / $4000 budget"
        except Exception:
            pass

        # English: try english app
        english_pct = 0
        english_detail = "no data"
        try:
            level = await self.call_app("english", "get_level")
            total_h = level.get("total_hours", 0)
            english_pct = min(100, round(total_h / 200 * 100))
            english_detail = f"{total_h:.0f}h / 200h target"
        except Exception:
            pass

        # Career: qualitative from tracker
        career_pct = 40
        career_detail = "Qualitative assessment"
        try:
            tracker_goals = await self.call_app("tracker", "goals")
            for g in tracker_goals:
                if g["name"] == "Career":
                    career_pct = g["progress"]
                    career_detail = g["detail"]
                    break
        except Exception:
            pass

        items.extend([
            {"name": "Savings", "progress": savings_pct, "detail": savings_detail, "icon": "piggy-bank"},
            {"name": "English", "progress": english_pct, "detail": english_detail, "icon": "book-open"},
            {"name": "Career", "progress": career_pct, "detail": career_detail, "icon": "briefcase"},
        ])
        return items

    @web_route("GET", "/api/countdowns")
    async def api_countdowns(self, request):
        """Countdowns for key dates."""
        today = date.today()

        countdowns = []
        for cd in self.get_countdown_items():
            try:
                target = date.fromisoformat(cd["date"])
                direction = cd.get("direction", "down")
                entry = {
                    "name": cd["label"],
                    "target_date": cd["date"],
                    "direction": direction,
                }
                if direction == "up":
                    entry["days_elapsed"] = (today - target).days
                else:
                    entry["days_remaining"] = (target - today).days
                countdowns.append(entry)
            except Exception:
                continue

        # Try to find project deadlines from task app
        try:
            tasks = await self.call_app("task", "list_tasks")
            for t in tasks:
                td = t.to_dict() if hasattr(t, "to_dict") else t
                due = td.get("due", "")
                if due and due >= today.isoformat():
                    try:
                        due_date = date.fromisoformat(due)
                        days_left = (due_date - today).days
                        if days_left <= 90:  # Only show deadlines within 90 days
                            countdowns.append({
                                "name": td.get("text", "Task")[:50],
                                "target_date": due,
                                "days_remaining": days_left,
                                "direction": "down",
                            })
                    except ValueError:
                        pass
        except Exception:
            pass

        return countdowns

    @web_route("GET", "/api/wellness")
    async def api_wellness(self, request):
        """Aggregated wellness snapshot: mood, sleep, nutrition, meditation, focus."""
        data = {}
        try:
            data["mood"] = await self.call_app("healing", "trend", days=7)
        except Exception:
            data["mood"] = []
        try:
            data["sleep"] = await self.call_app("healing", "api_sleep_stats")
        except Exception:
            data["sleep"] = {}
        try:
            data["nutrition"] = await self.call_app("nutrition", "today_summary")
        except Exception:
            data["nutrition"] = {}
        try:
            data["meditation"] = await self.call_app("meditation", "stats")
        except Exception:
            data["meditation"] = {}
        try:
            data["focus"] = await self.call_app("focus", "today_stats")
        except Exception:
            data["focus"] = {}
        try:
            data["reader"] = await self.call_app("reader", "stats")
        except Exception:
            data["reader"] = {}
        try:
            data["dictionary"] = await self.call_app("dictionary", "api_stats")
        except Exception:
            data["dictionary"] = {}
        return data

    @web_route("GET", "/api/digest")
    async def api_digest(self, request):
        """Weekly digest — all key metrics for the past 7 days."""
        data = await self.generate()
        goals = []
        try:
            goals = await self.api_goals(request)
        except Exception:
            pass
        narrative = ""
        try:
            narrative = await self.narrative()
        except Exception:
            pass
        return {
            "date": date.today().isoformat(),
            "sections": data["sections"],
            "goals": goals,
            "narrative": narrative,
        }

    @web_route("GET", "/api/streaks")
    async def api_streaks(self, request):
        """All active streaks across apps."""
        streaks = []
        for app_id, label in [("journal", "Journal"), ("healing", "Mood"), ("focus", "Focus"),
                               ("meditation", "Meditation"), ("nutrition", "Nutrition"),
                               ("reader", "Reading")]:
            try:
                if app_id == "nutrition":
                    s = await self.call_app(app_id, "api_streak")
                else:
                    s = await self.call_app(app_id, "api_streak")
                streak_val = s.get("streak", 0) if isinstance(s, dict) else 0
                streaks.append({"app": app_id, "label": label, "streak": streak_val})
            except Exception:
                streaks.append({"app": app_id, "label": label, "streak": 0})
        return streaks

    @web_route("GET", "/api/month-compare")
    async def api_month_compare(self, request):
        """Compare this month vs last month for key metrics."""
        today = date.today()
        this_month = today.replace(day=1)
        last_month = (this_month - timedelta(days=1)).replace(day=1)

        result = {
            "this_month": today.strftime("%Y-%m"),
            "last_month": last_month.strftime("%Y-%m"),
            "metrics": [],
        }

        # Expenses comparison
        try:
            current_exp = await self.call_app("expense", "summary")
            current_total = current_exp.get("total", 0)
            current_count = current_exp.get("count", 0)

            # Try to get last month's data
            try:
                last_exp = await self.call_app("expense", "summary_month", month=last_month.strftime("%Y-%m"))
                last_total = last_exp.get("total", 0)
            except Exception:
                last_total = 0

            result["metrics"].append({
                "name": "Expenses",
                "current": round(current_total, 2),
                "previous": round(last_total, 2),
                "unit": "$",
                "direction": "lower_is_better",
            })
        except Exception:
            pass

        # Journal days comparison
        try:
            recent = await self.call_app("journal", "_recent_days", n=60)
            this_month_str = today.strftime("%Y-%m")
            last_month_str = last_month.strftime("%Y-%m")
            this_journal = sum(1 for d in recent if d.get("date", "").startswith(this_month_str) and d["entries"] > 0)
            last_journal = sum(1 for d in recent if d.get("date", "").startswith(last_month_str) and d["entries"] > 0)
            result["metrics"].append({
                "name": "Journal Days",
                "current": this_journal,
                "previous": last_journal,
                "unit": "days",
                "direction": "higher_is_better",
            })
        except Exception:
            pass

        # Tasks completed comparison
        try:
            tasks = await self.call_app("task", "list_tasks", include_done=True)
            this_month_str = today.strftime("%Y-%m")
            last_month_str = last_month.strftime("%Y-%m")
            this_done = 0
            last_done = 0
            for t in tasks:
                td = t.to_dict() if hasattr(t, "to_dict") else t
                completed = td.get("completed_date", "")
                if completed.startswith(this_month_str):
                    this_done += 1
                elif completed.startswith(last_month_str):
                    last_done += 1
            result["metrics"].append({
                "name": "Tasks Completed",
                "current": this_done,
                "previous": last_done,
                "unit": "tasks",
                "direction": "higher_is_better",
            })
        except Exception:
            pass

        return result
