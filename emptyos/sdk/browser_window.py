"""Browser app-window helper — find a Chromium-family browser and open a
borderless ``--app=`` window pointing at a local URL.

Extracted from ``plugins/command-launcher/plugin.py`` when a second consumer
(``scripts/eos_desktop.py``, the desktop daily-driver launcher) needed the same
"find browser + open chromeless window" logic (CLAUDE.md Dev Rule 9).

Pure module — no kernel import, no daemon boot. Safe to import from a
standalone script per ``.claude/rules/daemon-handling.md`` (``emptyos.sdk.*``
is always import-safe).

Consumers:
- ``plugins/command-launcher/plugin.py`` — hotkey-toggled command palette window
- ``scripts/eos_desktop.py`` — the desktop daily-driver window
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Flags Chrome/Edge accept that make an --app window behave like an app surface
# rather than a fresh browser session. Baked in so every consumer gets them.
_BASE_FLAGS = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=TranslateUI",
)


def find_chromium() -> tuple[str, str] | None:
    """Locate a Chromium-family browser. Returns ``(path, name)`` or ``None``.

    Order: Chrome → Edge (always on Windows 10+) → Brave. Edge is the safe
    fallback because Windows ships it.
    """
    if sys.platform == "win32":
        candidates = [
            (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
            (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chrome"),
            (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "edge"),
            (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "edge"),
            (r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe", "brave"),
        ]
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            candidates.insert(0, (os.path.join(local_appdata, r"Google\Chrome\Application\chrome.exe"), "chrome"))
        for path, name in candidates:
            if os.path.exists(path):
                return path, name
    elif sys.platform == "darwin":
        candidates = [
            ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chrome"),
            ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "edge"),
            ("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", "brave"),
        ]
        for path, name in candidates:
            if os.path.exists(path):
                return path, name
    else:
        # Linux — rely on PATH
        from shutil import which
        for name in ("google-chrome", "chromium", "microsoft-edge", "brave-browser"):
            path = which(name)
            if path:
                return path, name
    return None


def build_app_window_args(
    browser_path: str,
    url: str,
    *,
    width: int = 1440,
    height: int = 900,
    profile_dir: str | os.PathLike,
    extra_args: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Build the full argv for a chromeless ``--app`` window. Pure — no spawn,
    no filesystem mutation — so it's unit-testable without a browser present.

    The ``profile_dir`` isolates the window from the user's main browser profile
    (no logged-in accounts, no extensions); it persists cookies across launches.
    """
    return [
        browser_path,
        f"--app={url}",
        f"--window-size={int(width)},{int(height)}",
        f"--user-data-dir={os.fspath(profile_dir)}",
        *_BASE_FLAGS,
        *extra_args,
    ]


def open_app_window(
    url: str,
    *,
    browser: tuple[str, str] | None = None,
    width: int = 1440,
    height: int = 900,
    profile_dir: str | os.PathLike,
    extra_args: tuple[str, ...] | list[str] = (),
) -> subprocess.Popen:
    """Spawn a detached chromeless ``--app`` window at ``url``.

    ``browser`` is an optional ``(path, name)`` from :func:`find_chromium`; when
    omitted it is resolved here. Raises ``RuntimeError`` if no browser is found.
    The child is detached (Windows: ``DETACHED_PROCESS`` + new process group;
    POSIX: new session) so it outlives the spawning process.
    """
    if browser is None:
        browser = find_chromium()
    if browser is None:
        raise RuntimeError("No Chromium-family browser found (Chrome / Edge / Brave).")

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    args = build_app_window_args(
        browser[0], url, width=width, height=height,
        profile_dir=profile_dir, extra_args=extra_args,
    )

    kwargs: dict = dict(
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True

    return subprocess.Popen(args, **kwargs)
