"""Unit tests for the EmptyOS Desktop native shell (products/_shared/shell*.py).

Daemon-free and GUI-free: ``shell.py`` imports ``webview`` only inside
``AttachShell.run``, so the shell's decisions are driven here through a fake
window. What only a real window shows (focus, WebView2 behaviour) is the
checklist in the session plan ``_plans/eos-desktop-gui.md`` (A1).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED = REPO_ROOT / "products" / "_shared"
sys.path.insert(0, str(REPO_ROOT / "products"))

from _shared import shell as sh  # noqa: E402
from _shared import shell_core as sc  # noqa: E402

pytestmark = pytest.mark.unit


def _load_eos_desktop():
    spec = importlib.util.spec_from_file_location("eos_desktop", REPO_ROOT / "scripts" / "eos_desktop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── same-origin path gate ────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/portal/", "/journal/", "/", "/portal/#agent:abc",
                                  "/kb/?q=a:b", "/code/?embed=1"])
def test_safe_local_path_accepts(path):
    assert sc.safe_local_path(path) == path


@pytest.mark.parametrize("path", [
    "//evil.example/", "/\\evil.example", "http://evil.example/", "javascript:alert(1)",
    "portal/", "", None, 42, "/a\x00b", "/" + "a" * 2048,
    "/x:y/", "C:/Program Files/Git/journal/",
    "/a\r\nb", "/a\tb", "/a\x7fb", "/a\x85b",   # C0, DEL and C1 controls, no colon to hide behind
])
def test_safe_local_path_rejects(path):
    assert sc.safe_local_path(path) is None


def test_start_url_never_carries_a_token_and_falls_back():
    assert sc.start_url(9000) == "http://127.0.0.1:9000/portal/"
    assert sc.start_url(9000, "/journal/") == "http://127.0.0.1:9000/journal/"
    assert sc.start_url(9000, "//evil/") == "http://127.0.0.1:9000/portal/"
    assert "token" not in sc.start_url(9000, "/x/").lower()


@pytest.mark.parametrize("url,ok", [
    ("http://127.0.0.1:9000/portal/", True), ("http://127.0.0.1:9000", True),
    ("http://127.0.0.1:9000?x=1", True), ("http://127.0.0.1:90001/", False),
    ("http://127.0.0.1:9001/", False), ("https://127.0.0.1:9000/", False),
    ("http://localhost:9000/", False), ("about:blank", False), (None, False),
])
def test_is_daemon_url(url, ok):
    assert sc.is_daemon_url(url, 9000) is ok


# ── second-launch IPC contract ───────────────────────────────────────────────
def test_validate_ipc():
    assert sc.validate_ipc({"cmd": "show"}) == {"cmd": "show"}
    assert sc.validate_ipc({"cmd": "show", "path": "/kb/"}) == {"cmd": "show", "path": "/kb/"}
    assert sc.validate_ipc({"cmd": "show", "path": None}) == {"cmd": "show"}
    # a bad path drops the whole message, never half-honours it
    assert sc.validate_ipc({"cmd": "show", "path": "//evil/"}) is None
    for bad in ({"cmd": "quit"}, {"cmd": "restart_daemon"}, "show", None, [], {}):
        assert sc.validate_ipc(bad) is None


def test_instance_and_pipe_names_are_per_port():
    assert sc.instance_name(9000) != sc.instance_name(9002)
    assert sc.pipe_address(9000) != sc.pipe_address(9002)
    assert sc.instance_name(9000).startswith("Local\\")
    assert sc.pipe_address(9000).startswith("\\\\.\\pipe\\")


# ── health ───────────────────────────────────────────────────────────────────
def test_is_up_requires_status_ok():
    assert sc.is_up({"status": "ok", "apps": 233})
    assert not sc.is_up({"status": "starting", "apps": 0})  # answers, loaded nothing
    assert not sc.is_up(None)
    assert not sc.is_up({"apps": 3})


def test_health_watch_down_at_launch_is_immediate():
    w = sc.HealthWatch()
    assert w.observe(False) == "went_down"
    assert w.observe(False) is None


def test_health_watch_needs_sustained_misses():
    w = sc.HealthWatch(misses_to_down=3)
    assert w.observe(True) == "came_up"
    assert w.observe(False) is None
    assert w.observe(False) is None          # two slow probes keep the page
    assert w.observe(True) is None           # ...and the miss count resets
    assert [w.observe(False) for _ in range(3)] == [None, None, "went_down"]
    assert w.observe(True) == "came_up"


def test_default_watch_is_three_misses():
    # debugging.md: short probes misfire on a live daemon — never one slow answer
    assert sc.MISSES_TO_DOWN >= 3 and sc.PROBE_TIMEOUT_S >= 5.0


def test_port_listening_against_a_real_socket():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert sc.port_listening(port) is True
    finally:
        srv.close()
    assert sc.port_listening(port) is False     # closed → refused


def test_start_allowed_only_on_splash_with_nothing_listening():
    assert sc.start_allowed(on_splash=True, listening=False)
    assert not sc.start_allowed(on_splash=True, listening=True)    # booting / slow / wedged
    assert not sc.start_allowed(on_splash=True, listening=None)    # unknown is not a no
    assert not sc.start_allowed(on_splash=False, listening=False)  # called from a page, not the splash


@pytest.mark.parametrize("exe,ok", [
    (r"C:\Python313\pythonw.exe", True), (r"C:\venv\Scripts\pythonw.exe", True),
    (r"C:\Python313\python.exe", False), (r"C:\venv\Scripts\PYTHON.EXE", False),
    ("/usr/bin/python3", True),
])
def test_can_offer_start(exe, ok):
    assert sc.can_offer_start(exe) is ok


def test_splash_button_only_when_nothing_listening():
    assert 'id="start"' in sc.splash_html(9000, listening=False, can_start=True)
    assert 'id="start"' not in sc.splash_html(9000, listening=True, can_start=True)
    assert 'id="start"' not in sc.splash_html(9000, listening=None, can_start=True)
    assert 'id="start"' not in sc.splash_html(9000, listening=False, can_start=False)
    assert 'id="start"' not in sc.splash_html(9000, listening=False, can_start=True, starting=True)
    assert "may still be starting" in sc.splash_html(9000, listening=True, can_start=True)


# ── the shell's interpreter survives restart.bat ─────────────────────────────
def test_default_interpreter_is_pythonw_and_restart_bat_spares_it():
    """Both halves of one invariant: the default shell interpreter is pythonw,
    and nothing in restart.bat targets pythonw."""
    assert sc.default_desktop_python("C:/L").name.lower() == "pythonw.exe"
    assert sc.can_offer_start(str(sc.default_desktop_python("C:/L")))
    bat = (REPO_ROOT / "restart.bat").read_text(encoding="utf-8", errors="replace").lower()
    code = [ln for ln in bat.splitlines() if not ln.strip().startswith(("rem", "::"))]
    assert any("taskkill" in ln and "python.exe" in ln for ln in code), \
        "restart.bat no longer kills python.exe by image name — revisit can_offer_start"
    assert not any("pythonw" in ln for ln in code), "restart.bat now touches pythonw — it would kill the shell"


def test_resolve_desktop_python(tmp_path):
    exe = tmp_path / "pythonw.exe"
    exe.write_bytes(b"")
    assert sc.resolve_desktop_python({"EOS_DESKTOP_PYTHON": str(exe)}) == exe
    assert sc.resolve_desktop_python({"EOS_DESKTOP_PYTHON": str(tmp_path / "nope.exe")}) is None
    assert sc.resolve_desktop_python({"LOCALAPPDATA": str(tmp_path)}) is None
    venv = tmp_path / "eos" / "envs" / "desktop-3.13" / "Scripts"
    venv.mkdir(parents=True)
    (venv / "pythonw.exe").write_bytes(b"")
    assert sc.resolve_desktop_python({"LOCALAPPDATA": str(tmp_path)}) == venv / "pythonw.exe"


# ── import discipline: the shell runs from a venv without the daemon's deps ─
def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


@pytest.mark.parametrize("mod", ["shell_core.py", "single_instance.py", "shell.py", "tray.py",
                                 "hotkey_win.py"])
def test_shell_modules_never_import_emptyos_sdk(mod):
    bad = {n for n in _imports(SHARED / mod) if n == "emptyos.sdk" or n.startswith("emptyos.sdk.")}
    assert not bad, f"{mod} imports {bad}; the desktop venv cannot run emptyos/sdk/__init__"


def test_import_scan_sees_from_package_form(tmp_path):
    probe = tmp_path / "p.py"
    probe.write_text("from emptyos import sdk\n", encoding="utf-8")
    assert "emptyos.sdk" in _imports(probe)


def test_only_shell_py_imports_webview():
    # hotkey_win.py's own docstring claims "Nothing here imports webview either" —
    # the claim was unchecked until it joined this list.
    for mod in ("shell_core.py", "single_instance.py", "hotkey_win.py"):
        assert "webview" not in _imports(SHARED / mod)
    top_level = {n.names[0].name for n in ast.parse((SHARED / "shell.py").read_text(encoding="utf-8")).body
                 if isinstance(n, ast.Import)}
    assert "webview" not in top_level, "webview must stay a lazy import so tests (and the daemon env) can load shell.py"


# ── the page ↔ shell bridge ──────────────────────────────────────────────────
def _pywebview_resolve(functions: dict, js_api: object, func_name: str):
    """pywebview 6.2.1's dispatch, verbatim in effect (webview/util.py
    ``js_bridge_call``): exposed functions by exact name, else a plain getattr
    walk from js_api — no ``_`` filter, dunders included."""
    func = functions.get(func_name)
    if func is not None:
        return func
    obj = js_api
    for attr in func_name.split("."):
        obj = getattr(obj, attr, None)
        if obj is None:
            return None
    return obj


ATTACK_PATHS = [
    "_shell", "_shell._start_daemon_fn", "_shell.log.path.write_text",
    "_shell.watch.observe", "start_daemon.__func__.__globals__.update",
    "start_daemon.__closure__", "shell_info.__globals__.update",
    "__class__.__init__.__globals__.update", "__class__.__base__.__subclasses__",
    "__dict__", "__reduce_ex__", "__getattribute__",
]


@pytest.mark.parametrize("path", ATTACK_PATHS)
def test_bridge_walks_reach_nothing(path):
    shell = _shell()
    functions = {f.__name__: f for f in shell.exposed()}
    assert _pywebview_resolve(functions, sh._InertRoot(), path) is None


def test_bridge_publishes_exactly_the_declared_names():
    """The published set and the guard's allowlist are one list, not two that
    happen to agree: a name exposed but not allowed is dead, and a name allowed
    but not exposed means the guard is vouching for something that isn't there.

    Per window, too. The guard narrows the quick verbs to the quick window and
    Start to the splash; publishing everything to both windows would make that
    narrowing the ONLY thing keeping them apart."""
    shell = _shell()
    main = {f.__name__ for f in shell.exposed()}
    quick = {f.__name__ for f in shell.exposed("quick")}
    assert main == {"shell_info", "start_daemon"}
    assert quick == {"shell_info", "hide", "open_main", "set_quick_height"}
    assert main | quick == set(sc.BRIDGE_NAMES)
    assert main & quick == {"shell_info"}, "only the read-only verb is shared"
    functions = {f.__name__: f for f in shell.exposed()}
    assert _pywebview_resolve(functions, sh._InertRoot(), "shell_info")()["mode"] == "attach"
    assert dir(sh._InertRoot()) == []   # pywebview's own listing publishes nothing from the root


def test_each_window_is_handed_only_its_own_verbs(monkeypatch, tmp_path):
    """The wiring: `exposed()` can split by role and still be called with the
    default for both windows."""
    wv, ec, calls = _fake_webview(monkeypatch)
    published = []

    made = []

    class Win(FakeWindow):
        def __init__(self):
            super().__init__()
            # A real pywebview Window carries `uid`, and `run()` copies the
            # quick window's into `_quick_uid` — the ONLY place the guard's
            # expected uid comes from outside a test. Without a uid here the
            # fake ran with `_quick_uid is None`, which fails closed on every
            # quick verb, so deleting that copy changed nothing and the whole
            # bridge could be disabled with the suite green.
            self.uid = f"win-{len(made)}"
            made.append(self)
            self.events = types.SimpleNamespace(loaded=_Hook(), closing=_Hook(), shown=_Hook())

        def expose(self, *fns):
            published.append({f.__name__ for f in fns})

    monkeypatch.setattr(wv, "create_window", lambda title, url=None, html=None, **kw: Win())
    s = sh.AttachShell(port=9000, start_path="/portal/", title="t", width=800, height=600,
                       log=lambda m: None, probe=lambda p: {"status": "ok"},
                       listening=lambda p: True, quick_shortcut="ctrl+space")
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(s, "_start_quick", lambda: None)
    s.run(storage_path=tmp_path, icon=None, ipc_address=None)
    assert published == [{"shell_info", "start_daemon"},
                         {"shell_info", "hide", "open_main", "set_quick_height"}]
    # The binding, not the comparison: message_allowed's quick branch needs a
    # truthy `quick_uid` to admit anything, so this is what makes the exposed
    # quick verbs reachable at all.
    assert s._quick_uid == s.quick.uid
    assert s._quick_uid and s._quick_uid != s.window.uid


# ── AttachShell behaviour, through a fake window ─────────────────────────────
class FakeWindow:
    def __init__(self, url="http://127.0.0.1:9000/portal/"):
        self.url = url
        self.loaded: list[str] = []
        self.html: list[str] = []
        self.shown = 0

    def get_current_url(self):
        return self.url

    def load_url(self, url):
        self.loaded.append(url)
        self.url = url

    def load_html(self, html):
        # Real pywebview (edgechromium on_navigation_completed) reports NO url
        # for HTML it loaded itself — not "about:blank", which is only what CDP
        # shows. An earlier fake returned "about:blank" and hid a live bug.
        self.html.append(html)
        self.url = None

    def show(self):
        self.shown += 1

    def restore(self):
        pass

    def hide(self):
        self.hidden = getattr(self, "hidden", 0) + 1

    def destroy(self):
        self.destroyed = True


def _shell(start_daemon=None, starting=False, probe=None, listening=None, log=None):
    s = sh.AttachShell(port=9000, start_path="/portal/", title="t", width=800, height=600,
                       log=log or (lambda m: None), start_daemon=start_daemon, starting=starting,
                       probe=probe or (lambda port: {"status": "ok"}),
                       listening=listening or (lambda port: False))
    s.window = FakeWindow()
    return s


def test_start_daemon_refused_off_the_splash():
    calls = []
    s = _shell(start_daemon=lambda: calls.append(1) or True)
    s.window.url = "http://127.0.0.1:9000/portal/"      # a daemon page (or XSS in it) calling in
    assert s.start_daemon() == {"ok": False, "reason": "not_allowed"}
    s.window.url = "https://evil.example/"               # a foreign site that reached the bridge
    assert s.start_daemon() == {"ok": False, "reason": "not_allowed"}
    assert calls == []


def test_start_gate_reads_the_shells_view_not_the_window_url():
    """A page cannot make itself look like the splash: the gate trusts only the
    view the shell put up, and the window's URL is None on the real splash."""
    calls = []
    s = _shell(start_daemon=lambda: calls.append(1) or True)
    for spoof in ("about:blank", None, "about:srcdoc"):
        s.window.url = spoof
        assert s.start_daemon() == {"ok": False, "reason": "not_allowed"}
    assert calls == []
    s._render_splash(False)                              # the shell itself shows the splash
    assert s.window.get_current_url() is None
    assert s.start_daemon()["ok"] is True


