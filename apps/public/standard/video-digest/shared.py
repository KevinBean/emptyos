"""video-digest — module-level constants + prompts + pure helpers.

Extracted from app.py so helper modules (queue/digest/listen) can import
these directly without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path


# Canonical vault folder for Web-Clip digests. One declaration; every rel_path
# composed below derives from it, so a future move to vault_config() can be
# done in one place.
WEB_CLIPS_DIR = "30_Resources/Web-Clips"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

DIGEST_SYSTEM = """You are an expert distiller of long-form talks and lectures.

You are given a YouTube transcript. Produce a structured digest in the user's voice
(first-person where appropriate; never marketing copy; never filler). The user
keeps the raw transcript separately — this digest is the synthesis, not a transcript
replay.

HARD CONSTRAINTS:
- Do NOT regurgitate the transcript verbatim or near-verbatim.
- Do NOT use marketing prose, hype words, or filler like "in conclusion".
- Do NOT drop concrete examples, names, dates, study citations, or numerical claims
  the speaker actually used — those are the digest's value.
- If the speaker draws a hard line between "what I can defend" vs "what I conjecture",
  preserve that line explicitly.
- Citations to canonical or peer-reviewed work (paper titles, year, journal) should
  be retained verbatim where mentioned.

STRUCTURE (markdown, no frontmatter):
## Thesis
One paragraph stating the central claim, in the user's voice.

## <Section per major argument>
Each section is 2-5 short paragraphs or a tight bullet list. Section headings must
name the argument, not generic labels like "Point 1".

## What I take away
2-4 bullets: durable conclusions worth holding onto.
"""

WEB_DIGEST_SYSTEM = """You are an expert distiller of web articles, docs pages, and tool pages.

You are given the readable text of one web page. Produce a structured digest in
the user's voice (first-person where appropriate; never marketing copy; never
filler). The goal is a note the user can REUSE later — not a bookmark blurb.

HARD CONSTRAINTS:
- Do NOT regurgitate the page verbatim or near-verbatim.
- Do NOT use marketing prose, hype words, or filler like "in conclusion".
- Do NOT drop concrete specifics the page actually carries — names, numbers,
  versions, commands, API names, dates, prices. Those are the digest's value.
- Do NOT invent content for a section the page can't support — write "(none)"
  for an empty section rather than padding it.
- If the page is a tool/product page, capture what it does, what it costs (if
  stated), and what it replaces — never its tagline.

STRUCTURE (markdown, no frontmatter):
## Summary
One tight paragraph: what this page is and its central claim or offer.

## Key points
3-8 bullets of substance. Keep concrete specifics verbatim.

## Reusable angles
2-4 bullets: how this could be reused — a topic angle to write about, a pattern
to apply, a comparison it enables. Anchor each to something specific on the page.

## Next steps
1-3 bullets: concrete follow-ups (try X, read the linked Y, compare against Z).
Only include steps the page itself motivates.
"""

DEFAULT_DOMAINS = [
    "ai-engineering",       # LLMs, agents, prompts, AI Engineer talks
    "software-engineering", # languages, design, craft (Pocock-style)
    "infrastructure",       # devops, deploy, observability
    "data-engineering",     # pipelines, data quality, ETL
    "buddhism",             # dharma, meditation (cross-corpus with KB domain)
    "cognitive-science",    # psychology, neuroscience, learning
    "tools",                # IDE, productivity software
    "business",             # strategy, hiring, leadership
    "other",                # fallback when nothing fits
]

CATEGORIZE_SYSTEM = """You assign a YouTube video digest to exactly ONE domain.

Vocabulary: {domains}

Output rules:
- Output ONLY the chosen domain identifier, lowercased, no quotes, no prose, no leading dash.
- If nothing fits any listed domain, output 'other'.
- Use the title + speaker + summary excerpt to decide.
"""


EXTRACTION_SYSTEM = """You are extracting durable knowledge from a video digest into KB-note candidates.

Each candidate is a structured object with these keys:
    kind: one of "concept" | "lesson" | "reference"
        - "concept": an explanatory standalone idea (a model, framework, distinction)
        - "lesson": a practical / hard-won applied insight ("when X, do Y")
        - "reference": a pointer at an external work, person, or canonical entity
    title: short noun phrase, ≤8 words, suitable as a note title
    slug: kebab-case slug (lowercase, hyphens, ascii) ≤40 chars
    domain: subject area (e.g. "buddhism", "ml", "leadership")
    topic: finer slice within the domain (optional)
    body: 3-8 sentences of substance, in the user's voice. Cite specifics (names,
          studies, numbers) where the source has them.

HARD CONSTRAINTS:
- Quality over quantity. 3-6 candidates total for a typical 25-minute talk.
- Do NOT propose candidates for trivia, examples, or one-liners.
- Do NOT propose candidates whose body would just paraphrase the title.
- Do NOT include any candidate whose substance the source itself flags as
  unverified speculation, unless the candidate is explicitly framed as such.

