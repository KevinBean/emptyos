"""Assistant — durable ripple: propose/apply a vault edit that records a
reconcile correction, through the propose→preview→confirm gate.

When the two-phase companion finds two notes disagree (and the user confirms the
current fact), the stale note is fixed so the conflict stops recurring — either a
non-destructive dated line in its ``## Timeline`` (default) or a ``superseded_by``
frontmatter marker (whole-note retirement, which `vault_query(include_superseded=
False)` already excludes). Never auto-writes: every edit is captured as a
`SandboxedWrite` diff the user Applies/Rejects (`.claude/rules/proposed-action.md`).

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns the
`/api/reconcile/*` endpoints + the pure `append_timeline_line` transform.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   api_reconcile_propose = _reconcile_mod.api_reconcile_propose
#   api_reconcile_apply   = _reconcile_mod.api_reconcile_apply
#   api_reconcile_reject  = _reconcile_mod.api_reconcile_reject
#   _reconcile_store      = _reconcile_mod._reconcile_store
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def append_timeline_line(content: str, line: str) -> str:
    """Append a dated bullet to the note's ``## Timeline`` section, creating the
    section if absent. PURE string transform — builds the proposed content for the
    sandbox diff; it does NOT write. The bullet lands at the end of the existing
    Timeline section (before the next ``## `` heading) so chronology stays intact."""
    line = line.rstrip()
    had_trailing_nl = content.endswith("\n")
    lines = content.splitlines()
    idx = next((i for i, ln in enumerate(lines)
                if ln.strip().lower() == "## timeline"), None)
    if idx is None:
        sep = "" if content.endswith("\n\n") or not content else "\n"
        body = content.rstrip("\n")
        return f"{body}\n\n## Timeline\n\n{line}\n"
    end = len(lines)
    for j in range(idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    insert_at = end
    while insert_at - 1 > idx and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    new_lines = lines[:insert_at] + [line] + lines[insert_at:]
    return "\n".join(new_lines) + ("\n" if had_trailing_nl else "")


def _reconcile_store(self):
    """Shared diff propose/apply/reject store for reconcile edits."""
    from emptyos.sdk.diff_proposal import DiffProposalStore

    return DiffProposalStore(self.data_dir / "reconcile")


@web_route("POST", "/api/reconcile/propose-fix")
async def api_reconcile_propose(self, request):
    """Capture a proposed durable fix to a stale note + return the diff. Body:
    {stale_path, fact, source_path?, mode?}. mode = "timeline" (default) | "supersede"."""
    if not self.app_config("feature.companion-twophase.enabled", False):
        return {"ok": False, "error": "disabled"}
    data = await request.json()
    stale = (data.get("stale_path") or "").replace("\\", "/").strip()
    # Collapse to one line + cap: a ## Timeline entry is one dated bullet per event,
    # but the correction answer the UI passes can be multi-paragraph.
    fact = " ".join((data.get("fact") or "").split())[:240]
    source = (data.get("source_path") or "").replace("\\", "/").strip()
    mode = data.get("mode") or "timeline"
    if not stale or not fact:
        return {"ok": False, "error": "stale_path and fact required"}

    from emptyos.sdk.utils import now_iso, set_frontmatter_field

    current = self.vault_read_at(stale)
    if not current:
        return {"ok": False, "error": "stale note not found"}
    today = now_iso()[:10]
    src_slug = source.rsplit("/", 1)[-1].removesuffix(".md") if source else ""

    if mode == "supersede" and src_slug:
        new_content = set_frontmatter_field(current, "superseded_by", f'"[[{src_slug}]]"')
        new_content = set_frontmatter_field(new_content, "as_of", today)
    else:
        mode = "timeline"
        ref = f" (per [[{src_slug}]])" if src_slug else ""
        new_content = append_timeline_line(current, f"- {today} — {fact}{ref}")

    if new_content == current:
        return {"ok": False, "error": "no change"}
    action_id = "act-" + uuid.uuid4().hex[:10]
    res = _reconcile_store(self).propose(action_id, self.vault_root, stale, new_content)
    if not res.get("ok"):
        return res
    return {"ok": True, "action_id": action_id, "path": stale, "mode": mode,
            "diff": res["diff"]}


@web_route("POST", "/api/reconcile/apply-fix")
async def api_reconcile_apply(self, request):
    """Apply a captured proposed fix (staleness-checked). Body: {action_id}."""
    data = await request.json()
    aid = (data.get("action_id") or "").strip()
    res = _reconcile_store(self).apply(aid)
    if not res.get("ok"):
        return res
    try:
        rel = str(Path(res["target"]).resolve().relative_to(self.vault_root.resolve())).replace("\\", "/")
    except Exception:
        rel = res["target"]
    await self.emit("assistant:reconcile_applied", {"path": rel, "action_id": aid})
    return {"ok": True, "path": rel}


@web_route("POST", "/api/reconcile/reject-fix")
async def api_reconcile_reject(self, request):
    """Discard a captured proposed fix. Body: {action_id}."""
    data = await request.json()
    aid = (data.get("action_id") or "").strip()
    return _reconcile_store(self).reject(aid)
