"""Brain Dump corpus recall — closes gap ``braindump-no-corpus-recall``.

Two things are proved here, and the second one is the reason the feature is
allowed to exist at all:

1. **Recall works when you opted in.** A kept summary note (the artifact the
   applied keep-summary path writes) is findable by a later question, both on
   the local lexical path and on the semantic path, and the grounded-answer
   step is fed the matched excerpts.

2. **A discarded dump leaves no retained trace, with the feature present.**
   Brain Dump's leave-no-trace default is a trust promise, not a preference:
   adding recall must not turn a transient capture into a searchable one. So
   the corpus builder is driven with a real discarded run sitting on disk and
   asserted to never see it — not to filter it out, to never reach it. It also
   may not touch ``self.runs`` at all, which the fake enforces by raising.

Daemon-free: the app module is loaded from source and driven with fakes, so no
real embedder, LLM, kernel, or vault is involved.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

_RECALL = (
    Path(__file__).resolve().parents[1]
    / "apps/public/standard/braindump/recall.py"
)
_SPEC = importlib.util.spec_from_file_location("braindump_recall_under_test", _RECALL)
_MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MOD)


# A string that exists ONLY inside a discarded run's residue. If it ever shows
# up in a corpus item, an embedded text, or an LLM prompt, the trust promise is
# broken and the assertion naming it fails.
DISCARDED_SECRET = "ZZ-DISCARDED-SECRET-antibiotics-dosage-ZZ"

KEPT_NOTE = """---
title: Rewire the billing retry loop
tags:
  - braindump
author: ai
lifecycle: snapshot
as_of: 2026-08-10
run_id: run-kept-1
---

The billing retry loop double-charges when Stripe returns a 409. Need to make
the retry idempotent by keying on the invoice id before the next release.
"""

KEPT_NOTE_2 = """---
title: Garden watering schedule
tags:
  - braindump
as_of: 2026-08-02
run_id: run-kept-2
---

Tomatoes need water every second morning through February. The drip timer on
the north bed is stuck open.
"""

# Sits in outputs/ but is not a kept Brain Dump summary — must be ignored.
FOREIGN_NOTE = """---
title: Someone else's report
tags:
  - report
as_of: 2026-08-11
---

Quarterly numbers for the north region.
"""


class _NoRunsAccess(AssertionError):
    """Raised if recall reaches for run storage — the whole point is it can't."""


class FakeApp:
    """Minimal stand-in carrying only what recall.py is allowed to use.

    ``runs`` raises rather than returning a stub, so "recall never reads a run
    directory" is enforced by the test double instead of being asserted about
    code that could quietly change.
    """

    def __init__(self, vault_dir: Path, *, recall_on=True, master_on=True,
                 embeddings=False):
        self.vault_dir = vault_dir
        self._recall_on = recall_on
        self._master_on = master_on
        self.embeddings_available = embeddings
        self.embedded_texts: list[str] = []
        self.think_prompts: list[str] = []
        self.think_systems: list[str] = []
        self.emitted: list[tuple[str, dict]] = []
        self.think_reply = "You said the retry loop double-charges on 409. [1]"

        # Bind the real implementations under test.
        self._recall_enabled = _MOD._recall_enabled.__get__(self)
        self._recall_corpus = _MOD._recall_corpus.__get__(self)
        self._recall_search = _MOD._recall_search.__get__(self)
        self.api_recall = _MOD.api_recall.__get__(self)
        self.api_recall_status = _MOD.api_recall_status.__get__(self)

    # -- flags --
    def _enabled(self):
        return self._master_on

    def app_config(self, key, default=None):
        if key == "feature.recall.enabled":
            return self._recall_on
        return default

    # -- vault --
    def vault_list(self, pattern="*.md"):
        return sorted(self.vault_dir.glob(pattern))

    def vault_rel(self, p):
        return "30_Resources/EmptyOS/braindump/" + Path(p).name

    # -- forbidden --
    def runs(self, *a, **kw):
        raise _NoRunsAccess("recall must never reach run storage")

    # -- capabilities --
    async def embedding_index(self, items, text_fn):
        texts = [text_fn(it) for it in items]
        self.embedded_texts.extend(texts)
        return _FakeIndex(items, texts)

    async def think(self, user, *, domain=None, system=None, temperature=None):
        self.think_prompts.append(user)
        self.think_systems.append(system or "")
        return self.think_reply

    async def emit(self, event_type, data=None):
        self.emitted.append((event_type, data or {}))

    def spawn_background(self, coro):
        # Drive the coroutine to completion synchronously. `emit` has no awaits
        # inside it, so one send() finishes it — which exercises the real emit
        # call rather than dropping it on the floor as a pending task.
        try:
            coro.send(None)
        except StopIteration:
            pass
        else:
            coro.close()


