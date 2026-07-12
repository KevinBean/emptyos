"""Capture facade — dispatch screen/context primitives by OS.

The plan's ``capture_mac.py`` was written as the future per-OS boundary; the
Windows backend (homepc) is the second platform that earns it. This thin facade
keeps one call surface (``grab_screen`` / ``frontmost_app`` / ``window_title`` /
``browser_context`` / ``selected_text``) and routes to ``capture_mac`` on macOS,
``capture_win`` on Windows, and safe empties elsewhere. ``app.py`` imports this,
never a backend directly.

Both backends are pure def-modules (no OS calls at import), so importing both on
any OS is harmless; only the selected one is ever invoked.
"""
from __future__ import annotations

import sys

from . import capture_mac, capture_win

if sys.platform == "darwin":
    _BACKEND = capture_mac
elif sys.platform.startswith("win"):
    _BACKEND = capture_win
else:  # linux / unknown — no capture backend yet
    _BACKEND = None

BACKEND_NAME = ("mac" if _BACKEND is capture_mac
                else "win" if _BACKEND is capture_win else "none")


async def grab_screen(dest, *, run=None) -> bool:
    if _BACKEND is None:
        return False
    return await _BACKEND.grab_screen(dest, run=run)


async def frontmost_app(*, run=None) -> str:
    if _BACKEND is None:
        return ""
    return await _BACKEND.frontmost_app(run=run)


async def window_title(*, run=None) -> str:
    if _BACKEND is None:
        return ""
    return await _BACKEND.window_title(run=run)


async def browser_context(app_name: str, *, run=None) -> dict:
    if _BACKEND is None:
        return {"url": "", "title": ""}
    return await _BACKEND.browser_context(app_name, run=run)


async def selected_text(*, run=None, settle: float = 0.3) -> str:
    if _BACKEND is None:
        return ""
    return await _BACKEND.selected_text(run=run, settle=settle)
