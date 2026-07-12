"""Pure unit tests for emptyos/sdk/prompt_registry.py — no daemon needed.

Run: python -m pytest tests/test_sdk_prompt_registry.py -v
"""

from __future__ import annotations

import json
import os

import pytest

from emptyos.sdk import prompt_registry as pr


PLAIN = "You are a task classifier. Output ONLY a JSON object - no prose."
TEMPLATE = "Classify this request in one pass.\n\nRequest: {user_text}\nContext: {context}"
JSON_EXAMPLE = 'Reply as {"reply": "...", "actions": []} - nothing else.'
DOUBLED = 'Return JSON like {{"kind": "code"}} for request {user_text}.'


@pytest.fixture(autouse=True)
def clean_registry():
    # Snapshot-and-restore: other test modules import real apps' prompts.py,
    # whose declare_prompts() entries live in the same module-global registry —
    # clearing without restoring would break them in a shared pytest session.
    saved = dict(pr._REGISTRY)
    saved_state = (pr._DATA_DIR, pr._CACHE, pr._CORRUPT)
    pr.reset_for_tests()
    yield
    pr.reset_for_tests()
    pr._REGISTRY.update(saved)
    pr._DATA_DIR, pr._CACHE, pr._CORRUPT = saved_state


@pytest.fixture
def opath(tmp_path):
    return tmp_path / "prompts" / "overrides.json"


def _write(path, overrides: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "overrides": overrides}), encoding="utf-8"
    )


class TestPlaceholders:
    def test_named_fields(self):
        assert pr.extract_placeholders(TEMPLATE) == frozenset({"user_text", "context"})

    def test_plain_text(self):
        assert pr.extract_placeholders(PLAIN) == frozenset()

    def test_doubled_braces_not_fields(self):
        assert pr.extract_placeholders(DOUBLED) == frozenset({"user_text"})

    def test_json_pseudo_fields_filtered(self):
        # {"reply": ...} parses but the field name isn't an identifier.
        got = pr.extract_placeholders(JSON_EXAMPLE)
        assert got is None or got == frozenset()

    def test_stray_brace_is_none(self):
        assert pr.extract_placeholders("broken { example") is None


class TestDeclareAndResolve:
    def test_declare_registers_and_resolves_default(self):
        prompts = pr.declare_prompts("agent", classify_system=PLAIN)
        assert prompts.classify_system == PLAIN  # byte-identical, unconfigured
        assert prompts["classify_system"] == PLAIN
        assert [e.key for e in pr.entries()] == ["agent.classify_system"]
        assert pr.entries()[0].constant == "CLASSIFY_SYSTEM"
        assert pr.apps() == {"agent"}

    def test_unknown_attribute_raises(self):
        prompts = pr.declare_prompts("agent", classify_system=PLAIN)
        with pytest.raises(AttributeError):
            _ = prompts.nope

    def test_non_string_default_rejected(self):
        with pytest.raises(TypeError):
            pr.declare_prompts("agent", bad=123)  # type: ignore[arg-type]

    def test_redeclare_overwrites(self):
        pr.declare_prompts("agent", classify_system="v1")
        pr.declare_prompts("agent", classify_system="v2")
        assert len(pr.entries("agent")) == 1
        assert pr.resolve("agent", "classify_system") == "v2"

    def test_resolve_undeclared_without_default_raises(self):
        with pytest.raises(KeyError):
            pr.resolve("agent", "missing")

    def test_resolve_missing_file_returns_default(self, opath):
        pr.declare_prompts("agent", classify_system=PLAIN)
        assert pr.resolve("agent", "classify_system", path=opath) == PLAIN

    def test_resolve_corrupt_file_fails_open(self, opath):
        pr.declare_prompts("agent", classify_system=PLAIN)
        opath.parent.mkdir(parents=True)
        opath.write_text("{not json", encoding="utf-8")
        assert pr.resolve("agent", "classify_system", path=opath) == PLAIN
        assert pr.sweep(opath)["corrupt"]

    def test_override_wins(self, opath):
        pr.declare_prompts("agent", classify_system=PLAIN)
        _write(opath, {"agent.classify_system": {"text": "MY VOICE"}})
        assert pr.resolve("agent", "classify_system", path=opath) == "MY VOICE"

    def test_template_override_with_matching_placeholders_wins(self, opath):
        pr.declare_prompts("agent", classify_prompt=TEMPLATE)
        _write(opath, {"agent.classify_prompt": {"text": "Q: {user_text} | C: {context}"}})
        assert pr.resolve("agent", "classify_prompt", path=opath).startswith("Q:")

    def test_template_override_missing_placeholder_falls_back(self, opath):
        pr.declare_prompts("agent", classify_prompt=TEMPLATE)
        _write(opath, {"agent.classify_prompt": {"text": "only {user_text} here"}})
        assert pr.resolve("agent", "classify_prompt", path=opath) == TEMPLATE

    def test_plain_default_allows_free_text_override(self, opath):
        pr.declare_prompts("publish", chat_system=JSON_EXAMPLE)
        _write(opath, {"publish.chat_system": {"text": "any free text"}})
        assert pr.resolve("publish", "chat_system", path=opath) == "any free text"


