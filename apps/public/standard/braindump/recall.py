"""braindump — recall across the KEPT corpus, and nothing else.

Closes the "no corpus recall" gap: Brain Dump could sort one pile of thoughts
beautifully and then had no way to answer *"what have I said about X across
every dump I've made?"* — the one thing in this category that compounds.

**The retention boundary is the whole design.** Brain Dump's trust promise is
leave-no-trace by default: a raw capture is transient, and a summary survives
only when the user ticked "keep this summary" *and* clicked Apply on the review
gate, which writes a note under the app's ``outputs/`` folder. So the corpus
recall reads is exactly that folder — the notes the user already, explicitly,
chose to keep.

Concretely, and load-bearingly:

* :func:`_recall_corpus` is the **only** source of recall material, and it
  reaches exactly one place — ``self.vault_list("outputs/*.md")``.
* It never touches ``self.runs(...)``, a run directory, ``transcript.txt``,
  ``summary.md``, or ``run.json``. A discarded dump is therefore unreachable
  by construction, not by a filter that could be forgotten.
* A note only enters the corpus if it parses as a kept Brain Dump summary
  (:func:`parse_kept_note` — ``braindump`` in its frontmatter tags). A stray
  file in the folder is skipped rather than trusted.

Dark by default behind ``[apps.braindump] feature.recall.enabled`` (read via
``app_config``). With the flag off every endpoint here refuses and the page
renders no recall surface, so behaviour is byte-identical to pre-feature.

Privacy notes that are part of the contract, not decoration:

* **Search sends nothing to a model.** Ranking is semantic when an embedder is
  configured and lexical otherwise; the lexical path is fully local.
* **Synthesis is opt-in per request.** The grounded-answer step runs only when
  the caller passes ``answer: true``, so a plain search never puts vault text
  in front of an LLM (CLAUDE.md rule 19 — explicit per-request, on top of the
  opt-in retention and the dark flag).

Pure helpers below take no ``self`` and are unit-tested without a daemon;
the four bound helpers reach the app via ``self``.

Cross-module callers reach these via ``self.X`` after re-binding in app.py.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from emptyos.frontmatter import parse_frontmatter, strip_frontmatter
from emptyos.sdk import web_route

if TYPE_CHECKING:  # for type hints only
    from .app import BrainDumpApp  # noqa: F401


# ─── Bind to BrainDumpApp class as ───────────────────────────────────────────
#   _recall_enabled    = _recall._recall_enabled
#   _recall_corpus     = _recall._recall_corpus
#   _recall_search     = _recall._recall_search
#   api_recall_status  = _recall.api_recall_status        # @web_route
#   api_recall         = _recall.api_recall               # @web_route
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────────────


#: Frontmatter tag every kept summary carries (stamped by ``save_summary_note``).
KEPT_TAG = "braindump"

#: Folder, relative to the app's vault dir, holding kept summaries. This is the
#: entire retained corpus — see the module docstring's retention boundary.
KEPT_GLOB = "outputs/*.md"

DEFAULT_TOP_K = 5
SEMANTIC_MIN_SCORE = 0.30
EMBED_CHAR_CAP = 1500
EXCERPT_CHARS = 320
ANSWER_CONTEXT_CHARS = 900


RECALL_ANSWER_SYSTEM = """You answer a question using ONLY the user's own kept \
brain-dump summaries, which are supplied below as numbered excerpts.

Write 2-5 sentences in plain prose, addressed to the user. Cite the excerpts you \
used by their number in square brackets, like [2]. Prefer the user's own wording \
and preserve names, dates, and numbers verbatim.

Do NOT:
- state anything the excerpts do not support, or fill a gap from general knowledge
- speculate about what the user "probably" meant
- add advice, next steps, or an action-item list
- pad. If the excerpts only partly answer the question, say what they do cover \
and name what is missing.

If the excerpts do not answer the question at all, say exactly that in one \
sentence and stop."""


# ── Pure helpers (no self, no I/O — unit-tested without a daemon) ────────────

_WORD_RE = re.compile(r"[\w一-鿿]+", re.UNICODE)

# Ignored when scoring: present in almost every dump, so they add rank noise
# without adding signal.
_STOPWORDS = frozenset("""
a an the and or but if of to in on at by for with from as is are was were be
been being it its this that these those i me my we our you your they them their
he she his her do does did done have has had will would can could should about
what when where which who whom why how not no so than then there here
""".split())


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens, stopwords dropped. CJK-aware enough to be useful
    for a bilingual vault (each CJK run becomes one token)."""
    return [t for t in (m.group(0).lower() for m in _WORD_RE.finditer(text or ""))
            if t and t not in _STOPWORDS]


