"""Unit tests for the codex think provider — no daemon, no subprocess.

The provider's only real logic is turning a `codex exec --json` stream into an
answer, so that is what these test. The fixtures are VERBATIM lines from a real
run (2026-09-02), not invented shapes: the whole reason this parser exists
rather than reusing `claude_run_stream.transform_stream_json_obj` is a detail
that only shows up in real output.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "codex_cli_under_test",
        _ROOT / "emptyos" / "capabilities" / "providers" / "codex_cli.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


codex = _load()

# Verbatim from `printf 'Reply with exactly: PONG' | codex exec --json -`.
REAL = "\n".join([
    '{"type":"thread.started","thread_id":"01a05d5b"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"error",'
    '"message":"Skill descriptions were shortened to fit the 2% skills context budget."}}',
    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"PONG"}}',
    '{"type":"turn.completed","usage":{"input_tokens":21603}}',
])


def test_the_skills_warning_never_reaches_the_answer():
    """Codex reports its own skills-budget warning as an `error` ITEM on a
    perfectly successful turn. It is not an error and it is not the answer.

    This is the entire reason this module does not reuse
    `transform_stream_json_obj`: that parser maps `error` to a text chunk, which
    is right for rendering a room turn and would prepend the warning to every
    `think()` result here. The second half of this test measures that, so the
    justification cannot rot into a claim nobody rechecks.
    """
    answer, errors = codex.extract_codex_reply(REAL)
    assert answer == "PONG"
    assert errors and "Skill descriptions" in errors[0]
    assert "Skill descriptions" not in answer

    from emptyos.sdk.claude_run_stream import transform_stream_json_obj
    shared = " ".join(
        e["text"] for line in REAL.splitlines()
        for e in transform_stream_json_obj(json.loads(line))
        if e.get("type") == "chunk"
    )
    assert "Skill descriptions" in shared, (
        "the shared parser no longer folds error items into chunks — if that is "
        "deliberate, this provider can stop hand-parsing the dialect")


def test_several_agent_messages_join_in_order():
    stream = "\n".join([
        '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"second"}}',
    ])
    assert codex.extract_codex_reply(stream)[0] == "first\nsecond"


def test_an_error_only_stream_yields_no_answer():
    """So `execute` raises instead of returning "" — a provider that returns an
    empty string looks like a successful call with nothing to say, and the
    capability chain would not fall through to the next provider."""
    stream = '{"type":"item.completed","item":{"type":"error","message":"boom"}}'
    assert codex.extract_codex_reply(stream) == ("", ["boom"])


@pytest.mark.parametrize("junk", [
    "", "   ", "not json\nalso not json",
    'warning: something\n{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
])
def test_non_json_lines_are_skipped_not_fatal(junk):
    """Codex interleaves plain-text warnings on stdout. One stray line must not
    discard a completed answer."""
    answer, _ = codex.extract_codex_reply(junk)
    assert answer == ("ok" if "agent_message" in junk else "")


def test_envelope_events_contribute_nothing():
    """thread.started / turn.started / turn.completed carry no answer text."""
    stream = "\n".join([
        '{"type":"thread.started","thread_id":"x"}',
        '{"type":"turn.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":21603}}',
    ])
    assert codex.extract_codex_reply(stream) == ("", [])


def test_provider_declares_cloud_and_login_auth():
    """Inference happens at OpenAI, so the consent gate must fire (rule 18) —
    but the credential is the CLI's own session, never an EmptyOS-held key."""
    p = codex.CodexCLIThinkProvider()
    assert p.name == "codex"
    assert p.is_cloud is True
    assert p.auth_mode == "login"


@pytest.mark.parametrize("section", [
    {},                      # section absent entirely
    {},                      # section present but COMMENTS ONLY -> TOML gives {}
    {"timeout": 600},        # section with a real key
])
def test_factory_builds_codex_for_every_section_shape(section):
    """Registration is what makes `think.app.<id> = "codex"` mean anything — an
    override naming a provider the factory never built falls through in silence.

    The middle case is not redundant: `[capabilities.think.codex]` holding only
    comment lines parses to `{}`, which is falsy, and the factory's
    `if not section: return None` guard then dropped the provider entirely. The
    chain named codex, the config had a codex section, and no codex existed.
    Grepping setup.py for the branch would have passed the whole time.
    """
    from emptyos.capabilities.setup import _build_think_provider_raw

    class _Cfg:
        def get_section(self, key):
            return dict(section) if key == "capabilities.think.codex" else {}

        def get(self, key, default=None):
            return default

    provider = _build_think_provider_raw("codex", _Cfg())
    assert provider is not None, "codex named in a chain but never constructed"
    assert provider.name == "codex"
