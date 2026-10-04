"""Flipbook — LLM-generated annotated-SVG explainer pages ("label any object").

The shared generation engine behind the **kb** app's Flipbook builder (consumer
#1) and the **condition-map** app's Illustrated view (consumer #2). Given a topic
(or an object + a predefined callout set), it produces a self-contained SVG diagram
plus a ``callouts`` array, where each callout is pinned to a real SVG element via
``data-anchor="N"`` so a frontend overlay can draw leader lines at real features.

Lineage: this logic began in the retired ``explore`` app, was ported into ``kb``
during the kb⇔explore merge, and is extracted here once a second consumer
(condition-map) appeared — per CLAUDE.md rule 9 (extract on the *second* caller).

Pure + think-injected (mirrors ``html_element_edit.py`` / ``compose.py``):
``generate_svg_page(..., think_fn=...)`` takes an async closure ``(system, user) ->
str`` so it unit-tests without a daemon. The consuming app owns everything impure —
vault I/O, the ``@web_route`` endpoints, image rendering (draw capability), symbol
files, and corpus integration. This module owns ONLY the prompts + the
JSON-parse/shape-assembly step. No ``self``, no kernel, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from emptyos.sdk.utils import parse_llm_json


def _parsed_dict(raw) -> dict:
    """``parse_llm_json`` raises on unparseable text when no fallback is given;
    the generators must never raise, so always pass a fallback and coerce a
    non-dict (e.g. a JSON array) to ``{}``."""
    data = parse_llm_json(raw, fallback={})
    return data if isinstance(data, dict) else {}

# ── Prompts (moved verbatim from kb/flipbook_prompts.py) ─────────────────────

SYSTEM_PROMPT = """You are a visual encyclopedia. Given a topic, you produce a single \
JSON object describing one explanatory page.

The page should be a labeled diagram with callouts — like an isometric \
infographic in a high-quality science textbook. Aim for 4-7 callouts.

Output ONLY a JSON object with this shape:
{
  "title": "Concise topic title",
  "subtitle": "One short sentence framing the page",
  "svg": "<svg viewBox='0 0 800 500' xmlns='http://www.w3.org/2000/svg'>...</svg>",
  "callouts": [
    {"label": "Layer name", "body": "1-2 sentence explanation", "x": 35, "y": 50}
  ],
  "caption": "One-line takeaway shown at the bottom of the page"
}