def test_view_state_follows_every_shell_navigation():
    ok, gone = {"status": "ok"}, None
    s = _shell(probe=_probe_seq(*([gone] * sc.MISSES_TO_DOWN), ok))
    s.watch.observe(True)
    for _ in range(sc.MISSES_TO_DOWN):
        s.tick()
    assert s._on_splash is True                          # outage → splash
    s.tick()
    assert s._on_splash is False                         # recovery → page, before it loads
    s._render_splash(False)
    s.window.url = "http://127.0.0.1:9000/kb/"           # a daemon page finished loading
    s._on_loaded()
    assert s._on_splash is False


def test_start_daemon_refused_while_anything_holds_the_port():
    calls = []
    s = _shell(start_daemon=lambda: calls.append(1) or True, listening=lambda p: True)
    s._render_splash(True)
    assert s.start_daemon() == {"ok": False, "reason": "not_allowed"}
    s._listening = lambda p: None                        # unknown is not "free"
    assert s.start_daemon() == {"ok": False, "reason": "not_allowed"}
    assert calls == []


def test_start_daemon_runs_once_from_the_splash():
    calls = []
    s = _shell(start_daemon=lambda: calls.append(1) or True)
    s._render_splash(False)
    assert s.start_daemon()["ok"] is True
    assert s.start_daemon() == {"ok": True, "reason": "already_starting"}
    assert calls == [1]
    assert "Starting the daemon" in s.window.html[-1]


