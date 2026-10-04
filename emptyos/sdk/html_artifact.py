"""Shared helpers for single-file HTML artifact builders (viz, designer).

Both apps drive an LLM to emit one standalone HTML document, then strip/validate
it before saving. The extraction + validation logic is identical, so it lives
here (CLAUDE.md rule 9 — second consumer triggers the SDK extraction).

Pure functions only — no `self`, no kernel access, no I/O. Consumers alias the
public names to their existing private ones, e.g.:

    from emptyos.sdk.html_artifact import (
        extract_html as _extract_html,
        looks_truncated as _looks_truncated,
        ...
    )
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

# A whole-string ```html ... ``` fence.
FENCE_RE = re.compile(r"^```(?:html)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
# A fenced html block ANYWHERE (model wrote a prose preamble, then the fence).
FENCE_ANYWHERE_RE = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL)


def strip_fences(text: str) -> str:
    """If the LLM wrapped output in a whole-string ```html ... ``` fence, strip it."""
    text = text.strip()
    m = FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def looks_like_html(text: str) -> bool:
    """True if the head looks like an HTML document start (loose: substring scan)."""
    head = text.lstrip()[:200].lower()
    return "<!doctype" in head or "<html" in head


def extract_html(text: str) -> str:
    """Robustly pull the HTML document out of an LLM reply.

    Larger few-shots make gpt-class models wrap the page in a prose preamble
    and/or a ```html fence rather than emit the bare document. Always trim to the
    actual document span so neither leading nor trailing prose survives:
      1. strip a whole-string ```html fence,
      2. prefer a fenced html block anywhere (prose-then-fence),
      3. trim to the first <!doctype/<html through the last </html>.
    A reply with no recoverable document (refusal / truncation) falls through
    unchanged so the caller's validation flags it. Don't short-circuit on
    `looks_like_html` — it matches `<!doctype` *anywhere* in the first 200 chars,
    so a short preamble would otherwise leave the prose + fence markers in.
    """
    t = strip_fences(text)
    m = FENCE_ANYWHERE_RE.search(t)
    if m and ("<!doctype" in m.group(1).lower() or "<html" in m.group(1).lower()):
        t = m.group(1).strip()
    low = t.lower()
    i = low.find("<!doctype")
    if i < 0:
        i = low.find("<html")
    if i >= 0:
        j = low.rfind("</html>")
        t = t[i:(j + 7) if j >= 0 else len(t)]
    return t.strip()


def looks_truncated(text: str) -> tuple[bool, str]:
    """Detect mid-stream truncation. Output token caps bite hardest on full-file
    rewrites; refusing to save a truncated artifact beats silently corrupting it."""
    tail = text.rstrip()[-500:].lower()
    if "</html>" not in tail:
        return True, "missing closing </html> tag — output likely truncated"
    if "</body>" not in tail:
        return True, "missing closing </body> tag — output likely truncated"
    last_char = text.rstrip()[-1:] if text.rstrip() else ""
    if last_char != ">":
        return True, f"output ends with {last_char!r}, not a closing tag — likely truncated"
    return False, ""


def salvage_truncated_html(partial: str, *, banner_note: str = "") -> str:
    """Repair a truncated HTML artifact into a valid, self-contained document.

    The output-token ceiling can cut a dense artifact mid-stream. Detection
    (``looks_truncated``) correctly refuses to *save* it as-is, but discarding
    the whole thing loses everything the model did produce. This salvages the
    partial: drops a dangling half-written tag, injects a visible truncation
    banner at the top of the body, and appends the missing structural closers —
    so the user sees what completed instead of nothing (no silent cap).

    Pure. Returns "" for empty input. The banner is inline-styled + emoji-only so
    the artifact stays self-contained (no external assets, CSP-safe).
    """
    s = (partial or "").rstrip()
    if not s:
        return ""
    # Drop a dangling half-written tag (e.g. `<div class="foo`) so the appended
    # banner/closers can't be swallowed into an unterminated element — but keep
    # trailing plain text (a cut mid-sentence leaves readable content). A '<'
    # with no '>' after it is the unterminated tag; trim from there.
    last_lt, last_gt = s.rfind("<"), s.rfind(">")
    if last_lt > last_gt:
        s = s[:last_lt].rstrip()
    kb = max(1, round(len(partial.encode("utf-8")) / 1024))
    note = banner_note or (
        f"This artifact was truncated at the model's output limit "
        f"(~{kb} KB generated) and is incomplete — regenerate with a shorter or "
        f"simpler brief, or split it into parts."
    )
    banner = (
        '<div style="position:sticky;top:0;z-index:99999;background:#7a1f1f;'
        'color:#fff;font:600 13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;'
        'padding:10px 16px;border-bottom:2px solid #ff5c5c">'
        "⚠️ " + html_escape(note) + "</div>"
    )
    low = s.lower()
    bi = low.find("<body")
    if bi >= 0:
        gt = s.find(">", bi)
        s = (s[:gt + 1] + banner + s[gt + 1:]) if gt >= 0 else (s + banner)
    else:
        s = banner + s
    tail = s.lower()[-600:]
    if "</body>" not in tail:
        s += "</body>"
    if "</html>" not in s.lower()[-600:]:
        s += "</html>"
    return s


def html_escape(text: str) -> str:
    """Minimal HTML-text escape for the salvage banner (no external dep)."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def rewrite_user_msg(prior_prompt: str, change: str, prior_html: str, *, preserve_hint: str = "") -> str:
    """Whole-file-rewrite user message for the iterate path.

    `preserve_hint` appends an app-specific clause inside the parenthetical (e.g.
    designer asks to preserve baked `<iframe srcdoc>` embeds; viz passes nothing).
    """
    hint = f"; {preserve_hint}" if preserve_hint else ""
    return (
        f"PRIOR BRIEF:\n{prior_prompt}\n\n"
        f"CHANGE REQUEST:\n{change}\n\n"
        f"CURRENT FILE (rewrite the whole thing applying the change{hint}):\n{prior_html}"
    )


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_artifact_id() -> str:
    # 8 hex chars — collision-resistant at one user's artifact-output scale.
    return secrets.token_hex(4)


