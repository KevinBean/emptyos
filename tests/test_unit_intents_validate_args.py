"""Unit tests for emptyos.sdk.intents.validate_args — the Gate-1 shape check.

Pure function, no daemon. This guards every model-originated verb dispatch that
opts in, so both directions are pinned: what it must accept (or the voice
surface regresses) and what it must reject (or the gate is decorative).

The `allow_unknown=True` block is a *regression* suite — those cases describe
behaviour that predates the flag and must stay byte-identical, because the
voice callers (voice-assistant chat_pipeline / planning / build_plan_dict) rely
on it and were the function's only consumers before this change.
"""
from __future__ import annotations

from emptyos.sdk.intents import validate_args


# ── Regression: default (allow_unknown=True) behaviour is unchanged ──────────


def test_valid_types_pass():
    schema = {"text": "string", "count": "number", "done": "boolean"}
    ok, msg = validate_args(schema, {"text": "x", "count": 3, "done": True})
    assert ok and msg == ""


def test_number_accepts_int_and_float():
    for v in (3, 3.5):
        ok, _ = validate_args({"n": "number"}, {"n": v})
        assert ok, v


def test_missing_required_arg_rejects():
    ok, msg = validate_args({"text": "string"}, {})
    assert not ok
    assert "text" in msg


def test_missing_optional_arg_passes():
    ok, msg = validate_args({"text": "string", "due": "string?"}, {"text": "x"})
    assert ok and msg == ""


def test_wrong_type_rejects_and_names_the_arg():
    ok, msg = validate_args({"text": "string"}, {"text": 42})
    assert not ok
    assert "text" in msg and "string" in msg


def test_non_dict_args_rejects():
    # parse_llm_json can legally return a list; the gate must not splat it.
    ok, msg = validate_args({"text": "string"}, ["text", "x"])  # type: ignore[arg-type]
    assert not ok
    assert "JSON object" in msg


def test_unknown_key_passes_by_default():
    # The pre-flag behaviour, relied on by the voice surface. An undeclared key
    # rides through to fn(**kwargs); this test exists so nobody tightens the
    # default without noticing which callers they changed.
    ok, msg = validate_args({"text": "string"}, {"text": "x", "extra": 1})
    assert ok and msg == ""


def test_unknown_type_token_passes_silently():
    # Deliberate: an unrecognised type token is a manifest authoring error,
    # caught statically by scripts/check_verb_args.py. Failing here would break
    # a working verb over a typo in its declaration.
    ok, _ = validate_args({"tags": "list[str]"}, {"tags": ["a", "b"]})
    assert ok


def test_empty_schema_accepts_anything_by_default():
    ok, _ = validate_args({}, {"whatever": 1})
    assert ok


# ── New: allow_unknown=False makes the declared schema a real contract ───────


def test_unknown_key_rejects_when_strict():
    ok, msg = validate_args({"text": "string"}, {"text": "x", "txt": "y"}, allow_unknown=False)
    assert not ok
    assert "'txt'" in msg


def test_rejection_names_the_accepted_set():
    # The message is fed back to the model, so it must carry enough to
    # self-correct — the bad key AND what was allowed instead.
    ok, msg = validate_args(
        {"text": "string", "due": "string?"},
        {"text": "x", "titel": "typo"},
        allow_unknown=False,
    )
    assert not ok
    assert "'titel'" in msg
    assert "text" in msg and "due" in msg


def test_multiple_unknown_keys_all_named_and_sorted():
    ok, msg = validate_args(
        {"text": "string"}, {"text": "x", "zeta": 1, "alpha": 2}, allow_unknown=False
    )
    assert not ok
    assert msg.index("'alpha'") < msg.index("'zeta'")


def test_renamed_arg_reports_both_problems():
    # text -> txt is simultaneously a missing required arg and an unexpected
    # one. Reporting only the first would tell the model half the story and it
    # would re-emit the same wrong key.
    ok, msg = validate_args({"text": "string"}, {"txt": "typo"}, allow_unknown=False)
    assert not ok
    assert "'text'" in msg and "'txt'" in msg


def test_single_problem_message_is_unchanged_by_strict_mode():
    # Only the combined case is new; one-problem messages must not drift.
    lenient = validate_args({"text": "string"}, {})[1]
    strict = validate_args({"text": "string"}, {}, allow_unknown=False)[1]
    assert lenient == strict == "missing required arg 'text'"


def test_valid_call_still_passes_when_strict():
    ok, msg = validate_args(
        {"text": "string", "due": "string?"}, {"text": "x"}, allow_unknown=False
    )
    assert ok and msg == ""


def test_empty_schema_is_undeclared_not_accepts_nothing():
    # Load-bearing. A verb with `args = {}` has *not* declared a contract, so
    # strict mode must leave it alone — otherwise turning the flag on would
    # reject every call to every verb that never listed its args.
    ok, msg = validate_args({}, {"anything": 1, "at": "all"}, allow_unknown=False)
    assert ok and msg == ""


def test_strict_still_enforces_declared_types_and_required():
    ok, _ = validate_args({"n": "number"}, {"n": "not-a-number"}, allow_unknown=False)
    assert not ok
    ok, _ = validate_args({"n": "number"}, {}, allow_unknown=False)
    assert not ok