def test_start_daemon_is_serialised_across_threads():
    import threading
    import time as _t
    calls = []

    def slow():
        calls.append(1)
        _t.sleep(0.2)
        return True

    def slow_port_check(port):
        _t.sleep(0.05)   # the real check is a ~2 s connect — every caller is inside the gate at once
        return False

    s = _shell(start_daemon=slow, listening=slow_port_check)
    s._render_splash(False)
    ts = [threading.Thread(target=s.start_daemon) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert calls == [1]


def test_start_daemon_failure_gives_the_button_back():
    s = _shell(start_daemon=lambda: False)
    s._render_splash(False)
    assert s.start_daemon() == {"ok": False}
    assert 'id="start"' in s.window.html[-1]


def test_start_daemon_unavailable_without_a_starter():
    s = _shell()
    s._render_splash(False)
    assert s.start_daemon() == {"ok": False, "reason": "unavailable"}


def test_external_navigation_goes_to_system_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(sh.webbrowser, "open", lambda u: opened.append(u))
    s = _shell()
    s._on_loaded()                                   # a daemon page is remembered
    assert s.last_url == "http://127.0.0.1:9000/portal/"
    s.window.url = "https://example.com/x"
    s._on_loaded()
    assert opened == ["https://example.com/x"]
    assert s.window.loaded[-1] == "http://127.0.0.1:9000/portal/"


def test_splash_load_is_not_treated_as_external(monkeypatch):
    opened = []
    monkeypatch.setattr(sh.webbrowser, "open", lambda u: opened.append(u))
    s = _shell()
    for splash_url in (None, "about:blank"):           # pywebview's view, and CDP's
        s.window.url = splash_url
        s._on_loaded()
    assert opened == [] and s.window.loaded == []


# ── the watcher ──────────────────────────────────────────────────────────────
def _probe_seq(*answers):
    it = iter(answers)
    return lambda port: next(it)


def test_outage_then_recovery_restores_the_page_the_user_was_on():
    ok, gone = {"status": "ok"}, None
    s = _shell(probe=_probe_seq(ok, gone, gone, gone, ok))
    s.watch.observe(True)
    s.window.url = "http://127.0.0.1:9000/journal/"
    s._on_loaded()                                    # pywebview fires `loaded` for the page
    s.tick()                                          # ok
    s.tick()                                          # miss 1
    s.tick()                                          # miss 2
    assert not s.window.html                          # two misses keep the page
    s.tick()                                          # miss 3 → down
    assert s.window.html, "sustained silence must show the splash"
    assert s.last_url == "http://127.0.0.1:9000/journal/"
    s.tick()                                          # ok → back
    assert s.window.loaded[-1] == "http://127.0.0.1:9000/journal/"


def test_recovery_clears_starting():
    s = _shell(probe=_probe_seq({"status": "ok"}), starting=True)
    s.watch.observe(False)
    s.tick()
    assert s._starting is False


def test_splash_updates_when_the_port_is_released():
    held = {"v": True}
    s = _shell(probe=lambda p: None, listening=lambda p: held["v"], start_daemon=lambda: True)
    s.watch.observe(False)
    s._render_splash(True)
    assert 'id="start"' not in s.window.html[-1]      # booting: no button
    s.tick()
    assert len(s.window.html) == 1                    # unchanged state: no reload flicker
    held["v"] = False
    s.tick()
    assert 'id="start"' in s.window.html[-1]          # port freed: button appears


def test_stuck_start_times_out_and_offers_start_again(monkeypatch):
    s = _shell(probe=lambda p: None, start_daemon=lambda: True, starting=True)
    s.watch.observe(False)
    s._starting_since -= sh.START_TIMEOUT_S + 1
    s.tick()
    assert s._starting is False
    assert 'id="start"' in s.window.html[-1]


def test_watch_loop_survives_a_failing_step(monkeypatch):
    logged = []
    s = _shell(log=logged.append)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("WebViewException: window not shown")

    class Stop(BaseException):
        pass

    def sleep(_):
        if calls["n"] >= 2:
            raise Stop

    s.tick = boom
    monkeypatch.setattr(sh.time, "sleep", sleep)
    with pytest.raises(Stop):
        s._watch_loop()
    assert calls["n"] == 2, "the loop must keep running after a failed step"
    assert any("watcher step failed" in m for m in logged)


def test_second_launch_focuses_and_navigates_only_when_up_and_safe():
    s = _shell()
    s.watch.observe(True)
    s.on_second_launch({"cmd": "show"})
    assert s.window.shown == 1 and s.window.loaded == []
    s.on_second_launch({"cmd": "show", "path": "/kb/"})
    assert s.window.loaded == ["http://127.0.0.1:9000/kb/"]
    s.on_second_launch({"cmd": "show", "path": "//evil/"})
    assert s.window.loaded == ["http://127.0.0.1:9000/kb/"]
    for _ in range(sc.MISSES_TO_DOWN):
        s.watch.observe(False)
    assert s.watch.state == "down"
    s.on_second_launch({"cmd": "show", "path": "/journal/"})
    assert s.window.loaded == ["http://127.0.0.1:9000/kb/"]   # don't leave the splash for a dead daemon


# ── eos_desktop shell selection ──────────────────────────────────────────────
def test_desktop_config_defaults_to_browser():
    ed = _load_eos_desktop()
    def shell_of(data):
        return ed.desktop_config_from_dict(data)["shell"]

    assert shell_of({}) == "browser"
    assert shell_of({"desktop": {"shell": "webview"}}) == "webview"
    assert shell_of({"desktop": {"shell": "electron"}}) == "browser"
    assert shell_of({"desktop": "webview"}) == "browser"      # not even a table


def test_choose_shell_precedence():
    ed = _load_eos_desktop()
    assert ed.choose_shell(webview_flag=False, browser_flag=False, configured="webview") == "webview"
    assert ed.choose_shell(webview_flag=True, browser_flag=False, configured="browser") == "webview"
    # --browser is the escape hatch: it wins over both the config and --webview
    assert ed.choose_shell(webview_flag=True, browser_flag=True, configured="webview") == "browser"


def test_unsafe_path_is_refused_not_silently_replaced(capsys):
    ed = _load_eos_desktop()
    assert ed.main(["--webview", "--dry-run", "--path", "//evil/"]) == 2
    assert "same-origin" in capsys.readouterr().out


def test_reexec_marker_does_not_leak_to_children(monkeypatch):
    ed = _load_eos_desktop()
    monkeypatch.setenv(ed.REEXEC_ENV, "1")
    monkeypatch.setattr(ed, "_webview_importable", lambda: True)
    ed.main(["--webview", "--dry-run"])
    assert ed.REEXEC_ENV not in os.environ


# ── the web-message filter (pywebview's built-ins run before the API) ────────
def _msg(name, params="[]", call_id="0123456789"):
    return json.dumps([name, params, call_id])


@pytest.mark.parametrize("source,raw,ok", [
    ("http://127.0.0.1:9000/portal/", _msg("shell_info"), True),
    ("about:blank", _msg("shell_info"), True),
    ("about:blank", _msg("start_daemon"), True),
    ("http://127.0.0.1:9000/portal/", _msg("start_daemon"), False),   # only the splash presses Start
    ("https://evil.example/", _msg("shell_info"), False),              # foreign document
    ("file:///C:/x.html", _msg("shell_info"), False),                  # a dropped file
    ("http://127.0.0.1:9000/portal/",
     _msg("pywebviewStateUpdate", json.dumps({"key": "k", "value": "'+x+'"})), False),
    ("http://127.0.0.1:9000/portal/", _msg("pywebviewMoveWindow", "[-32000, -32000]"), False),
    ("http://127.0.0.1:9000/portal/", _msg("_pywebviewAlert", '"hi"'), False),
    ("http://127.0.0.1:9000/portal/", _msg("console", '"x"'), False),
    ("http://127.0.0.1:9000/portal/", _msg("shell_info", call_id='"+fetch(1)+"'), False),  # id → evaluate_js
    ("http://127.0.0.1:9000/portal/", _msg("shell_info", params='["x"]'), False),
    # Built-ins with EMPTY params, so only the name allowlist can refuse them
    # (the params check alone stops the loaded variants above).
    ("http://127.0.0.1:9000/portal/", _msg("pywebviewMoveWindow"), False),
    ("http://127.0.0.1:9000/portal/", _msg("pywebviewStateDelete"), False),
    ("about:blank", _msg("pywebviewAsyncCallback", "{}"), False),
    ("http://127.0.0.1:9000/portal/", '"FilesDropped"', False),
    ("http://127.0.0.1:9000/portal/", "not json", False),
    ("http://127.0.0.1:9000/portal/", json.dumps(["shell_info", "[]"]), False),
])
def test_message_allowed(source, raw, ok):
    assert sc.message_allowed(source, raw, 9000) is ok


D = "http://127.0.0.1:9000/portal/"


@pytest.mark.parametrize("uri,current,decision", [
    ("http://127.0.0.1:9000/kb/", D, "allow"),
    ("about:blank", "", "splash"),                      # token-gated by the shell
    ("about:srcdoc", D, "block"),
    ("https://example.com/", D, "browser"),
    ("http://127.0.0.1:9001/", D, "browser"),           # another origin
    ("file:///C:/Users/x/evil.html", D, "block"),
    ("javascript:alert(1)", D, "block"),
    ("vbscript:msgbox(1)", D, "block"),
    ("data:text/plain,x", D, "block"),
    ("data:text/html,<script>x</script>", D, "block"),
    ("blob:http://127.0.0.1:9000/abc", D, "block"),
    ("view-source:http://127.0.0.1:9000/", D, "block"),
    ("shell:startup", D, "block"),
    ("mailto:someone@example.com", D, "os"),            # the default allowlist
    ("search-ms:query=x&crumb=location:\\\\evil@SSL\\DavWWWRoot", D, "unlisted"),
    ("ms-msdt:/id x", D, "unlisted"),
    ("ms-officecmd:{}", D, "unlisted"),
    ("mailto:someone@example.com", "about:blank", "block"),   # not from a daemon page
    ("mailto:someone@example.com", "https://evil.example/", "block"),
    ("", D, "block"),
    ("noscheme", D, "block"),
])
def test_navigation_decision(uri, current, decision):
    assert sc.navigation_decision(uri, current, 9000) == decision


def test_configured_scheme_is_handed_on_but_never_a_dangerous_one():
    schemes = sc.clean_external_schemes(["notes", "FILE", "javascript:", "vbscript", "bad scheme", 3])
    assert schemes == ("mailto", "notes")
    assert sc.navigation_decision("notes://open?x", D, 9000, schemes) == "os"
    assert sc.navigation_decision("file:///C:/x", D, 9000, ("mailto", "file")) == "block"


def test_splash_source_is_exactly_about_blank():
    assert sc.is_splash_source("about:blank")
    for s in ("about:srcdoc", "about:blank#x", "data:text/html,x", "", None, D):
        assert not sc.is_splash_source(s)


class _Inline:
    """threading.Thread stand-in that runs the target at start()."""

    def __init__(self, target, args=(), daemon=None, **_):
        self.t, self.a = target, args

    def start(self):
        self.t(*self.a)


def test_on_navigation_side_effects(monkeypatch):
    opened, handed = [], []
    monkeypatch.setattr(sh.webbrowser, "open", lambda u: opened.append(u))
    monkeypatch.setattr(sh.os, "startfile", lambda u: handed.append(u), raising=False)
    monkeypatch.setattr(sh.threading, "Thread", _Inline)
    s = _shell()
    s._render_splash(False)
    assert s.on_navigation("http://127.0.0.1:9000/kb/", "about:blank") is True
    assert s._on_splash is False                       # cleared before the page's scripts run
    assert s.on_navigation("https://example.com/", "http://127.0.0.1:9000/kb/") is False
    assert s.on_navigation("mailto:a@b.c", "http://127.0.0.1:9000/kb/") is False
    assert s.on_navigation("file:///C:/evil.html", "http://127.0.0.1:9000/kb/") is False
    assert opened == ["https://example.com/"] and handed == ["mailto:a@b.c"]


def _data_url(html):
    import base64
    return "data:text/html;charset=utf-8;base64," + base64.b64encode(html.encode()).decode()


def test_data_url_matches_only_the_exact_splash():
    html = sc.splash_html(9000, listening=False, can_start=True)
    assert sc.is_data_url_for(_data_url(html), html)
    assert not sc.is_data_url_for(_data_url(html + " "), html)          # not byte-identical
    assert not sc.is_data_url_for(_data_url("<script>x</script>"), html)
    assert not sc.is_data_url_for("data:text/html;charset=utf-8;base64,@@@", html)
    assert not sc.is_data_url_for(_data_url(html), None)


def test_the_shells_own_splash_navigation_is_a_data_url():
    """Measured live: WebView2 reports NavigateToString as a data: URL. The
    shell's own splash must pass; the same shape carrying other HTML must not."""
    s = _shell()
    s._render_splash(False)
    assert s.on_navigation(_data_url(s._splash_html), D) is True
    s._render_splash(False)
    assert s.on_navigation(_data_url("<h1>not the splash</h1>"), D) is False
    assert s.on_navigation(_data_url(s._splash_html), D) is True       # the token was still unspent
    assert s.on_navigation(_data_url(s._splash_html), D) is False      # ...and now it is


def test_start_from_a_data_url_splash_needs_the_exact_splash():
    html = sc.splash_html(9000, listening=False, can_start=True)
    assert sc.message_allowed(_data_url(html), _msg("start_daemon"), 9000, splash_html_now=html)
    assert not sc.message_allowed(_data_url("<b>x</b>"), _msg("start_daemon"), 9000, splash_html_now=html)
    assert not sc.message_allowed(_data_url(html), _msg("start_daemon"), 9000)


def test_the_shell_hands_the_filter_its_current_splash():
    """The wiring, not the helper: on_message must pass the splash it rendered."""
    s = _shell()
    s._render_splash(False)
    assert s.on_message(_data_url(s._splash_html), _msg("start_daemon")) is True
    assert s.on_message(_data_url("<b>x</b>"), _msg("start_daemon")) is False


def test_about_blank_only_when_the_shell_loads_the_splash():
    s = _shell()
    assert s.on_navigation("about:blank", D) is False         # a page sending the window blank
    s._render_splash(False)
    assert s.on_navigation("about:blank", D) is True          # the shell's own splash load
    assert s.on_navigation("about:blank", D) is False         # one-shot: a replay is refused


def test_unlisted_scheme_explains_once(monkeypatch):
    boxes = []
    monkeypatch.setattr(sh, "_message_box", lambda text, title="EmptyOS": boxes.append(text))
    monkeypatch.setattr(sh.threading, "Thread", _Inline)
    s = _shell()
    for _ in range(5):
        assert s.on_navigation("search-ms:query=x", D) is False
    assert len(boxes) == 1 and "external_schemes" in boxes[0]


def test_new_window_never_opens_a_window_or_hands_on_blindly(monkeypatch):
    opened, handed = [], []
    monkeypatch.setattr(sh.webbrowser, "open", lambda u: opened.append(u))
    monkeypatch.setattr(sh.os, "startfile", lambda u: handed.append(u), raising=False)
    monkeypatch.setattr(sh.threading, "Thread", _Inline)
    monkeypatch.setattr(sh, "_message_box", lambda *a, **k: None)
    s = _shell()
    s.on_new_window("http://127.0.0.1:9000/kb/", D)            # daemon popup → this window
    assert s.window.loaded[-1] == "http://127.0.0.1:9000/kb/"
    s.on_new_window("https://example.com/", D)
    s.on_new_window("file:///C:/Windows/System32/calc.exe", D)
    s.on_new_window("search-ms:query=x", D)
    s.on_new_window("about:blank", D)                          # window.open() with no url
    s.on_new_window("mailto:a@b.c", "https://evil.example/")
    assert opened == ["https://example.com/"] and handed == []


# ── the guard and run(): the real wiring, through a fake webview ─────────────
class _Args:
    def __init__(self, raw=None, **props):
        self.Cancel = False
        self._raw = raw
        for k, v in props.items():
            setattr(self, k, v)

    def get_WebMessageAsJson(self):
        return self._raw


class _Hook:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self


def _fake_webview(monkeypatch, *, renderer="edgechromium", version="6.2.1"):
    import importlib.metadata
    import types

    calls = {"created": [], "exposed": [], "started": 0, "notify": [], "nav": [], "popup": []}

    class EdgeChrome:
        def on_script_notify(self, sender, args):
            calls["notify"].append(args)

        def on_navigation_start(self, sender, args):
            calls["nav"].append(args)

        def on_new_window_request(self, sender, args):
            calls["popup"].append(args)   # pywebview's own: webbrowser.open(any uri)

    class Win(FakeWindow):
        def __init__(self):
            super().__init__()
            self.events = types.SimpleNamespace(loaded=_Hook(), closing=_Hook(), shown=_Hook())
            calls["window"] = self

        def expose(self, *fns):
            calls["exposed"].extend(f.__name__ for f in fns)

    def create_window(title, url=None, html=None, **kw):
        calls["created"].append({"url": url, "html": html, **kw})
        return Win()

    def start(**kw):
        calls["started"] += 1

    wv = types.ModuleType("webview")
    wv.settings = {"ALLOW_FILE_URLS": True, "ALLOW_DOWNLOADS": False}
    wv.create_window, wv.start = create_window, start
    plat = types.ModuleType("webview.platforms")
    ec = types.ModuleType("webview.platforms.edgechromium")
    ec.EdgeChrome = EdgeChrome
    wf = types.ModuleType("webview.platforms.winforms")
    wf.renderer = renderer
    plat.edgechromium, plat.winforms = ec, wf
    wv.platforms = plat
    for name, mod in (("webview", wv), ("webview.platforms", plat),
                      ("webview.platforms.edgechromium", ec),
                      ("webview.platforms.winforms", wf)):
        monkeypatch.setitem(sys.modules, name, mod)
    real_version = importlib.metadata.version
    monkeypatch.setattr(importlib.metadata, "version",
                        lambda dist: version if dist == "pywebview" else real_version(dist))
    return wv, ec, calls


def test_run_wires_the_inert_root_the_exposed_names_and_the_guard(monkeypatch, tmp_path):
    wv, ec, calls = _fake_webview(monkeypatch)
    s = sh.AttachShell(port=9000, start_path="/portal/", title="t", width=800, height=600,
                       log=lambda m: None, probe=lambda p: {"status": "ok"},
                       listening=lambda p: True)
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)  # no watcher here
    assert s.run(storage_path=tmp_path, icon=None, ipc_address=None) == 0
    assert isinstance(calls["created"][0]["js_api"], sh._InertRoot), \
        "js_api must be the inert root, never the shell or anything walkable"
    assert sorted(calls["exposed"]) == ["shell_info", "start_daemon"]
    assert len(calls["created"]) == 1, "quick entry is off — no second window"
    assert wv.settings["ALLOW_FILE_URLS"] is False
    assert ec.EdgeChrome.on_script_notify is not ec.EdgeChrome._eos_orig_notify, "guard not installed"
    assert ec.EdgeChrome.on_navigation_start is not ec.EdgeChrome._eos_orig_nav, "guard not installed"
    assert ec.EdgeChrome.on_new_window_request.__qualname__.startswith("install_bridge_guard"), \
        "popups are not guarded"
    assert calls["started"] == 1


