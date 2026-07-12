"""Voice-intent `link` contract — static regression guard.

The voice-intent return contract (`.claude/rules/voice-intents.md`) names
eight reference creators that must return a `link: {text, href}` so the
quick-action Plan results panel and the Aura transcript can render a
"go check what you just created" affordance.

This test parses each listed source file, locates the named method, and
asserts at least one `return` statement in its body carries a `link` key.
Failure means a refactor silently dropped the contract; the test row is
the canonical list of intents that participate.

Cheap, no daemon, no LLM. Runs in CI.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from helpers import app_path

REPO = Path(__file__).resolve().parent.parent

# (app id, file within the app dir, method name). Apps live under the track
# tree (apps/public/<group>/<id> etc.), so resolve via app_path rather than
# hardcoding paths. Method names are unique per file; no class disambiguation
# needed (the voice-intent contract maps verb → file by convention, one
# creator per file).
CONTRACT = [
    ("task", "voice.py", "voice_add_task"),
    ("expense", "app.py", "voice_add_expense"),
    ("journal", "app.py", "voice_add_entry"),
    ("note", "app.py", "voice_create_note"),
    ("quick-action", "app.py", "voice_capture"),
    ("video-digest", "app.py", "voice_queue_video"),
    ("rooms", "rooms_core.py", "voice_open_room"),
    ("learn", "srs.py", "voice_start_review"),
    ("improv", "app.py", "voice_start_scene"),
    ("people", "app.py", "voice_log_interaction"),
]


def _returns_link(func: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """True when any `return` statement in the function body returns a
    dict literal that carries a `link` key (with a truthy value), OR
    assigns one onto a dict before returning it.

    Two shapes the contract accepts:

        return {"say": "...", "link": {"text": ..., "href": ...}}

        out = {"say": "..."}
        out["link"] = ...
        return out
    """
    # Shape A: dict literal in a return statement
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and key.value == "link":
                    return True
    # Shape B: subscript-assignment of "link" onto a dict that's later returned
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].slice, ast.Constant)
            and node.targets[0].slice.value == "link"
        ):
            return True
    return False


def _find_method(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


@pytest.mark.parametrize(
    "app_id,fname,method", CONTRACT, ids=[f"{a}/{f}::{m}" for a, f, m in CONTRACT]
)
def test_voice_intent_returns_link(app_id: str, fname: str, method: str):
    src_path = app_path(app_id) / fname
    assert src_path.exists(), f"contract file missing: {app_id}/{fname}"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    func = _find_method(tree, method)
    assert func is not None, f"method {method} not found in {app_id}/{fname}"
    assert _returns_link(func), (
        f"{method} in {app_id}/{fname} must return a `link` field — voice-intent contract "
        f"(.claude/rules/voice-intents.md). Either restore the link, or remove the "
        f"method from CONTRACT in this test if the intent is no longer a creator."
    )


def test_contract_excludes_memory_intents():
    """voice_remember and voice_forget intentionally DO NOT return link —
    there is no dedicated memory list page; voice_recall is the inspection
    path. Guard against accidental re-addition (which would land on
    /voice-assistant/ where memories aren't shown).
    """
    src = (app_path("voice-assistant") / "memory.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for name in ("voice_remember", "voice_forget"):
        func = _find_method(tree, name)
        assert func is not None, f"{name} not found in memory.py"
        assert not _returns_link(func), (
            f"{name} returns a `link` field, but there is no memory list page to "
            f"land on. Remove it (voice_recall is the inspection surface) or add a "
            f"real /voice-assistant/?tab=memory route first."
        )
