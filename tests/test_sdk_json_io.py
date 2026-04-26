"""Unit tests for emptyos.sdk.utils.load_json / save_json.

Pure tmp_path — no daemon required.
"""

from __future__ import annotations

from emptyos.sdk import load_json, save_json


def test_load_json_returns_default_when_missing(tmp_path):
    assert load_json(tmp_path / "nope.json", []) == []
    assert load_json(tmp_path / "nope.json", {}) == {}
    assert load_json(tmp_path / "nope.json", None) is None


def test_round_trip_list(tmp_path):
    p = tmp_path / "data.json"
    data = [{"a": 1}, {"b": "hello"}]
    save_json(p, data)
    assert load_json(p, []) == data


def test_round_trip_dict(tmp_path):
    p = tmp_path / "data.json"
    data = {"k": [1, 2, 3], "nested": {"x": "y"}}
    save_json(p, data)
    assert load_json(p, {}) == data


def test_save_json_utf8_preserves_non_ascii(tmp_path):
    p = tmp_path / "cn.json"
    save_json(p, [{"title": "你好世界"}])
    # Raw file should contain the literal chars, not \uXXXX escapes
    raw = p.read_text(encoding="utf-8")
    assert "你好世界" in raw
    assert "\\u" not in raw


def test_save_json_handles_non_serializable_via_default_str(tmp_path):
    from datetime import datetime
    p = tmp_path / "ts.json"
    save_json(p, {"at": datetime(2026, 1, 2, 3, 4, 5)})
    # Should not raise; datetime serialises via str()
    loaded = load_json(p, {})
    assert loaded["at"].startswith("2026-01-02")
