"""Projects hub-panel + slot contributions.

Thin functions bound onto ProjectsApp as class attributes (same pattern as
``extended.py`` / ``operations.py`` / ``dev_features.py``). Separated so app.py
stays under the monolith threshold and the hub glance-layer stays easy to scan
in one file.
"""

from __future__ import annotations


# ------------------------------------------------------------------
# Hub panel contributions — manifest: [[contributes.hub.panel]]
# ------------------------------------------------------------------

async def panel_upcoming_deadlines(self):
    """Deadlines in the next ~14 days (+ up to 7 days overdue) for the hub list."""
    deadlines = await self.get_deadlines(days=14, overdue_days=7)
    if not deadlines:
        return None
    return [
        {
            "days": d["days_left"],
            "name": d["name"],
            "date": d["deadline"],
            "href": f"/projects/#{d['id']}",
        }
        for d in deadlines[:6]
    ]


async def panel_projects_pipeline(self):
    """Dashboard tile: count of active projects with work pending."""
    projects = await self.list_projects()
    active = [
        p for p in projects
        if p.get("status") == "active" and p.get("open_tasks", 0) > 0
    ]
    if not active:
        return None
    return self.stat_tile("📋", len(active), "in pipeline", "/projects/")


async def panel_project_countdowns(self):
    """Project deadlines as small countdown tiles (far-out deadlines only)."""
    deadlines = await self.get_deadlines(days=365, overdue_days=0)
    if not deadlines:
        return None
    far = [d for d in deadlines if d["days_left"] >= 30]
    if not far:
        return None
    return [
        {"name": d["name"], "days": d["days_left"], "direction": "down"}
        for d in far[:6]
    ]


# ------------------------------------------------------------------
# Hub slot contributions — manifest: [[contributes.hub.<slot>]]
# ------------------------------------------------------------------

async def slot_needs_attention(self):
    """Overdue project deadlines."""
    projects = await self.list_projects()
    out = []
    for p in projects:
        if not p.get("overdue"):
            continue
        days = abs(p.get("days_until_deadline") or 0)
        out.append({
            "title": p["name"],
            "subtitle": f"deadline overdue {days}d",
            "href": f"/projects/#{p['id']}",
            "badge": "overdue",
            "priority": min(100, 15 + days),
        })
    return out


async def slot_today(self):
    """Projects with a deadline today."""
    projects = await self.list_projects()
    out = []
    for p in projects:
        if p.get("days_until_deadline") == 0:
            out.append({
                "title": p["name"],
                "subtitle": "deadline today",
                "href": f"/projects/#{p['id']}",
                "badge": "due-today",
                "priority": 8,
            })
    return out


async def slot_resume(self):
    """Active projects with open tasks, ordered by freshness (mtime)."""
    projects = await self.list_projects()
    active = [
        p for p in projects
        if p.get("status") == "active" and p.get("open_tasks", 0) > 0
    ]
    active.sort(key=lambda p: p.get("stale_days", 999))
    out = []
    for p in active[:3]:
        days = p.get("stale_days", 0)
        when = "today" if days == 0 else f"{days}d ago"
        out.append({
            "title": p["name"],
            "subtitle": f"{p['open_tasks']} open · touched {when}",
            "href": f"/projects/#{p['id']}",
            "badge": None,
            "priority": max(0, 10 - days),
        })
    return out
