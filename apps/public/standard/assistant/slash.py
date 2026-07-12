"""Assistant — slash command discovery + dispatch.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the manifest-driven slash command table and the dispatcher
that routes `/foo` text into `call_app(...)` payloads. Built-in commands
(`/help`, `/new`, `/export`, `/archive`) are handled inline; everything
else dispatches via the discovered table.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._auto_archive`` (sessions.py mixin)
on `/archive`.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .prompts import BUILTIN_OVERRIDES

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   _discover_slash_commands = _slash._discover_slash_commands
#   _handle_slash            = _slash._handle_slash
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _discover_slash_commands(self) -> dict:
    """Build the slash command table {slash: (app_id, method, arg)}.

    Source of truth is the unified verb registry ([[provides.verbs]],
    surfaces ∋ "assistant") for migrated apps; legacy [provides.assistant]
    for the rest. All-or-nothing per (app, assistant): an app with ANY
    registry assistant verb has its legacy [provides.assistant] skipped, so a
    slash command never registers twice. See .claude/rules/verb-registry.md.
    """
    loader = self.kernel.apps
    commands: dict = {}
    migrated_assistant_apps: set[str] = set()

    registry = None
    try:
        registry = loader.get_verbs()
    except Exception:
        registry = None
    if registry is not None:
        migrated_assistant_apps = registry.apps_for_surface("assistant")
        for e in registry.for_surface("assistant"):
            a = e.assistant or {}
            slash = a.get("slash")
            if slash:
                # assistant.method overrides the canonical method (the slash
                # handler may differ from the agent/mcp dispatch target).
                commands[slash] = (e.app_id, a.get("method") or e.method, a.get("arg"))

    for app_id, manifest in loader.manifests.items():
        if app_id in migrated_assistant_apps:
            continue
        assistant_section = manifest.provides.get("assistant", {})
        for cmd_def in assistant_section.get("commands", []):
            slash = cmd_def.get("slash", "")
            if slash and slash not in commands:
                commands[slash] = (app_id, cmd_def["method"], cmd_def.get("arg"))
    return commands


async def _handle_slash(self, text: str) -> str | None:
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/help":
        lines = ["**Available commands:**"]
        for c, (app, method, _) in sorted(self._slash_commands.items()):
            lines.append(f"  `{c}` — {app}.{method}")
        lines.append("  `/export` — export this session to vault")
        lines.append("  `/archive` — archive old sessions to vault")
        lines.append("  `/new` — new conversation")
        lines.append("  `/help` — this list")
        return "\n".join(lines)

    if cmd == "/new":
        return None  # handled by frontend

    if cmd == "/export":
        return "__EXPORT__"  # sentinel — handled by ws_chat with session context

    if cmd == "/archive":
        archived = await self._auto_archive()
        if archived:
            names = ", ".join(a["name"] for a in archived)
            return f"Archived {len(archived)} sessions to vault: {names}"
        return "No sessions to archive (need >5 messages and >7 days idle)."

    # Prefix matching: /ima → /image, /tas → /tasks
    if cmd not in self._slash_commands:
        matches = [c for c in self._slash_commands if c.startswith(cmd)]
        if len(matches) == 1:
            cmd = matches[0]

    if cmd in self._slash_commands:
        app_id, method, arg_name = self._slash_commands[cmd]
        override = BUILTIN_OVERRIDES.get(cmd, {})

        if override.get("usage_hint") and not arg:
            return override["usage_hint"]

        try:
            kwargs = {arg_name: arg} if arg_name and arg else {}
            kwargs.update(override.get("extra_kwargs", {}))

            result = await self.call_app(app_id, method, **kwargs)
            if isinstance(result, list):
                items = result[:10]
                formatted = json.dumps(items, indent=2, ensure_ascii=False, default=str)
                return f"**{cmd}** ({len(result)} items):\n```json\n{formatted[:2000]}\n```"
            elif isinstance(result, dict):
                return f"**{cmd}**:\n```json\n{json.dumps(result, indent=2, ensure_ascii=False, default=str)[:2000]}\n```"
            else:
                return f"**{cmd}**: {str(result)[:2000]}"
        except Exception as e:
            return f"**{cmd}** failed: {e}"

    return None
