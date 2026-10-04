"""ChatSessionStore artifacts — the session↔artifact link (B4).

The artifact itself lives in viz. This table exists for one reason: history
replay carries no tool ``display`` payload, so a chat's side panel can only
come back after a reload if the link was written down.
"""

from __future__ import annotations

import sqlite3

import pytest

from emptyos.sdk.chat_session import ChatSessionStore


def store(artifacts=True):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    s = ChatSessionStore(db, prefix="a_", artifacts=artifacts)
    s.init_schema()
    return s


def test_a_session_lists_only_its_own_artifacts():
    s = store()
    s.record_artifact("s1", "aaa", title="Budget", shape="chart")
    s.record_artifact("s2", "bbb", title="Map", shape="svg-diagram")
    assert [a["artifact_id"] for a in s.list_artifacts("s1")] == ["aaa"]
    assert s.list_artifacts("s1")[0]["title"] == "Budget"
    assert s.list_artifacts("nobody") == []


def test_revising_an_artifact_moves_the_row_rather_than_adding_one():
    s = store()
    s.record_artifact("s1", "aaa", title="Budget", shape="chart")
    s.record_artifact("s1", "aaa", title="Budget v2", shape="chart")
    rows = s.list_artifacts("s1")
    assert len(rows) == 1 and rows[0]["title"] == "Budget v2"


def test_the_newest_touch_comes_first():
    """The panel opens on the artifact the conversation is actually about —
    which is the one last revised, not the one first created."""
    s = store()
    s.record_artifact("s1", "old", title="First")
    s.record_artifact("s1", "new", title="Second")
    s.record_artifact("s1", "old", title="First again")   # revised → back on top
    assert [a["artifact_id"] for a in s.list_artifacts("s1")] == ["old", "new"]


def test_the_same_artifact_can_belong_to_two_chats():
    s = store()
    for sid in ("s1", "s2"):
        s.record_artifact(sid, "shared", title="Shared")
    assert len(s.list_artifacts("s1")) == 1 and len(s.list_artifacts("s2")) == 1


def test_deleting_a_chat_drops_its_links_and_nothing_else():
    s = store()
    s1 = s.create_session(name="a")["id"]
    s.record_artifact(s1, "aaa")
    s.record_artifact("s2", "bbb")
    s.delete_session(s1)
    assert s.list_artifacts(s1) == []
    assert [a["artifact_id"] for a in s.list_artifacts("s2")] == ["bbb"]
    # The row count, not just the query: an orphan would still be in the table.
    n = s.db.execute(f"SELECT COUNT(*) FROM {s.artifacts_table}").fetchone()[0]
    assert n == 1


def test_a_store_without_the_opt_in_has_no_table_and_says_so():
    s = store(artifacts=False)
    with pytest.raises(RuntimeError, match="artifacts=True"):
        s.record_artifact("s1", "aaa")
    with pytest.raises(RuntimeError, match="artifacts=True"):
        s.list_artifacts("s1")
    tables = {r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "a_artifacts" not in tables


def test_deleting_one_chat_leaves_the_other_chats_link_to_the_same_artifact():
    """The composite case the two tests above only half-cover: two chats
    referencing ONE artifact, one chat deleted. The surviving chat must keep
    its link — the delete is per-session, and an artifact outlives both."""
    s = store()
    a = s.create_session(name="a")["id"]
    b = s.create_session(name="b")["id"]
    s.record_artifact(a, "shared", title="Shared")
    s.record_artifact(b, "shared", title="Shared")
    s.delete_session(a)
    assert s.list_artifacts(a) == []
    assert [r["artifact_id"] for r in s.list_artifacts(b)] == ["shared"]
