"""Countdown — track days until/since events, like the Days Matter app.

One vault note per event under ``30_Resources/EmptyOS/countdown/``,
``tags: [countdown]``. Direction (counting up vs down) is never stored —
it falls out of the sign of the resolved day count (see ``logic.py``).
Recurring events (yearly/monthly/weekly) always resolve to their next
occurrence relative to today.
"""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import datetime, timezone

from emptyos.sdk import BaseApp, normalize_relative_date, web_route
from emptyos.sdk.utils import path_segment_error, safe_path_segment
from emptyos.sdk.vault_library import VaultLibrary

from . import logic

TAG = "countdown"
FOLDER = f"30_Resources/EmptyOS/{TAG}"


class CountdownLibrary(VaultLibrary):
    tag = TAG
    fields = {
        "title": str,
        "date": str,
        "repeat": str,
        "category": str,
        "icon": str,
        "color": str,
        "pinned": str,     # vault frontmatter is string-typed — "true"/"false"
        "archived": str,
        "created": str,
        "updated": str,
    }
    sort_key = "date"
    fallback_folder = FOLDER


def _slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower()).strip("-")
    return s or "event"


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _coerce_field(key: str, val):
    """Normalize one settable field to its canonical stored form. Shared by
    the PUT route and the boards ``set_field`` path so a value written from
    either surface is always one of the closed vocabularies in logic.py —
    an edit-and-resave (which repopulates a <select> from the stored value)
    can never silently drift to whichever option happens to sort first."""
    if key in ("pinned", "archived"):
        return "true" if _truthy(val) else "false"
    if key == "date":
        return str(val)[:10]
    if key == "repeat":
        val = str(val or "none").strip().lower()
        return val if val in logic.REPEATS else "none"
    if key == "color":
        val = str(val or "accent").strip().lower()
        return val if val in logic.COLORS else "accent"
    if key == "category":
        val = str(val or "other").strip().lower()
        return val if val in logic.CATEGORIES else "other"
    return val


def _resolve_filename(raw: str) -> tuple[str, dict | None]:
    """Validate a caller-supplied event filename (HTTP path param / boards
    ``id``) before it reaches vault filesystem lookups. Returns
    ``(filename, None)`` on success, or ``("", {"error": ...})`` on a
    traversal-shaped or otherwise unsafe segment."""
    stem = raw[:-3] if raw.endswith(".md") else raw
    safe = safe_path_segment(stem)
    if not safe:
        return "", {"error": path_segment_error(stem, "event id") or "invalid event id"}
    return f"{safe}.md", None


