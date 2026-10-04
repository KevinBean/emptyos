"""Hello Connector — the smallest installable example plugin.

Copy this folder as a starting point for a plugin or connection. A
"connection" is just a plugin carrying the `connector` tag (see
.claude/rules/store.md) — the Store surfaces those in a filtered Connections
lens.

IMPORTANT — plugins are higher trust than apps: connect() (and optional
auto_start()) run automatically at daemon boot, so the marketplace runs a
MANDATORY static scan on every plugin install and shows a stronger warning.
Real connectors validate API keys / test connectivity in connect().
"""

from __future__ import annotations

from emptyos.sdk import BasePlugin


class HelloConnectorPlugin(BasePlugin):
    """Registers a no-op service. Apps reach it via self.require("hello-eos-connector")."""

    async def connect(self) -> None:
        # A real connector would read config + test the remote service here.
        pass

    async def available(self) -> bool:
        return True

    async def hello(self) -> str:
        return "Hello from a marketplace-installed connector!"
