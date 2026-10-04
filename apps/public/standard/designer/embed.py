"""designer — viz-embed expansion (the "help of viz" mechanism).

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
post-generation pass that turns the model's empty `data-viz` placeholder divs
into real, inline-baked viz artifacts. This is the one place designer touches
the `artifact` capability (provided by the viz app).

Mechanism (LLM-placed, single-prompt, baked-standalone):
  1. The page LLM leaves `<div data-viz="chart" data-brief="..." data-height="360"></div>`
     where a rich element belongs (see DESIGNER_BASE_SYSTEM in shared.py).
  2. For each (capped at max_embeds), call `self.artifact(brief, shape=...)` →
     a vault-relative path to the viz artifact's scene.html.
  3. Read that HTML, escape it, and replace the placeholder with
     `<iframe srcdoc="...escaped...">`. srcdoc inline-baking keeps the saved
     page genuinely standalone (no daemon dependency) and isolates the
     artifact's CSS/JS from the page.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach (only self.artifact + self.vault_root).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from emptyos.sdk.viz_embed import (
    fallback_block,
    id_from_artifact_path,
    parse_html_attrs,
    px_height,
    srcdoc_iframe,
)

from .shared import ALLOWED_VIZ_SHAPES, DEFAULT_EMBED_HEIGHT

if TYPE_CHECKING:
    from .app import DesignerApp  # noqa: F401 — for type hints only


# ─── Bind to DesignerApp class as ────────────────────────────────────
#   _expand_viz_embeds = _embed._expand_viz_embeds
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────
#
# The attribute parser, px-height normalizer, artifact-id extractor, the
# sandboxed-iframe bake, and the fallback block all moved to
# emptyos.sdk.viz_embed (CLAUDE.md rule 9 — the note artifact-embed feature is
# the second consumer). designer keeps only the placeholder-div regex + the
# generate-then-bake orchestration below.


# An EMPTY <div ...></div> — we filter to those carrying data-viz after parsing
# attrs (genuinely empty divs without data-viz are left untouched).
_DIV_RE = re.compile(r"<div\b([^>]*)>\s*</div>", re.IGNORECASE)


async def _expand_viz_embeds(self, html_doc: str) -> tuple[str, list[str]]:
    """Replace `data-viz` placeholder divs with inline-baked viz iframes.

    Returns (rewritten_html, [viz_artifact_ids]). Never raises — a failed
    embed degrades to a styled fallback block so the page still renders.
    """
    max_embeds = int(self.setting_or_config("designer.max_embeds", 4, config_key="max_embeds") or 4)
    ids: list[str] = []
    count = 0
    out: list[str] = []
    last = 0

    for m in _DIV_RE.finditer(html_doc):
        attrs = parse_html_attrs(m.group(1))
        shape = (attrs.get("data-viz") or "").strip().lower()
        if not shape:
            continue  # an ordinary empty <div> — leave it alone
        out.append(html_doc[last:m.start()])
        last = m.end()

        brief = (attrs.get("data-brief") or "").strip() or "diagram"
        height = px_height(attrs.get("data-height") or "", default=DEFAULT_EMBED_HEIGHT)

        if shape not in ALLOWED_VIZ_SHAPES:
            out.append(fallback_block(shape, brief, f"unsupported element type '{shape}'"))
            continue
        if count >= max_embeds:
            out.append(fallback_block(shape, brief, f"embed limit ({max_embeds}) reached"))
            continue

        try:
            path = await self.artifact(brief, shape=shape)
        except Exception:
            path = ""
        if not path:
            out.append(fallback_block(shape, brief, "viz unavailable — install/enable the viz app"))
            continue

        try:
            content = (self.vault_root / path).read_text(encoding="utf-8")
        except Exception:
            content = ""
        if not content:
            out.append(fallback_block(shape, brief, "generated artifact could not be read"))
            continue

        vid = id_from_artifact_path(path)
        if vid:
            ids.append(vid)
        out.append(srcdoc_iframe(content, height=height, title=brief))
        count += 1

    out.append(html_doc[last:])
    return "".join(out), ids
