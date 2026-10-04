"""ChatSessionStore projects + search (B2).

Search had to be an index, not a LIKE: ``content_json`` is ``json.dumps``
output, which escapes every CJK character to ``\\uXXXX`` — so a LIKE for
"会议" over the stored column can never match a chat that says it. The index
holds flattened user/assistant text (FTS5 trigram, case-insensitive), with a
substring scan for queries shorter than a trigram and a plain Python scan when
this SQLite has no FTS5.
"""

from __future__ import annotations

import sqlite3

import pytest

from emptyos.sdk import chat_session as cs
from emptyos.sdk.chat_session import ChatSessionStore, flatten_content, index_text

EXTRAS = {
    "provider": "TEXT NOT NULL DEFAULT ''",
    "profile": "TEXT NOT NULL DEFAULT ''",
    "project_id": "TEXT NOT NULL DEFAULT ''",
}
MSG_EXTRAS = {
    "provider_kind": "TEXT NOT NULL DEFAULT 'anthropic'",
    "display_text": "TEXT NOT NULL DEFAULT ''",
    "origin": "TEXT NOT NULL DEFAULT ''",
}


def store(**kw):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    s = ChatSessionStore(db, prefix="a_", session_extras=EXTRAS, message_extras=MSG_EXTRAS,
                         projects=True, search=True, **kw)
    s.init_schema()
    return s


def chat(s, name, *turns, **extras):
    sid = s.create_session(name=name, extras=extras)["id"]
    for role, content, *more in turns:
        s.append_message(sid, role, content, extras=(more[0] if more else None))
    return sid


def test_the_raw_column_cannot_find_cjk_which_is_why_there_is_an_index():
    s = store()
    sid = chat(s, "x", ("user", {"content": "我想查一下上周的会议记录"}))
    raw = s.db.execute("SELECT content_json FROM a_messages WHERE session_id = ?", (sid,)).fetchone()[0]
    assert "会议" not in raw  # escaped to \\uXXXX by json.dumps
    assert [h["session_id"] for h in s.search("会议记录")] == [sid]


def test_short_queries_fall_back_to_a_substring_scan():
    s = store()
    sid = chat(s, "x", ("assistant", {"content": "会议在周三"}))
    assert s.fts  # the index exists; a 2-char query just cannot use a trigram
    assert [h["session_id"] for h in s.search("会议")] == [sid]


def test_case_insensitive_and_one_row_per_session_newest_first():
    s = store()
    old = chat(s, "old", ("user", "Budget review"), ("assistant", "the BUDGET is fine"))
    new = chat(s, "new", ("user", "budget again"))
    hits = s.search("budget")
    assert [h["session_id"] for h in hits] == [new, old]
    assert hits[1]["hits"] == 2 and "BUDGET" in hits[1]["snippet"]


def test_tool_machinery_and_injected_context_are_not_indexed():
    s = store()
    sid = chat(
        s, "x",
        ("user", {"content": "[Orient]\nrules zzorient\n\nhello there"}, {"display_text": "hello there", "origin": "user"}),
        ("assistant", {"content": "", "tool_calls": [{"id": "c", "function": {"name": "Read", "arguments": "{\"path\":\"zztool\"}"}}]}),
        ("tool", {"content": "zzresult file listing", "tool_call_id": "c"}),
        ("user", {"content": "[Plan reminder — zznudge]"}, {"origin": "system"}),
    )
    assert s.search("hello there")[0]["session_id"] == sid
    for noise in ("zzorient", "zztool", "zzresult", "zznudge"):
        assert s.search(noise) == [], noise


def test_session_names_match_too_and_where_narrows():
    s = store()
    p = s.create_project("Thesis")
    inside = chat(s, "Chapter plan", ("user", "outline"), project_id=p["id"], profile="chat")
    # Matches by name AND by text, so `where` must narrow both paths.
    outside = chat(s, "Chapter notes elsewhere", ("user", "the chapter draft"))
    assert {h["session_id"] for h in s.search("chapter")} == {inside, outside}
    assert [h["session_id"] for h in s.search("chapter", where={"project_id": p["id"]})] == [inside]
    assert s.search("chapter", where={"project_id": "prj-none"}) == []


def test_a_busy_session_elsewhere_cannot_crowd_out_a_match():
    """Review B2 #1: search used to take the newest 500 matching MESSAGES and
    filter after, so 600 coding replies hid the one chat that said it."""
    s = store()
    chat_sid = chat(s, "c", ("user", "remember the zebra protocol"), profile="chat")
    busy = chat(s, "b", *[("assistant", f"zebra step {i}") for i in range(600)])
    assert [h["session_id"] for h in s.search("zebra", where={"profile": "chat"})] == [chat_sid]
    both = {h["session_id"]: h for h in s.search("zebra")}
    assert set(both) == {chat_sid, busy} and both[busy]["hits"] == 600   # counted, not capped
    assert "zebra step 599" in both[busy]["snippet"]                        # the newest match


def test_limit_counts_sessions_not_messages():
    s = store()
    sids = [chat(s, f"s{i}", ("user", "shared phrase here"), ("assistant", "shared phrase again")) for i in range(5)]
    assert len(s.search("shared phrase", limit=3)) == 3
    assert {h["session_id"] for h in s.search("shared phrase", limit=10)} == set(sids)


