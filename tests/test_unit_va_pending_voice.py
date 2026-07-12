"""Unit tests — voice-assistant pending.py voice-confirm path.

Loads pending.py standalone (its only runtime imports are emptyos.sdk) and
drives the module-level functions against a stub app. Covers the gap-1 fix:
"Want me to apply that?" must be resolvable by a follow-up voice yes/no —
find_pending_duplicate (no duplicate files on a repeated ask),
_resolve_last_pending (stash + newest fallback), voice_confirm_pending,
voice_reject_pending.
"""

from __future__ import annotations

import asyncio
import importlib.util
import types
from pathlib import Path

import pytest

_PENDING = Path(__file__).parent.parent / "apps/public/standard/voice-assistant/pending.py"


@pytest.fixture(scope="module")
def pending_mod():
    spec = importlib.util.spec_from_file_location("va_pending_under_test", _PENDING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StubVA:
    """Just enough BaseApp surface for pending.py's module functions."""

    def __init__(self, tmp: Path):
        self.data_dir = tmp
        self.kernel = types.SimpleNamespace()   # lookup_inverse fails soft on this
        self._active_companion = ""
        self._last_pending: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.events: list[tuple] = []

    def data_subdir(self, name: str) -> Path:
        p = self.data_dir / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def call_app(self, app, method, **kwargs):
        self.calls.append((app, method, kwargs))
        return {"say": f"Added: {kwargs.get('text', 'thing')}"}

    async def emit(self, *a, **k):
        self.events.append((a, k))


def _save(pm, stub, **overrides):
    kw = dict(verb="recipes.add", app="recipes", method="add_recipe",
              args={"name": "泡菜豆腐汤"}, description="Add a recipe")
    kw.update(overrides)
    return asyncio.run(pm.save_pending_action(stub, **kw))


def test_find_pending_duplicate_matches_same_verb_args(pending_mod, tmp_path):
    app = StubVA(tmp_path)
    saved = _save(pending_mod, app)
    dup = pending_mod.find_pending_duplicate(app, "recipes.add", {"name": "泡菜豆腐汤"})
    assert dup and dup["id"] == saved["id"]
    # Different args → no match
    assert pending_mod.find_pending_duplicate(app, "recipes.add", {"name": "other"}) is None
    # Different verb → no match
    assert pending_mod.find_pending_duplicate(app, "note.create", {"name": "泡菜豆腐汤"}) is None


def test_resolve_last_pending_prefers_stash_then_newest(pending_mod, tmp_path):
    app = StubVA(tmp_path)
    a1 = _save(pending_mod, app)
    a2 = _save(pending_mod, app, args={"name": "second"})
    # No stash → newest pending
    assert pending_mod._resolve_last_pending(app) == a2["id"]
    # Stash wins when it points at a still-pending action
    app._last_pending[""] = a1["id"]
    assert pending_mod._resolve_last_pending(app) == a1["id"]
    # Stale stash (resolved action) falls back to newest
    asyncio.run(pending_mod.reject_pending(app, a1["id"]))
    assert pending_mod._resolve_last_pending(app) == a2["id"]


def test_voice_confirm_applies_and_speaks_result(pending_mod, tmp_path):
    app = StubVA(tmp_path)
    saved = _save(pending_mod, app, verb="task.add", app="task", method="add",
                  args={"text": "buy milk"})
    app._last_pending[""] = saved["id"]
    out = asyncio.run(pending_mod.voice_confirm_pending(app))
    assert app.calls == [("task", "add", {"text": "buy milk"})]
    assert "Added: buy milk" in out["say"]
    # Second confirm: nothing pending anymore
    out2 = asyncio.run(pending_mod.voice_confirm_pending(app))
    assert out2["say"] == "Nothing pending to confirm."


def test_voice_reject_drops_without_executing(pending_mod, tmp_path):
    app = StubVA(tmp_path)
    saved = _save(pending_mod, app)
    out = asyncio.run(pending_mod.voice_reject_pending(app))
    assert out["say"] == "Okay, dropped it."
    assert app.calls == []
    assert pending_mod._load_pending(app, saved["id"])["status"] == "rejected"


def test_voice_confirm_nothing_pending(pending_mod, tmp_path):
    app = StubVA(tmp_path)
    out = asyncio.run(pending_mod.voice_confirm_pending(app))
    assert out["say"] == "Nothing pending to confirm."
