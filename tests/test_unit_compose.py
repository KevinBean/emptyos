"""Unit tests for emptyos.sdk.compose — the shared message-draft engine.

Pure: no daemon, no kernel. `propose_message` takes an injected async
`think_fn`, so the whole pipeline (kind → channel → prompt → parse) is testable
with a stub. Mirrors tests/test_unit_html_element_edit.py.
"""

import asyncio

import pytest

from emptyos.sdk import compose as C


def test_playbook_has_both_groups():
    groups = {k.group for k in C.MESSAGE_KINDS.values()}
    assert groups == {"job", "relationship"}
    # A couple of load-bearing kinds exist.
    assert "cold-outreach" in C.MESSAGE_KINDS
    assert "reconnect" in C.MESSAGE_KINDS


def test_kinds_for_filters_by_group():
    job = C.kinds_for("job")
    rel = C.kinds_for("relationship")
    allk = C.kinds_for()
    assert {k["id"] for k in job}.isdisjoint({k["id"] for k in rel})
    assert len(allk) == len(job) + len(rel)
    assert all(k["group"] == "job" for k in job)


def test_build_system_encodes_channel_subject_rule():
    mk = C.MESSAGE_KINDS["cold-outreach"]
    email_sys = C.build_system(mk, "email", "warm")
    dm_sys = C.build_system(mk, "linkedin-dm", "warm")
    assert "Write a subject line." in email_sys
    assert "NO subject" in dm_sys
    # The play guidance + networking principles ride along.
    assert mk.guidance[:20] in email_sys
    assert "Reciprocity" in email_sys or "Give before you ask" in email_sys
    assert '"body"' in email_sys  # JSON contract present


def test_build_user_renders_context_and_drops_empties():
    ctx = {"Company": "Acme", "Role": "", "Recipient": "Jane"}
    user = C.build_user(ctx, freeform="keep it short")
    assert "Company: Acme" in user
    assert "Recipient: Jane" in user
    assert "Role:" not in user  # empty value dropped
    assert "keep it short" in user


def _run(coro):
    return asyncio.run(coro)


def test_propose_message_happy_path():
    async def fake_think(system, user):
        assert "Acme" in user
        return '{"subject": "Quick question", "body": "Hi Jane...", "why": "specific", "variant": ""}'

    res = _run(C.propose_message(
        "cold-outreach", "email", {"Company": "Acme", "Recipient": "Jane"},
        think_fn=fake_think,
    ))
    assert res["ok"] is True
    assert res["subject"] == "Quick question"
    assert res["body"].startswith("Hi Jane")
    assert res["channel"] == "email"


def test_propose_message_strips_subject_for_subjectless_channel():
    async def fake_think(system, user):
        return '{"subject": "should be dropped", "body": "hey", "why": "", "variant": ""}'

    res = _run(C.propose_message(
        "congratulations", "linkedin-dm", {"Recipient": "Sam"}, think_fn=fake_think,
    ))
    assert res["ok"] is True
    assert res["subject"] == ""   # linkedin-dm has no subject
    assert res["body"] == "hey"


def test_propose_message_unknown_kind():
    async def fake_think(system, user):
        raise AssertionError("should not be called")

    res = _run(C.propose_message("nope", "email", {}, think_fn=fake_think))
    assert res["ok"] is False
    assert "unknown message kind" in res["error"]


def test_propose_message_unknown_channel_falls_back_to_default():
    async def fake_think(system, user):
        return '{"subject": "", "body": "x", "why": "", "variant": ""}'

    res = _run(C.propose_message("reconnect", "carrier-pigeon", {}, think_fn=fake_think))
    assert res["ok"] is True
    # reconnect default_channel is email
    assert res["channel"] == C.MESSAGE_KINDS["reconnect"].default_channel


def test_propose_message_bad_json():
    async def fake_think(system, user):
        return "not json at all"

    res = _run(C.propose_message("cold-outreach", "email", {}, think_fn=fake_think))
    assert res["ok"] is False


def test_propose_message_think_exception_captured():
    async def fake_think(system, user):
        raise RuntimeError("provider down")

    res = _run(C.propose_message("cold-outreach", "email", {}, think_fn=fake_think))
    assert res["ok"] is False
    assert "provider down" in res["error"]


def test_propose_message_variants_requested():
    async def fake_think(system, user):
        assert "shorter alternative" in user
        return '{"subject": "S", "body": "long version", "why": "", "variant": "short version"}'

    res = _run(C.propose_message(
        "follow-up-nudge", "email", {}, think_fn=fake_think, variants=True,
    ))
    assert res["variant"] == "short version"
