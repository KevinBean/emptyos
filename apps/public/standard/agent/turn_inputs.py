"""AgentApp — a chat turn's extra inputs: attachments and vault context (B3).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: turning the paths a composer attached into what the model
receives (image parts, extracted document text — emptyos/sdk/attachments.py),
the optional "ground this in my notes" block, and the two refusals that come
before either: vault-derived content never goes to a cloud model without an
explicit per-message yes (CLAUDE.md rule 19), and an image never goes to a
model that cannot read it (a clear error, never a silent reroute).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self.scoped_retrieve`` / ``self.read`` /
``self.call_app`` (BaseApp). Do not import from ``.app`` (it imports us).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk.attachments import prepare_turn, provider_reads_images

if TYPE_CHECKING:
    from .app import AgentApp  # noqa: F401 — for type hints only


# ─── Bind to AgentApp class as ──────────────────────────────────────────
#   _prepare_turn_inputs = _turn_inputs._prepare_turn_inputs
#   _vault_context_block = _turn_inputs._vault_context_block
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

#: Notes the vault-context block may carry, and characters of each. Four notes
#: of ~1.5k chars ≈ 1.5k tokens: enough to ground a question, small enough to
#: leave the conversation room (the chat can still open a note with Read).
VAULT_CONTEXT_NOTES = 4
VAULT_CONTEXT_CHARS = 1500

#: Attachments one message may carry — the portal composer's own cap
#: (portal-attach.js MAX_ITEMS). Anything past it is refused by name.
MAX_ATTACHMENTS = 8

VAULT_CONTEXT_HEADER = (
    "[From the user's notes — retrieved for this question; cite a note by name "
    "when you use it, and say so when none of it answers the question]"
)


async def _prepare_turn_inputs(
    self, provider, is_native: bool, user_text: str, attachments, vault_context: bool,
    cloud_ok: bool, websocket, session_id: str,
) -> dict | None:
    """``{text, image_urls, image_paths}`` for the turn, or ``None`` when it
    must not run (the reason was already sent on ``websocket``).

    ``text`` is what the model reads: the vault block (if asked for), the
    user's words, then each extracted document. The typed text is recorded
    beside it for display and search (sessions.turn_display_marks)."""
    paths = [str(p) for p in (attachments or []) if str(p or "").strip()]
    if not paths and not vault_context:
        return {"text": user_text, "image_urls": [], "image_paths": [], "vault_derived": False,
                "attached_label": "", "names": []}
    if is_native:
        await websocket.send_json({"type": "error", "message":
            f"{provider.name} runs its own tools and cannot take attachments — pick another model."})
        return None
    if getattr(provider, "is_cloud", False) and not cloud_ok:
        await websocket.send_json({
            "type": "agent:needs_confirmation",
            "session_id": session_id,
            "provider": provider.name,
            "attachments": len(paths),
            "vault_context": bool(vault_context),
            "message": (
                f"{provider.name} is a cloud model. Sending "
                + (f"{len(paths)} attachment{'s' if len(paths) != 1 else ''}" if paths else "")
                + (" and " if paths and vault_context else "")
                + ("excerpts from your notes" if vault_context else "")
                + " to it takes them off this machine."
            ),
        })
        return None
    vault_root = self.kernel.config.notes_path
    att = await asyncio.to_thread(prepare_turn, vault_root, paths, max_items=MAX_ATTACHMENTS)  # extraction blocks
    if att.has_images and not provider_reads_images(provider):
        await websocket.send_json({"type": "error", "message":
            f"{provider.name} ({getattr(provider, 'model', '') or 'this model'}) cannot read images — "
            "pick a vision model for this chat, or remove the image."})
        return None
    if att.problems:
        await websocket.send_json({"type": "agent:attachment_problems", "session_id": session_id,
                                   "problems": att.problems})
    context = await self._vault_context_block(user_text) if vault_context else ""
    text = "\n\n".join(p for p in (context, user_text, *att.file_blocks) if p)
    # Marked for storage: a later turn on a cloud model must not replay it
    # unless that message is approved too (hydrate_messages(withhold=)).
    derived = bool(att.image_paths or att.file_blocks or context)
    return {"text": text, "image_urls": att.image_urls, "image_paths": att.image_paths,
            "vault_derived": derived, "names": att.names,
            # Stands in for the typed text when a send is attachments only.
            "attached_label": ("📎 " + ", ".join(att.names)) if att.names else ""}


async def _vault_context_block(self, query: str) -> str:
    """Excerpts of the notes most relevant to ``query``, or ``""``.

    The scoped slice first (``BaseApp.scoped_retrieve`` — fast, narrow);
    otherwise the search app's whole-vault embedding search, the same pipeline
    the assistant uses. With no embeddings at all it returns "" and the chat
    still has VaultQuery / Locate / Read to look things up itself."""
    items: list[tuple[str, str]] = []
    try:
        scoped = await self.scoped_retrieve(query, top_k=VAULT_CONTEXT_NOTES, char_cap=VAULT_CONTEXT_CHARS)
        if scoped.usable:
            items = [(s.get("path", ""), s.get("text", "")) for s in scoped.snippets]
    except Exception as e:
        self.log_warn(f"vault context: scoped retrieval failed: {e}")
    if not items and self.embeddings_available:
        try:
            resp = await self.call_app("search", "_embed_search", query=query, top=VAULT_CONTEXT_NOTES)
            found = resp[0] if isinstance(resp, tuple) and resp else []
        except Exception:
            found = []
        for path in (found or [])[:VAULT_CONTEXT_NOTES]:
            try:
                from emptyos.sdk.utils import strip_frontmatter

                items.append((path, strip_frontmatter(await self.read(path))[:VAULT_CONTEXT_CHARS]))
            except Exception:
                continue
    items = [(p, t.strip()) for p, t in items if t and t.strip()]
    if not items:
        return ""
    body = "\n\n".join(f"[{Path(p).stem}] ({p})\n{t}" for p, t in items)
    return f"{VAULT_CONTEXT_HEADER}\n\n{body}"
