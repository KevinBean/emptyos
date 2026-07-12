"""Unit tests for BaseApp.recall() + its sdk/utils scoring helpers.

Function-level — no kernel boot. recall() is exercised against a light stub
providing only the embedding + availability surface it touches in items=
mode (so no vault I/O). The autouse ``server_health`` fixture in conftest
may skip the suite if the daemon is down (project convention); the logic
under test is pure.

Covers the Phase-1 borrow from moeru-ai/airi: blended
similarity + recency-decay + salience recall.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import pytest

from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.utils import ebbinghaus_decay, iso_age_days, salience_from_mood

NOW = datetime(2026, 6, 6, tzinfo=timezone.utc)
_VOCAB = ["cat", "dog", "boat", "river", "money", "day"]


def _vec(text: str) -> list[float]:
    """Deterministic toy embedding: per-vocab token counts + a tiny constant
    dim so no vector is all-zero (which would cosine-0 against everything)."""
    t = (text or "").lower()
    return [float(len(re.findall(rf"\b{w}\b", t))) for w in _VOCAB] + [0.001]


class _RecallStub:
    """Minimal surface BaseApp.recall() needs in items= mode."""

    def __init__(self, available: bool = True):
        self._available = available

    @property
    def embeddings_available(self) -> bool:
        return self._available

    async def embed_text(self, text):
        return _vec(text)

    async def embed_texts(self, texts):
        return [_vec(t) for t in texts]


def _recall(stub, query, **kw):
    return asyncio.run(BaseApp.recall(stub, query, **kw))


# ── pure helpers ──────────────────────────────────────────────────────


def test_ebbinghaus_decay():
    assert ebbinghaus_decay(0) == 1.0
    assert abs(ebbinghaus_decay(30, 30) - 0.5) < 1e-9
    assert ebbinghaus_decay(60, 30) == 0.25
    assert ebbinghaus_decay(-5, 30) == 1.0      # future → full weight
    assert ebbinghaus_decay(10, 0) == 1.0       # degenerate half-life


def test_salience_from_mood():
    assert salience_from_mood("great") == 10.0
    assert salience_from_mood("bad") == -10.0
    assert salience_from_mood("okay") == 0.0
    assert salience_from_mood("GreAt") == 10.0  # case-insensitive
    assert salience_from_mood("") == 0.0
    assert salience_from_mood(None) == 0.0
    assert salience_from_mood("ecstatic") == 0.0  # unknown → neutral


def test_iso_age_days():
    assert iso_age_days("", NOW) is None
    assert iso_age_days("not-a-date", NOW) is None
    assert abs(iso_age_days("2026-06-01", NOW) - 5) < 1e-6
    assert abs(iso_age_days("2026-06-04T00:00:00Z", NOW) - 2) < 1e-6
    assert iso_age_days("2026-06-10", NOW) < 0  # future date → negative age


# ── recall() ──────────────────────────────────────────────────────────


def test_recall_requires_exactly_one_mode():
    stub = _RecallStub()
    with pytest.raises(ValueError):
        _recall(stub, "x")                                  # neither
    with pytest.raises(ValueError):
        _recall(stub, "x", tags=["a"], items=[{"text": "y"}])  # both


def test_recall_similarity_orders_by_relevance():
    stub = _RecallStub(available=True)
    items = [
        {"id": "a", "text": "the cat sat"},
        {"id": "b", "text": "a boat on the river"},
        {"id": "c", "text": "dog and cat"},
    ]
    out = _recall(stub, "cat", items=items, weights=(1.0, 0.0, 0.0), top_k=3)
    assert out[0]["id"] in {"a", "c"}   # cat-mentioning items rank top
    assert out[-1]["id"] == "b"         # boat/river ranks last
    assert "score" in out[0] and "sim" in out[0] and "recency" in out[0]


def test_recall_recency_breaks_ties():
    stub = _RecallStub(available=False)  # similarity off
    items = [
        {"id": "old", "text": "money", "created": "2026-01-01"},
        {"id": "new", "text": "money", "created": "2026-06-05"},
    ]
    out = _recall(stub, "money", items=items, weights=(0.0, 1.0, 0.0), now=NOW)
    assert out[0]["id"] == "new"


def test_recall_salience_breaks_ties():
    stub = _RecallStub(available=False)
    items = [
        {"id": "meh", "text": "day", "mood": "okay"},
        {"id": "big", "text": "day", "mood": "great"},
    ]
    out = _recall(stub, "day", items=items, weights=(0.0, 0.0, 1.0), now=NOW)
    assert out[0]["id"] == "big"
    assert out[0]["salience"] == 10.0


def test_recall_explicit_salience_overrides_mood():
    stub = _RecallStub(available=False)
    items = [{"id": "z", "text": "t", "salience": -7, "mood": "great"}]
    out = _recall(stub, "t", items=items, weights=(0.0, 0.0, 1.0), now=NOW)
    assert out[0]["salience"] == -7.0   # explicit field wins over mood


def test_recall_degrades_without_embeddings():
    stub = _RecallStub(available=False)
    items = [
        {"id": "x", "text": "anything", "created": "2026-06-05", "salience": 8},
        {"id": "y", "text": "whatever", "created": "2026-01-01", "salience": 0},
    ]
    out = _recall(stub, "unrelated query", items=items, now=NOW)  # default weights
    assert out[0]["id"] == "x"                       # recency+salience favour x
    assert all(o["sim"] == 0.0 for o in out)         # sim term is 0 when off


def test_recall_empty_candidates():
    assert _recall(_RecallStub(), "q", items=[]) == []