class _FakeIndex:
    """Deterministic stand-in for EmbeddingIndex — substring hit = 0.9."""

    def __init__(self, items, texts):
        self.items = items
        self.texts = texts

    async def search(self, query, top_k=10, min_score=0.0):
        out = []
        for it, text in zip(self.items, self.texts):
            score = 0.9 if any(w in text.lower() for w in query.lower().split()) else 0.1
            if score >= min_score:
                out.append((it, score))
        out.sort(key=lambda pair: -pair[1])
        return out[:top_k]


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _make_vault(tmp_path: Path, notes: dict[str, str]) -> Path:
    """Build the app's vault dir with an outputs/ folder of kept notes."""
    outputs = tmp_path / "vault" / "outputs"
    outputs.mkdir(parents=True)
    for name, raw in notes.items():
        (outputs / name).write_text(raw, encoding="utf-8")
    return tmp_path / "vault"


def _make_discarded_run(tmp_path: Path) -> Path:
    """A run directory in the state ``api_discard`` leaves behind.

    Mirrors the real shape: raw audio + transcript unlinked, ``status``
    flipped to discarded, but ``summary.md`` and the transcript/summary text
    inside ``run.json`` still on disk. That residue is exactly what must never
    become searchable.
    """
    run_dir = tmp_path / "data" / "apps" / "braindump" / "runs" / "run-discarded-1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.md").write_text(DISCARDED_SECRET, encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "run-discarded-1",
        "status": "discarded",
        "results": {
            "transcribe": {"transcript": DISCARDED_SECRET},
            "summarize": {"summary": DISCARDED_SECRET},
        },
    }), encoding="utf-8")
    return run_dir


# ── Pure helpers ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_parse_kept_note_accepts_a_kept_summary():
    item = _MOD.parse_kept_note(KEPT_NOTE, "outputs/a.md")
    assert item is not None
    assert item["title"] == "Rewire the billing retry loop"
    assert item["as_of"] == "2026-08-10"
    assert item["run_id"] == "run-kept-1"
    assert "double-charges" in item["text"]


@pytest.mark.unit
@pytest.mark.parametrize("raw", [FOREIGN_NOTE, "", "no frontmatter at all"])
def test_parse_kept_note_rejects_anything_not_a_kept_summary(raw):
    """Only a note this app wrote through the applied keep path is recallable."""
    assert _MOD.parse_kept_note(raw, "outputs/x.md") is None


@pytest.mark.unit
def test_lexical_score_ranks_overlap_and_ignores_stopwords():
    assert _MOD.lexical_score("billing retry", "the billing retry loop") == pytest.approx(1.0)
    assert _MOD.lexical_score("billing retry", "tomatoes need water") == 0.0
    # A query of only stopwords carries no signal and must not match everything.
    assert _MOD.lexical_score("the and of", "the billing retry loop") == 0.0


@pytest.mark.unit
def test_rank_lexical_is_deterministic_and_drops_non_matches():
    items = [
        {"path": "b.md", "as_of": "2026-08-02", "text": "tomatoes and water"},
        {"path": "a.md", "as_of": "2026-08-10", "text": "billing retry loop"},
        {"path": "c.md", "as_of": "2026-08-11", "text": "unrelated prose"},
    ]
    ranked = _MOD.rank_lexical(items, "billing retry")
    assert [it["path"] for it, _ in ranked] == ["a.md"]
    # Same query twice yields the same order (stability, not luck).
    assert _MOD.rank_lexical(items, "billing retry") == ranked


