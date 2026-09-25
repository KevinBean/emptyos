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
from pathlib import Path

from fastapi.responses import FileResponse

from emptyos.sdk import BaseApp, normalize_relative_date, scheduled, web_route
from emptyos.sdk.ics_parse import parse_ics
from emptyos.sdk.utils import is_truthy, path_segment_error, read_upload_text, safe_path_segment, slugify
from emptyos.sdk.vault_library import VaultLibrary

from . import logic

TAG = "countdown"
FOLDER = f"30_Resources/EmptyOS/{TAG}"
MAX_ICS_BYTES = 2 * 1024 * 1024
MAX_ICS_EVENTS = 200  # a sane ceiling on one import

# ── Cover image (countdown-flat-notes-no-photo) ─────────────────────────
# No schema change — a "cover" is just the first image already embedded in
# the note's own body, either an Obsidian ![[wikilink]] embed (dropped in by
# drag-and-drop, the common case) or a plain markdown ![alt](path) image.
# Raster formats only — deliberately no SVG: an SVG served inline at
# image/svg+xml executes any embedded <script> if the cover route is ever
# opened directly (a same-origin, authenticated navigation), not just when
# used as a CSS background/<img> source. Raster bytes have no script model,
# so excluding SVG removes the whole risk class rather than mitigating it.
_COVER_EXT_RE = r"(?:png|jpe?g|gif|webp)"
_COVER_WIKILINK_RE = re.compile(r"!\[\[([^\]|]+?\." + _COVER_EXT_RE + r")(?:\|[^\]]*)?\]\]", re.IGNORECASE)
_COVER_MDLINK_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+\." + _COVER_EXT_RE + r")\)", re.IGNORECASE)
_COVER_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
}


def _first_cover_ref(body: str) -> str:
    """First embedded-image reference in a note body, or "" if none."""
    if not body:
        return ""
    m = _COVER_WIKILINK_RE.search(body) or _COVER_MDLINK_RE.search(body)
    return m.group(1).strip() if m else ""


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
        "remind_days_before": str,  # "" = no reminder, else a non-negative int
        "created": str,
        "updated": str,
    }
    sort_key = "date"
    fallback_folder = FOLDER


def _slugify(title: str) -> str:
    return slugify(title, max_len=None, fallback="event")


