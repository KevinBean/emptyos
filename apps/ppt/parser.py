"""Pure deck parsing + rendering — no I/O, no kernel.

Extracted from app.py so the class is just orchestration. Everything here is
side-effect-free: regexes, format strings, the outline system-prompt builder,
and the standalone-HTML template. Tests can import this module directly.
"""

from __future__ import annotations

import re
from pathlib import Path

from emptyos.sdk.markdown_render import HAS_MARKDOWN

_DECK_JS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "emptyos"
    / "web"
    / "static"
    / "eos-deck.js"
)


def _load_deck_js() -> str:
    """Read the shared deck renderer once, cache for the process lifetime.

    The exporter inlines this into a standalone HTML bundle so the file
    works offline (file:// or any static host).
    """
    cached = _load_deck_js.__dict__.get("cached")
    if cached is None:
        try:
            cached = _DECK_JS_PATH.read_text(encoding="utf-8")
        except OSError:
            cached = ""
        _load_deck_js.__dict__["cached"] = cached
    return cached


# Per-element prompt fragments. The deck-outline system prompt is built
# dynamically from the user-selected element palette so the AI only knows
# about the surfaces the user actually wants to see.
_ELEMENT_FRAGMENTS = {
    "bullets": "- **Bullet slides** — 3–5 short bullets with one supporting sentence each.",
    "quote": "- **Quote / pull-quote slides** — `> short impactful quote` taking the whole slide. Use sparingly.",
    "table": "- **Comparison slides** — a markdown table comparing options / before-after / pros-cons.",
    "code": "- **Code slides** — fenced code blocks (```language ... ```) for technical talks. Keep ≤8 lines.",
    "image": (
        "- **Image slides (AI-generated)** — `![image: <one-sentence vivid prompt>]` when a visual concept "
        "matters and no real artifact exists. The user runs a separate \"Resolve images\" pass to materialize them."
    ),
    "vault": (
        "- **Image slides (vault)** — `![vault: <name-or-keyword>]` when an image already lives in the user's vault. "
        "Prefer this over `image:` when plausible — it's free and faithful."
    ),
    "screenshot": (
        "- **Image slides (screenshot)** — `![screenshot: <url>]` when the visual IS a webpage (live dashboard, "
        "public page). Resolved via headless browser at materialize time."
    ),
    "embed": (
        "- **Live demo slides** — `![embed: <url-or-app-path>]` renders the page LIVE inside the slide during "
        "present mode. Use for showcasing real app pages (`![embed: /journal/]`) or any URL. Put on its own line "
        "for a full-bleed demo slide."
    ),
    "divider": "- **Section divider slides** — a single big `#` heading mid-deck to break sections.",
    "mermaid": (
        "- **Diagram slides** — a fenced ```mermaid block. Use for flowcharts (`graph TD; A-->B`), sequence "
        "diagrams, mindmaps, architecture overviews. Beats a bullet list when relationships matter more than items. "
        "Keep nodes ≤8."
    ),
    "chart": (
        "- **Chart slides** — a fenced ```chart block holding JSON `{\"type\":\"bar|line|pie|doughnut\","
        "\"labels\":[...],\"values\":[...]}`. Use when the *shape* of the numbers is the message. Optional "
        "`\"label\":\"<series name>\"`."
    ),
    "narration": (
        "- **Narrated slides** — write a strong `Notes:` line on every slide. The user clicks \"Generate "
        "narration\" once and a TTS audio track per slide auto-plays in present mode. No per-slide markup "
        "needed — just rich Notes."
    ),
    "audio": (
        "- **Audio slides** — `![audio: <path-or-url>]` to embed an existing audio file from the vault "
        "(`![[clip.mp3]]` also works if it sits next to the deck). Use for music, interviews, or a recorded "
        "explanation that's stronger than narration."
    ),
    "video": (
        "- **Video slides** — `![video: <path-url-or-youtube>]` to embed a video. Accepts YouTube URLs "
        "(rewritten to the embed player), vault paths (`.mp4`/`.webm`), or direct URLs. Use for demos and "
        "talks where the moving image IS the slide."
    ),
}

