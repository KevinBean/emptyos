"""Unit tests for emptyos/sdk/recipe_distill.py — pure distill validator.

parse_recipe_draft turns an LLM distill reply into a replayable recipe draft:
pins verbs to the live registry (known_verbs), restricts template refs to declared
inputs + earlier steps, drops + renumbers cleanly, never raises.
"""

from __future__ import annotations

import json

from emptyos.sdk.recipe_distill import iter_template_refs, parse_recipe_draft

KNOWN = {"task.add", "journal.add_entry", "kb.tag"}


def test_happy_path_recipe():
    raw = {
        "name": "Triage inbox",
        "goal": "File inbox items as tasks",
        "when_to_use": "When the inbox is full",
        "inputs": [{"name": "Focus Tag", "label": "Tag", "type": "string", "required": False}],
        "steps": [
            {"kind": "think", "prompt": "Classify {input.focus_tag}", "domain": "text", "expects_json": True, "why": "classify"},
            {"kind": "verb", "verb": "task.add", "args": {"text": "{step.1.output.next}"}, "why": "file it"},
        ],
        "verify": ["Inbox is empty", ""],
    }
    draft, warns = parse_recipe_draft(raw, known_verbs=KNOWN)
    assert draft is not None and warns == []
    assert draft["name"] == "Triage inbox"
    assert draft["inputs"][0]["name"] == "focus_tag"  # slugified
    assert [s["n"] for s in draft["steps"]] == [1, 2]
    assert draft["steps"][1]["args"]["text"] == "{step.1.output.next}"
    assert draft["verify"] == ["Inbox is empty"]


def test_unknown_and_malformed_verbs_dropped():
    raw = {
        "name": "x",
        "steps": [
            {"kind": "verb", "verb": "made.up", "args": {}},
            {"kind": "verb", "verb": "NOT A VERB", "args": {}},
            {"kind": "verb", "verb": "task.add", "args": {"text": "hi"}},
        ],
    }
    draft, warns = parse_recipe_draft(raw, known_verbs=KNOWN)
    assert draft is not None
    assert [s["verb"] for s in draft["steps"]] == ["task.add"]
    assert len(warns) == 2


def test_template_ref_to_undeclared_input_dropped():
    raw = {"name": "x", "steps": [{"kind": "verb", "verb": "task.add", "args": {"text": "{input.nope}"}}]}
    draft, warns = parse_recipe_draft(raw, known_verbs=KNOWN)
    assert draft is None
    assert any("undeclared input" in w for w in warns)


def test_ref_to_later_or_missing_step_dropped():
    raw = {
        "name": "x",
        "steps": [
            {"kind": "verb", "verb": "task.add", "args": {"text": "{step.2.output}"}},  # refs a later step
            {"kind": "think", "prompt": "later", "domain": "text"},
        ],
    }
    draft, warns = parse_recipe_draft(raw, known_verbs=KNOWN)
    # step 1 dropped (refs step 2), step 2 (think) survives and renumbers to 1
    assert draft is not None
    assert len(draft["steps"]) == 1 and draft["steps"][0]["kind"] == "think" and draft["steps"][0]["n"] == 1
    assert any("missing/later step" in w for w in warns)


def test_drop_renumbers_and_remaps_refs():
    raw = {
        "name": "x",
        "inputs": [],
        "steps": [
            {"kind": "think", "prompt": "a", "domain": "text"},          # oi1 -> n1
            {"kind": "verb", "verb": "made.up", "args": {}},             # oi2 dropped
            {"kind": "think", "prompt": "uses {step.1.output}", "domain": "text"},  # oi3 -> n2, ref stays 1
            {"kind": "verb", "verb": "task.add", "args": {"text": "{step.3.output}"}},  # oi4 -> n3, remap 3->2
        ],
    }
    draft, warns = parse_recipe_draft(raw, known_verbs=KNOWN)
    assert draft is not None
    ns = [s["n"] for s in draft["steps"]]
    assert ns == [1, 2, 3]
    assert draft["steps"][1]["prompt"] == "uses {step.1.output}"
    assert draft["steps"][2]["args"]["text"] == "{step.2.output}"  # remapped from oi3->n2


def test_think_missing_prompt_dropped():
    raw = {"name": "x", "steps": [{"kind": "think", "domain": "text"}, {"kind": "verb", "verb": "task.add"}]}
    draft, warns = parse_recipe_draft(raw, known_verbs=KNOWN)
    assert draft is not None and [s["kind"] for s in draft["steps"]] == ["verb"]


def test_no_name_or_no_steps_returns_none():
    assert parse_recipe_draft({"steps": []}, known_verbs=KNOWN)[0] is None
    assert parse_recipe_draft({"name": "x", "steps": []}, known_verbs=KNOWN)[0] is None
    assert parse_recipe_draft("not json at all", known_verbs=KNOWN)[0] is None


def test_accepts_raw_json_string():
    raw = json.dumps({"name": "x", "steps": [{"kind": "verb", "verb": "task.add", "args": {}}]})
    draft, _ = parse_recipe_draft(raw, known_verbs=KNOWN)
    assert draft is not None and draft["steps"][0]["verb"] == "task.add"


def test_known_verbs_none_skips_registry_check():
    raw = {"name": "x", "steps": [{"kind": "verb", "verb": "anything.goes", "args": {}}]}
    draft, _ = parse_recipe_draft(raw, known_verbs=None)
    assert draft is not None and draft["steps"][0]["verb"] == "anything.goes"


def test_iter_template_refs():
    ins, steps = iter_template_refs("a {input.x} b {step.2.output.field} {step.5.output}")
    assert ins == {"x"} and steps == {2, 5}
    assert iter_template_refs("no tokens") == (set(), set())
