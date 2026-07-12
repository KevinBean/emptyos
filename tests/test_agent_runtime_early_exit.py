"""Agent-runtime `run()` early-exit on terminal sentinel.

claude-cli sometimes emits its terminal stream-json `{"type":"result", ...}`
event and then hangs on background cleanup, leaving `proc.wait()` blocked
indefinitely. `run()` accepts an `early_exit_on_line` predicate; first match
starts a `early_exit_grace_s` countdown and then kills the process.

These tests drive `run()` against tiny python subprocesses that simulate
both shapes (hangs after sentinel; exits cleanly without sentinel) and
assert the timing + returncode contract.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_PATH = REPO / "plugins" / "agent-runtime" / "plugin.py"


@pytest.fixture(scope="module")
def runtime_module():
    spec = importlib.util.spec_from_file_location(
        "agent_runtime_under_test", PLUGIN_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(runtime_module):
    return runtime_module.AgentRuntimePlugin(kernel=None, manifest={})


def _result_sentinel(line: bytes) -> bool:
    return line.lstrip().startswith(b'{"type":"result"')


@pytest.mark.asyncio
async def test_early_exit_kills_after_grace_when_sentinel_seen(plugin):
    """Sentinel printed → grace elapses → process killed; we don't wait timeout."""
    code = (
        "import sys, time;"
        'sys.stdout.write(\'{"type":"result","subtype":"success"}\\n\');'
        "sys.stdout.flush();"
        "time.sleep(30)"
    )
    started = time.time()
    result = await plugin.run(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        timeout_s=20.0,
        early_exit_on_line=_result_sentinel,
        early_exit_grace_s=1.0,
    )
    elapsed = time.time() - started
    assert elapsed < 6.0, f"early-exit didn't kill subprocess (elapsed={elapsed:.1f}s)"
    assert result["timeout"] is False
    assert result["returncode"] != 0


@pytest.mark.asyncio
async def test_no_early_exit_when_predicate_does_not_match(plugin):
    """Non-sentinel output → process exits cleanly → rc 0, no early kill."""
    code = (
        "import sys;"
        'sys.stdout.write(\'{"type":"assistant"}\\n\');'
        "sys.stdout.flush()"
    )
    result = await plugin.run(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        timeout_s=10.0,
        early_exit_on_line=_result_sentinel,
        early_exit_grace_s=1.0,
    )
    assert result["returncode"] == 0
    assert result["timeout"] is False


@pytest.mark.asyncio
async def test_clean_exit_after_sentinel_within_grace(plugin):
    """Sentinel printed, then process exits on its own well inside grace →
    natural rc 0, no kill needed."""
    code = (
        "import sys;"
        'sys.stdout.write(\'{"type":"result","subtype":"success"}\\n\');'
        "sys.stdout.flush()"
    )
    result = await plugin.run(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        timeout_s=10.0,
        early_exit_on_line=_result_sentinel,
        early_exit_grace_s=5.0,
    )
    assert result["returncode"] == 0
    assert result["timeout"] is False


@pytest.mark.asyncio
async def test_anchored_predicate_ignores_embedded_sentinel(plugin):
    """A `tool_result` event whose body quotes `"type":"result"` must not
    trigger early-exit. The shipped predicate is line-anchored, so embedded
    occurrences inside other event types stay safe."""
    code = (
        "import sys;"
        # Imitates a tool_result whose body happens to quote the result key.
        'sys.stdout.write(\'{"type":"tool_result","content":"saw {\\\\"type\\\\":\\\\"result\\\\"}"}\\n\');'
        "sys.stdout.flush()"
    )
    started = time.time()
    result = await plugin.run(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        timeout_s=10.0,
        early_exit_on_line=_result_sentinel,
        early_exit_grace_s=1.0,
    )
    elapsed = time.time() - started
    # Process exits naturally; we should see rc 0 and elapsed dominated by
    # spawn time, not grace timer.
    assert result["returncode"] == 0
    assert result["timeout"] is False
    assert elapsed < 5.0