def test_run_preloads_a_hidden_frameless_quick_window(monkeypatch, tmp_path):
    """Preloading IS the feature: the launcher this replaces respawned a browser
    window per press (~1 s). Pin that the window is created up front, hidden,
    frameless, on top, and pointed at the quick page — and that a press has a
    window to show before the hotkey is ever registered."""
    wv, ec, calls = _fake_webview(monkeypatch)
    s = sh.AttachShell(port=9000, start_path="/portal/", title="t", width=800, height=600,
                       log=lambda m: None, probe=lambda p: {"status": "ok"},
                       listening=lambda p: True, quick_shortcut="ctrl+space")
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    started = []
    monkeypatch.setattr(s, "_start_quick", lambda: started.append(1))
    assert s.run(storage_path=tmp_path, icon=None, ipc_address=None) == 0
    assert len(calls["created"]) == 2
    quick = calls["created"][1]
    assert quick["hidden"] is True and quick["frameless"] is True
    assert quick["on_top"] is True and quick["easy_drag"] is False
    assert quick["url"].endswith("/portal/?quick=1")
    assert isinstance(quick["js_api"], sh._InertRoot)
    assert started == [1], "the hotkey must be armed only once a window exists"


def test_a_quick_window_that_cannot_be_created_does_not_take_the_main_one_down(monkeypatch, tmp_path):
    wv, ec, calls = _fake_webview(monkeypatch)
    s = sh.AttachShell(port=9000, start_path="/portal/", title="t", width=800, height=600,
                       log=lambda m: None, probe=lambda p: {"status": "ok"},
                       listening=lambda p: True, quick_shortcut="ctrl+space")
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(s, "_create_quick", lambda _wv: (_ for _ in ()).throw(RuntimeError("boom")))
    assert s.run(storage_path=tmp_path, icon=None, ipc_address=None) == 0
    assert s.quick is None and calls["started"] == 1


@pytest.mark.parametrize("renderer,version", [("mshtml", "6.2.1"), ("cef", "6.2.1"),
                                              ("edgechromium", "6.3.0"), ("edgechromium", "5.4")])
def test_run_refuses_without_webview2_or_on_an_untested_pywebview(monkeypatch, tmp_path, renderer, version):
    wv, ec, calls = _fake_webview(monkeypatch, renderer=renderer, version=version)
    s = _shell()
    with pytest.raises(RuntimeError):
        s.run(storage_path=tmp_path, icon=None, ipc_address=None)
    assert calls["created"] == [] and calls["started"] == 0, "a window opened without the guard"


def test_guard_owns_popups(monkeypatch):
    wv, ec, calls = _fake_webview(monkeypatch)
    s = _shell()
    got = []
    s.on_new_window = lambda uri, current: got.append((uri, current))
    sh.install_bridge_guard(s)

    class Popup:
        def __init__(self, uri):
            self.uri, self.handled = uri, False

        def get_Uri(self):
            return self.uri

        def set_Handled(self, v):
            self.handled = v

    p = Popup("search-ms:query=x")
    ec.EdgeChrome().on_new_window_request(type("S", (), {"Source": D})(), p)
    assert p.handled is True, "WebView2 would open its own window"
    assert calls["popup"] == [], "pywebview's handler ran — it would webbrowser.open() anything"
    assert got == [("search-ms:query=x", D)]


# The guard patches pywebview internals; this reads the INSTALLED source (the
# shell's venv) and fails if a new release subscribes a handler the guard
# doesn't wrap. Skipped where the desktop venv isn't installed.
_REAL_EC = sc.default_desktop_python().parent.parent / "Lib" / "site-packages" / "webview" / "platforms" / "edgechromium.py"


@pytest.mark.skipif(not _REAL_EC.is_file(), reason="desktop venv not installed")
def test_installed_pywebview_subscribes_only_what_the_guard_wraps():
    import re as _re
    src = _REAL_EC.read_text(encoding="utf-8")
    subs = set(_re.findall(r"\.(\w+) \+= self\.(\w+)", src))
    risky = {(evt, h) for evt, h in subs if evt in {
        "WebMessageReceived", "NavigationStarting", "NewWindowRequested",
        "FrameNavigationStarting", "FrameCreated", "ContentLoading"}}
    assert risky == {("NavigationStarting", "on_navigation_start"),
                     ("WebMessageReceived", "on_script_notify"),
                     ("NewWindowRequested", "on_new_window_request")}, \
        f"pywebview's WebView2 subscriptions changed: {sorted(risky)} — re-check install_bridge_guard"
    for handler in ("on_navigation_start", "on_script_notify", "on_new_window_request"):
        assert f"def {handler}(self, sender, args)" in src or f"def {handler}(self, _, args)" in src


def test_guard_blocks_before_pywebview_and_passes_the_allowed(monkeypatch):
    wv, ec, calls = _fake_webview(monkeypatch)
    sh.install_bridge_guard(_shell())
    host = ec.EdgeChrome()
    host.on_script_notify(None, _Args(Source="https://evil.example/", raw=_msg("shell_info")))
    host.on_script_notify(None, _Args(
        Source="http://127.0.0.1:9000/p/",
        raw=_msg("pywebviewStateUpdate", json.dumps({"key": "k", "value": "v"}))))
    assert calls["notify"] == []
    ok = _Args(Source="http://127.0.0.1:9000/p/", raw=_msg("shell_info"))
    host.on_script_notify(None, ok)
    assert calls["notify"] == [ok]

    sender = type("S", (), {"Source": "http://127.0.0.1:9000/p/"})()
    monkeypatch.setattr(sh.webbrowser, "open", lambda u: None)
    bad = _Args(Uri="file:///C:/evil.html")
    host.on_navigation_start(sender, bad)
    assert bad.Cancel is True and calls["nav"] == []
    good = _Args(Uri="http://127.0.0.1:9000/kb/")
    host.on_navigation_start(sender, good)
    assert good.Cancel is False and calls["nav"] == [good]


def test_guard_refuses_what_it_cannot_read(monkeypatch):
    wv, ec, calls = _fake_webview(monkeypatch)
    sh.install_bridge_guard(_shell())
    host = ec.EdgeChrome()

    class Broken:
        def __init__(self):
            object.__setattr__(self, "Cancel", False)

        def __getattr__(self, name):
            raise RuntimeError("pythonnet surprise")

    nav = Broken()
    host.on_navigation_start(type("S", (), {"Source": ""})(), nav)
    assert nav.Cancel is True and calls["nav"] == []
    host.on_script_notify(None, Broken())
    assert calls["notify"] == []


# ── --start runs only for the process that won the lock ──────────────────────
def test_start_on_launch_waits_for_the_lock_and_a_free_port(monkeypatch, tmp_path):
    started = []

    def starter():
        started.append(1)
        return True

    monkeypatch.setattr(sh.AttachShell, "run", lambda self, **kw: 0)
    monkeypatch.setattr(sh, "port_listening", lambda p, **kw: False)

    monkeypatch.setattr(sh.single_instance.InstanceLock, "acquire", classmethod(lambda cls, n: None))
    monkeypatch.setattr(sh.single_instance, "send", lambda a, m: True)
    sh.run_shell(port=9000, repo_root=tmp_path, start_daemon=starter, start_on_launch=True)
    assert started == [], "a second launch must never run restart.bat"

    monkeypatch.setattr(sh.single_instance.InstanceLock, "acquire", classmethod(lambda cls, n: cls(0)))
    sh.run_shell(port=9000, repo_root=tmp_path, start_daemon=starter, start_on_launch=True)
    assert started == [1]

    monkeypatch.setattr(sh, "port_listening", lambda p, **kw: True)   # a daemon holds the port
    sh.run_shell(port=9000, repo_root=tmp_path, start_daemon=starter, start_on_launch=True)
    assert started == [1]


# ── port probing: every bind address, and "unknown" is not "free" ────────────
def test_listen_hosts():
    assert sc.listen_hosts(None) == ("127.0.0.1", "::1")
    assert sc.listen_hosts("0.0.0.0") == ("127.0.0.1", "::1")
    assert sc.listen_hosts("::") == ("127.0.0.1", "::1")
    assert sc.listen_hosts("100.64.1.2") == ("127.0.0.1", "::1", "100.64.1.2")


def test_port_listening_combines_hosts(monkeypatch):
    answers = {}
    monkeypatch.setattr(sc, "_probe_one", lambda h, p, t: answers[h])
    answers.update({"127.0.0.1": False, "::1": True})
    assert sc.port_listening(9000) is True                 # an IPv6-only daemon still counts
    answers.update({"127.0.0.1": False, "::1": False})
    assert sc.port_listening(9000) is False
    answers.update({"127.0.0.1": False, "::1": None})
    assert sc.port_listening(9000) is None                 # one timeout → unknown, not free


