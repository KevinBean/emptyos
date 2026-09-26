"""The daemon's realtime socket needs a WebSocket library installed.

uvicorn only upgrades a connection to a WebSocket when `websockets` or
`wsproto` is importable; bare `uvicorn` installs neither. Without one it logs
"No supported WebSocket library detected" and answers /ws as plain HTTP, so
every realtime connection fails while every page still loads. That is how the
englishos-cloud learner image and the public demo image shipped (2026-09-27):
both install from pyproject.toml, and a developer machine that happened to
have `websockets` installed for something else never saw it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dependency_names() -> set[str]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    names = set()
    for spec in deps:
        match = re.match(r"\s*([A-Za-z0-9_.-]+)(\[[^\]]*\])?", spec)
        if match:
            name = match.group(1).lower()
            extras = (match.group(2) or "").lower()
            names.add(name + extras)
    return names


def test_the_daemon_declares_a_websocket_library():
    names = _dependency_names()
    assert names & {"websockets", "wsproto", "uvicorn[standard]"}, (
        "pyproject.toml installs no WebSocket library: /ws would be served as plain HTTP")
