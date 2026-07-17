"""Site builder — scans vault, renders notes, writes static site."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime
from html import escape as html_escape
from pathlib import Path

from emptyos.sdk.utils import fm_list, fm_str, parse_frontmatter, slugify, strip_frontmatter

# Corpus chunking — emits corpus.json alongside the built site for downstream
# consumers (chatbot service, future search, embeddings). One chunk per H2
# section, capped at ~1500 chars on a paragraph boundary so retrieval can
# return small, self-contained passages.
_CHUNK_MAX_CHARS = 1500
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_IMG_WIKI_RE = re.compile(r"!\[\[[^\]]+\]\]")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def _strip_for_corpus(text: str) -> str:
    """Reduce markdown to plain prose suitable for retrieval/embedding."""
    text = _CODE_BLOCK_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    text = _IMG_MD_RE.sub("", text)
    text = _IMG_WIKI_RE.sub("", text)
    text = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = _LINK_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(lambda m: m.group(0).strip("`"), text)
    text = _HTML_TAG_RE.sub("", text)
    text = _HEADING_PREFIX_RE.sub("", text)
    # Collapse whitespace runs but keep paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_on_paragraph(text: str, max_chars: int) -> list[str]:
    """Split text into pieces ≤ max_chars on paragraph boundaries when possible."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        plen = len(para) + (2 if buf else 0)
        if buf and buf_len + plen > max_chars:
            parts.append("\n\n".join(buf))
            buf = [para]
            buf_len = len(para)
        elif len(para) > max_chars:
            # Single paragraph too long — hard-cut on sentence/space boundary.
            if buf:
                parts.append("\n\n".join(buf))
                buf = []
                buf_len = 0
            for i in range(0, len(para), max_chars):
                parts.append(para[i : i + max_chars])
        else:
            buf.append(para)
            buf_len += plen
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def _chunk_body(body_md: str) -> list[dict]:
    """Split markdown body into corpus chunks keyed by H2 section.

    Returns a list of {section, section_slug, text} dicts. An empty section
    name means the prologue before the first H2 (or the whole body if no H2s).
    """
    sections: list[dict] = []
    current_heading = ""
    current_lines: list[str] = []

    def flush():
        if not current_lines:
            return
        raw = "\n".join(current_lines)
        clean = _strip_for_corpus(raw)
        if not clean:
            return
        pieces = _split_on_paragraph(clean, _CHUNK_MAX_CHARS)
        for i, piece in enumerate(pieces):
            sections.append(
                {
                    "section": current_heading,
                    "section_slug": slugify(current_heading) if current_heading else "",
                    "part": i,  # 0-based; >0 when section split
                    "part_count": len(pieces),
                    "text": piece,
                }
            )

    for line in body_md.split("\n"):
        if line.startswith("## ") and not line.startswith("### "):
            flush()
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    return sections


def _load_faqs(source_dir: Path) -> list[dict]:
    """Read optional {source}/faqs.toml → [{q, a}, ...]. Empty list if missing."""
    fpath = source_dir / "faqs.toml"
    if not fpath.exists():
        return []
    try:
        import tomllib

        with open(fpath, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []
    raw = data.get("faq") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or "").strip()
        a = str(item.get("a") or "").strip()
        if q and a:
            out.append({"q": q, "a": a})
    return out


def _parse_metrics_yaml(content: str) -> list:
    """Parse nested `metrics: [{val, lbl}, ...]` frontmatter using yaml.

    The SDK's parse_frontmatter is flat-only; metrics is the one field that
    needs real YAML. Returns [] if yaml is unavailable or the block isn't a list.
    """
    if not content.startswith("---"):
        return []
    end = content.find("---", 3)
    if end < 0:
        return []
    try:
        import yaml  # pyyaml

        fm = yaml.safe_load(content[3:end]) or {}
    except Exception:
        return []
    m = fm.get("metrics") if isinstance(fm, dict) else None
    return m if isinstance(m, list) else []


from emptyos.sdk.markdown_render import extract_images, render_markdown

from .templates import (
    AI_NOTICE,
    AUTHOR_CARD,
    BACKLINK_ITEM,
    BACKLINKS,
    BASE_TEMPLATE,
    BLOG_PREVIEW,
    CONTACT_BAND,
    DOCS_PAGE_CONTENT,
    EDITIONS_BAND,
    EDITIONS_GROUP,
    FEATURE_CARD,
    FEATURED_POST,
    HERO_STAT,
    INDEX_CONTENT,
    INDEX_CONTENT_PRO,
    LANDING_CONTENT,
    LANG_NAMES,
    LANG_SWITCHER,
    PAGE_CONTENT,
    POST_CARD,
    POST_CONTENT,
    POST_ITEM,
    RELATED_POST_ITEM,
    RELATED_POSTS,
    RSS_ENTRY,
    RSS_TEMPLATE,
    TAG_LINK,
    TAG_PAGE_CONTENT,
    TAGS_INDEX_CONTENT,
    TOC_SIDEBAR,
    get_site_css,
    resolve_theme_vars,
)


def _reading_time(text: str) -> int:
    words = len(text.split())
    return max(1, round(words / 200))


def _first_paragraph(body: str) -> str:
    for line in body.split("\n"):
        line = line.strip()
        if (
            line
            and not line.startswith("#")
            and not line.startswith("!")
            and not line.startswith(">")
        ):
            return line[:200] + ("..." if len(line) > 200 else "")
    return ""


def _cover_img_html(cover: str, css_class: str, alt: str) -> str:
    """Render an <img> for a post cover, or empty string if no cover set.

    Cover paths in frontmatter are root-relative (e.g. "media/cover-slug.png");
    site listings live at root so the path is used as-is.
    """
    if not cover:
        return ""
    safe_alt = alt.replace('"', "&quot;")
    return f'<img src="{cover}" class="{css_class}" alt="{safe_alt}" loading="lazy">'


def _render_post_cards(posts: list[dict]) -> str:
    """Render a list of posts as POST_CARD HTML."""
    return "\n".join(
        POST_CARD.format(
            slug=p["slug"],
            title=p["title"],
            date=p["date"],
            reading_time=p["reading_time"],
            summary=p["summary"],
            tags_html=" ".join(f'<span class="tag">{t}</span>' for t in p["tags"]),
            cover_html=_cover_img_html(p.get("cover", ""), "card-cover", p["title"]),
        )
        for p in posts
    )


def _social_links_html(social_links: str, include_website: bool = False) -> str:
    """Parse comma-separated social URLs into HTML links."""
    if not social_links:
        return ""
    parts = []
    for link in social_links.split(","):
        link = link.strip()
        if "linkedin" in link.lower():
            parts.append(f'<a href="{link}">LinkedIn</a>')
        elif "github" in link.lower():
            parts.append(f'<a href="{link}">GitHub</a>')
        elif "mailto:" in link.lower():
            parts.append(f'<a href="{link}">Email</a>')
        elif include_website and link.startswith("http"):
            parts.append(f'<a href="{link}">Website</a>')
    return " ".join(parts)


_UNSET_CTA_URLS = {"", "#", "#unset", "#todo", "#tbd", "#tba", "#set"}

# CTA URLs come from note bodies rendered onto public pages; a bare/relative
# URL has scheme "". Anything else (javascript:/data:/vbscript:) is dropped.
_ALLOWED_CTA_SCHEMES = {"http", "https", "mailto", "tel", ""}


def _cta_button_html(text: str, url: str, *, primary: bool = False) -> str:
    """Render a hero/contact CTA button. A placeholder/unset URL (``#``, empty,
    ``#todo`` …) renders as a greyed, non-clickable button (no href) instead of
    a dead link — so an unconfigured CTA reads as 'coming soon', not broken.
    A URL with an unsafe scheme renders as nothing at all."""
    from html import escape
    from urllib.parse import urlparse

    cls = "cta-primary" if primary else "cta-secondary"
    label = escape(text or "")
    u = (url or "").strip()
    if u.lower() in _UNSET_CTA_URLS:
        return f'<a class="{cls} cta-disabled" title="Coming soon" aria-disabled="true">{label}</a>'
    if urlparse(u).scheme.lower() not in _ALLOWED_CTA_SCHEMES:
        return ""
    return f'<a href="{escape(u, quote=True)}" class="{cls}">{label}</a>'


