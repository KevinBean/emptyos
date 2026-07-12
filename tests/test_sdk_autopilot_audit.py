"""Autopilot tamper-evident audit chain.

Validates the HMAC-SHA256 chain in `emptyos/sdk/autopilot.py`:
- Append → verify is green
- Hand-edit any entry → verify reports it as tampered
- Truncate the log → verify reports the broken chain at the new tail (n/a;
  truncation surfaces as a missing next link, but the existing entries
  still hash correctly — only a follow-up append-without-rebuild would
  detect it. We test the rotation-segment shape instead.)
- Key rotation → new entries verify under the new key; old entries break

Run: python -m pytest tests/test_sdk_autopilot_audit.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from emptyos.sdk.autopilot import (
    AUDIT_FILE,
    AUDIT_KEY_FILE,
    GENESIS_PREV,
    _canonical,
    append_audit,
    verify_audit,
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Fresh data root per test — autopilot subdir is lazily created."""
    return tmp_path


def _audit_path(data_dir: Path) -> Path:
    return data_dir / "autopilot" / AUDIT_FILE


def _read_lines(data_dir: Path) -> list[str]:
    return _audit_path(data_dir).read_text(encoding="utf-8").splitlines()


def test_empty_audit_verifies(data_dir):
    """No log file → ok, 0 lines."""
    r = verify_audit(data_dir)
    assert r["ok"] is True
    assert r["lines_checked"] == 0
    assert r["tampered"] == []


def test_single_append_verifies(data_dir):
    sig = append_audit(
        data_dir,
        actor={"type": "agent", "id": "test"},
        app="task", method="add", args={"text": "hello"},
        grant_id=None, ok=True,
    )
    assert len(sig) == 64  # hex sha256
    r = verify_audit(data_dir)
    assert r["ok"] is True
    assert r["lines_checked"] == 1
    assert r["tampered"] == []


def test_three_appends_chain_intact(data_dir):
    sigs = []
    for i in range(3):
        sigs.append(append_audit(
            data_dir,
            actor={"type": "agent", "id": "test"},
            app="task", method="add", args={"text": f"entry {i}"},
            grant_id=None, ok=True,
        ))
    assert len(set(sigs)) == 3  # all distinct
    r = verify_audit(data_dir)
    assert r["ok"] is True
    assert r["lines_checked"] == 3
    assert r["tampered"] == []


def test_chain_links_to_previous_hmac(data_dir):
    """Each entry's `prev` field equals the previous entry's `hmac`."""
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=True)
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=True)
    lines = _read_lines(data_dir)
    e0 = json.loads(lines[0])
    e1 = json.loads(lines[1])
    assert e0["prev"] == GENESIS_PREV
    assert e1["prev"] == e0["hmac"]


def test_tampered_middle_line_detected(data_dir):
    """Edit one byte of any entry — verify reports it tampered, chain
    breaks from there forward."""
    for i in range(3):
        append_audit(data_dir, actor={}, app="task", method="add",
                     args={"i": i}, grant_id=None, ok=True)
    path = _audit_path(data_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    # Flip a value in the middle entry's args
    e1 = json.loads(lines[1])
    e1["args"] = e1["args"].replace('"i": 1', '"i": 999')
    lines[1] = json.dumps(e1, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = verify_audit(data_dir)
    assert r["ok"] is False
    # Line 1 (the tampered one) AND line 2 (whose `prev` no longer matches
    # the broken line 1's hmac) both fail.
    assert 1 in r["tampered"]


def test_tampered_hmac_detected(data_dir):
    """Replacing just the hmac field also gets caught — the recomputed
    hmac won't match the forged value."""
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=True)
    path = _audit_path(data_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[0])
    e["hmac"] = "0" * 64
    lines[0] = json.dumps(e, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = verify_audit(data_dir)
    assert r["ok"] is False
    assert 0 in r["tampered"]


def test_corrupt_json_line_detected(data_dir):
    """A line that isn't valid JSON is also tampered."""
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=True)
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=True)
    path = _audit_path(data_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = "{not valid json"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = verify_audit(data_dir)
    assert r["ok"] is False
    assert 0 in r["tampered"]


def test_key_rotation_breaks_chain(data_dir):
    """Rotating the key mid-stream → old entries no longer verify."""
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={"text": "before rotation"}, grant_id=None, ok=True)
    # Rotate: delete the key, next append generates a new one
    key_path = data_dir / "autopilot" / AUDIT_KEY_FILE
    key_path.unlink()
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={"text": "after rotation"}, grant_id=None, ok=True)
    r = verify_audit(data_dir)
    # The pre-rotation entry can't be verified under the new key.
    assert r["ok"] is False
    assert 0 in r["tampered"]


def test_args_truncation_at_1000_chars(data_dir):
    """Args field is truncated to keep the log compact."""
    huge = {"blob": "x" * 5000}
    append_audit(data_dir, actor={}, app="task", method="add",
                 args=huge, grant_id=None, ok=True)
    lines = _read_lines(data_dir)
    e = json.loads(lines[0])
    assert e["args"].endswith("...[truncated]")
    assert len(e["args"]) < 1100  # 1000 + suffix
    # Chain still validates over the truncated form
    r = verify_audit(data_dir)
    assert r["ok"] is True


def test_canonical_json_stable(data_dir):
    """`_canonical` is deterministic — same input always yields same bytes,
    regardless of dict key insertion order."""
    a = _canonical({"b": 2, "a": 1, "c": {"y": 20, "x": 10}})
    b = _canonical({"a": 1, "c": {"x": 10, "y": 20}, "b": 2})
    assert a == b


def test_failed_action_still_audited(data_dir):
    """ok=False entries chain just like successes."""
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=False, error="timeout")
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=True)
    r = verify_audit(data_dir)
    assert r["ok"] is True
    assert r["lines_checked"] == 2


def test_key_file_persists_across_calls(data_dir):
    """Once generated, the audit key is reused on subsequent appends."""
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=True)
    key_path = data_dir / "autopilot" / AUDIT_KEY_FILE
    key_a = key_path.read_bytes()
    append_audit(data_dir, actor={}, app="task", method="add",
                 args={}, grant_id=None, ok=True)
    key_b = key_path.read_bytes()
    assert key_a == key_b
    assert len(key_a) == 32
