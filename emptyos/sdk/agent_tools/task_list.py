"""TaskList tool — plan-and-track scratchpad for multi-step work.

Mirrors Claude Code's TodoWrite: the agent sends the **full current list** on
every call; the tool echoes it back with a normalized shape. State lives in
the conversation history (each call becomes a tool_result the model reads on
the next iteration), so there's no server-side session store to maintain.

Typical flow:
    1. Model calls `TaskList(tasks=[...])` at the start of a multi-step task
       to lay out a plan.
    2. Before starting each task, the model calls again with that task's
       status flipped to "in_progress".
    3. After finishing, it calls with status "completed" and the next task
       marked "in_progress".

The REPL (and web UI) render each TaskList call as a live checkbox panel.
"""

from __future__ import annotations

from emptyos.sdk.agent_tools.base import Tool, ToolResult


VALID_STATUSES = {"pending", "in_progress", "completed"}


class TaskListTool(Tool):
    name = "TaskList"
    description = (
        "Create or update the task list for a multi-step piece of work. Pass the FULL "
        "current list on every call — this tool replaces the prior state entirely. "
        "Use it whenever a request takes 3+ distinct steps, spans multiple files, or "
        "you want to track progress visibly. Exactly one task should be `in_progress` "
        "at a time. Mark `completed` the moment a step finishes; don't batch at the end. "
        "Each task needs a short `content` (verb-leading, e.g. 'Scaffold manifest.toml') "
        "and a stable `id` (reuse across calls to track the same task)."
    )
    permission = "auto"  # pure planning artifact — no side effects
    readonly = True       # plan-mode safe (it IS the planning tool)
    input_schema = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "Full task list — replaces prior state.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Stable id (e.g. '1', '2', …)."},
                        "content": {"type": "string", "description": "Short action sentence."},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Current state.",
                        },
                    },
                    "required": ["id", "content", "status"],
                },
            },
        },
        "required": ["tasks"],
    }

    async def run(self, app, **kwargs) -> ToolResult:
        raw = kwargs.get("tasks")
        if not isinstance(raw, list):
            return ToolResult(ok=False, content="error: `tasks` must be an array")

        tasks: list[dict] = []
        seen_ids: set[str] = set()
        in_progress = 0
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return ToolResult(ok=False, content=f"error: task[{i}] must be an object")
            tid = str(item.get("id") or "").strip() or str(i + 1)
            content = str(item.get("content") or "").strip()
            status = str(item.get("status") or "pending").strip().lower()
            if not content:
                return ToolResult(ok=False, content=f"error: task[{i}] missing `content`")
            if status not in VALID_STATUSES:
                return ToolResult(
                    ok=False,
                    content=f"error: task[{i}] status={status!r} invalid (use pending/in_progress/completed)",
                )
            if tid in seen_ids:
                return ToolResult(ok=False, content=f"error: duplicate task id {tid!r}")
            seen_ids.add(tid)
            if status == "in_progress":
                in_progress += 1
            tasks.append({"id": tid, "content": content, "status": status})

        if in_progress > 1:
            return ToolResult(
                ok=False,
                content=f"error: {in_progress} tasks are in_progress — exactly one is allowed at a time",
            )

        counts = {
            "total": len(tasks),
            "pending": sum(1 for t in tasks if t["status"] == "pending"),
            "in_progress": in_progress,
            "completed": sum(1 for t in tasks if t["status"] == "completed"),
        }

        # Text representation the model reads back on the next turn.
        lines = []
        symbol = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        for t in tasks:
            lines.append(f"{symbol[t['status']]} {t['id']}. {t['content']}")
        body = "\n".join(lines) if lines else "(empty list)"
        content = (
            f"Task list ({counts['completed']}/{counts['total']} done, "
            f"{counts['in_progress']} in progress):\n{body}"
        )

        return ToolResult(
            ok=True,
            content=content,
            display={
                "name": "TaskList",
                "tasks": tasks,
                "counts": counts,
            },
        )