@pytest.mark.unit
def test_excerpt_centres_on_the_query_match():
    body = ("filler " * 80) + "the invoice id is the idempotency key " + ("tail " * 80)
    out = _MOD.excerpt(body, "idempotency key", max_chars=120)
    assert "idempotency" in out
    assert len(out) <= 130  # max_chars plus the ellipsis affordances


@pytest.mark.unit
def test_build_answer_context_numbers_hits_from_one():
    ctx = _MOD.build_answer_context([
        {"title": "First", "as_of": "2026-08-10", "text": "alpha"},
        {"title": "Second", "as_of": "", "text": "beta"},
    ])
    assert "[1] First (2026-08-10)" in ctx
    assert "[2] Second" in ctx
    assert "alpha" in ctx and "beta" in ctx


# ── (a) Recall works when the user opted in ─────────────────────────────────

@pytest.mark.unit
def test_corpus_contains_kept_summaries_only(tmp_path):
    vault = _make_vault(tmp_path, {
        "kept1.md": KEPT_NOTE, "kept2.md": KEPT_NOTE_2, "foreign.md": FOREIGN_NOTE})
    app = FakeApp(vault)
    titles = sorted(it["title"] for it in app._recall_corpus())
    assert titles == ["Garden watering schedule", "Rewire the billing retry loop"]


@pytest.mark.unit
def test_lexical_recall_finds_a_kept_dump(tmp_path):
    vault = _make_vault(tmp_path, {"kept1.md": KEPT_NOTE, "kept2.md": KEPT_NOTE_2})
    app = FakeApp(vault, embeddings=False)
    hits, mode = asyncio.run(app._recall_search("billing retry idempotent", top_k=5))
    assert mode == "lexical"
    assert [h["title"] for h in hits] == ["Rewire the billing retry loop"]
    assert app.embedded_texts == []  # no embedder configured → nothing left the box


@pytest.mark.unit
def test_semantic_recall_finds_a_kept_dump(tmp_path):
    vault = _make_vault(tmp_path, {"kept1.md": KEPT_NOTE, "kept2.md": KEPT_NOTE_2})
    app = FakeApp(vault, embeddings=True)
    hits, mode = asyncio.run(app._recall_search("double-charges", top_k=5))
    assert mode == "semantic"
    assert hits and hits[0]["title"] == "Rewire the billing retry loop"
    assert any("double-charges" in t for t in app.embedded_texts)


@pytest.mark.unit
def test_recall_endpoint_answers_only_when_asked(tmp_path):
    vault = _make_vault(tmp_path, {"kept1.md": KEPT_NOTE})
    app = FakeApp(vault)

    plain = asyncio.run(app.api_recall(FakeRequest({"query": "billing retry"})))
    assert plain["hits"] and plain["answer"] == ""
    assert app.think_prompts == []  # a plain search puts no vault text near a model

    asked = asyncio.run(app.api_recall(
        FakeRequest({"query": "billing retry", "answer": True})))
    assert asked["answer"] == app.think_reply
    assert "double-charges" in app.think_prompts[0]
    assert app.think_systems[0] == _MOD.RECALL_ANSWER_SYSTEM
    # The wire payload carries the excerpt, not the whole note body.
    assert "text" not in asked["hits"][0]
    assert asked["hits"][0]["excerpt"]


@pytest.mark.unit
def test_recall_endpoint_refuses_an_empty_query(tmp_path):
    app = FakeApp(_make_vault(tmp_path, {"kept1.md": KEPT_NOTE}))
    assert asyncio.run(app.api_recall(FakeRequest({"query": "   "})))["error"] == "no query"


