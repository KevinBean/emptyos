"""The shell's global hotkey — the parsing half, and the shape of the thread.

``RegisterHotKey`` cannot be exercised without a Windows message loop, so what
is pinned here is everything that decides *what gets registered*: the spelling
a user writes in ``emptyos.toml``, the refusal of a combination that would take
a bare key away from the whole session, and that a failed registration reports
rather than leaving a dead hotkey behind. The press itself is a manual window
check (see the plan's Track A checklist).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "products"))

from _shared import hotkey_win as hk  # noqa: E402


# ── parse_shortcut ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("spelling", [
    "ctrl+space", "Ctrl+Space", "CTRL+SPACE", " ctrl + space ", "control+space",
])
def test_the_same_combination_survives_every_spelling(spelling):
    """These are the spellings ``plugins/global-hotkey``'s normalize_shortcut
    accepts, and a user moving the shortcut between the plugin and [desktop]
    should not have to learn a second vocabulary."""
    assert hk.parse_shortcut(spelling) == (hk.MOD_CONTROL | hk.MOD_NOREPEAT, 0x20)


@pytest.mark.parametrize("alias,bit", [
    ("option", hk.MOD_ALT), ("alt", hk.MOD_ALT),
    ("win", hk.MOD_WIN), ("windows", hk.MOD_WIN), ("super", hk.MOD_WIN),
    ("command", hk.MOD_WIN), ("cmd", hk.MOD_WIN),
    ("shift", hk.MOD_SHIFT),
])
def test_modifier_aliases(alias, bit):
    mods, vk = hk.parse_shortcut(f"{alias}+k")
    assert mods == bit | hk.MOD_NOREPEAT and vk == ord("K")


def test_modifiers_combine():
    mods, vk = hk.parse_shortcut("ctrl+shift+alt+win+j")
    assert mods == (hk.MOD_CONTROL | hk.MOD_SHIFT | hk.MOD_ALT | hk.MOD_WIN | hk.MOD_NOREPEAT)
    assert vk == ord("J")


@pytest.mark.parametrize("key,vk", [
    ("a", 0x41), ("z", 0x5A), ("0", 0x30), ("9", 0x39),
    ("space", 0x20), ("enter", 0x0D), ("return", 0x0D), ("tab", 0x09),
    ("esc", 0x1B), ("escape", 0x1B), ("delete", 0x2E), ("home", 0x24),
    ("left", 0x25), ("up", 0x26), ("right", 0x27), ("down", 0x28),
    ("f1", 0x70), ("f12", 0x7B), ("f24", 0x87),
    ("`", 0xC0), ("-", 0xBD), ("/", 0xBF), (";", 0xBA),
])
def test_key_codes(key, vk):
    assert hk.parse_shortcut(f"ctrl+{key}")[1] == vk


def test_no_repeat_is_always_set():
    """Without MOD_NOREPEAT, HOLDING the combination repeats at the keyboard's
    repeat rate and a toggling window flickers open and closed while the keys
    are down. It is not a tunable — every parse carries it."""
    for spelling in ("ctrl+space", "alt+f1", "ctrl+shift+/"):
        assert hk.parse_shortcut(spelling)[0] & hk.MOD_NOREPEAT


@pytest.mark.parametrize("bad", ["space", "a", "f1", "ctrl", "shift+", "+", "",
                                 "a+b", "space+k", "ctrl", "alt"])
def test_a_combination_with_no_modifier_is_refused(bad):
    """RegisterHotKey on a plain key reserves it process-wide for the whole
    session: `space` would take the space bar away from every other program
    until the shell exits. No configuration is worth that, so the parser will
    not produce one.

    Asserted as a PROPERTY over the whole input space rather than against one
    branch, because no single branch owns it: the single-token cases are refused
    by the length check and the multi-key ones by the two-keys check, and the
    explicit `not mods` guard is unreachable behind them (see its comment)."""
    assert hk.parse_shortcut(bad) is None


def test_nothing_that_parses_is_missing_a_modifier():
    """The same property, stated the other way round, so a refactor that moves
    the refusal between branches still has something to satisfy."""
    mod_bits = hk.MOD_CONTROL | hk.MOD_ALT | hk.MOD_SHIFT | hk.MOD_WIN
    for a in ("", "ctrl", "alt", "shift", "win", "a", "space", "f4", "nope"):
        for b in ("", "ctrl", "alt", "a", "space", "f4", "nope"):
            got = hk.parse_shortcut(f"{a}+{b}")
            if got is not None:
                assert got[0] & mod_bits, f"{a}+{b} parsed with no modifier"


@pytest.mark.parametrize("bad", [
    "ctrl+space+k",          # two real keys
    "ctrl+nope",             # not a key name
    "ctrl+f0", "ctrl+f25",   # outside VK_F1..VK_F24
    "ctrl+ab",               # not a single character
    None, 7, ["ctrl", "k"],
])
def test_unusable_spellings_are_refused(bad):
    assert hk.parse_shortcut(bad) is None


def test_modifier_order_does_not_matter():
    assert hk.parse_shortcut("shift+ctrl+k") == hk.parse_shortcut("ctrl+shift+k")


# ── HotkeyThread ─────────────────────────────────────────────────────────────
def test_an_unusable_shortcut_fails_before_touching_the_os(monkeypatch):
    """Pinned with the platform forced to win32. `start()` checks the platform
    BEFORE parsing, so off Windows the error is "Windows-only" and the
    modifier assertion is false — this file went red on the Mac dev box while
    CI (which only collects it) stayed green."""
    monkeypatch.setattr(hk.sys, "platform", "win32")
    hkt = hk.HotkeyThread("space", lambda: None)
    assert hkt.start() is False
    assert "modifier" in (hkt.error or "")
    hkt.stop()          # must be safe on a thread that never started


@pytest.mark.parametrize("empty", ["", None, "   "])
def test_an_empty_shortcut_never_becomes_a_default_one(empty, monkeypatch):
    """Empty is how the whole feature says "off". A fallback here would register
    Ctrl+Space for a user who turned quick entry off — and would be a second
    place deciding the default, which is where config defaults drift from.

    Forced to win32 for the same reason as above: off Windows `start()` returns
    False before ever reaching the parser, so this passed whether or not the
    fallback it forbids existed."""
    monkeypatch.setattr(hk.sys, "platform", "win32")
    hkt = hk.HotkeyThread(empty, lambda: None)
    assert hkt.shortcut == empty
    assert hkt.start() is False
    hkt.stop()


def test_off_windows_it_declines_rather_than_raising(monkeypatch):
    """The shell runs on Windows today, but nothing here may raise at *start*
    time on another platform."""
    monkeypatch.setattr(hk.sys, "platform", "darwin")
    hkt = hk.HotkeyThread("ctrl+space", lambda: None)
    assert hkt.start() is False
    assert "Windows-only" in (hkt.error or "")


def test_no_windows_only_module_is_imported_at_module_scope():
    """The IMPORT must also survive another platform, and the test above cannot
    prove it: it patches `sys.platform` only after this module has already
    imported successfully here. Checked statically instead, because the failure
    is a *collection* error — and CI's first step is a bare `--collect-only`
    where one of those fails the entire suite (`.claude/rules/testing.md`).

    `ctypes.wintypes` is the specific trap: its `VARIANT_BOOL` uses the type
    code "v", which exists only in the Windows build of `_ctypes`, so importing
    it on POSIX raises ValueError rather than ImportError.
    """
    banned = {"ctypes.wintypes", "winreg", "msvcrt", "_winapi", "win32api", "win32con"}
    src = Path(hk.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:                      # module scope only, not nested
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        assert not (set(names) & banned), f"module-scope import of {set(names) & banned}"


def test_a_failing_handler_does_not_end_the_loop():
    """A registered-but-dead hotkey is the worst outcome: the combination stays
    reserved (so nothing else can take it) and nothing happens when pressed."""
    hkt = hk.HotkeyThread("ctrl+space", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    logged = []
    hkt._log = logged.append
    hkt._dispatch()     # the loop body, without the loop
    assert logged and "handler failed" in logged[0]


def test_stop_is_safe_before_start_and_twice():
    hkt = hk.HotkeyThread("ctrl+space", lambda: None)
    hkt.stop()
    hkt.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey is Windows-only")
def test_it_really_registers_and_releases_on_windows():
    """The one end-to-end pin available without a keypress: an obscure
    combination registers, a SECOND thread on the same combination is refused
    (that is what a reservation means), and releasing frees it again."""
    pressed = []
    first = hk.HotkeyThread("ctrl+shift+alt+f24", lambda: pressed.append(1))
    assert first.start() is True, first.error
    try:
        second = hk.HotkeyThread("ctrl+shift+alt+f24", lambda: None)
        assert second.start() is False
        assert "already held" in (second.error or "")
        second.stop()
    finally:
        first.stop()
    again = hk.HotkeyThread("ctrl+shift+alt+f24", lambda: None)
    assert again.start() is True, "the combination was not released"
    again.stop()
