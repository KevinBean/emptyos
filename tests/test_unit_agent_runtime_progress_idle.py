"""Agent-runtime `run()` progress-based idle watchdog.

The default idle watchdog measures stdout-line silence — too generous for
claude-cli, which streams assistant-text / heartbeat tokens even when wedged
in a retry loop. `run()` accepts a `progress_predicate`; when set, the idle
timer resets only on lines that match the predicate (treated as real
progress), while non-matching lines are still drained normally.

These tests drive `run()` against tiny python subprocesses that simulate
both shapes: chatty stdout without progress (should hit idle), and chatty
stdout with intermittent progress (should not).
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
        "agent_runtime_progress_under_test", PLUGIN_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(runtime_module):
    return runtime_module.AgentRuntimePlugin(kernel=None, manifest={})


def _tool_progress(line: bytes) -> bool:
    return b'"type":"tool_use"' in line or b'"type":"tool_result"' in line


@pytest.mark.asyncio
async def test_chatty_stdout_without_progress_hits_idle(plugin):
    """Subprocess prints assistant-text every 0.2s for 30s but never emits a
    tool_use/tool_result. With `progress_predicate` set, idle watchdog must
    fire well before the wall timeout."""
    code = (
        "import sys, time\n"
        "for _ in range(150):\n"
        "    sys.stdout.write('{\"type\":\"assistant\",\"text\":\"thinking...\"}\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.2)\n"
    )
    started = time.time()
    result = await plugin.run(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        timeout_s=60.0,
        idle_timeout_s=12.0,
        progress_predicate=_tool_progress,
    )
    elapsed = time.time() - started
    # 12s idle ceiling + 10s watchdog poll cadence + a couple seconds of
    # spawn/kill slack.
    assert result["idle_timeout"] is True, f"idle didn't fire (result={result})"
    assert result["timeout"] is False
    assert elapsed < 30.0, f"idle fired too late (elapsed={elapsed:.1f}s)"


@pytest.mark.asyncio
async def test_intermittent_progress_keeps_alive(plugin):
    """Subprocess prints assistant-text every 0.2s AND a tool_use every 5s.
    Idle watchdog (12s ceiling) must NOT fire — each tool_use resets the
    progress anchor. Process exits naturally with rc 0."""
    code = (
        "import sys, time\n"
        "deadline = time.time() + 20.0\n"
        "next_tool = time.time() + 5.0\n"
        "while time.time() < deadline:\n"
        "    sys.stdout.write('{\"type\":\"assistant\",\"text\":\"...\"}\\n')\n"
        "    sys.stdout.flush()\n"
        "    if time.time() >= next_tool:\n"
        "        sys.stdout.write('{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"tool_use\",\"name\":\"Bash\"}]}}\\n')\n"
        "        sys.stdout.flush()\n"
        "        next_tool = time.time() + 5.0\n"
        "    time.sleep(0.2)\n"
    )
    result = await plugin.run(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        timeout_s=60.0,
        idle_timeout_s=12.0,
        progress_predicate=_tool_progress,
    )
    assert result["idle_timeout"] is False, f"idle wrongly fired (result={result})"
    assert result["timeout"] is False
    assert result["returncode"] == 0


@pytest.mark.asyncio
async def test_no_predicate_falls_back_to_stdout_idle(plugin):
    """Without `progress_predicate`, idle watchdog measures any stdout line.
    Chatty-without-progress subprocess should NOT hit idle — backward
    compatibility for text_cli_run and other existing callers."""
    code = (
        "import sys, time\n"
        "deadline = time.time() + 20.0\n"
        "while time.time() < deadline:\n"
        "    sys.stdout.write('chatter\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.2)\n"
    )
    result = await plugin.run(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        timeout_s=60.0,
        idle_timeout_s=12.0,
        # progress_predicate intentionally omitted
    )
    assert result["idle_timeout"] is False
    assert result["timeout"] is False
    assert result["returncode"] == 0
