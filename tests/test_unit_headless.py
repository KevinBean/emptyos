"""Unit tests for the headless-process console guard (``emptyos/headless.py``).

Daemon-free. The pure half (flag arithmetic + the ``Popen.__init__`` wrapper)
is driven against a fake constructor so the test asserts exactly what reaches
``subprocess.Popen``. One Windows-only test runs the real mechanism end to
end: a DETACHED child (no console) spawns a grandchild that reports whether
it received a console — non-zero without the guard, zero with it. Both
directions are asserted in the same test, because a guard that "passes" on a
machine where the grandchild never got a console anyway proves nothing.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from emptyos import headless
from emptyos.headless import (
    CREATE_NEW_CONSOLE,
    CREATE_NO_WINDOW,
    DETACHED_PROCESS,
    hidden_creationflags,
    install_headless_subprocess_guard,
    no_window_flags,
    wrap_popen_init,
)

CREATE_NEW_PROCESS_GROUP = 0x00000200


# ── pure: flag arithmetic ────────────────────────────────────────────────


def test_no_intent_gains_no_window():
    assert hidden_creationflags(0) == CREATE_NO_WINDOW


def test_unrelated_flags_are_preserved_and_no_window_added():
    out = hidden_creationflags(CREATE_NEW_PROCESS_GROUP)
    assert out & CREATE_NEW_PROCESS_GROUP
    assert out & CREATE_NO_WINDOW


@pytest.mark.parametrize("intent", [CREATE_NEW_CONSOLE, DETACHED_PROCESS, CREATE_NO_WINDOW])
def test_explicit_console_intent_is_left_alone(intent):
    flags = intent | CREATE_NEW_PROCESS_GROUP
    assert hidden_creationflags(flags) == flags


def test_no_window_flags_matches_platform():
    if sys.platform == "win32":
        assert no_window_flags() == subprocess.CREATE_NO_WINDOW == CREATE_NO_WINDOW
    else:
        assert no_window_flags() == 0


@pytest.mark.skipif(sys.platform != "win32", reason="constants only exist on Windows")
def test_constants_match_the_interpreter():
    # A wrong CREATE_NEW_CONSOLE would OR CREATE_NO_WINDOW onto system-tray's
    # new-console spawn — a combination CreateProcess rejects.
    assert (CREATE_NEW_CONSOLE, DETACHED_PROCESS, CREATE_NO_WINDOW) == (
        subprocess.CREATE_NEW_CONSOLE, subprocess.DETACHED_PROCESS, subprocess.CREATE_NO_WINDOW,
    )


def test_positional_index_matches_popen_signature():
    import inspect

    params = list(inspect.signature(subprocess.Popen.__init__).parameters)
    assert params.index("creationflags") - 1 == headless._CREATIONFLAGS_POS  # minus self


# ── pure: the wrapper — what reaches the real constructor ───────────────


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, self_, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_wrapper_adds_flag_when_caller_passed_none():
    rec = _Recorder()
    wrap_popen_init(rec)(object(), ["git", "--version"], stdout=subprocess.PIPE)
    (args, kwargs), = rec.calls
    assert args == (["git", "--version"],)
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["creationflags"] == CREATE_NO_WINDOW


def test_wrapper_merges_into_keyword_flags():
    rec = _Recorder()
    wrap_popen_init(rec)(object(), ["x"], creationflags=CREATE_NEW_PROCESS_GROUP)
    (_, kwargs), = rec.calls
    assert kwargs["creationflags"] == CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def test_wrapper_respects_keyword_console_intent():
    rec = _Recorder()
    wrap_popen_init(rec)(object(), ["x"], creationflags=CREATE_NEW_CONSOLE)
    (_, kwargs), = rec.calls
    assert kwargs["creationflags"] == CREATE_NEW_CONSOLE


def test_wrapper_handles_positional_creationflags():
    # Popen(args, bufsize, executable, stdin, stdout, stderr, preexec_fn,
    #       close_fds, shell, cwd, env, universal_newlines, startupinfo,
    #       creationflags) — index 13 after self.
    rec = _Recorder()
    positional = [["x"], -1, None, None, None, None, None, True, False, None, None, False, None, 0]
    wrap_popen_init(rec)(object(), *positional)
    (args, kwargs), = rec.calls
    assert args[13] == CREATE_NO_WINDOW
    assert "creationflags" not in kwargs


def test_is_installed_reads_the_live_attribute(monkeypatch):
    # The marker is what install_* consults, so a later reassignment of
    # Popen.__init__ that drops the wrapper reads as "not installed".
    monkeypatch.setattr(subprocess.Popen, "__init__", wrap_popen_init(_Recorder()))
    assert headless.is_installed() is True
    monkeypatch.setattr(subprocess.Popen, "__init__", _Recorder())
    assert headless.is_installed() is False


# ── install: idempotent, platform-gated, restores cleanly ────────────────


@pytest.fixture
def restore_popen():
    orig = subprocess.Popen.__init__
    was = headless._installed
    yield
    subprocess.Popen.__init__ = orig  # type: ignore[method-assign]
    headless._installed = was


def test_install_is_noop_off_windows_or_with_console(monkeypatch, restore_popen):
    if sys.platform != "win32":
        assert install_headless_subprocess_guard() is False
        assert subprocess.Popen.__init__ is not None
        return
    monkeypatch.setattr(headless, "has_console", lambda: True)
    headless._installed = False
    before = subprocess.Popen.__init__
    assert install_headless_subprocess_guard() is False
    assert subprocess.Popen.__init__ is before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console semantics")
def test_install_wraps_once(monkeypatch, restore_popen):
    monkeypatch.setattr(headless, "has_console", lambda: False)
    headless._installed = False
    assert install_headless_subprocess_guard() is True
    wrapped = subprocess.Popen.__init__
    assert getattr(wrapped, "__eos_headless_guard__", False) is True
    assert install_headless_subprocess_guard() is True
    assert subprocess.Popen.__init__ is wrapped  # second call did not re-wrap


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console semantics")
def test_install_reinstalls_after_wrapper_was_dropped(monkeypatch, restore_popen):
    monkeypatch.setattr(headless, "has_console", lambda: False)
    headless._installed = False
    assert install_headless_subprocess_guard() is True
    subprocess.Popen.__init__ = _Recorder()  # type: ignore[method-assign]  # someone re-patched
    assert install_headless_subprocess_guard() is True
    assert getattr(subprocess.Popen.__init__, "__eos_headless_guard__", False) is True


# ── consumers: the shared helpers pass the flag even without the guard ───
#
# The process-level guard covers the daemon; these two helpers are also
# imported by scripts that run console-less without ever calling `eos start`,
# so each passes the flag itself. Pinned at the call site (what the helper
# hands to the spawner), not by grepping the source.


def test_git_run_passes_no_window_flag(monkeypatch, tmp_path):
    import emptyos.sdk.worktree as wt

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)

        class R:
            returncode, stdout, stderr = 0, "main\n", ""

        return R()

    monkeypatch.setattr(wt.subprocess, "run", fake_run)
    assert wt.git_run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path)[0] == 0
    assert seen["creationflags"] == no_window_flags()


def test_run_command_passes_no_window_flag(monkeypatch):
    import asyncio

    import emptyos.sdk.proc as proc

    seen = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_exec(*argv, **kwargs):
        seen.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(proc.asyncio, "create_subprocess_exec", fake_exec)
    out = asyncio.run(proc.run_command(["git", "--version"], timeout=5))
    assert out.ok
    assert seen["creationflags"] == no_window_flags()


# ── real mechanism, three directions ─────────────────────────────────────
#
# The middle process is the daemon stand-in. It prints two numbers: what the
# guard's install returned (1/0) and the grandchild's console HWND.

_CHILD = textwrap.dedent(
    """
    import subprocess, sys
    sys.path.insert(0, sys.argv[1])
    installed = 0
    if sys.argv[2] == "guard":
        from emptyos.headless import install_headless_subprocess_guard
        installed = int(install_headless_subprocess_guard())
    probe = [sys.executable, "-c",
             "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"]
    r = subprocess.run(probe, capture_output=True, text=True, timeout=30)
    print(installed, r.stdout.strip())
    """
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console semantics")
def test_real_mechanism_detached_vs_no_window_parent(tmp_path):
    """DETACHED middle (no console): the grandchild gets a console — non-zero
    HWND — unless the guard installs. CREATE_NO_WINDOW middle (hidden
    console): the grandchild inherits it (HWND 0) and the guard must decline
    to install, or it would add a conhost per spawn in every sandbox member.
    The unguarded DETACHED direction opens one real console window briefly."""
    from pathlib import Path

    repo = str(Path(__file__).resolve().parents[1])
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")

    def run(mode: str, flags: int) -> tuple[int, int]:
        r = subprocess.run(
            [sys.executable, str(script), repo, mode],
            capture_output=True, text=True, timeout=60, creationflags=flags,
        )
        assert r.returncode == 0, r.stderr
        installed, hwnd = r.stdout.strip().splitlines()[-1].split()
        return int(installed), int(hwnd)

    _, plain_hwnd = run("plain", DETACHED_PROCESS)
    if plain_hwnd == 0:
        # A non-interactive window station (ssh, a service session) may hand
        # out a console with no window; the fixture precondition failed, not
        # the code.
        pytest.skip("unguarded grandchild got no window HWND on this window station")

    installed, hwnd = run("guard", DETACHED_PROCESS)
    assert installed == 1, "guard must install in a process with no console"
    assert hwnd == 0, "guarded grandchild must not receive a console"

    installed, hwnd = run("guard", CREATE_NO_WINDOW)
    assert installed == 0, "guard must NOT install in a process that has a hidden console"
    assert hwnd == 0, "grandchild of a CREATE_NO_WINDOW parent inherits, gets no window"