class CountdownApp(BaseApp):
    SETTABLE_FIELDS = {"title", "date", "repeat", "category", "icon", "color", "pinned", "archived"}

    async def setup(self):
        await super().setup()
        self.events = CountdownLibrary(self)

    # ------------------------------------------------------------------
    # Core verbs
    # ------------------------------------------------------------------

    async def add(self, title: str = "", date: str = "", repeat: str = "none",
                  category: str = "other", icon: str = "", color: str = "accent",
                  notes: str = "") -> dict:
        """Create a new countdown event. Canonical verb — callable via
        call_app (agent / mcp) and the api_create route below."""
        title = (title or "").strip()
        date = (date or "").strip()[:10]
        if not title:
            return {"error": "title is required"}
        if not date:
            return {"error": "date is required (YYYY-MM-DD)"}
        try:
            _date.fromisoformat(date)
        except ValueError:
            return {"error": f"invalid date: {date!r} (expected YYYY-MM-DD)"}

        repeat = _coerce_field("repeat", repeat)
        category = _coerce_field("category", category)
        icon = (icon or "").strip() or logic.icon_for(category)
        color = _coerce_field("color", color)

        slug = _slugify(title)
        filename = f"{slug}.md"
        n = 2
        while self.events.find_file(filename):
            filename = f"{slug}-{n}.md"
            n += 1

        now = datetime.now(timezone.utc).isoformat()
        fm = {
            "tags": [TAG],
            "title": title,
            "date": date,
            "repeat": repeat,
            "category": category,
            "icon": icon,
            "color": color,
            "pinned": "false",
            "archived": "false",
            "created": now,
            "updated": now,
        }
        self.vault_create_note(f"{FOLDER}/{filename}", fm, (notes or "").strip())
        await self.emit("countdown:created", {"file": filename, "title": title, "date": date})
        return {"ok": True, "file": filename, **fm}

    async def _resolved(self, item: dict) -> dict | None:
        st = logic.event_status(item.get("date", ""), item.get("repeat", "none"))
        if st.get("error"):
            return None
        return {
            **item,
            **st,
            "pinned": _truthy(item.get("pinned")),
            "archived": _truthy(item.get("archived")),
        }

    async def _all_events(self, include_archived: bool = False) -> list[dict]:
        out = []
        for it in self.events.list():
            resolved = await self._resolved(it)
            if resolved is None:
                continue
            if resolved["archived"] and not include_archived:
                continue
            out.append(resolved)
        return out

    def _sorted(self, items: list[dict]) -> list[dict]:
        sort_key = self.setting_or_config("countdown.default_sort", "proximity")
        if sort_key == "alphabetical":
            items.sort(key=lambda e: (not e["pinned"], (e.get("title") or "").lower()))
        elif sort_key == "created":
            items.sort(key=lambda e: (not e["pinned"], e.get("created", "")))
        else:
            items.sort(key=lambda e: (not e["pinned"], abs(e["days"] if e["days"] is not None else 10**9)))
        return items

    async def list_upcoming(self, limit: int = 10) -> list[dict]:
        """Non-archived events sorted for glancing at — pinned first, then
        closest in time. Cross-app-callable via call_app."""
        items = self._sorted(await self._all_events(include_archived=False))
        return items[:limit]

    # ------------------------------------------------------------------
    # Web API
    # ------------------------------------------------------------------

    @web_route("GET", "/api/events")
    async def api_list(self, request):
        include_archived = request.query_params.get("archived") == "1"
        items = self._sorted(await self._all_events(include_archived=include_archived))
        return {"events": items}

    @web_route("POST", "/api/events")
    async def api_create(self, request):
        body = await request.json()
        return await self.add(
            title=body.get("title") or "", date=body.get("date") or "",
            repeat=body.get("repeat") or "none", category=body.get("category") or "other",
            icon=body.get("icon") or "", color=body.get("color") or "accent",
            notes=body.get("notes") or "",
        )

    @web_route("GET", "/api/events/{file}")
    async def api_get(self, request):
        filename, err = _resolve_filename(request.path_params.get("file", ""))
        if err:
            return err
        detail = self.events.detail(filename)
        if not detail:
            return {"error": "not found"}
        resolved = await self._resolved(detail)
        return resolved or {"error": "invalid date"}

    @web_route("PUT", "/api/events/{file}")
    async def api_update(self, request):
        filename, err = _resolve_filename(request.path_params.get("file", ""))
        if err:
            return err
        body = await request.json()
        patch = {key: _coerce_field(key, val) for key, val in body.items() if key in self.SETTABLE_FIELDS}
        if not patch:
            return {"error": "nothing to update"}
        patch["updated"] = datetime.now(timezone.utc).isoformat()
        res = self.events.update(filename, patch)
        if res.get("error"):
            return res
        await self.emit("countdown:updated", {"file": filename, "fields": list(patch.keys())})
        return {"ok": True}

    @web_route("DELETE", "/api/events/{file}")
    async def api_delete(self, request):
        filename, err = _resolve_filename(request.path_params.get("file", ""))
        if err:
            return err
        path = self.events.find_file(filename)
        if not path:
            return {"error": "not found"}
        rel = self.vault_rel(path)
        path.unlink()
        if rel:
            self.vault_force_index(rel)
        await self.emit("countdown:deleted", {"file": filename})
        return {"ok": True}

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        all_events = await self._all_events(include_archived=True)
        upcoming = [e for e in all_events if not e["archived"] and e["days"] is not None and e["days"] >= 0]
        return {
            "total": len(all_events),
            "upcoming": len(upcoming),
            "pinned": sum(1 for e in all_events if e["pinned"] and not e["archived"]),
            "archived": sum(1 for e in all_events if e["archived"]),
        }

    # ------------------------------------------------------------------
    # Hub panel
    # ------------------------------------------------------------------

    async def panel_upcoming(self) -> list[dict] | None:
        items = await self.list_upcoming(limit=6)
        if not items:
            return None
        return [
            {
                "label": (f"{e['icon']} " if e.get("icon") else "") + e.get("title", ""),
                "days": e["days"],
                "date": e.get("target_date", ""),
                "href": "/countdown/",
            }
            for e in items
        ]

    # ------------------------------------------------------------------
    # Voice + assistant
    # ------------------------------------------------------------------

    async def voice_add(self, title: str = "", date: str = "", repeat: str = "none") -> dict:
        title = (title or "").strip()
        if not title:
            return {"say": "What should I count down to?"}
        if not date:
            return {"say": f"What date is {title}? For example, tomorrow or a specific date."}
        due_iso = normalize_relative_date(date)
        if not due_iso:
            return {"say": f"I couldn't parse the date '{date}' — try a specific date or 'tomorrow'."}
        res = await self.add(title=title, date=due_iso, repeat=repeat)
        if res.get("error"):
            return {"say": f"Couldn't add that — {res['error']}"}
        st = logic.event_status(res["date"], res.get("repeat", "none"))
        days = st.get("days")
        when = f"{days} days" if (days or 0) >= 0 else f"{abs(days)} days ago"
        return {
            "say": f"Added {title} — {when}.",
            "link": {"text": "Open countdown", "href": "/countdown/"},
        }

    async def voice_list(self) -> dict:
        items = await self.list_upcoming(limit=8)
        if not items:
            return {"say": "You have no countdowns set up yet.",
                    "link": {"text": "Open countdown", "href": "/countdown/"}}
        rows = []
        for e in items:
            days = e["days"]
            tag = f"in {days}d" if days >= 0 else f"{abs(days)}d ago"
            rows.append({"text": e.get("title", ""), "tag": tag,
                         "tone": "urgent" if 0 <= days <= 3 else ""})
        say = f"You have {len(items)} countdown{'s' if len(items) != 1 else ''}."
        first = items[0]
        if first.get("days", 0) >= 0:
            say += f" Next up: {first.get('title', '')} in {first['days']} days."
        return {
            "say": say,
            "card": {"renderer": "task-list", "title": "Countdowns", "data": rows},
            "link": {"text": "Open countdown", "href": "/countdown/"},
        }

    # ------------------------------------------------------------------
    # Boards view-layer integration
    # ------------------------------------------------------------------

    async def list_all(self) -> list[dict]:
        return await self._all_events(include_archived=True)

    async def set_field(self, id: str, field: str, value) -> dict:
        if field not in self.SETTABLE_FIELDS:
            return {"error": f"field '{field}' not settable"}
        filename, err = _resolve_filename(id)
        if err:
            return err
        patch = {field: _coerce_field(field, value), "updated": datetime.now(timezone.utc).isoformat()}
        res = self.events.update(filename, patch)
        if res.get("error"):
            return res
        await self.emit("countdown:updated", {"file": filename, "fields": [field]})
        return {"ok": True}