DEFAULT_ELEMENTS = ["bullets", "quote", "table", "code", "image", "divider", "mermaid", "chart", "narration", "audio", "video"]
ALL_ELEMENTS = list(_ELEMENT_FRAGMENTS.keys())


# Intent shapes the rhythm and surface mix of a deck. Picked once per deck —
# stored in frontmatter, drives the plan + generation prompts.
INTENTS: dict[str, dict] = {
    "teach": {
        "label": "Teach",
        "guidance": (
            "Step-by-step build. Lead with a concrete problem, end each section with a "
            "one-line takeaway. Lean on diagrams, code, tables. ≤50% bullet slides. "
            "Worked examples beat abstractions."
        ),
    },
    "persuade": {
        "label": "Persuade",
        "guidance": (
            "One claim, one ask. Open with a hero quote OR hero image. Use contrast "
            "(before/after table, two-image compare). End on a single decision slide. "
            "≤40% bullet slides."
        ),
    },
    "story": {
        "label": "Tell a story",
        "guidance": (
            "Narrative arc — setup, conflict, resolution. Image and quote slides carry "
            "the emotional beats; bullets are the exception, not the rule. ≤30% bullets. "
            "Notes are the spoken story; on-slide text is sparse."
        ),
    },
    "decide": {
        "label": "Drive a decision",
        "guidance": (
            "Frame the decision in slide 2. Show options as a comparison table. End with "
            "a recommendation slide stating the call and the trigger to revisit. Tight: "
            "5–8 slides total."
        ),
    },
    "status": {
        "label": "Status update",
        "guidance": (
            "Lead with the headline (green/yellow/red). Numbers up front, prose at the "
            "back. Use tables. End with risks + asks. No filler slides."
        ),
    },
    "inspire": {
        "label": "Inspire",
        "guidance": (
            "Heavy on quote and image. Bullets only when listing concrete actions. "
            "Pacing is half of the work — use divider slides. ≤25% bullets."
        ),
    },
}


def intent_guidance(intent: str | None) -> str:
    """Return the guidance fragment for an intent (empty if unknown)."""
    if not intent:
        return ""
    spec = INTENTS.get(str(intent).lower().strip())
    return spec["guidance"] if spec else ""


def build_plan_system(allowed: list[str] | None = None) -> str:
    """System prompt for the planning pass. Returns JSON, not markdown."""
    if not allowed:
        allowed = list(DEFAULT_ELEMENTS)
    if "bullets" not in allowed:
        allowed = ["bullets"] + allowed
    surfaces = ", ".join(allowed)
    intents = ", ".join(f'"{k}"' for k in INTENTS.keys())
    return f"""You are a slide-deck planner. Given a topic, audience, and outline, produce a STRUCTURED PLAN — not the deck itself. The plan will be reviewed and edited by a human before the deck is generated.

Output ONLY a single JSON object, no surrounding prose, no code fences. Shape:

{{
  "intent": one of {intents},
  "audience": "<short phrase: who is this for>",
  "duration_min": <integer minutes; pick from the topic, default 5>,
  "title": "<deck title — refine the user's input if helpful>",
  "subtitle": "<one short subtitle line OR empty string>",
  "slides": [
    {{"surface": "<surface>", "beat": "<one-sentence purpose of this slide>", "headline": "<the heading text the audience sees>"}}
  ]
}}

Surface choices, pick from EXACTLY this set: {surfaces}.

Pick the surface for each slide based on what the slide needs to DO, not the user's preferences:
- bullets — when there are 3-5 short parallel points to enumerate
- quote — when one line of someone else's words carries the slide
- table — when comparing 2+ options across 2+ dimensions
- code — when the artifact IS code; do not use for pseudo-code
- image — when a visual concept matters and no real artifact exists
- vault — when an existing image in the user's vault should fill the slide
- screenshot — when the slide IS a real webpage
- embed — when the audience should see a LIVE demo of an app or page
- divider — between sections of a longer deck; use sparingly

Plan-level rules:
- Slide 1 is always a title slide (surface: "bullets", headline: deck title, beat describes the hook).
- 6-10 slides for 5 min, 12-18 for 10 min, scale linearly.
- Vary surfaces. Long bullet runs are a planning failure — break with quote/image/table/divider.
- Last slide is takeaways or a single decision, depending on intent.
- Each `beat` is ONE sentence stating what the slide accomplishes for the audience. Not the content itself.
- `headline` is the actual on-slide heading — short, concrete, no clickbait.

Do NOT:
- Output markdown, slide bodies, or anything besides the JSON object.
- Use any surface outside the allowed set.
- Repeat headlines across slides.
- Plan slides that have no clear beat — every slide earns its place.
"""