def lexical_score(query: str, text: str) -> float:
    """Local, deterministic relevance in ``0.0..1.0`` — the no-embedder path.

    Fraction of distinct query tokens present in ``text``, plus a small bonus
    when the whole query appears as a substring (an exact phrase should
    outrank a bag-of-words coincidence). Returns ``0.0`` when nothing matches,
    which callers treat as "not a hit" rather than "a weak hit".
    """
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return 0.0
    body_tokens = set(tokenize(text))
    overlap = len(q_tokens & body_tokens) / len(q_tokens)
    if overlap <= 0.0:
        return 0.0
    phrase = (query or "").strip().lower()
    bonus = 0.15 if phrase and phrase in (text or "").lower() else 0.0
    return min(1.0, overlap + bonus)


def rank_lexical(
    items: list[dict], query: str, *, top_k: int = DEFAULT_TOP_K
) -> list[tuple[dict, float]]:
    """Rank corpus items by :func:`lexical_score`, dropping zero-overlap items.

    Ties break newest-first on ``as_of`` (ISO, so string order is date order)
    then on ``path``, so the ordering is stable across runs — a result list that
    reshuffles between identical queries reads as a bug. Done as two passes
    because Python's sort is stable and the two keys run in opposite
    directions.
    """
    scored = [(it, lexical_score(query, it.get("text", ""))) for it in items]
    scored = [(it, s) for it, s in scored if s > 0.0]
    scored.sort(key=lambda pair: ((pair[0].get("as_of") or ""),
                                  (pair[0].get("path") or "")), reverse=True)
    scored.sort(key=lambda pair: -pair[1])
    return scored[:top_k]


def parse_kept_note(raw: str, rel_path: str) -> dict | None:
    """Parse one kept-summary note into a corpus item, or ``None`` if it isn't one.

    Returning ``None`` is the defensive half: only a note whose frontmatter
    carries the ``braindump`` tag — i.e. one this app wrote through the applied
    keep-summary path — becomes recallable. Anything else that happens to sit in
    the folder is skipped rather than indexed.
    """
    if not (raw or "").strip():
        return None
    fm = parse_frontmatter(raw) or {}
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if KEPT_TAG not in [str(t).strip().lower() for t in tags]:
        return None
    body = strip_frontmatter(raw).strip()
    if not body:
        return None
    title = str(fm.get("title") or "").strip() or body.split("\n", 1)[0][:60]
    return {
        "path": rel_path,
        "title": title,
        "as_of": str(fm.get("as_of") or "").strip(),
        "run_id": str(fm.get("run_id") or "").strip(),
        "text": body,
    }


