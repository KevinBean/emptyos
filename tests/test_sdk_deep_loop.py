"""Pure unit tests for emptyos.sdk.deep_loop — the gap-driven deepening loop.

No daemon, no kernel: deepen() takes plain async callables, so the whole
control flow is exercised by injecting fakes. Mirrors explore's orchestration
tests (tests/test_explore_app.py) at the SDK level — explore + kb-gap-miner both
ride this loop, so the contract lives here.
"""

import asyncio

from emptyos.sdk.deep_loop import DeepLoopResult, dedupe_sources, deepen


# ── dedupe_sources (pure) ───────────────────────────────────────────────────

def test_dedupe_unions_by_url_first_wins_and_caps():
    rounds = [
        {"sources": [{"url": "https://a/1", "title": "A"}, {"url": "https://b/2", "title": "B"}]},
        {"sources": [{"url": "https://a/1", "title": "A-dup"}, {"url": "https://c/3", "title": "C"}]},
    ]
    out = dedupe_sources(rounds, limit=10)
    assert [s["url"] for s in out] == ["https://a/1", "https://b/2", "https://c/3"]
    assert out[0]["title"] == "A"  # first occurrence wins
    assert dedupe_sources(rounds, limit=2) == out[:2]  # cap honoured


def test_dedupe_falls_back_to_title_key_and_handles_empties():
    rounds = [
        {"sources": [{"title": "T1"}, {"title": "T1"}]},  # no url → key on title
        {"sources": []},
        {},  # missing "sources"
        "not a dict",
    ]
    out = dedupe_sources(rounds)
    assert len(out) == 1 and out[0]["title"] == "T1"
    assert dedupe_sources([]) == []


# ── deepen orchestration ────────────────────────────────────────────────────

def _fakes(rounds_by_query, followups_by_prior):
    """round_fn/followups_fn/merge_fn closures + a calls ledger."""
    calls = {"rounds": [], "followups": 0, "merged": []}

    async def round_fn(q):
        calls["rounds"].append(q)
        return rounds_by_query[q]

    async def followups_fn(query, prior):
        idx = calls["followups"]
        calls["followups"] += 1
        return followups_by_prior[idx] if idx < len(followups_by_prior) else []

    async def merge_fn(query, union):
        calls["merged"].append([s.get("url") for s in union])
        return "MERGED ANSWER"

    return round_fn, followups_fn, merge_fn, calls


def test_single_pass_when_depth_zero():
    rounds = {"q": {"ok": True, "query": "q", "answer": "A1",
                    "sources": [{"url": "https://a/1"}], "skipped": ["s"]}}
    round_fn, followups_fn, merge_fn, calls = _fakes(rounds, [])
    out = asyncio.run(deepen("q", round_fn=round_fn, followups_fn=followups_fn,
                             merge_fn=merge_fn, depth=0, breadth=3))
    assert isinstance(out, DeepLoopResult) and out.ok
    assert len(out.rounds) == 1 and out.answer == "A1"
    assert out.followups == []
    assert calls["followups"] == 0 and calls["merged"] == []  # loop + merge never run
    assert out.first["skipped"] == ["s"]                       # first passed through


def test_deep_loop_fans_out_dedupes_and_merges():
    rounds = {
        "q":  {"ok": True, "query": "q",  "answer": "A1", "sources": [{"url": "https://a/1"}]},
        "f1": {"ok": True, "query": "f1", "answer": "AF1",
               "sources": [{"url": "https://a/1"}, {"url": "https://b/2"}]},  # a/1 dup of round 1
        "f2": {"ok": True, "query": "f2", "answer": "AF2", "sources": [{"url": "https://c/3"}]},
    }
    # follow-ups include the original query echoed back ("q") → must be filtered
    round_fn, followups_fn, merge_fn, calls = _fakes(rounds, [["q", "f1", "f2"]])
    out = asyncio.run(deepen("q", round_fn=round_fn, followups_fn=followups_fn,
                             merge_fn=merge_fn, depth=1, breadth=3))
    assert out.ok and len(out.rounds) == 3        # first + f1 + f2 (q filtered)
    assert "q" not in calls["rounds"][1:]          # echoed original not re-run
    assert out.answer == "MERGED ANSWER"
    assert calls["merged"] == [["https://a/1", "https://b/2", "https://c/3"]]  # deduped union
    assert out.followups == [                       # research path with per-question source counts
        {"question": "f1", "sources": 2},
        {"question": "f2", "sources": 1},
    ]


def test_deep_loop_respects_breadth_cap():
    rounds = {q: {"ok": True, "query": q, "answer": q.upper(), "sources": [{"url": f"https://{q}"}]}
              for q in ("q", "f1", "f2", "f3", "f4")}
    round_fn, followups_fn, merge_fn, calls = _fakes(rounds, [["f1", "f2", "f3", "f4"]])
    out = asyncio.run(deepen("q", round_fn=round_fn, followups_fn=followups_fn,
                             merge_fn=merge_fn, depth=1, breadth=2))
    assert len(out.rounds) == 3                     # first + only 2 follow-ups
    assert calls["rounds"] == ["q", "f1", "f2"]


def test_failed_followup_round_drops_out_but_run_survives():
    rounds = {
        "q":  {"ok": True, "query": "q",  "answer": "A1", "sources": [{"url": "https://a/1"}]},
        "f1": {"ok": False, "error": "no readable text", "query": "f1", "sources": []},
        "f2": {"ok": True, "query": "f2", "answer": "AF2", "sources": [{"url": "https://c/3"}]},
    }
    round_fn, followups_fn, merge_fn, calls = _fakes(rounds, [["f1", "f2"]])
    out = asyncio.run(deepen("q", round_fn=round_fn, followups_fn=followups_fn,
                             merge_fn=merge_fn, depth=1, breadth=3))
    assert out.ok and len(out.rounds) == 2          # first + f2 only (f1 dropped)
    # both were still asked → both in the research path; f1 contributed 0 sources
    assert out.followups == [{"question": "f1", "sources": 0}, {"question": "f2", "sources": 1}]


def test_first_round_failure_short_circuits():
    rounds = {"q": {"ok": False, "error": "No web results found.", "query": "q", "sources": []}}
    round_fn, followups_fn, merge_fn, calls = _fakes(rounds, [["f1"]])
    out = asyncio.run(deepen("q", round_fn=round_fn, followups_fn=followups_fn,
                             merge_fn=merge_fn, depth=2, breadth=3))
    assert out.ok is False
    assert out.first["error"] == "No web results found."
    assert calls["followups"] == 0                  # never looped past a failed first round


def test_merge_failure_falls_back_to_first_answer():
    rounds = {
        "q":  {"ok": True, "query": "q",  "answer": "A1", "sources": [{"url": "https://a/1"}]},
        "f1": {"ok": True, "query": "f1", "answer": "AF1", "sources": [{"url": "https://b/2"}]},
    }
    round_fn, followups_fn, _merge, calls = _fakes(rounds, [["f1"]])

    async def boom(query, union):
        raise RuntimeError("merge blew up")

    out = asyncio.run(deepen("q", round_fn=round_fn, followups_fn=followups_fn,
                             merge_fn=boom, depth=1, breadth=3))
    assert out.ok and len(out.rounds) == 2
    assert out.answer == "A1"                        # fell back, run not lost
    assert out.sources == [{"url": "https://a/1"}]
