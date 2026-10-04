"""Assistant — passage-level citation anchors (`path#Section`) + the passage reader.

Extracted as its own module (never lived in app.py) to keep the core spine
atomic (P4 Atomic, CLAUDE.md rule 4). Owns: turning a retrieved *note* into a
retrieved *passage* — pick the ``## section`` of that note whose body actually
matches the retrieval query, emit a ``path#Section`` anchor for it, and serve
that one section back so a citation chip can click through to the exact text
that produced the claim instead of dumping the reader at the top of a 400-line
note.

Two layers, deliberately separated so the scoring is testable without a daemon:

- **Pure** (``split_sections`` / ``best_section`` / ``excerpt_for`` /
  ``passage_anchor`` / ``parse_passage_anchor`` / ``passage_href``) — no
  ``self``, no I/O. Unit-tested in ``tests/test_unit_gap_assistant.py``.
- **Bound** (``_passage_citations_enabled`` / ``_passage_for`` /
  ``api_passage``) — reach the vault through ``self.vault_read_body`` and
  ``self.vault_read_section``.

``split_sections`` mirrors ``BaseApp.vault_read_section``'s heading grammar
exactly (a section starts at a line whose strip() is ``## <name>`` and ends at
the next line starting with ``## ``; ``### `` subsections stay inside). That
equality is load-bearing: the anchor this module emits is read back through
``vault_read_section``, so a divergent splitter would emit anchors that resolve
to nothing.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   _passage_citations_enabled = _passages._passage_citations_enabled
#   _passage_for               = _passages._passage_for
#   api_passage                = _passages.api_passage        # @web_route
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# A citation excerpt is a *pointer*, not the passage — it exists to let the
# reader recognise the claim before spending a click. The full section arrives
# from /api/passage on demand.
EXCERPT_CHARS = 240
PASSAGE_CHARS = 4000

# Scoring a section against the retrieval query is deliberately lexical, not
# embedding-based: it runs per-citation on the response path, and the candidate
# set is already narrowed to the notes retrieval chose. Cheap and explainable
# beats clever here — a wrong section is one click from the whole note.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by do does did for from has have how i if in into is it
    its me my no not of on or so than that the their them then there these they this
    to was were what when where which who why will with you your about can could
    should would am been being over under again just also very more most some such
    """.split()
)
_WORD_RE = re.compile(r"[a-z0-9_]+")
# CJK has no spaces, so _WORD_RE yields nothing for a Chinese query. Fall back to
# per-character bigrams, which is the standard cheap CJK matching unit. The class
# is built from chr() so this source file stays pure ASCII -- a literal CJK range
# is one encoding hop away from silent corruption.
_CJK_RANGES = ((0x4E00, 0x9FFF), (0x3040, 0x30FF))  # CJK ideographs, kana
_CJK_CLASS = "[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _CJK_RANGES) + "]"
_CJK_RUN_RE = re.compile(_CJK_CLASS + "+")


def query_terms(query: str) -> list[str]:
    """Retrieval query → deduped lowercase match terms, longest first.

    Longest-first matters for ``excerpt_for``: a hit on the most specific term
    is the one worth centring the excerpt on.
    """
    q = (query or "").lower()
    terms: list[str] = [w for w in _WORD_RE.findall(q) if len(w) > 2 and w not in _STOPWORDS]
    # CJK bigrams from every run of CJK characters.
    for run in _CJK_RUN_RE.findall(q):
        if len(run) == 1:
            terms.append(run)
        else:
            terms.extend(run[i : i + 2] for i in range(len(run) - 1))
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    out.sort(key=len, reverse=True)
    return out


