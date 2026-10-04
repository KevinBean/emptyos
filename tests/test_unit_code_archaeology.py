"""Unit tests for scripts/code_archaeology.py AST binding extraction.

Regression guard for the multi-module binding shapes the audit must recognise
as method *definitions* (see .claude/rules/multi-module-apps.md). The script is
wired into scripts/release-public.py as a HIGH-tier gate, so a false negative
here (a real binding read as "undefined") would block a legitimate release.

No daemon / kernel needed — pure AST helpers. The module is loaded by file
path because its directory (`scripts/`) isn't a package and the filename has a
hyphen-free name but a top-level `@dataclass` needs the module registered in
sys.modules before exec.
"""
import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_ca():
    spec = importlib.util.spec_from_file_location(
        "code_archaeology_under_test", str(ROOT / "scripts" / "code_archaeology.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["code_archaeology_under_test"] = mod  # before exec (@dataclass)
    spec.loader.exec_module(mod)
    return mod


ca = _load_ca()


def _defined(src: str) -> set[str]:
    """Run the script's defined-name extraction over a class body source."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and ca._is_binding_ref(node.value):
            out.update(ca._flatten_name_targets(node.targets))
    return out


def test_plain_attribute_binding_captured():
    assert _defined("class X:\n    a = _mod.a\n") == {"a"}


def test_bare_name_binding_captured():
    assert _defined("class X:\n    a = helper\n") == {"a"}


def test_staticmethod_wrap_captured():
    # The exact regression: `name = staticmethod(_mod.fn)` was previously missed.
    assert _defined("class X:\n    b = staticmethod(_helper.b)\n") == {"b"}


def test_classmethod_wrap_captured():
    assert _defined("class X:\n    c = classmethod(_helper.c)\n") == {"c"}


def test_tuple_target_binding_captured():
    assert _defined("class X:\n    d, e = _mod.d, _mod.e\n") == {"d", "e"}


def test_list_target_binding_captured():
    assert _defined("class X:\n    [f, g] = [_mod.f, _mod.g]\n") == {"f", "g"}


def test_non_reference_rhs_ignored():
    # Literals, calls, and comprehensions are NOT method bindings — must not
    # be added to `defined` (the other direction: no false definitions).
    assert _defined(
        "class X:\n"
        "    h = 5\n"
        "    i = some_func()\n"
        "    j = {1: 2}\n"
        "    k = [x for x in range(3)]\n"
    ) == set()


def test_mixed_block_only_real_bindings():
    src = (
        "class X:\n"
        "    a = _mod.a\n"
        "    b = staticmethod(_h.b)\n"
        "    n = 5\n"
        "    c, d = _mod.c, _mod.d\n"
        "    m = build()\n"
    )
    assert _defined(src) == {"a", "b", "c", "d"}


@pytest.mark.parametrize("wrapper", ["staticmethod", "classmethod"])
def test_wrapper_requires_attribute_or_name_arg(wrapper):
    # staticmethod(literal) is not a binding ref (no method to bind).
    assert _defined(f"class X:\n    z = {wrapper}(5)\n") == set()


# ─────────────── --impact reverse blast radius + agent-cli envelope ───────────────
#
# These pin the GitNexus-borrowed `impact()` behaviour: depth-grouped reverse
# traversal + the .claude/rules/agent-cli.md envelope. Synthetic AppInfo graph,
# no filesystem / daemon.


def _app(app_id, *, defined=(), call_sites=(), listens=None, manifest=None):
    a = ca.AppInfo(app_id=app_id, dir=Path("."), manifest=manifest or {})
    a.defined = set(defined)
    a.call_app_sites = list(call_sites)
    if listens:
        a.listens = dict(listens)
    return a


def _graph():
    # a.m  <- called by b      (depth 1)
    # b.x  <- called by c      (so editing a.m reaches c at depth 2)
    # c    <- called by nobody
    return {
        "a": _app("a", defined=["m"]),
        "b": _app("b", defined=["x"], call_sites=[("a", "m", "b.py:1")]),
        "c": _app("c", call_sites=[("b", "x", "c.py:2")]),
    }


def test_impact_app_method_depth_grouped():
    res = ca.impact(_graph(), "a.m")
    assert res["target"] == {"app": "a", "method": "m", "exists": True}
    assert [c["app"] for c in res["callers"]] == ["b"]
    assert res["callers"][0]["where"] == "b.py:1"
    assert res["blast_radius"] == [
        {"depth": 1, "apps": ["b"]},
        {"depth": 2, "apps": ["c"]},
    ]
    assert res["affected_apps"] == ["b", "c"]
    assert res["max_depth_reached"] == 2
    assert "error_code" not in res


def test_impact_max_depth_truncates():
    res = ca.impact(_graph(), "a.m", max_depth=1)
    assert res["blast_radius"] == [{"depth": 1, "apps": ["b"]}]
    assert res["affected_apps"] == ["b"]
    assert res["max_depth_reached"] == 1


def test_impact_app_not_found():
    res = ca.impact(_graph(), "ghost.m")
    assert res["error_code"] == "not_found"
    assert res["target"]["exists"] is False


def test_impact_invalid_query():
    res = ca.impact(_graph(), "nodotted")
    assert res["error_code"] == "invalid_args"


def test_impact_method_defined_but_absent_is_not_an_error():
    # app exists, method doesn't — a valid (advisory) answer, not a query error.
    res = ca.impact(_graph(), "a.nope")
    assert "error_code" not in res
    assert res["target"]["exists"] is False
    assert res["affected_apps"] == []


def test_impact_event_listeners():
    g = _graph()
    g["d"] = _app("d", listens={"e:v": ["on_e"]})
    res = ca.impact(g, "emit:e:v")
    assert res["kind"] == "event"
    assert [l["app"] for l in res["listeners"]] == ["d"]
    assert res["blast_radius"] == [{"depth": 1, "apps": ["d"]}]
    assert res["affected_apps"] == ["d"]


def test_impact_self_call_excluded_from_blast_radius():
    # an app calling its own method is not blast radius.
    g = {"a": _app("a", defined=["m", "n"], call_sites=[("a", "m", "a.py:9")])}
    res = ca.impact(g, "a.m")
    assert res["affected_apps"] == []
    assert res["blast_radius"] == []


def test_emit_envelope_exit_codes(capsys):
    assert ca.emit_envelope(True, "ok", "fine", {"x": 1}) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, "code": "ok", "message": "fine", "data": {"x": 1}}
    assert ca.emit_envelope(False, "not_found", "nope", None) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["code"] == "not_found"
