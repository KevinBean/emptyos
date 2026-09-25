"""The figure-receiver contract, pinned across every target app.

viz exports a static figure by asking the *target* app two things:

    call_app(app, "figure_asset_path", **target_kwargs) -> vault-relative .svg
    call_app(app, "attach_figure", asset=, png=, alt=, viz_id=, **target_kwargs)

kb and publish implement these independently and share **no code**, which is
correct: the paths differ (`notes_dir/_assets/<slug>.svg` vs
`source_folder/images/<post>-<concept>.svg`) and the embed semantics are
opposites — kb writes the figure into the note, publish deliberately writes
nothing and hands back a body line, because a diagram's position and its alt
text are editorial. Merging them would mean a `mode=` flag that exists only
because the callers disagree.

What two independent implementations of one interface DO need is protection
from drift. That is this file: no shared base class, but a shared contract
nothing can quietly break. It catches the three real failure shapes —

* viz adds a target kind whose kwargs a receiver cannot accept,
* a receiver renames a parameter viz passes by keyword (`call_app` is
  kwargs-only, so a rename is a TypeError at dispatch, inside viz's
  `except Exception` and therefore silent),
* a receiver stops returning a key the caller branches on (`embedded`
  decides whether the UI claims the figure was placed).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from helpers import load_app_module  # noqa: E402

# Kwargs viz always sends to attach_figure, on top of the per-target ones.
FIXED_ATTACH_KWARGS = ("asset", "png", "alt", "viz_id")

# (app_id, helper module, target kwargs) — target kwargs mirror viz's
# FIGURE_TARGETS, which the first test asserts is still the source of truth.
RECEIVERS = [
    pytest.param("kb", "figures", ("slug",), id="kb"),
    pytest.param("publish", "media", ("post", "concept"), id="publish"),
]


def _viz_targets() -> dict[str, tuple[str, ...]]:
    """FIGURE_TARGETS read statically, so this needs no kernel."""
    src = (ROOT / "apps/public/standard/viz/figures.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FIGURE_TARGETS" for t in node.targets
        ):
            return {
                k.value: tuple(e.value for e in v.elts)
                for k, v in zip(node.value.keys, node.value.values)
            }
    raise AssertionError("viz/figures.py no longer defines FIGURE_TARGETS")


def test_the_receiver_list_matches_vizs_target_table():
    """A new target app must land here too, or it ships unpinned."""
    declared = _viz_targets()
    covered = {p.values[0]: p.values[2] for p in RECEIVERS}
    assert declared == covered, (
        f"viz targets {declared} but this file pins {covered} — add the new "
        f"receiver's contract test."
    )


@pytest.mark.parametrize("app_id,module,target_kwargs", RECEIVERS)
def test_receiver_accepts_every_kwarg_viz_sends(app_id, module, target_kwargs):
    mod = load_app_module(app_id, module)

    path_params = inspect.signature(mod.figure_asset_path).parameters
    for key in target_kwargs:
        assert key in path_params, (
            f"{app_id}.figure_asset_path does not accept '{key}'; call_app is "
            f"kwargs-only so viz would TypeError at dispatch."
        )

    attach_params = inspect.signature(mod.attach_figure).parameters
    for key in target_kwargs + FIXED_ATTACH_KWARGS:
        assert key in attach_params, f"{app_id}.attach_figure does not accept '{key}'"


@pytest.mark.parametrize("app_id,module,target_kwargs", RECEIVERS)
def test_receiver_params_are_optional_so_a_partial_call_fails_soft(app_id, module, target_kwargs):
    """Every param defaults, so a missing kwarg returns an error dict rather
    than raising inside viz's `except Exception` and vanishing."""
    mod = load_app_module(app_id, module)
    for fn_name in ("figure_asset_path", "attach_figure"):
        for name, p in inspect.signature(getattr(mod, fn_name)).parameters.items():
            if name == "self":
                continue
            assert p.default is not inspect.Parameter.empty, (
                f"{app_id}.{fn_name} param '{name}' has no default"
            )


@pytest.mark.parametrize("app_id,module,_kw", RECEIVERS)
def test_asset_path_returns_empty_string_for_missing_input(app_id, module, _kw):
    """viz treats "" as 'this app cannot receive a figure' and refuses cleanly."""
    mod = load_app_module(app_id, module)
    assert mod.figure_asset_path(object()) == ""


def test_publish_attach_returns_the_body_line_and_does_not_claim_placement():
    """publish is the `embedded: False` half of the contract — the UI branches
    on it to show the line instead of saying the figure landed."""
    mod = load_app_module("publish", "media")
    res = asyncio.run(mod.attach_figure(
        object(), post="ai-access", concept="safety-line",
        asset="Published/images/ai-access-safety-line.svg", png="", alt="A takeaway",
    ))
    assert res["ok"] is True
    assert res["embedded"] is False
    assert res["markdown"] == "![A takeaway|637](ai-access-safety-line.png)"


def test_publish_attach_neutralises_brackets_in_alt():
    """A `[` truncates the embed at the first `]`, rendering the whole line as
    literal text — the house standard calls this out explicitly."""
    mod = load_app_module("publish", "media")
    res = asyncio.run(mod.attach_figure(
        object(), post="p", concept="c", asset="x.svg", alt="the [DO:] token path",
    ))
    # The alt sits between the opening `![` and the `](` — that span, and only
    # that span, must be bracket-free.
    alt_span = res["markdown"].split("](")[0].removeprefix("![")
    assert "[" not in alt_span and "]" not in alt_span
    assert res["markdown"].startswith("![the (DO:) token path|637]")


def test_publish_attach_refuses_without_an_asset():
    mod = load_app_module("publish", "media")
    res = asyncio.run(mod.attach_figure(object(), post="p", concept="c"))
    assert res["ok"] is False and "asset" in res["error"]