def split_sections(body: str) -> list[dict]:
    """Markdown body → ``[{"section": name, "body": text}]``.

    The text before the first ``## `` heading is returned as section ``""``
    (a note may be entirely preamble — that is the common case for a journal
    entry). Heading grammar matches ``BaseApp.vault_read_section``: the
    heading line's ``strip()`` must be ``## <name>``, and a section runs until
    the next line *starting with* ``## ``, so ``### `` subsections stay inside
    their parent. Section bodies are stripped, same as ``vault_read_section``.
    """
    if not body:
        return []
    current = ""
    buf: list[str] = []
    out: list[dict] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text or current:
            out.append({"section": current, "body": text})

    for line in body.split("\n"):
        stripped = line.strip()
        # `## ` prefix on the RAW line is what terminates a section in
        # vault_read_section; the heading NAME comes from the stripped line.
        if line.startswith("## ") and stripped.startswith("## "):
            flush()
            current = stripped[3:].strip()
            buf = []
            continue
        buf.append(line)
    flush()
    return [s for s in out if s["section"] or s["body"]]


def score_section(body: str, terms: list[str]) -> float:
    """How well one section's body answers the query terms.

    Coverage (how many distinct terms appear) dominates frequency (how often),
    because a section mentioning three of the query's terms once each is a far
    better passage than one repeating a single term six times. Length is not
    penalised — sections are already bounded by the note's own structure.
    """
    if not terms or not body:
        return 0.0
    low = body.lower()
    hits = 0
    freq = 0
    for t in terms:
        c = low.count(t)
        if c:
            hits += 1
            freq += min(c, 5)
    if not hits:
        return 0.0
    return hits + (freq / (len(terms) * 5.0))


def best_section(body: str, query: str) -> tuple[str, float]:
    """Pick the ``## section`` of ``body`` that best matches ``query``.

    Returns ``("", 0.0)`` when nothing matches — the caller must treat that as
    "no passage, cite the note" rather than falling back to section 0, which
    would point the reader at an arbitrary passage and look like a claim about
    where the answer came from.
    """
    terms = query_terms(query)
    if not terms:
        return "", 0.0
    best_name = ""
    best = 0.0
    for sec in split_sections(body):
        # The preamble (before the first `##`) is unanchorable -- vault_read_section
        # cannot address it -- so it must never win, or _passage_for would emit an
        # anchor that resolves to nothing.
        if not sec["section"]:
            continue
        s = score_section(sec["body"], terms)
        if s > best:
            best, best_name = s, sec["section"]
    return best_name, best


