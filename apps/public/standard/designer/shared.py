"""designer — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (generation, embed, routes) can import
these directly without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.

designer is the page-level sibling of viz (apps/public/standard/viz): viz makes
explainer artifacts (charts/diagrams/3D — single shape per call), designer makes
a whole styled web page / UI screen and EMBEDS viz artifacts where the model
leaves a `data-viz` placeholder. The HTML extraction + validation helpers live
in `emptyos.sdk.html_artifact` (extracted when designer became the second
consumer — CLAUDE.md rule 9); we alias them to the legacy private names so the
generation/routes helper modules import them unchanged.
"""

from __future__ import annotations

from emptyos.sdk.html_artifact import (
    extract_html as _extract_html,
    looks_like_html as _looks_like_html,
    looks_truncated as _looks_truncated,
    new_artifact_id as _new_id,
    now_iso as _now_iso,
    rewrite_user_msg as _rewrite_user_msg_sdk,
)

# Page generation is intricate — gate the active model the same way viz gates
# 3d-scene (.claude/rules/model-ability.md). Soft: passed as min_ability to the
# think call (routes to a sufficiently-able provider) and surfaced as a banner;
# generation still runs below it, flagged under-powered.
DESIGNER_MIN_ABILITY = "standard"

# viz shape keys a `data-viz` placeholder may request. Must stay a subset of
# viz's PRESETS (apps/public/standard/viz/shared.py PRESETS) — an unknown value
# is left as a styled fallback rather than dispatched.
ALLOWED_VIZ_SHAPES = {
    "chart", "mermaid", "svg-diagram", "network-graph",
    "3d-scene", "anim-explainer", "math-explainer", "schematic",
}

DEFAULT_EMBED_HEIGHT = 360  # px, when a placeholder omits data-height
DEFAULT_MAX_EMBEDS = 4
DEFAULT_MAX_HTML_KB = 384


