"""Desktop-shell tray presence: the one signal the shell and the daemon's tray share.

The shell holds a per-port named mutex while its tray icon runs;
``emptyos.desktop_presence`` names it and probes it. These tests pin that the
probe sees a real held mutex and stops seeing it once released, that a running
shell *without* a tray does not count, and that the daemon's system-tray plugin
stands down exactly while a shell tray is up — through its real ``setup=`` wiring.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "products"))

from _shared import shell_core as sc  # noqa: E402
from _shared import single_instance as si  # noqa: E402

from emptyos import desktop_presence as dp  # noqa: E402

pytestmark = pytest.mark.unit
win_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 named mutex")

# A port nothing on this machine uses, so a real shell on :9000 never interferes.
PORT = 59317


@win_only
def test_probe_sees_a_held_tray_mutex_and_its_release():
    assert dp.shell_tray_present(PORT) is False
    lock = si.InstanceLock.acquire(dp.tray_presence_name(PORT))   # what the shell claims with its tray
    assert lock is not None
    try:
        assert dp.shell_tray_present(PORT) is True
        assert dp.shell_tray_present(PORT + 1) is False              # per-port
    finally:
        lock.release()
    assert dp.shell_tray_present(PORT) is False                      # released → absent


@win_only
def test_a_running_shell_without_a_tray_does_not_count():
    """The single-instance lock is held by every running shell; it must not be
    the signal, or a tray-less shell would leave the user with zero tray icons."""
    assert sc.instance_name(PORT) != dp.tray_presence_name(PORT)
    lock = si.InstanceLock.acquire(sc.instance_name(PORT))
    try:
        assert dp.shell_tray_present(PORT) is False
    finally:
        lock.release()


@pytest.mark.parametrize("handle,error,present", [
    (1234, 0, True),     # opened
    (0, 5, True),        # ERROR_ACCESS_DENIED: it exists, at another integrity level
    (0, 2, False),       # ERROR_FILE_NOT_FOUND: no shell tray
    (0, 0, False),
])
def test_open_result_meaning(handle, error, present):
    assert dp.presence_from_open(handle, error) is present


def test_presence_module_is_stdlib_only():
    """Imported by the daemon's plugins AND the shell's own venv — it must pull in
    neither emptyos.sdk (daemon deps) nor anything third-party."""
    tree = ast.parse((REPO / "emptyos" / "desktop_presence.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names <= {"__future__", "sys", "ctypes"}, names


# ── the daemon's system-tray plugin stands down ──────────────────────────────
def _tray_plugin():
    spec = importlib.util.spec_from_file_location("_eos_system_tray", REPO / "plugins" / "system-tray" / "plugin.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_eos_system_tray"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Icon:
    visible = True


def _plugin(mod, *, defer=True):
    kernel = types.SimpleNamespace(config=types.SimpleNamespace(get=lambda k, d=None: 9000 if k == "network.port" else d))
    p = mod.SystemTrayPlugin(kernel, {})
    p.config = lambda key, default=None: defer if key == "defer_to_shell" else default
    return p


def test_tray_hides_while_a_shell_tray_is_up_and_returns_after(monkeypatch):
    mod = _tray_plugin()
    present = {"v": False}
    monkeypatch.setattr(mod, "shell_tray_present", lambda port: present["v"] and port == 9000)
    p, icon = _plugin(mod), _Icon()
    p._sync_visibility(icon)
    assert icon.visible is True
    present["v"] = True
    p._sync_visibility(icon)
    assert icon.visible is False                 # shell tray up → the daemon's tray steps aside
    present["v"] = False
    p._sync_visibility(icon)
    assert icon.visible is True                  # shell tray gone (crash included) → back


def test_tray_defer_can_be_switched_off(monkeypatch):
    mod = _tray_plugin()
    monkeypatch.setattr(mod, "shell_tray_present", lambda port: True)
    p, icon = _plugin(mod, defer=False), _Icon()
    p._sync_visibility(icon)
    assert icon.visible is True


def test_a_broken_probe_never_makes_the_tray_vanish(monkeypatch):
    mod = _tray_plugin()

    def boom(port):
        raise OSError("probe failed")

    monkeypatch.setattr(mod, "shell_tray_present", boom)
    p, icon = _plugin(mod), _Icon()
    icon.visible = False

    def once(timeout):
        p._stop.set()          # run the loop body exactly once

    p._stop.wait = once
    p._visibility_loop(icon)
    assert icon.visible is True


def test_the_icon_actually_runs_the_visibility_loop(monkeypatch):
    """The wiring, not the helper: pystray must be started with setup= pointing at
    the loop, or the whole stand-down silently never happens."""
    mod = _tray_plugin()
    seen = {}

    class FakeIcon:
        def __init__(self, *a, **k):
            pass

        def run(self, setup=None):
            seen["setup"] = setup

    fake = types.SimpleNamespace(Icon=FakeIcon, Menu=lambda *a: a, MenuItem=lambda *a, **k: a)
    fake.Menu.SEPARATOR = None
    monkeypatch.setattr(mod, "pystray", fake, raising=False)
    monkeypatch.setattr(mod.SystemTrayPlugin, "_create_image", lambda self: None)
    p = _plugin(mod)
    p._run_tray()
    assert seen["setup"] == p._visibility_loop


def test_disconnect_stops_the_loop():
    mod = _tray_plugin()
    p = _plugin(mod)
    asyncio.run(p.disconnect())
    assert p._stop.is_set()


# ── the daemon's command-launcher stands down (quick entry) ──────────────────
@win_only
def test_probe_sees_a_held_quick_mutex_independently_of_the_tray():
    """The two stand-downs are unrelated: a shell may have a tray and no hotkey
    (the shortcut was taken) or a hotkey and no tray (no pystray). One mutex for
    both would make each plugin step aside for the other plugin's reason."""
    assert dp.shell_quick_present(PORT) is False
    lock = si.InstanceLock.acquire(dp.quick_presence_name(PORT))
    assert lock is not None
    try:
        assert dp.shell_quick_present(PORT) is True
        assert dp.shell_tray_present(PORT) is False      # the tray mutex is a different name
        assert dp.shell_quick_present(PORT + 1) is False  # per-port
    finally:
        lock.release()
    assert dp.shell_quick_present(PORT) is False


