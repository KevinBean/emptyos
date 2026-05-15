"""Tests for emptyos.sdk.do_token — shared [DO:] token grammar."""

from __future__ import annotations

from emptyos.sdk.do_token import DO_RE, extract_do_tokens, new_action_id


def test_new_action_id_shape():
    aid = new_action_id()
    assert aid.startswith("act-")
    assert len(aid) == 14  # "act-" + 10 hex


def test_new_action_id_unique():
    ids = {new_action_id() for _ in range(50)}
    assert len(ids) == 50


def test_extract_returns_empty_when_no_tokens():
    cleaned, pending = extract_do_tokens(
        "Just plain prose with nothing to apply.",
        source_actor={"type": "worker", "id": "w1"},
    )
    assert cleaned == "Just plain prose with nothing to apply."
    assert pending == []


def test_extract_handles_empty_input():
    cleaned, pending = extract_do_tokens("", source_actor={"type": "worker"})
    assert cleaned == ""
    assert pending == []
    cleaned, pending = extract_do_tokens(None, source_actor={"type": "worker"})  # type: ignore[arg-type]
    assert cleaned == ""
    assert pending == []


def test_extract_single_token():
    text = 'Adding a task. [DO:task.add({"text":"call mom"})]'
    cleaned, pending = extract_do_tokens(
        text, source_actor={"type": "worker", "id": "w1"},
    )
    assert cleaned == "Adding a task."
    assert len(pending) == 1
    p = pending[0]
    assert p["app"] == "task"
    assert p["method"] == "add"
    assert p["args"] == {"text": "call mom"}
    assert p["source_actor"] == {"type": "worker", "id": "w1"}
    assert p["status"] == "pending"
    assert p["id"].startswith("act-")
    assert "ts" in p


def test_extract_multi_token_preserves_order():
    text = (
        "First [DO:task.add({\"text\":\"A\"})] then "
        "[DO:projects.add_task_to_project({\"project_id\":\"p1\",\"text\":\"B\"})]."
    )
    cleaned, pending = extract_do_tokens(text, source_actor={"type": "agent"})
    assert "[DO:" not in cleaned
    assert len(pending) == 2
    assert pending[0]["method"] == "add"
    assert pending[1]["method"] == "add_task_to_project"


def test_extract_context_merged_into_each_action():
    text = '[DO:task.add({"text":"X"})] [DO:task.add({"text":"Y"})]'
    _, pending = extract_do_tokens(
        text,
        source_actor={"type": "cli", "id": "claude-cli"},
        context={"room_id": "r-42"},
    )
    assert len(pending) == 2
    for p in pending:
        assert p["room_id"] == "r-42"


def test_extract_run_id_context_for_company_shape():
    text = '[DO:kb.api_doc_create({"title":"x","paragraphs":[]})]'
    _, pending = extract_do_tokens(
        text,
        source_actor={"type": "worker", "worker_id": "marketer"},
        context={"run_id": "run-7"},
    )
    assert pending[0]["run_id"] == "run-7"
    assert "room_id" not in pending[0]


def test_extract_malformed_json_yields_empty_args():
    # Garbled JSON inside [DO:] — parser falls back to {}, not a crash.
    text = '[DO:task.add({not valid json})]'
    _, pending = extract_do_tokens(text, source_actor={"type": "agent"})
    assert len(pending) == 1
    assert pending[0]["args"] == {}


def test_extract_multiline_token():
    # DOTALL means the inner JSON can span lines.
    text = (
        '[DO:publish.api_save_draft({\n'
        '  "title": "Hello",\n'
        '  "content": "body"\n'
        '})]'
    )
    cleaned, pending = extract_do_tokens(text, source_actor={"type": "worker"})
    assert cleaned == ""
    assert len(pending) == 1
    assert pending[0]["args"] == {"title": "Hello", "content": "body"}


def test_extract_hyphen_app_name_allowed():
    # The regex permits hyphens in the app namespace ([\w-]+).
    text = '[DO:fix-agent.run({"id":"x"})]'
    _, pending = extract_do_tokens(text, source_actor={"type": "agent"})
    assert len(pending) == 1
    assert pending[0]["app"] == "fix-agent"


def test_extract_ignores_text_only_describing_action():
    # Per the contract: the model must emit the token. Prose without one
    # produces zero pending actions — the gate isn't tricked by description.
    text = 'I will add a task to remind you tomorrow.'
    _, pending = extract_do_tokens(text, source_actor={"type": "cli"})
    assert pending == []


def test_do_re_pattern_anchors():
    # Sanity: regex requires the [DO: prefix and trailing )].
    assert DO_RE.search('[DO:task.add({"text":"x"})]')
    assert not DO_RE.search('DO:task.add({"text":"x"})')   # no [
    assert not DO_RE.search('[DO:task.add({"text":"x"})')  # no ]
