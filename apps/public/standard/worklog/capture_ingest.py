"""worklog — surfaces worklog-capture's pending AI-drafted items inline.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Not a merge of the two apps — worklog-capture stays the capture
SOURCE and worklog stays the sole write endpoint via log_work — this module
is an ingest lane: a thin read/apply/dismiss proxy over worklog-capture's own
queue, so the user doesn't have to know a second app exists to review what it
drafted. Soft dependency (optional_apps in manifest.toml): degrades to
`{"available": False}` when worklog-capture isn't installed, checked via the
cheap synchronous `app_available` feature-detect rather than paying a failed
call_app round trip.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


# ─── Bind to WorklogApp class as ────────────────────────────────
#   pending_captures      = _capture_ingest.pending_captures
#   apply_capture_draft   = _capture_ingest.apply_capture_draft
#   dismiss_capture_draft = _capture_ingest.dismiss_capture_draft
#   api_capture_pending   = _capture_ingest.api_capture_pending
#   api_capture_apply     = _capture_ingest.api_capture_apply
#   api_capture_dismiss   = _capture_ingest.api_capture_dismiss
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────

_APP = "worklog-capture"


async def pending_captures(self) -> dict:
    if not self.app_available(_APP):
        return {"available": False}
    try:
        summary = await self.call_app(_APP, "pending_summary")
    except Exception:
        return {"available": False}
    # The ingest lane only ever renders `flagged` (see pages/index.html). The
    # full summary also carries `unflagged`/`failed` cards — each with a
    # per-item OCR excerpt — meant for worklog-capture's own review UI; a
    # vault with a large capture queue can push that well past a few hundred
    # KB for nothing this endpoint's caller uses. Counts stay so a UI could
    # still say "N more, unreviewed" without paying for their bodies.
    return {
        "available": True,
        "flagged": summary.get("flagged", []),
        "counts": summary.get("counts", {}),
        "unflagged_count": len(summary.get("unflagged", [])),
        "failed_count": len(summary.get("failed", [])),
        "last_digest": summary.get("last_digest", ""),
    }


async def apply_capture_draft(self, id: str, **fields) -> dict:
    if not self.app_available(_APP):
        return {"error": "worklog-capture not installed"}
    return await self.call_app(_APP, "apply_capture", id=id, **fields)


async def dismiss_capture_draft(self, id: str) -> dict:
    if not self.app_available(_APP):
        return {"error": "worklog-capture not installed"}
    return await self.call_app(_APP, "dismiss_capture", id=id)


@web_route("GET", "/api/capture/pending")
async def api_capture_pending(self, request):
    return await self.pending_captures()


@web_route("POST", "/api/capture/apply/{id}")
async def api_capture_apply(self, request):
    cid = request.path_params.get("id", "")
    data = await self.read_json(request)
    return await self.apply_capture_draft(cid, **data)


@web_route("POST", "/api/capture/dismiss/{id}")
async def api_capture_dismiss(self, request):
    cid = request.path_params.get("id", "")
    return await self.dismiss_capture_draft(cid)
