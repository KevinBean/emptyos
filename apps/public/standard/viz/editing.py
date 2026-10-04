"""viz — element-anchored edit loop (propose / preview / apply / reject).

Second consumer of the shared scoped-edit core
`emptyos.sdk.html_element_edit.propose_element_edit` (designer is the first;
CLAUDE.md rule 9, .claude/rules/artifact-element-edit.md). Open-Canvas-style
"highlight-to-edit": click an element in the preview iframe (eos-edit-shim.js
posts the `data-eos-el` anchor out) → this turns (artifact id, anchor,
instruction|knob) into a previewable, gated write via
`emptyos.sdk.sandbox.SandboxedWrite`.

The load-bearing difference from designer: viz only offers this for
DOM-structured shapes (`SHAPE_SUPPORTS_ELEMENT_EDIT`). Canvas-rendered shapes
(3d-scene, chart, network-graph) have no addressable element to highlight, so
their full-file / agent iterate path stays. The shape gate here is the backstop
in addition to the frontend hiding the toggle.

Dark-flagged behind `[apps.viz] feature.element-edit.enabled` (default false) —
anchors are only stamped at persist time when the flag AND the shape gate both
hold, so output is byte-identical to the pre-feature artifact when off.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._record_dir / self._rel_html / self._rel_record
(generation).
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

from .shared import SHAPE_SUPPORTS_ELEMENT_EDIT, _now_iso

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ─────────────────────────────────────────
#   _edit_enabled        = _editing._edit_enabled
#   _shape_supports_edit = _editing._shape_supports_edit
#   _edit_root           = _editing._edit_root
#   _edit_think_fn       = _editing._edit_think_fn
#   api_edit_propose     = _editing.api_edit_propose
#   api_edit_apply       = _editing.api_edit_apply
#   api_edit_reject      = _editing.api_edit_reject
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _edit_enabled(self) -> bool:
    """True when the element-anchored edit loop is on for this machine.

    Dark default (per project_feature_pipeline_flag_default_dark) — anchors are
    only stamped, and the edit endpoints only act, when this is explicitly
    flipped in `[apps.viz] feature.element-edit.enabled`.
    """
    return bool(self.app_config("feature.element-edit.enabled", False))


def _shape_supports_edit(self, shape: str) -> bool:
    """True for DOM-structured shapes; False for canvas-rendered ones."""
    return shape in SHAPE_SUPPORTS_ELEMENT_EDIT


def _edit_root(self):
    """Per-action sandbox dir root for staged element edits."""
    return self.data_subdir("edit-proposals")


def _edit_think_fn(self):
    """Return an ``async (system, user) -> str`` closure over self.think.

    The purity seam for the shared `propose_element_edit`. No `min_ability` — a
    single-element rewrite is a bounded task weak models handle well.
    """
    async def _think(system: str, user: str) -> str:
        raw = await self.think(
            user,
            system=system,
            domain=self.setting_or_config("viz.think_domain", "code", config_key="think_domain"),
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

    html_path = self._record_dir(rid) / "scene.html"
    if not html_path.exists():
        return {"ok": False, "error": f"no artifact with id '{rid}'"}

    shape = (self.vault_get_properties(self._rel_record(rid)) or {}).get("shape", "") or ""
    if not self._shape_supports_edit(shape):
        return {"ok": False, "error": f"the '{shape}' shape can't be edited by element — use Iterate"}

    doc = html_path.read_text(encoding="utf-8")
    result = await propose_element_edit(
        doc, el, instruction=instruction, knob=knob,
        think_fn=self._edit_think_fn(),
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
        return {"ok": False, "error": "stale", "message": "the artifact changed since you proposed — re-pick"}

    # SandboxedWrite.apply() writes the file but does NOT emit or bump record.md;
    # do it here or list/timeline views go stale.
    try:
        size_kb = round(target.stat().st_size / 1024, 1)
        self.vault_update(self._rel_record(rid), {"updated": _now_iso(), "size_kb": size_kb})
    except Exception:
        pass
    sw.discard()
    await self.emit("viz:updated", {"id": rid})
    return {"ok": True, "id": rid}


@web_route("POST", "/api/edit/reject")
async def api_edit_reject(self, request) -> dict:
    """Discard a proposed element edit without writing."""
    body = await request.json()
    return discard_sandbox(body.get("token") or "", self._edit_root())
