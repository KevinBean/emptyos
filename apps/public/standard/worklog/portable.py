"""Portable Work Log document normalization and conflict-safe merging.

The portable shape is deliberately the same structured day data exposed by
``GET /worklog/api/day``.  Markdown remains the live source of truth; JSON is
only the explicit transport between EmptyOS and a standalone browser bundle.
This module is pure so both import validation and merge policy are cheap to
exercise without booting the kernel.
"""
from __future__ import annotations

import math
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

from .parser import (
    STATUS_EMOJI,
    append_section,
    render_notes,
    render_timesheet,
    render_work,
    replace_section,
)

FORMAT_ID = "emptyos.worklog"
FORMAT_VERSION = 1
OFFLINE_DAYS_KEY = "/worklog/offline/days"
MAX_DAYS = 10_000
MAX_ITEMS = 200_000
MAX_TEXT = 100_000


def _text(value: Any, *, limit: int = MAX_TEXT) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _status(value: Any) -> str | None:
    status = _text(value, limit=32).lower()
    return status if status in STATUS_EMOJI else None


def normalize_day(raw: Any) -> dict:
    """Validate and normalize one portable day, dropping unknown fields."""
    if not isinstance(raw, dict):
        raise ValueError("each worklog day must be an object")
    date_s = _text(raw.get("date"), limit=32)
    try:
        if len(date_s) != 10:
            raise ValueError
        date.fromisoformat(date_s)
    except ValueError as exc:
        raise ValueError(f"bad worklog date: {date_s or '(empty)'}") from exc

    projects: list[dict] = []
    item_count = 0
    for group in raw.get("projects") or []:
        if not isinstance(group, dict):
            continue
        project = _text(group.get("project"), limit=500) or "General"
        items: list[dict] = []
        for item in group.get("items") or []:
            if not isinstance(item, dict):
                continue
            text = _text(item.get("text"))
            if not text:
                continue
            items.append({"text": text, "status": _status(item.get("status"))})
            item_count += 1
            if item_count > MAX_ITEMS:
                raise ValueError(f"worklog import exceeds {MAX_ITEMS} items")
        if items:
            projects.append({"project": project, "items": items})

    timesheet: list[dict] = []
    for row in raw.get("timesheet") or []:
        if not isinstance(row, dict):
            continue
        try:
            hours = float(row.get("hours") or 0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(hours) or hours < 0 or hours > 24:
            continue
        timesheet.append({
            "project": _text(row.get("project"), limit=500) or "General",
            "hours": hours,
            "note": _text(row.get("note"), limit=5_000),
        })

    notes = [_text(note, limit=10_000) for note in (raw.get("notes") or [])]
    return {
        "date": date_s,
        "employer": _text(raw.get("employer"), limit=500),
        "plan": _text(raw.get("plan")),
        "update": _text(raw.get("update")),
        "projects": projects,
        "timesheet": timesheet,
        "notes": [note for note in notes if note],
    }


def portable_document(days: list[dict], *, exported_at: str = "") -> dict:
    """Build the stable transfer document from structured Work Log days."""
    normalized = [normalize_day(day) for day in days]
    normalized.sort(key=lambda day: day["date"], reverse=True)
    return {
        "format": FORMAT_ID,
        "version": FORMAT_VERSION,
        "exported_at": exported_at or datetime.now(UTC).isoformat(),
        "days": normalized,
    }


def extract_days(payload: Any) -> list[dict]:
    """Accept a portable document or the export shim's saved-data wrapper.

    The wrapper path lets a user take the JSON created by the standalone
    file's amber Offline pill and import it directly into the live app.
    """
    if not isinstance(payload, dict):
        raise ValueError("worklog import must be a JSON object")
    doc = payload
    if payload.get("app_id") == "worklog":
        kv = payload.get("kv") or {}
        stored = kv.get(OFFLINE_DAYS_KEY) if isinstance(kv, dict) else None
        export_data = payload.get("export_data") or {}
        if not isinstance(export_data, dict):
            export_data = {}
        candidate = stored if isinstance(stored, list) else export_data.get("days")
        doc = {"format": FORMAT_ID, "version": FORMAT_VERSION, "days": candidate or []}
    if doc.get("format") != FORMAT_ID:
        raise ValueError(f"expected format '{FORMAT_ID}'")
    if doc.get("version") != FORMAT_VERSION:
        raise ValueError(f"unsupported worklog format version: {doc.get('version')}")
    raw_days = doc.get("days")
    if not isinstance(raw_days, list):
        raise ValueError("worklog import is missing a days array")
    if len(raw_days) > MAX_DAYS:
        raise ValueError(f"worklog import exceeds {MAX_DAYS} days")
    days = [normalize_day(day) for day in raw_days]
    total_items = sum(len(group["items"]) for day in days for group in day["projects"])
    if total_items > MAX_ITEMS:
        raise ValueError(f"worklog import exceeds {MAX_ITEMS} items")
    seen: set[str] = set()
    for day in days:
        if day["date"] in seen:
            raise ValueError(f"duplicate worklog day: {day['date']}")
        seen.add(day["date"])
    return days


def merge_day(current: dict | None, incoming: dict) -> tuple[dict, dict]:
    """Merge one day without overwriting divergent prose or employer labels.

    Existing work items match by case-insensitive project + exact text.  Their
    status may advance from the standalone copy; new items append.  Plan and
    Update are filled only when blank, so a live edit made after export is
    never silently replaced.

    The receipt reports not just *how many* things changed but *which* — the
    exact timesheet rows and notes that were appended, and which prose fields
    were filled.  The caller needs that to touch only the sections that really
    changed: re-rendering an unchanged ``## Notes`` from parsed structure would
    drop any hand-written prose the parser doesn't model (see ``app.py``).
    """
    src = normalize_day(incoming)
    receipt = {
        "created": current is None,
        "changed": False,
        "items_added": 0,
        "statuses_updated": 0,
        "timesheet_added": 0,
        "notes_added": 0,
        "prose_added": 0,
        "work_changed": False,
        "prose_fields": [],
        "timesheet_new": [],
        "notes_new": [],
        "conflicts": [],
    }
    if current is None:
        receipt["changed"] = True
        receipt["items_added"] = sum(len(g["items"]) for g in src["projects"])
        receipt["timesheet_added"] = len(src["timesheet"])
        receipt["notes_added"] = len(src["notes"])
        receipt["prose_added"] = int(bool(src["plan"])) + int(bool(src["update"]))
        receipt["work_changed"] = bool(receipt["items_added"])
        receipt["prose_fields"] = [f for f in ("plan", "update") if src[f]]
        receipt["timesheet_new"] = deepcopy(src["timesheet"])
        receipt["notes_new"] = list(src["notes"])
        return deepcopy(src), receipt

    dst = normalize_day({**current, "date": src["date"]})
    if dst["employer"] and src["employer"] and dst["employer"].lower() != src["employer"].lower():
        receipt["conflicts"].append("employer")
        return dst, receipt
    if not dst["employer"] and src["employer"]:
        dst["employer"] = src["employer"]
        receipt["changed"] = True

    for field in ("plan", "update"):
        if not dst[field] and src[field]:
            dst[field] = src[field]
            receipt["prose_added"] += 1
            receipt["prose_fields"].append(field)
            receipt["changed"] = True
        elif dst[field] and src[field] and dst[field] != src[field]:
            receipt["conflicts"].append(field)

    groups = {group["project"].lower(): group for group in dst["projects"]}
    for incoming_group in src["projects"]:
        key = incoming_group["project"].lower()
        group = groups.get(key)
        if group is None:
            group = {"project": incoming_group["project"], "items": []}
            dst["projects"].append(group)
            groups[key] = group
        by_text = {item["text"]: item for item in group["items"]}
        for incoming_item in incoming_group["items"]:
            item = by_text.get(incoming_item["text"])
            if item is None:
                item = deepcopy(incoming_item)
                group["items"].append(item)
                by_text[item["text"]] = item
                receipt["items_added"] += 1
                receipt["changed"] = True
            elif item.get("status") != incoming_item.get("status"):
                item["status"] = incoming_item.get("status")
                receipt["statuses_updated"] += 1
                receipt["changed"] = True
    receipt["work_changed"] = bool(receipt["items_added"] or receipt["statuses_updated"])

    timesheet_keys = {
        (row["project"].lower(), row["hours"], row["note"]) for row in dst["timesheet"]
    }
    for row in src["timesheet"]:
        key = (row["project"].lower(), row["hours"], row["note"])
        if key not in timesheet_keys:
            dst["timesheet"].append(deepcopy(row))
            timesheet_keys.add(key)
            receipt["timesheet_new"].append(deepcopy(row))
            receipt["timesheet_added"] += 1
            receipt["changed"] = True

    note_set = set(dst["notes"])
    for note in src["notes"]:
        if note not in note_set:
            dst["notes"].append(note)
            note_set.add(note)
            receipt["notes_new"].append(note)
            receipt["notes_added"] += 1
            receipt["changed"] = True
    return dst, receipt


def apply_merge_to_note(content: str, merged: dict, receipt: dict) -> str:
    """Write a merged day back into its Markdown note, adding but never rewriting.

    A brand-new note has nothing hand-written to protect, so it gets the tidy
    canonical render. An *existing* note is only touched where the merge
    actually changed something, and Timesheet/Notes are appended rather than
    re-rendered: ``parse_day`` models neither free prose, nested bullets, nor
    timesheet rows whose project name contains a colon, so a parse/render round
    trip would silently delete all three from the user's own file.
    """
    if receipt["created"]:
        content = replace_section(content, "Plan", merged["plan"])
        content = replace_section(content, "Work", render_work(merged["projects"]))
        content = replace_section(content, "Timesheet", render_timesheet(merged["timesheet"]))
        content = replace_section(content, "Notes", render_notes(merged["notes"]))
        return replace_section(content, "Update", merged["update"])

    for field in receipt["prose_fields"]:
        content = replace_section(content, field.capitalize(), merged[field])
    if receipt["work_changed"]:
        content = replace_section(content, "Work", render_work(merged["projects"]))
    content = append_section(content, "Timesheet", render_timesheet(receipt["timesheet_new"]))
    return append_section(content, "Notes", render_notes(receipt["notes_new"]))
