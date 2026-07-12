"""Unit tests for ChatSessionStore.fork_session (P1.2).

Fork copies a session's history (and its extras — provider/provider_kind, so
the fork resumes identically) into a new session id. Pure sqlite, no daemon.
"""

from __future__ import annotations

import sqlite3

import pytest

from emptyos.sdk.chat_session import ChatSessionStore


def _store() -> ChatSessionStore:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    s = ChatSessionStore(
        db,
        prefix="agent_",
        session_extras={
            "provider": "TEXT NOT NULL DEFAULT ''",
            "model": "TEXT NOT NULL DEFAULT ''",
        },
        message_extras={"provider_kind": "TEXT NOT NULL DEFAULT 'anthropic'"},
    )
    s.init_schema()
    return s


def test_fork_copies_history_and_extras():
    s = _store()
    src = s.create_session(
        name="orig",
        extras={"provider": "openai", "model": "gpt-5.4-mini"},
    )
    s.append_message(src["id"], "user", "hello", extras={"provider_kind": "openai"})
    s.append_message(src["id"], "assistant", "hi", extras={"provider_kind": "openai"})

    fork = s.fork_session(src["id"], name="myfork")

    assert fork is not None
    assert fork["id"] != src["id"]
    assert fork["name"] == "myfork"
    assert fork["provider"] == "openai"  # session extra preserved → resumes identically
    assert fork["model"] == "gpt-5.4-mini"
    assert [m["content"] for m in fork["messages"]] == ["hello", "hi"]


def test_fork_truncates_at_message():
    s = _store()
    src = s.create_session(name="orig")
    for i in range(4):
        s.append_message(src["id"], "user", f"m{i}")

    fork = s.fork_session(src["id"], at_message=1)  # keep indices 0..1

    assert len(fork["messages"]) == 2
    assert fork["messages"][-1]["content"] == "m1"


def test_fork_default_name():
    s = _store()
    src = s.create_session(name="orig")
    fork = s.fork_session(src["id"])
    assert "Fork of orig" in fork["name"]


def test_fork_missing_source_returns_none():
    s = _store()
    assert s.fork_session("does-not-exist") is None


def test_fork_is_independent_copy():
    """Editing the fork must not touch the source (separate session ids)."""
    s = _store()
    src = s.create_session(name="orig")
    s.append_message(src["id"], "user", "a")
    fork = s.fork_session(src["id"])

    s.append_message(fork["id"], "user", "b")  # only in the fork

    assert len(s.get_session(src["id"])["messages"]) == 1
    assert len(s.get_session(fork["id"])["messages"]) == 2


def test_init_schema_backfills_new_session_extra_columns():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row

    legacy = ChatSessionStore(
        db,
        prefix="agent_",
        session_extras={"provider": "TEXT NOT NULL DEFAULT ''"},
        message_extras={"provider_kind": "TEXT NOT NULL DEFAULT 'anthropic'"},
    )
    legacy.init_schema()

    upgraded = ChatSessionStore(
        db,
        prefix="agent_",
        session_extras={
            "provider": "TEXT NOT NULL DEFAULT ''",
            "model": "TEXT NOT NULL DEFAULT ''",
        },
        message_extras={"provider_kind": "TEXT NOT NULL DEFAULT 'anthropic'"},
    )
    upgraded.init_schema()
    sess = upgraded.create_session(name="orig", extras={"provider": "openai"})
    upgraded.update_session(sess["id"], model="gpt-5.4-mini")

    assert upgraded.get_session(sess["id"])["model"] == "gpt-5.4-mini"
