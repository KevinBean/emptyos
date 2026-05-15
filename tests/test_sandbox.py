"""Unit tests for emptyos.sdk.sandbox.SandboxedWrite.

Pure, no daemon required — uses tempfile.TemporaryDirectory for both the
vault root and the sandbox root. CI-safe.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from emptyos.sdk.sandbox import SandboxedWrite, StaleSandbox, load_sandbox


def _make(tmpdir, rel_path: str, content: str, action_id: str = "act-test123"):
    vault = Path(tmpdir) / "vault"
    sandbox_root = Path(tmpdir) / "sandboxes"
    vault.mkdir(parents=True, exist_ok=True)
    return SandboxedWrite(
        action_id=action_id,
        vault_root=vault,
        rel_path=rel_path,
        content=content,
        sandbox_root=sandbox_root,
    )


def test_diff_new_file_is_all_adds():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/new.md", "hello\nworld\n")
        sw.capture()
        lines = sw.diff_lines()
        kinds = [l["kind"] for l in lines]
        assert "add" in kinds
        assert "del" not in kinds
        # The hunk marker may appear once; every non-hunk, non-ctx row is an add.
        assert all(l["kind"] in ("add", "hunk") for l in lines)


def test_diff_existing_file_shows_add_and_del():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/edit.md", "line one\nline TWO\nline three\n")
        sw.target.parent.mkdir(parents=True, exist_ok=True)
        sw.target.write_text("line one\nline two\nline three\n", encoding="utf-8")
        sw.capture()
        lines = sw.diff_lines()
        kinds = {l["kind"] for l in lines}
        assert "add" in kinds
        assert "del" in kinds


def test_diff_noop_is_empty():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/same.md", "unchanged\n")
        sw.target.parent.mkdir(parents=True, exist_ok=True)
        sw.target.write_text("unchanged\n", encoding="utf-8")
        sw.capture()
        assert sw.diff_lines() == []


def test_apply_creates_new_file():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/created.md", "fresh content\n")
        sw.capture()
        out = sw.apply()
        assert out == sw.target
        assert sw.target.read_text(encoding="utf-8") == "fresh content\n"


def test_apply_overwrites_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/exist.md", "after content\n")
        sw.target.parent.mkdir(parents=True, exist_ok=True)
        sw.target.write_text("before content\n", encoding="utf-8")
        sw.capture()
        sw.apply()
        assert sw.target.read_text(encoding="utf-8") == "after content\n"


def test_apply_stale_raises_when_vault_changed_after_capture():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/race.md", "agent's proposed\n")
        sw.target.parent.mkdir(parents=True, exist_ok=True)
        sw.target.write_text("original\n", encoding="utf-8")
        sw.capture()
        # User edits vault directly between capture and apply.
        sw.target.write_text("user edited mid-review\n", encoding="utf-8")
        with pytest.raises(StaleSandbox):
            sw.apply()
        # Vault content is untouched by the failed apply.
        assert sw.target.read_text(encoding="utf-8") == "user edited mid-review\n"


def test_apply_stale_raises_when_new_file_was_created_externally():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/squat.md", "agent's content\n")
        # Capture says "file doesn't exist yet"
        sw.capture()
        # But someone creates it before we apply.
        sw.target.parent.mkdir(parents=True, exist_ok=True)
        sw.target.write_text("someone got there first\n", encoding="utf-8")
        with pytest.raises(StaleSandbox):
            sw.apply()
        assert sw.target.read_text(encoding="utf-8") == "someone got there first\n"


def test_discard_removes_sandbox_dir():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/throwaway.md", "doesn't matter\n")
        sw.capture()
        assert sw.dir.exists()
        sw.discard()
        assert not sw.dir.exists()


def test_discard_is_safe_on_missing_dir():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/never.md", "x\n")
        # No capture; dir doesn't exist. Should not raise.
        sw.discard()


def test_path_traversal_outside_vault_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError):
            _make(tmp, "../escape.md", "evil\n")


def test_absolute_rel_path_is_rejected_when_outside_vault():
    with tempfile.TemporaryDirectory() as tmp:
        # An absolute path that doesn't live under vault_root.
        outside = Path(tmp) / "not_vault" / "x.md"
        with pytest.raises(ValueError):
            _make(tmp, str(outside), "leak\n")


def test_load_sandbox_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/roundtrip.md", "carry me\n", action_id="act-rt001")
        sw.capture()
        loaded = load_sandbox("act-rt001", sw.sandbox_root)
        assert loaded is not None
        assert loaded.rel_path == "notes/roundtrip.md"
        assert loaded._after_text() == "carry me\n"


def test_load_sandbox_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        out = load_sandbox("act-nope", Path(tmp))
        assert out is None


def test_capture_twice_overwrites_proposed_content():
    """A CLI may regenerate the same action; the latest capture wins."""
    with tempfile.TemporaryDirectory() as tmp:
        sw = _make(tmp, "notes/regen.md", "first draft\n")
        sw.capture()
        sw2 = _make(tmp, "notes/regen.md", "second draft\n")
        sw2.capture()
        assert sw2._after_text() == "second draft\n"
        # The shared action_id means dir is the same; check on disk.
        assert (sw2.dir / "after").read_text(encoding="utf-8") == "second draft\n"
