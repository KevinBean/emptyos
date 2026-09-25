"""MCPProxyTool — wraps one tool from an external MCP server as an agent Tool.

Built by the inbound-MCP wiring (``[apps.agent] feature.mcp-inbound.enabled``,
agent app setup). The name is namespaced ``mcp__<server>__<tool>`` (Claude
Code's convention) so a remote tool can never collide with a native one.

``permission = "ask"`` (read-only-first): every call goes through the existing
``ToolConsentManager`` — there is no MCP-specific bypass. ``readonly = False`` so
plan mode blocks it: we can't prove a remote tool is side-effect-free, so we
treat it as mutating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk.agent_tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from emptyos.sdk import BaseApp
    from emptyos.sdk.mcp_client import MCPClient


class MCPProxyTool(Tool):
    permission = "ask"
    readonly = False

    def __init__(
        self,
        client: "MCPClient",
        remote_name: str,
        description: str = "",
        input_schema: dict | None = None,
    ):
        self._client = client
        self._remote_name = remote_name
        self.name = f"mcp__{client.spec.id}__{remote_name}"
        self.description = description or f"(MCP {client.spec.id}) {remote_name}"
        self.input_schema = input_schema or {"type": "object", "properties": {}}

    async def run(self, app: "BaseApp", **kwargs) -> ToolResult:
        try:
            text = await self._client.call_tool(self._remote_name, kwargs)
        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"error: {type(e).__name__}: {e}",
                display={"name": self.name, "mcp": self._client.spec.id},
            )
        ok = not (isinstance(text, str) and text.startswith("error:"))
        # EVERYTHING the server returned is fenced, including what it calls an
        # error. `ok` is inferred from the server's own text, so gating the
        # fence on it would let the untrusted party opt out of being fenced by
        # prefixing "error:" — which is exactly what a hostile server would do.
        # Our OWN errors return early above and never reach this line.
        # (.claude/rules/untrusted-content.md; dark-flagged like every other
        # consumer, so with the flag off the result is byte-identical.)
        text = self._fence(app, text)
        return ToolResult(
            ok=ok, content=text, display={"name": self.name, "mcp": self._client.spec.id}
        )

    def _fence(self, app: "BaseApp", text: str) -> str:
        """Wrap the server's answer in the untrusted-source fence, when the
        driving app has the flag on. Never raises: a fencing failure must not
        lose the user the answer they asked for."""
        try:
            from emptyos.sdk.web_search import source_fencer

            return source_fencer(app).wrap(text, label=f"mcp:{self._client.spec.id}")
        except Exception as e:  # noqa: BLE001 — see docstring
            # Say so. A silent fail-open makes "fencing is on" and "fencing is
            # not running" indistinguishable at runtime, which is how a
            # security control decays into a comment.
            try:
                app.log(f"mcp: could not fence {self.name} output: {e}", level="warning")
            except Exception:  # noqa: BLE001 — no app, or no logger on it
                pass
            return text


def proxy_tools_for(client: "MCPClient") -> list[MCPProxyTool]:
    """One MCPProxyTool per tool the server advertised in tools/list."""
    out: list[MCPProxyTool] = []
    for t in client.tools or []:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        out.append(
            MCPProxyTool(
                client,
                name,
                t.get("description", ""),
                t.get("inputSchema") or t.get("input_schema"),
            )
        )
    return out
