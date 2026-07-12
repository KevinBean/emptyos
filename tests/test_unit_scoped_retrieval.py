"""Unit tests for emptyos.sdk.scoped_retrieval — pure scope/menu/gate logic.

No daemon, no kernel — the whole point of the pure-core split. Covers scope
routing (project/tag/alias/CJK/generic), menu round-trip, and the escalation gate.
"""

from __future__ import annotations

from emptyos.sdk.scoped_retrieval import (
    Scope,
    ScopedResult,
    parse_scope_key,
    scope_menu,
    select_scopes_deterministic as pick,
    should_escalate,
)

PROFILE = {
    "projects": ["pls-cadd-mastery", "Career Strategy"],
    "areas": ["Health", "Finances"],
    "resources": ["People"],
    "tags": [["kb", 50], ["song", 10], ["楞严咒", 3]],
}


def _labels(scopes):
    return [s.label for s in scopes]


# ── scope routing ──────────────────────────────────────────────────────

def test_project_name_token_matches_folder_scope():
    scopes = pick("how is pls-cadd mastery going", PROFILE)
    assert any(s.folder == "10_Projects/pls-cadd-mastery" for s in scopes)


def test_project_slug_part_matches():
    # a single significant slug token ("career") should still hit the project
    scopes = pick("any career updates this week", PROFILE)
    assert any(s.folder == "10_Projects/Career Strategy" for s in scopes)


def test_tag_token_matches_even_when_short():
    # 'kb' is 2 chars — exact match against the known tag/alias set still fires
    scopes = pick("what kb notes cover cable rating", PROFILE)
    assert any(s.tags == ["kb"] for s in scopes)


def test_alias_word_matches_folder():
    scopes = pick("what did my journal say yesterday", PROFILE)
    assert any(s.folder == "50_Journal" for s in scopes)


def test_cjk_tag_matches_by_raw_containment():
    scopes = pick("楞严咒 怎么念", PROFILE)
    assert any(s.tags == ["楞严咒"] for s in scopes)


def test_generic_query_yields_no_scope():
    assert pick("hello how are you today", PROFILE) == []
    assert pick("can you help me", PROFILE) == []


def test_dedup_and_cap():
    # 'kb' alias and the 'kb' profile tag both produce Scope(tags=['kb']) → one
    scopes = pick("kb knowledge notes", PROFILE)
    keys = [s.key() for s in scopes]
    assert len(keys) == len(set(keys))


def test_max_scopes_respected():
    prof = {"tags": [[f"t{i}", 5] for i in range(10)]}
    q = " ".join(f"t{i}" for i in range(10))
    assert len(pick(q, prof, max_scopes=2)) == 2


def test_custom_key_to_folder():
    prof = {"docs": ["spec-a"], "tags": []}
    scopes = pick("open spec-a", prof, key_to_folder={"docs": "99_Docs"})
    assert any(s.folder == "99_Docs/spec-a" for s in scopes)


# ── scope menu / parse round-trip ──────────────────────────────────────

def test_scope_menu_has_all_sentinel_and_tags():
    menu = scope_menu(PROFILE)
    assert "__all__" in menu
    assert "tag:kb" in menu
    assert any(k.startswith("folder:10_Projects/") for k in menu)


def test_parse_scope_key_roundtrip():
    assert parse_scope_key("__all__") is None
    assert parse_scope_key("") is None
    assert parse_scope_key("garbage") is None
    s = parse_scope_key("tag:kb")
    assert s and s.tags == ["kb"]
    f = parse_scope_key("folder:10_Projects/Career Strategy")
    assert f and f.folder == "10_Projects/Career Strategy"


# ── escalation gate ────────────────────────────────────────────────────

def test_escalate_on_empty_hits():
    assert should_escalate([]) is True


def test_escalate_when_top_below_bar():
    assert should_escalate([("x", 0.30)], min_top_score=0.45) is True


def test_no_escalate_when_top_meets_bar():
    assert should_escalate([("x", 0.60)], min_top_score=0.45) is False


def test_min_hits_boundary():
    assert should_escalate([("x", 0.9)], min_hits=2) is True
    assert should_escalate([("x", 0.9), ("y", 0.8)], min_hits=2) is False


# ── ScopedResult.usable ────────────────────────────────────────────────

def test_usable_only_when_scoped_with_snippets():
    assert ScopedResult(snippets=[{"x": 1}], tier="scoped").usable is True
    assert ScopedResult(snippets=[], tier="scoped").usable is False
    assert ScopedResult(snippets=[{"x": 1}], tier="no-scope").usable is False


def test_scope_key_identity():
    assert Scope(folder="a", tags=["t"]).key() == ("a", ("t",))
    assert Scope().key() == (None, ())