Output: ONLY a JSON array of candidates. No prose around it. No markdown fences.
"""


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------

_VIDEO_ID_RE = re.compile(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})")
_YT_HOST_RE = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch|shorts/|embed/)|youtu\.be/)", re.IGNORECASE)
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def is_youtube_url(url: str) -> bool:
    return bool(url) and bool(_YT_HOST_RE.search(url))


def extract_video_id(url_or_id: str) -> str | None:
    s = (url_or_id or "").strip()
    if len(s) == 11 and "/" not in s and "?" not in s and ":" not in s:
        return s
    m = _VIDEO_ID_RE.search(s)
    return m.group(1) if m else None


def slugify(text: str, max_len: int = 60) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:max_len] or "untitled"


def render_clip_note(meta: dict, summary: str) -> str:
    """Render frontmatter + body for the Web-Clips note."""
    today = datetime.date.today().isoformat()
    title = (meta.get("title") or "Untitled").replace('"', '\\"')
    speaker = meta.get("channel") or "Unknown"
    duration = meta.get("duration_s") or 0
    url = meta.get("url") or ""
    domain = (meta.get("domain") or "").strip()
    thumbnail = (meta.get("thumbnail") or "").strip()
    fm_tags = "\n".join(f"  - {t}" for t in ["web-clip", "video-digest"])
    transcript_stem = note_stem(meta, today) + ".transcript"
    domain_line = f"domain: {domain}\n" if domain else ""
    thumbnail_line = f"thumbnail: {thumbnail}\n" if thumbnail else ""
    fm = (
        "---\n"
        f'title: "{title}"\n'
        f"speaker: {speaker}\n"
        f"source: {url}\n"
        f"type: talk\n"
        f"clipped: {today}\n"
        f"duration_s: {duration}\n"
        f"{domain_line}"
        f"{thumbnail_line}"
        f"tags:\n{fm_tags}\n"
        f"related:\n"
        f'  - "[[{transcript_stem}]]"\n'
        "---\n\n"
    )
    # Embed the thumbnail at the top of the body so the note renders with a
    # poster image in markdown viewers. Plain `![]` link — the image lives on
    # YouTube's CDN, no local copy.
    body_prefix = f"![]({thumbnail})\n\n" if thumbnail else ""
    return fm + body_prefix + summary.strip() + "\n"


def render_web_note(meta: dict, summary: str) -> str:
    """Render frontmatter + body for a generic web-page Web-Clips note.

    Sibling of :func:`render_clip_note` (YouTube). Differences: ``type`` is
    ``article``, ``site`` replaces speaker/duration, there is no transcript
    sidecar link, and the tags carry ``link-digest`` so web clips stay
    queryable apart from video digests.
    """
    today = datetime.date.today().isoformat()
    title = (meta.get("title") or "Untitled").replace('"', '\\"')
    site = meta.get("channel") or "Unknown"
    url = meta.get("url") or ""
    domain = (meta.get("domain") or "").strip()
    fm_tags = "\n".join(f"  - {t}" for t in ["web-clip", "link-digest"])
    domain_line = f"domain: {domain}\n" if domain else ""
    fm = (
        "---\n"
        f'title: "{title}"\n'
        f"site: {site}\n"
        f"source: {url}\n"
        f"type: article\n"
        f"clipped: {today}\n"
        f"{domain_line}"
        f"tags:\n{fm_tags}\n"
        "---\n\n"
    )
    return fm + summary.strip() + "\n"


def normalize_domain(raw: str, vocabulary: list[str]) -> str:
    """Clean an LLM-emitted domain string and validate against the vocabulary.

    Falls back to ``"other"`` when the cleaned value is not in the vocabulary
    (or the vocabulary has no ``"other"`` entry — in which case the first
    vocabulary item wins, so we never return an unrecognised value).
    """
    chosen = (raw or "").strip().lower()
    # Take only the first line (LLMs sometimes add prose).
    chosen = chosen.splitlines()[0].strip() if chosen else ""
    # Strip leading dashes / quotes / backticks / bullets.
    chosen = chosen.lstrip("-•* `'\"").rstrip(" `'\".,;:")
    if chosen in vocabulary:
        return chosen
    return "other" if "other" in vocabulary else (vocabulary[0] if vocabulary else "other")


def _parse_frontmatter_from_disk(abs_path: Path) -> dict:
    """Last-resort frontmatter parser used by ``_resolve_digest`` when both
    VaultIndex's cached props and a force-`index_file` came back empty. Reads
    the file's YAML head and extracts simple ``key: value`` lines. Tolerant of
    missing or malformed frontmatter — returns ``{}`` rather than raising."""
    try:
        text = abs_path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end < 0:
        return {}
    out: dict = {}
    for line in text[3:end].splitlines():
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        k, _, v = s.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def note_stem(meta: dict, today: str) -> str:
    speaker = meta.get("channel") or "Unknown"
    raw_title = meta.get("title") or "Untitled"
    safe_title = re.sub(r'[<>:"/\\|?*]', "", raw_title).strip()
    return f"{today} {speaker} - {safe_title}"
