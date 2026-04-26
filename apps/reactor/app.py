"""Reactor — event chain reactions.

Listens to events across the system and triggers actions.
This is the system's "metabolism" — incoming events get processed,
routed, and acted upon automatically.

Reactions are simple rules, not a pipeline engine.
Each reaction: event type → condition → action.

Handlers live in reactions_*.py mixin modules, grouped by domain:
- reactions_life.py        — body, mood, habits, money, places, people
- reactions_creative.py    — music, podcast, studio, fiction, 3D
- reactions_learning.py    — speaking, shadowing, interview, reader, dictionary
- reactions_work.py        — tasks, projects, jobs, briefings, assistant
- reactions_system.py      — vault, git, staff, integrity, reflect, publish, billing
"""

from __future__ import annotations

from datetime import date

from emptyos.sdk import BaseApp, cli_command, web_route

from .reactions_creative import CreativeReactionsMixin
from .reactions_learning import LearningReactionsMixin
from .reactions_life import LifeReactionsMixin
from .reactions_system import SystemReactionsMixin
from .reactions_work import WorkReactionsMixin


class ReactorApp(
    LifeReactionsMixin,
    CreativeReactionsMixin,
    LearningReactionsMixin,
    WorkReactionsMixin,
    SystemReactionsMixin,
    BaseApp,
):

    def _log_action(self, event_type: str, action: str):
        """Track reactor actions for observability."""
        state = self.load_state({"actions": []})
        entry = {
            "date": date.today().isoformat(),
            "event": event_type,
            "action": action,
        }
        state["actions"].append(entry)
        if len(state["actions"]) > 200:
            state["actions"] = state["actions"][-200:]
        self.save_state(state)

    async def _notify(self, message: str, priority: str = "info"):
        notif = self.service("notifications")
        if notif:
            await notif.send(message, priority=priority, source="reactor")

    async def _telegram(self, message: str):
        """Push notification to Telegram (phone)."""
        tg = self.service("telegram")
        if tg:
            try:
                await tg.send(message)
            except Exception:
                pass

    async def _journal_ripple(self, emoji: str, text: str, dim: str = ""):
        """Write an activity summary to the daily journal note.

        Optional `dim` appends a dimension hashtag (e.g. "social", "emotional")
        so the wellbeing wheel picks up the signal even when the ripple text
        doesn't contain alias vocabulary. Internal breadcrumb metadata — the
        wheel is a silent lens, not a user-facing feature.
        """
        line = f"{emoji} {text}"
        if dim:
            line = f"{line} #{dim}"
        try:
            await self.call_app("journal", "_add_entry",
                                d=date.today(), text=line, mood="okay")
        except Exception:
            pass  # journal app may not be loaded

    # ── Observability ──

    @cli_command("reactor", help="View event chain reactions")
    async def cmd_reactor(self, action: str = "log"):
        state = self.load_state({"actions": []})
        actions = state["actions"]
        if not actions:
            print("  No reactor actions yet")
            return
        for a in actions[-15:]:
            print(f"  {a.get('date', '')}  {a['event']:<25} {a['action']}")

    @web_route("GET", "/api/log")
    async def api_log(self, request):
        state = self.load_state({"actions": []})
        limit = int(request.query_params.get("limit", "50"))
        return state["actions"][-limit:]