def test_probe_one_timeout_is_unknown(monkeypatch):
    def slow(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(sc.socket, "create_connection", slow)
    assert sc._probe_one("127.0.0.1", 9000, 0.1) is None


# ── A2: tray + close-to-tray ─────────────────────────────────────────────────
@pytest.mark.parametrize("tray_alive,quitting,reason,action", [
    (True, False, "UserClosing", "hide"), (False, False, "UserClosing", "close"),
    (True, True, "UserClosing", "close"), (False, True, "UserClosing", "close"),
    (True, False, "WindowsShutDown", "close"),     # sign-out, restart, Restart Manager: never veto
    (True, False, "TaskManagerClosing", "close"),  # "End task" means end it
    (True, False, "None", "close"),                # a programmatic close
])
def test_close_action(tray_alive, quitting, reason, action):
    assert sc.close_action(tray_alive=tray_alive, quitting=quitting, reason=reason) == action


def _ids(rows):
    return [r if r == "-" else r["id"] for r in rows]


def test_tray_menu_rules():
    up = sc.tray_menu(port=9000, daemon_state="up", can_start=True, starting=False)
    assert _ids(up) == ["open", "browser", "-", "status", "-", "quit"]   # no Start while up
    assert [r for r in up if r != "-" and r.get("default")] == [up[0]]     # one default: Open
    down = sc.tray_menu(port=9000, daemon_state="down", can_start=True, starting=False)
    assert "start" in _ids(down)
    assert next(r for r in down if r != "-" and r["id"] == "browser")["enabled"] is False
    starting = sc.tray_menu(port=9000, daemon_state="down", can_start=True, starting=True)
    assert "start" not in _ids(starting)
    assert "starting" in next(r for r in starting if r != "-" and r["id"] == "status")["label"]
    no_starter = sc.tray_menu(port=9000, daemon_state="down", can_start=False, starting=False)
    assert "start" not in _ids(no_starter)
    labels = " ".join(r["label"] for rows in (up, down) for r in rows if r != "-").lower()
    assert "restart" not in labels.replace("restart.bat", "") and "stop" not in labels
    assert "daemon keeps running" in up[-1]["label"]


class _FakeTray:
    def __init__(self, alive=True):
        self.notes, self.refreshes, self.stopped, self._alive = [], 0, False, alive

    def alive(self):
        return self._alive

    def notify(self, message, title=""):
        self.notes.append(message)

    def refresh(self):
        self.refreshes += 1

    def stop(self):
        self.stopped = True


def test_close_hides_to_the_tray_and_says_so_once(monkeypatch):
    monkeypatch.setattr(sh.threading, "Thread", _Inline)
    s = _shell()
    s.tray = _FakeTray()
    assert s._on_closing() is False                 # cancelled
    assert s._on_closing() is False
    assert s.window.hidden == 2
    assert s.tray.notes == [sc.HIDDEN_NOTICE]       # first hide only


def test_close_quits_without_a_tray():
    s = _shell()
    assert s._on_closing() is None                  # allowed: never hide into a void
    assert getattr(s.window, "hidden", 0) == 0


def test_close_quits_when_the_tray_icon_is_not_actually_running():
    s = _shell()
    s.tray = _FakeTray(alive=False)                 # a handle, but no icon on screen
    assert s._on_closing() is None


class _CloseArgs:
    def __init__(self, reason, cancel=False):
        self.CloseReason, self.Cancel = reason, cancel


@pytest.mark.parametrize("reason,cancelled", [
    ("UserClosing", True), ("WindowsShutDown", False), ("TaskManagerClosing", False), ("None", False),
])
def test_form_closing_decides_by_reason(monkeypatch, reason, cancelled):
    """The hook that actually answers Windows: only the user's own close hides."""
    monkeypatch.setattr(sh.threading, "Thread", _Inline)
    s = _shell()
    s.tray = _FakeTray()
    s._reason_hooked = True
    assert s._on_closing() is None                  # pywebview's hook never cancels once ours decides
    args = _CloseArgs(reason)
    s._on_form_closing(None, args)
    assert args.Cancel is cancelled
    assert getattr(s.window, "hidden", 0) == (1 if cancelled else 0)


def test_the_hook_falls_back_when_winforms_is_unreachable():
    logged = []
    s = _shell(log=logged.append)
    s.window.native = object()                      # no FormClosing to subscribe to
    s._hook_close_reason()
    assert s._reason_hooked is False and any("close-reason hook" in m for m in logged)


def test_a_dead_tray_falls_back_hands_the_icon_back_and_shows_the_window(monkeypatch):
    monkeypatch.setattr(sh, "_activate", lambda w: None)
    released = []
    s = _shell(probe=lambda p: {"status": "ok"})
    s.tray = _FakeTray(alive=False)
    s._tray_seen_alive = True                       # it was up, then died
    s._presence = type("L", (), {"release": lambda self: released.append(1)})()
    s.tick()
    assert s.tray is None and s._presence is None and released == [1]
    assert s.window.shown == 1                      # a hidden window is brought back
    assert s._on_closing() is None                  # and closing now quits


def test_presence_waits_for_the_icon_and_gives_it_a_grace_period(monkeypatch):
    claimed = []
    monkeypatch.setattr(sh.single_instance.InstanceLock, "acquire",
                        classmethod(lambda cls, name: claimed.append(name) or cls(0)))
    s = _shell(probe=lambda p: {"status": "ok"})
    s.tray = _FakeTray(alive=False)
    s._tray_started_at = sh.time.monotonic()
    s._check_tray()
    assert claimed == [] and s.tray is not None    # not up yet: no claim, not dropped
    s.tray._alive = True
    s._check_tray()
    s._check_tray()
    assert len(claimed) == 1                        # claimed once, when it appears
    s.tray._alive = False
    s._tray_seen_alive = False
    s._tray_started_at -= sh.TRAY_GRACE_S + 1
    s._check_tray()
    assert s.tray is None                           # never appeared in time → dropped


def test_quit_lets_the_close_through():
    s = _shell()
    s.tray = _FakeTray()
    s.quit()
    assert s.window.destroyed is True
    assert s._on_closing() is None                  # the destroy's own close is not cancelled


def test_tray_start_is_gated_on_the_port_not_the_splash(monkeypatch):
    monkeypatch.setattr(sh, "_activate", lambda w: None)
    calls = []
    held = {"v": True}
    s = _shell(start_daemon=lambda: calls.append(1) or True, listening=lambda p: held["v"])
    s.window.url = "http://127.0.0.1:9000/portal/"   # a page is showing, not the splash
    assert s.start_daemon_from_tray() == {"ok": False, "reason": "not_allowed"}
    held["v"] = False
    assert s.start_daemon_from_tray()["ok"] is True
    assert calls == [1] and s.window.shown == 1      # and the window comes forward


def test_page_start_still_needs_the_splash():
    s = _shell(start_daemon=lambda: True, listening=lambda p: False)
    assert s.start_daemon() == {"ok": False, "reason": "not_allowed"}   # tray path didn't loosen it


def test_tray_follows_daemon_edges():
    s = _shell(probe=_probe_seq({"status": "ok"}, None, None, None))
    s.tray = _FakeTray()
    s.tick()                      # unknown → up
    assert s.tray.refreshes == 1
    s.tick()
    s.tick()
    assert s.tray.refreshes == 1  # misses within the threshold are not an edge
    s.tick()                      # → down
    assert s.tray.refreshes == 2


def _record_locks(monkeypatch):
    claimed, released = [], []

    class Lock:
        def __init__(self, name):
            self.name = name

        def release(self):
            released.append(self.name)

    monkeypatch.setattr(sh.single_instance.InstanceLock, "acquire",
                        classmethod(lambda cls, name: claimed.append(name) or Lock(name)))
    return claimed, released


def test_run_wires_close_to_tray_claims_presence_and_cleans_up(monkeypatch, tmp_path):
    from emptyos.desktop_presence import tray_presence_name

    wv, ec, calls = _fake_webview(monkeypatch)
    tray = _FakeTray()
    claimed, released = _record_locks(monkeypatch)
    monkeypatch.setattr(sh, "start_shell_tray", lambda shell, brand: tray)
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    s = sh.AttachShell(port=9000, start_path="/portal/", title="t", width=800, height=600,
                       log=lambda m: None, probe=lambda p: {"status": "ok"}, listening=lambda p: True)
    s.run(storage_path=tmp_path, icon=None, ipc_address=None)
    assert s._on_closing in calls["window"].events.closing.handlers
    assert s._hook_close_reason in calls["window"].events.shown.handlers
    assert claimed == [tray_presence_name(9000)]          # the daemon's tray may stand down
    assert tray.stopped is True and released == [tray_presence_name(9000)]


def test_run_without_a_tray_still_opens_and_never_claims_presence(monkeypatch, tmp_path):
    wv, ec, calls = _fake_webview(monkeypatch)
    claimed, released = _record_locks(monkeypatch)

    def no_tray(shell, brand):
        raise ImportError("pystray")

    monkeypatch.setattr(sh, "start_shell_tray", no_tray)
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    s = sh.AttachShell(port=9000, start_path="/portal/", title="t", width=800, height=600,
                       log=lambda m: None, probe=lambda p: {"status": "ok"}, listening=lambda p: True)
    assert s.run(storage_path=tmp_path, icon=None, ipc_address=None) == 0
    assert s.tray is None and calls["started"] == 1
    assert claimed == [], "a tray-less shell must leave the daemon's tray visible"


def test_start_shell_tray_maps_each_item_to_its_action(monkeypatch):
    """The real wiring behind the pure menu: every id reaches the right method."""
    import types as _types

    from _shared import tray as tray_mod

    got = {}

    class MenuItem:
        def __init__(self, text, action, enabled=True, default=False):
            self.text, self.action, self.enabled, self.default = text, action, enabled, default

    class Menu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    monkeypatch.setitem(sys.modules, "pystray", _types.SimpleNamespace(MenuItem=MenuItem, Menu=Menu))
    monkeypatch.setattr(tray_mod, "run_tray", lambda icon_id, image, title, menu: got.setdefault("menu", menu))
    monkeypatch.setattr(tray_mod, "load_icon_image", lambda brand: None)
    monkeypatch.setattr(sh.threading, "Thread", _Inline)
    hits = []
    s = _shell(start_daemon=lambda: True)
    s.show_window = lambda: hits.append("open")
    s.open_in_browser = lambda: hits.append("browser")
    s.start_daemon_from_tray = lambda: hits.append("start")
    s.quit = lambda: hits.append("quit")
    s.watch.state = "down"
    sh.start_shell_tray(s, None)
    items = [i for i in got["menu"].items[0]() if i is not Menu.SEPARATOR]   # Menu(callable)
    by_text = {i.text: i for i in items}
    for label, expect in (("Open EmptyOS", "open"), ("Open in browser", "browser"),
                          ("Start daemon (restart.bat)…", "start"),
                          ("Quit (daemon keeps running)", "quit")):
        by_text[label].action(None, None)
        assert hits[-1] == expect, label
    assert by_text["Open EmptyOS"].default is True
    s.watch.state = "up"
    assert not any(getattr(i, "text", "").startswith("Start")
                   for i in got["menu"].items[0]())                          # regenerated per refresh


def test_tray_handle_alive_means_an_icon_is_on_screen():
    import threading as _th

    from _shared.tray import TrayHandle

    icon = type("I", (), {"visible": False, "stop": lambda self: None})()
    stop = _th.Event()
    t = _th.Thread(target=stop.wait, daemon=True)
    t.start()
    h = TrayHandle(icon, t)
    assert h.alive() is False          # running thread, icon not yet visible
    icon.visible = True
    assert h.alive() is True
    stop.set()
    t.join(2)
    assert h.alive() is False          # thread gone → no icon, whatever it last said


def test_starting_refreshes_the_tray(monkeypatch):
    monkeypatch.setattr(sh, "_activate", lambda w: None)
    s = _shell(start_daemon=lambda: True, listening=lambda p: False)
    s.tray = _FakeTray()
    s.start_daemon_from_tray()
    assert s.tray.refreshes >= 1


def test_cadence_values_are_exact():
    assert (sc.MISSES_TO_DOWN, sc.PROBE_TIMEOUT_S, sc.PROBE_INTERVAL_S) == (3, 5.0, 3.0)


# ── shell login exchange (A3) ────────────────────────────────────────────────
# The shell holds the machine token; the webview does not. These pin that the
# token never reaches a URL, that a code does, and that every failure mode ends
# with an ordinary window rather than a shell that will not start.

class _ExchangeStub:
    """Stands in for urllib.request.urlopen."""

    def __init__(self, payload=None, raises=None):
        self.payload, self.raises, self.seen = payload, raises, []

    def __call__(self, req, timeout=None):
        self.seen.append(req)
        if self.raises:
            raise self.raises
        import io
        import json as _json
        body = io.BytesIO(_json.dumps(self.payload).encode("utf-8"))
        body.__enter__ = lambda: body
        body.__exit__ = lambda *a: None
        return body


def test_the_exchange_url_carries_a_code_and_never_the_token():
    url = sc.exchange_url(9000, "c0de-value", "/portal/")
    assert url.startswith("http://127.0.0.1:9000/auth/shell-exchange?")
    assert "code=c0de-value" in url
    assert "next=/portal/" in url
    assert "token" not in url


def test_the_exchange_url_clamps_its_next_like_every_other_path():
    for hostile in ("//evil.example/x", "/\\evil", "javascript:alert(1)", "http://evil/"):
        url = sc.exchange_url(9000, "c", hostile)
        assert "next=%2Fportal%2F" in url or "next=/portal/" in url, hostile
        assert "evil" not in url, hostile


def test_a_code_is_percent_encoded_so_it_cannot_add_parameters():
    url = sc.exchange_url(9000, "a&next=//evil.example", "/portal/")
    assert url.count("next=") == 1
    assert "evil.example" not in url.split("next=")[1]


def test_minting_returns_the_code_and_sends_the_token_as_a_bearer(monkeypatch):
    stub = _ExchangeStub({"code": "minted", "expires_in": 60})
    monkeypatch.setattr(sc.urllib.request, "urlopen", stub)
    assert sc.mint_exchange_code(9000, "tok") == "minted"
    (req,) = stub.seen
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.full_url.endswith("/api/auth/shell-exchange")


def test_every_failure_to_mint_is_an_ordinary_window_not_a_broken_shell(monkeypatch):
    """A daemon older than the feature, the flag off, no token at all — each
    means "just open the window", never "refuse to start"."""
    import urllib.error

    monkeypatch.setattr(
        sc.urllib.request, "urlopen",
        _ExchangeStub(raises=urllib.error.HTTPError("u", 404, "nope", {}, None)),
    )
    assert sc.mint_exchange_code(9000, "tok") is None
    assert sc.first_url(9000, "tok") == sc.start_url(9000)

    monkeypatch.setattr(sc.urllib.request, "urlopen", _ExchangeStub({"error": "forbidden"}))
    assert sc.mint_exchange_code(9000, "tok") is None

    monkeypatch.setattr(sc.urllib.request, "urlopen", _ExchangeStub({"code": ""}))
    assert sc.mint_exchange_code(9000, "tok") is None


def test_no_token_means_no_request_at_all(monkeypatch):
    stub = _ExchangeStub({"code": "should-not-be-reached"})
    monkeypatch.setattr(sc.urllib.request, "urlopen", stub)
    assert sc.mint_exchange_code(9000, "") is None
    assert stub.seen == [], "local mode has no token — do not even ask"


def test_first_url_uses_the_exchange_when_one_is_available(monkeypatch):
    monkeypatch.setattr(sc.urllib.request, "urlopen", _ExchangeStub({"code": "abc"}))
    url = sc.first_url(9000, "tok", "/portal/")
    assert "/auth/shell-exchange?code=abc" in url
    assert "tok" not in url, "the token must never reach the window's URL"


def test_the_window_actually_uses_the_exchange(monkeypatch):
    """The helper existing proves nothing: first_url was dead code in the first
    draft — AttachShell still called start_url, so the phase shipped an
    auth-exempt route with no consumer, all of the surface and none of the
    benefit. This drives the real constructor."""
    import ast
    import inspect

    from _shared import shell as shell_mod

    # textwrap.dedent, not cleandoc: cleandoc leaves the first line's own
    # indentation alone, which is fine for a docstring and a syntax error here.
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(shell_mod.AttachShell.__init__)))
    calls = {
        getattr(n.func, "id", "") or getattr(n.func, "attr", "")
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }
    assert "first_url" in calls, "the window does not go through the exchange"
    assert "start_url" not in calls, "start_url would skip it entirely"

    # And the token reaches it: minting is what turns the URL into a redeem.
    seen = {}

    def fake_first_url(port, token="", path="/portal/"):
        seen.update(port=port, token=token, path=path)
        return "http://127.0.0.1:%d/auth/shell-exchange?code=x&next=%s" % (port, path)

    monkeypatch.setattr(shell_mod, "first_url", fake_first_url)
    shell = shell_mod.AttachShell(
        port=9000, start_path="/portal/", title="t", width=1, height=1,
        log=lambda *_a: None, auth_token="tok-from-config",
    )
    assert seen["token"] == "tok-from-config"
    assert "/auth/shell-exchange" in shell.start
    assert "tok-from-config" not in shell.start, "the token must never reach the URL"


