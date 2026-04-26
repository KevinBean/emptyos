"""Unit tests for scripts/log_app_development.py.

The transcript format and the "big change" diff policy are brittle to format
drift. These tests pin the parsing and policy so regressions surface quickly.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "log_app_development.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("log_app_development", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["log_app_development"] = module
    spec.loader.exec_module(module)
    return module


def test_relpath_absolute_windows(mod, tmp_path):
    root = Path("D:/emptyos")
    assert mod._relpath(r"D:\emptyos\apps\foo\manifest.toml", root) == "apps/foo/manifest.toml"
    assert mod._relpath("D:/emptyos/apps/foo/manifest.toml", root) == "apps/foo/manifest.toml"


def test_relpath_already_relative(mod):
    assert mod._relpath("apps/foo/manifest.toml", Path("D:/emptyos")) == "apps/foo/manifest.toml"


def test_pick_trigger_prefers_eos_skill(mod):
    assert mod.pick_trigger(["/clear", "/eos-new-app"]) == "`/eos-new-app` skill"


def test_pick_trigger_skips_noise(mod):
    assert mod.pick_trigger(["/clear", "/compact", "/simplify"]) == "`/simplify` command"


def test_pick_trigger_all_noise_falls_back(mod):
    assert mod.pick_trigger(["/clear", "/compact"]) == "manual / direct tool use"


def test_pick_trigger_empty(mod):
    assert mod.pick_trigger([]) == "manual / direct tool use"


def test_diff_manifest_version_bump(mod):
    old = {"app": {"version": "1.0.0"}}
    new = {"app": {"version": "1.1.0"}}
    diff = mod.diff_manifest(old, new)
    assert any("1.0.0" in d and "1.1.0" in d for d in diff)


def test_diff_manifest_no_change(mod):
    m = {"app": {"version": "1.0.0"}, "requires": {"capabilities": ["read"]}}
    assert mod.diff_manifest(m, m) == []


def test_diff_manifest_capability_added(mod):
    old = {"requires": {"capabilities": ["read"]}}
    new = {"requires": {"capabilities": ["read", "speak"]}}
    diff = mod.diff_manifest(old, new)
    assert any("speak" in d and "capabilities" in d for d in diff)


def test_diff_manifest_event_emits_added(mod):
    old = {"provides": {"events": {"emits": ["foo:bar"]}}}
    new = {"provides": {"events": {"emits": ["foo:bar", "foo:baz"]}}}
    diff = mod.diff_manifest(old, new)
    assert any("foo:baz" in d for d in diff)


def test_diff_manifest_new_provides_block(mod):
    old = {"provides": {"events": {"emits": []}}}
    new = {"provides": {"events": {"emits": []}, "web": {"prefix": "/x"}}}
    diff = mod.diff_manifest(old, new)
    assert any("[provides.web]" in d for d in diff)


def test_diff_manifest_new_contributes_block(mod):
    old = {}
    new = {"contributes": {"hub": {"panel": "x"}}}
    diff = mod.diff_manifest(old, new)
    assert any("[contributes.hub]" in d for d in diff)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_parse_transcript_tokens_and_duration(mod, tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [
        {"type": "user", "timestamp": "2026-04-20T10:00:00.000Z",
         "message": {"content": "<command-name>/eos-new-app</command-name>"}},
        {"type": "assistant", "timestamp": "2026-04-20T10:05:00.000Z",
         "message": {
             "model": "claude-opus-4-7",
             "content": [
                 {"type": "tool_use", "id": "t1", "name": "Write",
                  "input": {"file_path": "D:/emptyos/apps/foo/manifest.toml", "content": "x"}},
             ],
             "usage": {"input_tokens": 100, "output_tokens": 50,
                       "cache_creation_input_tokens": 30, "cache_read_input_tokens": 20},
         }},
        {"type": "assistant", "timestamp": "2026-04-20T10:10:00.000Z",
         "message": {"model": "claude-opus-4-7",
                     "content": [],
                     "usage": {"input_tokens": 200, "output_tokens": 100,
                               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 500}}},
    ])
    r = mod.parse_transcript(transcript)
    assert r["duration_min"] == 10
    opus = r["tokens_by_model"]["claude-opus-4-7"]
    assert opus["input"] == 300
    assert opus["output"] == 150
    assert opus["cache_create"] == 30
    assert opus["cache_read"] == 520
    assert "/eos-new-app" in r["skills"]
    assert "D:/emptyos/apps/foo/manifest.toml" in r["files"]


def test_parse_transcript_agent_invocation(mod, tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "timestamp": "2026-04-20T10:00:00.000Z",
         "message": {"model": "claude-opus-4-7", "usage": {},
                     "content": [
                         {"type": "tool_use", "id": "agent1", "name": "Agent",
                          "input": {"subagent_type": "Explore",
                                    "prompt": "look at files"}},
                     ]}},
        {"type": "user", "timestamp": "2026-04-20T10:01:00.000Z",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "agent1", "content": "findings here"}
         ]}},
    ])
    r = mod.parse_transcript(transcript)
    assert "Explore" in r["agents"]
    assert r["agents"]["Explore"]["count"] == 1
    assert r["agents"]["Explore"]["prompt_chars"] > 0
    assert r["agents"]["Explore"]["result_chars"] > 0


def test_parse_transcript_handles_missing_file(mod, tmp_path):
    r = mod.parse_transcript(tmp_path / "does-not-exist.jsonl")
    assert r["duration_min"] == 0
    assert r["tokens_by_model"] == {}
    assert r["agents"] == {}


def test_parse_transcript_tolerates_corrupt_lines(mod, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "not json\n"
        + json.dumps({"type": "assistant", "timestamp": "2026-04-20T10:00:00.000Z",
                      "message": {"model": "m", "usage": {"input_tokens": 5}}}) + "\n"
        + "{ partial\n",
        encoding="utf-8",
    )
    r = mod.parse_transcript(transcript)
    assert r["tokens_by_model"]["m"]["input"] == 5
