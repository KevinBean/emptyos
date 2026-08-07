"""Unit tests for `scripts/check_bare_create_task.py`.

Pins both directions per `.claude/rules/audits.md`: it must flag a genuinely
unreferenced detached task, and must stay silent on the shapes that are fine.

The `SubAgentTool` case is the reason the class check exists. A first cut of
this scanner tested only "is the first parameter named `self`", which flagged
`SubAgentTool.run` — a plain `Tool` subclass — and the mechanical sweep then
rewrote it to `self.spawn_background(...)` on a class with no such method. It
would have raised AttributeError the first time a background subagent ran.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import textwrap

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_bare_create_task", _REPO / "scripts" / "check_bare_create_task.py"
    )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        pytest.skip(f"scanner not importable: {e}")
    return mod


_m = _load()


def _scan(src: str):
    d = pathlib.Path(tempfile.mkdtemp())
    p = d / "probe.py"
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    _m.REPO_ROOT = d
    return _m.scan_file(p)


# ── flagged: the hazard ──────────────────────────────────────────────────────

def test_flags_a_bare_task_in_an_app_method():
    res = _scan("""
        import asyncio
        class MyApp(BaseApp):
            async def setup(self):
                asyncio.create_task(self._warm())
    """)
    assert len(res) == 1 and res[0]["holder"] == "self"


def test_flags_a_bound_helper_module_function():
    """The multi-module pattern: a module-level `def f(self, ...)` re-bound onto
    an app class. 63 of the sites in the real sweep had this shape."""
    res = _scan("""
        import asyncio
        def helper(self, x):
            asyncio.create_task(self._thing())
    """)
    assert len(res) == 1 and res[0]["holder"] == "self"


def test_flags_a_mixin():
    """A mixin only exists to be mixed into an app, so its `self` is an app."""
    res = _scan("""
        import asyncio
        class ReactionsMixin:
            async def on_thing(self, event):
                asyncio.create_task(self._ripple())
    """)
    assert len(res) == 1 and res[0]["holder"] == "self"


def test_a_non_app_class_is_flagged_via_app_not_self():
    """THE regression case. `self` here has no spawn_background; `app` does."""
    res = _scan("""
        import asyncio
        class Tool: pass
        class SubAgentTool(Tool):
            async def run(self, app, **kwargs):
                asyncio.create_task(self._go())
    """)
    assert len(res) == 1
    assert res[0]["holder"] == "app", "would rewrite onto a class that has no such method"


# ── silent: the shapes that are fine ─────────────────────────────────────────

def test_a_retained_task_is_not_flagged():
    res = _scan("""
        import asyncio
        class MyApp(BaseApp):
            async def setup(self):
                self._task = asyncio.create_task(self._loop())
    """)
    assert res == []


def test_a_task_added_to_a_collection_is_not_flagged():
    res = _scan("""
        import asyncio
        class MyApp(BaseApp):
            async def setup(self):
                self._tasks.add(asyncio.create_task(self._loop()))
    """)
    assert res == []


def test_a_plain_class_with_no_app_in_reach_is_not_flagged():
    """Nothing to suggest — flagging it would be noise the reader must dismiss
    forever, which is how a scanner stops being read."""
    res = _scan("""
        import asyncio
        class PlainThing:
            def go(self):
                asyncio.create_task(self._drain())
    """)
    assert res == []


def test_the_opt_out_marker_silences_a_site():
    res = _scan("""
        import asyncio
        class MyApp(BaseApp):
            async def setup(self):
                asyncio.create_task(self._warm())  # noqa: eos-bgtask  (deliberate)
    """)
    assert res == []


def test_a_module_with_no_create_task_is_cheap_and_silent():
    assert _scan("x = 1\n") == []


# ── the live tree stays clean ────────────────────────────────────────────────

def test_the_repo_has_no_unreferenced_detached_tasks():
    """Guards the 2026-08-05 sweep. A new bare create_task in an app or plugin
    should fail here rather than wait to be collected mid-flight in production."""
    findings = []
    for root in ("apps", "emptyos", "plugins"):
        base = _REPO / root
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            _m.REPO_ROOT = _REPO
            findings.extend(_m.scan_file(p))
    assert findings == [], (
        f"{len(findings)} unreferenced detached task(s): "
        + ", ".join(f"{f['file']}:{f['line']}" for f in findings[:8])
    )
