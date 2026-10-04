"""Unit tests for the replay app's pure shared.py (loaded standalone, no daemon).

Covers the template resolver (typed whole-token vs inline substitution, recursion,
unresolved → raise vs safe placeholder), slugify, and the recipe-note /
SKILL.md renderers' round-trip against the frontmatter JSON contract.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parent.parent / "apps/public/standard/replay/shared.py"


@pytest.fixture(scope="module")
def shared():
    spec = importlib.util.spec_from_file_location("replay_shared", _SHARED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_slugify(shared):
    assert shared.slugify("Triage Inbox & File!") == "triage-inbox-file"
    assert shared.slugify("") == "recipe"


def test_resolve_whole_token_returns_typed_value(shared):
    inputs = {"n": 5}
    results = {"step-1": {"next": "do it", "count": 3}}
    # whole-token input → typed (int, not "5")
    assert shared.resolve_template("{input.n}", inputs, results) == 5
    # whole-token step field → typed
    assert shared.resolve_template("{step.1.output.count}", inputs, results) == 3
    # whole-token step output → the whole dict
    assert shared.resolve_template("{step.1.output}", inputs, results) == {"next": "do it", "count": 3}


def test_resolve_inline_substitution_stringifies(shared):
    out = shared.resolve_template("got {input.n} of {step.1.output.next}", {"n": 5}, {"step-1": {"next": "x"}})
    assert out == "got 5 of x"


def test_resolve_recurses_dict_and_list(shared):
    val = {"a": "{input.x}", "b": ["{input.x}", "lit"]}
    out = shared.resolve_template(val, {"x": "Z"}, {})
    assert out == {"a": "Z", "b": ["Z", "lit"]}


def test_resolve_unresolved_raises_then_safe_placeholder(shared):
    with pytest.raises(shared.TemplateError):
        shared.resolve_template("{input.missing}", {}, {})
    assert shared.safe_resolve("{input.missing}", {}, {}) == shared.UNRESOLVED_PLACEHOLDER
    assert shared.safe_resolve("x {step.9.output}", {}, {}) == f"x {shared.UNRESOLVED_PLACEHOLDER}"


def test_non_string_passthrough(shared):
    assert shared.resolve_template(7, {}, {}) == 7
    assert shared.resolve_template(True, {}, {}) is True


# ── recipe note round-trip ───────────────────────────────────────────────────


def test_recipe_note_roundtrip(shared):
    # Goal carries BOTH an apostrophe and a double quote — the exact shape that
    # corrupted under the old single-quote-JSON-in-frontmatter encoding. Round-
    # trips through the REAL reader (parse_frontmatter), as VaultLibrary.detail does.
    from emptyos.sdk.utils import parse_frontmatter, strip_frontmatter

    draft = {
        "name": "Triage inbox",
        "goal": "File the project's \"urgent\" items",
        "when_to_use": "When full",
        "inputs": [{"name": "focus_tag", "label": "Tag", "type": "string", "required": True, "default": ""}],
        "steps": [
            {"n": 1, "kind": "think", "prompt": "Classify {input.focus_tag}", "domain": "text", "why": "c", "expects_json": True},
            {"n": 2, "kind": "verb", "verb": "task.add", "args": {"text": "{step.1.output.next}"}, "why": "file"},
        ],
        "verify": ["Inbox empty"],
    }
    md = shared.render_recipe_note(draft, source_session="sid1", source_trace="trace-x", created="2026-06-19")
    assert md.startswith("---")
    assert "  - replay" in md  # block-style tags, unique (not "recipe" → no food-recipe collision)
    assert "```json" in md      # machine spec lives in the body fence, not frontmatter

    props = parse_frontmatter(md)
    detail = {**props, "body": strip_frontmatter(md)}
    recipe = shared.parse_recipe(detail)
    assert recipe["id"] == "triage-inbox"
    assert recipe["name"] == "Triage inbox"
    assert recipe["goal"] == "File the project's \"urgent\" items"  # apostrophe + quote survive
    assert recipe["status"] == "draft"
    assert recipe["source_session"] == "sid1"
    assert recipe["inputs"][0]["name"] == "focus_tag"
    assert recipe["steps"][1]["verb"] == "task.add"
    assert recipe["steps"][0]["expects_json"] is True
    assert recipe["verify"] == ["Inbox empty"]


def test_parse_recipe_tolerates_missing_or_bad_spec(shared):
    assert shared.parse_recipe({"name": "x", "body": "no fence here"})["steps"] == []
    assert shared.parse_recipe({"name": "x", "body": "```json\n{not json\n```"})["inputs"] == []
    assert shared.parse_recipe({"name": "x"})["verify"] == []


def test_render_skill_md(shared):
    draft = {
        "name": "Triage inbox",
        "goal": "File items",
        "when_to_use": "When full",
        "inputs": [{"name": "focus_tag", "label": "Tag", "required": True}],
        "steps": [{"n": 1, "kind": "verb", "verb": "task.add", "args": {"text": "x"}, "why": "file"}],
        "verify": ["empty"],
    }
    md = shared.render_skill_md(draft, source_session="sid1")
    assert md.startswith("---")
    assert "name: eos-triage-inbox" in md
    assert "description:" in md
    assert "task.add" in md
    assert "session `sid1`" in md