def test_whitespace_is_collapsed_on_both_sides():
    s = store()
    sid = chat(s, "x", ("user", "first line\nsecond   line"))
    assert [h["session_id"] for h in s.search("line second")] == [sid]


def test_short_queries_escape_like_wildcards():
    s = store()
    chat(s, "x", ("user", "plain words only"))
    assert s.search("%") == [] and s.search("_") == []   # literal, not "match anything"
    sid = chat(s, "y", ("user", "5% off"))
    assert [h["session_id"] for h in s.search("%")] == [sid]


def test_search_survives_like_metacharacters_and_quotes():
    s = store()
    sid = chat(s, "x", ("user", 'a 100% "quoted" o_k line'))
    assert [h["session_id"] for h in s.search('100% "quoted"')] == [sid]
    assert [h["session_id"] for h in s.search("o_k")] == [sid]
    assert s.search("100x") == []  # % is literal, not a wildcard
    # An unbalanced quote is text, not FTS5 syntax (unescaped, it is a parse error).
    q = chat(s, "q", ("user", 'she said "hi there'))
    assert [h["session_id"] for h in s.search('"hi')] == [q]
    # Session names go through LIKE: % there is literal too.
    sale = s.create_session(name="50% off")["id"]
    s.create_session(name="50 dollars")
    assert [h["session_id"] for h in s.search("50%")] == [sale]


def test_without_fts5_search_scans_and_still_finds_cjk(monkeypatch):
    real_execute = sqlite3.Connection.execute

    class NoFts(sqlite3.Connection):
        def execute(self, sql, *a):
            if "USING fts5" in sql:
                raise sqlite3.OperationalError("no such module: fts5")
            return real_execute(self, sql, *a)

    db = sqlite3.connect(":memory:", factory=NoFts)
    db.row_factory = sqlite3.Row
    s = ChatSessionStore(db, prefix="a_", session_extras=EXTRAS, message_extras=MSG_EXTRAS, search=True)
    s.init_schema()
    assert s.fts is False
    sid = chat(s, "x", ("user", {"content": "我想查一下上周的会议记录"}))
    assert [h["session_id"] for h in s.search("会议")] == [sid]
    assert s.search("会议", where={"profile": "chat"}) == []   # the scan honours `where` too
    # The scan applies the same what-counts rule as the index.
    chat(s, "y", ("user", {"content": "[Orient] zzorient\n\nhello"}, {"display_text": "hello", "origin": "user"}))
    assert s.search("zzorient") == []


def test_existing_messages_are_indexed_when_search_is_turned_on():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    plain = ChatSessionStore(db, prefix="a_", session_extras=EXTRAS, message_extras=MSG_EXTRAS)
    plain.init_schema()
    sid = chat(plain, "x", ("user", "before the index existed"))
    later = ChatSessionStore(db, prefix="a_", session_extras=EXTRAS, message_extras=MSG_EXTRAS, search=True)
    later.init_schema()
    assert [h["session_id"] for h in later.search("index existed")] == [sid]


def test_delete_session_removes_its_messages_and_index_rows():
    s = store()
    sid = chat(s, "x", ("user", "remove me please"))
    s.delete_session(sid)
    assert s.search("remove me") == []
    assert s.db.execute("SELECT COUNT(*) FROM a_messages").fetchone()[0] == 0
    # search() already hides rows of a missing session; the rows themselves go too.
    assert s.db.execute("SELECT COUNT(*) FROM a_search").fetchone()[0] == 0


def test_project_crud_and_detaching_on_delete():
    s = store()
    p = s.create_project("Thesis", "Cite sources.")
    assert p["name"] == "Thesis" and p["instructions"] == "Cite sources."
    sid = chat(s, "c", ("user", "hi"), project_id=p["id"])
    (row,) = s.list_projects()
    assert row["session_count"] == 1 and row["last_active"]
    s.update_project(p["id"], instructions="Be brief.", bogus="ignored")
    assert s.get_project(p["id"])["instructions"] == "Be brief."
    s.delete_project(p["id"])
    assert s.get_project(p["id"]) is None and s.list_projects() == []
    assert s.get_session(sid)["project_id"] == ""


def test_projects_are_opt_in():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    s = ChatSessionStore(db, prefix="a_", session_extras=EXTRAS)
    s.init_schema()
    with pytest.raises(RuntimeError):
        s.create_project("x")


def test_flatten_and_index_text_cover_every_encoding():
    assert flatten_content("plain") == "plain"
    assert flatten_content([{"type": "text", "text": "a"}, {"type": "tool_use"}, {"type": "text", "text": "b"}]) == "a b"
    assert flatten_content({"content": [{"type": "text", "text": "x"}]}) == "x"
    assert flatten_content(None) == ""
    assert index_text("tool", "anything") == ""
    assert index_text("user", "raw", {"display_text": "typed"}) == "typed"
    assert index_text("user", "nudge", {"origin": "system"}) == ""


def test_a_version_bump_rebuilds_the_index(monkeypatch):
    s = store()
    sid = chat(s, "x", ("user", "reindex target"))
    s.db.execute("DELETE FROM a_search")
    s.db.commit()
    assert s.search("reindex target") == []
    monkeypatch.setattr(cs, "SEARCH_INDEX_VERSION", cs.SEARCH_INDEX_VERSION + "-next")
    s.init_schema()
    assert [h["session_id"] for h in s.search("reindex target")] == [sid]
