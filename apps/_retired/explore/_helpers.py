"""Shared module-level constants + helpers for the explore app and its mixins."""

from __future__ import annotations


DEFAULT_FOLDER = "30_Resources/Explore"


DEMO_SYMBOLS: dict[str, str] = {
    # Pad-mount distribution transformer — cylindrical tank, three bushings, base
    "transformer": (
        "<svg viewBox=\"0 0 200 240\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Pad-mount distribution transformer with three bushings</desc>"
        "<line x1=\"50\" y1=\"10\" x2=\"50\" y2=\"50\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        "<line x1=\"100\" y1=\"10\" x2=\"100\" y2=\"50\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        "<line x1=\"150\" y1=\"10\" x2=\"150\" y2=\"50\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        "<circle cx=\"50\" cy=\"10\" r=\"8\" fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<circle cx=\"100\" cy=\"10\" r=\"8\" fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<circle cx=\"150\" cy=\"10\" r=\"8\" fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<rect x=\"20\" y=\"50\" width=\"160\" height=\"160\" rx=\"6\" fill=\"#8a7456\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<line x1=\"30\" y1=\"70\" x2=\"30\" y2=\"190\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"170\" y1=\"70\" x2=\"170\" y2=\"190\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"60\" y1=\"110\" x2=\"140\" y2=\"110\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"60\" y1=\"140\" x2=\"140\" y2=\"140\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"60\" y1=\"170\" x2=\"140\" y2=\"170\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<rect x=\"10\" y=\"210\" width=\"180\" height=\"20\" fill=\"#6f5d3f\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "</svg>"
    ),
    # Lattice transmission tower — H-frame
    "transmission-tower": (
        "<svg viewBox=\"0 0 180 280\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Lattice steel transmission tower with two crossarms</desc>"
        "<polygon points=\"30,260 60,40 120,40 150,260\" fill=\"none\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        "<line x1=\"30\" y1=\"260\" x2=\"150\" y2=\"40\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"150\" y1=\"260\" x2=\"30\" y2=\"40\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"45\" y1=\"150\" x2=\"135\" y2=\"150\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"55\" y1=\"100\" x2=\"125\" y2=\"100\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"38\" y1=\"200\" x2=\"142\" y2=\"200\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<rect x=\"10\" y=\"60\" width=\"160\" height=\"6\" fill=\"#6f5d3f\" stroke=\"#2c2722\"/>"
        "<rect x=\"30\" y=\"30\" width=\"120\" height=\"6\" fill=\"#6f5d3f\" stroke=\"#2c2722\"/>"
        "<circle cx=\"15\" cy=\"63\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "<circle cx=\"90\" cy=\"63\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "<circle cx=\"165\" cy=\"63\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "<circle cx=\"35\" cy=\"33\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "<circle cx=\"145\" cy=\"33\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "</svg>"
    ),
    # XLPE underground cable — concentric layers cross-section
    "cable-cross-section": (
        "<svg viewBox=\"0 0 200 200\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>XLPE MV/HV cable cross-section: conductor, semicon, XLPE, screen, sheath, jacket</desc>"
        "<circle cx=\"100\" cy=\"100\" r=\"95\" fill=\"#2c2722\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"82\" fill=\"#cdb88c\" opacity=\"0.6\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"70\" fill=\"#8a7456\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"56\" fill=\"#fdfaf2\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"44\" fill=\"#6f5d3f\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"30\" fill=\"#b08968\"/>"
        "<g fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"0.5\">"
        "<circle cx=\"100\" cy=\"80\" r=\"4\"/><circle cx=\"110\" cy=\"86\" r=\"4\"/>"
        "<circle cx=\"114\" cy=\"96\" r=\"4\"/><circle cx=\"110\" cy=\"108\" r=\"4\"/>"
        "<circle cx=\"100\" cy=\"114\" r=\"4\"/><circle cx=\"90\" cy=\"108\" r=\"4\"/>"
        "<circle cx=\"86\" cy=\"96\" r=\"4\"/><circle cx=\"90\" cy=\"86\" r=\"4\"/>"
        "<circle cx=\"100\" cy=\"96\" r=\"4\"/>"
        "</g></svg>"
    ),
    # Solar PV panel — grid of cells
    "solar-panel": (
        "<svg viewBox=\"0 0 220 140\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Photovoltaic solar panel with cell grid</desc>"
        "<rect x=\"10\" y=\"10\" width=\"200\" height=\"120\" fill=\"#2c2722\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        + "".join(
            f"<rect x='{15 + (col * 32)}' y='{15 + (row * 28)}' "
            f"width='30' height='26' fill='#3a4f6b' stroke='#1a1a1a' stroke-width='0.5'/>"
            for row in range(4) for col in range(6)
        )
        + "</svg>"
    ),
    # Wind turbine — tower + 3 blades
    "wind-turbine": (
        "<svg viewBox=\"0 0 200 280\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Horizontal-axis wind turbine — tower, nacelle, three blades</desc>"
        "<polygon points=\"95,260 105,260 102,80 98,80\" fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<rect x=\"85\" y=\"70\" width=\"30\" height=\"20\" rx=\"4\" fill=\"#8a7456\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<circle cx=\"100\" cy=\"80\" r=\"6\" fill=\"#2c2722\"/>"
        "<path d=\"M 100 80 L 100 10 L 95 14 Z\" fill=\"#fdfaf2\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<path d=\"M 100 80 L 165 115 L 160 120 Z\" fill=\"#fdfaf2\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<path d=\"M 100 80 L 35 115 L 40 120 Z\" fill=\"#fdfaf2\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "</svg>"
    ),
    # Lightning bolt — high-voltage / fault icon
    "lightning-bolt": (
        "<svg viewBox=\"0 0 100 160\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Lightning bolt — high voltage / fault indicator</desc>"
        "<polygon points=\"55,5 15,90 45,90 30,155 85,55 55,55 75,5\" "
        "fill=\"#d4a73c\" stroke=\"#2c2722\" stroke-width=\"2\" stroke-linejoin=\"round\"/>"
        "</svg>"
    ),
}


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


def _refine_anchor_user_text(callouts: list[dict]) -> str:
    return (
        "Image attached. For each label below, return its (x, y) anchor.\n\n"
        + "\n".join(
            f"{i}. {(c or {}).get('label', '')}"
            for i, c in enumerate(callouts)
        )
    )


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