def _launcher_plugin():
    spec = importlib.util.spec_from_file_location(
        "_eos_command_launcher", REPO / "plugins" / "command-launcher" / "plugin.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_eos_command_launcher"] = mod
    spec.loader.exec_module(mod)
    return mod


def _launcher(mod, *, defer=True, port=9000):
    kernel = types.SimpleNamespace(events=types.SimpleNamespace(on=lambda *a: None))
    p = mod.CommandLauncherPlugin(kernel, {})
    p.config = lambda key, default=None: (
        defer if key == "defer_to_shell" else (port if key == "port" else default))
    p._browser = ("chrome.exe", "Chrome")
    p.spawned, p.closed = [], []
    p._spawn_window = lambda: p.spawned.append(1)
    p._close_window = lambda: p.closed.append(1)
    return p


def _presence(monkeypatch, value):
    """Patch what the plugin's late import resolves to, not the plugin."""
    monkeypatch.setattr(dp, "shell_quick_present", lambda port: value and port == 9000)


def test_launcher_spawns_when_no_shell_holds_the_hotkey(monkeypatch):
    mod = _launcher_plugin()
    _presence(monkeypatch, False)
    p = _launcher(mod)
    p._window_alive = lambda: False
    asyncio.run(p._on_hotkey({}))
    assert p.spawned == [1]


def test_launcher_stands_down_while_a_shell_holds_the_hotkey(monkeypatch):
    """Nothing can reserve a key combination away from the `keyboard` package's
    low-level hook, so BOTH listeners fire on one press. Standing down has to be
    the launcher declining to act — not the event failing to arrive."""
    mod = _launcher_plugin()
    _presence(monkeypatch, True)
    p = _launcher(mod)
    p._window_alive = lambda: False
    asyncio.run(p._on_hotkey({}))
    assert p.spawned == []


def test_a_window_open_before_the_shell_arrived_is_not_stranded(monkeypatch):
    """Otherwise the toggle that would close it has stood down, and a borderless
    Chrome window with no title bar is left with no way to dismiss it."""
    mod = _launcher_plugin()
    _presence(monkeypatch, True)
    p = _launcher(mod)
    p._window_alive = lambda: True
    asyncio.run(p._on_hotkey({}))
    assert p.closed == [1] and p.spawned == []


def test_launcher_defer_can_be_switched_off(monkeypatch):
    mod = _launcher_plugin()
    _presence(monkeypatch, True)
    p = _launcher(mod, defer=False)
    p._window_alive = lambda: False
    asyncio.run(p._on_hotkey({}))
    assert p.spawned == [1]


def test_a_broken_probe_never_costs_the_user_their_launcher(monkeypatch):
    mod = _launcher_plugin()

    def boom(port):
        raise OSError("probe failed")

    monkeypatch.setattr(dp, "shell_quick_present", boom)
    p = _launcher(mod)
    p._window_alive = lambda: False
    asyncio.run(p._on_hotkey({}))
    assert p.spawned == [1]


def test_deference_is_checked_per_press_not_cached(monkeypatch):
    """The shell comes and goes independently of the daemon, and its mutex is
    released even on a crash — so a cached answer would leave the launcher dead
    until the next daemon restart.

    Driven, not grepped. The previous version asserted that the string
    "shell_quick_present" appeared in the method's source — which the import
    line alone satisfies — and could not observe a cache at all, the one
    property it is named for. `.claude/rules/audits.md`: for the grep shape the
    fix is to stop reading the file and execute the handler.
    """
    mod = _launcher_plugin()
    answers = iter([True, False])
    probed = []

    def probe(port):
        probed.append(port)
        return next(answers)

    monkeypatch.setattr(dp, "shell_quick_present", probe)
    p = _launcher(mod)
    p._window_alive = lambda: False

    # Press 1: the shell holds the shortcut, so the launcher stands down.
    asyncio.run(p._on_hotkey({}))
    assert p.spawned == []

    # Press 2: the shell is gone. A cached True would spawn nothing here and
    # leave the user with no launcher at all until the daemon restarts.
    asyncio.run(p._on_hotkey({}))
    assert p.spawned == [1]
    assert len(probed) == 2, "the mutex must be re-probed on every press"
