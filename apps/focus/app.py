"""Focus — pomodoro timer with task integration.

Tracks focus sessions, suggests tasks to work on,
records completed sessions to app-local data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from emptyos.sdk import BaseApp, cli_command, load_json, save_json, web_route


FOCUS_PICK_SYSTEM = (
    "You are a focus coach. Given a short list of open tasks, name the "
    "single one the user should work on for the next pomodoro and give a "
    "one-clause reason. Output one sentence, no preamble.\n\n"
    "Do NOT:\n"
    "- Pick more than one task.\n"
    "- List or rank the alternatives.\n"
    "- Add productivity advice or motivational filler.\n"
    "- Quote the task verbatim — paraphrase it briefly."
)


class FocusApp(BaseApp):
    def _sessions_path(self):
        return self.data_dir / "sessions.json"

    def _load_sessions(self) -> list[dict]:
        return load_json(self._sessions_path(), [])

    def _save_session(self, session: dict):
        sessions = self._load_sessions()
        sessions.append(session)
        if len(sessions) > 500:
            sessions = sessions[-500:]
        save_json(self._sessions_path(), sessions)

    async def suggest_task(self) -> str:
        """Suggest what to focus on using LLM + task list."""
        try:
            tasks = await self.call_app("task", "list_tasks")
            task_text = "\n".join(
                f"- {t.text}" + (f" (due: {t.due})" if t.due else "") for t in tasks[:15]
            )
            if not task_text:
                return "No open tasks. Pick something meaningful to you."
            return await self.think(
                f"Tasks:\n{task_text}",
                system=FOCUS_PICK_SYSTEM,
                domain="text",
                temperature=0.4,
            )
        except Exception:
            return "Pick your most important task and start."

    async def complete_session(self, minutes: int = 25, task: str = "") -> dict:
        # Validate duration against server-tracked start time
        state = self.load_state({})
        active = state.pop("active_session", None)
        if active:
            self.save_state(state)
            if active.get("started_at"):
                try:
                    started = datetime.fromisoformat(active["started_at"])
                    actual_min = (datetime.now(UTC) - started).total_seconds() / 60
                    minutes = max(1, round(actual_min))
                except Exception:
                    pass
        session = {
            "date": date.today().isoformat(),
            "time": datetime.now(UTC).strftime("%H:%M"),
            "minutes": minutes,
            "task": task,
        }
        self._save_session(session)
        await self.emit("focus:completed", session)
        return session

    def today_stats(self) -> dict:
        sessions = self._load_sessions()
        today = date.today().isoformat()
        today_sessions = [s for s in sessions if s.get("date") == today]
        return {
            "date": today,
            "sessions": len(today_sessions),
            "total_minutes": sum(s.get("minutes", 25) for s in today_sessions),
        }

    # ── Hub panel contribution ──
    async def panel_focus_today(self) -> dict | None:
        """Dashboard tile: focus sessions completed today + total minutes."""
        stats = self.today_stats()
        n = stats.get("sessions", 0)
        if not n:
            return None
        mins = stats.get("total_minutes", 0)
        return self.stat_tile("🎯", str(n), f"{mins}m today" if mins else "today", "/focus/")

    # ── Voice Assistant contribution ──
    async def assistant_context(self) -> str | None:
        stats = self.today_stats()
        n = stats.get("sessions", 0)
        mins = stats.get("total_minutes", 0)
        if n == 0:
            return "You haven't focused today yet — ready to start a session?"
        return (
            f"You've completed {n} focus session"
            + ("s" if n != 1 else "")
            + f" today, {mins} minutes total."
        )

    @cli_command("focus", help="Pomodoro focus timer")
    async def cmd_focus(self, action: str = "suggest", minutes: str = "25", task: str = ""):
        if action == "suggest":
            suggestion = await self.suggest_task()
            print(f"\n  Focus on: {suggestion}\n")
        elif action == "done":
            session = await self.complete_session(int(minutes), task)
            stats = self.today_stats()
            print(f"  Session logged: {session['minutes']}min")
            print(f"  Today: {stats['sessions']} sessions, {stats['total_minutes']}min total")
        elif action == "stats":
            stats = self.today_stats()
            print(f"\n  Today: {stats['sessions']} sessions, {stats['total_minutes']}min")
        else:
            print("Usage: eos focus [suggest|done|stats] [--minutes 25] [--task 'description']")

    @web_route("GET", "/api/suggest")
    async def api_suggest(self, request):
        return {"suggestion": await self.suggest_task()}

    @web_route("POST", "/api/start")
    async def api_start_session(self, request):
        """Record session start time for server-side duration validation."""
        data = await request.json()
        state = self.load_state({})
        state["active_session"] = {
            "started_at": datetime.now(UTC).isoformat(),
            "minutes": int(data.get("minutes", 25)),
            "task": data.get("task", ""),
        }
        self.save_state(state)
        return {"ok": True}

    @web_route("POST", "/api/complete")
    async def api_complete(self, request):
        data = await request.json()
        return await self.complete_session(data.get("minutes", 25), data.get("task", ""))

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        return self.today_stats()

    @web_route("GET", "/api/history")
    async def api_history(self, request):
        limit = int(request.query_params.get("limit", "50"))
        return self._load_sessions()[-limit:]

    @web_route("GET", "/api/heatmap")
    async def api_heatmap(self, request):
        sessions = self._load_sessions()
        daily = {}
        for s in sessions:
            d = s.get("date", "")
            daily[d] = daily.get(d, 0) + 1
        return daily

    @web_route("GET", "/api/goal")
    async def api_get_goal(self, request):
        state = self.load_state({"daily_goal": 4})
        return {"daily_goal": state.get("daily_goal", 4)}

    @web_route("POST", "/api/goal")
    async def api_set_goal(self, request):
        data = await request.json()
        state = self.load_state({"daily_goal": 4})
        state["daily_goal"] = int(data.get("daily_goal", 4))
        self.save_state(state)
        return {"daily_goal": state["daily_goal"]}

    @web_route("GET", "/api/vault-report")
    async def api_vault_report(self, request):
        """Write weekly focus report to vault."""
        from datetime import timedelta

        today = date.today()
        sessions = self._load_sessions()
        week_sessions = [
            s for s in sessions if s.get("date", "") >= (today - timedelta(days=7)).isoformat()
        ]
        total_min = sum(s.get("minutes", 25) for s in week_sessions)
        tasks = {}
        for s in week_sessions:
            t = s.get("task", "unspecified") or "unspecified"
            tasks[t] = tasks.get(t, 0) + 1
        task_lines = "\n".join(
            f"- {t}: {c} sessions" for t, c in sorted(tasks.items(), key=lambda x: -x[1])
        )
        content = (
            f"---\ndate: {today.isoformat()}\ntype: focus-report\n---\n\n"
            f"## Focus Report — Week of {today.isoformat()}\n\n"
            f"- Sessions: {len(week_sessions)}\n- Total: {total_min} minutes\n\n"
            f"### Tasks\n{task_lines or '- No tasks logged'}\n"
        )
        path = f"40_Journal/Reports/{today.isoformat()}-focus.md"
        await self.write(path, content)
        return {"ok": True, "path": path, "sessions": len(week_sessions), "minutes": total_min}

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        state = self.load_state(
            {
                "daily_goal": 4,
                "work_min": 25,
                "break_min": 5,
                "long_break_min": 15,
                "long_break_every": 4,
            }
        )
        return state

    @web_route("GET", "/api/streak")
    async def api_streak(self, request):
        from datetime import timedelta

        sessions = self._load_sessions()
        dates = set(s.get("date", "") for s in sessions)
        streak = 0
        d = date.today()
        while d.isoformat() in dates:
            streak += 1
            d -= timedelta(days=1)
        return {"streak": streak, "total_sessions": len(sessions)}

    @web_route("GET", "/api/weekly")
    async def api_weekly(self, request):
        from datetime import timedelta

        sessions = self._load_sessions()
        today = date.today()
        days = []
        for i in range(6, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            day_sessions = [s for s in sessions if s.get("date") == d]
            days.append(
                {
                    "date": d,
                    "sessions": len(day_sessions),
                    "minutes": sum(s.get("minutes", 25) for s in day_sessions),
                }
            )
        return days

    @web_route("POST", "/api/config")
    async def api_set_config(self, request):
        data = await request.json()
        state = self.load_state(
            {
                "daily_goal": 4,
                "work_min": 25,
                "break_min": 5,
                "long_break_min": 15,
                "long_break_every": 4,
            }
        )
        for key in ["daily_goal", "work_min", "break_min", "long_break_min", "long_break_every"]:
            if key in data:
                state[key] = int(data[key])
        self.save_state(state)
        return state

    @web_route("POST", "/api/break")
    async def api_log_break(self, request):
        """Log a break taken between sessions."""
        data = await request.json()
        breaks = self._load_breaks()
        entry = {
            "date": date.today().isoformat(),
            "time": datetime.now(UTC).strftime("%H:%M"),
            "minutes": int(data.get("minutes", 5)),
            "type": data.get("type", "short"),  # short/long/walk
        }
        breaks.append(entry)
        self._save_breaks(breaks)
        return entry

    @web_route("GET", "/api/breaks")
    async def api_breaks(self, request):
        """Today's break history."""
        breaks = self._load_breaks()
        today = date.today().isoformat()
        return [b for b in breaks if b.get("date") == today]

    @web_route("POST", "/api/distraction")
    async def api_log_distraction(self, request):
        """Log a distraction during a focus session."""
        data = await request.json()
        distractions = self._load_distractions()
        entry = {
            "date": date.today().isoformat(),
            "time": datetime.now(UTC).strftime("%H:%M"),
            "type": data.get("type", "phone"),  # phone/chat/browsing/noise/other
            "note": data.get("note", ""),
        }
        distractions.append(entry)
        self._save_distractions(distractions)
        return entry

    @web_route("GET", "/api/distraction-stats")
    async def api_distraction_stats(self, request):
        """Distraction patterns — by type, frequency."""
        distractions = self._load_distractions()
        by_type: dict[str, int] = {}
        for d in distractions:
            t = d.get("type", "other")
            by_type[t] = by_type.get(t, 0) + 1
        return {"total": len(distractions), "by_type": by_type}

    def _load_breaks(self) -> list[dict]:
        return load_json(self.data_dir / "breaks.json", [])

    def _save_breaks(self, data):
        save_json(self.data_dir / "breaks.json", data)

    def _load_distractions(self) -> list[dict]:
        return load_json(self.data_dir / "distractions.json", [])

    def _save_distractions(self, data):
        save_json(self.data_dir / "distractions.json", data)