@pytest.mark.unit
def test_recall_is_dark_by_default(tmp_path):
    """Flag off → every recall surface refuses, so behaviour is pre-feature."""
    vault = _make_vault(tmp_path, {"kept1.md": KEPT_NOTE})
    off = FakeApp(vault, recall_on=False)
    assert off._recall_enabled() is False
    assert asyncio.run(off.api_recall(FakeRequest({"query": "billing"})))["error"] == "disabled"
    assert asyncio.run(off.api_recall_status(FakeRequest({}))) == {"enabled": False}
    assert off.think_prompts == [] and off.embedded_texts == []


@pytest.mark.unit
def test_recall_follows_the_master_flag(tmp_path):
    """With Brain Dump itself off there is nothing to recall across."""
    vault = _make_vault(tmp_path, {"kept1.md": KEPT_NOTE})
    app = FakeApp(vault, recall_on=True, master_on=False)
    assert app._recall_enabled() is False
    assert asyncio.run(app.api_recall(FakeRequest({"query": "billing"})))["error"] == "disabled"


# ── (b) THE load-bearing one: a discarded dump leaves no retained trace ──────

@pytest.mark.unit
def test_discarded_dump_is_unreachable_by_recall(tmp_path):
    """A discarded capture must not become searchable now that recall exists.

    Drives the corpus builder with a real discarded run on disk (the exact
    residue ``api_discard`` leaves) and asserts its content reaches nothing:
    not the corpus, not the embedder, not the LLM prompt, not the response.
    """
    vault = _make_vault(tmp_path, {"kept1.md": KEPT_NOTE, "kept2.md": KEPT_NOTE_2})
    run_dir = _make_discarded_run(tmp_path)
    assert DISCARDED_SECRET in (run_dir / "summary.md").read_text(encoding="utf-8")
    assert DISCARDED_SECRET in (run_dir / "run.json").read_text(encoding="utf-8")

    app = FakeApp(vault, embeddings=True)

    # The corpus cannot see it.
    corpus = app._recall_corpus()
    assert corpus, "kept notes should still be present"
    for item in corpus:
        assert DISCARDED_SECRET not in json.dumps(item)

    # Querying its own text finds nothing of it, on either retrieval path.
    for embeddings in (False, True):
        probe = FakeApp(vault, embeddings=embeddings)
        res = asyncio.run(probe.api_recall(
            FakeRequest({"query": DISCARDED_SECRET, "answer": True})))
        res.pop("query")  # the echo of what we asked is not a retained trace
        assert DISCARDED_SECRET not in json.dumps(res)
        assert res["hits"] == [] and res["answer"] == ""
        assert not any(DISCARDED_SECRET in t for t in probe.embedded_texts)
        assert not any(DISCARDED_SECRET in p for p in probe.think_prompts)


@pytest.mark.unit
def test_recall_never_touches_run_storage(tmp_path):
    """Structural half of the promise: unreachable by construction.

    ``FakeApp.runs`` raises, so any code path that reached for a run directory
    would blow up here rather than quietly widening the corpus later.
    """
    vault = _make_vault(tmp_path, {"kept1.md": KEPT_NOTE})
    _make_discarded_run(tmp_path)
    app = FakeApp(vault, embeddings=True)

    app._recall_corpus()
    asyncio.run(app._recall_search("anything at all", top_k=5))
    asyncio.run(app.api_recall(FakeRequest({"query": "anything", "answer": True})))
    asyncio.run(app.api_recall_status(FakeRequest({})))

    with pytest.raises(_NoRunsAccess):
        app.runs("runs")  # the guard itself is live, not a no-op


@pytest.mark.unit
def test_corpus_reads_only_the_kept_outputs_folder(tmp_path):
    """The retention boundary is one glob — pin it so a future edit can't
    quietly point it at run storage or the whole vault."""
    vault = _make_vault(tmp_path, {"kept1.md": KEPT_NOTE})
    seen: list[str] = []

    app = FakeApp(vault)
    real_list = app.vault_list

    def spy(pattern="*.md"):
        seen.append(pattern)
        return real_list(pattern)

    app.vault_list = spy
    app._recall_corpus()
    assert seen == ["outputs/*.md"]
    assert _MOD.KEPT_GLOB == "outputs/*.md"