def test_run_shell_passes_the_token_through_to_the_window():
    """A parameter that stops one frame short is the same dead end."""
    import inspect

    from _shared import shell as shell_mod

    assert "auth_token" in inspect.signature(shell_mod.run_shell).parameters
    body = inspect.getsource(shell_mod.run_shell)
    assert "auth_token=auth_token" in body, "run_shell accepts it and drops it"


# ── quick entry (A4 + B6) ────────────────────────────────────────────────────
Q = "http://127.0.0.1:9000/portal/?quick=1"


def test_quick_url_is_same_origin_and_carries_no_token():
    url = sc.quick_url(9000)
    assert url.startswith("http://127.0.0.1:9000/")
    assert "quick=1" in url and "token" not in url
    # A path that fails the same-origin gate falls back rather than escaping.
    assert sc.quick_url(9000, "//evil.example/x") == sc.quick_url(9000)
    assert sc.quick_url(9000, "javascript:alert(1)") == sc.quick_url(9000)


@pytest.mark.parametrize("value,expect", [
    (0, sc.QUICK_MIN_HEIGHT),
    (sc.QUICK_MIN_HEIGHT - 1, sc.QUICK_MIN_HEIGHT),
    (300, 300),
    (sc.QUICK_MAX_HEIGHT + 1, sc.QUICK_MAX_HEIGHT),
    (10 ** 9, sc.QUICK_MAX_HEIGHT),
    (-5, sc.QUICK_MIN_HEIGHT),
    (300.7, 300),
])
def test_clamp_quick_height(value, expect):
    assert sc.clamp_quick_height(value) == expect


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "tall", None, {}])
def test_clamp_quick_height_refuses_the_unmeasurable(value):
    """NaN is the one that gets through a naive clamp: every comparison against
    it is False, so min()/max() return it unchanged and the window is resized to
    a number nothing downstream can reject."""
    assert sc.clamp_quick_height(value) == sc.QUICK_START_HEIGHT


def test_the_two_sides_of_the_height_band_agree():
    """portal-quick.js clamps what it asks for and the shell clamps what it is
    told. They are separate numbers in separate languages, so pin them together
    — a band that drifts means a panel silently capped by whichever is smaller."""
    js = (REPO_ROOT / "apps/public/standard/portal/pages/portal-quick.js").read_text(encoding="utf-8")
    assert f"var MIN_HEIGHT = {sc.QUICK_MIN_HEIGHT};" in js
    assert f"var MAX_HEIGHT = {sc.QUICK_MAX_HEIGHT};" in js
    assert f"var START_HEIGHT = {sc.QUICK_START_HEIGHT};" in js


def test_quick_geometry_centres_high_on_the_screen():
    g = sc.quick_geometry(1920, 1080)
    assert g["width"] <= sc.QUICK_MAX_WIDTH
    assert g["x"] + g["width"] // 2 == 1920 // 2      # horizontally centred
    assert 0 < g["y"] < 1080 // 2                      # high, so it can grow downward
    assert g["height"] == sc.QUICK_START_HEIGHT


@pytest.mark.parametrize("w,h", [(0, 0), (None, None), ("x", "y"), (100, 80), (-1920, 1080)])
def test_quick_geometry_survives_an_unreadable_screen(w, h):
    """No branch handles this — the floors do. Pinned as a property rather than
    a code path, because an explicit fallback here was measurably unreachable:
    a mutation deleting it changed no outcome in any of these cases."""
    g = sc.quick_geometry(w, h)
    # Against the named floor, not a looser literal: 320 would stay green while
    # the real floor moved below the width QUICK_START_HEIGHT was measured at.
    assert g["width"] >= sc.QUICK_MIN_WIDTH and g["x"] >= 0 and g["y"] >= 0
    assert g["height"] == sc.QUICK_START_HEIGHT


@pytest.mark.parametrize("sw,sh", [(1280, 720), (1920, 1080), (2560, 1440),
                                   (3840, 2160), (800, 600), (1366, 768)])
def test_a_fully_grown_panel_still_fits_the_screen_it_was_sized_for(sw, sh):
    """Two separate clamps, and each has a screen that needs it: 1280x720 needs
    the position raised (the aesthetic y put a full panel 201px off the bottom),
    800x600 needs the ceiling lowered (no position fits QUICK_MAX_HEIGHT)."""
    g = sc.quick_geometry(sw, sh)
    assert g["x"] + g["width"] <= sw
    assert g["y"] + g["max_height"] + sc.QUICK_BOTTOM_MARGIN <= sh
    assert g["max_height"] >= sc.QUICK_MIN_HEIGHT


def test_a_small_screen_trades_the_opening_position_for_a_taller_panel():
    """What the y clamp actually buys, which the fit invariant alone cannot see:
    the ceiling is derived from y, so leaving y at the aesthetic quarter-down
    would satisfy "it fits" by shrinking the panel instead. At 1280x720 that is
    471px of usable panel against 672 — the position is the cheaper thing to
    give up, and on a small screen it is the one worth giving up."""
    g = sc.quick_geometry(1280, 720)
    assert g["y"] == 0
    assert g["max_height"] >= 650
    # On a large screen the aesthetic position costs nothing and is kept.
    big = sc.quick_geometry(1920, 1080)
    assert big["y"] > 0 and big["max_height"] == sc.QUICK_MAX_HEIGHT


def test_a_small_screen_lowers_the_ceiling_rather_than_clipping_the_panel():
    assert sc.quick_geometry(1920, 1080)["max_height"] == sc.QUICK_MAX_HEIGHT
    assert sc.quick_geometry(800, 600)["max_height"] < sc.QUICK_MAX_HEIGHT
    # ...and an unreadable screen concludes nothing, rather than concluding from a zero.
    assert sc.quick_geometry(0, 0)["max_height"] == sc.QUICK_MAX_HEIGHT


def test_the_screens_ceiling_is_what_the_shell_actually_clamps_against():
    """The wiring: a ceiling computed at window creation and never consulted is
    the same as no ceiling at all."""
    s = _with_quick()
    s._quick_max_h = 400
    assert s.set_quick_height(700) == {"ok": True, "height": 400}
    assert s.quick.resized[-1] == (560, 400)


@pytest.mark.parametrize("ceiling", [None, 0, -50, "tall", 10 ** 9, float("nan")])
def test_a_nonsense_ceiling_gives_back_the_ordinary_band(ceiling):
    """A ceiling can only narrow the band. One that cannot be read must not be
    able to pin the window to its floor — that is a window the user cannot use."""
    assert sc.clamp_quick_height(500, ceiling) == 500
    assert sc.clamp_quick_height(10 ** 9, ceiling) == sc.QUICK_MAX_HEIGHT


@pytest.mark.parametrize("name,params,expect", [
    ("shell_info", "[]", ()),
    ("shell_info", "{}", ()),                       # some runtimes spell it this way
    ("shell_info", '["x"]', None),                  # no-arg verbs take no arguments
    ("hide", "[]", ()),
    ("open_main", "[]", ()),                        # "just bring the window forward"
    ("open_main", '["/journal/"]', ("/journal/",)),
    ("open_main", '["//evil.example/x"]', None),    # protocol-relative reaches another origin
    ("open_main", '["javascript:alert(1)"]', None),
    ("open_main", '["/a", "/b"]', None),
    ("open_main", "[123]", None),
    ("set_quick_height", "[300]", (300,)),
    ("set_quick_height", "[9999]", (sc.QUICK_MAX_HEIGHT,)),
    ("set_quick_height", '["300"]', None),          # a string is not a measurement
    ("set_quick_height", "[true]", None),           # bool is an int in Python, not here
    ("set_quick_height", "[]", None),
    ("set_quick_height", "not json", None),
    ("open_main", None, None),
])
def test_bridge_args(name, params, expect):
    assert sc.bridge_args(name, params) == expect


