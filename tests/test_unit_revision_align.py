"""Unit tests for emptyos.sdk.revision_align — pure content-based clause aligner.

No daemon. The embedder + LLM are faked. Async calls run via asyncio.run so the
tests don't depend on pytest-asyncio config.
"""

import asyncio

from emptyos.sdk.revision_align import align_revisions


def _run(coro):
    return asyncio.run(coro)


def _by(edges, **kv):
    """Find the single edge matching the given key/values."""
    for e in edges:
        if all(e.get(k) == v for k, v in kv.items()):
            return e
    return None


def test_title_match_detects_renumber():
    old = [{"slug": "o17", "clause_no": "1.7", "title": "Ratings", "text": "rate A"}]
    new = [{"slug": "n18", "clause_no": "1.8", "title": "Ratings", "text": "rate B"}]
    edges = _run(align_revisions(old, new))
    e = _by(edges, old_slug="o17")
    assert e["new_slug"] == "n18"
    assert e["status"] == "renumbered"
    assert e["num_changed"] is True
    assert e["method"] == "title"


def test_number_match_unchanged_vs_modified():
    old = [
        {"slug": "o1", "clause_no": "1.1", "title": "Purpose", "text": "same"},
        {"slug": "o2", "clause_no": "1.2", "title": "Scope", "text": "old scope"},
    ]
    new = [
        {"slug": "n1", "clause_no": "1.1", "title": "Purpose", "text": "same"},
        {"slug": "n2", "clause_no": "1.2", "title": "Scope", "text": "new scope"},
    ]
    edges = _run(align_revisions(old, new))
    assert _by(edges, old_slug="o1")["status"] == "unchanged"
    assert _by(edges, old_slug="o2")["status"] == "modified"


def test_added_and_removed():
    old = [{"slug": "o1", "clause_no": "1.1", "title": "Gone", "text": "x"}]
    new = [{"slug": "n9", "clause_no": "1.9", "title": "Brand New", "text": "y"}]
    edges = _run(align_revisions(old, new))
    assert _by(edges, old_slug="o1")["status"] == "removed"
    assert _by(edges, new_slug="n9")["status"] == "added"


def test_embedding_aligns_renumbered_with_changed_title():
    # Titles + numbers differ, so only embeddings can pair them.
    old = [{"slug": "o", "clause_no": "7.17", "title": "Duct installation", "text": "ducts"}]
    new = [{"slug": "n", "clause_no": "7.19", "title": "Duct banks and conduits", "text": "ducts"}]

    async def fake_embed(texts):
        # one-hot on whether the payload mentions "duct" → identical vectors → cosine 1.0
        return [[1.0, 0.0] if "duct" in t.lower() else [0.0, 1.0] for t in texts]

    edges = _run(align_revisions(old, new, embed_fn=fake_embed, min_similarity=0.5))
    e = _by(edges, old_slug="o")
    assert e["new_slug"] == "n"
    assert e["status"] == "renumbered"
    assert e["method"] == "embedding"
    assert e["similarity"] >= 0.99


def test_embedding_below_threshold_falls_to_added_removed():
    old = [{"slug": "o", "clause_no": "1.1", "title": "Alpha", "text": "alpha"}]
    new = [{"slug": "n", "clause_no": "2.2", "title": "Beta", "text": "beta"}]

    async def fake_embed(texts):
        return [[1.0, 0.0] if "alpha" in t.lower() else [0.0, 1.0] for t in texts]

    edges = _run(align_revisions(old, new, embed_fn=fake_embed, min_similarity=0.5))
    assert _by(edges, old_slug="o")["status"] == "removed"
    assert _by(edges, new_slug="n")["status"] == "added"


def test_llm_disambiguation_match_and_split():
    old = [
        {"slug": "oA", "clause_no": "3.1", "title": "Aaa", "text": "a"},
        {"slug": "oB", "clause_no": "3.2", "title": "Bbb", "text": "b"},
    ]
    new = [
        {"slug": "nA", "clause_no": "4.5", "title": "Zzz", "text": "a"},
        {"slug": "nB1", "clause_no": "4.6", "title": "Yyy", "text": "b1"},
        {"slug": "nB2", "clause_no": "4.7", "title": "Www", "text": "b2"},
    ]

    async def fake_think(system, user):
        # 3.1 -> 4.5 (rename); 3.2 splits into 4.6 + 4.7
        return (
            '{"matches":[{"old":"3.1","new":"4.5"}],'
            '"split":[{"old":"3.2","new":["4.6","4.7"]}],"merged":[]}'
        )

    edges = _run(align_revisions(old, new, think_fn=fake_think))
    assert _by(edges, old_slug="oA")["new_slug"] == "nA"
    assert _by(edges, old_slug="oA")["method"] == "llm"
    splits = [e for e in edges if e["status"] == "split"]
    assert {e["new_slug"] for e in splits} == {"nB1", "nB2"}


def test_graceful_degrade_without_helpers():
    # No embed_fn, no think_fn → title+number matching only; the rest add/remove.
    old = [{"slug": "o", "clause_no": "1.1", "title": "Same Title", "text": "x"}]
    new = [{"slug": "n", "clause_no": "9.9", "title": "Same Title", "text": "x2"}]
    edges = _run(align_revisions(old, new))
    e = _by(edges, old_slug="o")
    assert e["new_slug"] == "n"           # matched by title despite number change
    assert e["status"] == "renumbered"
