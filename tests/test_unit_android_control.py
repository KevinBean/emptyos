"""Unit tests for plugins/android-control/adb.py — pure helpers + the injection
guards added after the security review flagged command injection through
`adb shell` (adb re-joins argv into one on-device shell string). No daemon,
no device — pure functions + a monkeypatched `_run`."""
from __future__ import annotations

import importlib.util
import shlex
from pathlib import Path

import pytest

_ADB_PATH = Path(__file__).resolve().parents[1] / "plugins" / "android-control" / "adb.py"


def _load():
    spec = importlib.util.spec_from_file_location("android_adb", _ADB_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


adb = _load()


# ── keycode validation (injection guard) ──────────────────────────────
def test_keycode_known():
    assert adb._keycode("back") == "KEYCODE_BACK"
    assert adb._keycode("enter") == "KEYCODE_ENTER"
    assert adb._keycode("a") == "KEYCODE_A"
    assert adb._keycode("keycode_home") == "KEYCODE_HOME"


@pytest.mark.parametrize("evil", ["a; reboot", "$(reboot)", "a`id`", "a b", "a && rm -rf /"])
def test_keycode_rejects_injection(evil):
    assert adb._keycode(evil) == "KEYCODE_UNKNOWN"


# ── uiautomator XML parse ─────────────────────────────────────────────
def test_parse_ui_xml_basic():
    xml = ('<hierarchy><node class="android.widget.Button" text="OK" '
           'bounds="[0,0][100,50]" clickable="true" enabled="true"/></hierarchy>')
    els = adb.parse_ui_xml(xml)
    assert len(els) == 1
    e = els[0]
    assert e["ref"] == "e1" and e["role"] == "Button" and e["name"] == "OK"
    assert e["rect"] == [0, 0, 100, 50] and "clickable" in e["state"]


def test_parse_ui_xml_skips_zero_area_and_unnamed():
    xml = ('<node text="x" bounds="[0,0][0,0]"/>'          # zero area
           '<node class="V" bounds="[0,0][10,10]"/>')       # unnamed + not clickable
    assert adb.parse_ui_xml(xml) == []


def test_parse_ui_xml_caps_at_max():
    xml = "".join(f'<node text="t{i}" bounds="[0,0][10,10]"/>' for i in range(100))
    assert len(adb.parse_ui_xml(xml, max_elements=5)) == 5


# ── act() injection guards (monkeypatched _run captures the device cmd) ─
def _capture(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(adb, "_run", lambda a, s, args, **k: (calls.append(args), (0, "", ""))[1])
    return calls


def test_type_is_shell_quoted_not_injected(monkeypatch):
    calls = _capture(monkeypatch)
    evil = "a; touch /sdcard/PWNED #"
    r = adb.act("adb", "dev", {"op": "type", "text": evil})
    assert r["ok"]
    # ONE quoted device command — metachars neutralized, not split into a 2nd cmd
    assert calls[-1] == ["shell", "input text " + shlex.quote(evil)]


def test_type_strips_control_chars(monkeypatch):
    calls = _capture(monkeypatch)
    adb.act("adb", "dev", {"op": "type", "text": "a\nb\x00c"})
    assert "\n" not in calls[-1][1] and "\x00" not in calls[-1][1]


def test_click_maps_to_tap(monkeypatch):
    calls = _capture(monkeypatch)
    r = adb.act("adb", "dev", {"op": "click", "x": 10, "y": 20})
    assert r["ok"] and calls[-1] == ["shell", "input", "tap", "10", "20"]


def test_press_uses_validated_keycode(monkeypatch):
    calls = _capture(monkeypatch)
    adb.act("adb", "dev", {"op": "press", "key": "a; reboot"})
    assert calls[-1] == ["shell", "input", "keyevent", "KEYCODE_UNKNOWN"]


def test_unknown_op_is_refused():
    r = adb.act("adb", "dev", {"op": "explode"})
    assert r["ok"] is False and "unknown op" in r["error"]


# ── launch_app package validation (no _run call on bad input) ──────────
@pytest.mark.parametrize("bad", ["com.x; reboot", "a`reboot`", "; rm -rf /", "a b"])
def test_launch_rejects_bad_package(bad):
    r = adb.launch_app("adb", "dev", bad)
    assert r["ok"] is False and "invalid package" in r["error"]


def test_launch_rejects_empty_package():
    assert adb.launch_app("adb", "dev", "")["ok"] is False


# ── read_dark_flag: nested-vs-flat (the actuation-flag regression) ─────
def test_read_dark_flag_flat():
    assert adb.read_dark_flag({"feature.android-control.enabled": True}, "feature.android-control.enabled") is True
    assert adb.read_dark_flag({"feature.android-control.enabled": False}, "feature.android-control.enabled") is False


def test_read_dark_flag_nested():
    # TOML `feature.android-control.enabled = true` parses to nested tables.
    assert adb.read_dark_flag({"feature": {"android-control": {"enabled": True}}},
                              "feature.android-control.enabled") is True
    assert adb.read_dark_flag({"feature": {"android-control": {"enabled": False}}},
                              "feature.android-control.enabled") is False


def test_read_dark_flag_absent_or_bad():
    assert adb.read_dark_flag({}, "feature.android-control.enabled") is False
    assert adb.read_dark_flag({"feature": {}}, "feature.android-control.enabled") is False
    assert adb.read_dark_flag(None, "x") is False
    assert adb.read_dark_flag("notadict", "x") is False