@pytest.mark.parametrize("source,name,window_uid,quick_uid,ok", [
    (Q, "hide", "w2", "w2", True),
    (Q, "open_main", "w2", "w2", True),
    (Q, "set_quick_height", "w2", "w2", True),
    # The main window is the SAME ORIGIN, so only the window id separates them.
    ("http://127.0.0.1:9000/portal/", "hide", "w1", "w2", False),
    (Q, "hide", "w1", "w2", False),                 # a page that navigated to ?quick=1
    (Q, "hide", None, "w2", False),
    (Q, "hide", "w2", None, False),                 # no quick window exists to drive
    (Q, "hide", None, None, False),
    ("about:blank", "hide", "w2", "w2", False),     # the splash is not the quick window
    ("https://evil.example/", "hide", "w2", "w2", False),
])
def test_quick_verbs_are_bound_to_the_quick_window(source, name, window_uid, quick_uid, ok):
    raw = _msg(name, "[300]" if name == "set_quick_height" else "[]")
    assert sc.message_allowed(source, raw, 9000,
                              window_uid=window_uid, quick_uid=quick_uid) is ok


def test_quick_window_cannot_press_start_daemon():
    """The narrowings run in both directions: the quick window is a daemon page
    like any other, and Start stays the splash's alone."""
    assert sc.message_allowed(Q, _msg("start_daemon"), 9000,
                              window_uid="w2", quick_uid="w2") is False


def test_shell_info_still_works_from_either_window():
    for uid in ("w1", "w2"):
        assert sc.message_allowed(Q, _msg("shell_info"), 9000,
                                  window_uid=uid, quick_uid="w2") is True


def test_the_shell_hands_the_filter_the_calling_window():
    """The wiring, not the helper: on_message must pass the uid through, or the
    quick verbs are unreachable no matter how correct the gate is."""
    s = _shell()
    s._quick_uid = "w2"
    assert s.on_message(Q, _msg("hide"), "w2") is True
    assert s.on_message(Q, _msg("hide"), "w1") is False
    assert s.on_message(Q, _msg("hide")) is False       # no uid at all


@pytest.mark.parametrize("renderer_uid,dispatched", [("w2", True), ("w1", False), (None, False)])
def test_the_guard_reads_the_uid_off_the_renderer_not_the_message(monkeypatch, renderer_uid,
                                                                  dispatched):
    """A page can put anything in the message body; it cannot set pywebview's
    per-window id. Driven through the real patched handler rather than grepped:
    a source check stays green if pywebview renames the attribute, and the
    consequence of that is every quick verb silently ceasing to dispatch."""
    wv, ec, calls = _fake_webview(monkeypatch)
    s = _shell()
    s._quick_uid = "w2"
    sh.install_bridge_guard(s)

    class Args:
        Source = Q

        def get_WebMessageAsJson(self):
            return _msg("hide")

    class Sender:
        Source = Q

    chrome = ec.EdgeChrome()
    chrome.pywebview_window = types.SimpleNamespace(uid=renderer_uid)
    chrome.on_script_notify(Sender(), Args())
    assert (len(calls["notify"]) == 1) is dispatched


def test_a_renderer_with_no_window_reference_dispatches_nothing(monkeypatch):
    """If pywebview ever stops setting `pywebview_window`, the uid is None on
    BOTH sides — and `bool(quick_uid) and window_uid == quick_uid` must not read
    None == None as a match."""
    wv, ec, calls = _fake_webview(monkeypatch)
    s = _shell()
    s._quick_uid = None
    sh.install_bridge_guard(s)

    class Args:
        Source = Q

        def get_WebMessageAsJson(self):
            return _msg("hide")

    ec.EdgeChrome().on_script_notify(types.SimpleNamespace(Source=Q), Args())
    assert calls["notify"] == []


# -- quick-window behaviour, through the fake window --------------------------
def _with_quick(**kw):
    s = _shell(**kw)
    s.quick = FakeWindow(url=Q)
    s.quick.resized = []
    s.quick.resize = lambda w, h: s.quick.resized.append((w, h))
    s.quick.evaluate_js = lambda js: None
    s._quick_uid = "w2"
    s._quick_w = 560
    return s


def test_show_quick_resets_the_height_it_was_left_at():
    """The window keeps whatever size the last answer grew it to. Reopening at
    700px because of something asked yesterday is a different window each time."""
    s = _with_quick()
    s.set_quick_height(600)
    assert s.quick.resized[-1] == (560, 600)
    s.show_quick()
    assert s.quick.resized[-1] == (560, sc.QUICK_START_HEIGHT)
    assert s._quick_visible is True


def test_hide_quick_is_idempotent_and_records_the_state():
    s = _with_quick()
    s.show_quick()
    s.hide_quick()
    s.hide_quick()
    assert s._quick_visible is False and s.quick.hidden == 2


def test_set_quick_height_clamps_whatever_the_page_asks_for():
    s = _with_quick()
    assert s.set_quick_height(10 ** 9) == {"ok": True, "height": sc.QUICK_MAX_HEIGHT}
    assert s.set_quick_height(float("nan"))["height"] == sc.QUICK_START_HEIGHT
    assert s.quick.resized[-1][1] == sc.QUICK_START_HEIGHT


def test_set_quick_height_without_a_window_answers_rather_than_raising():
    s = _shell()                                  # quick entry off: no second window
    assert s.set_quick_height(300)["ok"] is False


def test_open_main_hides_the_panel_and_navigates():
    s = _with_quick()
    s.show_quick()
    s.watch.observe(True)
    assert s.open_main("/journal/") == {"ok": True, "path": "/journal/"}
    assert s._quick_visible is False
    assert s.window.loaded[-1] == "http://127.0.0.1:9000/journal/"
    assert s.window.shown == 1


def test_open_main_with_a_refused_path_still_opens_the_window():
    """Losing the destination is a smaller failure than losing the answer."""
    s = _with_quick()
    s.watch.observe(True)
    assert s.open_main("//evil.example/x") == {"ok": True, "path": ""}
    assert s.window.shown == 1
    assert s.window.loaded == []                  # and it did NOT navigate anywhere


def test_open_main_with_no_path_just_shows_the_window():
    s = _with_quick()
    s.watch.observe(True)
    assert s.open_main() == {"ok": True, "path": ""}
    assert s.window.shown == 1 and s.window.loaded == []


def test_a_huge_json_number_is_refused_not_raised():
    """`json.loads` has no integer bound, so a page can post a 400-digit int.
    `int.__float__` raises OverflowError — an ArithmeticError, caught by neither
    TypeError nor ValueError — and this runs INSIDE the guard, where an escaping
    exception is thrown out of a .NET handler on the WebView2 message pump
    rather than refusing the message."""
    huge = "1" + "0" * 400
    # Unmeasurable, not "very tall" — the same answer `inf` and `NaN` get, since
    # a number no float can hold is not a measurement of anything.
    assert sc.clamp_quick_height(int(huge)) == sc.QUICK_START_HEIGHT
    # As a CEILING it is unreadable, which means unset, which means the ordinary
    # band — never a bound that pins the window to its floor.
    assert sc.clamp_quick_height(300, int(huge)) == 300
    raw = _msg("set_quick_height", f"[{huge}]")
    assert sc.message_allowed(Q, raw, 9000, window_uid="w2", quick_uid="w2") is True
    # ...and it is reachable with quick entry OFF, from the main window, because
    # the argument shape is checked before the window narrowing.
    assert sc.message_allowed(D, raw, 9000) is False


