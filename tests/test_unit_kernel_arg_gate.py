"""Pins Kernel.validate_verb_args — the [verbs] arg_gate contract.

Called unbound against a stub `self` so no Kernel is instantiated (that would
open a syslog SQLite handle — see .claude/rules/daemon-handling.md). Importing
the module is safe; the handle opens in __init__.

The flag-off case is the load-bearing one: with the gate dark every dispatch
path must behave byte-identically, which is the whole regression contract.
"""
from __future__ import annotations

import types

from emptyos.kernel import Kernel


class _Verbs:
    def __init__(self, entries: dict):
        self._e = entries

    def get(self, verb):
        return self._e.get(verb)


def _stub(flag, entries: dict | None = None):
    """Minimal duck-typed stand-in for the two attributes the method touches."""
    s = types.SimpleNamespace()
    s.config = types.SimpleNamespace(get=lambda key, default=None: flag)
    s.apps = types.SimpleNamespace(get_verbs=lambda: _Verbs(entries or {}))
    return s


def _entry(args: dict):
    return types.SimpleNamespace(args=args)


def _call(stub, verb, args):
    return Kernel.validate_verb_args(stub, verb, args)


# ── flag off: total no-op ────────────────────────────────────────────────────


def test_off_passes_even_a_violating_call():
    stub = _stub(False, {"task.add": _entry({"text": "string"})})
    assert _call(stub, "task.add", {"txt": "wrong", "n": 1}) == (True, "")


def test_off_is_the_default_when_key_absent():
    s = types.SimpleNamespace()
    s.config = types.SimpleNamespace(get=lambda key, default=None: default)
    s.apps = types.SimpleNamespace(get_verbs=lambda: _Verbs({"task.add": _entry({"text": "string"})}))
    assert _call(s, "task.add", {"nope": 1}) == (True, "")


# ── flag on ─────────────────────────────────────────────────────────────────


def test_on_accepts_a_valid_call():
    stub = _stub(True, {"task.add": _entry({"text": "string"})})
    assert _call(stub, "task.add", {"text": "buy milk"}) == (True, "")


def test_on_rejects_an_unknown_key_and_names_it():
    stub = _stub(True, {"task.add": _entry({"text": "string"})})
    ok, msg = _call(stub, "task.add", {"txt": "typo"})
    assert not ok
    assert "'txt'" in msg and "text" in msg


def test_on_rejects_a_wrong_type():
    stub = _stub(True, {"task.add": _entry({"text": "string"})})
    ok, msg = _call(stub, "task.add", {"text": 42})
    assert not ok


def test_on_ignores_a_verb_with_no_registry_entry():
    # Nothing was promised, so nothing is enforced — this is what keeps the
    # gate from breaking internal or undeclared calls when it's switched on.
    stub = _stub(True, {})
    assert _call(stub, "whatever.method", {"anything": 1}) == (True, "")


def test_on_ignores_a_verb_declaring_empty_args():
    # `args = {}` is "undeclared", never "accepts nothing".
    stub = _stub(True, {"task.list": _entry({})})
    assert _call(stub, "task.list", {"limit": 5}) == (True, "")


def test_string_truthy_flag_values_enable_the_gate():
    for v in ("true", "1", "yes", "ON", " True "):
        stub = _stub(v, {"task.add": _entry({"text": "string"})})
        ok, _ = _call(stub, "task.add", {"txt": "x"})
        assert not ok, v


def test_string_falsy_flag_values_leave_it_off():
    for v in ("false", "0", "no", ""):
        stub = _stub(v, {"task.add": _entry({"text": "string"})})
        assert _call(stub, "task.add", {"txt": "x"}) == (True, "")


# ── fails open ──────────────────────────────────────────────────────────────


def test_registry_explosion_fails_open():
    # A broken registry must not wedge every action path in the system; the
    # per-site TypeError handling is still underneath as the backstop.
    def boom():
        raise RuntimeError("registry on fire")

    s = types.SimpleNamespace()
    s.config = types.SimpleNamespace(get=lambda key, default=None: True)
    s.apps = types.SimpleNamespace(get_verbs=boom)
    assert _call(s, "task.add", {"txt": "x"}) == (True, "")
