"""Locate tool — find files anywhere on this computer by name.

Read-only, auto-permission. Calls the `files` search domain (voidtools
Everything / mdfind / fd) — the instant filename/path index — so the agent can
find a file on disk *outside* the vault and repo (a PDF in Downloads, a
spreadsheet in OneDrive), then Read or open it. The local mirror of WebSearch:
WebSearch finds things on the internet, Locate finds things on this machine.

Filename/path only — for searching file *contents* the agent uses Grep. Scoped
to the user's configured allowed_roots; degrades to grep when the files domain
isn't enabled (no consumer breakage, just narrower reach).
"""

from __future__ import annotations

from emptyos.sdk.agent_tools.base import Tool, ToolResult

MAX_RESULTS = 100


class LocateTool(Tool):
    name = "Locate"
    description = (
        "Find files anywhere on this computer by name or partial name — a fast "
        "filename index, NOT a content search. Use this to locate a document, PDF, "
        "spreadsheet, or any file by what it is called, including files outside the "
        "vault and repo. Returns absolute paths. To search file *contents*, use Grep "
        "instead. Results are scoped to the user's allowed file roots."
    )
    permission = "auto"
    readonly = True  # plan-mode safe — pure read
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Filename or partial name to find, e.g. 'IEC 60853' or 'budget 2026'. Must not start with '-'.",
            },
            "type": {
                "type": "string",
                "description": "Optional file-extension filter, no dot, e.g. 'pdf', 'xlsx', 'docx'.",
            },
            "limit": {"type": "integer", "description": "Max results (default 50, max 100)."},
        },
        "required": ["query"],
    }

    async def run(self, app, **kwargs) -> ToolResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, content="error: query is required")
        try:
            limit = min(int(kwargs.get("limit") or 50), MAX_RESULTS)
        except (TypeError, ValueError):
            limit = 50
        try:
            result = await app.kernel.capability("search").execute(
                query=query,
                domain="files",
                type=(kwargs.get("type") or ""),
                limit=limit,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, content=f"error: {e}")
        records = result.value if hasattr(result, "value") else result
        paths = [r.get("path", "") for r in (records or []) if r.get("path")]
        if not paths:
            return ToolResult(ok=True, content="(no files found)", display={"matches": 0})
        out = "\n".join(p.replace("\\", "/") for p in paths)
        return ToolResult(ok=True, content=out, display={"matches": len(paths)})
