"""kb — receive a generated figure into a note.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
asset path a producing app writes to, and the embed that puts the figure in
the note body.

This is the receiving half of viz's static-figure export. **kb owns these two
answers, not viz** — `notes_dir` is kb's own config key, and the `![[…]]`
wikilink is kb's idiom. A producing app asks; it never assumes.

Note what the wikilink does and does not buy. It makes the figure *render* —
in the vault viewer, in the KB app — which a bare path would not. It does NOT
create a vault-graph edge: the graph resolves links against indexed notes, and
the index is markdown-only, so an `.svg` target is not a node. Measured, not
assumed. The artifact↔note edge comes from the producer's `used_in:`
frontmatter instead (a `vault-graph` ref field), which is the only direction
that resolves — an artifact record is always a fixed `record.md` under a
per-id folder, so nothing can address it by slug.

The asset path deliberately matches what `flipbook_io._asset_path_for` already
writes — one `_assets/` convention per corpus, whether the SVG came from the
flipbook generator or from a viz artifact.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._notes_dir + self._note_path (notes).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk.utils import require_path_segment

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   figure_asset_path = _figures.figure_asset_path
#   attach_figure     = _figures.attach_figure
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

FIGURE_SECTION = "Diagram"


def figure_asset_path(self, slug: str = "") -> str:
    """Vault-relative `.svg` path for this note's figure, or "".

    Public verb — a producing app (viz) calls this to learn where to write,
    so the location stays kb's business.

    Returns "" when the note does not exist, which is what stops the producer
    writing the asset at all. Without that check a typo'd slug silently
    succeeds: an orphan SVG+PNG lands in `_assets/` for a note nobody will
    ever open, and the artifact records `used_in: kb/<typo>` — provenance
    asserting a relationship that does not exist. The embed failing later is
    too late; by then both files are on disk.
    """
    s = (slug or "").strip()
    if not s:
        return ""
    try:
        s = require_path_segment(s, "slug")
    except ValueError:
        return ""
    if not (self.vault_root / self._note_path(s)).exists():
        return ""
    return f"{self._notes_dir()}/_assets/{s}.svg"


async def attach_figure(
    self, slug: str = "", asset: str = "", png: str = "", alt: str = "", viz_id: str = "",
) -> dict:
    """Embed an already-written figure into note `slug`.

    Writes the `![[…]]` embed into a `## Diagram` section and records
    `figures:` frontmatter. Idempotent — re-attaching the same asset rewrites
    the section rather than stacking duplicates.
    """
    s = (slug or "").strip()
    rel = (asset or "").strip()
    if not s or not rel:
        return {"ok": False, "error": "slug and asset are required"}
    try:
        s = require_path_segment(s, "slug")
    except ValueError as exc:
        return {"ok": False, "error": f"invalid slug: {exc}"}

    note_rel = self._note_path(s)
    if not (self.vault_root / note_rel).exists():
        return {"ok": False, "error": f"no kb note '{s}'"}

    # Serialize the read-modify-write: `vault_set_section` and `vault_update`
    # both read then write the same note, and this coroutine awaits in between,
    # so a concurrent writer could land between them and lose one of the two.
    # note_lock is kernel-wide by normalized path, so it also excludes the
    # producing app writing the same note (CLAUDE.md § vault RMW races).
    async with self.note_lock(note_rel):
        caption = (alt or "").strip()
        body = f"![[{rel}]]" + (f"\n\n*{caption}*" if caption else "")
        self.vault_set_section(note_rel, FIGURE_SECTION, body)

        props = self.vault_get_properties(note_rel) or {}
        figures = [f for f in (props.get("figures") or []) if isinstance(f, str)]
        marker = (viz_id or "").strip()
        if marker and marker not in figures:
            figures.append(marker)
            self.vault_update(note_rel, {"figures": figures})

    # Emit outside the lock so a handler can call back through the app
    # without deadlocking on the same note.
    await self.emit("kb:figure_attached", {"slug": s, "asset": rel, "viz_id": marker})
    # `embedded: True` — unlike publish, kb really does place the figure:
    # a note has one canonical `## Diagram` slot, so there is no editorial
    # placement decision to take away from the author.
    return {"ok": True, "embedded": True, "slug": s, "note": note_rel,
            "asset": rel, "png": png}
