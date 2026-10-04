"""Markdown → HTML renderer with wikilink, callout, and image-embed support.

Shared by any app that needs to turn vault markdown into HTML. Originally lived
inside `apps/publish/`; extracted when `apps/reports/` became the second consumer
(CLAUDE.md principle 9 — extract shared, then reuse).
"""

from __future__ import annotations

import re

try:
    import markdown

    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False


# --- Code regions ---
# A markdown renderer does not transform inside code, so nothing that rewrites
# markdown should either. Extracted here when `apps/public/core/link/linkindex.py`
# became the second consumer (CLAUDE.md principle 9) — it needs the same regions
# to *exclude* link targets, where this module needs them to *preserve* code
# verbatim. One scanner, two derived helpers.

# Inline code, kept to one line: markdown inline code effectively never wraps,
# and allowing it to made the pattern catastrophically slow — an alternation
# between two backtick runs backtracks so badly it measured 44.1s across 27,591
# notes, against 4.6s for this.
_INLINE_CODE = re.compile(r"`{1,3}[^`\n]*`{1,3}")
_FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def iter_code_segments(text: str):
    """Yield ``(is_code, chunk)`` pairs covering ``text`` in order.

    Line-scanned rather than matched with one big regex, which also gets fence
    state right where a regex did not: an unclosed fence runs to end of file,
    and a closing run must be at least as long and of the same character as the
    one that opened it.
    """
    lines = str(text or "").split("\n")
    fence: str | None = None
    for i, line in enumerate(lines):
        nl = "\n" if i < len(lines) - 1 else ""
        if fence is not None:
            stripped = line.strip()
            if (stripped.startswith(fence[0]) and len(stripped) >= len(fence)
                    and set(stripped) == {fence[0]}):
                fence = None
            yield True, line + nl
            continue
        m = _FENCE_OPEN.match(line)
        if m:
            fence = m.group(1)
            yield True, line + nl
            continue
        pos = 0
        for cm in _INLINE_CODE.finditer(line):
            if cm.start() > pos:
                yield False, line[pos:cm.start()]
            yield True, cm.group(0)
            pos = cm.end()
        yield False, line[pos:] + nl


def sub_outside_code(pattern: re.Pattern, repl, text: str) -> str:
    """``pattern.sub(repl, text)``, but never inside code.

    Without this, a `[[Note]]` written inside a fenced example became a real
    ``<a href>`` in the rendered code block on the published site — the reader
    saw markup where the author wrote a literal.
    """
    return "".join(chunk if is_code else pattern.sub(repl, chunk)
                   for is_code, chunk in iter_code_segments(text))


def strip_code(text: str) -> str:
    """``text`` with every code region blanked, line count preserved.

    Blanked rather than deleted so nothing on either side of a code span is
    joined into a construct that was never written.
    """
    return "".join(
        re.sub(r"[^\n]", " ", chunk) if is_code else chunk
        for is_code, chunk in iter_code_segments(text)
    )


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

    # Outside code only. A `[[Note]]` inside a fenced example is a literal the
    # author wrote, and substituting it put an `<a href>` into the rendered code
    # block on the published site.
    return sub_outside_code(WIKILINK, _replace, text)


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

    # Standard markdown images `![alt](file.png)` — prefix bare relative paths
    # with assets_prefix so the builder's image-copy destination matches the
    # rendered <img src=...>. Skip absolute URLs, already-prefixed paths, and
    # media/ paths (handled separately for the posts/ subdir below).
    if assets_prefix:
        _skip = ("http://", "https://", "/", "../", "data:", "#",
                 assets_prefix, "media/")

        def _prefix_md_img(m):
            alt_part, path = m.group(1), m.group(2)
            if path.startswith(_skip):
                return m.group(0)
            return f"{alt_part}({assets_prefix}{path})"

        content = re.sub(
            r"(!\[[^\]]*\])\(([^)]+\.(?:png|jpg|jpeg|gif|svg|webp))\)",
            _prefix_md_img,
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
            r"(!\[[^\]]*\])\((media/[^)]+)\)",
            lambda m: f"{m.group(1)}(../{m.group(2)})",
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
        "footnotes",
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
    for m in re.finditer(
        r"!\[[^\]]*\]\(([^)]+\.(png|jpg|jpeg|gif|svg|webp))\)", content, re.IGNORECASE
    ):
        images.append(m.group(1))
    return images
