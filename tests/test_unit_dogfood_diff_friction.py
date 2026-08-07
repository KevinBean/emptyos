"""Unit tests for dogfood-agent `_diff_friction` -- the gate on auto-mark-done.

`cleared` (verified) items are moved to done by `_finalize`, so a false positive
here silently closes an unfixed bug. The rule under test: absence is only evidence
when we can SHOW the persona went back and looked.

Regression origin (2026-08-05): the guard read `app is None or app in
exercised_apps`, which failed OPEN -- `_friction_app` returns None for 39% of real
friction items and every one auto-cleared. Replaying same-day run pairs (unchanged
system, nothing fixed between them) through the real function put 90% of carried
friction into `verified`. See "2026-08-05 dogfood persona test-retest".
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/extension/dev/dogfood-agent"

pytestmark = pytest.mark.skipif(not APP.exists(), reason="dogfood-agent not installed")


def _host():
    """Load drain.py standalone and expose its classmethods as the app binds them."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "dfa_t" not in sys.modules:
        pkg = types.ModuleType("dfa_t")
        pkg.__path__ = [str(APP)]
        sys.modules["dfa_t"] = pkg
    for name in ("shared", "drain"):
        key = f"dfa_t.{name}"
        if key not in sys.modules:
            spec = importlib.util.spec_from_file_location(key, APP / f"{name}.py")
            m = importlib.util.module_from_spec(spec)
            sys.modules[key] = m
            spec.loader.exec_module(m)
    drain = sys.modules["dfa_t.drain"]

    class Host:
        _friction_key = staticmethod(drain._friction_key)
        _friction_app = drain._friction_app
        _diff_friction = drain._diff_friction

    return Host


def _f(kind, text):
    return {"kind": kind, "text": text}


def _act(target):
    return {"target": target}


# ------------------------------------------------- the fail-open regression


def test_unattributable_item_is_held_not_verified():
    """`_friction_app` -> None must land in not_retested, never in cleared."""
    H = _host()
    before = [_f("bug", "Hit 5 of 5 goals in 6 turns but the inline due: syntax is ignored")]
    d = H._diff_friction(before, [], None, [_act("journal:entry")])
    assert d["cleared"] == [], "unattributable friction must not auto-clear"
    assert len(d["cleared_not_retested"]) == 1


def test_cli_friction_is_held_when_no_apps_were_exercised():
    """CLI runs produce no `<app>:` targets, so nothing can be verified-cleared."""
    H = _host()
    before = [
        _f("missing", "- TURN 9 - `eos task list --json` - no --json on task list"),
        _f("bug", "- TURN 7 - `eos` (status) - resolved the wrong emptyos.toml"),
    ]
    d = H._diff_friction(before, [], None, [_act("tool:Bash"), _act("vault:50_Journal")])
    assert d["cleared"] == []
    assert len(d["cleared_not_retested"]) == 2


# ------------------------------------------------------ the intended path


def test_absence_after_revisiting_the_app_is_verified():
    H = _host()
    before = [_f("bug", "POST /journal/api/entry with em-dash · 500 utf-8 decode")]
    d = H._diff_friction(before, [], None, [_act("journal:api/entry")])
    assert len(d["cleared"]) == 1, "persona went back to journal and saw nothing"
    assert d["cleared_not_retested"] == []


def test_absence_without_revisiting_the_app_is_held():
    H = _host()
    before = [_f("bug", "POST /journal/api/entry with em-dash · 500 utf-8 decode")]
    d = H._diff_friction(before, [], None, [_act("task:api/list")])
    assert d["cleared"] == []
    assert len(d["cleared_not_retested"]) == 1


def test_still_present_item_is_regressed_not_cleared():
    H = _host()
    f = _f("bug", "POST /journal/api/entry with em-dash · 500 utf-8 decode")
    d = H._diff_friction([f], [dict(f)], None, [_act("journal:api/entry")])
    assert len(d["regressed"]) == 1
    assert d["cleared"] == [] and d["cleared_not_retested"] == []


def test_new_item_is_reported_as_new():
    H = _host()
    before = [_f("bug", "POST /journal/api/entry · 500")]
    after = [_f("bug", "POST /journal/api/entry · 500"),
             _f("missing", "GET /task/api/list · no due dates")]
    d = H._diff_friction(before, after, None, [_act("journal:api/entry")])
    assert len(d["new"]) == 1 and len(d["regressed"]) == 1


# -------------------------------------------------- paraphrase stays visible


def test_paraphrase_is_not_silently_verified_when_app_not_revisited():
    """A reworded re-report misses the exact-prefix key; it must not auto-clear."""
    H = _host()
    before = [_f("bug", "POST /assistant/api/chat · 500 no provider for think")]
    after = [_f("bug", "POST /assistant/api/chat · HTTP 200 but body is an error string")]
    d = H._diff_friction(before, after, None, [_act("task:api/list")])
    assert d["cleared"] == []
    assert len(d["cleared_not_retested"]) == 1


def test_exercised_apps_ignores_non_app_prefixes():
    H = _host()
    before = [_f("bug", "POST /journal/api/entry · 500")]
    for tgt in ("vault:journal", "tool:journal", "kernel:journal", "static:journal"):
        d = H._diff_friction(before, [], None, [_act(tgt)])
        assert d["cleared"] == [], f"{tgt} must not count as exercising the journal app"
