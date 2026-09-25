"""viz — KB few-shot pattern resolution (viz-specific wrapper).

Thin layer over the shared injector (`emptyos.sdk.pattern_examples`): owns the
per-shape accepted-language map (`_SHAPE_LANGS`) + the viz intro, and the
`api_examples` listing route. The resolve / extract / build logic moved to the
SDK when cad became the second consumer of curated few-shot patterns
(CLAUDE.md rule 9) — viz now delegates so there's one implementation.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach (only self.call_app for the KB app).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from emptyos.sdk import web_route
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ────────────────────────────────
#   _SHAPE_LANGS            = _examples._SHAPE_LANGS
#   _build_examples_block   = _examples._build_examples_block
#   api_examples            = _examples.api_examples
# Adding a new method here? Add a matching binding line in app.py.
# (resolve/extract/build moved to emptyos.sdk.pattern_examples — rule 9.)
# ─────────────────────────────────────────────────────────────────────


# Languages we accept inside a pattern note's fenced code blocks per shape.
# We accept the canonical web language for the shape plus the umbrella
# `html` tag for blocks that wrap the whole artifact. Pattern authors who
# mark fences with `js` get treated as `javascript`.
_SHAPE_LANGS = {
    "3d-scene":       {"javascript", "js", "html"},
    "immersive-scene":{"javascript", "js", "html", "glsl"},
    "svg-diagram":    {"svg", "html", "javascript", "js"},
    "schematic":      {"svg", "html"},
    "network-graph":  {"javascript", "js", "html"},
    "chart":          {"javascript", "js", "html"},
    "anim-explainer": {"javascript", "js", "html", "css"},
    "slide-deck":     {"html"},
    "math-explainer": {"latex", "tex", "html", "javascript", "js"},
    "mermaid":        {"mermaid", "html"},
}


# Preserves the original viz wording (construction style + lighting + dimension
# labelling) so generated artifacts read identically after the SDK extraction.
_VIZ_INTRO = (
    "Treat the following as canonical scaffolding for this shape — match the "
    "construction style, lighting, dimension labelling, and library choices "
    "unless the brief explicitly diverges. Do NOT copy verbatim; adapt to the "
    "brief's dimensions and intent."
)


async def _build_examples_block(self, examples: list[str], shape: str) -> tuple[str, list[str]]:
    """Few-shot block for the named pattern notes, filtered to this shape's langs.

    Returns ``(block, resolved_slugs)``. The second half is what `_persist`
    writes to the record's ``related:`` — only patterns the model was actually
    shown, never merely the ones requested.
    """
    from emptyos.sdk.pattern_examples import resolve_pattern_examples_detail
    langs = self._SHAPE_LANGS.get(shape, {"javascript", "js", "html"})
    return await resolve_pattern_examples_detail(self, examples, langs=langs, intro=_VIZ_INTRO)


@web_route("GET", "/api/examples")
async def api_examples(self, request) -> dict:
    """List pattern notes available for KB few-shot injection.

    First queries the KB app for `kind: pattern` notes; falls back to a
    direct vault glob if KB isn't installed. Returns a flat list of
    `{slug, title, domain, topic}` ready for a multi-select.
    """
    items: list[dict] = []
    seen: set[str] = set()
    try:
        res = await self.call_app("kb", "list_notes", kind="pattern")
    except Exception:
        res = None
    if isinstance(res, dict) and isinstance(res.get("notes"), list):
        for n in res["notes"]:
            slug = (n.get("slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            items.append({
                "slug": slug,
                "title": n.get("title") or slug,
                "domain": n.get("domain", ""),
                "topic": n.get("topic", ""),
            })
    for cand in self.vault_root.glob("30_Resources/KB/**/patterns/*.md"):
        slug = cand.stem
        if slug in seen:
            continue
        seen.add(slug)
        parts = cand.parts
        domain = ""
        if "KB" in parts:
            i = parts.index("KB")
            if i + 1 < len(parts):
                domain = parts[i + 1]
        items.append({"slug": slug, "title": slug, "domain": domain, "topic": ""})
    items.sort(key=lambda r: (r.get("domain", ""), r.get("slug", "")))
    return {"items": items, "count": len(items)}
