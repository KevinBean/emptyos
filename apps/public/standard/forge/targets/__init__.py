"""Target registry — data-driven dispatch.

Add a new target by writing a sibling module that exposes a class
satisfying the `Target` Protocol (see base.py) and registering it here.
Mirrors plugins/agent-runtime/plugin.py:52 DEFAULT_CLI_ADAPTERS.
"""

from __future__ import annotations

from .base import (
    BuildResult,
    Check,
    LogCallback,
    ProcessRecord,
    ReleaseResult,
    ScaffoldCtx,
    ScaffoldResult,
    Target,
)
from .cli import CliTarget
from .native_android import NativeAndroidTarget
from .tauri import TauriTarget

TARGETS: dict[str, Target] = {
    "tauri": TauriTarget(),
    "cli": CliTarget(),
    "native-android": NativeAndroidTarget(),
}

# Placeholders surfaced in the UI as "coming soon" cards — they have no
# implementation, only metadata. Implement by promoting them into TARGETS.
COMING_SOON: list[dict] = [
    {"id": "flutter", "name": "Flutter", "description": "Cross-platform mobile + desktop apps (Dart)."},
    {"id": "electron", "name": "Electron", "description": "Cross-platform desktop apps (Node + Chromium)."},
]

__all__ = [
    "BuildResult",
    "Check",
    "COMING_SOON",
    "LogCallback",
    "ProcessRecord",
    "ReleaseResult",
    "ScaffoldCtx",
    "ScaffoldResult",
    "TARGETS",
    "Target",
]
