"""markdown_blocks — address a rendered HTML element back to its markdown block.

The markdown-sourced sibling of ``emptyos.sdk.html_anchors``. Designer/viz
element-edit splice HTML *in place* because their artifact on disk IS html. A
publish article's artifact on disk is **markdown**; the preview HTML is a lossy
one-way render (``markdown_render.render_markdown`` rewrites wikilinks/callouts/
image-embeds), so a clicked HTML anchor must resolve back to a **markdown source
block**, not an HTML span.

Two pure pieces:

  1. ``split_markdown_blocks(body)`` — a deterministic, char-offset-based block
     splitter over a note body (**frontmatter already stripped**). Editable
     blocks get a sequential ordinal ``idx`` (this is the ``eN``); non-editable
     blocks carry ``idx == -1``.
  2. ``stamp_markdown_blocks(body, html)`` — stamp ``data-eos-el="eN"`` on the
     **top-level** rendered elements, zipping them onto the ordered editable
     blocks by position and verifying each pair by a text fingerprint. Any
     count/kind/fingerprint mismatch → ``aligned_ok=False`` and the caller
     disables element-edit for that note (whole-file path only). A wrong pick
     can therefore never silently corrupt a file — the diff/staleness gate is
     the backstop, this alignment check is the pre-filter.

Pure functions only — no ``self``, no kernel access, no I/O, no ``markdown``
dependency (``stamp_markdown_blocks`` takes the already-rendered HTML). All
offsets are char offsets into the decoded ``str`` — never bytes — so the
bilingual (CJK) vault is handled correctly. Unit-tested without a daemon
(tests/test_unit_markdown_blocks.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from emptyos.sdk.html_anchors import VOID, line_starts

# Rendered elements that map cleanly to exactly ONE markdown block. Everything
# else (code fences, tables, hr, TOC, footnote/ref definitions, raw-HTML blocks)
# is excluded — no anchor — so it can never be mis-edited. This is the markdown
# analogue of viz's per-shape allow-set / the canvas exclusion; err toward
# exclude. The excluded set can shrink later once the splitter has test coverage.
EDITABLE_BLOCK_KINDS: frozenset[str] = frozenset(
    {"paragraph", "heading", "blockquote", "callout", "list", "image"}
)


@dataclass
class Block:
    """One markdown block. ``idx`` is the editable-ordinal (0,1,2,… over editable
    blocks in document order — the ``eN``) or ``-1`` for a non-editable block.
    ``start``/``end`` are char offsets into the note body (frontmatter stripped);
    ``end`` is the end of the last non-blank line, exclusive."""

    idx: int
    kind: str
    start: int
    end: int
    text: str
    fingerprint: str


# --- Line classifiers ------------------------------------------------------

_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
_HR = re.compile(r"^\s{0,3}([-*_])[ \t]*(\1[ \t]*){2,}$")
_BLOCKQUOTE = re.compile(r"^\s{0,3}>")
_CALLOUT = re.compile(r"^\s{0,3}>\s*\[!")
_LIST = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])(\s)")
_HTMLBLOCK = re.compile(r"^\s{0,3}<(/?[a-zA-Z][\w-]*|!--)")
_REFDEF = re.compile(r"^\s{0,3}\[[^\^\]][^\]]*\]:\s")
_FOOTNOTEDEF = re.compile(r"^\s{0,3}\[\^[^\]]+\]:\s")
_IMAGE_ONLY = re.compile(r"^\s*(?:!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\))\s*$")
_TABLE_DELIM = re.compile(r"^\s*\|?[\s:]*-{1,}[\s:|-]*\|?\s*$")
_INDENTED = re.compile(r"^\s+\S")


def _is_block_starter(line: str) -> bool:
    """True if ``line`` begins a new block (ends a paragraph run)."""
    return bool(
        _HEADING.match(line)
        or _FENCE.match(line)
        or _HR.match(line)
        or _BLOCKQUOTE.match(line)
        or _LIST.match(line)
        or _HTMLBLOCK.match(line)
    )


def split_markdown_blocks(body: str) -> list[Block]:
    """Split a frontmatter-stripped note body into ordered blocks.

    Deterministic and char-offset based. Fence tracking is load-bearing (blank
    lines inside a code fence don't split it). Editable blocks receive a
    sequential ``idx``; non-editable blocks get ``idx == -1``.
    """
    # Per-line offset table: content span excludes the trailing newline.
    rows: list[tuple[int, int, str]] = []  # (start, content_end, text)
    off = 0
    for ln in body.splitlines(keepends=True):
        stripped = ln.rstrip("\n").rstrip("\r")
        rows.append((off, off + len(stripped), stripped))
        off += len(ln)

    n = len(rows)
    blocks: list[Block] = []
    edit_idx = 0

    def emit(kind: str, a: int, b: int) -> None:
        nonlocal edit_idx
        start = rows[a][0]
        end = rows[b][1]
        text = body[start:end]
        editable = kind in EDITABLE_BLOCK_KINDS
        idx = -1
        if editable:
            idx = edit_idx
            edit_idx += 1
        fp = _plain_md(text) if editable else ""
        blocks.append(Block(idx=idx, kind=kind, start=start, end=end, text=text, fingerprint=fp))

    i = 0
    while i < n:
        line = rows[i][2]
        if line.strip() == "":
            i += 1
            continue

        m = _FENCE.match(line)
        if m:
            marker = m.group(1)[0]  # ` or ~
            close = re.compile(rf"^\s{{0,3}}{re.escape(marker)}{{3,}}\s*$")
            a = i
            i += 1
            while i < n:
                if close.match(rows[i][2]):
                    i += 1  # include the closing fence line
                    break
                i += 1
            emit("code", a, i - 1)
            continue

        if _HEADING.match(line):
            emit("heading", i, i)
            i += 1
            continue

        if _HR.match(line):
            emit("hr", i, i)
            i += 1
            continue

        if _FOOTNOTEDEF.match(line):
            emit("footnote_def", i, i)
            i += 1
            continue

        if _BLOCKQUOTE.match(line):
            kind = "callout" if _CALLOUT.match(line) else "blockquote"
            a = i
            i += 1
            while i < n and _BLOCKQUOTE.match(rows[i][2]):
                i += 1
            emit(kind, a, i - 1)
            continue

        if _REFDEF.match(line):
            emit("ref", i, i)
            i += 1
            continue

        if _HTMLBLOCK.match(line):
            a = i
            i += 1
            while i < n and rows[i][2].strip() != "":
                i += 1
            emit("html", a, i - 1)
            continue

        if _LIST.match(line):
            a = i
            i += 1
            while i < n:
                t = rows[i][2]
                if t.strip() == "":
                    j = i + 1
                    while j < n and rows[j][2].strip() == "":
                        j += 1
                    if j < n and (_LIST.match(rows[j][2]) or _INDENTED.match(rows[j][2])):
                        i = j
                        continue
                    break
                if _LIST.match(t) or _INDENTED.match(t):
                    i += 1
                    continue
                break
            emit("list", a, i - 1)
            continue

        # Table: a pipe row immediately followed by a delimiter row.
        if "|" in line and i + 1 < n and "-" in rows[i + 1][2] and _TABLE_DELIM.match(rows[i + 1][2]):
            a = i
            i += 2
            while i < n and rows[i][2].strip() != "" and "|" in rows[i][2]:
                i += 1
            emit("table", a, i - 1)
            continue

        # Paragraph (default). May be a single-line image embed.
        a = i
        i += 1
        while i < n:
            t = rows[i][2]
            if t.strip() == "" or _is_block_starter(t):
                break
            i += 1
        single = (i - 1) == a
        kind = "image" if (single and _IMAGE_ONLY.match(rows[a][2])) else "paragraph"
        emit(kind, a, i - 1)

    return blocks


# --- Fingerprints ----------------------------------------------------------

_ALNUM = re.compile(r"[^a-z0-9]+")
_TAG = re.compile(r"<[^>]*>")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_IMAGE_EMBED = re.compile(r"!\[\[[^\]]*\]\]")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _norm(s: str) -> str:
    """Lowercase, keep only ``[a-z0-9]``. Drops all markdown/HTML punctuation,
    heading permalink glyphs (¶), and whitespace so the two sides compare on
    plain text alone."""
    return _ALNUM.sub("", (s or "").lower())


def _plain_md(text: str) -> str:
    """Reduce a markdown block to normalized plain text for fingerprinting,
    mirroring what ``render_markdown`` does to the visible text: image embeds
    drop out, wikilinks/links collapse to their display text."""
    t = _MD_IMAGE_EMBED.sub(" ", text)
    t = _MD_IMAGE.sub(" ", t)
    t = _WIKILINK.sub(lambda m: m.group(2) or m.group(1), t)
    t = _MD_LINK.sub(lambda m: m.group(1), t)
    return _norm(t)


def _plain_html(fragment_text: str) -> str:
    return _norm(_TAG.sub(" ", fragment_text))


def _fp_match(a: str, b: str) -> bool:
    """Tolerant text-fingerprint match. Both empty (image/void blocks) → match.
    Otherwise one being a prefix of the other, or a shared 16-char prefix,
    counts — so trailing noise (permalink glyphs, a link URL the markdown side
    keeps but the rendered text drops, footnote-ref digits) doesn't desync."""
    if not a and not b:
        return True
    if not a or not b:
        return False
    if a.startswith(b) or b.startswith(a):
        return True
    return a[:16] == b[:16]


# --- Top-level HTML collection + stamping ----------------------------------


class _TopLevelCollector(HTMLParser):
    """Collect one record per depth-0 (top-level) element in document order:
    ``{tag, start, attrs, text}``. python-markdown output is well-formed, so a
    plain tag stack (with void-element handling) tracks depth reliably."""

    def __init__(self, ls: list[int]):
        super().__init__(convert_charrefs=False)
        self._ls = ls
        self._stack: list[str] = []
        self._cur: dict | None = None
        self.tops: list[dict] = []

    def _off(self) -> int:
        line, col = self.getpos()
        return self._ls[line - 1] + col

    def _finalize(self) -> None:
        if self._cur is not None:
            self._cur["text"] = "".join(self._cur.pop("_texts"))
            self.tops.append(self._cur)
            self._cur = None

    def handle_starttag(self, tag, attrs):
        if not self._stack and self._cur is None:
            rec = {"tag": tag, "start": self._off(), "attrs": dict(attrs), "_texts": []}
            if tag in VOID:
                rec["text"] = ""
                self.tops.append(rec)
                return
            self._cur = rec
            self._stack.append(tag)
            return
        if tag not in VOID:
            self._stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if not self._stack and self._cur is None:
            self.tops.append({"tag": tag, "start": self._off(), "attrs": dict(attrs), "text": ""})

    def handle_endtag(self, tag):
        for k in range(len(self._stack) - 1, -1, -1):
            if self._stack[k] == tag:
                del self._stack[k:]
                break
        if not self._stack:
            self._finalize()

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["_texts"].append(data)


_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _html_editable(rec: dict) -> bool:
    """True if a top-level rendered element corresponds to an editable markdown
    block (must mirror ``EDITABLE_BLOCK_KINDS`` on the render side)."""
    tag = rec["tag"]
    if tag in _HEADING_TAGS or tag in ("p", "ul", "ol", "blockquote"):
        return True
    if tag == "div" and "callout" in (rec.get("attrs", {}).get("class") or ""):
        return True
    return False


def stamp_markdown_blocks(body: str, html: str) -> tuple[str, dict, bool]:
    """Stamp ``data-eos-el="eN"`` (+ ``data-eos-md="start:end"``) on the
    top-level rendered elements that map to editable markdown blocks.

    Returns ``(anchored_html, index, aligned_ok)`` where ``index`` maps
    ``"eN" -> {"start","end","kind"}``. On any count or fingerprint mismatch
    between the editable markdown blocks and the editable top-level elements,
    returns ``(html, {}, False)`` unchanged — the caller then disables
    element-edit for the note.
    """
    editable_blocks = [b for b in split_markdown_blocks(body) if b.idx >= 0]

    c = _TopLevelCollector(line_starts(html))
    try:
        c.feed(html)
        c.close()
    except Exception:
        return html, {}, False
    editable_tops = [t for t in c.tops if _html_editable(t)]

    if len(editable_blocks) != len(editable_tops):
        return html, {}, False
    for b, t in zip(editable_blocks, editable_tops):
        if not _fp_match(b.fingerprint, _plain_html(t["text"])):
            return html, {}, False

    index: dict = {}
    edits: list[tuple[int, str]] = []
    for b, t in zip(editable_blocks, editable_tops):
        eid = f"e{b.idx}"
        index[eid] = {"start": b.start, "end": b.end, "kind": b.kind}
        insert_at = t["start"] + 1 + len(t["tag"])
        edits.append((insert_at, f' data-eos-el="{eid}" data-eos-md="{b.start}:{b.end}"'))

    for insert_at, txt in sorted(edits, reverse=True):
        html = html[:insert_at] + txt + html[insert_at:]
    return html, index, True
