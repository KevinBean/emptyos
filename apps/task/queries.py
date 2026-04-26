"""Pure aggregations over task dicts — no IO, no app state.

Inputs are the open/done lists produced by ``indexer.scan_vault`` /
``indexer.get_cached_or_scan``. Outputs are the shapes the web/hub/voice
surfaces consume verbatim.
"""

from __future__ import annotations

import re
from datetime import date


CONTEXT_KEYWORDS = {
    "career": ["career", "job", "resume", "interview", "application", "linkedin"],
    "health": ["health", "gym", "exercise", "zumba", "fitness", "medical", "doctor"],
    "english": ["english", "speaking", "vocabulary", "ielts", "pte", "pronunciation"],
}

_TAG_RE = re.compile(r"#([\w一-鿿]+)")
_RECUR_RE = re.compile(r"🔁\s*(daily|weekly|monthly|yearly|biweekly)")


def project_from_path(rel_path: str) -> str:
    """Tasks under ``10_Projects/<id>/...`` belong to ``<id>``; else ``inbox``."""
    if not rel_path:
        return "inbox"
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0].lower() in ("10_projects", "projects"):
        return parts[1]
    return "inbox"


def _days_until(due: str, today: date) -> int | None:
    if not due or len(due) < 10:
        return None
    try:
        return (date.fromisoformat(due[:10]) - today).days
    except (ValueError, TypeError):
        return None


def pulse_stats(open_tasks: list[dict], done_tasks: list[dict], today: date) -> list[dict]:
    today_str = today.isoformat()
    overdue = sum(1 for t in open_tasks if t.get("overdue_days", 0) > 0)
    due_today = sum(1 for t in open_tasks if (t.get("due") or "")[:10] == today_str)
    due_week = 0
    for t in open_tasks:
        d = _days_until(t.get("due") or "", today)
        if d is not None and 0 <= d <= 7:
            due_week += 1
    done_today = sum(
        1 for t in done_tasks if (t.get("done_date") or "")[:10] == today_str
    )
    return [
        {"value": overdue, "label": "Overdue", "tone": "red", "href": "/task/"},
        {"value": due_today, "label": "Today", "tone": "amber", "href": "/task/"},
        {"value": due_week, "label": "Week", "tone": "blue", "href": "/task/"},
        {"value": done_today, "label": "Done", "tone": "green", "href": "/task/"},
    ]


def todays_tasks_rows(open_tasks: list[dict], today: date, limit: int = 5) -> list[dict] | None:
    today_str = today.isoformat()

    def _bucket(t: dict) -> tuple:
        od = t.get("overdue_days", 0)
        if od > 0:
            return (0, -od)
        due = (t.get("due") or "")[:10]
        if due == today_str:
            return (1, -t.get("focus_score", 0))
        return (2, due or "9999")

    urgent = sorted(open_tasks, key=_bucket)[:limit]
    if not urgent:
        return None
    out = []
    for t in urgent:
        od = t.get("overdue_days", 0)
        due = (t.get("due") or "")[:10]
        if od > 0:
            tag, tag_tone = f"overdue {od}d", "overdue"
        elif due == today_str:
            tag, tag_tone = "today", "today"
        elif due:
            tag, tag_tone = due[5:], "week"
        else:
            tag, tag_tone = "", ""
        out.append({
            "text": t["text"],
            "done": t.get("done", False),
            "tag": tag,
            "tag_tone": tag_tone,
            "href": "/task/",
        })
    return out


def needs_attention_slot(open_tasks: list[dict], limit: int = 5) -> list[dict]:
    overdue = [t for t in open_tasks if t.get("overdue_days", 0) > 0]
    overdue.sort(key=lambda t: -t.get("overdue_days", 0))
    return [
        {
            "title": t["text"],
            "subtitle": f"overdue {t['overdue_days']}d",
            "href": "/task/",
            "badge": "overdue",
            "priority": min(100, 20 + t["overdue_days"]),
        }
        for t in overdue[:limit]
    ]


def due_today_slot(open_tasks: list[dict], today: date) -> list[dict]:
    today_str = today.isoformat()
    due_today = [
        t for t in open_tasks if (t.get("due") or "")[:10] == today_str
    ]
    due_today.sort(key=lambda t: -t.get("focus_score", 0))
    return [
        {
            "title": t["text"],
            "subtitle": None,
            "href": "/task/",
            "badge": "due-today",
            "priority": t.get("focus_score", 0),
        }
        for t in due_today
    ]


def group_by_context(open_tasks: list[dict]) -> dict[str, list]:
    groups: dict[str, list] = {"other": []}
    for t in open_tasks:
        text_lower = t.get("text", "").lower()
        matched = False
        for ctx, keywords in CONTEXT_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                groups.setdefault(ctx, []).append(t)
                matched = True
                break
        if not matched:
            groups["other"].append(t)
    return groups


def group_by_date(open_tasks: list[dict], done_tasks: list[dict]) -> dict[str, list[dict]]:
    by_date: dict[str, list[dict]] = {}
    for t in open_tasks:
        d = t.get("due", "")
        if d:
            by_date.setdefault(d, []).append(t)
    for t in done_tasks:
        d = t.get("done_date", "") or t.get("due", "")
        if d:
            by_date.setdefault(d, []).append(t)
    return by_date


def top_focus(open_tasks: list[dict], limit: int = 3) -> list[dict]:
    scored = [t for t in open_tasks if t.get("focus_score", 0) > 0]
    scored.sort(key=lambda t: t["focus_score"], reverse=True)
    return scored[:limit]


def recurring_tasks(open_tasks: list[dict]) -> list[dict]:
    result = []
    for t in open_tasks:
        m = _RECUR_RE.search(t.get("text", ""))
        if m:
            result.append({**t, "frequency": m.group(1)})
    return result


def tag_counts(open_tasks: list[dict], done_tasks: list[dict]) -> dict[str, int]:
    tags: dict[str, int] = {}
    for t in open_tasks + done_tasks:
        for tag in _TAG_RE.findall(t.get("text", "")):
            tags[tag] = tags.get(tag, 0) + 1
    return dict(sorted(tags.items(), key=lambda x: -x[1]))


def stats(open_tasks: list[dict], done_tasks: list[dict], today: date) -> dict:
    today_str = today.isoformat()
    overdue = [t for t in open_tasks if t.get("overdue_days", 0) > 0]
    done_today = [t for t in done_tasks if t.get("done_date") == today_str]
    tiers: dict[str, int] = {}
    for t in open_tasks:
        tier = t.get("tier", "fresh")
        tiers[tier] = tiers.get(tier, 0) + 1
    return {
        "open": len(open_tasks),
        "done": len(done_tasks),
        "overdue": len(overdue),
        "done_today": len(done_today),
        "by_tier": tiers,
    }
