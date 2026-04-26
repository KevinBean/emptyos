"""Markdown → HTML renderer with wikilink, callout, and image-embed support.

Shared by any app that needs to turn vault markdown into HTML. Originally lived
inside `apps/publish/`; extracted when `apps/reports/` became the second consumer
(CLAUDE.md principle 9 — extract shared, then reuse).
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import markdown
    from markdown.extensions import Extension
    from markdown.preprocessors import Preprocessor

    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False


# --- Wikilink handling ---

WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

# Callout syntax: > [!type] title
CALLOUT_RE = re.compile(r"^> \[!(\w+)\]\s*(.*)", re.MULTILINE)
CALLOUT_BODY = re.compile(r"^> (.*)$", re.MULTILINE)


def resolve_wikilinks(text: str, published_slugs: dict[str, str], link_prefix: str = "") -> str:
    """Replace [[Note]] with HTML links for published notes, plain text for private ones.

    Args:
        text: Markdown content.
        published_slugs: Mapping {lookup_key: (slug, type)}.
            e.g. {"my-note": ("my-note", "post"), "about": ("about", "page")}
        link_prefix: Path prefix for resolving links (e.g., "../" when rendering from /posts/).
    """

    def _replace(m: re.Match) -> str:
        target = m.group(1).strip()
        display = m.group(2) or target
        lookup = target.lower().replace(" ", "-")
        if lookup in published_slugs:
            slug, item_type = published_slugs[lookup]
            if item_type == "page":
                href = f"{link_prefix}{slug}.html"
            else:
                href = f"{link_prefix}posts/{slug}.html"
            return f'<a href="{href}" class="wikilink">{display}</a>'
        return f'<span class="wikilink-private">{display}</span>'

    return WIKILINK.sub(_replace, text)


def convert_callouts(text: str) -> str:
    """Convert callouts to HTML divs.

    > [!note] Title  →  <div class="callout callout-note"><p class="callout-title">Title</p>...
    """
    lines = text.split("\n")
    result = []
    in_callout = False
    callout_type = ""
    callout_body: list[str] = []

    def flush_callout():
        nonlocal in_callout, callout_body
        if in_callout:
            body = "\n".join(callout_body)
            result.append(f'<div class="callout callout-{callout_type}">')
            if callout_body:
                result.append(f"<p>{body}</p>")
            result.append("</div>")
            result.append("")
            in_callout = False
            callout_body = []

    for line in lines:
        cm = CALLOUT_RE.match(line)
        if cm:
            flush_callout()
            in_callout = True
            callout_type = cm.group(1).lower()
            title = cm.group(2).strip()
            callout_body = []
            if title:
                callout_body.append(f'<strong class="callout-title">{title}</strong><br>')
            continue

        if in_callout:
            if line.startswith("> "):
                callout_body.append(line[2:])
                continue
            elif line.strip() == ">":
                callout_body.append("")
                continue
            else:
                flush_callout()

        result.append(line)

    flush_callout()
    return "\n".join(result)


def render_markdown(
    content: str,
    published_slugs: dict | None = None,
    assets_prefix: str = "assets/",
    link_prefix: str = "",
) -> str:
    """Render extended markdown to HTML.

    Args:
        content: Raw markdown (frontmatter already stripped).
        published_slugs: Map of published note stems → (slug, type) for wikilink resolution.
        assets_prefix: Path prefix for image assets (e.g., "assets/" for root, "../assets/" for posts/).
        link_prefix: Path prefix for wikilinks (e.g., "../" when rendering from /posts/).

    Returns:
        HTML string.
    """
    if not HAS_MARKDOWN:
        return f"<pre>{content}</pre>"

    # Pre-process: image embeds FIRST (before wikilinks eat the [[]])
    # ![[image.png]] → ![](assets_prefix/image.png)
    content = re.sub(
        r"!\[\[([^\]]+\.(png|jpg|jpeg|gif|svg|webp))\]\]",
        lambda m: f"![]({assets_prefix}{m.group(1)})",
        content,
        flags=re.IGNORECASE,
    )

    # Media paths stay as media/ — builder copies media/ to site root level
    # Posts are in posts/ subdir, so they need ../media/ prefix.
    # Covers both raw HTML embeds (audio/video/script) and markdown image syntax.
    if assets_prefix.startswith("../"):
        content = re.sub(
            r'src="(media/[^"]+)"',
            lambda m: f'src="../{m.group(1)}"',
            content,
        )
        content = re.sub(
            r'(!\[[^\]]*\])\((media/[^)]+)\)',
            lambda m: f'{m.group(1)}(../{m.group(2)})',
            content,
        )

    # Pre-process: wikilinks → HTML links/spans
    if published_slugs:
        content = resolve_wikilinks(content, published_slugs, link_prefix=link_prefix)
    else:
        content = WIKILINK.sub(lambda m: m.group(2) or m.group(1), content)

    # Pre-process: callouts
    content = convert_callouts(content)

    # Render with python-markdown
    extensions = [
        "fenced_code",
        "tables",
        "toc",
        "attr_list",
        "md_in_html",
    ]

    # Add codehilite only if pygments is available
    try:
        import pygments  # noqa: F401

        extensions.append("codehilite")
        extension_configs = {
            "codehilite": {"css_class": "highlight", "guess_lang": False},
            "toc": {"permalink": True, "permalink_class": "header-link"},
        }
    except ImportError:
        extension_configs = {
            "toc": {"permalink": True, "permalink_class": "header-link"},
        }

    md = markdown.Markdown(
        extensions=extensions,
        extension_configs=extension_configs,
    )
    html = md.convert(content)
    toc = getattr(md, "toc", "")

    return html, toc


def extract_images(content: str) -> list[str]:
    """Extract image filenames referenced in the markdown.

    Handles:
    - ![[image.png]]  (wikilink embed)
    - ![alt](path/image.png)  (standard markdown)
    """
    images = []
    # Wikilink embeds
    for m in re.finditer(r"!\[\[([^\]]+\.(png|jpg|jpeg|gif|svg|webp))\]\]", content, re.IGNORECASE):
        images.append(m.group(1))
    # Standard markdown images
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+\.(png|jpg|jpeg|gif|svg|webp))\)", content, re.IGNORECASE):
        images.append(m.group(1))
    return images
