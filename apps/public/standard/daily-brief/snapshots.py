"""daily-brief — command-center snapshots + personalization.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The task/project/journal/people/audit snapshots that feed the command-center view, plus vault-signal derivation and the personalize endpoint.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.call_app to sibling apps; self._locale (spine); shared.PERSONALIZE_SYSTEM.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
from datetime import date as date_cls
from emptyos.sdk import web_route
from .shared import PERSONALIZE_SYSTEM, _LANG
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import DailyBriefApp  # noqa: F401 — for type hints only


# ─── Bind to DailyBriefApp class as ────────────────────────────────
#   _task_snapshot            = _snapshots._task_snapshot
#   _project_snapshot         = _snapshots._project_snapshot
#   _journal_snapshot         = _snapshots._journal_snapshot
#   _people_snapshot          = _snapshots._people_snapshot
#   _audit_snapshot           = _snapshots._audit_snapshot
#   _command_center_snapshot  = _snapshots._command_center_snapshot
#   _vault_signals            = _snapshots._vault_signals
#   _SIGNAL_LABELS            = _snapshots._SIGNAL_LABELS
#   _derive_focus             = _snapshots._derive_focus
#   api_personalize           = _snapshots.api_personalize
#   api_command_center        = _snapshots.api_command_center
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def _task_snapshot(self, today: date_cls) -> dict:
    raw, err = await self.try_call_app("task", "list_all")
    if err:
        return self._unavailable("task", err)
    rows = raw if isinstance(raw, list) else []
    open_rows = [r for r in rows if isinstance(r, dict) and not r.get("done")]

    def due_date(row: dict) -> date_cls | None:
        # Trust the task app's parsed `due` field only — scanning the task
        # text would misread any prose date as a deadline.
        return self._maybe_iso_date(row.get("due"))

    overdue = []
    due_today = []
    for row in open_rows:
        d = due_date(row)
        if d and d < today:
            overdue.append(row)
        elif d == today:
            due_today.append(row)

    def score(row: dict):
        d = due_date(row)
        due_rank = 0 if d and d < today else 1 if d == today else 2
        return (due_rank, -int(row.get("focus_score") or 0), row.get("text", ""))

    focus = []
    for row in sorted(open_rows, key=score)[:6]:
        d = due_date(row)
        focus.append({
            "id": row.get("id") or f"{row.get('file', '')}:{row.get('line', '')}",
            "text": row.get("text", ""),
            "file": row.get("file", ""),
            "line": row.get("line", 0),
            "due": d.isoformat() if d else "",
            "project": row.get("project", ""),
            "overdue": bool(d and d < today),
            "due_today": bool(d == today),
        })
    return {
        "available": True,
        "open_count": len(open_rows),
        "due_count": len(due_today) + len(overdue),
        "overdue_count": len(overdue),
        "focus": focus,
    }


async def _project_snapshot(self, today: date_cls) -> dict:
    raw, err = await self.try_call_app("projects", "list_all")
    if err:
        return self._unavailable("projects", err)
    rows = raw if isinstance(raw, list) else []
    live_status = {"idea", "active", "blocked"}
    active = [p for p in rows if isinstance(p, dict) and p.get("status") in live_status]
    blocked = [p for p in active if p.get("status") == "blocked"]

    attention = []
    for p in active:
        days = p.get("days_until_deadline")
        stale = int(p.get("stale_days") or 0)
        if p.get("status") == "blocked" or p.get("overdue") or (
            isinstance(days, int) and days <= 14
        ) or stale >= 30:
            attention.append({
                "id": p.get("id", ""),
                "name": p.get("name") or p.get("id", ""),
                "status": p.get("status", ""),
                "deadline": p.get("deadline", ""),
                "days_until_deadline": days,
                "stale_days": stale,
                "open_tasks": p.get("open_tasks", 0),
                "next_action": p.get("next_action", ""),
                "overdue": bool(p.get("overdue")),
            })

    def project_rank(p: dict):
        days = p.get("days_until_deadline")
        deadline_rank = days if isinstance(days, int) else 9999
        return (0 if p.get("status") == "blocked" else 1, deadline_rank, -p.get("stale_days", 0))

    return {
        "available": True,
        "active_count": len(active),
        "blocked_count": len(blocked),
        "attention": sorted(attention, key=project_rank)[:6],
    }


async def _journal_snapshot(self, today: date_cls) -> dict:
    raw, err = await self.try_call_app("journal", "get_summary", date_str=today.isoformat())
    if err:
        return self._unavailable("journal", err)
    summary = raw if isinstance(raw, dict) else {}
    return {
        "available": True,
        "entries": int(summary.get("entries") or summary.get("today_entries") or 0),
        "streak": int(summary.get("streak") or 0),
        "mood": summary.get("mood", ""),
    }


async def _people_snapshot(self) -> dict:
    followups, err = await self.try_call_app("people", "panel_reach_out")
    if err:
        return self._unavailable("people", err)
    birthdays, _bd_err = await self.try_call_app("people", "birthdays", days=14)
    return {
        "available": True,
        "followups": followups if isinstance(followups, list) else [],
        "birthdays": birthdays if isinstance(birthdays, list) else [],
    }


def _audit_snapshot(self) -> dict:
    try:
        from emptyos.sdk import autopilot as _autopilot

        root = self.autopilot_root()
        integrity = _autopilot.verify_audit(root)
        review = _autopilot.review_grants(root)
    except Exception as e:  # noqa: BLE001 - command center should still load
        return {"available": False, "error": str(e)[:220]}
    return {
        "available": True,
        "ok": bool(integrity.get("ok")),
        "lines_checked": int(integrity.get("lines_checked") or 0),
        "tampered_count": len(integrity.get("tampered") or []),
        "grants_count": len(review.get("grants") or []),
        "stale_count": len(review.get("stale_ids") or []),
        "expiring_count": len(review.get("expiring_ids") or []),
        "holds_count": int(review.get("holds_count") or 0),
        "audit_lines": int(review.get("audit_lines") or 0),
        "week": review.get("week") or {},
        "budgets_over": review.get("budgets_over") or [],
    }


async def _command_center_snapshot(self) -> dict:
    today = date_cls.today()
    tasks, projects, journal, people = await asyncio.gather(
        self._task_snapshot(today),
        self._project_snapshot(today),
        self._journal_snapshot(today),
        self._people_snapshot(),
    )
    return {
        "date": today.isoformat(),
        "status": self._status(),
        "tasks": tasks,
        "projects": projects,
        "journal": journal,
        "people": people,
        "audit": self._audit_snapshot(),
    }


# ── Personalize from vault ─────────────────────────────────
def _vault_signals(self) -> dict:
    """Compact interest profile used to derive the focus lens. Metadata
    only — never note prose. Delegates to the shared
    ``BaseApp.vault_interest_profile`` (CLAUDE.md rule 9).

    We read three folder dimensions (projects + areas + top-level
    resources), not just work, so the tailor can span the *whole person*
    — and surface more tags (40) so genuine personal-interest tags survive
    truncation rather than being crowded out by a single bulk data import."""
    return self.vault_interest_profile(
        folders=("10_Projects", "20_Areas", "30_Resources"),
        max_tags=40,
    )


# Human labels for the folder dimensions the profile may carry. Anything
# not listed falls back to a title-cased key, so new folders surface too.
_SIGNAL_LABELS = {
    "projects": "Active projects",
    "areas": "Areas of responsibility",
    "resources": "Resource folders",
}


async def _derive_focus(self, signals: dict) -> dict:
    lang_name = _LANG[self._locale()][1]
    profile = []
    # Authored folder names first — the trustworthy signal. Render every
    # folder dimension the profile carries (projects, areas, resources, …),
    # not a hardcoded two, so broadening the read doesn't drop a dimension.
    for key, vals in signals.items():
        if key == "tags" or not vals:
            continue
        label = self._SIGNAL_LABELS.get(key, key.replace("_", " ").title())
        profile.append(f"{label}: " + ", ".join(str(v) for v in vals))
    if signals.get("tags"):
        profile.append(
            "Most-used tags (counts are NOISY — bulk imports inflate them): "
            + ", ".join(f"{t}({c})" for t, c in signals["tags"]))
    if not profile:
        profile.append("(profile is sparse — little structured vault content yet)")
    # NB: PERSONALIZE_SYSTEM contains literal JSON braces ({"focus": ...}) —
    # str.format() would read those as replacement fields and KeyError. Use
    # replace() for the single placeholder.
    system = PERSONALIZE_SYSTEM.replace("{lang_name}", lang_name)
    out = await self.think("\n".join(profile), domain="reason", system=system,
                           temperature=0.3)
    from emptyos.sdk.utils import parse_llm_json
    parsed = parse_llm_json(out, fallback={}) if isinstance(out, str) else (out or {})
    if not isinstance(parsed, dict):
        parsed = {}
    # The model sometimes returns a topic field as a JSON array rather than
    # a comma string — flatten either shape to a clean "a, b, c".
    def _flat(v) -> str:
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x).strip() for x in v if str(x).strip())
        return str(v or "").strip()

    focus = _flat(parsed.get("focus"))
    rationale = _flat(parsed.get("rationale"))
    raw_dims = parsed.get("dimensions") or {}
    dimensions = {
        k: _flat(raw_dims.get(k))
        for k in ("work", "interests", "growth")
        if isinstance(raw_dims, dict) and _flat(raw_dims.get(k))
    }
    # Compose a flat focus from the dimensions if the model gave only those,
    # so the downstream brief lens (a single string) is never empty.
    if not focus and dimensions:
        focus = ", ".join(dimensions.values())
    return {"focus": focus, "rationale": rationale, "dimensions": dimensions}


@web_route("POST", "/api/personalize")
async def api_personalize(self, request):
    """Propose a focus lens derived from the vault. Writes NOTHING — the
    client previews the suggestion (and the exact signals used) and only
    saves the `daily-brief.focus` setting on the user's confirmation."""
    signals = self._vault_signals()
    try:
        derived = await self._derive_focus(signals)
    except Exception as e:  # noqa: BLE001 - fail-soft, surface to UI
        return {"ok": False, "error": str(e)[:300], "signals": signals}
    if not derived["focus"]:
        return {"ok": False, "error": "could not derive a focus", "signals": signals}
    return {"ok": True, **derived, "signals": signals,
            "current_focus": str(self._cfg("focus", "") or "")}


# ── Web API ────────────────────────────────────────────────
@web_route("GET", "/api/command-center")
async def api_command_center(self, request):
    """Read-only daily command surface: work, journal, people, and audit state."""
    return await self._command_center_snapshot()
