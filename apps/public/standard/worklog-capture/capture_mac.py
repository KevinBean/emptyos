"""macOS screen + context capture primitives.

Five one-shot subprocess calls behind a small injectable runner so the output
parsers unit-test with canned strings (no real screen needed):

    grab_screen      /usr/sbin/screencapture -x   (Screen Recording permission)
    frontmost_app    lsappinfo                     (NO permission — preferred)
    window_title     osascript System Events       (Accessibility/Automation)
    browser_context  osascript Chrome/Safari       (Automation)
    selected_text    System Events Cmd+C + pbpaste (Accessibility; default-off)

Every primitive is individually exception-guarded: a denied permission (or a
missing tool) degrades to an empty field, NEVER a failed capture. This module is
macOS-only and is the future ``plugins/screen-capture/`` boundary — a Windows
port swaps the backend without touching app logic (see INTENT.md).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional

# A runner takes argv (+ optional stdin bytes) and returns (returncode, stdout, stderr).
Runner = Callable[..., Awaitable[tuple[int, str, str]]]

# Frontmost-app name → browser family, for the optional URL/title grab.
_CHROME_FAMILY = {
    "Google Chrome", "Google Chrome Canary", "Google Chrome Beta",
    "Brave Browser", "Microsoft Edge", "Arc", "Chromium", "Vivaldi",
}
_SAFARI_FAMILY = {"Safari", "Safari Technology Preview"}


async def _default_run(argv, *, stdin: Optional[bytes] = None, timeout: float = 8.0):
    """Spawn ``argv``, return ``(rc, stdout, stderr)``; ``(-1, "", <err>)`` on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(input=stdin), timeout)
        return (
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )
    except Exception as e:  # missing binary, timeout, etc. — never raise
        return -1, "", str(e)


# ── pure parsers (unit-tested with canned strings) ────────────────────────────
def parse_lsappinfo_name(info_out: str) -> str:
    """``"LSDisplayName"="Google Chrome"`` → ``Google Chrome`` (last quoted token)."""
    quoted = re.findall(r'"([^"]*)"', info_out or "")
    return quoted[-1].strip() if quoted else ""


def is_browser(app_name: str) -> str:
    """Return ``"chrome"`` / ``"safari"`` / ``""`` for a frontmost-app name."""
    if app_name in _CHROME_FAMILY:
        return "chrome"
    if app_name in _SAFARI_FAMILY:
        return "safari"
    return ""


def parse_browser_context(osa_out: str) -> dict:
    """Two-line osascript output (URL\\ntitle) → ``{"url":..., "title":...}``."""
    lines = [ln.strip() for ln in (osa_out or "").splitlines()]
    lines = [ln for ln in lines if ln]
    url = lines[0] if lines else ""
    title = lines[1] if len(lines) > 1 else ""
    if not url.startswith(("http://", "https://", "file://", "ftp://")):
        url = ""  # missing-value / error text is not a URL
    return {"url": url, "title": title}


# ── async primitives ──────────────────────────────────────────────────────────
async def grab_screen(dest: str | Path, *, run: Runner | None = None) -> bool:
    """``screencapture -x`` (no shutter sound, whole screen) → True on a real file."""
    run = run or _default_run
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    rc, _out, _err = await run(["/usr/sbin/screencapture", "-x", str(dest)])
    return rc == 0 and dest.exists() and dest.stat().st_size > 0


async def frontmost_app(*, run: Runner | None = None) -> str:
    """Frontmost app display name via ``lsappinfo`` (needs no permission)."""
    run = run or _default_run
    try:
        rc, front, _ = await run(["lsappinfo", "front"])
        asn = (front or "").strip()
        if rc != 0 or not asn:
            return ""
        rc, info, _ = await run(["lsappinfo", "info", "-only", "name", asn])
        return parse_lsappinfo_name(info) if rc == 0 else ""
    except Exception:
        return ""


async def window_title(*, run: Runner | None = None) -> str:
    """Frontmost window title via System Events (Accessibility/Automation; optional)."""
    run = run or _default_run
    script = (
        'tell application "System Events" to get title of front window of '
        "(first application process whose frontmost is true)"
    )
    try:
        rc, out, _ = await run(["osascript", "-e", script])
        return out.strip() if rc == 0 else ""
    except Exception:
        return ""


async def browser_context(app_name: str, *, run: Runner | None = None) -> dict:
    """URL + tab title of the frontmost browser window (Automation; only for browsers)."""
    fam = is_browser(app_name)
    if not fam:
        return {"url": "", "title": ""}
    run = run or _default_run
    if fam == "chrome":
        script = (
            f'tell application "{app_name}"\n'
            "  set u to URL of active tab of front window\n"
            "  set t to title of active tab of front window\n"
            "  return u & linefeed & t\n"
            "end tell"
        )
    else:  # safari
        script = (
            'tell application "Safari"\n'
            "  set u to URL of front document\n"
            "  set t to name of front document\n"
            "  return u & linefeed & t\n"
            "end tell"
        )
    try:
        rc, out, _ = await run(["osascript", "-e", script])
        return parse_browser_context(out) if rc == 0 else {"url": "", "title": ""}
    except Exception:
        return {"url": "", "title": ""}


async def selected_text(*, run: Runner | None = None, settle: float = 0.3) -> str:
    """Current selection via Cmd+C round-trip (Accessibility; clobbers clipboard).

    Saves and restores the clipboard around a simulated copy. Default-off in the
    app — it simulates input and momentarily overwrites the clipboard.
    """
    run = run or _default_run
    saved = ""
    try:
        _rc, saved, _ = await run(["pbpaste"])
        await run(["osascript", "-e",
                   'tell application "System Events" to keystroke "c" using command down'])
        await asyncio.sleep(settle)
        _rc, new, _ = await run(["pbpaste"])
        return (new or "").strip()
    except Exception:
        return ""
    finally:
        try:  # best-effort restore of the user's clipboard
            await run(["pbcopy"], stdin=(saved or "").encode("utf-8"))
        except Exception:
            pass
