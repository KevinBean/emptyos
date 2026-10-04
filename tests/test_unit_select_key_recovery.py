"""Unit tests for BaseApp.select() answer recovery + the existing matching ladder.

Regression origin (2026-08-05): the menu renders as ``- <key>: <description>`` and
models routinely echo the whole line back as the choice. Measured at ~11% of
dict-form calls on both qwen3.5-32k and gpt-5.4-mini, with the model naming the
CORRECT key every time -- so select() was discarding a right answer for the
caller's default. See the vault note "2026-08-05 select() order-bias experiment".

Both directions are pinned: the echo shape must now resolve, and a genuinely
unusable reply must still fall back (and still record the demand).
"""

from __future__ import annotations

import asyncio

from emptyos.sdk.base_app import BaseApp


def _app(reply):
    """BaseApp stub whose think() returns `reply` (or raises it)."""
    app = BaseApp.__new__(BaseApp)
    app._demands = []

    async def fake_think(prompt, domain=None, system=None, min_ability=None, **kw):
        if isinstance(reply, Exception):
            raise reply
        return reply

    def fake_demand(**kw):
        app._demands.append(kw)

    app.think = fake_think
    app._record_demand = fake_demand
    return app


def _run(app, choices, default=None):
    return asyncio.run(BaseApp.select(app, "pick one", choices, default=default))


KINDS = {
    "concept": "explanatory standalone",
    "formula": "implementable spec",
    "clause": "verbatim text of one section of a standard",
    "case": "worked example",
}

# scope_menu emits colon-bearing keys -- a naive split(":") would truncate these.
SCOPES = {
    "__all__": "No clear topic -- search the whole vault",
    "tag:cable": "Notes tagged #cable",
    "folder:10_Projects/emptyos": "The emptyos project",
}


# --------------------------------------------------------------- the recovery


def test_key_colon_description_echo_resolves():
    """The measured failure: {"choice": "clause: verbatim text ..."} -> "clause"."""
    app = _app('{"choice": "clause: verbatim text of one section of a standard"}')
    assert _run(app, KINDS, default="concept") == "clause"
    assert app._demands == []  # recovered, so no no_match demand recorded


def test_echo_without_json_wrapper_resolves():
    app = _app("confusing: works but unclear")
    assert _run(app, {"bug": "behaves wrong", "confusing": "works but unclear"},
                default="bug") == "confusing"


def test_echo_recovery_is_case_insensitive_and_strips_quotes():
    app = _app('{"choice": "\\"Clause: Verbatim Text Of One Section\\""}')
    assert _run(app, KINDS, default="concept") == "clause"


# ------------------------------------------- colon-bearing keys must survive


def test_colon_bearing_key_not_truncated():
    """``tag:cable`` echoed with its description must not collapse to ``tag``."""
    app = _app('{"choice": "tag:cable: Notes tagged #cable"}')
    assert _run(app, SCOPES, default="__all__") == "tag:cable"


def test_exact_colon_bearing_key_still_exact_matches():
    app = _app('{"choice": "folder:10_Projects/emptyos"}')
    assert _run(app, SCOPES, default="__all__") == "folder:10_Projects/emptyos"


def test_longest_key_wins_when_one_key_prefixes_another():
    """With both ``tag`` and ``tag:cable`` offered, the specific key must win."""
    choices = {"tag": "any tag", "tag:cable": "Notes tagged #cable"}
    app = _app('{"choice": "tag:cable: Notes tagged #cable"}')
    assert _run(app, choices, default="tag") == "tag:cable"


# ------------------------------------------------ existing ladder unchanged


def test_exact_json_choice_still_wins():
    app = _app('{"choice": "formula"}')
    assert _run(app, KINDS, default="concept") == "formula"


def test_bare_key_reply_still_wins():
    app = _app("formula")
    assert _run(app, KINDS, default="concept") == "formula"


def test_case_insensitive_bare_key_still_wins():
    app = _app("  FORMULA  ")
    assert _run(app, KINDS, default="concept") == "formula"


def test_list_form_choices_still_supported():
    app = _app('{"choice": "no"}')
    assert _run(app, ["yes", "no"], default="yes") == "no"


# ------------------------------------------------------ fallback still holds


def test_hallucinated_key_still_falls_back_and_records_demand():
    app = _app('{"choice": "epsilon"}')
    assert _run(app, KINDS, default="concept") == "concept"
    assert len(app._demands) == 1


def test_prose_mentioning_a_key_mid_sentence_does_not_match():
    """Recovery is prefix-anchored -- it must not fire on incidental mentions."""
    app = _app('{"choice": "I would ask: it depends on the note"}')
    assert _run(app, {"ask": "a question", "find": "locate notes"}, default="find") == "find"


def test_empty_reply_falls_back_to_default_not_first_key():
    app = _app("")
    assert _run(app, KINDS, default="case") == "case"


def test_provider_exception_falls_back_to_default():
    app = _app(RuntimeError("provider down"))
    assert _run(app, KINDS, default="case") == "case"


def test_no_default_falls_back_to_first_key():
    """Documented behaviour: without default=, an unusable reply lands on keys[0]."""
    app = _app("")
    assert _run(app, KINDS) == "concept"