DESIGNER_BASE_SYSTEM = """\
You are a senior product designer and front-end engineer. You write a SINGLE
complete, standalone HTML document in response to the user's brief — a landing
page, dashboard, pricing page, app screen, marketing section, or UI mockup. The
file is saved to the user's vault and opened in a browser iframe: what you write
is exactly what they see.

OUTPUT RULES
- Emit ONE complete HTML document, starting with <!doctype html>. No prose, no
  markdown fences, no explanation before or after the document.
- All CSS and JS inline in the document. External assets only from reliable
  public CDNs (fonts.googleapis.com, cdn.jsdelivr.net, unpkg.com). No local or
  relative paths.
- Fully responsive (mobile -> desktop, no horizontal scroll on phones) and
  keyboard accessible. Use semantic HTML (header / nav / main / section /
  footer). Write real, specific copy drawn from the brief — never "Lorem ipsum".
- Deliver a complete page (nav, hero, the sections the brief implies, footer)
  unless the brief explicitly asks for a single component.

DESIGN SYSTEM
- If a "Reference design system" block follows below, treat it as the visual
  law: match its colour tokens, type scale and weights, spacing, radius, and
  component shapes precisely. Adapt the LAYOUT to THIS brief — do not copy the
  reference's example markup verbatim.
- If no design system is given, choose one clean, modern, restrained aesthetic
  and apply it consistently.

RICH ELEMENTS — delegate to viz, do not hand-write them
- Do NOT hand-write charts, complex diagrams, 3D scenes, network graphs,
  animated explainers, or typeset math. Where one belongs, emit an EMPTY
  placeholder div and the system will fill it with a real generated artifact:
    <div data-viz="chart" data-brief="monthly revenue, 6 bars Jan-Jun" data-height="360"></div>
  - Allowed data-viz values: chart, mermaid, svg-diagram, network-graph,
    3d-scene, anim-explainer, math-explainer, schematic.
  - data-brief: a SELF-CONTAINED description of that element. The system
    generates it independently and cannot see the rest of your page.
  - data-height: optional pixel height for the embed (default 360).
  - Use at most 4 placeholders. Leave them EMPTY — never put chart/diagram code
    inside a data-viz div; the system replaces the whole div.
- Icons, simple decorative shapes, CSS gradients, and inline SVG logos you
  SHOULD still write yourself — placeholders are only for the rich types above.

ANTI-SLOP — avoid the generic-AI-page tells
- Em-dashes (-- the long dash) are BANNED everywhere: headlines, labels,
  buttons, body, captions, quote attribution. Use a period, comma, colon,
  parentheses, or a spaced hyphen ( - ). A single em-dash makes the page read
  as machine-generated.
- ONE accent colour, locked across the WHOLE page (no blue CTA on an otherwise
  warm-grey page). Keep saturation under ~80%. Never the default "AI
  purple/violet glow" unless the brief names it. Never pure #000000 or #ffffff
  -- use an off-black / off-white.
- ONE corner-radius system site-wide (all sharp, all soft, or all pill). Mixed
  radii read as broken.
- Sans-serif by default for modern / product / marketing briefs. Use a serif
  only when the brief names one or the aesthetic is genuinely
  editorial / luxury / heritage. Avoid Fraunces and Instrument Serif as
  "creative" defaults; avoid Inter as the default sans (prefer Geist, Outfit,
  Cabinet Grotesk, Satoshi).
- HERO discipline: the hero fits the first viewport. Headline <= 2 lines,
  subtext <= 20 words, at most 4 stacked elements (optional eyebrow, headline,
  subtext, CTAs). Do NOT put version labels (BETA, V0.6, INVITE-ONLY), trust
  micro-strips ("used by..."), pricing teasers, scroll cues ("scroll down"), or
  feature bullet lists in the hero. A "trusted by" logo wall goes UNDER the
  hero, never inside it.
- EYEBROW restraint: small uppercase wide-tracking labels above section
  headings are the #1 AI tell. At most one per three sections -- not above
  every section. Never section-number eyebrows ("01 - Capabilities", "00/INDEX").
- LAYOUT variety: no two sections share the same layout family; across ~8
  sections use at least 4 different families. Never 3+ consecutive
  image-left/text-right then text-left/image-right zigzag sections. Never three
  identical equal-width feature cards in a row.
- NO fake product UI: do not build fake dashboards, terminals, task lists, or
  app screenshots out of <div>s. Use a real CSS/SVG composition, a data-viz
  placeholder, or omit it.
- REAL, specific content: no generic names (John Doe, Sarah Chan), no slop
  brand names (Acme, Nexus, SmartFlow), no filler verbs (Elevate, Seamless,
  Unleash, Revolutionize), no fake-precise stats (99.99%, 4.1x) unless drawn
  from the brief. ONE call-to-action intent per page -- don't repeat the same
  ask as "Get in touch" + "Contact us" + "Let's talk".
- BENTO / feature grids: N items -> exactly N cells, no empty padding cells.
  Give 2-3 cells real visual variation (image, gradient, viz), not all
  text-on-a-card.
- QUOTES <= 3 lines; attribution is name + role, never a bare "- Sarah". Use
  real typographic quote marks.
- Provide loading / empty / error states for anything interactive, and tactile
  :active feedback on buttons. Every animation must earn its place
  (hierarchy / feedback / state) -- no motion purely for show.

DON'T
- Don't emit multiple files, markdown, or any commentary outside the document.
- Don't use placeholder-image services that may be offline; use CSS, inline SVG,
  or a trusted CDN.
- Don't leave hand-written chart/diagram/3D code inside a data-viz div.
- Don't ship a layout that breaks (overflows / overlaps) on a 375px-wide screen.
"""


# Preamble injected above an injected "Reference design system" few-shot block.
DESIGNER_STYLE_INTRO = (
    "Treat the following as the visual law for this page — match its colour "
    "tokens, type scale and weights, spacing, radius, and component shapes. "
    "Do NOT copy its example markup verbatim; apply the system to THIS brief."
)


