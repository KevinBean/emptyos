"""designer — element-anchored edit loop (propose / preview / apply / reject).

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
click-an-element → scoped-change → diff-preview → confirm flow that is the
designer Phase-2 differentiator. The visual *targeting* happens in the iframe
(eos-edit-shim.js → postMessage with the clicked `data-eos-el`); this module
turns a (page id, anchor, instruction|knob) request into a previewable, gated
write.

The scoped-edit *core* (anchor → extract span → rewrite|knob → validate →
splice) lives in the shared SDK `emptyos.sdk.html_element_edit.propose_element_edit`
(shared with viz — CLAUDE.md rule 9, .claude/rules/artifact-element-edit.md).
This module supplies the designer-specific seams: the LLM closure
(`_edit_think_fn`), the page-vs-viz read paths, and the SandboxedWrite staging.

Both edit kinds (instruction LLM-rewrite, deterministic style knob) splice the
new element back into the full page and stage it through
`emptyos.sdk.sandbox.SandboxedWrite` — the same propose/preview/confirm +
staleness primitive the rooms review-gate uses (.claude/rules/proposed-action.md).
The diff stays small because only one element changed; the gate stays because a
free-form HTML edit is never autopilot-eligible — the diff IS the value.

When extraction is ambiguous (unclosed/auto-closed tags), propose returns
`fallback:"iterate"` and the UI offers the existing whole-file path instead of
splicing a wrong span.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._edit_enabled (anchors); self._record_dir /
self._rel_html / self._rel_record (generation).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.html_element_edit import propose_element_edit
from emptyos.sdk.sandbox import (
    SandboxedWrite,
    StaleSandbox,
    discard_sandbox,
    load_sandbox,
)

from .shared import _now_iso

if TYPE_CHECKING:
    from .app import DesignerApp  # noqa: F401 — for type hints only


# ─── Bind to DesignerApp class as ────────────────────────────────────
#   _edit_root        = _editing._edit_root
#   _edit_think_fn    = _editing._edit_think_fn
#   api_edit_propose  = _editing.api_edit_propose
#   api_edit_apply    = _editing.api_edit_apply
#   api_edit_reject   = _editing.api_edit_reject
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _edit_root(self):
    """Per-action sandbox dir root for staged element edits."""
    return self.data_subdir("edit-proposals")


def _edit_think_fn(self):
    """Return an ``async (system, user) -> str`` closure over self.think.

    The purity seam for the shared `propose_element_edit`: the SDK owns the
    prompt + validation, this owns the provider knobs. No `min_ability` — a
    single-element rewrite is a bounded/structured task weak models handle well
    (.claude/rules/model-ability.md); forcing a tier would prefer a weak
    provider even when a strong one is free.
    """
    async def _think(system: str, user: str) -> str:
        raw = await self.think(
            user,
            system=system,
            domain=self.setting_or_config("designer.think_domain", "code", config_key="think_domain"),
            temperature=0.3,
            max_tokens=2048,
        )
        return raw if isinstance(raw, str) else str(raw)

    return _think


@web_route("POST", "/api/edit/propose")
async def api_edit_propose(self, request) -> dict:
    """Stage a scoped element edit; return a diff for review (no write yet)."""
    if not self._edit_enabled():
        return {"ok": False, "error": "element edit is disabled"}
    body = await request.json()
    rid = (body.get("id") or "").strip()
    el = (body.get("el") or "").strip()
    instruction = (body.get("instruction") or "").strip()
    knob = body.get("knob") if isinstance(body.get("knob"), dict) else None

    if not rid or not el:
        return {"ok": False, "error": "id and el are required"}

    html_path = self._record_dir(rid) / "page.html"
    if not html_path.exists():
        return {"ok": False, "error": f"no design with id '{rid}'"}
    doc = html_path.read_text(encoding="utf-8")

    style = (self.vault_get_properties(self._rel_record(rid)) or {}).get("style", "") or ""
    result = await propose_element_edit(
        doc, el, instruction=instruction, knob=knob,
        think_fn=self._edit_think_fn(), style_hint=style,
    )
    if not result.ok:
        out = {"ok": False, "error": result.error}
        if result.fallback:
            out["fallback"] = result.fallback
        return out

    token = "ed-" + secrets.token_hex(6)
    sw = SandboxedWrite(
        action_id=token,
        vault_root=self.kernel.config.notes_path,
        rel_path=self._rel_html(rid),
        content=result.new_html,
        sandbox_root=self._edit_root(),
    )
    sw.capture()
    return {"ok": True, "token": token, "el": el, "diff_lines": sw.diff_lines()}


@web_route("POST", "/api/edit/apply")
async def api_edit_apply(self, request) -> dict:
    """Apply a previously-proposed element edit (with staleness guard)."""
    if not self._edit_enabled():
        return {"ok": False, "error": "element edit is disabled"}
    body = await request.json()
    rid = (body.get("id") or "").strip()
    token = (body.get("token") or "").strip()
    if not rid or not token:
        return {"ok": False, "error": "id and token are required"}

    sw = load_sandbox(token, self._edit_root())
    if sw is None:
        return {"ok": False, "error": "expired", "message": "proposal expired — re-pick the element"}
    try:
        target = sw.apply()
    except StaleSandbox:
        return {"ok": False, "error": "stale", "message": "the page changed since you proposed — re-pick"}

    # SandboxedWrite.apply() writes the file but does NOT emit or bump record.md;
    # do it here or list/timeline views go stale.
    try:
        size_kb = round(target.stat().st_size / 1024, 1)
        self.vault_update(self._rel_record(rid), {"updated": _now_iso(), "size_kb": size_kb})
    except Exception:
        pass
    sw.discard()
    await self.emit("designer:updated", {"id": rid})
    return {"ok": True, "id": rid}


@web_route("POST", "/api/edit/reject")
async def api_edit_reject(self, request) -> dict:
    """Discard a proposed element edit without writing."""
    body = await request.json()
    return discard_sandbox(body.get("token") or "", self._edit_root())