def excerpt_for(text: str, terms: list[str], max_chars: int = EXCERPT_CHARS) -> str:
    """A short window of ``text`` centred on its densest query-term hit.

    Falls back to the head of the text when no term hits. Ellipses mark a
    window that does not start/end at the text's own boundaries, so the reader
    can tell an excerpt from a whole short section.
    """
    t = (text or "").strip()
    if not t:
        return ""
    flat = " ".join(t.split())
    if len(flat) <= max_chars:
        return flat
    low = flat.lower()
    at = -1
    for term in terms or []:
        at = low.find(term)
        if at >= 0:
            break
    if at < 0:
        return flat[:max_chars].rstrip() + "…"
    start = max(0, at - max_chars // 3)
    # Prefer a word boundary so the window does not open mid-token.
    if start > 0:
        sp = flat.find(" ", start)
        if 0 <= sp < start + 30:
            start = sp + 1
    end = min(len(flat), start + max_chars)
    return ("…" if start > 0 else "") + flat[start:end].rstrip() + ("…" if end < len(flat) else "")


def passage_anchor(path: str, section: str = "") -> str:
    """``("notes/x.md", "Timeline")`` → ``"notes/x.md#Timeline"``.

    A note with no matched section anchors to the bare path — an anchor is a
    claim about where a passage is, so an empty section must not produce a
    dangling ``"x.md#"``.
    """
    p = (path or "").replace("\\", "/").strip()
    s = (section or "").strip()
    if not p:
        return ""
    return f"{p}#{s}" if s else p


def parse_passage_anchor(anchor: str) -> tuple[str, str]:
    """Inverse of :func:`passage_anchor` → ``(path, section)``.

    Splits on the FIRST ``#``, not the last: vault paths never contain ``#``
    but a heading plausibly can (``## Phase #2``), and splitting last would
    silently truncate such a section name into an unresolvable anchor.
    """
    a = (anchor or "").replace("\\", "/").strip()
    if not a:
        return "", ""
    path, _, section = a.partition("#")
    return path.strip(), section.strip()


def passage_href(path: str, section: str = "") -> str:
    """Deep link into the note's *own tool*, with the case loaded.

    KB notes open in ``/kb/#<slug>`` — the real reading surface for that
    corpus (`.claude/rules/deep-link-to-app.md`). Everything else returns ``""``,
    meaning "no better tool than the vault viewer" — the caller then leaves the
    existing ``EOS.noteActions`` links as the open-externally affordance rather
    than inventing a link that lands somewhere the note is not visible.
    """
    p = (path or "").replace("\\", "/").strip()
    if not p or "/kb/" not in f"/{p.strip('/')}/":
        return ""
    slug = p.rsplit("/", 1)[-1]
    if slug.endswith(".md"):
        slug = slug[:-3]
    if not slug:
        return ""
    from urllib.parse import quote

    return f"/kb/#{quote(slug, safe='')}"


# ── Bound methods ───────────────────────────────────────────────────


def _passage_citations_enabled(self) -> bool:
    """Dark flag — off means every citation stays note-level, byte-for-byte.

    Read through ``setting_or_config`` because the key is declared in the
    manifest's ``[provides.settings]`` schema: /settings writes it to the
    settings service, and a bare ``app_config`` would read only emptyos.toml
    and leave the toggle inert (`.claude/rules/app-ui-patterns.md`).
    """
    return bool(
        self.setting_or_config(
            "assistant.feature.passage-citations.enabled",
            False,
            config_key="feature.passage-citations.enabled",
        )
    )


def _passage_for(self, path: str, query: str) -> dict:
    """One retrieved note + the query → the passage inside it that matched.

    Returns ``{}`` when there is no confident passage (unreadable note, no
    query terms, no section scored). ``{}`` is the honest answer — the caller
    keeps citing the note, which is exactly today's behaviour.
    """
    rel = (path or "").replace("\\", "/").strip()
    if not rel or not (query or "").strip():
        return {}
    try:
        body = self.vault_read_body(rel) or ""
    except Exception:
        return {}
    if not body:
        return {}
    terms = query_terms(query)
    if not terms:
        return {}
    section, score = best_section(body, query)
    if not section or score <= 0:
        return {}
    try:
        text = self.vault_read_section(rel, section) or ""
    except Exception:
        text = ""
    if not text:
        return {}
    return {
        "section": section,
        "anchor": passage_anchor(rel, section),
        "excerpt": excerpt_for(text, terms),
        "href": passage_href(rel, section),
    }


@web_route("GET", "/api/passage")
async def api_passage(self, request):
    """Serve the one section a citation anchor points at.

    ``GET /assistant/api/passage?anchor=<path>%23<Section>``. Reads the whole
    vault (not just the KB corpus), so a citation of a journal entry or a
    project note clicks through the same way a KB clause does. Read-only.
    """
    anchor = (request.query_params.get("anchor") or "").strip()
    path, section = parse_passage_anchor(anchor)
    if not path:
        return {"ok": False, "error": "anchor required"}
    if not path.endswith(".md"):
        return {"ok": False, "error": "not a note"}
    try:
        body = (
            self.vault_read_section(path, section) if section else self.vault_read_body(path)
        ) or ""
    except Exception:
        body = ""
    if not body:
        return {"ok": False, "error": "passage not found", "path": path, "section": section}
    return {
        "ok": True,
        "path": path,
        "section": section,
        "body": body[:PASSAGE_CHARS],
        "truncated": len(body) > PASSAGE_CHARS,
        "href": passage_href(path, section),
    }