class TestConfiguredCache:
    def test_mtime_reload(self, tmp_path):
        pr.declare_prompts("agent", classify_system=PLAIN)
        pr.configure(tmp_path)
        opath = pr.overrides_path()
        assert pr.resolve("agent", "classify_system") == PLAIN
        _write(opath, {"agent.classify_system": {"text": "v1"}})
        assert pr.resolve("agent", "classify_system") == "v1"
        # rewrite with a bumped mtime -> cache invalidates
        _write(opath, {"agent.classify_system": {"text": "v2"}})
        os.utime(opath, ns=(os.stat(opath).st_mtime_ns + 10_000_000,) * 2)
        assert pr.resolve("agent", "classify_system") == "v2"

    def test_unconfigured_overrides_path_raises(self):
        with pytest.raises(RuntimeError):
            pr.overrides_path()


class TestSetClear:
    def test_set_and_clear_roundtrip(self, opath):
        pr.declare_prompts("agent", classify_system=PLAIN)
        res = pr.set_override("agent.classify_system", "tuned", path=opath, note="test")
        assert res["ok"]
        data = json.loads(opath.read_text(encoding="utf-8"))
        assert data["version"] == 1
        rec = data["overrides"]["agent.classify_system"]
        assert rec["text"] == "tuned" and rec["note"] == "test" and rec["updated"]
        assert pr.resolve("agent", "classify_system", path=opath) == "tuned"
        assert pr.clear_override("agent.classify_system", path=opath) is True
        assert pr.clear_override("agent.classify_system", path=opath) is False
        assert pr.resolve("agent", "classify_system", path=opath) == PLAIN

    def test_set_unregistered_key_rejected(self, opath):
        res = pr.set_override("nope.nothing", "x", path=opath)
        assert not res["ok"] and "not registered" in res["error"]

    def test_set_empty_text_rejected(self, opath):
        pr.declare_prompts("agent", classify_system=PLAIN)
        assert not pr.set_override("agent.classify_system", "   ", path=opath)["ok"]

    def test_set_placeholder_mismatch_rejected_with_expected(self, opath):
        pr.declare_prompts("agent", classify_prompt=TEMPLATE)
        res = pr.set_override("agent.classify_prompt", "no fields", path=opath)
        assert not res["ok"]
        assert res["expected"] == ["context", "user_text"]

    def test_atomic_write_leaves_no_tmp(self, opath):
        pr.declare_prompts("agent", classify_system=PLAIN)
        pr.set_override("agent.classify_system", "tuned", path=opath)
        assert [p.name for p in opath.parent.iterdir()] == ["overrides.json"]


class TestSweepAndStatus:
    def test_sweep_finds_orphans_and_stale(self, opath):
        pr.declare_prompts("agent", classify_prompt=TEMPLATE)
        _write(opath, {
            "agent.classify_prompt": {"text": "missing fields"},   # stale
            "ghost.gone": {"text": "orphan"},                       # orphan
        })
        rep = pr.sweep(opath)
        assert rep["orphans"] == ["ghost.gone"]
        assert rep["stale"] == ["agent.classify_prompt"]
        assert rep["registered"] == 1 and rep["overridden"] == 2

    def test_override_status_marks_stale(self, opath):
        pr.declare_prompts("agent", classify_prompt=TEMPLATE, classify_system=PLAIN)
        _write(opath, {
            "agent.classify_prompt": {"text": "missing fields", "updated": "t"},
            "agent.classify_system": {"text": "fine", "updated": "t"},
        })
        st = pr.override_status(opath)
        assert st["agent.classify_prompt"]["stale"] is True
        assert st["agent.classify_system"]["active"] is True
