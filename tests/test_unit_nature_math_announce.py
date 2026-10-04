"""nature-math-simulation emits its events under their full, declared names,
with the payload the reactor reads.

``_announce`` used to build the name (``f"{APP_ID}:{event}"``), which
scripts/check_event_wiring.py cannot read, so wiring the reactor to
``:fitted`` / ``:rendered`` (2026-10-04) reported both listeners as phantoms.
Callers now pass the literal name and ``_announce`` emits it unchanged.

Two halves, because a test of the helper alone would never notice a call site
passing a short name or a renamed payload key: drive ``_announce``, and
AST-read every real call.
"""

from __future__ import annotations

import ast

import pytest

from helpers import app_path, load_app_module, public_snapshot

APP = "nature-math-simulation"
# Skip only where the extension track is dropped. In the private tree a missing
# app is a failure, never a silent skip (tests/helpers.py::public_snapshot).
pytestmark = pytest.mark.skipif(public_snapshot(), reason="extension track not in the public snapshot")


def test_announce_emits_the_name_it_is_given():
    mod = load_app_module(APP, "app")
    emitted, spawned = [], []

    class Fake:
        def emit(self, name, payload):
            emitted.append((name, payload))
            return "coro"

        def spawn_background(self, coro, label=""):
            spawned.append((coro, label))

    mod.NatureMathSimulationApp._announce(Fake(), "nature-math-simulation:fitted", {"id": "m1"})
    assert emitted == [("nature-math-simulation:fitted", {"id": "m1"})]
    assert spawned == [("coro", "nature-math-simulation:fitted")]


def _announce_calls():
    """(event name, payload keys, payload `kind` or None) for every real call site."""
    tree = ast.parse((app_path(APP) / "app.py").read_text(encoding="utf-8"))
    sites = []
    for c in ast.walk(tree):
        if not (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "_announce"):
            continue
        name, payload = c.args
        assert isinstance(name, ast.Constant), "event name built at runtime"
        assert isinstance(payload, ast.Dict), "payload not a dict literal"
        keys = {k.value for k in payload.keys}
        kind = next((v.value for k, v in zip(payload.keys, payload.values) if k.value == "kind"), None)
        sites.append((name.value, keys, kind))
    return sites


def test_each_call_site_sends_what_the_reactor_reads():
    # One site per expected payload: the fit, a still, and an animation.
    assert sorted(_announce_calls(), key=lambda s: (s[0], s[2] or "")) == [
        ("nature-math-simulation:fitted", {"id", "recipe", "passed", "of", "reference"}, None),
        ("nature-math-simulation:rendered", {"kind", "path", "frames"}, "animation"),
        ("nature-math-simulation:rendered", {"kind", "path"}, "still"),
    ]
