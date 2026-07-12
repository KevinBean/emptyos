"""revision_align — pure content-based clause alignment across two revisions.

When a standard is re-issued, clause numbers shift: rev 0.2 §1.7 may become
rev 1.0 §1.8, sections get added/removed/split. Number-matching therefore aligns
the *wrong* content. This aligns old↔new clauses by **content**, cascading
cheap→expensive so most pairs are matched for free:

  1. exact normalized-title match        (stable across revisions, free)
  2. exact clause-number match           (confirms the easy unchanged ones)
  3. embedding cosine best-match         (the renumbered ones — needs embed_fn)
  4. LLM disambiguation of the leftovers (split/merge/uncertain — needs think_fn)

Whatever is still unmatched is ``added`` (new-only) or ``removed`` (old-only).

Pure: no ``self``, no kernel, no I/O. The embedder and the LLM are **injected**
(``embed_fn`` / ``think_fn``), mirroring ``emptyos/sdk/html_element_edit.py`` —
so this unit-tests without a daemon (``tests/test_unit_revision_align.py``).
Degrades gracefully: with neither injected it still does title+number matching.

Each input clause is a dict ``{slug, clause_no, title, text}``. Each returned
edge is ``{old_slug, new_slug, old_no, new_no, old_title, new_title, status,
num_changed, content_changed, similarity, method}`` where ``status`` is one of
``unchanged · modified · renumbered · added · removed · split · merged``.
"""

from __future__ import annotations

import json
import math
import re
from typing import Awaitable, Callable, Optional

ALIGN_SYSTEM = (
    "You align clauses between two revisions of a technical standard by MEANING, "
    "not by clause number (numbers shift between revisions). You are given the "
    "leftover clauses that simple matching could not pair. Return STRICT JSON only "
    "(no prose, no code fence): "
    '{"matches":[{"old":"<old clause_no>","new":"<new clause_no>"}],'
    '"split":[{"old":"<old>","new":["<n1>","<n2>"]}],'
    '"merged":[{"old":["<o1>","<o2>"],"new":"<new>"}]}. '
    "Only pair clauses about the same subject. Omit anything with no good counterpart "
    "(it is an addition or a removal). Use the exact clause_no strings given."
)


def _norm_title(t: str | None) -> str:
    return re.sub(r"\s+", " ", str(t or "").strip().lower())


def _norm_text(t: str | None) -> str:
    return re.sub(r"\s+", " ", str(t or "").strip().lower())


def _norm_no(n: str | None) -> str:
    return re.sub(r"\s+", "", str(n or "").replace("§", "").strip())


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _embed_payload(c: dict) -> str:
    return f"{c.get('title') or ''}\n{c.get('text') or ''}".strip()


def _make_edge(o: dict | None, n: dict | None, *, similarity: float, method: str) -> dict:
    """Build an alignment edge, classifying its status."""
    if o is None:  # new-only
        return {
            "old_slug": None, "new_slug": n.get("slug"),
            "old_no": None, "new_no": n.get("clause_no"),
            "old_title": None, "new_title": n.get("title"),
            "status": "added", "num_changed": False, "content_changed": True,
            "similarity": 0.0, "method": method,
        }
    if n is None:  # old-only
        return {
            "old_slug": o.get("slug"), "new_slug": None,
            "old_no": o.get("clause_no"), "new_no": None,
            "old_title": o.get("title"), "new_title": None,
            "status": "removed", "num_changed": False, "content_changed": True,
            "similarity": 0.0, "method": method,
        }
    num_changed = _norm_no(o.get("clause_no")) != _norm_no(n.get("clause_no"))
    content_changed = _norm_text(o.get("text")) != _norm_text(n.get("text"))
    if num_changed:
        status = "renumbered"
    elif content_changed:
        status = "modified"
    else:
        status = "unchanged"
    return {
        "old_slug": o.get("slug"), "new_slug": n.get("slug"),
        "old_no": o.get("clause_no"), "new_no": n.get("clause_no"),
        "old_title": o.get("title"), "new_title": n.get("title"),
        "status": status, "num_changed": num_changed, "content_changed": content_changed,
        "similarity": round(float(similarity), 4), "method": method,
    }