def excerpt(body: str, query: str, *, max_chars: int = EXCERPT_CHARS) -> str:
    """The most query-relevant window of ``body``, so a hit shows *why* it matched.

    Falls back to the head of the note when nothing matches (or the note is
    already short), which is what a semantic hit with no lexical overlap looks
    like.
    """
    body = (body or "").strip()
    if len(body) <= max_chars:
        return body
    q_tokens = set(tokenize(query))
    if q_tokens:
        best_at, best_hits = 0, 0
        step = max(1, max_chars // 4)
        for start in range(0, max(1, len(body) - max_chars + step), step):
            window = body[start:start + max_chars].lower()
            hits = sum(1 for t in q_tokens if t in window)
            if hits > best_hits:
                best_at, best_hits = start, hits
        if best_hits:
            head = "…" if best_at else ""
            return head + body[best_at:best_at + max_chars].strip() + "…"
    return body[:max_chars].strip() + "…"


def build_answer_context(
    hits: list[dict], *, char_cap: int = ANSWER_CONTEXT_CHARS
) -> str:
    """Numbered excerpt block for :data:`RECALL_ANSWER_SYSTEM` to cite.

    Numbering is 1-based and matches the ``hits`` order the caller returns to
    the UI, so a ``[2]`` in the answer points at the second card on screen.
    """
    blocks = []
    for n, h in enumerate(hits, start=1):
        head = f"[{n}] {h.get('title') or 'Untitled'}"
        when = (h.get("as_of") or "").strip()
        if when:
            head += f" ({when})"
        blocks.append(f"{head}\n{(h.get('text') or '')[:char_cap].strip()}")
    return "\n\n".join(blocks)


# ── Bound helpers ───────────────────────────────────────────────────────────

def _recall_enabled(self) -> bool:
    """Dark-default gate. Recall also requires the app's master flag — with
    Brain Dump itself off there is nothing to recall across."""
    if not self._enabled():
        return False
    return bool(self.app_config("feature.recall.enabled", False))


def _recall_corpus(self) -> list[dict]:
    """THE retention boundary. Reads only kept summary notes — nothing else.

    Reaches exactly one place: this app's vault ``outputs/`` folder, which only
    ``save_summary_note`` writes to and only after the user applied the
    keep-summary proposal on the review gate. Run directories, transcripts, and
    discarded captures are not read here and must never be added — a discarded
    dump stays unreachable because this function cannot see it, not because a
    filter excludes it.
    """
    items: list[dict] = []
    try:
        paths = self.vault_list(KEPT_GLOB)
    except Exception:
        return items
    for p in paths:
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = self.vault_rel(p) or str(p)
        item = parse_kept_note(raw, rel)
        if item:
            items.append(item)
    return items


async def _recall_search(self, query: str, *, top_k: int) -> tuple[list[dict], str]:
    """Rank the kept corpus against ``query``. Returns ``(hits, mode)``.

    Semantic when an embedder is configured, lexical otherwise — the SDK's
    stated contract for a fresh self-host with no key. Both paths read the same
    corpus, so retrieval quality varies but the retention boundary does not.
    """
    items = self._recall_corpus()
    if not items or not (query or "").strip():
        return [], "empty"

    mode = "lexical"
    scored: list[tuple[dict, float]] = []
    if getattr(self, "embeddings_available", False):
        try:
            index = await self.embedding_index(
                items, text_fn=lambda it: it["text"][:EMBED_CHAR_CAP]
            )
            scored = await index.search(
                query, top_k=top_k, min_score=SEMANTIC_MIN_SCORE
            )
            mode = "semantic"
        except Exception:
            scored = []  # degrade to lexical rather than fail the request
    if not scored and mode != "semantic":
        scored = rank_lexical(items, query, top_k=top_k)

    hits = []
    for it, score in scored:
        hits.append({
            "path": it["path"],
            "title": it["title"],
            "as_of": it["as_of"],
            "run_id": it["run_id"],
            "excerpt": excerpt(it["text"], query),
            "text": it["text"],
            "score": round(float(score), 4),
        })
    return hits, mode


@web_route("GET", "/api/recall/status")
async def api_recall_status(self, request):
    """What the page needs to decide whether to render the recall surface at
    all. With the flag off this is the only thing recall answers."""
    if not self._recall_enabled():
        return {"enabled": False}
    return {
        "enabled": True,
        "kept": len(self._recall_corpus()),
        "semantic": bool(getattr(self, "embeddings_available", False)),
    }


@web_route("POST", "/api/recall")
async def api_recall(self, request):
    """Ask across every dump you kept.

    Body: ``{query, top_k?, answer?}``. ``answer`` is opt-in per request — the
    default search is retrieval only and puts no vault text in front of a
    model.
    """
    if not self._recall_enabled():
        return {"error": "disabled"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = (body.get("query") or "").strip()
    if not query:
        return {"error": "no query"}
    try:
        top_k = max(1, min(20, int(body.get("top_k") or DEFAULT_TOP_K)))
    except (TypeError, ValueError):
        top_k = DEFAULT_TOP_K

    hits, mode = await self._recall_search(query, top_k=top_k)

    answer = ""
    if body.get("answer") and hits:
        user = (
            f"QUESTION:\n{query}\n\n"
            f"KEPT BRAIN-DUMP EXCERPTS:\n{build_answer_context(hits)}"
        )
        answer = (await self.think(
            user, domain="reason", system=RECALL_ANSWER_SYSTEM, temperature=0.2,
        ) or "").strip()

    self.spawn_background(self.emit(
        "braindump:recalled", {"hits": len(hits), "mode": mode, "answered": bool(answer)}
    ))
    # `text` is the full note body — needed to ground the answer, too heavy for
    # the wire. The UI renders `excerpt`.
    return {
        "query": query,
        "mode": mode,
        "answer": answer,
        # Feeds the auto-provenance chip on the answer box (data-ai-output);
        # only when this request actually thought, so a stale attribution from
        # an earlier call is never re-attached to a hits-only response.
        "provenance": self.last_provenance() if answer else None,
        "hits": [{k: v for k, v in h.items() if k != "text"} for h in hits],
    }
