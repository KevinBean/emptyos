"""task — conservative re-runnable backlog triage — duplicates, zombies, and AI someday classification.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The read-only triage scan, the dedup apply, and the dark-flagged AI someday classify/promote flow. Source of truth for the /api/triage/* endpoints.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.archive_tasks (archive module via self), self.set_field/_idx/think (spine), queries.detect_duplicates/map_dispositions/CONTEXT_KEYWORDS.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import parse_llm_json

from . import queries

if TYPE_CHECKING:
    from .app import TaskApp  # noqa: F401 — for type hints only

log = logging.getLogger("emptyos.task")


TRIAGE_CLASSIFY_SYSTEM = (
    "You triage a backlog of one-line 'someday' tasks. For EACH task decide one "
    "disposition:\n"
    "- promote: a concrete, specific, still-relevant next action to do soon\n"
    "- keep: a real but not-yet-actionable idea worth keeping in the someday list\n"
    "- archive: stale, vague, obsolete, or clearly no-longer-relevant\n\n"
    "Return STRICT JSON and NOTHING else:\n"
    '{"dispositions": [{"i": <int>, "disposition": "promote|keep|archive", "reason": "<short>"}]}\n\n'
    "Rules:\n"
    "- Echo back the exact integer index 'i' given for each task; never invent tasks.\n"
    "- Default to 'keep' when unsure — archiving is destructive and promoting adds noise.\n"
    "- 'promote' sparingly: only clearly actionable, specific, still-relevant items.\n"
    "- Judge ONLY from the text given; do not assume external context.\n"
    "- 'reason' is one short clause."
)


# ─── Bind to TaskApp class as ────────────────────────────────
#   TRIAGE_OLD_SOMEDAY_DAYS   = _triage.TRIAGE_OLD_SOMEDAY_DAYS
#   triage_scan               = _triage.triage_scan
#   api_triage_scan           = _triage.api_triage_scan
#   api_triage_dedup          = _triage.api_triage_dedup
#   _ai_triage_enabled        = _triage._ai_triage_enabled
#   classify_someday_batch    = _triage.classify_someday_batch
#   api_triage_ai_status      = _triage.api_triage_ai_status
#   api_triage_classify       = _triage.api_triage_classify
#   api_triage_promote_batch  = _triage.api_triage_promote_batch
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


TRIAGE_OLD_SOMEDAY_DAYS = 180  # dateless someday older than this = archive candidate


async def triage_scan(self) -> dict:
    """Read-only triage proposals — never writes.

    Surfaces two conservative-cleanup batches the user confirms per-section:
    duplicate groups (keep first, archive the rest) and zombie/very-old
    someday tasks grouped by life domain. The UI confirms each batch, which
    calls the archive endpoints — nothing here mutates the vault.
    """
    import time as _time

    open_tasks, _ = await self._idx.get()

    # Only flag duplicates WITHIN the same file. Identical text across
    # different project files is almost always boilerplate (e.g. every
    # project scaffold has a "Define project scope" task), not a real
    # duplicate — archiving those would delete distinct tasks.
    by_file_tasks: dict[str, list] = {}
    for t in open_tasks:
        by_file_tasks.setdefault(t.get("file", ""), []).append(t)
    duplicates = []
    for ftasks in by_file_tasks.values():
        for grp in queries.detect_duplicates(ftasks):
            keeper = grp[0]
            duplicates.append({
                "keep": {"file": keeper["file"], "line": keeper["line"], "text": keeper["text"]},
                "remove": [
                    {"file": t["file"], "line": t["line"], "text": t["text"]} for t in grp[1:]
                ],
            })

    cutoff = _time.time() - self.TRIAGE_OLD_SOMEDAY_DAYS * 86400
    by_domain: dict[str, list] = {}
    zombie_total = 0
    for t in open_tasks:
        is_zombie = t.get("tier") == "zombie"
        is_old_someday = (
            t.get("actionability") == "someday"
            and not (t.get("due") or "")
            and 0 < (t.get("mtime") or 0) < cutoff
        )
        if not (is_zombie or is_old_someday):
            continue
        zombie_total += 1
        low = (t.get("text", "") or "").lower()
        dom = "other"
        for ctx, kws in queries.CONTEXT_KEYWORDS.items():
            if any(k in low for k in kws):
                dom = ctx
                break
        by_domain.setdefault(dom, []).append({
            "file": t["file"], "line": t["line"], "text": t["text"],
            "tier": t.get("tier", "fresh"), "due": t.get("due", ""),
        })

    return {
        "duplicates": duplicates,
        "zombies_by_domain": by_domain,
        "counts": {
            "duplicate_groups": len(duplicates),
            "duplicate_removable": sum(len(d["remove"]) for d in duplicates),
            "zombies": zombie_total,
        },
    }


@web_route("GET", "/api/triage/scan")
async def api_triage_scan(self, request):
    return await self.triage_scan()


@web_route("POST", "/api/triage/dedup")
async def api_triage_dedup(self, request):
    """Archive the redundant copies in every duplicate group (keeps the
    first occurrence). Re-scans live so line numbers are fresh."""
    scan = await self.triage_scan()
    removable = []
    for d in scan["duplicates"]:
        removable.extend(d["remove"])
    if not removable:
        return {"ok": True, "archived": 0}
    res = await self.archive_tasks(removable)
    await self.emit("task:triaged", {"kind": "dedup", "archived": res.get("archived", 0)})
    return res


def _ai_triage_enabled(self) -> bool:
    """Live ⚙-toggle first, then emptyos.toml; default OFF (Rule 19)."""
    v = self.setting("task.feature.ai-triage.enabled", None)
    if v is not None:
        return bool(v)
    return bool(self.app_config("feature.ai-triage.enabled", False))


async def classify_someday_batch(self, tasks: list[dict], *, chunk: int = 100) -> list[dict]:
    """Classify someday tasks into promote/keep/archive via batched think.

    Returns proposals only — never writes. Sends ONLY one-line task text
    (Rule 19); classification is bounded so a local model suffices. Never
    raises — a failed/garbled batch contributes nothing.
    """
    out: list[dict] = []
    for start in range(0, len(tasks), chunk):
        batch = tasks[start:start + chunk]
        listing = "\n".join(
            f"{i}. {(t.get('text') or '').strip()}" for i, t in enumerate(batch)
        )
        try:
            raw = await self.think(
                f"Tasks:\n{listing}",
                # No domain → default provider chain (widest availability,
                # incl. local ollama). Classification doesn't need a
                # domain-specialised model.
                system=TRIAGE_CLASSIFY_SYSTEM,
                temperature=0.1,
            )
        except Exception as e:  # noqa: BLE001 — never break the page on a bad batch
            log.warning("triage classify batch failed: %s", e)
            continue
        parsed = parse_llm_json(raw, fallback={"dispositions": []})
        disp = parsed.get("dispositions") if isinstance(parsed, dict) else parsed
        out.extend(queries.map_dispositions(disp, batch))
    return out


@web_route("GET", "/api/triage/ai-status")
async def api_triage_ai_status(self, request):
    """Feature-detect for the page: is AI triage enabled + how many someday."""
    open_tasks, _ = await self._idx.get()
    someday = sum(1 for t in open_tasks if t.get("actionability") == "someday")
    return {"enabled": self._ai_triage_enabled(), "someday_total": someday}


@web_route("GET", "/api/triage/classify")
async def api_triage_classify(self, request):
    """One page of someday tasks with suggested dispositions. Writes nothing."""
    if not self._ai_triage_enabled():
        return {"disabled": True}
    try:
        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))
    except (TypeError, ValueError):
        offset, limit = 0, 50
    offset = max(0, offset)
    limit = max(1, min(limit, 150))
    open_tasks, _ = await self._idx.get()
    someday = [t for t in open_tasks if t.get("actionability") == "someday"]
    page = someday[offset:offset + limit]
    if not page:
        return {"dispositions": [], "offset": offset, "total": len(someday), "done": True}
    props = await self.classify_someday_batch(page)
    return {
        "dispositions": props, "offset": offset, "limit": limit,
        "total": len(someday), "next_offset": offset + limit,
        "done": offset + limit >= len(someday),
    }


@web_route("POST", "/api/triage/promote-batch")
async def api_triage_promote_batch(self, request):
    """Promote tasks to Next (tag `#next`) — body ``{items: [{file, line}, ...]}``."""
    data = await request.json()
    items = data.get("items")
    if not isinstance(items, list):
        return {"error": "items must be a list of {file, line}"}
    promoted = 0
    for it in items:
        f = (it.get("file") or "").replace("\\", "/")
        ln = it.get("line")
        if not f or not ln:
            continue
        try:
            res = await self.set_field(f"{f}:{int(ln)}", "actionability", "next")
        except (TypeError, ValueError):
            continue
        if res.get("ok"):
            promoted += 1
    if promoted:
        await self.emit("task:triaged", {"kind": "promote", "promoted": promoted})
    return {"ok": True, "promoted": promoted}
