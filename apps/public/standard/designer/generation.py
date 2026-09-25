"""designer — single-shot LLM->styled-page core + persistence.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
non-streaming generation pipeline — design-system few-shot injection, the think
call, size/truncation validation, the viz-embed pass, vault persistence
(page.html + record.md), and the public `generate` verb. Owns the vault path
helpers.

Mirrors apps/public/standard/viz/generation.py, adapted: a single page archetype
(no shape presets — the design system is the variable), an async system builder
(few-shot resolution needs `await self.call_app`), and a post-generation embed
pass via embed.py.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._expand_viz_embeds (embed) for viz baking.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .shared import (
    DESIGNER_BASE_SYSTEM,
    DESIGNER_MIN_ABILITY,
    DESIGNER_STYLE_INTRO,
    _artifact_title,
    _extract_html,
    _looks_like_html,
    _looks_truncated,
    _new_id,
    _now_iso,
)

if TYPE_CHECKING:
    from .app import DesignerApp  # noqa: F401 — for type hints only


# ─── Bind to DesignerApp class as ────────────────────────────────────
#   _outputs_root   = _generation._outputs_root
#   _record_dir     = _generation._record_dir
#   _rel_html       = _generation._rel_html
#   _rel_record     = _generation._rel_record
#   _build_style_block = _generation._build_style_block
#   _build_system   = _generation._build_system
#   _think_html     = _generation._think_html
#   _check_size     = _generation._check_size
#   _reject_reason  = _generation._reject_reason
#   _persist        = _generation._persist
#   generate        = _generation.generate
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _outputs_root(self) -> Path:
    rel = self.vault_config("path", "30_Resources/EmptyOS/designer") + "/outputs"
    return self.vault_root / rel


def _record_dir(self, rid: str) -> Path:
    return self._outputs_root() / rid


def _rel_html(self, rid: str) -> str:
    base = self.vault_config("path", "30_Resources/EmptyOS/designer")
    return f"{base}/outputs/{rid}/page.html"


def _rel_record(self, rid: str) -> str:
    base = self.vault_config("path", "30_Resources/EmptyOS/designer")
    return f"{base}/outputs/{rid}/record.md"


async def _build_style_block(self, style: str) -> str:
    """Few-shot 'Reference design system' block for a `kind: pattern` KB note.

    Pulls the note's fenced css/html blocks via the shared injector — the same
    path viz uses, just filtered to design languages.
    """
    if not style:
        return ""
    from emptyos.sdk.pattern_examples import resolve_pattern_examples

    return await resolve_pattern_examples(
        self,
        [style],
        langs={"css", "html"},
        intro=DESIGNER_STYLE_INTRO,
        heading="Reference design system",
    )


async def _build_system(self, style: str | None) -> str:
    system = DESIGNER_BASE_SYSTEM
    block = await self._build_style_block(style or "")
    if block:
        system = system + block
    return system


async def _think_html(self, system: str, user: str) -> str:
    """One think call, returns cleaned HTML string.

    max_tokens=8192 matches viz: enough for a ~24 KB page; higher risks a
    provider timeout before the response lands. The truncation guard
    (_looks_truncated) is the safety net if the ceiling bites.
    """
    domain = self.app_config("think_domain", "code")
    raw = await self.think(
        user,
        system=system,
        domain=domain,
        temperature=0.5,
        max_tokens=8192,
        min_ability=DESIGNER_MIN_ABILITY,
    )
    text = raw if isinstance(raw, str) else str(raw)
    return _extract_html(text)


def _check_size(self, html: str) -> tuple[bool, str]:
    max_kb = int(self.app_config("max_html_kb", 384) or 384)
    size_kb = len(html.encode("utf-8")) / 1024
    if size_kb > max_kb:
        return False, f"HTML size {size_kb:.1f} KB exceeds limit {max_kb} KB"
    return True, ""


def _reject_reason(self, html: str) -> str:
    """Return "" if `html` is a saveable page, else a human error reason."""
    if not _looks_like_html(html):
        return "LLM output does not look like HTML"
    truncated, why = _looks_truncated(html)
    if truncated:
        return f"LLM output truncated — refusing to save. {why}. Try a shorter brief or regenerate."
    ok, why = self._check_size(html)
    if not ok:
        return why
    return ""


async def _persist(
    self, rid: str, html: str, prompt: str, style: str, embeds: list[str], *,
    is_update: bool, source: str = "",
) -> dict:
    """Write page.html + record.md, return metadata dict.

    `source` is an optional origin the caller wants recorded (a vault-relative
    note path, or an app id for a programmatic caller). It lands in a
    ``vault-graph`` ref field so the page points back at what produced it.
    """
    record_dir = self._record_dir(rid)
    record_dir.mkdir(parents=True, exist_ok=True)

    # Stamp data-eos-el anchors when the element-edit loop is on (no-op + saved
    # output unchanged when off). Single write choke-point → both generate and
    # iterate produce anchored pages. See anchors.py / .claude/rules + the
    # element-anchored edit loop in editing.py.
    html = self._maybe_anchor(html)

    html_path = record_dir / "page.html"
    html_path.write_text(html, encoding="utf-8")

    record_path = record_dir / "record.md"
    now = _now_iso()
    existing: dict = {}
    if is_update and record_path.exists():
        existing = self.vault_get_properties(self._rel_record(rid)) or {}
        created = existing.get("created", now)
        history = existing.get("history", []) or []
        history.append({"ts": now, "prompt": prompt})
        display_prompt = existing.get("prompt") or prompt
        if not embeds:
            embeds = existing.get("embeds", []) or []
    else:
        created = now
        history = [{"ts": now, "prompt": prompt}]
        display_prompt = prompt

    # `lifecycle` stays folder-inferred (30_Resources/ -> living): an iterate
    # rewrites THIS record in place and grows `history`, so the note keeps
    # changing rather than being a frozen snapshot.
    fm = {
        "tags": ["designer"],
        "title": _artifact_title(display_prompt, fallback=f"Designer page {rid}"),
        "designer_id": rid,
        "style": style or "",
        "prompt": display_prompt,
        "author": "ai",
        "created": created,
        "updated": now,
        "size_kb": round(len(html.encode("utf-8")) / 1024, 1),
        "embeds": embeds or [],
        "history": history,
    }
    # `style` is the design-system pattern slug that shaped this page, but
    # `style:` is not a field vault-graph walks — mirroring it into `related:`
    # is what turns the design system into an actual edge. Preserved across an
    # iterate, which passes no new provenance.
    related = [style] if style else (existing.get("related") or [])
    if related:
        fm["related"] = related
    origin = (source or "").strip() or str(existing.get("source") or "").strip()
    if origin:
        fm["source"] = origin

    style_line = f"**Design system:** {style}\n\n" if style else ""
    embed_line = f"**Embedded viz artifacts:** {', '.join(embeds)}\n\n" if embeds else ""
    body = (
        f"# {fm['title']}\n\n"
        f"**Original brief:** {display_prompt}\n\n"
        f"{style_line}{embed_line}"
        f"**Latest change:** {prompt}\n\n"
        f"[Open page.html]({self._rel_html(rid)})\n"
    )
    self.vault_create_note(self._rel_record(rid), fm, body)

    return {
        "id": rid,
        "style": style or "",
        "prompt": display_prompt,
        "created": created,
        "updated": now,
        "record_dir": str(record_dir),
        "html_path": self._rel_html(rid),
        "record_path": self._rel_record(rid),
        "size_kb": fm["size_kb"],
        "embeds": embeds or [],
    }


async def generate(
    self,
    prompt: str,
    *,
    style: str | None = None,
    embed: bool | None = None,
    source: str = "",
) -> dict:
    """Single-shot page generation. Returns the same dict shape as api_generate.

    style: a `kind: pattern` KB note slug (topic: ui-design) to inject as the
           visual law, or None/"" for a freeform aesthetic.
    embed: override the `designer.embed_viz` setting for this call.
    source: optional origin to record on the page (vault path or app id).
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt is required"}
    style = (style or "").strip()

    system = await self._build_system(style)
    html = await self._think_html(system, prompt)

    # Validate the raw page BEFORE spending viz calls on embeds.
    reason = self._reject_reason(html)
    if reason:
        return {"ok": False, "error": reason}

    embed_on = self.app_config("embed_viz", True) if embed is None else bool(embed)
    embeds: list[str] = []
    if embed_on:
        html, embeds = await self._expand_viz_embeds(html)
        # Re-check size only — structure already validated pre-embed.
        ok, why = self._check_size(html)
        if not ok:
            return {"ok": False, "error": why}

    rid = _new_id()
    meta = await self._persist(
        rid, html, prompt, style, embeds, is_update=False, source=source
    )
    await self.emit("designer:created", {"id": rid, "style": style, "embeds": embeds})
    return {"ok": True, **meta}
