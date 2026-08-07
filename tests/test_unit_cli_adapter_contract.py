"""Contract tests for DEFAULT_CLI_ADAPTERS entries. Pure — no daemon, no spawn.

These pin invariants that are *silent* when broken. A prompt truncated by a
.CMD shim still exits 0 and still produces a fluent reply — it just answers a
question nobody asked — so nothing downstream fails loudly enough to notice.
Measured 2026-07-31: codex received a prompt of exactly "[System]" and
answered "What would you like me to work on in EmptyOS?".
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def plugin_mod():
    spec = importlib.util.spec_from_file_location(
        "ar_plugin_contract", REPO / "plugins" / "agent-runtime" / "plugin.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ar_plugin_contract"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_codex_prompt_is_not_passed_on_argv(plugin_mod):
    """The prompt must ride stdin, because `codex` resolves to codex.CMD on
    Windows and cmd.exe cuts a multi-line argument at its first newline."""
    cx = plugin_mod.DEFAULT_CLI_ADAPTERS["codex"]
    assert cx.get("prompt_via_stdin") is True
    assert "{prompt}" not in cx["args_template"], (
        "a multi-line prompt on argv is truncated by the .CMD shim"
    )
    assert cx["args_template"][-1] == "-", "`-` is what tells codex to read stdin"


def test_codex_stream_json_flag_matches_its_args(plugin_mod):
    """`--json` and `stream_json` are a matched pair. Setting one without the
    other either loses the tool cards or prints raw JSONL at the user."""
    cx = plugin_mod.DEFAULT_CLI_ADAPTERS["codex"]
    assert ("--json" in cx["args_template"]) == bool(cx.get("stream_json"))


def test_codex_pins_a_read_only_flag(plugin_mod):
    """Without it codex inherits sandbox_mode from ~/.codex/config.toml,
    which is workspace-write."""
    args = plugin_mod.DEFAULT_CLI_ADAPTERS["codex"]["args_template"]
    assert "-s" in args and "read-only" in args


def test_writes_unsandboxed_is_declared_for_codex_on_windows(plugin_mod):
    """The rooms cwd guard keys off this. It is a known-bad list, so it must
    be True exactly where the escape was measured — win32."""
    cx = plugin_mod.DEFAULT_CLI_ADAPTERS["codex"]
    assert cx.get("writes_unsandboxed") is (sys.platform == "win32")


def test_run_accepts_stdin_data(plugin_mod):
    """text_cli_run's stdin routing depends on this parameter existing."""
    sig = inspect.signature(plugin_mod.AgentRuntimePlugin.run)
    assert "stdin_data" in sig.parameters


def test_stdin_adapters_do_not_also_template_the_prompt(plugin_mod):
    """General invariant across every adapter, not just codex: sending the
    prompt twice (argv AND stdin) would duplicate it into the model."""
    for cli_id, cfg in plugin_mod.DEFAULT_CLI_ADAPTERS.items():
        if cfg.get("prompt_via_stdin"):
            assert "{prompt}" not in cfg.get("args_template", []), cli_id


def test_no_adapter_puts_a_multiline_slot_on_argv_for_a_shim_cli(plugin_mod):
    """`pi` installs as a .cmd shim, so BOTH of its old argv slots were being
    truncated — including `--append-system-prompt`, which looks like the
    correct channel for a system prompt and is not an escape from this bug.

    Kept narrow deliberately: this asserts the two adapters whose binaries were
    actually measured as shims. The rest are unverified, not proven safe.
    """
    for cli_id in ("codex", "pi"):
        cfg = plugin_mod.DEFAULT_CLI_ADAPTERS[cli_id]
        args = cfg.get("args_template", [])
        assert cfg.get("prompt_via_stdin") is True, cli_id
        assert not any("{prompt}" in a for a in args), f"{cli_id}: prompt on argv"
        assert not any("{system}" in a for a in args), f"{cli_id}: system on argv"
        assert cfg.get("supports_system") is not True, (
            f"{cli_id}: supports_system would route the system prompt back to argv"
        )
