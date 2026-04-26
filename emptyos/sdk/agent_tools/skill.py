"""Skill tool — Claude-Code-compatible skill invocation.

Skills are markdown playbooks the agent can pull in on demand. The system
prompt lists every available skill (name + description — progressive
disclosure); this tool fetches the full SKILL.md when the model decides a
task matches a skill.

Two ops:
    - list  → catalog (name + description, usually redundant with the prompt)
    - load  → full SKILL.md body for a given skill name

Read-only, auto-permission.
"""

from __future__ import annotations

from apps.agent.skills import discover_skills
from emptyos.sdk.agent_tools.base import Tool, ToolResult


class SkillTool(Tool):
    name = "Skill"
    description = (
        "Load a Claude Code skill's full instructions. Skills are markdown "
        "playbooks (SKILL.md files) for recurring tasks. The system prompt "
        "lists every available skill with a short description; when a user "
        "request matches one, call this tool with op='load' and the skill "
        "name to read the full playbook, then follow it. "
        "Use op='list' only when the skill list isn't already in context."
    )
    permission = "auto"
    readonly = True  # plan-mode safe — just reads SKILL.md files
    input_schema = {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["list", "load"],
                "description": "'list' returns the catalog; 'load' fetches a specific SKILL.md.",
            },
            "name": {
                "type": "string",
                "description": "Skill name (required for op='load').",
            },
        },
        "required": ["op"],
    }

    async def run(self, app, **kwargs) -> ToolResult:
        op = (kwargs.get("op") or "").strip().lower()
        if op not in ("list", "load"):
            return ToolResult(ok=False, content="error: op must be 'list' or 'load'")

        catalog = discover_skills(app.repo_root)

        if op == "list":
            if not catalog:
                return ToolResult(ok=True, content="(no skills installed)", display={"count": 0})
            lines = [
                f"- {s.name}  [{s.source}]  — {s.description}"
                for s in sorted(catalog.values(), key=lambda s: s.name)
            ]
            return ToolResult(
                ok=True,
                content=f"{len(catalog)} skill(s):\n" + "\n".join(lines),
                display={"count": len(catalog)},
            )

        # op == "load"
        name = (kwargs.get("name") or "").strip()
        if not name:
            return ToolResult(ok=False, content="error: `name` is required for op='load'")
        skill = catalog.get(name)
        if not skill:
            available = ", ".join(sorted(catalog.keys())[:30])
            return ToolResult(
                ok=False,
                content=f"error: skill {name!r} not found. Available: {available}",
            )
        try:
            text = skill.path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(ok=False, content=f"error reading skill: {e}")
        return ToolResult(
            ok=True,
            content=text,
            display={"name": skill.name, "source": skill.source, "path": str(skill.path)},
        )