# Sentence-ish end of a brief's first clause. Deliberately not a full sentence
# splitter — a brief's first period is a reliable-enough cut, and over-fitting
# here would cost more than a slightly-long title.
#
# The one case worth encoding: a `.` between two digits is a decimal point, not
# a sentence end. Engineering briefs are dense with them ("11/0.415 kV",
# "R2.65 m", "0.6/1 kV"), and cutting there yields a title that looks truncated
# mid-number — "utility incoming → 11/0" — which reads as a bug in the record
# rather than a naming choice.
_TITLE_CUT_RE = re.compile(r"(?<!\d)\.(?!\d)|[.](?=\s)|[!?\n]")
# Stray quote left over from a nested-quoted YAML scalar: frontmatter parsing
# strips one layer, so a brief authored as '…' inside "…" keeps an inner quote.
_TITLE_EDGE_QUOTES = "\"'`“”‘’"

_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_SVG_TOKEN_RE = re.compile(r"<svg\b|</svg\s*>", re.IGNORECASE)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.IGNORECASE | re.DOTALL)
_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"


def _outermost_svg_span(html: str) -> tuple[int, int] | None:
    """(start, end) of the outermost <svg>…</svg>, depth-aware. None if absent.

    Depth-aware because an SVG may nest another <svg> (symbols, foreignObject
    fallbacks); a non-greedy regex would stop at the first inner `</svg>` and
    silently return a truncated, unclosed document.
    """
    first = _SVG_OPEN_RE.search(html)
    if not first:
        return None
    depth = 0
    for m in _SVG_TOKEN_RE.finditer(html, first.start()):
        depth += 1 if m.group(0).lower().startswith("<svg") else -1
        if depth == 0:
            return first.start(), m.end()
    return None  # unbalanced — refuse rather than emit a broken file


