"""Pure/unit coverage for Macro Studio's Windows plugin safety primitives."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import threading
import types

import pytest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


actuate = _load("_macro_actuate", "plugins/desktop-control/actuate.py")
uia = _load("_macro_uia", "plugins/desktop-control/uia.py")
pick = _load("_macro_pick", "plugins/screen-capture/pick.py")

# NOT loaded at module scope, unlike its three siblings above. This plugin
# imports `keyboard`, whose Darwin backend can SIGBUS at import -- a fatal
# signal, so a module-scope load here would abort collection of the ENTIRE
# suite (CI's first step is a bare `--collect-only`) instead of failing one
# test. Loading it lazily keeps a plugin regression reportable.
def _hotkey_import_is_safe() -> bool:
    """Probe the plugin import in a throwaway process.

    Answers "can this module be loaded in-process without killing us?" without
    finding out the hard way. Cached for the module's lifetime.
    """
    global _HOTKEY_SAFE
    if _HOTKEY_SAFE is None:
        code = (
            "import importlib.util, sys;"
            "s = importlib.util.spec_from_file_location('gh', sys.argv[1]);"
            "m = importlib.util.module_from_spec(s);"
            "sys.modules['gh'] = m;"
            "s.loader.exec_module(m)"
        )
        try:
            r = subprocess.run(
                [sys.executable, "-c", code,
                 str(REPO / "plugins/global-hotkey/plugin.py")],
                capture_output=True, timeout=60,
            )
            _HOTKEY_SAFE = r.returncode == 0
        except Exception:
            _HOTKEY_SAFE = False
    return _HOTKEY_SAFE


_HOTKEY_SAFE: bool | None = None

needs_importable_hotkey = pytest.mark.skipif(
    not _hotkey_import_is_safe(),
    reason="global-hotkey plugin is not importable in-process on this platform",
)


def _hotkey_module():
    mod = sys.modules.get("_macro_hotkey")
    if mod is None:
        mod = _load("_macro_hotkey", "plugins/global-hotkey/plugin.py")
    return mod


class TestDesktopControlHelpers:
    def test_target_match_is_process_exact_title_substring_and_case_insensitive(self):
        target = {"process": "NOTEpad", "title_contains": "Draft"}
        assert uia.target_matches({"proc": "notepad.exe", "title": "My DRAFT.txt"}, target)
        assert not uia.target_matches({"proc": "notepad-plus.exe", "title": "Draft"}, target)
        assert not uia.target_matches({"proc": "notepad.exe", "title": "Other"}, target)

    def test_relative_coordinate_conversion_tracks_moved_window(self):
        assert actuate.relative_point([100, 200, 500, 600], 12, 34) == (112, 234)
        assert actuate.relative_point([400, 500, 800, 900], 12, 34) == (412, 534)

    def test_relative_coordinate_refuses_outside_window(self):
        for point in ((-1, 0), (0, -1), (400, 0), (0, 400)):
            try:
                actuate.relative_point([0, 0, 400, 400], *point)
            except ValueError as exc:
                assert "outside" in str(exc)
            else:  # pragma: no cover - assertion clarity
                raise AssertionError("out-of-bounds point accepted")

    def test_unicode_preparation_preserves_bmp_and_surrogate_pairs(self):
        assert actuate.unicode_code_units("A中") == [0x0041, 0x4E2D]
        assert actuate.unicode_code_units("😀") == [0xD83D, 0xDE00]


class _FakeListener:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class TestPointPicker:
    def test_pick_is_relative_owner_scoped_and_one_shot(self, monkeypatch):
        async def run():
            monkeypatch.setattr(pick, "HAS_PYNPUT", True)
            monkeypatch.setattr(pick, "_mouse", types.SimpleNamespace(Listener=_FakeListener))

            async def foreground():
                return {
                    "proc": "notepad.exe",
                    "title": "Draft - Notepad",
                    "rect": [100, 200, 500, 600],
                }

            picker = pick.PointPicker(asyncio.get_running_loop(), foreground)
            started = picker.start(
                "operate:controller-1",
                {
                    "process": "notepad.exe",
                    "title_contains": "draft",
                },
            )
            assert started["status"] == "waiting"
            assert picker.status(owner="other", pick_id=started["pick_id"])["ok"] is False
            picker._on_click(125, 245, "Button.left", True)
            await asyncio.sleep(0.02)
            result = picker.status(owner="operate:controller-1", pick_id=started["pick_id"])
            assert result["status"] == "picked"
            assert result["point"] == {"x": 25, "y": 45}
            assert result["active"] is False

        asyncio.run(run())

    def test_target_mismatch_and_cancel_and_timeout(self, monkeypatch):
        async def run():
            monkeypatch.setattr(pick, "HAS_PYNPUT", True)
            monkeypatch.setattr(pick, "_mouse", types.SimpleNamespace(Listener=_FakeListener))

            async def foreground():
                return {"proc": "calc.exe", "title": "Calculator", "rect": [0, 0, 300, 300]}

            picker = pick.PointPicker(asyncio.get_running_loop(), foreground)
            target = {"process": "notepad.exe", "title_contains": "Draft"}
            first = picker.start("owner", target)
            picker._on_click(10, 10, "Button.left", True)
            await asyncio.sleep(0.02)
            assert (
                picker.status(owner="owner", pick_id=first["pick_id"])["error"] == "target_mismatch"
            )

            second = picker.start("owner", target)
            cancelled = picker.cancel(owner="owner", pick_id=second["pick_id"])
            assert cancelled["status"] == "cancelled"

            third = picker.start("owner", target, timeout_seconds=0.05)
            await asyncio.sleep(0.08)
            assert picker.status(owner="owner", pick_id=third["pick_id"])["status"] == "timeout"

        asyncio.run(run())


class _FakeKeyboard:
    def __init__(self):
        self.added = {}
        self.removed = []
        self._next = 0

    def add_hotkey(self, shortcut, callback, args):
        self._next += 1
        self.added[self._next] = (shortcut, callback, args)
        return self._next

    def remove_hotkey(self, handle):
        self.removed.append(handle)
        self.added.pop(handle, None)


class TestPluginImportIsProcessSafe:
    """The global-hotkey plugin must be importable on every platform.

    `keyboard`'s Darwin backend builds a KeyController at module scope, whose
    KeyMap calls Carbon CFDataGetBytes on a possibly-NULL layout -- SIGBUS, a
    fatal signal no `except ImportError` can catch. Because this test module
    imports the plugin at module scope, that crash aborted collection of the
    ENTIRE suite on macOS (`pytest --collect-only` died at exit 138), which is
    also CI's first step.

    Run in a SUBPROCESS on purpose: importing a module that may SIGBUS would
    kill the test runner rather than fail a test -- the very failure being
    pinned. A regression must be reportable, not fatal.
    """

    def test_plugin_imports_without_crashing_the_process(self):
        code = (
            "import importlib.util, sys;"
            "s = importlib.util.spec_from_file_location('gh', sys.argv[1]);"
            "m = importlib.util.module_from_spec(s);"
            "sys.modules['gh'] = m;"
            "s.loader.exec_module(m);"
            "print(m.HAS_KEYBOARD)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code, str(REPO / "plugins/global-hotkey/plugin.py")],
            capture_output=True, text=True, timeout=60,
        )
        # A fatal signal shows up as a negative returncode on POSIX (-10 = SIGBUS).
        assert proc.returncode == 0, (
            f"importing the plugin exited {proc.returncode} "
            f"(negative = killed by a signal): {proc.stderr[:400]}"
        )
        assert proc.stdout.strip() in {"True", "False"}

    def test_keyboard_is_not_imported_on_macos(self):
        """The gate is by platform, not by luck -- pin the darwin branch."""
        code = (
            "import sys; sys.platform = 'darwin';"
            "import importlib.util;"
            "s = importlib.util.spec_from_file_location('gh', sys.argv[1]);"
            "m = importlib.util.module_from_spec(s);"
            "sys.modules['gh'] = m;"
            "s.loader.exec_module(m);"
            "print(m.HAS_KEYBOARD, 'keyboard' in dir(m))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code, str(REPO / "plugins/global-hotkey/plugin.py")],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr[:400]
        assert proc.stdout.split() == ["False", "False"], (
            f"on darwin the plugin must report no keyboard and bind no module, "
            f"got {proc.stdout.strip()!r}"
        )


@needs_importable_hotkey
class TestOwnerScopedHotkeys:
    def _plugin(self, monkeypatch):
        fake = _FakeKeyboard()
        hotkey_module = _hotkey_module()
        monkeypatch.setattr(hotkey_module, "HAS_KEYBOARD", True)
        # raising=False: the plugin never imports `keyboard` on macOS (its Darwin
        # backend SIGBUSes at import), so the attribute is absent there. These
        # tests supply their own fake and set HAS_KEYBOARD themselves, so they
        # are platform-independent -- but only if injection tolerates the gap.
        monkeypatch.setattr(hotkey_module, "keyboard", fake, raising=False)
        plugin = hotkey_module.GlobalHotkeyPlugin.__new__(hotkey_module.GlobalHotkeyPlugin)
        plugin._loop = None
        plugin._bindings = {}
        plugin._shortcut_owners = {}
        plugin._bindings_lock = threading.RLock()
        return plugin, fake

    def test_unregistering_one_owner_preserves_existing_shortcut(self, monkeypatch):
        plugin, fake = self._plugin(monkeypatch)
        assert plugin.register_hotkey("default", "Ctrl + Space")["ok"]
        assert plugin.register_hotkey("macro", "Ctrl+Shift+F12")["ok"]
        assert len(plugin.bindings()) == 2
        assert plugin.unregister_owner("macro") == {
            "ok": True,
            "removed": True,
            "shortcut": "ctrl+shift+f12",
        }
        assert plugin.bindings() == [{"owner": "default", "shortcut": "ctrl+space"}]
        assert len(fake.added) == 1

    def test_shortcut_conflict_is_structured(self, monkeypatch):
        plugin, _ = self._plugin(monkeypatch)
        assert plugin.register_hotkey("first", "ctrl+shift+f12")["ok"]
        conflict = plugin.register_hotkey("second", "CTRL+SHIFT+F12")
        assert conflict["ok"] is False and conflict["conflict_owner"] == "first"
