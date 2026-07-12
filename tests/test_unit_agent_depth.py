"""Sub-agent depth guard (agent-runtime, OpenClaw borrow 2026-06-11).

Every agent-runtime spawn stamps EOS_AGENT_DEPTH=<depth+1> into the child's
env; a spawn attempted at depth >= max_agent_depth is refused before exec.
These tests drive the pure `agent_depth_guard` helper plus both spawn
choke-points (`run` and `spawn_detached`) against the depth env var. No
daemon required.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_PATH = REPO / "plugins" / "agent-runtime" / "plugin.py"


@pytest.fixture(scope="module")
def runtime_module():
    spec = importlib.util.spec_from_file_location(
        "agent_runtime_depth_under_test", PLUGIN_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(runtime_module):
    return runtime_module.AgentRuntimePlugin(kernel=None, manifest={})


# ── Pure guard ──────────────────────────────────────────────────────────────

class TestAgentDepthGuard:
    def test_depth_zero_passes_and_stamps_child(self, runtime_module, monkeypatch):
        monkeypatch.delenv(runtime_module.AGENT_DEPTH_ENV, raising=False)
        env = {}
        assert runtime_module.agent_depth_guard(env, 3) is None
        assert env[runtime_module.AGENT_DEPTH_ENV] == "1"

    def test_below_cap_passes_and_increments(self, runtime_module, monkeypatch):
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "2")
        env = {}
        assert runtime_module.agent_depth_guard(env, 3) is None
        assert env[runtime_module.AGENT_DEPTH_ENV] == "3"

    def test_at_cap_refuses(self, runtime_module, monkeypatch):
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "3")
        env = {}
        msg = runtime_module.agent_depth_guard(env, 3)
        assert msg and "agent depth limit" in msg
        # Refusal must not stamp — nothing is being spawned.
        assert runtime_module.AGENT_DEPTH_ENV not in env

    def test_over_cap_refuses(self, runtime_module, monkeypatch):
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "7")
        assert runtime_module.agent_depth_guard({}, 3)

    def test_cap_zero_disables_but_still_stamps(self, runtime_module, monkeypatch):
        """max_agent_depth = 0 disables refusal, but depth keeps being stamped
        so a deeper hop with the cap re-enabled still knows where it is."""
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "9")
        env = {}
        assert runtime_module.agent_depth_guard(env, 0) is None
        assert env[runtime_module.AGENT_DEPTH_ENV] == "10"

    def test_garbage_env_value_treated_as_zero(self, runtime_module, monkeypatch):
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "banana")
        env = {}
        assert runtime_module.agent_depth_guard(env, 3) is None
        assert env[runtime_module.AGENT_DEPTH_ENV] == "1"

    def test_negative_env_value_clamped(self, runtime_module, monkeypatch):
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "-5")
        env = {}
        assert runtime_module.agent_depth_guard(env, 3) is None
        assert env[runtime_module.AGENT_DEPTH_ENV] == "1"


# ── Config accessor ─────────────────────────────────────────────────────────

class TestMaxAgentDepthConfig:
    def test_default(self, runtime_module, plugin):
        assert plugin._max_agent_depth() == runtime_module.DEFAULT_MAX_AGENT_DEPTH

    def test_override(self, plugin):
        plugin._config["max_agent_depth"] = 5
        assert plugin._max_agent_depth() == 5

    def test_explicit_zero_disables(self, plugin):
        plugin._config["max_agent_depth"] = 0
        assert plugin._max_agent_depth() == 0

    def test_unparseable_falls_back_to_default(self, runtime_module, plugin):
        plugin._config["max_agent_depth"] = "lots"
        assert plugin._max_agent_depth() == runtime_module.DEFAULT_MAX_AGENT_DEPTH


# ── Choke-points ────────────────────────────────────────────────────────────

class TestSpawnRefusal:
    @pytest.mark.asyncio
    async def test_run_refuses_at_cap(self, runtime_module, plugin, monkeypatch):
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "3")
        result = await plugin.run(
            [sys.executable, "-c", "print('SPAWNED')"], cwd=str(REPO)
        )
        assert result["returncode"] == -1
        assert "agent depth limit" in result["error"]
        assert result["duration_s"] == 0.0

    @pytest.mark.asyncio
    async def test_run_passes_below_cap_and_child_sees_depth(
        self, runtime_module, plugin, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "1")
        out = tmp_path / "depth.out"
        code = (
            "import os, sys\n"
            f"sys.stdout.write(os.environ.get('{runtime_module.AGENT_DEPTH_ENV}', 'MISSING'))\n"
        )
        result = await plugin.run(
            [sys.executable, "-u", "-c", code], cwd=str(REPO), stdout_path=out
        )
        assert result["returncode"] == 0
        assert out.read_text().strip() == "2"

    def test_spawn_detached_refuses_at_cap(
        self, runtime_module, plugin, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "3")
        spawn = plugin.spawn_detached(
            [sys.executable, "-c", "print('SPAWNED')"],
            cwd=str(REPO),
            stdout_path=tmp_path / "x.out",
            stderr_path=tmp_path / "x.err",
        )
        assert "error" in spawn and "agent depth limit" in spawn["error"]
        assert "pid" not in spawn

    @pytest.mark.asyncio
    async def test_caller_env_dict_not_mutated(
        self, runtime_module, plugin, monkeypatch, tmp_path
    ):
        """A caller-supplied env= dict must come back untouched — the depth
        stamp and env_drop pops apply to an internal copy only."""
        monkeypatch.delenv(runtime_module.AGENT_DEPTH_ENV, raising=False)
        caller_env = dict(os.environ)
        caller_env["DROPME"] = "1"
        before = dict(caller_env)
        result = await plugin.run(
            [sys.executable, "-c", "pass"],
            cwd=str(REPO),
            env=caller_env,
            env_drop=["DROPME"],
        )
        assert result["returncode"] == 0
        assert caller_env == before  # no depth stamp, no popped key

    @pytest.mark.asyncio
    async def test_text_cli_run_surfaces_refusal_as_error(
        self, runtime_module, plugin, monkeypatch
    ):
        """text_cli_run returns the standard {error} shape on a depth refusal,
        matching its existing not-on-PATH refusal contract."""
        monkeypatch.setenv(runtime_module.AGENT_DEPTH_ENV, "3")
        # Use a binary guaranteed on PATH (python) via a custom adapter so the
        # not-on-PATH branch doesn't mask the depth branch.
        plugin._config["clis"] = {
            "fake": {"binary": sys.executable, "args_template": ["-c", "print(1)"]}
        }
        result = await plugin.text_cli_run(cli_id="fake", prompt="hi", cwd=str(REPO))
        assert "agent depth limit" in result.get("error", "")
