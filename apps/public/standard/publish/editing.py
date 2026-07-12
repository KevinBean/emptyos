"""Publish — element-anchored edit loop for a markdown article (preview / propose
/ apply / reject).

The markdown-sourced cousin of designer/viz element-edit. Designer splices HTML
because its artifact on disk IS html; a publish article's artifact on disk is
**markdown** and the preview HTML is a lossy render, so a clicked anchor resolves
to a markdown *block* and the LLM rewrites *markdown*, spliced into the `.md`.

The scoped-edit cores live in the shared SDK:
  - `emptyos.sdk.markdown_blocks` — split a note body into blocks + stamp the
    rendered top-level elements with `data-eos-el="eN"` (fingerprint-verified;
    disables on desync).
  - `emptyos.sdk.markdown_block_edit.propose_markdown_block_edit` — anchor →
    block → LLM rewrite → validate → splice → full new body.

This module supplies the publish-specific seams: the LLM closure
(`_edit_think_fn`), the note load / frontmatter split, and the SandboxedWrite
staging on the `.md` file (the propose/preview/confirm + staleness primitive,
.claude/rules/proposed-action.md). A free-form article edit is never
autopilot-eligible — the diff IS the value.

Functions here are bound onto PublishApp as methods (see app.py). Gated by the
dark flag `feature.element-edit.enabled` (default False): off → no anchors, no
endpoints, output byte-identical to pre-feature.

Reaches into other modules: self._voice_block (writer). Do not import from
`.app` (it imports us, which would cycle).
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.markdown_block_edit import propose_markdown_block_edit
from emptyos.sdk.markdown_blocks import stamp_markdown_blocks
from emptyos.sdk.markdown_render import render_markdown
from emptyos.sdk.sandbox import (
    SandboxedWrite,
    StaleSandbox,
    discard_sandbox,
    load_sandbox,
)

if TYPE_CHECKING:
    from .app import PublishApp  # noqa: F401 — for type hints only


# ─── Bind to PublishApp class as ─────────────────────────────────────
#   _edit_enabled     = _editing._edit_enabled
#   _edit_root        = _editing._edit_root
#   _edit_think_fn    = _editing._edit_think_fn
#   _load_note_split  = _editing._load_note_split
#   api_edit_preview  = _editing.api_edit_preview
#   api_edit_propose  = _editing.api_edit_propose
#   api_edit_apply    = _editing.api_edit_apply
#   api_edit_reject   = _editing.api_edit_reject
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _edit_enabled(self) -> bool:
    """Dark flag — element-edit off by default (byte-identical output when off)."""
    return bool(self.app_config("feature.element-edit.enabled", False))


def _edit_root(self):
    """Per-action sandbox dir root for staged block edits."""
    return self.data_subdir("edit-proposals")


def _edit_think_fn(self):
    """Return an ``async (system, user) -> str`` closure over self.think.

    The purity seam for `propose_markdown_block_edit`: the SDK owns the prompt +
    validation, this owns the provider knobs. No `min_ability` — a single-block
    rewrite is bounded (.claude/rules/model-ability.md)."""
    async def _think(system: str, user: str) -> str:
        raw = await self.think(user, system=system, domain="text", temperature=0.3)
        return raw if isinstance(raw, str) else str(raw)

    return _think


def _load_note_split(self, path: str):
    """Resolve a note path (absolute or vault-relative), read it, and split off
    frontmatter. Returns ``(file_path, rel_path, fm_prefix, body)`` or an error
    dict. ``fm_prefix + body == full file content`` (verbatim, no stripping) so
    a spliced ``new_body`` reassembles losslessly.
    """
    vault = self._vault_dir()
    if not vault:
        return {"error": "No vault configured"}
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = Path(vault) / path
    try:
        rel = file_path.resolve().relative_to(Path(vault).resolve())
    except ValueError:
        return {"error": "Path outside vault"}
    if not file_path.exists():
        return {"error": "File not found"}

    content = file_path.read_text(encoding="utf-8")
    fm_prefix, body = "", content
    # Split off frontmatter by scanning lines (CRLF-robust, unlike a "\n---\n"
    # substring find). fm_prefix + body == content verbatim, so a spliced body
    # reassembles losslessly and the block splitter never sees frontmatter.
    lines = content.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                split_at = sum(len(l) for l in lines[: i + 1])
                fm_prefix, body = content[:split_at], content[split_at:]
                break
    return file_path, str(rel).replace("\\", "/"), fm_prefix, body


@web_route("GET", "/api/edit/preview")
async def api_edit_preview(self, request) -> dict:
    """Render the note to anchored HTML for the edit surface.

    Returns ``{ok, html, aligned, index}``. ``aligned=False`` (a splitter↔render
    desync) means the note can't be element-edited safely — the UI then hides the
    Edit affordance and offers the Source tab. Wikilinks resolve to display text
    (published_slugs=None) — this is an editing preview, not the built site.
    """
    if not self._edit_enabled():
        return {"ok": False, "error": "element edit is disabled"}
    path = request.query_params.get("path", "")
    if not path:
        return {"ok": False, "error": "path is required"}
    loaded = self._load_note_split(path)
    if isinstance(loaded, dict):
        return {"ok": False, **loaded}
    _fp, _rel, _fm, body = loaded

    rendered = render_markdown(body)
    html = rendered[0] if isinstance(rendered, tuple) else rendered
    anchored, index, aligned = stamp_markdown_blocks(body, html)
    return {"ok": True, "html": anchored, "aligned": aligned, "index": index}


@web_route("POST", "/api/edit/propose")
async def api_edit_propose(self, request) -> dict:
    """Stage a scoped block edit; return a diff for review (no write yet)."""
    if not self._edit_enabled():
        return {"ok": False, "error": "element edit is disabled"}
    data = await request.json()
    path = (data.get("path") or "").strip()
    block_id = (data.get("block_id") or data.get("el") or "").strip()
    instruction = (data.get("instruction") or "").strip()
    if not path or not block_id:
        return {"ok": False, "error": "path and block_id are required"}

    loaded = self._load_note_split(path)
    if isinstance(loaded, dict):
        return {"ok": False, **loaded}
    _fp, rel, fm_prefix, body = loaded

    result = await propose_markdown_block_edit(
        body, block_id, instruction=instruction,
        think_fn=self._edit_think_fn(), voice_hint=await self._voice_block(),
    )
    if not result.ok:
        out = {"ok": False, "error": result.error}
        if result.fallback:
            out["fallback"] = result.fallback
        return out

    token = "ed-" + secrets.token_hex(6)
    sw = SandboxedWrite(
        action_id=token,
        vault_root=self._vault_dir(),
        rel_path=rel,
        content=fm_prefix + result.new_body,
        sandbox_root=self._edit_root(),
    )
    sw.capture()
    return {"ok": True, "token": token, "block_id": block_id, "diff_lines": sw.diff_lines()}


@web_route("POST", "/api/edit/apply")
async def api_edit_apply(self, request) -> dict:
    """Apply a previously-proposed block edit (with staleness guard)."""
    if not self._edit_enabled():
        return {"ok": False, "error": "element edit is disabled"}
    data = await request.json()
    token = (data.get("token") or "").strip()
    if not token:
        return {"ok": False, "error": "token is required"}

    sw = load_sandbox(token, self._edit_root())
    if sw is None:
        return {"ok": False, "error": "expired", "message": "proposal expired — re-pick the block"}
    try:
        target = sw.apply()
    except StaleSandbox:
        return {"ok": False, "error": "stale", "message": "the note changed since you proposed — re-pick"}
    sw.discard()
    await self.emit("publish:updated", {"path": str(target)})
    return {"ok": True, "path": str(target)}


@web_route("POST", "/api/edit/reject")
async def api_edit_reject(self, request) -> dict:
    """Discard a proposed block edit without writing."""
    data = await request.json()
    return discard_sandbox(data.get("token") or "", self._edit_root())
