"""Unit tests for apps/company/scenarios/base.py helpers.

Covers the persistence + attachment behavior `gate_responses` wraps around
the shared SDK token parser, plus prompt-allowlist rendering. Pure unit
tests — no daemon required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the company app importable without going through the kernel loader.
APP_DIR = Path(__file__).resolve().parents[1] / "apps" / "company"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from scenarios.base import (  # noqa: E402
    DELIVERABLE_VERBS,
    extract_do_actions,
    gate_responses,
    pending_dir,
    render_deliverable_allowlist,
    save_pending,
)


class _FakeApp:
    """Minimal stand-in for BaseApp — just exposes `data_dir`."""

    def __init__(self, root: Path):
        self.data_dir = root


# ── extract_do_actions (thin SDK wrapper) ──────────────────────────


def test_extract_do_actions_attaches_run_id():
    cleaned, pending = extract_do_actions(
        'Plan [DO:task.add({"text":"x"})]',
        run_id="run-abc",
        source={"type": "worker", "worker_id": "w1"},
    )
    assert cleaned == "Plan"
    assert len(pending) == 1
    assert pending[0]["run_id"] == "run-abc"
    assert pending[0]["source_actor"]["worker_id"] == "w1"


def test_extract_do_actions_empty_when_no_tokens():
    cleaned, pending = extract_do_actions(
        "Just prose.", run_id="run-1", source={"type": "worker"},
    )
    assert cleaned == "Just prose."
    assert pending == []


# ── gate_responses ─────────────────────────────────────────────────


def test_gate_responses_strips_tokens_and_attaches_pending_ids(tmp_path):
    app = _FakeApp(tmp_path)
    responses = [
        {
            "worker_id": "marketer",
            "name": "PMM",
            "role": "Product Marketing",
            "response": (
                'Positioning draft. '
                '[DO:kb.api_doc_create({"title":"pos","paragraphs":[]})] '
                'Also a task: [DO:task.add({"text":"refine ICP"})].'
            ),
        },
        {
            "worker_id": "designer",
            "name": "D",
            "role": "Designer",
            "response": "No deliverable here — just commentary.",
        },
    ]

    all_pending = gate_responses(app, responses, run_id="run-xyz")

    # Two tokens across responses, parsed into two actions.
    assert len(all_pending) == 2
    assert {p["app"] for p in all_pending} == {"kb", "task"}
    for p in all_pending:
        assert p["run_id"] == "run-xyz"
        assert p["status"] == "pending"
        # source_actor carries the persona context for review-gate cards.
        assert p["source_actor"]["type"] == "worker"
        assert p["source_actor"]["worker_id"] == "marketer"

    # First response is cleaned + carries both pending ids.
    assert "[DO:" not in responses[0]["response"]
    assert len(responses[0]["pending"]) == 2
    # Second response had no tokens — empty pending list, prose unchanged.
    assert responses[1]["pending"] == []
    assert responses[1]["response"] == "No deliverable here — just commentary."


def test_gate_responses_persists_each_action_to_disk(tmp_path):
    app = _FakeApp(tmp_path)
    responses = [
        {
            "worker_id": "w",
            "name": "n",
            "role": "r",
            "response": '[DO:task.add({"text":"persist me"})]',
        },
    ]
    pending = gate_responses(app, responses, run_id="run-disk")

    assert len(pending) == 1
    action_id = pending[0]["id"]
    on_disk = pending_dir(app) / f"{action_id}.json"
    assert on_disk.exists(), f"pending action not persisted: {on_disk}"

    stored = json.loads(on_disk.read_text(encoding="utf-8"))
    assert stored["id"] == action_id
    assert stored["app"] == "task"
    assert stored["method"] == "add"
    assert stored["args"] == {"text": "persist me"}
    assert stored["run_id"] == "run-disk"
    assert stored["status"] == "pending"


def test_gate_responses_empty_list_is_safe(tmp_path):
    app = _FakeApp(tmp_path)
    assert gate_responses(app, [], run_id="run-empty") == []


def test_save_pending_writes_one_file_per_action(tmp_path):
    app = _FakeApp(tmp_path)
    action = {
        "id": "act-deadbeef00",
        "run_id": "run-1",
        "app": "task",
        "method": "add",
        "args": {"text": "hi"},
        "status": "pending",
    }
    save_pending(app, action)
    target = pending_dir(app) / "act-deadbeef00.json"
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["id"] == "act-deadbeef00"


# ── render_deliverable_allowlist ───────────────────────────────────


def test_render_deliverable_allowlist_includes_every_verb():
    rendered = render_deliverable_allowlist()
    for verb, _args, _desc in DELIVERABLE_VERBS:
        assert f"[DO:{verb}(" in rendered, f"verb missing from allowlist: {verb}"


def test_render_deliverable_allowlist_starts_with_header():
    rendered = render_deliverable_allowlist()
    first = rendered.splitlines()[0]
    assert "Available" in first and "DO:" in first


def test_deliverable_verbs_includes_review_gated_publish_deploy():
    # publish.deploy is the canonical impact-shaped review-gate proposal
    # per .claude/rules/proposed-action.md. Removing it from the allowlist
    # is a meaningful behavior change — pin it via test.
    verbs = {v for v, _a, _d in DELIVERABLE_VERBS}
    assert "publish.deploy" in verbs
