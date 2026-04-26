"""Calendar App — unified schedule view across EmptyOS."""

from __future__ import annotations
import datetime
from emptyos.sdk import BaseApp, web_route

class CalendarApp(BaseApp):

    async def setup(self):
        pass

    async def get_agenda(self, target_date: str) -> list[dict]:
        """Aggregate tasks and project deadlines for a specific date."""
        agenda = []
        
        # 1. Fetch tasks due on target_date
        try:
            tasks = await self.call_app("task", "list_tasks", overdue_only=False, done=False)
            if tasks:
                for t in tasks:
                    if t.due and t.due.startswith(target_date):
                        agenda.append({
                            "time": "All Day",
                            "title": t.text,
                            "type": "task",
                            "source": "task app"
                        })
        except Exception as e:
            print(f"[Calendar] Failed to fetch tasks: {e}")

        # TODO: In the future, we will parse external .ics feeds here and append them to `agenda`
        
        return agenda

    # ------------------------------------------------------------------
    # Hub Panel contribution
    # ------------------------------------------------------------------
    async def panel_agenda(self) -> list[dict] | None:
        """Contributes a Hub Panel showing today's agenda."""
        today = datetime.date.today().isoformat()
        agenda = await self.get_agenda(today)
        
        if not agenda:
            return None
            
        out = []
        for item in agenda[:5]:
            out.append({
                "text": item["title"],
                "tag": item["time"],
                "tag_tone": "blue",
                "href": "/calendar/"
            })
        return out

    # ------------------------------------------------------------------
    # Voice Assistant contribution
    # ------------------------------------------------------------------
    async def assistant_context(self) -> str | None:
        """Tells Aura about today's agenda."""
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
