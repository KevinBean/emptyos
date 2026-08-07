"""worklog — portable export + the merge-import gate.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns the portable transfer document and the propose/preview/confirm
import that merges one back into canonical Markdown, including the receipt
shape and the dry-run path.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._load_day / self._all_days (reads),
self._ensure_daily / self._daily_lock (spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from emptyos.sdk import parse_frontmatter, set_frontmatter_field, web_route
from .portable import apply_merge_to_note, extract_days, merge_day, portable_document
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


# ─── Bind to WorklogApp class as ────────────────────────────────
#   portable_payload    = _importing.portable_payload
#   api_portable        = _importing.api_portable
#   import_portable     = _importing.import_portable
#   api_import_preview  = _importing.api_import_preview
#   api_import          = _importing.api_import
#   _import_request     = _importing._import_request
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# -- portable import/export --
async def portable_payload(self) -> dict:
    """Return the complete structured Work Log transfer document."""
    days = []
    for day in await self._all_days():
        days.append({
            "date": day["date"],
            "employer": day["employer"],
            **day["parsed"],
        })
    return portable_document(days)


@web_route("GET", "/api/portable")
async def api_portable(self, request):
    return await self.portable_payload()


async def import_portable(self, payload: dict, *, dry_run: bool = False) -> dict:
    """Merge a portable/offline document into canonical Markdown notes.

    ``dry_run`` computes the identical receipt without writing — the
    preview half of the propose/preview/confirm gate. Apply re-runs the
    merge against live state, so a day edited between preview and apply is
    re-checked rather than overwritten with what the preview saw.
    """
    days = extract_days(payload)
    receipt = {
        "ok": True,
        "preview": dry_run,
        "days_seen": len(days),
        "days_created": 0,
        "days_updated": 0,
        "days_unchanged": 0,
        "items_added": 0,
        "statuses_updated": 0,
        "timesheet_added": 0,
        "notes_added": 0,
        "prose_added": 0,
        "conflicts": [],
        "changes": [],
    }
    changed_dates: list[str] = []
    for incoming in days:
        d = date.fromisoformat(incoming["date"])
        async with self._daily_lock(d):
            existing_day = await self._load_day(self._daily_path(d))
            current = None
            if existing_day:
                current = {
                    "date": existing_day["date"],
                    "employer": existing_day["employer"],
                    **existing_day["parsed"],
                }
            merged, day_receipt = merge_day(current, incoming)
            for field in (
                "items_added", "statuses_updated", "timesheet_added",
                "notes_added", "prose_added",
            ):
                receipt[field] += day_receipt[field]
            if day_receipt["conflicts"]:
                receipt["conflicts"].append({
                    "date": incoming["date"],
                    "fields": day_receipt["conflicts"],
                })
            if not day_receipt["changed"]:
                receipt["days_unchanged"] += 1
                continue

            receipt["changes"].append({
                "date": incoming["date"],
                "created": day_receipt["created"],
                "items_added": day_receipt["items_added"],
                "statuses_updated": day_receipt["statuses_updated"],
                "timesheet_added": day_receipt["timesheet_added"],
                "notes_added": day_receipt["notes_added"],
                "prose_added": day_receipt["prose_added"],
            })
            if day_receipt["created"]:
                receipt["days_created"] += 1
            else:
                receipt["days_updated"] += 1
            if dry_run:
                continue

            if existing_day:
                content = await self.read(str(self._daily_path(d)))  # noqa: eos-rmw
            else:
                content = await self._ensure_daily(d, merged["employer"])
            current_employer = str(parse_frontmatter(content).get("employer") or "").strip()
            if merged["employer"] and not current_employer:
                content = set_frontmatter_field(content, "employer", merged["employer"])
            content = apply_merge_to_note(content, merged, day_receipt)
            await self.write(str(self._daily_path(d)), content)
            changed_dates.append(incoming["date"])

    if changed_dates:
        await self.emit("worklog:imported", {
            "dates": changed_dates,
            "days_created": receipt["days_created"],
            "days_updated": receipt["days_updated"],
        })
    return receipt


@web_route("POST", "/api/import/preview")
async def api_import_preview(self, request):
    """Dry-run the merge and report what would change. Writes nothing."""
    return await self._import_request(request, dry_run=True)


@web_route("POST", "/api/import")
async def api_import(self, request):
    return await self._import_request(request, dry_run=False)


async def _import_request(self, request, *, dry_run: bool) -> dict:
    data = await self.read_json(request)
    try:
        payload = data.get("document", data) if isinstance(data, dict) else data
        return await self.import_portable(payload, dry_run=dry_run)
    except ValueError as exc:
        return {"error": str(exc)}