def _coerce_field(key: str, val):
    """Normalize one settable field to its canonical stored form. Shared by
    the PUT route and the boards ``set_field`` path so a value written from
    either surface is always one of the closed vocabularies in logic.py —
    an edit-and-resave (which repopulates a <select> from the stored value)
    can never silently drift to whichever option happens to sort first."""
    if key in ("pinned", "archived"):
        return "true" if is_truthy(val) else "false"
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
    if key == "remind_days_before":
        val = str(val or "").strip()
        if not val:
            return ""
        try:
            n = int(val)
        except ValueError:
            return ""
        return str(n) if n >= 0 else ""
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
    SETTABLE_FIELDS = {
        "title", "date", "repeat", "category", "icon", "color", "pinned", "archived",
        "remind_days_before",
    }

    async def setup(self):
        await super().setup()
        self.events = CountdownLibrary(self)
        self._cover_cache: dict[str, Path | None] = {}

    # ------------------------------------------------------------------
    # Core verbs
    # ------------------------------------------------------------------

    async def add(self, title: str = "", date: str = "", repeat: str = "none",
                  category: str = "other", icon: str = "", color: str = "accent",
                  notes: str = "", remind_days_before: str = "") -> dict:
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
            "remind_days_before": _coerce_field("remind_days_before", remind_days_before),
            "created": now,
            "updated": now,
        }
        self.vault_create_note(f"{FOLDER}/{filename}", fm, (notes or "").strip())
        await self.emit("countdown:created", {"file": filename, "title": title, "date": date})
        return {"ok": True, "file": filename, **fm}

    def _resolve_cover_path(self, ref: str) -> Path | None:
        """Resolve an embedded-image reference to a real file under the
        vault. A path-shaped ref (contains a slash — the common markdown
        ``![]()`` case) resolves directly and is containment-checked. A
        bare filename (the common ``![[wikilink]]`` embed case — the
        embed records no folder, the vault viewer resolves it by name) is
        found via a one-time vault-wide search, cached for the life of this
        app instance since a countdown roster is small and rarely changes."""
        if not ref or not self.vault_root:
            return None
        vault_root = self.vault_root.resolve()
        if "/" in ref or "\\" in ref:
            candidate = (vault_root / ref.lstrip("/\\")).resolve()
            try:
                candidate.relative_to(vault_root)
            except ValueError:
                return None
            return candidate if candidate.is_file() else None
        if ref not in self._cover_cache:
            self._cover_cache[ref] = next(vault_root.rglob(ref), None)
        return self._cover_cache[ref]

    async def _resolved(self, item: dict) -> dict | None:
        st = logic.event_status(item.get("date", ""), item.get("repeat", "none"))
        if st.get("error"):
            return None
        cover_ref = ""
        rel = item.get("path") or ""
        if rel and self.vault_root:
            try:
                cover_ref = _first_cover_ref((self.vault_root / rel).read_text(encoding="utf-8"))
            except OSError:
                cover_ref = ""
        return {
            **item,
            **st,
            "pinned": is_truthy(item.get("pinned")),
            "archived": is_truthy(item.get("archived")),
            "has_cover": bool(cover_ref and self._resolve_cover_path(cover_ref)),
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
            notes=body.get("notes") or "", remind_days_before=body.get("remind_days_before") or "",
        )

    # ── Calendar (.ics) import (countdown-no-calendar-import) ──────────
    async def _existing_identity(self) -> set[tuple[str, str]]:
        """(title-casefold, date) pairs already in the roster, for
        duplicate flagging on import."""
        out = set()
        for item in self.events.list():
            title = (item.get("title") or "").strip().casefold()
            date = (item.get("date") or "").strip()[:10]
            if title and date:
                out.add((title, date))
        return out

    @web_route("POST", "/api/import-ics/preview")
    async def api_import_ics_preview(self, request):
        """Parse an .ics file into candidate countdown events (title, date)
        and flag ones already in the roster. Writes nothing. No RRULE
        expansion — each VEVENT becomes one one-off countdown, matching
        the shared parser's scope (`emptyos.sdk.ics_parse`, also used by
        `calendar`'s own subscription feature).
        """
        ics_text, err = await read_upload_text(request, max_bytes=MAX_ICS_BYTES, json_key="ics_content")
        if err:
            return {"error": err}
        try:
            events = parse_ics(ics_text, source="import")
        except Exception as e:  # noqa: BLE001 — malformed file, not a 500
            return {"error": f"could not parse .ics file: {e}"}
        if not events:
            return {"error": "no events found in file"}

        warnings = []
        if len(events) > MAX_ICS_EVENTS:
            warnings.append(f"file has {len(events)} events — only the first {MAX_ICS_EVENTS} will be imported")
            events = events[:MAX_ICS_EVENTS]

        existing = await self._existing_identity()
        rows = []
        new_count = 0
        for e in events:
            title, date = e.get("title", ""), e.get("date", "")
            duplicate = (title.casefold(), date) in existing
            if not duplicate:
                new_count += 1
            rows.append({"title": title, "date": date, "duplicate": duplicate})

        return {
            "rows": rows,
            "warnings": warnings,
            "summary": {"total": len(rows), "new": new_count, "duplicate": len(rows) - new_count},
        }

    @web_route("POST", "/api/import-ics/confirm")
    async def api_import_ics_confirm(self, request):
        """Write the reviewed rows through the existing `add()` verb. Body:
        {rows: [{title, date}]}. Re-checks duplicates against the current
        roster, so a stale confirm can't double-import."""
        body = await request.json()
        rows = body.get("rows") or []
        if not isinstance(rows, list) or not rows:
            return {"error": "rows required"}
        if len(rows) > MAX_ICS_EVENTS:
            return {"error": "too many rows"}

        existing = await self._existing_identity()
        imported = 0
        skipped = 0
        seen: set[tuple[str, str]] = set()
        for r in rows:
            if not isinstance(r, dict):
                skipped += 1
                continue
            title = str(r.get("title", "")).strip()
            date = str(r.get("date", "")).strip()[:10]
            if not title or not date:
                skipped += 1
                continue
            key = (title.casefold(), date)
            if key in existing or key in seen:
                skipped += 1
                continue
            res = await self.add(title=title, date=date)
            if res.get("error"):
                skipped += 1
                continue
            seen.add(key)
            imported += 1
        return {"ok": True, "imported": imported, "skipped": skipped}

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

    @web_route("GET", "/api/events/{file}/cover")
    async def api_cover(self, request):
        filename, err = _resolve_filename(request.path_params.get("file", ""))
        if err:
            return err
        detail = self.events.detail(filename)
        if not detail:
            return {"error": "not found"}
        ref = _first_cover_ref(detail.get("body", ""))
        full = self._resolve_cover_path(ref) if ref else None
        if not full:
            return {"error": "no cover image"}
        mime = _COVER_MIME.get(full.suffix.lower().lstrip("."))
        if not mime:
            return {"error": "unsupported image type"}
        return FileResponse(str(full), media_type=mime)

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
    # Pre-event reminders — routes through the shared proactive_notify gate
    # (quiet-hours + daily-cap + dedup) rather than a bespoke push path.
    # Found dark during the 2026-08 gap-analysis pass: every countdown app
    # in the market leads with "notify me N days before"; this one only
    # ever surfaced on-demand (open the page or glance at the hub tile).
    # ------------------------------------------------------------------

    @scheduled("0 8 * * *", id="countdown-reminders")
    async def _check_reminders(self):
        for item in await self._all_events(include_archived=False):
            raw = (item.get("remind_days_before") or "").strip()
            if not raw:
                continue
            try:
                lead = int(raw)
            except ValueError:
                continue
            days = item.get("days")
            if days is None or days != lead:
                continue
            title = item.get("title", "this event")
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            await self.proactive_notify(
                "countdown",
                f"{title} is {when}.",
                dedup_key=f"countdown:{item.get('title')}:{item.get('target_date', '')}",
                link={"text": "Open countdown", "href": "/countdown/"},
            )

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
