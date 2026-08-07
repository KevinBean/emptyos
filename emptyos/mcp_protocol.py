"""The MCP revision this codebase speaks — one literal, three modules.

``emptyos/mcp_server.py`` and ``emptyos/mcp_outbound_server.py`` *advertise* it
in their ``initialize`` result; ``emptyos/sdk/mcp_client.py`` *requests* it when
connecting outward. Those are opposite directions, and in principle a client
could want a revision the servers do not yet serve — but nothing in this
codebase distinguishes them today: all three meant "the revision we speak", and
kept three copies of the same string, which is a bump waiting to be applied
twice.

Deliberately an import-free leaf. ``mcp_server.py`` is spawned standalone
(``python -m emptyos.mcp_server``, launched by claude-cli) and imports nothing
but stdlib — a shared constant must not be the thing that drags ``emptyos.sdk``
into that bridge process. (``mcp_outbound_server.py`` already pulls
``emptyos.sdk.autopilot`` for its grant check, so it has no such budget; the
constraint comes from the lighter of the two.) Keep it that way — constants
only, no imports, no helpers that would tempt one.
"""

from __future__ import annotations

#: MCP protocol revision. Bump in ONE place; every module below follows.
PROTOCOL_VERSION = "2024-11-05"