Rules:
- The SVG must be valid, self-contained, viewBox 0 0 800 500.
- **Fill the entire viewBox.** Use the full 0-800 horizontal and 0-500 vertical \
range. The diagram should reach close to all four edges (within ~20px padding). \
Do not leave large empty regions. If the subject is a single object, scale it \
up to dominate the frame; if a process, spread the stages across the width.
- Use simple shapes (circle, rect, ellipse, path, line) with `fill` and `stroke`. \
No external images, no `<image>`, no `<foreignObject>`.
- Use a warm muted palette: soft cream backgrounds (#f5efe6), \
muted earth tones for fills (#b08968, #8a7456, #6f5d3f, #cdb88c), \
dark slate strokes (#2c2722).
- Do NOT put text labels inside the SVG. All labels go in the `callouts` array.
- Each callout's (x, y) is a fallback percentage anchor (0-100), used only \
if no SVG element is tagged for it.
- **Tag the SVG elements that each callout describes.** For each callout at \
index N (0-based), add the attribute `data-anchor="N"` to the single SVG \
element that visually represents that callout's subject. Example: if callout 0 \
is "Conductor", put `data-anchor="0"` on the inner cable circle. This makes \
leader lines point at real features, not guessed coordinates. Every callout \
should have exactly one tagged element.
- **Use inset detail views when scale matters.** When part of the subject \
needs a different scale or perspective to be legible (e.g., a zoomed cross-\
section, a buried-cable trench cutaway, an exploded mechanism, a map detail), \
draw a small framed inset within the main viewBox. Render it as a `<g>` group \
with a thin border rectangle and a small label inside (text inside this kind \
of inset is fine — it's a label, not a callout). Place insets in unused corners \
(typically bottom-right, ~180-220px wide). Use them sparingly — at most 1-2 \
per page, only when the main view can't show the detail at adequate scale.
- Return ONLY the JSON object. No prose, no fences."""


PROMPT_TEMPLATE = """Create an explanatory page about: {topic}

{context}

Return the JSON object now."""


PEEK_SYSTEM_PROMPT = """You write short popover-sized detail cards. \
Output ONLY a JSON object: \
{"summary": "1-2 sentence framing", "facts": ["fact 1", "fact 2", "fact 3", "fact 4"]}. \
No prose, no fences. Each fact is a complete short sentence. Aim for 3-5 facts.

Do NOT:
- prefix with conversational filler ("Sure," "Here's…")
- include URLs (TTS reads them literally)
- include markdown formatting in summary or facts
- exceed one sentence per fact"""


PEEK_PROMPT_TEMPLATE = """Subject: {label}
Context: This is one part of '{parent}'.
Write a short detail card explaining what this is and why it matters."""


REFINE_ANCHOR_SYSTEM_PROMPT = """You are a precise visual-grounding assistant. \
Look at the image and locate each labeled feature. Output ONLY a JSON object: \
{"anchors": [{"idx": <int>, "x": <0-100>, "y": <0-100>}, ...]}. \
(x, y) is the centre of the named feature, as a percentage of the image \
width and height. If a feature is not visible, omit it.

Do NOT:
- include explanatory prose around the JSON
- guess coordinates for features you can't see
- return values outside 0-100"""


IMAGE_SYSTEM_PROMPT = """You produce visual-explanation pages where the \
illustration is generated by an image model (FLUX/SDXL).

Output ONLY a JSON object:
{
  "title": "Concise topic title",
  "subtitle": "One short sentence framing the page",
  "image_prompt": "Detailed image prompt describing the visual scene only",
  "callouts": [
    {"label": "...", "body": "1-2 sentence explanation", "x": 35, "y": 50}
  ],
  "caption": "One-line takeaway"
}

Rules:
- image_prompt: describe the subject visually for an image model — clean \
illustration style, neutral cream background (#f5efe6), warm muted earth-tone \
palette, focused subject filling the frame. End the prompt with: \
"no text, no labels, no annotations, no watermarks".
- For iconic objects (instruments, vehicles, animals, anatomy), specify the \
recognizable silhouette explicitly ("acoustic guitar with figure-8 body, \
6 strings running from headstock to bridge, sound hole in centre").
- (x, y) is your best estimate of where each callout's subject will appear \
in the image, as percentages 0-100.
- Aim for 4-7 callouts.
- Return ONLY the JSON object. No prose, no fences."""


# Default placeholder when the model returns no SVG. Equal to kb's historical
# ``_fallback_svg()`` so a consumer that passes nothing stays byte-identical.
DEFAULT_FALLBACK_SVG = (
    "<svg viewBox='0 0 800 500' xmlns='http://www.w3.org/2000/svg'>"
    "<rect width='800' height='500' fill='#f5efe6'/>"
    "<text x='400' y='250' text-anchor='middle' fill='#8a7456' "
    "font-family='serif' font-size='24'>"
    "(illustration unavailable — try again)</text></svg>"
)


# ── Result type (mirrors ElementEdit) ────────────────────────────────────────

@dataclass
class FlipbookPage:
    """One generated SVG explainer page. Orchestration fields
    (mode/breadcrumb/saved/...) are NOT here — the consuming app stamps those so
    its on-the-wire dict stays byte-identical to the pre-extraction shape."""

    ok: bool
    title: str = ""
    subtitle: str = ""
    svg: str = ""               # raw LLM svg — NOT symbol-injected (consumer injects)
    callouts: list = field(default_factory=list)
    caption: str = ""
    used_fallback: bool = False
    error: str = ""


# ── Pure prompt-hint builders ────────────────────────────────────────────────

def _build_callouts_hint(callouts_in, topic: str) -> str:
    """Enforced-callout-set hint. ``callouts_in`` is ``[{label, body?}]`` (already
    resolved — the consumer maps its slugs/records to labels). With body absent
    this is byte-identical to kb's historical ``callouts_hint``; with body present
    (condition-map) each line carries ``label — body`` to seed the callout text."""
    items = [
        c for c in (callouts_in or [])
        if isinstance(c, dict) and str(c.get("label") or "").strip()
    ]
    if not items:
        return ""
    lines = []
    for i, c in enumerate(items):
        label = str(c["label"]).strip()
        body = str(c.get("body") or "").strip()
        lines.append(f"  {i+1}. {label} — {body}" if body else f"  {i+1}. {label}")
    return (
        "\n\nThe callouts MUST be exactly these concepts (one "
        "callout per item, in this order, using these labels):\n"
        + "\n".join(lines)
        + "\n\nDo not invent additional callouts. Do not drop any "
        "of the required ones. The SVG should visually represent "
        f"'{topic}' with each listed concept marked as a distinct "
        "feature (use data-anchor=\"<idx>\" on the SVG element "
        "depicting each one)."
    )


def _build_symbol_hint(symbol_catalog) -> str:
    """Symbol-library hint. ``symbol_catalog`` is ``[{id, name, description}]``
    (text-only — the consumer reads the SVG files; this module never touches
    vault content). Byte-identical to kb's historical ``symbol_hint``."""
    symbols = [
        s for s in (symbol_catalog or [])
        if isinstance(s, dict) and s.get("id")
    ]
    if not symbols:
        return ""
    catalog = "\n".join(
        f"- id='{s['id']}'  ({s.get('name', '')}"
        + (f": {s['description']}" if s.get("description") else "")
        + ")"
        for s in symbols
    )
    return (
        "\n\nA symbol library is available. Reference any symbol by "
        "id with: `<use href='#<id>' x='..' y='..' width='..' "
        "height='..' data-anchor='<callout-idx>'/>`. "
        "Prefer symbols for shapes you'd otherwise draw from scratch. "
        "Available symbols:\n" + catalog
    )


def refine_anchor_user_text(callouts) -> str:
    """User-message text for the vision anchor-refine pass (one line per callout)."""
    return (
        "Image attached. For each label below, return its (x, y) anchor.\n\n"
        + "\n".join(
            f"{i}. {(c or {}).get('label', '')}"
            for i, c in enumerate(callouts)
        )
    )


# ── Pure async generators (think_fn-injected) ────────────────────────────────

async def generate_svg_page(
    topic,
    *,
    context: str = "",
    callouts_in=None,
    symbol_catalog=None,
    think_fn,
    fallback_svg: str = "",
) -> FlipbookPage:
    """Generate an annotated SVG explainer page for ``topic``.

    ``callouts_in`` ([{label, body?}]) enforces an exact callout set (kb passes
    resolved slug labels; condition-map passes its conditions+levers). ``think_fn``
    is an async ``(system, user) -> str`` closure. Never raises — returns
    ``ok=False`` on a think error and falls back to ``fallback_svg`` /
    ``DEFAULT_FALLBACK_SVG`` when the model returns no svg. Does NOT inject the
    symbol ``<defs>`` (that reads vault files) — the consumer does that after."""
    prompt = (
        PROMPT_TEMPLATE.format(topic=topic, context=context)
        + _build_callouts_hint(callouts_in, topic)
        + _build_symbol_hint(symbol_catalog)
    )
    fb = fallback_svg or DEFAULT_FALLBACK_SVG
    try:
        raw = await think_fn(SYSTEM_PROMPT, prompt)
    except Exception as e:
        return FlipbookPage(
            ok=False, error=f"think failed: {e}", title=topic,
            svg=fb, used_fallback=True,
        )
    data = _parsed_dict(raw)
    svg = data.get("svg") or fb
    return FlipbookPage(
        ok=True,
        title=data.get("title") or topic,
        subtitle=data.get("subtitle") or "",
        svg=svg,
        callouts=data.get("callouts") or [],
        caption=data.get("caption") or "",
        used_fallback=not data.get("svg"),
    )


async def generate_image_meta(topic, *, context: str = "", think_fn) -> dict:
    """Image-mode metadata prefix: title/subtitle/image_prompt/callouts/caption.
    Rendering (draw capability) stays in the consumer. Never raises."""
    prompt = PROMPT_TEMPLATE.format(topic=topic, context=context)
    try:
        raw = await think_fn(IMAGE_SYSTEM_PROMPT, prompt)
    except Exception as e:
        return {
            "ok": False, "error": f"think failed: {e}", "title": topic,
            "subtitle": "", "image_prompt": topic, "callouts": [], "caption": "",
        }
    data = _parsed_dict(raw)
    return {
        "ok": True,
        "title": data.get("title") or topic,
        "subtitle": data.get("subtitle") or "",
        "image_prompt": data.get("image_prompt") or topic,
        "callouts": data.get("callouts") or [],
        "caption": data.get("caption") or "",
    }


async def generate_peek(label, *, parent: str = "", think_fn) -> dict:
    """Popover detail card for one callout → ``{summary, facts}``. The consumer
    owns the temperature knob via its think_fn closure. Never raises."""
    prompt = PEEK_PROMPT_TEMPLATE.format(label=label, parent=parent)
    try:
        raw = await think_fn(PEEK_SYSTEM_PROMPT, prompt)
    except Exception:
        return {"summary": "", "facts": []}
    data = _parsed_dict(raw)
    return {"summary": data.get("summary") or "", "facts": data.get("facts") or []}


# ── Pure vision-refine helpers (no think_fn — bookends around the provider call) ─

def build_refine_messages(callouts, png_b64: str) -> list[dict]:
    """Chat messages for the vision anchor-refine pass. ``png_b64`` is a base64
    string — the consumer does the byte→base64 encode (this module never touches
    bytes)."""
    return [
        {"role": "system", "content": REFINE_ANCHOR_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": refine_anchor_user_text(callouts)},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{png_b64}",
            }},
        ]},
    ]


def parse_refine_anchors(anchors, callout_count: int) -> list[dict]:
    """Validate + clamp a list of ``{idx, x, y}`` anchors (already JSON-parsed):
    drop non-numeric / out-of-range entries, round x/y to 1dp. Pure."""
    out = []
    for a in anchors or []:
        try:
            idx = int(a.get("idx"))
            x = float(a.get("x"))
            y = float(a.get("y"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= idx < callout_count and 0 <= x <= 100 and 0 <= y <= 100:
            out.append({"idx": idx, "x": round(x, 1), "y": round(y, 1)})
    return out