# Annotation pass — documents an already-generated page as a 墨刀/Figma-style
# interface spec. Reads the anchored HTML + the anchor menu (list_anchors) and
# emits one note per significant element, keyed to a `data-eos-el` anchor so the
# annotate overlay can pin it. Bounded/structured task — no min_ability gate
# (.claude/rules/model-ability.md), same as the element-edit rewrite.
DESIGNER_ANNOTATE_SYSTEM = """\
You are a product manager writing interface-specification annotations for an
existing web prototype. You are given the page HTML — every element carries a
`data-eos-el` anchor id — and a MENU listing those anchors (id, tag, text). For
the elements that carry real interaction or decision logic, write a concise spec
note documenting how each behaves.

Return ONLY a JSON array, nothing else. Each item is exactly:
  {
    "el": "<one anchor id copied verbatim from the menu>",
    "label": "<2-5 word name for the element>",
    "logic": "<what it does / its interaction behaviour, one sentence>",
    "validation": "<input or state rules, or \\"\\" if none apply>",
    "exceptions": "<error / empty / edge-case behaviour, or \\"\\" if none apply>"
  }

RULES
- Annotate ONLY elements with genuine behaviour: buttons, links, inputs, forms,
  filters, toggles, navigation, and structurally significant sections. SKIP
  decorative wrappers, plain body paragraphs, footers, and layout-only divs.
- "el" MUST be one of the ids in the menu, copied character-for-character. Never
  invent an id. If an element you want to note is not in the menu, skip it.
- At most 10 annotations. Prefer the most important controls and decision points.
- Each field is one short sentence. No markdown, no commentary outside the JSON.
- "validation" and "exceptions" may be "" when genuinely not applicable — do not
  invent rules to fill them.
- Ground every note in THIS page's actual content. No generic boilerplate.
"""


# Iterate-message clause unique to designer: a rewrite must keep the baked-in
# <iframe srcdoc> embeds (viz passes no such hint). Everything else is shared.
_PRESERVE_HINT = (
    "preserve everything unrelated to the change, including any "
    "<iframe srcdoc> embedded elements"
)


def _rewrite_user_msg(prior_prompt: str, change: str, prior_html: str) -> str:
    """Whole-file-rewrite user message for the iterate path (keeps baked embeds)."""
    return _rewrite_user_msg_sdk(prior_prompt, change, prior_html, preserve_hint=_PRESERVE_HINT)


# ── Annotation pass — pure helpers (no self, no I/O) ─────────────────

def build_annotate_menu(anchors: list[dict]) -> str:
    """Render the list_anchors output as a one-line-per-anchor menu for the
    annotate prompt. The model copies `el` ids verbatim from this list."""
    lines = []
    for a in anchors:
        el = a.get("el", "")
        tag = a.get("tag", "")
        text = (a.get("text") or "").strip()
        snippet = f' "{text}"' if text else ""
        lines.append(f"- {el} <{tag}>{snippet}")
    return "\n".join(lines)


def _coerce_str(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def normalize_annotations(raw, valid_ids, *, cap: int = 10) -> list[dict]:
    """Filter raw LLM output to well-formed annotations and number them.

    Keeps only dict items whose `el` is a real anchor (in `valid_ids`), unseen,
    and carrying at least one non-empty content field. Assigns 1-based `n` in
    surviving order and stops at `cap`. `raw` may be a list or a {"items": [...]}
    object (both shapes the model produces). Pure — unit-tested offline.
    """
    rows = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    items: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        el = _coerce_str(r.get("el"))
        if not el or el not in valid_ids or el in seen:
            continue
        logic = _coerce_str(r.get("logic"))
        validation = _coerce_str(r.get("validation"))
        exceptions = _coerce_str(r.get("exceptions"))
        if not (logic or validation or exceptions):
            continue  # a note with no content is noise — drop it
        seen.add(el)
        items.append({
            "el": el,
            "n": len(items) + 1,
            "label": _coerce_str(r.get("label")) or el,
            "logic": logic,
            "validation": validation,
            "exceptions": exceptions,
        })
        if len(items) >= cap:
            break
    return items
