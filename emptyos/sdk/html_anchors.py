"""html_anchors — positional HTML element addressing over the stdlib parser.

A small, pure, dependency-free toolkit for two needs that both reduce to "map a
rendered DOM element back to an exact span of its source HTML":

  1. The designer app's element-anchored edit loop — stamp generated pages with
     ``data-eos-el="eN"`` anchors, then resolve a clicked anchor to the exact
     char span of that element so a single element can be rewritten and spliced
     back (small, reviewable diff). See apps/public/standard/designer/editing.py.
  2. The platform pixel->source locator — stamp any served app page with
     ``data-eos-src="<rel_path>:<line>"`` so a debug overlay can report the
     source location of a clicked element. See emptyos/web/server.py.

Pure functions only — no ``self``, no kernel access, no I/O. Unit-tested without
a daemon (tests/test_unit_html_anchors.py).

**No third-party dependency.** Built on stdlib ``html.parser.HTMLParser`` with
``convert_charrefs=False`` so the source text round-trips verbatim (``&amp;``
stays ``&amp;``; splices are byte-exact on the decoded string). All offsets are
char offsets into the decoded ``str`` — never bytes — so non-ASCII content
(CJK, em-dashes) is handled correctly.

Why not a depth counter for ``extract_element``: void elements emit no end tag
and LLM/browser HTML routinely omits ``</p>``/``</li>`` closes, so a flat
``depth += 1 / -= 1`` desyncs on exactly the tags we most want to edit. Instead
we mirror HTMLParser's own implicit tag stack and pop to the nearest same-name
frame on each end tag — the same rule browsers use to auto-close.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags a user may click-to-edit / locate. Excludes raw-text elements
# (script/style/textarea — their `<` is not markup), opaque/nested-document
# elements (iframe), and head-only elements.
EDITABLE_TAGS: frozenset[str] = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "a", "button", "li", "ul", "ol",
    "section", "div", "span", "img",
    "header", "footer", "nav", "main", "article", "aside",
    "blockquote", "figure", "figcaption",
    "table", "thead", "tbody", "tr", "td", "th",
    "label", "form",
})

# HTML5 void elements — no end tag, content model empty.
VOID: frozenset[str] = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def line_starts(doc: str) -> list[int]:
    """Per-line char-offset table: ``starts[line_index]`` = offset of that line.

    HTMLParser's ``getpos()`` returns 1-based line + 0-based column; combined
    with this table it yields an absolute char offset into ``doc``.
    """
    starts = [0]
    off = 0
    for ln in doc.splitlines(keepends=True):
        off += len(ln)
        starts.append(off)
    return starts


class _AnchorSpanFinder(HTMLParser):
    """Find the ``[start, end)`` char span of the element carrying
    ``attr=target``. Mirrors HTMLParser's implicit tag stack so void elements
    and browser-style auto-closed tags don't desync the match."""

    def __init__(self, target: str, attr: str, ls: list[int]):
        super().__init__(convert_charrefs=False)
        self.target = target
        self.attr = attr
        self._ls = ls
        self._stack: list[tuple[str, str | None]] = []  # (tag, anchor)
        self.start: int | None = None
        self.end: int | None = None

    def _off(self) -> int:
        line, col = self.getpos()
        return self._ls[line - 1] + col

    def handle_starttag(self, tag, attrs):
        anchor = dict(attrs).get(self.attr)
        if tag in VOID:
            if anchor == self.target and self.start is None:
                self.start = self._off()
                self.end = self.start + len(self.get_starttag_text() or "")
            return
        if anchor == self.target and self.start is None:
            self.start = self._off()
        self._stack.append((tag, anchor))

    def handle_startendtag(self, tag, attrs):  # <foo/>
        if dict(attrs).get(self.attr) == self.target and self.start is None:
            self.start = self._off()
            self.end = self.start + len(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        # Pop to the nearest same-name frame (auto-close intervening unclosed
        # tags, exactly as a browser would).
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                if self._stack[i][1] == self.target and self.end is None:
                    self.end = self._off() + len(tag) + 3  # len("</%s>" % tag)
                del self._stack[i:]
                return


def extract_element(doc: str, anchor_value: str, attr: str = "data-eos-el") -> tuple[int, int] | None:
    """Return ``(start, end)`` char span of the element whose ``attr`` equals
    ``anchor_value``, or ``None`` if it can't be unambiguously resolved.

    ``None`` is the signal to fall back (e.g. to a whole-file rewrite) rather
    than splice a wrong span. Slice with ``doc[start:end]`` to get the exact
    source outerHTML; splice with ``doc[:start] + replacement + doc[end:]``.
    """
    if not anchor_value:
        return None
    p = _AnchorSpanFinder(anchor_value, attr, line_starts(doc))
    try:
        p.feed(doc)
        p.close()
    except Exception:
        return None
    if p.start is None or p.end is None or p.end <= p.start or p.end > len(doc):
        return None
    return p.start, p.end


class _StartTagCollector(HTMLParser):
    """Collect ``(offset, tag, has_attr)`` for every start tag (and self-closing
    tag), in document order. Used by inject_anchors / inject_src to splice an
    attribute in right after the tag name."""

    def __init__(self, attr: str, tags: frozenset[str], ls: list[int]):
        super().__init__(convert_charrefs=False)
        self.attr = attr
        self.tags = tags
        self._ls = ls
        # (char_offset, tag, line, has_attr)
        self.hits: list[tuple[int, str, int, bool]] = []

    def _off(self) -> int:
        line, col = self.getpos()
        return self._ls[line - 1] + col

    def _record(self, tag, attrs):
        if tag not in self.tags:
            return
        has = self.attr in dict(attrs)
        line, _ = self.getpos()
        self.hits.append((self._off(), tag, line, has))

    def handle_starttag(self, tag, attrs):
        self._record(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._record(tag, attrs)


def _splice_attrs(doc: str, value_fn, attr: str, tags: frozenset[str]) -> str:
    """Insert ``attr="<value_fn(seq, tag, line)>"`` after the tag name of each
    matching start tag that lacks ``attr``. Idempotent (skips tags already
    carrying ``attr``). Splices in reverse offset order so earlier offsets stay
    valid."""
    ls = line_starts(doc)
    c = _StartTagCollector(attr, tags, ls)
    try:
        c.feed(doc)
        c.close()
    except Exception:
        return doc

    # Assign sequence numbers in document order (only to tags we will stamp).
    edits: list[tuple[int, str]] = []  # (insert_offset, attr_text)
    seq = 0
    for off, tag, line, has in c.hits:
        if has:
            continue
        value = value_fn(seq, tag, line)
        seq += 1
        # Insert position = just after "<tag" (offset + 1 + len(tag)).
        insert_at = off + 1 + len(tag)
        edits.append((insert_at, f' {attr}="{value}"'))

    for insert_at, text in sorted(edits, reverse=True):
        doc = doc[:insert_at] + text + doc[insert_at:]
    return doc


def inject_anchors(
    doc: str,
    attr: str = "data-eos-el",
    prefix: str = "e",
    tags: frozenset[str] = EDITABLE_TAGS,
) -> str:
    """Stamp ``attr="<prefix><N>"`` (sequential, document order) on each
    ``tags`` start tag lacking it. Idempotent — safe to re-run after an edit."""
    return _splice_attrs(doc, lambda seq, tag, line: f"{prefix}{seq}", attr, tags)


def inject_src(
    doc: str,
    rel_path: str,
    attr: str = "data-eos-src",
    tags: frozenset[str] = EDITABLE_TAGS,
) -> str:
    """Stamp ``attr="<rel_path>:<line>"`` on each ``tags`` start tag lacking it,
    where ``line`` is the 1-based source line of the tag. Read-only provenance
    for the platform pixel->source locator."""
    safe = rel_path.replace('"', "")
    return _splice_attrs(doc, lambda seq, tag, line: f"{safe}:{line}", attr, tags)


class _AnchorListCollector(HTMLParser):
    """Collect ``(anchor_value, tag)`` for every start tag carrying ``attr``,
    in document order. Read-side companion to _StartTagCollector — used by
    ``list_anchors`` to enumerate the stamped-anchor menu."""

    def __init__(self, attr: str):
        super().__init__(convert_charrefs=False)
        self.attr = attr
        self.hits: list[tuple[str, str]] = []

    def _record(self, tag, attrs):
        v = dict(attrs).get(self.attr)
        if v:
            self.hits.append((v, tag))

    def handle_starttag(self, tag, attrs):
        self._record(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._record(tag, attrs)


_TEXT_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")


def list_anchors(doc: str, attr: str = "data-eos-el", max_text: int = 80) -> list[dict]:
    """Enumerate every element carrying ``attr``, in document order.

    Returns ``[{"el": <value>, "tag": <name>, "text": <snippet>}]`` — the
    "anchor menu" a documentation/annotation pass picks from (the read-side
    companion to ``inject_anchors``, which stamps the anchors). ``text`` is a
    short whitespace-collapsed text-content snippet so a human/LLM can identify
    the element without seeing markup. Duplicate anchor values keep the first.
    Returns ``[]`` on a parse error or a page with no stamped anchors.
    """
    c = _AnchorListCollector(attr)
    try:
        c.feed(doc)
        c.close()
    except Exception:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for value, tag in c.hits:
        if value in seen:
            continue
        seen.add(value)
        text = ""
        span = extract_element(doc, value, attr)
        if span:
            inner = doc[span[0]:span[1]]
            text = _WS_RE.sub(" ", _TEXT_TAG_RE.sub(" ", inner)).strip()[:max_text]
        out.append({"el": value, "tag": tag, "text": text})
    return out


# Read-side companion to inject_src. A JS snippet to prepend inside a
# ``page.evaluate(() => { ... })`` body in an audit harness: it defines
# ``srcOf(el)`` returning the nearest ``data-eos-src`` stamp
# ("apps/foo/pages/index.html:142") or "". Keeping the attribute name in ONE
# place means the audit readers can't drift from the stamper above. Consumers:
# scripts/check-clickable.py, scripts/ui_walk_round2.py, tests/test_sys_mobile.py.
SRC_OF_JS = (
    "var srcOf = (el) => { var a = el && el.closest && el.closest('[data-eos-src]');"
    " return a ? a.getAttribute('data-eos-src') : ''; };\n"
)


def strip_attr(doc: str, attr: str = "data-eos-el") -> str:
    """Remove every ``attr="..."`` occurrence (for clean standalone exports)."""
    return re.sub(r'\s+' + re.escape(attr) + r'="[^"]*"', "", doc)


# Match the opening tag of a fragment: "<tag ...>" — group 1 is the inside
# (after the tag name, before the closing ">"), preserving self-closing slash.
_OPEN_TAG_RE = re.compile(r"^(\s*<[a-zA-Z][\w-]*)([^>]*?)(/?)>", re.DOTALL)
_STYLE_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)


def merge_inline_style(outer_html: str, prop: str, value: str) -> str:
    """Merge a single CSS property into the inline ``style`` of the fragment's
    outermost element. Replaces the property if present, appends otherwise.
    Used by deterministic quick-knob edits (no LLM)."""
    m = _OPEN_TAG_RE.match(outer_html)
    if not m:
        return outer_html
    head, attrs, slash = m.group(1), m.group(2), m.group(3)
    prop = prop.strip()
    value = value.strip()
    decl = f"{prop}: {value}"

    sm = _STYLE_RE.search(attrs)
    if sm:
        existing = sm.group(1)
        # Drop any existing declaration of the same property, then append.
        kept = [
            d.strip()
            for d in existing.split(";")
            if d.strip() and d.split(":", 1)[0].strip().lower() != prop.lower()
        ]
        kept.append(decl)
        new_style = "; ".join(kept)
        new_attrs = attrs[: sm.start()] + f'style="{new_style}"' + attrs[sm.end():]
    else:
        sep = "" if (not attrs or attrs.endswith(" ")) else " "
        new_attrs = f'{attrs}{sep}style="{decl}"'

    return head + new_attrs + slash + ">" + outer_html[m.end():]


def outer_tag_name(outer_html: str) -> str:
    """Return the outermost tag name of an HTML fragment, lowercased, or ""."""
    m = re.match(r"\s*<([a-zA-Z][\w-]*)", outer_html)
    return m.group(1).lower() if m else ""