def build_gen_from_plan_system(allowed: list[str] | None, intent: str | None) -> str:
    """System prompt for generating markdown locked to an approved plan."""
    if not allowed:
        allowed = list(DEFAULT_ELEMENTS)
    if "bullets" not in allowed:
        allowed = ["bullets"] + allowed
    variety = "\n".join(_ELEMENT_FRAGMENTS[k] for k in allowed if k in _ELEMENT_FRAGMENTS)
    intent_block = ""
    if intent:
        guidance = intent_guidance(intent)
        if guidance:
            intent_block = f"\nIntent — {intent}: {guidance}\n"
    return f"""You are filling in a slide deck from an APPROVED PLAN. The plan fixes the surface and beat of every slide. Your job is to write the actual slide markdown.

You will receive the plan as JSON. Render it as markdown:
- Output ONLY the slide markdown. No preamble, no code fences around the whole thing.
- Slides are separated by a line containing only `---`.
- Slide 1: `# <title>` then optional one-line subtitle (from plan.subtitle).
- Slides 2+: `## <headline>` (use the plan's headline verbatim), then content matching the surface.
- Honor every slide's surface choice — do NOT swap surfaces silently.
- Speaker notes go on a `Notes:` line under each slide (1-3 sentences). Notes are the spoken story; on-slide text stays tight.
{intent_block}
Surface implementations (use ONLY these):
{variety}

Do NOT:
- Change the slide order or count.
- Swap surfaces (if the plan says quote, deliver a quote slide).
- Wrap output in ```markdown fences.
- Invent statistics, dates, or quotes you cannot ground in the topic or source.
- Put `Notes:` inside an image placeholder or embed line.
"""


def build_outline_system(allowed: list[str] | None = None) -> str:
    """Compose the deck-outline system prompt from the user's element palette."""
    if not allowed:
        allowed = list(DEFAULT_ELEMENTS)
    if "bullets" not in allowed:
        allowed = ["bullets"] + allowed
    variety = "\n".join(_ELEMENT_FRAGMENTS[k] for k in allowed if k in _ELEMENT_FRAGMENTS)
    image_warning = ""
    if any(k in allowed for k in ("image", "vault", "screenshot", "embed")):
        image_warning = "- Include more than ~30% image / embed slides — text-driven slides do most of the work."
    return f"""You are a slide-deck planner. Given a talk topic, produce a markdown deck rich enough to *show*, not just narrate.

Slide structure:
- Output ONLY the slide markdown. No preamble, no explanation, no surrounding code fences.
- Split slides with a single line containing only `---`.
- First slide: a single `#` heading + optional one-line subtitle.
- Each subsequent slide opens with `## ` heading, followed by varied content (see Slide variety).
- 6–10 slides for a 5-min talk; 12–18 for a 10-min talk.
- Final slide is a "Takeaways" slide with 3 bullets.

Slide variety — vary across the deck. Mix in ONLY the surfaces below:
{variety}

Speaker notes:
- Use `Notes:` lines under a slide for things the audience does NOT see — the spoken story behind the slide.
- Aim for 1–3 sentences of notes on every slide that has any abstraction the audience might need help following.

Do NOT:
- Use any slide surface NOT listed above (the user explicitly opted out).
- Wrap the whole output in ```markdown fences.
- Repeat the title across multiple slides.
- Use raw HTML — markdown only.
- Invent statistics, dates, or quotes you cannot ground in the topic.
{image_warning}
- Put speaker `Notes:` inside an image-placeholder or embed line.
"""