def test_the_guard_refuses_rather_than_raising_whatever_a_page_posts(monkeypatch):
    """The guard's own docstring promises input it cannot read is *refused,
    never passed through*. Pin that a decision which raises is a refusal too —
    without this, the only thing standing between a page and an exception on the
    message pump is every decision function being individually total."""
    wv, ec, calls = _fake_webview(monkeypatch)
    s = _shell()
    logged = []
    s.log = logged.append
    monkeypatch.setattr(s, "on_message", lambda *a: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(s, "on_navigation", lambda *a: (_ for _ in ()).throw(ValueError("boom")))
    sh.install_bridge_guard(s)

    class Args:
        Source, Uri = D, D
        Cancel = False

        def get_WebMessageAsJson(self):
            return _msg("shell_info")

    class Sender:
        Source = D

    ec.EdgeChrome().on_script_notify(Sender(), Args())
    assert calls["notify"] == [], "a message was dispatched after the guard failed"
    a = Args()
    ec.EdgeChrome().on_navigation_start(Sender(), a)
    assert a.Cancel is True, "a navigation proceeded after the guard failed"
    assert len(logged) == 2, "a failed guard must leave a trace"


def test_quick_close_does_not_veto_a_windows_session_end():
    """WinForms delivers FormClosing with WindowsShutDown to EVERY form. The
    main window has a reason hook for exactly this; a quick window that cancels
    unconditionally would veto every sign-out and restart — the failure the main
    window's whole close design exists to prevent."""
    s = _with_quick()
    s._quick_reason_hooked = True
    assert s._decide_quick_close("UserClosing") is False    # Alt+F4 → hide
    for reason in ("WindowsShutDown", "TaskManagerClosing", "ApplicationExitCall"):
        assert s._decide_quick_close(reason) is None, reason


def test_without_the_reason_hook_the_quick_window_still_hides_on_close():
    s = _with_quick()
    assert s._quick_reason_hooked is False
    assert s._on_quick_closing() is False


def test_the_quick_window_subscribes_the_reason_hook(monkeypatch, tmp_path):
    """The hook is what keeps the clause above reachable — unsubscribed, every
    close takes the `UserClosing` fallback, including a sign-out."""
    wv, ec, calls = _fake_webview(monkeypatch)
    s = sh.AttachShell(port=9000, start_path="/portal/", title="t", width=800, height=600,
                       log=lambda m: None, probe=lambda p: {"status": "ok"},
                       listening=lambda p: True, quick_shortcut="ctrl+space")
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(s, "_start_quick", lambda: None)
    s.run(storage_path=tmp_path, icon=None, ipc_address=None)
    assert s._hook_quick_close_reason in s.quick.events.shown.handlers


def test_closing_the_main_window_takes_the_quick_one_with_it():
    """pywebview leaves its message loop only when the LAST window is destroyed.
    A hidden quick window is one — so closing the main window alone leaves a
    pythonw.exe with nothing on screen, still holding the instance mutex, the
    presence mutex and the hotkey, which a relaunch then hands off to."""
    s = _with_quick()
    assert s._decide_close("UserClosing") is None      # no tray → close
    assert s.quick.destroyed is True


def test_closing_to_the_TRAY_leaves_the_quick_window_alone():
    """The opposite case: hiding to the tray must not destroy the preloaded
    page, or the hotkey would show an empty window afterwards."""
    s = _with_quick()
    s.tray = types.SimpleNamespace(alive=lambda: True, notify=lambda *a: None)
    assert s._decide_close("UserClosing") is False     # hide
    assert getattr(s.quick, "destroyed", False) is False


def test_closing_the_quick_window_hides_it_but_quit_destroys_it():
    """Frameless means no X, but Alt+F4 still arrives. Cancelling that close is
    what keeps the preloaded page alive; NOT cancelling it during a quit is what
    lets the process actually end."""
    s = _with_quick()
    assert s._on_quick_closing() is False          # cancelled, hidden instead
    s._quitting = True
    assert s._on_quick_closing() is None           # let it close
    s.quit()
    assert s.quick.destroyed is True and s.window.destroyed is True


def test_quick_entry_is_off_unless_a_shortcut_is_configured():
    """The whole feature is one value: no shortcut, no window, no hotkey."""
    s = _shell()
    assert s.quick_shortcut == "" and s.quick is None
    s._start_quick()                               # must not raise or register anything
    assert s._hotkey is None


# -- the hotkey wiring, with a fake HotkeyThread ------------------------------
class _FakeHotkey:
    """Stands in for the Win32 thread. `started` records what `_start_quick`
    actually asked for — the shortcut AND the callback — because both have been
    droppable without any test noticing."""

    made: list = []

    def __init__(self, shortcut, on_press, log=None):
        self.shortcut, self.on_press, self.error = shortcut, on_press, None
        self.ok, self.stopped, self.registered = True, False, True
        _FakeHotkey.made.append(self)

    def start(self, timeout=5.0):
        if not self.ok:
            self.error = "already held by another program"
        return self.ok

    def is_registered(self):
        return self.registered and self.ok

    def stop(self):
        self.stopped = True


def _quick_shell(monkeypatch, *, ok=True, presence=object()):
    _FakeHotkey.made = []
    import _shared.hotkey_win as hkmod

    monkeypatch.setattr(hkmod, "HotkeyThread",
                        lambda *a, **k: _FakeHotkey(*a, **k) if ok
                        else _mk_failing(*a, **k))
    s = _with_quick()
    s.quick_shortcut = "ctrl+space"
    claimed = []
    monkeypatch.setattr(s, "_claim_quick_presence", lambda: claimed.append(1))
    return s, claimed


def _mk_failing(*a, **k):
    h = _FakeHotkey(*a, **k)
    h.ok = False
    return h


def test_start_quick_registers_the_configured_shortcut_and_claims_presence(monkeypatch):
    """The four things `_start_quick` does, none of which any other test reaches
    (the run() test stubs this method out entirely). Dropping the presence claim
    is the worst of them: `command-launcher` never stands down and one press
    opens TWO windows."""
    s, claimed = _quick_shell(monkeypatch)
    s._start_quick()
    assert len(_FakeHotkey.made) == 1
    assert _FakeHotkey.made[0].shortcut == "ctrl+space"
    assert s._hotkey is _FakeHotkey.made[0]
    assert claimed == [1]


def test_the_hotkey_callback_is_the_TOGGLE_not_show_or_hide(monkeypatch):
    """Bound to `hide_quick` the panel could only ever close; bound to
    `show_quick` the hotkey could never dismiss it. Only the toggle is right,
    and the wiring is a single argument nothing else checks."""
    s, _ = _quick_shell(monkeypatch)
    s._start_quick()
    assert _FakeHotkey.made[0].on_press == s.toggle_quick


def test_a_shortcut_another_program_holds_leaves_the_launcher_alone(monkeypatch):
    """Standing down without a working hotkey is the zero-window outcome."""
    s, claimed = _quick_shell(monkeypatch, ok=False)
    s._start_quick()
    assert claimed == []
    assert s._hotkey is not None, "the handle is kept so a late registration can still be stopped"


def test_presence_is_released_when_the_hotkey_stops_being_registered(monkeypatch):
    """The mutex means 'a shell holds this shortcut'. If the message loop ends,
    an unreleased claim keeps command-launcher standing down forever — one
    press, zero windows. The tray presence has this upkeep; this is its twin."""
    s, _ = _quick_shell(monkeypatch)
    released = []
    s._quick_presence = types.SimpleNamespace(release=lambda: released.append(1))
    s._hotkey = _FakeHotkey("ctrl+space", lambda: None)
    s._check_quick()
    assert released == []                      # still registered → still claimed
    s._hotkey.registered = False
    s._check_quick()
    assert released == [1] and s._quick_presence is None


def test_a_late_registering_hotkey_still_claims_the_stand_down(monkeypatch):
    """`HotkeyThread.start()` gives up on a timeout but leaves its thread
    running, so `RegisterHotKey` can succeed after `_start_quick` already took
    the failure branch. Without a claim behind it the hotkey is live and
    `command-launcher` still fires — two windows per press, the exact thing the
    mutex exists to prevent. The watcher used to return early whenever
    `_quick_presence` was None, so it could only release, never claim."""
    s, _ = _quick_shell(monkeypatch)
    claimed = []
    monkeypatch.setattr(s, "_claim_quick_presence", lambda: claimed.append(1))
    s._quick_presence = None                   # start() reported failure
    s._hotkey = _FakeHotkey("ctrl+space", lambda: None)   # ...but it registered
    s._check_quick()
    assert claimed == [1]
    # Attempted once, not once per health tick — a mutex genuinely held by a
    # second shell would otherwise log forever.
    s._check_quick()
    assert claimed == [1]


def test_the_watcher_runs_the_quick_upkeep(monkeypatch):
    """A check nothing calls is not a check."""
    s = _shell()
    seen = []
    monkeypatch.setattr(s, "_check_quick", lambda: seen.append(1))
    s.tick()
    assert seen == [1]


def test_stop_quick_releases_both_the_hotkey_and_the_presence(monkeypatch):
    s, _ = _quick_shell(monkeypatch)
    released = []
    s._quick_presence = types.SimpleNamespace(release=lambda: released.append(1))
    s._hotkey = _FakeHotkey("ctrl+space", lambda: None)
    s._stop_quick()
    assert s._hotkey is None and released == [1]


def test_toggle_reports_was_visible_instead_of_reshowing():
    """The `toggle=True` contract: an already-visible window reports rather
    than re-showing, so one press can decide show-or-hide."""
    s = _with_quick()
    assert s.show_quick(toggle=True) == "shown"
    assert s.show_quick(toggle=True) == "was_visible"
    s._toggle_quick()
    assert s._quick_visible is False


def test_two_real_presses_cannot_interleave(monkeypatch):
    """MOD_NOREPEAT suppresses auto-repeat, not two real presses.

    Genuinely concurrent, because the single-threaded version this replaced
    could not see the lock at all: replacing both `with self._quick_lock:`
    blocks with `if True:` left the whole suite green. A toggle is
    read-then-act across TWO acquisitions of `_quick_lock`, so serialising the
    press needs `_toggle_lock`; the window ends VISIBLE (shown, then the
    second press hides, or vice versa) but never in the both-hide state that
    loses a press.
    """
    s = _with_quick()
    # Start VISIBLE. From hidden the two outcomes are indistinguishable by
    # count — serialised gives show-then-hide, and interleaved ALSO gives one
    # show and one hide, because `show_quick` has its own `_quick_lock`. From
    # visible they separate: serialised is hide-then-show (ends visible, one
    # of each), interleaved is both threads reading "was_visible" and both
    # hiding (ends hidden, two hides, no show) — the lost press.
    s.show_quick()
    s.quick.shown = 0
    s.quick.hidden = 0

    barrier = threading.Barrier(2)
    # Widen the gap between the read and the act to whatever the scheduler
    # gives us; without a real delay the race rarely reproduces.
    real_show = s.show_quick

    def slow_show(toggle=False):
        out = real_show(toggle=toggle)
        time.sleep(0.05)
        return out

    monkeypatch.setattr(s, "show_quick", slow_show)

    def press():
        barrier.wait()
        s._toggle_quick()

    threads = [threading.Thread(target=press) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert getattr(s.quick, "hidden", 0) == 1, "both presses hid — one was lost"
    assert s.quick.shown == 1
    assert s._quick_visible is True


def test_a_failed_hide_does_not_invert_the_toggle():
    """`Window.hide` can raise (it waits on `shown`, up to 20 s). Recording
    'hidden' for a window still on screen means the next press SHOWS it again
    and the hotkey can never dismiss it."""
    s = _with_quick()
    s.show_quick()
    s.quick.hide = lambda: (_ for _ in ()).throw(RuntimeError("busy"))
    s.hide_quick()
    assert s._quick_visible is True, "the window is still up; the flag must say so"


def test_shell_info_reports_whether_quick_entry_is_on():
    off = {f.__name__: f for f in _shell().exposed()}["shell_info"]()
    assert off["quick"] is False
    s = _shell()
    s.quick_shortcut = "ctrl+space"
    assert {f.__name__: f for f in s.exposed()}["shell_info"]()["quick"] is True


def test_presence_names_are_distinct_per_concern_and_per_port():
    """A shell may have a tray and no hotkey, or a hotkey and no tray. One mutex
    for both would make each plugin stand down for the other's reason."""
    from emptyos.desktop_presence import quick_presence_name, tray_presence_name

    assert quick_presence_name(9000) != tray_presence_name(9000)
    assert quick_presence_name(9000) != quick_presence_name(9002)
    assert quick_presence_name(9000).startswith("Local\\")


def test_run_shell_passes_the_shortcut_through_to_the_window():
    """The A3 dead-code lesson, applied to the next parameter: a knob threaded
    to the front door and dropped one frame short is invisible to every test of
    the parts."""
    import inspect

    from _shared import shell as shell_mod

    assert "quick_shortcut" in inspect.signature(shell_mod.run_shell).parameters
    assert "quick_shortcut=quick_shortcut" in inspect.getsource(shell_mod.run_shell)
    ed = _load_eos_desktop()
    assert "quick_shortcut" in inspect.signature(ed.run_webview).parameters
    assert "quick_shortcut=quick_shortcut" in inspect.getsource(ed.run_webview)
    assert 'quick_shortcut=dcfg["quick_shortcut"]' in inspect.getsource(ed.main)


@pytest.mark.parametrize("toml_text,expect", [
    ("", ""),
    ("[desktop]\n", ""),
    ("[desktop]\nquick_entry.enabled = false\n", ""),
    ('[desktop]\nquick_shortcut = "ctrl+alt+k"\n', ""),        # a shortcut alone is not opt-in
    ("[desktop]\nquick_entry.enabled = true\n", "ctrl+space"),
    ('[desktop]\nquick_entry.enabled = true\nquick_shortcut = "ctrl+alt+k"\n', "ctrl+alt+k"),
    ('[desktop]\nquick_entry.enabled = true\nquick_shortcut = "   "\n', "ctrl+space"),
    ("[desktop]\nquick_entry.enabled = true\nquick_shortcut = 7\n", "ctrl+space"),
    ('[desktop]\nquick_entry.enabled = "yes"\n', ""),           # truthy is not True
    ("[desktop]\nquick_entry.enabled = 1\n", ""),
    # The table spelling of the same thing — TOML's two ways to write one key.
    ("[desktop.quick_entry]\nenabled = true\n", "ctrl+space"),
])
def test_quick_entry_is_dark_until_explicitly_enabled(toml_text, expect):
    """Driven through `tomllib`, NOT a hand-built dict.

    A dotted TOML key is a nested table: `quick_entry.enabled = true` parses to
    `{"quick_entry": {"enabled": True}}`. A fixture that passes
    `{"quick_entry.enabled": True}` tests a shape tomllib never produces — and
    it passed against a reader that could not turn the feature on from the one
    spelling the docs give (`.claude/rules/audits.md`: a fixture not shaped like
    the real input tests a parser you do not ship).
    """
    import tomllib

    ed = _load_eos_desktop()
    data = tomllib.loads(toml_text)
    assert ed.desktop_config_from_dict(data)["quick_shortcut"] == expect


def test_the_documented_spelling_is_the_one_that_works():
    """The doc and the reader, compared directly. The shortcut appears in
    exactly one place a user reads — this script's own docstring — so that is
    what the reader must accept."""
    import tomllib

    ed = _load_eos_desktop()
    doc = ed.__doc__ or ""
    assert "quick_entry.enabled = true" in doc, "the docstring stopped showing a spelling"
    block = "[desktop]\nquick_entry.enabled = true\n"
    assert ed.desktop_config_from_dict(tomllib.loads(block))["quick_shortcut"]


@pytest.mark.parametrize("desk,expect", [
    ({"quick_entry": {"enabled": True}}, "ctrl+space"),
    ({"quick_entry.enabled": True}, "ctrl+space"),   # a hand-built dict may use the flat key
    ({"quick_entry": "on"}, ""),                     # not a table, not True
    ({"quick_entry": {}}, ""),
])
def test_quick_entry_reader_accepts_both_in_memory_shapes(desk, expect):
    ed = _load_eos_desktop()
    assert ed.desktop_config_from_dict({"desktop": desk})["quick_shortcut"] == expect
