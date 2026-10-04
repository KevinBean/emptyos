"""A think call must run `claude -p` with the operator's hooks switched off.

A bare `claude -p` loads every user-settings and plugin hook. Measured
2026-09-30 in the Telegram-bridge dispatch eval: security-guidance's Stop hook
reviewed the git diff of the vault cwd, and in 2 of 58 turns the model's final
message became a reply to that review instead of the answer. The fix is
`--settings '{"disableAllHooks": true}'` on both spawn paths. It must not be
`--bare`, which stops reading the subscription login (a think then fails
without an API key, or bills one).

Offline: `create_subprocess_exec` is replaced by a stub that records the argv
and aborts, so no real `claude` runs.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from emptyos.capabilities.providers.claude_cli import ClaudeCLIThinkProvider


class _Spawned(Exception):
    pass


@pytest.fixture
def spawn(monkeypatch):
    seen: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        seen.append([str(a) for a in args])
        raise _Spawned

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return seen


def _provider():
    p = ClaudeCLIThinkProvider(model="opus")
    p._claude_path = "/fake/claude"
    return p


def _hooks_disabled(argv: list[str]) -> bool:
    if "--settings" not in argv:
        return False
    settings = json.loads(argv[argv.index("--settings") + 1])
    return settings.get("disableAllHooks") is True


def _assert_hookless(argv: list[str]):
    assert _hooks_disabled(argv), argv
    # --bare also skips hooks, but it never reads the OAuth login: every think
    # would silently move from the subscription to billed API usage.
    assert "--bare" not in argv
    # Dropping the user source would also drop the operator's model/effort.
    assert "--setting-sources" not in argv


@pytest.mark.asyncio
async def test_execute_spawns_claude_with_hooks_disabled(spawn):
    with pytest.raises(_Spawned):
        await _provider().execute(prompt="hi", system="be brief")
    _assert_hookless(spawn[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_json", [False, True])
async def test_execute_stream_spawns_claude_with_hooks_disabled(spawn, stream_json):
    with pytest.raises(_Spawned):
        async for _ in _provider().execute_stream(prompt="hi", stream_json=stream_json):
            pass
    _assert_hookless(spawn[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["execute", "stream_text", "stream_json"])
async def test_long_prompt_on_stdin_keeps_hooks_disabled(spawn, path):
    # Above the argv budget the prompt moves to stdin; the flag must survive
    # the channel switch on every path (it sits in base_cmd, not after the prompt).
    long = "x" * 60000
    p = _provider()
    with pytest.raises(_Spawned):
        if path == "execute":
            await p.execute(prompt=long)
        else:
            async for _ in p.execute_stream(prompt=long, stream_json=path == "stream_json"):
                pass
    argv = spawn[0]
    _assert_hookless(argv)
    assert long not in argv