def _parse_json(raw: str) -> dict:
    """Tolerant JSON extraction from an LLM reply (strips fences/prose)."""
    s = str(raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


async def align_revisions(
    old: list[dict],
    new: list[dict],
    *,
    embed_fn: Optional[Callable[[list[str]], Awaitable[list[list[float]]]]] = None,
    think_fn: Optional[Callable[[str, str], Awaitable[str]]] = None,
    min_similarity: float = 0.55,
) -> list[dict]:
    """Align `old` clauses to `new` clauses by content. See module docstring."""
    old_left = [c for c in (old or []) if c]
    new_left = [c for c in (new or []) if c]
    edges: list[dict] = []

    def _take(pool: list[dict], pred) -> dict | None:
        for i, c in enumerate(pool):
            if pred(c):
                return pool.pop(i)
        return None

    # 1. Exact normalized-title match.
    for o in list(old_left):
        ot = _norm_title(o.get("title"))
        if not ot:
            continue
        n = _take(new_left, lambda c: _norm_title(c.get("title")) == ot)
        if n is not None:
            old_left.remove(o)
            edges.append(_make_edge(o, n, similarity=1.0, method="title"))

    # 2. Exact clause-number match (the easy unchanged/modified ones).
    for o in list(old_left):
        on = _norm_no(o.get("clause_no"))
        if not on:
            continue
        n = _take(new_left, lambda c: _norm_no(c.get("clause_no")) == on)
        if n is not None:
            old_left.remove(o)
            edges.append(_make_edge(o, n, similarity=1.0, method="number"))

    # 3. Embedding cosine best-match (renumbered clauses), if an embedder is given.
    if embed_fn and old_left and new_left:
        try:
            ovecs = await embed_fn([_embed_payload(c) for c in old_left])
            nvecs = await embed_fn([_embed_payload(c) for c in new_left])
        except Exception:
            ovecs = nvecs = None
        if ovecs and nvecs and len(ovecs) == len(old_left) and len(nvecs) == len(new_left):
            # Score every remaining pair, then greedily take the global best.
            pairs = []
            for i, ov in enumerate(ovecs):
                for j, nv in enumerate(nvecs):
                    pairs.append((_cosine(ov, nv), i, j))
            pairs.sort(reverse=True)
            used_o: set[int] = set()
            used_n: set[int] = set()
            matched_o: set[int] = set()
            for sim, i, j in pairs:
                if sim < min_similarity:
                    break
                if i in used_o or j in used_n:
                    continue
                used_o.add(i)
                used_n.add(j)
                matched_o.add(i)
                edges.append(_make_edge(old_left[i], new_left[j], similarity=sim, method="embedding"))
            old_left = [c for k, c in enumerate(old_left) if k not in used_o]
            new_left = [c for k, c in enumerate(new_left) if k not in used_n]

    # 4. LLM disambiguation of whatever is left (split/merge/uncertain pairs).
    if think_fn and old_left and new_left:
        payload = {
            "old": [{"clause_no": c.get("clause_no"), "title": c.get("title")} for c in old_left],
            "new": [{"clause_no": c.get("clause_no"), "title": c.get("title")} for c in new_left],
        }
        try:
            reply = await think_fn(ALIGN_SYSTEM, json.dumps(payload, ensure_ascii=False))
            parsed = _parse_json(reply)
        except Exception:
            parsed = {}
        # 1:1 matches
        for mm in parsed.get("matches") or []:
            o = _take(old_left, lambda c: _norm_no(c.get("clause_no")) == _norm_no(mm.get("old")))
            n = _take(new_left, lambda c: _norm_no(c.get("clause_no")) == _norm_no(mm.get("new")))
            if o is not None and n is not None:
                edges.append(_make_edge(o, n, similarity=0.0, method="llm"))
            else:  # put back any half-match
                if o is not None:
                    old_left.append(o)
                if n is not None:
                    new_left.append(n)
        # splits: one old → several new
        for sp in parsed.get("split") or []:
            o = _take(old_left, lambda c: _norm_no(c.get("clause_no")) == _norm_no(sp.get("old")))
            news = [
                _take(new_left, lambda c, t=t: _norm_no(c.get("clause_no")) == _norm_no(t))
                for t in (sp.get("new") or [])
            ]
            news = [x for x in news if x is not None]
            if o is not None and news:
                for n in news:
                    e = _make_edge(o, n, similarity=0.0, method="llm")
                    e["status"] = "split"
                    edges.append(e)
            elif o is not None:
                old_left.append(o)
        # merges: several old → one new
        for mg in parsed.get("merged") or []:
            n = _take(new_left, lambda c: _norm_no(c.get("clause_no")) == _norm_no(mg.get("new")))
            olds = [
                _take(old_left, lambda c, t=t: _norm_no(c.get("clause_no")) == _norm_no(t))
                for t in (mg.get("old") or [])
            ]
            olds = [x for x in olds if x is not None]
            if n is not None and olds:
                for o in olds:
                    e = _make_edge(o, n, similarity=0.0, method="llm")
                    e["status"] = "merged"
                    edges.append(e)
            elif n is not None:
                new_left.append(n)

    # 5. Leftovers → removed / added.
    for o in old_left:
        edges.append(_make_edge(o, None, similarity=0.0, method="unmatched"))
    for n in new_left:
        edges.append(_make_edge(None, n, similarity=0.0, method="unmatched"))

    return edges