def _parse_landing_sections(body_md: str) -> dict:
    """Parse a landing page markdown into hero + feature sections.

    Everything before the first ``## `` heading is the hero (tagline + CTA
    links). Each ``## `` section becomes a feature card, except sections
    named Blog / Updates / Latest which signal a blog preview.
    """
    import re

    lines = body_md.split("\n")
    sections: list[dict] = []
    current_heading: str | None = None
    current_body: list[str] = []
    hero_lines: list[str] = []
    in_hero = True

    for line in lines:
        if line.startswith("## "):
            if in_hero:
                in_hero = False
            elif current_heading is not None:
                sections.append(
                    {"heading": current_heading, "body_md": "\n".join(current_body).strip()}
                )
            current_heading = line[3:].strip()
            current_body = []
        elif in_hero:
            hero_lines.append(line)
        else:
            current_body.append(line)

    if current_heading is not None:
        sections.append({"heading": current_heading, "body_md": "\n".join(current_body).strip()})

    hero_text = "\n".join(hero_lines).strip()

    # Extract CTA links from hero: [Text](url). The (?<!!) guard excludes
    # image markdown ![alt](url) — a hero image is not a CTA button.
    cta_links = re.findall(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", hero_text)
    # Drop link-only lines, then split into tagline (first paragraph) and the
    # rest (rendered as markdown — supports blockquote callouts, lists, etc.).
    cleaned = re.sub(r"^\s*\[.*?\]\(.*?\)\s*$", "", hero_text, flags=re.MULTILINE).strip()
    paragraphs = re.split(r"\n\s*\n", cleaned)
    tagline = paragraphs[0].strip() if paragraphs else ""
    hero_note_md = "\n\n".join(p.strip() for p in paragraphs[1:] if p.strip())

    # Detect reserved sections: blog preview + gallery
    _BLOG_NAMES = {"blog", "updates", "latest", "recent posts"}
    _GALLERY_NAMES = {"gallery", "screenshots", "images", "tour"}
    has_blog = any(s["heading"].lower() in _BLOG_NAMES for s in sections)

    gallery_section = next((s for s in sections if s["heading"].lower() in _GALLERY_NAMES), None)
    gallery_items: list[dict] = []
    if gallery_section:
        for m in re.finditer(
            r"!\[\[([^\]]+\.(?:png|jpe?g|gif|svg|webp))\]\]"
            r"|!\[([^\]]*)\]\(([^)]+\.(?:png|jpe?g|gif|svg|webp))\)",
            gallery_section["body_md"],
            flags=re.IGNORECASE,
        ):
            if m.group(1):
                gallery_items.append({"src": m.group(1), "alt": ""})
            else:
                gallery_items.append({"src": m.group(3), "alt": (m.group(2) or "").strip()})

    # Editions band: one intro line + a table of edition tiles
    #   | icon | name | one-liner | access |
    _EDITIONS_NAMES = {"editions", "distributions"}
    editions_section = next(
        (s for s in sections if s["heading"].lower() in _EDITIONS_NAMES), None
    )
    editions_intro = ""
    edition_tiles: list[dict] = []
    if editions_section:
        body = editions_section["body_md"]
        editions_intro = "\n".join(
            ln for ln in body.split("\n") if not ln.lstrip().startswith("|")
        ).strip()
        for ln in body.split("\n"):
            s = ln.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip().strip("*") for c in s.strip("|").split("|")]
            if len(cells) < 4:
                continue
            icon, name, oneliner, access = cells[0], cells[1], cells[2], cells[3]
            if set(access) <= set("-: ") or access.lower() in {"access", "tag"}:
                continue  # skips the |---| separator and the header row
            if name.lower() in {"name", ""}:
                continue
            edition_tiles.append(
                {
                    "icon": icon,
                    "name": name,
                    "oneliner": oneliner,
                    "access": "private" if access.lower().startswith("priv") else "public",
                }
            )

    # Contact CTA band: a headline paragraph + [text](url) action links.
    _CONTACT_NAMES = {"contact", "get in touch"}
    contact_section = next(
        (s for s in sections if s["heading"].lower() in _CONTACT_NAMES), None
    )
    contact_heading = ""
    contact_ctas: list[tuple[str, str]] = []
    if contact_section:
        body = contact_section["body_md"]
        contact_heading = "\n".join(
            ln for ln in body.split("\n")
            if not re.match(r"^\s*\[.*?\]\(.*?\)\s*$", ln)
        ).strip()
        contact_ctas = re.findall(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", body)

    features = [
        s
        for s in sections
        if s["heading"].lower() not in _BLOG_NAMES
        and s["heading"].lower() not in _GALLERY_NAMES
        and s["heading"].lower() not in _EDITIONS_NAMES
        and s["heading"].lower() not in _CONTACT_NAMES
    ]

    # Split a leading emoji off each feature heading → icon + heading.
    # Covers: pictographs (1F300–1FAFF), misc symbols + dingbats (2600–27BF).
    _ICON_RE = re.compile(r"^([\U0001F300-\U0001FAFF\U00002600-\U000027BF])\s+(.+)$")
    for f in features:
        m = _ICON_RE.match(f["heading"])
        if m:
            f["icon"] = m.group(1)
            f["heading"] = m.group(2).strip()
        else:
            f["icon"] = ""

    # Metric strip: <!-- metrics: 44 Apps · 91% Integrity · 9 Capabilities -->
    metrics: list[dict] = []
    full_md = body_md  # search across whole body, hero or section
    mm = re.search(r"<!--\s*metrics:\s*(.+?)\s*-->", full_md)
    if mm:
        for chunk in re.split(r"\s*[·|]\s*", mm.group(1)):
            chunk = chunk.strip()
            if not chunk:
                continue
            # Pattern: "<value> <label>" — split on first space if value is a
            # number/percent, otherwise treat the whole thing as label.
            mv = re.match(r"^([\d.]+%?)\s+(.+)$", chunk)
            if mv:
                metrics.append({"value": mv.group(1), "label": mv.group(2).strip()})
            else:
                metrics.append({"value": "", "label": chunk})

    return {
        "tagline": tagline,
        "hero_note_md": hero_note_md,
        "cta_links": cta_links,
        "features": features,
        "has_blog_preview": has_blog,
        "gallery_items": gallery_items,
        "gallery_heading": gallery_section["heading"] if gallery_section else "",
        "editions_intro": editions_intro,
        "edition_tiles": edition_tiles,
        "contact_heading": contact_heading,
        "contact_ctas": contact_ctas,
        "metrics": metrics,
    }


class SiteBuilder:
    def __init__(self, vault_dir: str, source_folder: str, output_dir: str, config: dict):
        self.vault_dir = Path(vault_dir)
        self.source_dir = self.vault_dir / source_folder
        self.output_dir = Path(output_dir)
        self.config = config
        # Inline analytics beacon — empty string when disabled. Injected into
        # every built page's <head> via extra_head.
        script = (config.get("analytics_script") or "").strip()
        self.analytics_head = f"<script>{script}</script>" if script else ""
        self.cross_site_html = self._render_cross_site_links(config.get("cross_site_links") or [])
        # Chatbot meta tags + widget script — empty string when disabled.
        # Uses {root} placeholder, resolved per-page like favicon.
        self.chatbot_head = self._render_chatbot_head(config.get("chatbot") or {})
        self.chatbot_enabled = bool((config.get("chatbot") or {}).get("enabled"))

    @staticmethod
    def _render_chatbot_head(cb: dict) -> str:
        if not cb.get("enabled"):
            return ""
        endpoint = (cb.get("endpoint") or "").strip()
        site_id = (cb.get("site_id") or "").strip()
        if not endpoint or not site_id:
            # Misconfigured: enabled but missing endpoint/site_id. Skip.
            return ""
        starters = cb.get("starter_questions") or []
        starters_attr = json.dumps(starters, ensure_ascii=False).replace('"', "&quot;")
        return (
            f'<meta name="chatbot-endpoint" content="{html_escape(endpoint, quote=True)}">\n'
            f'  <meta name="chatbot-site-id" content="{html_escape(site_id, quote=True)}">\n'
            f'  <meta name="chatbot-starters" content="{starters_attr}">\n'
            f'  <script src="{{root}}chatbot-widget.js" defer></script>'
        )

    @staticmethod
    def _render_cross_site_links(links: list) -> str:
        parts = []
        for l in links:
            url = (l.get("url") or "").strip()
            name = (l.get("name") or "").strip()
            if not url or not name:
                continue
            parts.append(f'<a href="{html_escape(url, quote=True)}">{html_escape(name)}</a>')
        if not parts:
            return ""
        return '<span class="cross-site-links">' + " &middot; ".join(parts) + "</span>"

    # Per-file cap for demo_data JSONs inlined into the portfolio bundle.
    # Larger files should stream from a separate URL, not balloon the main page.
    _DEMO_DATA_MAX_BYTES = 1_000_000

    def _inject_demo_data(self, html: str) -> str:
        """Read demo_data/*.json and embed as window.DEMO_DATA[<stem>]. No-op if absent."""
        demo_dir = self.source_dir / "demo_data"
        if not demo_dir.is_dir():
            return html
        bundle: dict = {}
        for dj in sorted(demo_dir.glob("*.json")):
            try:
                size = dj.stat().st_size
                if size > self._DEMO_DATA_MAX_BYTES:
                    print(
                        f"[publish] demo_data: skipping {dj.name} ({size:,} bytes > {self._DEMO_DATA_MAX_BYTES:,} cap)"
                    )
                    continue
                bundle[dj.stem] = json.loads(dj.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError) as e:
                print(f"[publish] demo_data: failed to load {dj.name}: {e}")
        if not bundle:
            return html
        payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
        return html.replace("</head>", f"<script>window.DEMO_DATA = {payload};</script>\n</head>")

    def scan(self, include_drafts: bool = False) -> list[dict]:
        if not self.source_dir.exists():
            return []

        items = []
        for md_file in sorted(self.source_dir.rglob("*.md")):
            if md_file.name.startswith(("_", ".")):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            fm = parse_frontmatter(content)
            publish_val = fm.get("publish", "")
            is_published = str(publish_val).lower() in ("true", "yes")
            if not is_published and not include_drafts:
                continue

            item_type = fm_str(fm, "type") or "post"
            title = fm_str(fm, "title") or md_file.stem.replace("-", " ").title()
            slug = slugify(fm_str(fm, "slug") or md_file.stem)
            date_str = fm_str(fm, "date", "created") or date.today().isoformat()
            tags = fm_list(fm, "tags")
            tags = [t for t in tags if t not in ("publish", "private")]
            summary = fm_str(fm, "summary", "description") or _first_paragraph(
                strip_frontmatter(content)
            )

            item = {
                "path": str(md_file),
                "relative": str(md_file.relative_to(self.vault_dir)),
                "type": item_type,
                "title": title,
                "slug": slug,
                "date": date_str,
                "tags": tags,
                "summary": summary,
                "reading_time": _reading_time(content),
                "featured": str(fm.get("featured", "")).lower() == "true",
                "cover": fm_str(fm, "cover"),
                "draft": not is_published,
                "publish_at": fm_str(fm, "publish_at"),
                # corpus: false → the note still builds + deploys (stays live for
                # SEO/links) but is dropped from corpus.json, so the chatbot never
                # retrieves it. First deprecation lever; pair it with the site's
                # avoid_topics prompt exclusion in the chatbot service.
                "corpus": str(fm.get("corpus", "true")).lower() not in ("false", "no"),
            }

            if item_type == "page":
                item["nav_order"] = int(fm_str(fm, "nav_order") or "99")
                item["nav_label"] = fm_str(fm, "nav_label") or title
                item["layout"] = fm_str(fm, "layout") or "default"
                # nav_top: false → sidebar-only (kept out of the top nav bar)
                item["nav_top"] = str(fm.get("nav_top", "true")).lower() not in ("false", "no")
                # nav_hidden: true → renders + searchable, but out of BOTH the top
                # nav and the doc sidebar. For large auto-generated page sets (e.g.
                # per-app catalog pages) that would otherwise flood the chrome.
                item["nav_hidden"] = str(fm.get("nav_hidden", "false")).lower() in ("true", "yes")

            items.append(item)

        pages = sorted([i for i in items if i["type"] == "page"], key=lambda p: p["nav_order"])
        posts = sorted(
            [i for i in items if i["type"] == "post"], key=lambda p: p["date"], reverse=True
        )
        return pages + posts

    def build(self) -> dict:
        all_items = self.scan()
        if not all_items:
            return {"pages": 0, "posts": 0, "error": "No publishable notes found"}

        pages = [i for i in all_items if i["type"] == "page"]
        posts = [i for i in all_items if i["type"] == "post"]

        # Portfolio template — single-page interactive SPA
        if self.config.get("template") == "portfolio":
            return self._build_portfolio(posts, pages)

        # Prepare output dirs
        site = self.output_dir
        if site.exists():
            for item in site.iterdir():
                if item.name == ".git":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        else:
            site.mkdir(parents=True)
        (site / "posts").mkdir(exist_ok=True)
        (site / "tags").mkdir(exist_ok=True)
        (site / "assets").mkdir(exist_ok=True)

        theme = self.config.get("theme", "void-dark")
        site_name = self.config.get("site_name", "My Site")
        site_desc = self.config.get("site_description", "")
        author_name = self.config.get("author", "")
        author_bio = self.config.get("author_bio", "")
        social_links = self.config.get("social_links", "")
        original_lang = self.config.get("original_language", "en")

        # Write site.css — resolve `design-system-*` themes from KB notes
        # (sync vault read), built-ins from THEME_VARS.
        # NB: SiteBuilder is a plain class — the vault root is self.vault_dir
        # (no BaseApp vault_root property here).
        theme_vars = resolve_theme_vars(theme, self.vault_dir)
        (site / "site.css").write_text(get_site_css(theme, vars=theme_vars), encoding="utf-8")

        # Copy chatbot widget asset if enabled.
        if self.chatbot_enabled:
            self._copy_widget_asset(site)

        # Detect project site mode (has a landing page)
        landing_page = next((p for p in pages if p.get("layout") == "landing"), None)
        is_project_site = landing_page is not None
        doc_pages = [p for p in pages if p != landing_page] if landing_page else pages

        # Build nav HTML. The brand links Home, so we don't repeat a "Home" link.
        # Doc pages with nav_top:false stay in the left sidebar only — keeps the
        # top bar lean instead of duplicating every page across both nav surfaces.
        if is_project_site:
            nav_links = ""
            for pg in doc_pages:
                if not pg.get("nav_top", True) or pg.get("nav_hidden"):
                    continue
                nav_links += f'        <a href="{{root}}{pg["slug"]}.html">{pg["nav_label"]}</a>\n'
            if posts:
                nav_links += '        <a href="{root}blog.html">Blog</a>\n'
            nav_links += '        <a href="{root}tags.html">Tags</a>'
        else:
            nav_links = '<a href="{root}index.html">Blog</a>\n'
            for pg in pages:
                if not pg.get("nav_top", True) or pg.get("nav_hidden"):
                    continue
                nav_links += f'        <a href="{{root}}{pg["slug"]}.html">{pg["nav_label"]}</a>\n'
            nav_links += '        <a href="{root}tags.html">Tags</a>'

        # Published slugs for wikilink resolution
        published_slugs = {}
        for item in all_items:
            entry = (item["slug"], item["type"])
            published_slugs[item["slug"]] = entry
            published_slugs[Path(item["path"]).stem.lower().replace(" ", "-")] = entry

        # Backlinks ("linked mentions"): reverse [[wikilink]] map, resolved with the
        # SAME normalization as resolve_wikilinks() so mentions agree with rendered
        # links. {target_slug: [{slug,title,type,date}, ...]} — deduped per source.
        backlinks: dict[str, list[dict]] = {}
        _wikilink_re = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
        for src in all_items:
            try:
                raw = Path(src["path"]).read_text(encoding="utf-8")
            except Exception:
                continue
            seen: set[str] = set()
            for m in _wikilink_re.finditer(raw):
                lookup = m.group(1).strip().split("#")[0].strip().lower().replace(" ", "-")
                target = published_slugs.get(lookup)
                if not target:
                    continue
                target_slug = target[0]
                if target_slug == src["slug"] or target_slug in seen:
                    continue
                seen.add(target_slug)
                backlinks.setdefault(target_slug, []).append(
                    {
                        "slug": src["slug"],
                        "title": src["title"],
                        "type": src["type"],
                        "date": src["date"],
                    }
                )

        # Check for avatar image
        avatar_file = self.source_dir / "images" / "avatar.png"
        has_avatar = avatar_file.exists()
        if has_avatar:
            self._copy_image("avatar.png", str(avatar_file), site / "assets")

        social_html = _social_links_html(social_links)

        # Author card HTML
        author_card_html = ""
        if author_name:
            author_card_html = AUTHOR_CARD.format(
                name=author_name,
                bio=author_bio,
                links_html=_social_links_html(social_links, include_website=True),
                avatar_img=f'<img src="../assets/avatar.png" class="author-avatar" alt="{author_name}">'
                if has_avatar
                else "",
            )

        # site_url is also computed later (for RSS) — hoisted here so _make_page
        # can build absolute og:image URLs for post pages.
        domain = self.config.get("domain", "")
        site_url = f"https://{domain}/" if domain else ""

        # Favicon + search-engine visibility — both are site-wide, resolved once per build.
        favicon_filename = (self.config.get("favicon") or "").strip()
        favicon_link_html = ""
        if favicon_filename:
            ext = Path(favicon_filename).suffix.lower()
            mime = {
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }.get(ext, "image/png")
            favicon_link_html = f'<link rel="icon" type="{mime}" href="{{root}}{favicon_filename}">'

        search_engines = self.config.get("search_engines", True)
        robots_meta_html = (
            "" if search_engines else '<meta name="robots" content="noindex, nofollow">'
        )

        # Feed is only written when site_url + posts exist (see "--- RSS feed ---"
        # below) — the visible footer link must match, or it 404s.
        has_feed = bool(site_url and posts)

        def _make_page(title, description, content_html, root, out_path, lang="en", cover=""):
            resolved_nav = nav_links.replace("{root}", root)
            head_parts = []
            if favicon_link_html:
                head_parts.append(favicon_link_html.replace("{root}", root))
            if robots_meta_html:
                head_parts.append(robots_meta_html)
            if cover:
                # Build an absolute og:image URL if a site domain is configured.
                # Social scrapers (LinkedIn, Twitter) require absolute URLs.
                og_url = cover
                if site_url:
                    og_url = site_url.rstrip("/") + "/" + cover.lstrip("/")
                og_safe = og_url.replace('"', "&quot;")
                head_parts.append(f'<meta property="og:image" content="{og_safe}">')
                head_parts.append(f'<meta name="twitter:image" content="{og_safe}">')
            if self.analytics_head:
                head_parts.append(self.analytics_head)
            if self.chatbot_head:
                head_parts.append(self.chatbot_head.replace("{root}", root))
            extra_head = "\n  ".join(head_parts)
            page_html = BASE_TEMPLATE.format(
                title=title,
                site_name=site_name,
                description=description[:160],
                root=root,
                extra_head=extra_head,
                nav_links=resolved_nav,
                content=content_html,
                lang=lang,
                cross_site_html=self.cross_site_html,
                rss_link=f' &middot; <a href="{root}atom.xml">RSS</a>' if has_feed else "",
            )
            out_path.write_text(page_html, encoding="utf-8")

        images_copied = 0

        # --- Render standalone pages ---
        for pg in pages:
            if pg is landing_page:
                continue  # landing page rendered as index.html below
            content = Path(pg["path"]).read_text(encoding="utf-8")
            body_md = strip_frontmatter(content).strip()
            for img_name in extract_images(body_md):
                self._copy_image(img_name, pg["path"], site / "assets")
                images_copied += 1
            body_html, _ = render_markdown(
                body_md, published_slugs, assets_prefix="assets/", link_prefix=""
            )

            if is_project_site and doc_pages:
                # Docs layout with sidebar
                sidebar_links = ""
                for dp in doc_pages:
                    if dp.get("nav_hidden"):
                        continue
                    active = ' class="active"' if dp["slug"] == pg["slug"] else ""
                    sidebar_links += f'      <li><a href="{dp["slug"]}.html"{active}>{dp["nav_label"]}</a></li>\n'
                page_html = DOCS_PAGE_CONTENT.format(
                    title=pg["title"],
                    sidebar_links=sidebar_links,
                ).replace("%%BODY%%", body_html)
            else:
                page_html = PAGE_CONTENT.format(title=pg["title"], body=body_html)

            _make_page(pg["title"], pg["summary"], page_html, "", site / f"{pg['slug']}.html")

        # --- Render posts ---
        for post in posts:
            content = Path(post["path"]).read_text(encoding="utf-8")
            body_md = strip_frontmatter(content).strip()

            for img_name in extract_images(body_md):
                self._copy_image(img_name, post["path"], site / "assets")
                images_copied += 1

            body_html, toc_raw = render_markdown(
                body_md, published_slugs, assets_prefix="../assets/", link_prefix="../"
            )

            toc_html = TOC_SIDEBAR.format(toc=toc_raw) if toc_raw and "<li>" in toc_raw else ""

            tags_html = " ".join(
                f'<a href="../tags/{slugify(t)}.html" class="tag">{t}</a>' for t in post["tags"]
            )

            # Related posts
            related_html = ""
            if post["tags"]:
                post_tags = set(post["tags"])
                scored = [
                    (len(post_tags & set(o["tags"])), o) for o in posts if o["slug"] != post["slug"]
                ]
                scored = [(s, o) for s, o in scored if s > 0]
                scored.sort(key=lambda x: x[0], reverse=True)
                if scored:
                    related_items = "\n".join(
                        RELATED_POST_ITEM.format(
                            root="../", slug=s["slug"], title=s["title"], date=s["date"]
                        )
                        for _, s in scored[:3]
                    )
                    related_html = RELATED_POSTS.format(items=related_items)

            # Backlinks ("linked mentions") — notes that wikilink to this post.
            backlinks_html = ""
            mentions = backlinks.get(post["slug"], [])
            if mentions:
                mentions_sorted = sorted(mentions, key=lambda m: m["date"], reverse=True)
                bl_items = "\n".join(
                    BACKLINK_ITEM.format(
                        root="../",
                        href=(
                            f"{s['slug']}.html"
                            if s["type"] == "page"
                            else f"posts/{s['slug']}.html"
                        ),
                        title=html_escape(s["title"]),
                        date=s["date"],
                    )
                    for s in mentions_sorted
                )
                backlinks_html = BACKLINKS.format(count=len(mentions_sorted), items=bl_items)

            post_html = POST_CONTENT.format(
                title=post["title"],
                date=post["date"],
                reading_time=post["reading_time"],
                tags_html=tags_html,
                body=body_html,
                root="../",
                related_html=related_html,
                backlinks_html=backlinks_html,
                toc_html=toc_html,
                author_card_html=author_card_html,
                lang_switch_html="",
                ai_notice_html="",
            )

            # JSON-LD structured data (SEO) — BlogPosting. Valid in <body>; Google
            # accepts it. json.dumps handles all escaping.
            post_url = (
                site_url.rstrip("/") + f"/posts/{post['slug']}.html" if site_url else ""
            )
            ld: dict = {
                "@context": "https://schema.org",
                "@type": "BlogPosting",
                "headline": post["title"],
                "datePublished": post["date"],
                "dateModified": post["date"],
            }
            if post.get("summary"):
                ld["description"] = post["summary"][:300]
            if post_url:
                ld["url"] = post_url
                ld["mainEntityOfPage"] = post_url
            if author_name:
                ld["author"] = {"@type": "Person", "name": author_name}
            if post.get("cover"):
                cov = post["cover"]
                ld["image"] = (
                    site_url.rstrip("/") + "/" + cov.lstrip("/") if site_url else cov
                )
            post_html = (
                '<script type="application/ld+json">'
                + json.dumps(ld, ensure_ascii=False)
                + "</script>\n"
                + post_html
            )

            _make_page(
                post["title"],
                post["summary"],
                post_html,
                "../",
                site / "posts" / f"{post['slug']}.html",
                lang=original_lang,
                cover=post.get("cover", ""),
            )

        # --- Index page ---
        if landing_page:
            # Project site: landing page becomes index.html
            lp_content = Path(landing_page["path"]).read_text(encoding="utf-8")
            lp_body_md = strip_frontmatter(lp_content).strip()
            for img_name in extract_images(lp_body_md):
                self._copy_image(img_name, landing_page["path"], site / "assets")
                images_copied += 1

            parsed = _parse_landing_sections(lp_body_md)

            # CTA buttons
            cta_html = ""
            for i, (text, url) in enumerate(parsed["cta_links"]):
                cta_html += _cta_button_html(text, url, primary=(i == 0)) + "\n"

            # Feature cards — render each section's markdown, then inject
            # via string replace to avoid .format() eating HTML braces
            feature_cards = ""
            for feat in parsed["features"]:
                feat_html, _ = render_markdown(
                    feat["body_md"],
                    published_slugs,
                    assets_prefix="assets/",
                    link_prefix="",
                )
                icon = feat.get("icon") or ""
                icon_html = f'<div class="feature-icon">{icon}</div>' if icon else ""
                card = FEATURE_CARD.format(heading=feat["heading"], icon_html=icon_html)
                feature_cards += card.replace("%%BODY%%", feat_html)

            # Metric strip
            metrics_html = ""
            if parsed.get("metrics"):
                from html import escape as _esc

                tiles = []
                for m in parsed["metrics"]:
                    val = _esc(m.get("value") or "")
                    lbl = _esc(m.get("label") or "")
                    tiles.append(
                        f'<div class="metric-tile">'
                        f'<div class="metric-value">{val}</div>'
                        f'<div class="metric-label">{lbl}</div>'
                        f"</div>"
                    )
                metrics_html = f'<div class="metric-strip">{"".join(tiles)}</div>'

            # Blog preview (latest 3 posts)
            blog_preview_html = ""
            if parsed["has_blog_preview"] and posts:
                blog_preview_html = BLOG_PREVIEW.format(post_cards=_render_post_cards(posts[:3]))

            hero_note_html = ""
            if parsed.get("hero_note_md"):
                note_html, _ = render_markdown(
                    parsed["hero_note_md"],
                    published_slugs,
                    assets_prefix="assets/",
                    link_prefix="",
                )
                hero_note_html = f'<div class="hero-note">{note_html}</div>'

            # Gallery — reserved ## Screenshots / ## Gallery section
            gallery_html = ""
            if parsed.get("gallery_items"):
                from html import escape as _esc

                tiles = []
                for it in parsed["gallery_items"]:
                    src = "assets/" + Path(it["src"]).name
                    alt = _esc(it["alt"] or "")
                    cap = f"<figcaption>{alt}</figcaption>" if it["alt"] else ""
                    tiles.append(
                        f'<figure class="gallery-tile">'
                        f'<a href="{src}" target="_blank" rel="noopener">'
                        f'<img src="{src}" alt="{alt}" loading="lazy">'
                        f"</a>{cap}</figure>"
                    )
                gallery_html = (
                    f'<div class="gallery-section">'
                    f'<div class="section-heading">{_esc(parsed["gallery_heading"])}</div>'
                    f'<div class="gallery-grid">{"".join(tiles)}</div>'
                    f"</div>"
                )

            # Editions band — reserved ## Editions section → full-width concept band.
            # Tiles split into two labelled groups: open foundation (public) →
            # tailored distributions (private), telling the one-core → many-uses story.
            editions_html = ""
            if parsed.get("edition_tiles"):
                from html import escape as _esc

                def _edition_tile(e):
                    badge = "Public" if e["access"] == "public" else "Private"
                    return (
                        f'<div class="edition-tile edition-{e["access"]}">'
                        f'<div class="edition-icon">{_esc(e["icon"])}</div>'
                        f'<div class="edition-name">{_esc(e["name"])}</div>'
                        f'<div class="edition-oneliner">{_esc(e["oneliner"])}</div>'
                        f'<span class="edition-badge">{badge}</span>'
                        f"</div>"
                    )

                groups = ""
                pub = [e for e in parsed["edition_tiles"] if e["access"] == "public"]
                priv = [e for e in parsed["edition_tiles"] if e["access"] != "public"]
                if pub:
                    groups += EDITIONS_GROUP.format(
                        label="Open foundation &middot; public",
                        grid_mod=" editions-grid-public",
                        tiles="".join(_edition_tile(e) for e in pub),
                    )
                if priv:
                    groups += EDITIONS_GROUP.format(
                        label="Tailored distributions &middot; private",
                        grid_mod="",
                        tiles="".join(_edition_tile(e) for e in priv),
                    )

                intro_md = parsed.get("editions_intro") or ""
                intro_html = ""
                if intro_md:
                    intro_html, _ = render_markdown(
                        intro_md, published_slugs, assets_prefix="assets/", link_prefix=""
                    )
                    intro_html = re.sub(
                        r"^\s*<p>(.*)</p>\s*$", r"\1", intro_html.strip(), flags=re.DOTALL
                    ).strip()
                editions_html = EDITIONS_BAND.format(intro=intro_html, groups=groups)

            # Contact CTA band — reserved ## Contact section → CTA band before the blog.
            contact_html = ""
            if parsed.get("contact_ctas"):
                from html import escape as _esc

                # Unsafe schemes (javascript:/data:/vbscript:) are dropped inside
                # _cta_button_html — the one choke-point for note-body CTA links.
                btns = ""
                for i, (text, url) in enumerate(parsed["contact_ctas"]):
                    btns += _cta_button_html(text, url, primary=(i == 0))
                contact_html = CONTACT_BAND.format(
                    heading=_esc(parsed.get("contact_heading") or ""), buttons=btns
                )

            # Render the tagline as inline markdown (so **bold** / *italic* / links
            # work), then strip the single wrapping <p> to keep it inline.
            tagline_html, _ = render_markdown(
                parsed["tagline"], published_slugs, assets_prefix="assets/", link_prefix=""
            )
            tagline_html = re.sub(
                r"^\s*<p>(.*)</p>\s*$", r"\1", tagline_html.strip(), flags=re.DOTALL
            ).strip()

            index_html = LANDING_CONTENT.format(
                title=landing_page["title"],
                tagline=tagline_html,
                hero_note_html=hero_note_html,
                cta_html=cta_html,
                metrics_html=metrics_html,
                feature_cards=feature_cards,
                gallery_html=gallery_html,
                editions_html=editions_html,
                contact_html=contact_html,
                blog_preview_html=blog_preview_html,
            )
            _make_page(
                landing_page["title"],
                landing_page.get("summary", site_desc),
                index_html,
                "",
                site / "index.html",
            )

            # Generate blog listing page
            if posts:
                blog_html = (
                    '<div class="section-heading">All Posts</div>\n'
                    f'<div class="post-grid">\n{_render_post_cards(posts)}\n</div>'
                )
                _make_page("Blog", site_desc, blog_html, "", site / "blog.html")

        else:
            # Blog site: classic hero + featured + card grid
            featured_post = next(
                (p for p in posts if p.get("featured")), posts[0] if posts else None
            )
            remaining_posts = [p for p in posts if p != featured_post]

            featured_html = ""
            if featured_post:
                ftags = " ".join(f'<span class="tag">{t}</span>' for t in featured_post["tags"])
                featured_html = FEATURED_POST.format(
                    slug=featured_post["slug"],
                    title=featured_post["title"],
                    summary=featured_post["summary"],
                    date=featured_post["date"],
                    reading_time=featured_post["reading_time"],
                    tags_html=ftags,
                    cover_html=_cover_img_html(
                        featured_post.get("cover", ""), "featured-cover", featured_post["title"]
                    ),
                )

            post_cards = _render_post_cards(remaining_posts)

            avatar_html = (
                f'<img src="assets/avatar.png" class="hero-avatar" alt="{author_name}">'
                if has_avatar
                else ""
            )
            hero_role = (self.config.get("hero_role") or "").strip()
            if hero_role:
                hero_summary = (self.config.get("hero_summary") or "").strip()
                hero_secondary = (self.config.get("hero_secondary") or "").strip()
                stats_raw = (self.config.get("hero_stats") or "").strip()
                cv_url = (self.config.get("hero_cv_url") or "").strip()
                case_studies_url = (self.config.get("hero_case_studies_url") or "").strip()

                def _render_stats_group(raw: str, group_label: str) -> str:
                    """Render one labeled stats grid. Format:
                       'VALUE|LABEL<sep>VALUE|LABEL', sep = ';' if any present, else ','."""
                    if not raw:
                        return ""
                    sep = ";" if ";" in raw else ","
                    parts = []
                    for entry in raw.split(sep):
                        entry = entry.strip()
                        if not entry:
                            continue
                        if "|" in entry:
                            value, label = entry.split("|", 1)
                        else:
                            value, label = entry, ""
                        parts.append(HERO_STAT.format(value=value.strip(), label=label.strip()))
                    if not parts:
                        return ""
                    label_html = (
                        f'<div class="stats-label">{group_label}</div>'
                        if group_label.strip()
                        else ""
                    )
                    return (
                        '<div class="stats-group">'
                        + label_html
                        + '<div class="stats">'
                        + "".join(parts)
                        + "</div></div>"
                    )

                stats_label = (self.config.get("hero_stats_label") or "").strip()
                stats_2 = (self.config.get("hero_stats_2") or "").strip()
                stats_2_label = (self.config.get("hero_stats_2_label") or "").strip()
                stats_html = (
                    _render_stats_group(stats_raw, stats_label)
                    + _render_stats_group(stats_2, stats_2_label)
                )

                cta_parts = []
                if cv_url:
                    if cv_url.startswith("mailto:"):
                        cta_parts.append(
                            f'<a class="primary" href="{cv_url}">Contact for CV &rarr;</a>'
                        )
                    else:
                        cta_parts.append(
                            f'<a class="primary" href="{cv_url}" download>Download CV</a>'
                        )
                if case_studies_url:
                    cta_parts.append(
                        f'<a class="secondary" href="{case_studies_url}">Case studies &rarr;</a>'
                    )
                cta_html = (
                    '<div class="cta">' + "".join(cta_parts) + "</div>" if cta_parts else ""
                )

                secondary_html = (
                    f'<div class="secondary-line">{hero_secondary}</div>'
                    if hero_secondary
                    else ""
                )

                index_html = INDEX_CONTENT_PRO.format(
                    author_name=author_name or site_name,
                    hero_role=hero_role,
                    hero_summary=hero_summary,
                    stats_html=stats_html,
                    cta_html=cta_html,
                    secondary_html=secondary_html,
                    social_html=social_html,
                    featured_html=featured_html,
                    post_cards=post_cards,
                    avatar_html=avatar_html,
                )
            else:
                index_html = INDEX_CONTENT.format(
                    author_name=author_name or site_name,
                    site_description=site_desc,
                    social_html=social_html,
                    featured_html=featured_html,
                    post_cards=post_cards,
                    avatar_html=avatar_html,
                )
            _make_page("Home", site_desc, index_html, "", site / "index.html")

        # --- Tag pages ---
        tag_map: dict[str, list[dict]] = {}
        for p in posts:
            for t in p["tags"]:
                tag_map.setdefault(t, []).append(p)

        for tag_name, tag_posts in tag_map.items():
            tag_slug = slugify(tag_name)
            tag_post_items = "\n".join(
                POST_ITEM.format(
                    slug=p["slug"],
                    title=p["title"],
                    date=p["date"],
                    tags_html="",
                    summary=p["summary"],
                )
                for p in tag_posts
            )
            tag_html = TAG_PAGE_CONTENT.format(tag=tag_name, post_items=tag_post_items, root="../")
            _make_page(
                f"Tag: {tag_name}",
                f"Posts tagged {tag_name}",
                tag_html,
                "../",
                site / "tags" / f"{tag_slug}.html",
            )

        tag_links = "\n".join(
            TAG_LINK.format(slug=slugify(t), name=t, count=len(ps))
            for t, ps in sorted(tag_map.items())
        )
        _make_page(
            "Tags",
            "All tags",
            TAGS_INDEX_CONTENT.format(tag_links=tag_links),
            "",
            site / "tags.html",
        )

        # --- Copy media (podcast audio/video) ---
        self._copy_media(site)

        # --- Passthrough: standalone .html and .pdf files at source root ---
        # Any .html or .pdf file sitting directly in the source folder
        # (not a generated post/page) is copied as-is to the site root so
        # absolute links work (e.g. /cv.pdf).
        for ext in ("*.html", "*.pdf"):
            for fp in self.source_dir.glob(ext):
                shutil.copy2(str(fp), str(site / fp.name))

        # --- Search index (posts + pages) ---
        search_data = [
            {
                "title": p["title"],
                "slug": p["slug"],
                "date": p["date"],
                "summary": p["summary"][:200],
                "tags": p["tags"],
                "type": p["type"],
                "body_preview": strip_frontmatter(
                    Path(p["path"]).read_text(encoding="utf-8")
                ).strip()[:300],
            }
            for p in (posts + [pg for pg in doc_pages if pg is not landing_page])
        ]
        (site / "search-index.json").write_text(
            json.dumps(search_data, ensure_ascii=False), encoding="utf-8"
        )

        # --- sitemap.xml (SEO) — absolute URLs, so it needs a configured domain ---
        if site_url:
            sm_urls = [site_url]
            for pg in pages:
                if pg is landing_page:
                    continue  # landing renders as index.html (home url above)
                sm_urls.append(f"{site_url}{pg['slug']}.html")
            for p in posts:
                sm_urls.append(f"{site_url}posts/{p['slug']}.html")
            if tag_map:
                sm_urls.append(f"{site_url}tags.html")
                for t in tag_map:
                    sm_urls.append(f"{site_url}tags/{slugify(t)}.html")
            sm_body = "\n".join(
                f"  <url><loc>{html_escape(u)}</loc></url>" for u in sm_urls
            )
            (site / "sitemap.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + sm_body
                + "\n</urlset>\n",
                encoding="utf-8",
            )

        # --- CNAME ---
        if domain:
            (site / "CNAME").write_text(domain, encoding="utf-8")

        # --- .nojekyll ---
        (site / ".nojekyll").write_text("", encoding="utf-8")

        # --- Favicon ---
        if favicon_filename:
            fav_src = self.source_dir / favicon_filename
            if fav_src.exists():
                shutil.copy2(str(fav_src), str(site / favicon_filename))

        # --- robots.txt ---
        if not search_engines:
            (site / "robots.txt").write_text(
                "User-agent: *\nDisallow: /\n",
                encoding="utf-8",
            )
        elif domain:
            (site / "robots.txt").write_text(
                f"User-agent: *\nAllow: /\nSitemap: {site_url}sitemap.xml\n",
                encoding="utf-8",
            )

        # --- RSS feed ---
        if site_url and posts:
            rss_entries = "\n".join(
                RSS_ENTRY.format(
                    title=p["title"],
                    slug=p["slug"],
                    date=p["date"],
                    summary=p["summary"][:300],
                    site_url=site_url,
                )
                for p in posts[:20]
            )
            rss = RSS_TEMPLATE.format(
                site_name=site_name,
                site_description=site_desc,
                site_url=site_url,
                updated=posts[0]["date"] + "T00:00:00Z",
                author=author_name,
                entries=rss_entries,
            )
            (site / "atom.xml").write_text(rss, encoding="utf-8")

        # --- Custom 404 page (root-absolute links so it works at any depth) ---
        not_found_html = (
            '<article style="text-align:center;padding:80px 20px">'
            '<h1 style="font-size:3rem;color:var(--text-heading);margin-bottom:8px">404</h1>'
            '<p style="color:var(--text-secondary);margin-bottom:24px">'
            "This page doesn't exist or has moved.</p>"
            '<a href="/index.html" class="tag">&larr; Back home</a>'
            "</article>"
        )
        _make_page(
            "Page not found",
            "The page you're looking for doesn't exist.",
            not_found_html,
            "/",
            site / "404.html",
        )

        # --- Corpus for downstream consumers (chatbot, search, embeddings) ---
        self._emit_corpus(
            site_dir=site,
            posts=posts,
            pages=[pg for pg in pages if pg is not landing_page],
            landing=landing_page,
        )

        return {
            "pages": len(pages),
            "posts": len(posts),
            "tags": len(tag_map),
            "images": images_copied,
            "output": str(site),
        }

    # ── Corpus emission ───────────────────────────────────────────────

    def _site_meta(self) -> dict:
        return {
            "site_name": self.config.get("site_name", ""),
            "site_description": self.config.get("site_description", ""),
            "author": self.config.get("author", ""),
            "domain": self.config.get("domain", ""),
        }

    def _post_url(self, slug: str, section_slug: str = "") -> str:
        anchor = f"#{section_slug}" if section_slug else ""
        return f"/posts/{slug}.html{anchor}"

    def _page_url(self, slug: str, section_slug: str = "", is_landing: bool = False) -> str:
        anchor = f"#{section_slug}" if section_slug else ""
        if is_landing:
            return f"/index.html{anchor}"
        return f"/{slug}.html{anchor}"

    @staticmethod
    def _chunk_id(base: str, sec: dict) -> str:
        """Derive a unique chunk id from a base + section dict.

        Adds a #section anchor when the H2 has a slug, and a :NN suffix when
        paragraph-splitting produced multiple chunks within the same section.
        """
        cid = base
        if sec.get("section_slug"):
            cid += f"#{sec['section_slug']}"
        if sec.get("part_count", 1) > 1:
            cid += f":{sec['part']}"
        return cid

    def _emit_corpus(
        self,
        site_dir: Path,
        posts: list[dict] | None = None,
        pages: list[dict] | None = None,
        landing: dict | None = None,
    ) -> None:
        """Write site/corpus.json with chunked content + optional faqs.

        One chunk per H2 section, each capped at _CHUNK_MAX_CHARS. Downstream
        consumers (chatbot service in services/chatbot/) fetch this file from
        the deployed site and use it as retrieval/system-prompt context.
        """
        chunks: list[dict] = []

        for post in posts or []:
            if not post.get("corpus", True):
                continue
            try:
                content = Path(post["path"]).read_text(encoding="utf-8")
            except Exception:
                continue
            body_md = strip_frontmatter(content).strip()
            sections = _chunk_body(body_md)
            for sec in sections:
                chunks.append(
                    {
                        "id": self._chunk_id(f"post:{post['slug']}", sec),
                        "type": "post",
                        "slug": post["slug"],
                        "title": post["title"],
                        "section": sec["section"],
                        "tags": post.get("tags", []),
                        "url": self._post_url(post["slug"], sec["section_slug"]),
                        "text": sec["text"],
                    }
                )

        for pg in pages or []:
            if not pg.get("corpus", True):
                continue
            try:
                content = Path(pg["path"]).read_text(encoding="utf-8")
            except Exception:
                continue
            body_md = strip_frontmatter(content).strip()
            sections = _chunk_body(body_md)
            for sec in sections:
                chunks.append(
                    {
                        "id": self._chunk_id(f"page:{pg['slug']}", sec),
                        "type": "page",
                        "slug": pg["slug"],
                        "title": pg["title"],
                        "section": sec["section"],
                        "tags": pg.get("tags", []),
                        "url": self._page_url(pg["slug"], sec["section_slug"]),
                        "text": sec["text"],
                    }
                )

        if landing and landing.get("corpus", True):
            try:
                content = Path(landing["path"]).read_text(encoding="utf-8")
                body_md = strip_frontmatter(content).strip()
                for sec in _chunk_body(body_md):
                    chunks.append(
                        {
                            "id": self._chunk_id("landing", sec),
                            "type": "landing",
                            "slug": landing["slug"],
                            "title": landing["title"],
                            "section": sec["section"],
                            "tags": landing.get("tags", []),
                            "url": self._page_url(
                                landing["slug"], sec["section_slug"], is_landing=True
                            ),
                            "text": sec["text"],
                        }
                    )
            except Exception:
                pass

        corpus = {
            **self._site_meta(),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "chunks": chunks,
            "faqs": _load_faqs(self.source_dir),
        }
        (site_dir / "corpus.json").write_text(
            json.dumps(corpus, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def build_translations(self, translate_fn) -> dict:
        """Build translated versions of all posts.

        Args:
            translate_fn: async callable(text, target_lang) -> translated_text
                Called only for uncached translations.

        Returns:
            Stats dict.
        """
        import asyncio

        languages = [l.strip() for l in (self.config.get("languages") or "").split(",") if l.strip()]
        original_lang = self.config.get("original_language", "en")
        if not languages:
            return {"translated": 0, "message": "No target languages configured"}

        all_items = self.scan()
        posts = [i for i in all_items if i["type"] == "post"]
        site = self.output_dir
        cache_dir = self.output_dir.parent / "translations"
        cache_dir.mkdir(parents=True, exist_ok=True)

        site_name = self.config.get("site_name", "My Site")
        author_name = self.config.get("author", "")
        author_bio = self.config.get("author_bio", "")

        # Published slugs
        published_slugs = {}
        for item in all_items:
            entry = (item["slug"], item["type"])
            published_slugs[item["slug"]] = entry
            published_slugs[Path(item["path"]).stem.lower().replace(" ", "-")] = entry

        # Nav
        nav_links = '<a href="{root}index.html">Blog</a>\n'
        pages = [i for i in all_items if i["type"] == "page"]
        for pg in pages:
            nav_links += f'        <a href="{{root}}{pg["slug"]}.html">{pg["nav_label"]}</a>\n'
        nav_links += '        <a href="{root}tags.html">Tags</a>'

        # Author card
        author_card_html = ""
        if author_name:
            author_card_html = AUTHOR_CARD.format(
                name=author_name, bio=author_bio, links_html="", avatar_img=""
            )

        # Mirrors the main build's has_feed: atom.xml exists only for domain+posts sites.
        has_feed = bool(self.config.get("domain", "") and posts)

        translated_count = 0

        for lang in languages:
            if lang == original_lang:
                continue

            for post in posts:
                cache_file = cache_dir / f"{post['slug']}-{lang}.md"
                content = Path(post["path"]).read_text(encoding="utf-8")
                body_md = strip_frontmatter(content).strip()

                # Check cache
                if cache_file.exists():
                    translated_md = cache_file.read_text(encoding="utf-8")
                else:
                    # Translate via provided function
                    translated_md = asyncio.get_event_loop().run_until_complete(
                        translate_fn(body_md, lang)
                    )
                    cache_file.write_text(translated_md, encoding="utf-8")
                    translated_count += 1

                body_html, toc_raw = render_markdown(
                    translated_md, published_slugs, assets_prefix="../assets/", link_prefix="../"
                )

                # TOC
                toc_raw = toc_raw
                toc_html = TOC_SIDEBAR.format(toc=toc_raw) if toc_raw else ""

                tags_html = " ".join(
                    f'<a href="../tags/{slugify(t)}.html" class="tag">{t}</a>' for t in post["tags"]
                )

                # Language switcher
                orig_label = LANG_NAMES.get(original_lang, original_lang.upper())
                lang_links = f'  <a href="{post["slug"]}.html">{orig_label}</a>\n'
                for tl in languages:
                    if tl == original_lang:
                        continue
                    tl_name = LANG_NAMES.get(tl, tl.upper())
                    active = ' class="active"' if tl == lang else ""
                    lang_links += f'  <a href="{post["slug"]}-{tl}.html"{active}>{tl_name}</a>\n'
                lang_switch_html = LANG_SWITCHER.format(links=lang_links)

                ai_notice_html = AI_NOTICE.format(
                    original_lang=orig_label,
                    original_url=f"{post['slug']}.html",
                )

                post_html = POST_CONTENT.format(
                    title=post["title"],
                    date=post["date"],
                    reading_time=post["reading_time"],
                    tags_html=tags_html,
                    body=body_html,
                    root="../",
                    related_html="",
                    backlinks_html="",
                    toc_html=toc_html,
                    author_card_html=author_card_html,
                    lang_switch_html=lang_switch_html,
                    ai_notice_html=ai_notice_html,
                )

                resolved_nav = nav_links.replace("{root}", "../")
                page_html = BASE_TEMPLATE.format(
                    title=post["title"],
                    site_name=site_name,
                    description=post["summary"][:160],
                    root="../",
                    extra_head=self.analytics_head,
                    nav_links=resolved_nav,
                    content=post_html,
                    lang=lang,
                    cross_site_html=self.cross_site_html,
                    rss_link=' &middot; <a href="../atom.xml">RSS</a>' if has_feed else "",
                )
                (site / "posts" / f"{post['slug']}-{lang}.html").write_text(
                    page_html, encoding="utf-8"
                )

        # Add language switcher to original posts too
        if languages:
            for post in posts:
                post_path = site / "posts" / f"{post['slug']}.html"
                if post_path.exists():
                    html = post_path.read_text(encoding="utf-8")
                    orig_label = LANG_NAMES.get(original_lang, original_lang.upper())
                    lang_links = (
                        f'  <a href="{post["slug"]}.html" class="active">{orig_label}</a>\n'
                    )
                    for tl in languages:
                        if tl == original_lang:
                            continue
                        tl_name = LANG_NAMES.get(tl, tl.upper())
                        lang_links += f'  <a href="{post["slug"]}-{tl}.html">{tl_name}</a>\n'
                    switcher = LANG_SWITCHER.format(links=lang_links)
                    # Insert after opening <article> tag
                    html = html.replace("<article>\n  \n  \n", f"<article>\n  {switcher}\n  \n", 1)
                    post_path.write_text(html, encoding="utf-8")

        return {
            "translated": translated_count,
            "languages": languages,
            "cached": len(posts) * len(languages) - translated_count,
        }

    def _copy_widget_asset(self, site_dir: Path) -> None:
        """Copy chatbot-widget.js from apps/publish/static/ into site root."""
        src = Path(__file__).parent / "static" / "chatbot-widget.js"
        if not src.exists():
            return
        dest = site_dir / "chatbot-widget.js"
        shutil.copy2(str(src), str(dest))

    def _copy_image(self, img_name: str, source_md: str, assets_dir: Path) -> None:
        source_path = Path(source_md).parent / img_name
        if not source_path.exists():
            source_path = Path(source_md).parent / "images" / img_name
        if not source_path.exists():
            source_path = self.vault_dir / "99_Attachments" / img_name
        if not source_path.exists():
            matches = list(self.source_dir.rglob(img_name))
            if matches:
                source_path = matches[0]
        if source_path.exists():
            dest = assets_dir / img_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(source_path, dest)

    def _copy_media(self, site_dir: Path) -> int:
        """Copy media files from source media/ to site media/ (same relative path)."""
        media_dir = self.source_dir / "media"
        if not media_dir.exists():
            return 0
        copied = 0
        media_out = site_dir / "media"
        media_out.mkdir(parents=True, exist_ok=True)
        for f in media_dir.iterdir():
            if f.suffix.lower() in (
                ".mp3",
                ".mp4",
                ".wav",
                ".ogg",
                ".m4a",
                ".webm",
                ".png",
                ".jpg",
                ".json",
                ".js",
            ):
                dest = media_out / f.name
                if not dest.exists():
                    shutil.copy2(str(f), str(dest))
                    copied += 1
        return copied

    def _build_portfolio(self, posts: list[dict], pages: list[dict]) -> dict:
        """Build a portfolio site — single interactive SPA with embedded data."""
        import re
        import tomllib

        site = self.output_dir
        if site.exists():
            for item in site.iterdir():
                if item.name == ".git":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        else:
            site.mkdir(parents=True)

        # Read portfolio config
        config_path = self.source_dir / "_portfolio.toml"
        pcfg = {}
        if config_path.exists():
            with open(config_path, "rb") as f:
                pcfg = tomllib.load(f)

        # Collect project data with full bodies
        CATEGORY_MAP = {
            "ai": "ai",
            "data-science": "data-science",
            "business-analysis": "digital-transformation",
            "digital-transformation": "digital-transformation",
            "engineering": "engineering",
        }
        CATEGORY_LABELS = {
            "ai": "AI & Machine Learning",
            "data-science": "Data Science",
            "engineering": "Engineering & Modelling",
            "digital-transformation": "Digital Transformation & BA",
        }

        projects = []
        for p in posts:
            content = Path(p["path"]).read_text(encoding="utf-8", errors="ignore")
            fm = parse_frontmatter(content)
            body = strip_frontmatter(content)
            # Accept `categories: [ai, engineering]` list or legacy `category: ai` string
            raw_cats_val = fm.get("categories", fm.get("category", ""))
            if isinstance(raw_cats_val, list):
                raw_cats = [str(c).strip() for c in raw_cats_val if str(c).strip()]
            elif isinstance(raw_cats_val, str) and raw_cats_val.strip():
                raw_cats = [raw_cats_val.strip()]
            else:
                raw_cats = []
            cat_groups = []
            for c in raw_cats:
                g = CATEGORY_MAP.get(c, c)
                if g and g not in cat_groups:
                    cat_groups.append(g)
            cat_labels = [CATEGORY_LABELS.get(g, g) for g in cat_groups]
            tags = fm_list(fm, "tags")
            # Parse metrics from frontmatter (nested dicts need real YAML)
            metrics = _parse_metrics_yaml(content)
            projects.append(
                {
                    "slug": p["slug"],
                    "title": p["title"],
                    "category": raw_cats[0] if raw_cats else "",
                    "categories": raw_cats,
                    "categoryGroup": cat_groups[0] if cat_groups else "",
                    "categoryGroups": cat_groups,
                    "categoryLabel": cat_labels[0] if cat_labels else "",
                    "categoryLabels": cat_labels,
                    "date": str(p.get("date", "")),
                    "tags": tags,
                    "summary": p.get("summary", ""),
                    "context": fm_str(fm, "context"),
                    "metrics": metrics,
                    "body": body,
                }
            )
        projects.sort(key=lambda p: p["date"], reverse=True)

        # About content
        about_body = ""
        about_path = self.source_dir / "about.md"
        if about_path.exists():
            about_body = strip_frontmatter(about_path.read_text(encoding="utf-8", errors="ignore"))

        # Build PORTFOLIO_DATA payload
        payload = json.dumps(
            {"projects": projects, "about": about_body},
            ensure_ascii=False,
        )

        # Read SPA template
        template_path = Path(__file__).parent / "portfolio_template.html"
        html = template_path.read_text(encoding="utf-8")

        # Inject data
        data_script = f"<script>window.PORTFOLIO_DATA = {payload};</script>"
        html = html.replace("</head>", data_script + "\n</head>")

        # Demo datasets: any JSON in {source_folder}/demo_data/ becomes
        # window.DEMO_DATA[<stem>] so interactive case-study demos can replay
        # real precomputed model output without baking data into app code.
        html = self._inject_demo_data(html)

        # Chatbot widget — meta tags + script. Portfolio runs as a SPA at "/",
        # so root="/" for asset paths.
        if self.chatbot_enabled and self.chatbot_head:
            chatbot_html = self.chatbot_head.replace("{root}", "/")
            html = html.replace("</head>", chatbot_html + "\n</head>")
            self._copy_widget_asset(site)

        # Fill hero placeholders from config
        name = pcfg.get("name", self.config.get("author", ""))
        title = pcfg.get("title", "")
        tagline = pcfg.get("tagline", "")
        subtitle = pcfg.get("subtitle", "")
        linkedin = pcfg.get("linkedin", "")
        github = pcfg.get("github", "")

        # Generate hero sup from domain names
        domain_names = [d.get("name", "") for d in pcfg.get("domains", [])]
        hero_sup = " &bull; ".join(n.upper() for n in domain_names) if domain_names else "PORTFOLIO"

        # Stats HTML
        stats_html = ""
        for s in pcfg.get("stats", []):
            stats_html += f'<div class="hero-stat"><div class="val"><em>{s["val"]}</em></div><div class="lbl">{s["lbl"]}</div></div>\n'

        # Domain cards HTML
        domains_html = ""
        for d in pcfg.get("domains", []):
            key = d.get("key", "")
            dname = d.get("name", "")
            dtags = d.get("tags", "")
            count = len(
                [
                    p
                    for p in projects
                    if key in (p.get("categoryGroups") or [p.get("categoryGroup", "")])
                ]
            )
            domains_html += (
                f'<a class="hero-domain" data-f="{key}" onclick="goFilter(\'{key}\');return false" href="#projects">'
                f"<h3>{dname}</h3>"
                f'<div class="hero-domain-count" id="hd-{key[:3]}-count">{count} project{"s" if count != 1 else ""}</div>'
                f'<div class="hero-domain-tags">{dtags}</div>'
                f'<div class="arrow">View projects &rarr;</div>'
                f"</a>\n"
            )

        # Skills HTML
        skills_html = ""
        for sg in pcfg.get("skills", []):
            items = "".join(f'<span class="skill-item">{it}</span>' for it in sg.get("items", []))
            skills_html += f'<div class="skill-group"><h4>{sg["name"]}</h4><div class="skill-items">{items}</div></div>\n'

        # Repositories from [[repos]] in _portfolio.toml — rendered as GitHub repo cards.
        repos_section_html = ""
        repos_nav_html = ""
        cards = []
        for r in pcfg.get("repos", []):
            url = r.get("url", "")
            if not url:
                continue
            rname = r.get("name", "") or url.replace("https://github.com/", "").rstrip("/")
            desc = r.get("description", "")
            lang = r.get("language", "")
            tags_list = r.get("tags", []) or []
            if isinstance(tags_list, str):
                tags_list = [t.strip() for t in tags_list.split(",") if t.strip()]
            stars = r.get("stars", "")
            lang_html = f'<span class="rcard-lang">{html_escape(str(lang))}</span>' if lang else ""
            stars_html = (
                f'<span class="rcard-stars">&#9733; {html_escape(str(stars))}</span>'
                if stars
                else ""
            )
            tags_html = "".join(
                f'<span class="rcard-tag">{html_escape(str(t))}</span>' for t in tags_list
            )
            meta_html = (
                f'<div class="rcard-meta">{tags_html}{stars_html}</div>'
                if (tags_html or stars_html)
                else ""
            )
            desc_html = f'<div class="rcard-desc">{html_escape(str(desc))}</div>' if desc else ""
            cards.append(
                f'<a class="rcard" href="{html_escape(str(url), quote=True)}" target="_blank" rel="noopener">'
                f'<div class="rcard-head"><span class="rcard-name">{html_escape(str(rname))}</span>{lang_html}</div>'
                f"{desc_html}{meta_html}"
                f"</a>"
            )
        if cards:
            repos_section_html = (
                '<section class="sec" id="repos">'
                '<div class="sec-label">OPEN SOURCE</div>'
                '<div class="sec-title">Repositories</div>'
                f'<div class="rgrid">{"".join(cards)}</div>'
                "</section>"
            )
            repos_nav_html = '<a href="#repos">Code</a>'

        # About sidebar HTML
        sidebar_html = ""
        for card in pcfg.get("about_sidebar", []):
            items_html = "".join(
                f'<div class="about-card-item"><span class="dot"></span>{it}</div>'
                for it in card.get("items", [])
            )
            sidebar_html += f'<div class="about-card"><h4>{card["title"]}</h4>{items_html}</div>\n'
        # Add connect card
        connect_items = ""
        if linkedin:
            connect_items += f'<div class="about-card-item"><a href="{linkedin}" target="_blank" style="color:var(--p-blue);text-decoration:none">LinkedIn &rarr;</a></div>'
        if github:
            connect_items += f'<div class="about-card-item"><a href="{github}" target="_blank" style="color:var(--p-blue);text-decoration:none">GitHub &rarr;</a></div>'
        if connect_items:
            sidebar_html += f'<div class="about-card"><h4>CONNECT</h4>{connect_items}</div>\n'

        # Replace all placeholders
        replacements = {
            "{PORTFOLIO_NAME}": name,
            "{PORTFOLIO_TITLE}": title,
            "{PORTFOLIO_LINKEDIN}": linkedin,
            "{PORTFOLIO_HERO_SUP}": hero_sup,
            "{PORTFOLIO_TAGLINE}": tagline,
            "{PORTFOLIO_SUBTITLE}": subtitle,
            "{PORTFOLIO_STATS}": stats_html,
            "{PORTFOLIO_DOMAINS}": domains_html,
            "{PORTFOLIO_SKILLS}": skills_html,
            "{PORTFOLIO_SIDEBAR}": sidebar_html,
            "{PORTFOLIO_REPOS_SECTION}": repos_section_html,
            "{PORTFOLIO_REPOS_NAV}": repos_nav_html,
            "{PORTFOLIO_CROSS_SITE}": self.cross_site_html,
        }
        for key, val in replacements.items():
            html = html.replace(key, val)

        # Inline CSS/JS for self-contained output
        static_dir = Path(__file__).parents[2] / "emptyos" / "web" / "static"
        css_path = static_dir / "eos-components.css"
        if css_path.exists():
            css = css_path.read_text(encoding="utf-8")
            html = re.sub(
                r"<link[^>]*eos-components\.css[^>]*/?>", lambda _: f"<style>{css}</style>", html
            )
        js_path = static_dir / "eos-components.js"
        if js_path.exists():
            js = js_path.read_text(encoding="utf-8")
            js = js.replace("</script>", "<\\/script>")
            html = re.sub(
                r"<script[^>]*eos-components\.js[^>]*>\s*</script>",
                lambda _: f"<script>{js}</script>",
                html,
            )
        html = re.sub(r"<link[^>]*theme\.css[^>]*/?>", "", html)

        # Portfolio has its own two-tone palette (dark default, .light override).
        # Bake the site's theme choice into <html class=...> so a light publish
        # theme actually renders light in the built page. `soft-light` is the
        # only light entry in THEME_VARS (apps/publish/templates.py).
        if self.config.get("theme", "void-dark") == "soft-light":
            html = html.replace('<html lang="en">', '<html lang="en" class="light">', 1)

        # Write output
        (site / "index.html").write_text(html, encoding="utf-8")

        # Copy favicon
        favicon = self.source_dir / "favicon.svg"
        if favicon.exists():
            shutil.copy2(str(favicon), str(site / "favicon.svg"))

        # Copy media/ so embedded <video>/<audio> tags in posts resolve
        media_copied = self._copy_media(site)

        # CNAME for custom domain + .nojekyll so GitHub Pages serves as-is
        domain = self.config.get("domain", "")
        if domain:
            (site / "CNAME").write_text(domain, encoding="utf-8")
        (site / ".nojekyll").write_text("", encoding="utf-8")

        # Corpus for downstream consumers — portfolio sites emit one chunk per
        # project body (already in memory) plus the About section. Each chunk
        # links back to the SPA's hash route so the chatbot can deep-link.
        portfolio_chunks: list[dict] = []
        for proj in projects:
            for sec in _chunk_body(proj.get("body", "") or ""):
                portfolio_chunks.append(
                    {
                        "id": self._chunk_id(f"project:{proj['slug']}", sec),
                        "type": "project",
                        "slug": proj["slug"],
                        "title": proj["title"],
                        "section": sec["section"],
                        "tags": proj.get("tags", []),
                        "url": f"/#project-{proj['slug']}",
                        "text": sec["text"],
                    }
                )
        if about_body:
            for sec in _chunk_body(about_body):
                portfolio_chunks.append(
                    {
                        "id": self._chunk_id("about", sec),
                        "type": "about",
                        "slug": "about",
                        "title": "About",
                        "section": sec["section"],
                        "tags": [],
                        "url": "/#about",
                        "text": sec["text"],
                    }
                )

        corpus = {
            **self._site_meta(),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "chunks": portfolio_chunks,
            "faqs": _load_faqs(self.source_dir),
        }
        (site / "corpus.json").write_text(
            json.dumps(corpus, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

        return {
            "pages": 1,
            "posts": len(projects),
            "tags": 0,
            "images": 0,
            "media": media_copied,
            "output": str(site),
        }
