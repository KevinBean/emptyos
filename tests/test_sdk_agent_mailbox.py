"""Agent mailbox — `emptyos/sdk/agent_mailbox.py`.

Pure per-recipient durable queue. No daemon — works over a tmp dir. Covers
enqueue→list round-trip, per-recipient isolation, mark_delivered filtering,
oldest-first order, malformed-file tolerance, and fail-soft on bad paths.

Run: python -m pytest tests/test_sdk_agent_mailbox.py -v
"""
from __future__ import annotations

import pytest

from emptyos.sdk.agent_mailbox import AgentMailbox, mailbox_message

pytestmark = pytest.mark.unit


def test_enqueue_then_list(tmp_path):
    mb = AgentMailbox(tmp_path)
    rec = mb.enqueue("agent-a", sender="rooms", kind="task", subject="finish the diagram",
                     payload={"task_id": "t1"})
    assert rec and rec["id"].startswith("msg-")
    pend = mb.list_pending("agent-a")
    assert len(pend) == 1
    assert pend[0]["subject"] == "finish the diagram"
    assert pend[0]["sender"] == "rooms"
    assert pend[0]["payload"]["task_id"] == "t1"
    assert pend[0]["status"] == "pending"


def test_recipients_are_isolated(tmp_path):
    mb = AgentMailbox(tmp_path)
    mb.enqueue("agent-a", subject="for A")
    mb.enqueue("agent-b", subject="for B")
    assert len(mb.list_pending("agent-a")) == 1
    assert mb.list_pending("agent-a")[0]["subject"] == "for A"
    assert len(mb.list_pending("agent-b")) == 1
    assert mb.list_pending("agent-c") == []
    assert sorted(mb.recipients()) == ["agent-a", "agent-b"]


def test_mark_delivered_drops_from_pending(tmp_path):
    mb = AgentMailbox(tmp_path)
    rec = mb.enqueue("agent-a", subject="do X")
    assert mb.count_pending("agent-a") == 1
    assert mb.mark_delivered("agent-a", rec["id"]) is True
    assert mb.count_pending("agent-a") == 0
    # still on disk for audit (list_all shows it), status flipped
    all_msgs = mb.list_all("agent-a")
    assert len(all_msgs) == 1
    assert all_msgs[0]["status"] == "delivered"
    assert all_msgs[0]["delivered_ts"]
    # idempotent — second ack is a no-op
    assert mb.mark_delivered("agent-a", rec["id"]) is False


def test_pending_is_oldest_first(tmp_path):
    mb = AgentMailbox(tmp_path)
    # write with explicit ts via mailbox_message + direct file to control order
    for ts, sub in [("2026-02-01T00:00:00+00:00", "second"), ("2026-01-01T00:00:00+00:00", "first")]:
        rec = mailbox_message(recipient="agent-a", subject=sub, ts=ts)
        d = mb._dir("agent-a"); d.mkdir(parents=True, exist_ok=True)
        import json
        (d / f"{rec['id']}.json").write_text(json.dumps(rec), encoding="utf-8")
    subs = [m["subject"] for m in mb.list_pending("agent-a")]
    assert subs == ["first", "second"]


def test_recipient_id_sanitized(tmp_path):
    mb = AgentMailbox(tmp_path)
    # a recipient id with path-unsafe chars still round-trips
    rec = mb.enqueue("room:abc/def", subject="hi")
    assert rec is not None
    assert len(mb.list_pending("room:abc/def")) == 1


def test_malformed_file_tolerated(tmp_path):
    mb = AgentMailbox(tmp_path)
    mb.enqueue("agent-a", subject="good")
    (mb._dir("agent-a") / "msg-bad.json").write_text("{not json", encoding="utf-8")
    pend = mb.list_pending("agent-a")
    assert len(pend) == 1 and pend[0]["subject"] == "good"


def test_missing_recipient_is_empty(tmp_path):
    mb = AgentMailbox(tmp_path)
    assert mb.list_pending("nobody") == []
    assert mb.count_pending("nobody") == 0
    assert mb.mark_delivered("nobody", "msg-x") is False
