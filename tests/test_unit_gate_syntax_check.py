"""Unit tests for scripts/gate_syntax_check.py — the PostToolBatch syntax gate.

Pins both directions: it must block a broken edit, and must NEVER block a healthy
batch (a gate that false-positives halts the agent's loop, which is far more
disruptive than an advisory hook — so the silent-on-healthy direction is the more
important half here).

Payload shape is the GROUND TRUTH captured from a live PostToolBatch via
probe_hook_payload.py — `tool_calls[]` with a per-call `tool_response` (the docs
get this field name wrong).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
# The hook imports `hook_common`, which resolves at runtime because Python puts a
# script's own dir on sys.path[0]. Under importlib that doesn't happen, so add it.
sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "gate_syntax_check", _SCRIPTS / "gate_syntax_check.py"
)
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["gate_syntax_check"] = gate
_SPEC.loader.exec_module(gate)


def batch(*paths: str, tool: str = "Edit") -> dict:
    """A PostToolBatch payload in the real captured shape."""
    return {
        "hook_event_name": "PostToolBatch",
        "tool_calls": [
            {
                "tool_name": tool,
                "tool_input": {"file_path": p},
                "tool_use_id": f"toolu_{i}",
                "tool_response": "ok",
            }
            for i, p in enumerate(paths)
        ],
    }


# ── MUST BLOCK ──────────────────────────────────────────────────────────────

def test_blocks_broken_python(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def f(:\n    pass\n")
    bad = gate.audit(batch(str(f)))
    assert len(bad) == 1
    assert "SyntaxError" in bad[0][1]


def test_blocks_broken_json(tmp_path):
    f = tmp_path / "broken.json"
    f.write_text('{"a": 1,,}')
    bad = gate.audit(batch(str(f)))
    assert len(bad) == 1
    assert "JSONDecodeError" in bad[0][1]


def test_blocks_only_the_broken_file_in_a_mixed_batch(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\n")
    bad_f = tmp_path / "bad.py"
    bad_f.write_text("x = (\n")
    bad = gate.audit(batch(str(ok), str(bad_f)))
    assert [Path(p).name for p, _ in bad] == ["bad.py"]


def test_emits_block_json(tmp_path, monkeypatch, capsys):
    f = tmp_path / "broken.py"
    f.write_text("if True\n    pass\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(batch(str(f)))))
    assert gate.main() == 0  # fail-open: never a hard non-zero exit
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "broken.py" in out["reason"]


# ── MUST STAY SILENT ────────────────────────────────────────────────────────

def test_silent_on_valid_python(tmp_path):
    f = tmp_path / "good.py"
    f.write_text("from __future__ import annotations\n\n\ndef f() -> int:\n    return 1\n")
    assert gate.audit(batch(str(f))) == []


def test_silent_on_valid_json(tmp_path):
    f = tmp_path / "good.json"
    f.write_text('{"a": [1, 2], "b": {"c": null}}')
    assert gate.audit(batch(str(f))) == []


def test_silent_on_unchecked_extensions(tmp_path):
    for name, body in [("a.md", "# hi `x`"), ("b.html", "<p>"), ("c.toml", "x = ]")]:
        f = tmp_path / name
        f.write_text(body)
        assert gate.audit(batch(str(f))) == [], name


def test_silent_on_non_edit_tools(tmp_path):
    """A Bash/Read call in the batch must not be treated as an edit."""
    f = tmp_path / "broken.py"
    f.write_text("def f(:\n")
    assert gate.collect_paths(batch(str(f), tool="Read")) == []
    assert gate.audit(batch(str(f), tool="Bash")) == []


def test_silent_on_deleted_file(tmp_path):
    """Edited then moved/deleted before the hook ran — not an error."""
    assert gate.audit(batch(str(tmp_path / "gone.py"))) == []


def test_silent_on_empty_batch():
    assert gate.audit({"tool_calls": []}) == []
    assert gate.audit({}) == []


def test_dedupes_repeated_edits_to_one_file(tmp_path):
    """One entry per file, and paths come back forward-slashed — `hook_common.
    edited_paths` normalises Windows separators for us."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    assert gate.collect_paths(batch(str(f), str(f))) == [str(f).replace("\\", "/")]


def test_malformed_stdin_is_fail_open(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert gate.main() == 0
    assert capsys.readouterr().out == ""


def test_healthy_batch_prints_nothing(tmp_path, monkeypatch, capsys):
    f = tmp_path / "good.py"
    f.write_text("x = 1\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(batch(str(f)))))
    assert gate.main() == 0
    assert capsys.readouterr().out == ""


def test_real_repo_sources_all_pass(tmp_path):
    """The gate must be silent on this repo's own hook scripts — the healthy-tree
    calibration required by .claude/rules/audits.md before a checker may gate."""
    root = Path(__file__).resolve().parents[1]
    paths = [
        str(root / "scripts" / "guard_git_safety.py"),
        str(root / "scripts" / "check_gitignored_path.py"),
        str(root / "scripts" / "gate_syntax_check.py"),
        str(root / "scripts" / "probe_hook_payload.py"),
        str(root / ".claude" / "settings.json"),
    ]
    assert gate.audit(batch(*paths)) == []


def test_silent_on_empty_file(tmp_path):
    """An empty (or whitespace-only) file is not a syntax error worth halting
    the loop for — found during the healthy-tree calibration, where an empty
    `.tmp/active.json` was the sole non-artifact finding."""
    for name in ("empty.json", "empty.py"):
        f = tmp_path / name
        f.write_text("   \n")
        assert gate.audit(batch(str(f))) == [], name
