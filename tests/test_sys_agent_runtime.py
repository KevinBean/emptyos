"""Agent-runtime CLI adapter catalog.

Validates the shape of every entry in `DEFAULT_CLI_ADAPTERS` and exercises
the `{prompt}` substitution path in `text_cli_run`'s argv assembly without
actually spawning a subprocess (CLIs aren't installed in CI).

Run: python -m pytest tests/test_sys_agent_runtime.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_PATH = REPO / "plugins" / "agent-runtime" / "plugin.py"


@pytest.fixture(scope="module")
def runtime_module():
    spec = importlib.util.spec_from_file_location(
        "agent_runtime_under_test_catalog", PLUGIN_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _adapters(runtime_module):
    return runtime_module.DEFAULT_CLI_ADAPTERS


def test_catalog_is_non_empty(runtime_module):
    assert len(_adapters(runtime_module)) >= 2, "expected at least the seed CLIs"


@pytest.mark.parametrize(
    "cli_id", sorted(["codex", "gemini", "aider", "sgpt", "mods",
                      "aichat", "goose", "opencode"]),
)
def test_catalog_entry_shape(runtime_module, cli_id):
    """Each entry has the keys text_cli_run reads."""
    cfg = _adapters(runtime_module).get(cli_id)
    assert cfg is not None, f"{cli_id} missing from DEFAULT_CLI_ADAPTERS"
    assert isinstance(cfg.get("binary"), str) and cfg["binary"], \
        f"{cli_id}.binary must be a non-empty string"
    tmpl = cfg.get("args_template")
    assert isinstance(tmpl, list) and tmpl, \
        f"{cli_id}.args_template must be a non-empty list"
    for piece in tmpl:
        assert isinstance(piece, str), \
            f"{cli_id}.args_template pieces must be str (got {type(piece).__name__})"
    # supports_system is optional but if present must be bool
    if "supports_system" in cfg:
        assert isinstance(cfg["supports_system"], bool)


@pytest.mark.parametrize(
    "cli_id",
    sorted(["codex", "gemini", "aider", "sgpt", "mods",
            "aichat", "goose", "opencode"]),
)
def test_prompt_substitution_lands_in_argv(runtime_module, cli_id):
    """{prompt} placeholder must appear in at least one argv slot, and
    substitution must produce an argv that contains the prompt verbatim."""
    cfg = _adapters(runtime_module)[cli_id]
    tmpl = cfg["args_template"]
    assert any("{prompt}" in piece for piece in tmpl), \
        f"{cli_id}.args_template has no {{prompt}} slot — text_cli_run can't pass user input"

    # Simulate text_cli_run's substitution (plugin.py lines 420-426).
    test_prompt = "PROBE-PROMPT-12345"
    cmd = [cfg["binary"]]
    for piece in tmpl:
        try:
            cmd.append(piece.format(prompt=test_prompt, system=""))
        except (IndexError, KeyError):
            cmd.append(piece)
    # The prompt must appear verbatim somewhere in argv.
    assert any(test_prompt in part for part in cmd), \
        f"{cli_id}: prompt didn't land in argv — substitution broken (cmd={cmd!r})"


def test_resolve_cli_config_user_override_wins(runtime_module, monkeypatch):
    """`_resolve_cli_config` merges defaults with user emptyos.toml overrides;
    user keys win on collision."""
    # Build a minimal fake plugin instance. We bypass BasePlugin setup and
    # only stub the one method `_resolve_cli_config` reads.
    plugin = runtime_module.AgentRuntimePlugin.__new__(
        runtime_module.AgentRuntimePlugin
    )

    user_clis = {
        "aider": {"binary": "aider-edge", "args_template": ["--once", "{prompt}"]},
        "newcli": {"binary": "newcli", "args_template": ["{prompt}"]},
    }
    plugin.config = lambda key, default=None: user_clis if key == "clis" else default

    # Existing adapter — user override on `binary` and `args_template` wins.
    merged_aider = plugin._resolve_cli_config("aider")
    assert merged_aider["binary"] == "aider-edge"
    assert merged_aider["args_template"] == ["--once", "{prompt}"]
    # Keys the user didn't override come through from defaults.
    assert merged_aider.get("supports_system") is False

    # User-only CLI with no default — purely user config.
    merged_new = plugin._resolve_cli_config("newcli")
    assert merged_new["binary"] == "newcli"
    assert merged_new["args_template"] == ["{prompt}"]

    # CLI in neither defaults nor user config → empty.
    merged_missing = plugin._resolve_cli_config("notreal")
    assert merged_missing == {}