# Back-compat alias — call sites that don't pass an element list get the default.
DECK_OUTLINE_SYSTEM = build_outline_system()


SPEAKIFY_SYSTEM = """You convert presenter coaching notes into the actual sentences a speaker would say aloud to the audience.

Tone:
- First person, conversational, natural rhythm.
- Around 30 seconds spoken (≈70-90 words).
- Sound like a person talking, not a textbook.

Do NOT:
- Use meta phrases like "in this slide", "as you can see", "here we have", "let's talk about".
- Echo the slide heading verbatim — paraphrase or skip past it.
- Quote the presenter's coaching back ("walk through", "emphasize", "stress that"); execute the coaching, don't repeat it.
- Output bullets, markdown, or quotes around the script.
- Add stage directions, timing notes, or anything in brackets.

Output ONLY the spoken script as plain prose — no preamble, no quotes, no formatting."""

_IMAGE_PLACEHOLDER_RE = re.compile(
    r"!\[(image|vault|screenshot):\s*([^\]]+)\]",
    re.IGNORECASE,
)
_EMBED_PLACEHOLDER_RE = re.compile(
    r"!\[embed:\s*([^\]]+)\]",
    re.IGNORECASE,
)
_AUDIO_PLACEHOLDER_RE = re.compile(
    r"!\[audio:\s*([^\]]+)\]",
    re.IGNORECASE,
)
_VIDEO_PLACEHOLDER_RE = re.compile(
    r"!\[video:\s*([^\]]+)\]",
    re.IGNORECASE,
)
_AUDIO_EXTS = ("mp3", "wav", "m4a", "ogg", "flac")
_VIDEO_EXTS = ("mp4", "webm", "mov")
_WIKI_AUDIO_RE = re.compile(
    r"!\[\[([^\]|]+\.(?:" + "|".join(_AUDIO_EXTS) + r"))(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)
_WIKI_VIDEO_RE = re.compile(
    r"!\[\[([^\]|]+\.(?:" + "|".join(_VIDEO_EXTS) + r"))(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)
_YT_RE = re.compile(
    r"^https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)

_HR_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_NOTES_LINE_RE = re.compile(r"^\s*Notes:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_NOTES_HTML_RE = re.compile(r"<!--\s*notes:\s*(.*?)-->", re.DOTALL | re.IGNORECASE)
# Audience-facing spoken script. Authored separately from `Notes:` (which is
# presenter-only memory aid). When present, narration prefers `Say:` over
# `Notes:` so TTS reads what the speaker would actually say to the audience,
# not director-style coaching like "Walk through..." or "Land the core rule".
_SAY_LINE_RE = re.compile(r"^\s*Say:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_SAY_HTML_RE = re.compile(r"<!--\s*say:\s*(.*?)-->", re.DOTALL | re.IGNORECASE)
_WIKI_IMG_RE = re.compile(
    r"!\[\[([^\]|]+\.(?:png|jpg|jpeg|gif|svg|webp))(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)
_MD_IMG_BARE_RE = re.compile(
    r"!\[([^\]]*)\]\(((?!https?://|data:|/)[^)\s]+?\.(?:png|jpg|jpeg|gif|svg|webp))\)",
    re.IGNORECASE,
)


def _normalize_elements(raw) -> list[str]:
    """Coerce a list-or-csv-or-None into a clean list of valid element names."""
    if raw is None:
        return list(DEFAULT_ELEMENTS)
    if isinstance(raw, str):
        raw = [s.strip() for s in raw.split(",")]
    if not isinstance(raw, list):
        return list(DEFAULT_ELEMENTS)
    out = [str(x).strip().lower() for x in raw if x]
    valid = [e for e in out if e in _ELEMENT_FRAGMENTS]
    return valid or list(DEFAULT_ELEMENTS)


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "deck"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body). Crude YAML — flat string/number/list only."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end() :]
    fm: dict = {}
    cur_key = None
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith(("  - ", "- ")):
            val = line.split("- ", 1)[1].strip()
            if cur_key:
                fm.setdefault(cur_key, [])
                if isinstance(fm[cur_key], list):
                    fm[cur_key].append(val.strip("'\""))
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            cur_key = k
            if v == "":
                fm[k] = []
            elif v.startswith("[") and v.endswith("]"):
                items = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                fm[k] = items
            else:
                fm[k] = v.strip("'\"")
    return fm, body


def _extract_notes(slide_md: str) -> tuple[str, str, str]:
    """Strip Notes: + Say: blocks, return (clean_md, joined_notes, joined_say).

    `Notes:` is presenter-only (hidden in present mode, never spoken).
    `Say:` is audience-facing — what TTS narration reads aloud. When absent,
    narration falls back to Notes for backward compatibility.
    """
    notes: list[str] = []
    for m in _NOTES_HTML_RE.finditer(slide_md):
        notes.append(m.group(1).strip())
    clean = _NOTES_HTML_RE.sub("", slide_md)
    for m in _NOTES_LINE_RE.finditer(clean):
        notes.append(m.group(1).strip())
    clean = _NOTES_LINE_RE.sub("", clean)

    say: list[str] = []
    for m in _SAY_HTML_RE.finditer(clean):
        say.append(m.group(1).strip())
    clean = _SAY_HTML_RE.sub("", clean)
    for m in _SAY_LINE_RE.finditer(clean):
        say.append(m.group(1).strip())
    clean = _SAY_LINE_RE.sub("", clean)

    return (
        clean.strip("\n"),
        "\n\n".join(n for n in notes if n),
        "\n\n".join(s for s in say if s),
    )


def parse_deck(text: str, asset_url_prefix: str = "", embed_base: str = "") -> dict:
    """Parse a deck markdown blob → frontmatter + ordered slides.

    `embed_base`: optional host (e.g. `http://localhost:9001`) to prepend to
    same-origin embed targets. Used so a deck can render against the demo
    daemon while authored on the personal one. Empty = current host.
    """
    fm, body = _split_frontmatter(text)
    raw_slides = _HR_RE.split(body)
    slides: list[dict] = []
    for chunk in raw_slides:
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        clean_md, notes, say = _extract_notes(chunk)
        if not clean_md.strip() and not notes and not say:
            continue
        clean_md = _rewrite_image_refs(clean_md, asset_url_prefix, embed_base)
        html = _md_to_html(clean_md)
        slides.append({"md": clean_md, "html": html, "notes": notes, "say": say, "theme": None})
    return {"frontmatter": fm, "slides": slides}


def _rewrite_image_refs(text: str, prefix: str, embed_base: str = "") -> str:
    """`![[name.png]]` → `![](prefix/name.png)`; placeholders → labeled cards; embeds → iframes.

    `embed_base` is prepended to relative embed targets so the live demo can
    point at a different host (e.g. dogfood daemon, public demo VPS).
    """
    from urllib.parse import quote

    def _placeholder_card(m: re.Match) -> str:
        kind = m.group(1).lower()
        arg = m.group(2).strip()
        icon = {"image": "🎨", "vault": "🗂️", "screenshot": "📸"}.get(kind, "📷")
        label = {"image": "Generate", "vault": "Vault", "screenshot": "Screenshot"}.get(
            kind, kind.capitalize()
        )
        return (
            '<div class="ppt-image-placeholder" data-kind="'
            + kind
            + '" data-arg="'
            + _html_escape(arg)
            + '">'
            + icon
            + " "
            + label
            + ": "
            + _html_escape(arg)
            + "</div>"
        )

    text = _IMAGE_PLACEHOLDER_RE.sub(_placeholder_card, text)

    def _embed_block(m: re.Match) -> str:
        target = m.group(1).strip()
        if not (target.startswith("http://") or target.startswith("https://") or target.startswith("/")):
            target = "/" + target.lstrip("/")
        # For same-origin embeds (relative paths), append ?demo=1 so the embedded
        # page redacts personal data via the eos-redact runtime, and prepend
        # `embed_base` so the deck can point at a different daemon (dogfood,
        # public demo) than the one rendering it. External URLs are left alone.
        if target.startswith("/"):
            sep = "&" if "?" in target else "?"
            target = target + sep + "demo=1"
            if embed_base:
                target = embed_base.rstrip("/") + target
        safe = _html_escape(target)
        return (
            '<iframe class="ppt-embed" src="'
            + safe
            + '" loading="lazy" referrerpolicy="no-referrer" '
            + 'allow="clipboard-read; clipboard-write" '
            + 'sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>'
        )

    text = _EMBED_PLACEHOLDER_RE.sub(_embed_block, text)

    def _join(name: str) -> str:
        if not prefix:
            return name
        return prefix.rstrip("/") + "/" + quote(name, safe="")

    def _resolve_media_src(arg: str) -> str:
        """Resolve a media arg to a URL. Bare names → per-deck asset folder."""
        arg = arg.strip()
        if arg.startswith(("http://", "https://", "/")):
            return arg
        return _join(arg)

    def _audio_block(m: re.Match) -> str:
        src = _resolve_media_src(m.group(1))
        return (
            '<audio controls preload="metadata" class="ppt-audio" src="'
            + _html_escape(src) + '"></audio>'
        )

    def _video_block(m: re.Match) -> str:
        src = m.group(1).strip()
        yt = _YT_RE.match(src)
        if yt:
            embed = "https://www.youtube.com/embed/" + yt.group(1)
            # No referrerpolicy — YouTube uses Referer to validate the
            # embedding domain. Stripping it triggers Error 153 on any video
            # the uploader has restricted to specific domains.
            return (
                '<iframe class="ppt-video ppt-video-yt" src="' + _html_escape(embed)
                + '" loading="lazy" '
                + 'allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture" '
                + 'allowfullscreen></iframe>'
            )
        url = _resolve_media_src(src)
        return (
            '<video controls preload="metadata" class="ppt-video" src="'
            + _html_escape(url) + '"></video>'
        )

    text = _AUDIO_PLACEHOLDER_RE.sub(_audio_block, text)
    text = _VIDEO_PLACEHOLDER_RE.sub(_video_block, text)
    text = _WIKI_AUDIO_RE.sub(
        lambda m: '<audio controls preload="metadata" class="ppt-audio" src="'
        + _html_escape(_join(m.group(1).strip())) + '"></audio>',
        text,
    )
    text = _WIKI_VIDEO_RE.sub(
        lambda m: '<video controls preload="metadata" class="ppt-video" src="'
        + _html_escape(_join(m.group(1).strip())) + '"></video>',
        text,
    )

    text = _WIKI_IMG_RE.sub(lambda m: f"![]({_join(m.group(1).strip())})", text)
    text = _MD_IMG_BARE_RE.sub(
        lambda m: f"![{m.group(1)}]({_join(m.group(2).strip())})",
        text,
    )
    return text


def _md_to_html(text: str) -> str:
    """Convert one slide's markdown to HTML."""
    if not HAS_MARKDOWN:
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre>{esc}</pre>"
    import markdown as _md

    return _md.markdown(text, extensions=["fenced_code", "tables", "attr_list"])


def _starter_body(title: str) -> str:
    return (
        f"# {title}\n\n"
        "---\n\n"
        "## Why this matters\n\n"
        "- one bullet\n"
        "- another bullet\n\n"
        "Notes: speak to the audience problem here.\n\n"
        "---\n\n"
        "## Key idea\n\n"
        "- supporting point\n"
        "- supporting point\n\n"
        "---\n\n"
        "## Takeaways\n\n"
        "- one\n- two\n- three\n"
    )


def _count_slides(text: str) -> int:
    parsed = parse_deck(text)
    return len(parsed["slides"])


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_STANDALONE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html, body {{ margin:0; height:100%; background:#0a0a1a; }}
  body {{ display:flex; align-items:center; justify-content:center; padding:24px; }}
  #deck {{ width: min(96vw, 1400px); }}
</style>
</head>
<body>
<div id="deck"></div>
<script>
{deck_js}
</script>
<script>
(function() {{
  var slides = {slides_json};
  EOS_DECK.create(document.getElementById('deck'), {{
    mode: 'manual',
    slides: slides,
    theme: '{theme}',
    aspect: '{aspect}',
  }});
}})();
</script>
</body>
</html>
"""
