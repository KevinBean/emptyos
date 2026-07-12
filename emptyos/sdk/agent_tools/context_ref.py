"""ContextRef tool — recover the exact original behind a packed ctx_* ref.

When a large tool output is compressed by the Context Packing domain
(``emptyos/context/``), the model sees a summary plus a ``ctx_<id>`` handle and
a hint to "ask for ctx_X if exact lines are needed". This tool is how it asks:
it returns the byte-exact original text the summary was derived from, until the
ref's TTL expires.

Read-only and auto-approved — it only reads ``data/context/refs/``.
"""

from __future__ import annotations

from emptyos.sdk.agent_tools.base import Tool, ToolResult


class ContextRefTool(Tool):
    name = "ContextRef"
    description = (
        "Retrieve the exact, full original text behind a packed `ctx_<id>` "
        "reference. When a large tool output was summarized, its summary carries "
        "a `ctx_...` id — pass that id here to get the untruncated original. "
        "Auto-approved (read-only)."
    )
    permission = "auto"
    readonly = True  # plan-mode safe
    input_schema = {
        "type": "object",
        "properties": {
            "ref_id": {
                "type": "string",
                "description": "The ctx_* id from a packed summary (e.g. 'ctx_3f9a1b2c0d').",
            },
        },
        "required": ["ref_id"],
    }

    def is_readonly(self, input: dict) -> bool:
        return True

    def permission_for(self, input: dict) -> str:
        return "auto"

    def permission_summary(self, input: dict) -> str:
        return f"ContextRef: recover {input.get('ref_id', '?')}"

    async def run(self, app, **kwargs) -> ToolResult:
        ref_id = (kwargs.get("ref_id") or "").strip()
        if not ref_id:
            return ToolResult(ok=False, content="error: ref_id is required")
        if app is None or not hasattr(app, "kernel"):
            return ToolResult(ok=False, content="error: agent app reference unavailable")
        try:
            from emptyos.context import load_original

            store_root = app.kernel.config.data_dir / "context"
            original = load_original(store_root, ref_id)
        except Exception as e:
            return ToolResult(ok=False, content=f"error: {e}")
        if original is None:
            return ToolResult(
                ok=True,
                content=(
                    f"{ref_id} is not available — it is unknown, malformed, or its "
                    f"retention window (TTL) has expired. Work from the summary, or "
                    f"re-run the tool that produced it."
                ),
            )
        return ToolResult(
            ok=True,
            content=original,
            display={"ref_id": ref_id, "chars": len(original)},
        )