def extract_svg(html: str, *, background: str | None = "var(--bg, #ffffff)") -> str | None:
    """Lift a standalone, self-contained `.svg` document out of an artifact page.

    Returns None when the page has no SVG, or when its tags are unbalanced.

    Three things make this more than a substring grab. Each one, left undone,
    produces a file that is *valid* and *wrong* — the failure is visual, so it
    passes every structural check:

    1. **`xmlns`.** An inline SVG in HTML needs no namespace (the HTML parser
       implies it); a standalone `.svg` file that omits it does not render at
       all. Generated artifacts never carry it.
    2. **Document CSS.** Artifacts style SVG children from the page's `<style>`
       block (`.cable{stroke:…}`), not with presentation attributes. Extracted
       bare, every shape falls back to black-on-nothing. The page's styles are
       inlined into the SVG root, where `:root` resolves to the `<svg>` element,
       so custom properties and `prefers-color-scheme` blocks keep working.
    3. **Background.** The page paints its canvas on `html,body`, which does not
       come along. Without an explicit rect the figure is transparent, so a dark
       palette lands invisibly on a dark note. Pass `background=None` to opt out
       when the artwork already paints its own full-bleed canvas.
    """
    if not html:
        return None
    span = _outermost_svg_span(html)
    if not span:
        return None
    start, end = span
    svg = html[start:end]

    open_m = _SVG_OPEN_RE.search(svg)
    if not open_m:
        return None
    open_tag, inner = open_m.group(0), svg[open_m.end():-len("</svg>")]

    if "xmlns=" not in open_tag:
        open_tag = f'{open_tag[:-1]} xmlns="{_SVG_NS}">'
    if ("xlink:" in svg or "xlink:" in open_tag) and "xmlns:xlink=" not in open_tag:
        open_tag = f'{open_tag[:-1]} xmlns:xlink="{_XLINK_NS}">'

    # Styles from the page OUTSIDE the svg (an svg-internal <style> is already
    # in `inner` and must not be duplicated).
    outside = html[:start] + html[end:]
    css = "\n".join(b.strip() for b in _STYLE_BLOCK_RE.findall(outside) if b.strip())

    parts = [open_tag]
    if css:
        parts.append(f"<style>\n{css}\n</style>")
    if background:
        parts.append(f'<rect width="100%" height="100%" fill="{background}"/>')
    parts.append(inner)
    parts.append("</svg>")
    return "\n".join(parts)


def artifact_title(prompt: str, *, fallback: str = "", limit: int = 72) -> str:
    """A short human label for a generated artifact, derived from its brief.

    Deterministic and free — no model call. It runs on every generation, so an
    LLM round-trip here would tax the hot path to name something the user can
    already read off their own prompt.

    Takes the brief's first clause, collapses whitespace, and truncates on a
    word boundary. Returns ``fallback`` when the prompt yields nothing usable,
    so a caller can pass e.g. ``f"Viz artifact {rid}"`` and never get "".
    """
    raw = (prompt or "").strip().lstrip(_TITLE_EDGE_QUOTES).strip()
    if not raw:
        return fallback
    # Cut BEFORE collapsing whitespace — a newline is a real boundary. Briefs
    # routinely put the subject on line 1 ("Create a slide deck titled: X\n
    # Brief: …"); collapsing first would make the `\n` cut point dead and the
    # title a run-on truncated mid-sentence.
    head = " ".join((_TITLE_CUT_RE.split(raw, 1)[0] or "").split())
    if not head:
        head = " ".join(raw.split())
    if len(head) > limit:
        # Cut at the last space inside the budget so we don't split a word;
        # a single long token with no space falls back to a hard slice.
        clipped = head[:limit]
        space = clipped.rfind(" ")
        head = (clipped[:space] if space > limit // 2 else clipped).rstrip(" ,;:-") + "…"
    # Trailing punctuation left by the cut. `…` is not in the set — it is the
    # truncation marker and must survive.
    head = head.strip().rstrip(_TITLE_EDGE_QUOTES).rstrip(" ,;:-.").strip()
    return head or fallback
