"""Code-preset bootstrap for ``eos rooms --code`` (formerly ``eos code``).

Distinct from `eos rooms` in defaults only: opens directly into the `cli-code`
agent (vs `cli-chat`), which carries a code-focused system prompt + the full
`repo.*` allowlist (read/grep/edit/write/exec). Force-gate in
`apps/rooms/pending.py` routes edit/write/exec through the diff-and-command
review cards regardless of allowlist.

Distinct from `eos chat` (apps/agent's autonomous tool-loop REPL): single-turn
chat with review-gate cards, designed to work well on cheaper models like
gpt-5.4-mini. apps/agent is the loop; this is the chat-shape coding terminal.

Reuses everything from `emptyos.cli.chat`:
  - REPL machinery (slash commands, room picker, history, prompt rendering)
  - Pending-card rendering (diff for edit/write, command card for exec)
  - Apply/Reject/Skip flow

Only differences:
  - Ensures the `cli-code` agent exists before entering the REPL
  - Passes "cli-code" as the explicit agent (overrides last-used state from
    the shared chat-state.json — the code preset always opens the code agent)
"""

from __future__ import annotations

import aiohttp

from emptyos.cli.chat import (
    _probe_daemon,
    console,
    repl,
)

CODE_AGENT_ID = "cli-code"
CODE_AGENT_NAME = "CLI Code"

# Code-focused system prompt. Search-first, narrate findings, never invent
# file contents. The model still emits [DO:repo.*] tokens; the rooms gate
# enforces review for edit/write/exec.
CODE_SYSTEM_PROMPT = (
    "You are an EmptyOS coding assistant in a terminal REPL. You work in a "
    "chat-shape loop — one turn per user message, review-gated for any "
    "destructive verb. Use `repo.read`, `repo.grep`, and `repo.tree` freely "
    "(read-only, auto-execute). Use `repo.edit` for targeted in-file edits, `repo.write` "
    "for new files or full rewrites, `repo.exec` for shell commands — all "
    "three are reviewed as Apply/Reject cards before any change runs.\n\n"
    "Discipline:\n"
    "1. Search before edit. Use `repo.tree` to inspect structure when needed, "
    "use `repo.grep` to find the target, and use `repo.read` to see surrounding "
    "context. Never propose an edit on a file you haven't "
    "read this turn.\n"
    "2. Narrate findings tersely. The user reads the diff — don't restate it. "
    "Say what changed and why, not what each line does.\n"
    "3. Run tests after writes when relevant. If you edited `apps/foo/`, "
    "propose `repo.exec({\"cmd\":\"python -m pytest tests/test_sys_foo.py -v\"})` "
    "so the user can verify in the same turn.\n"
    "4. Quote unique context for `repo.edit`. The `old` arg must match "
    "exactly once in the file; if you get a multi-match error, expand the "
    "surrounding context until unique.\n"
    "5. Path is repo-relative with forward slashes (Windows-safe). The repo "
    "root is the directory containing emptyos.toml.\n"
    "6. Never invent file contents. If you don't have evidence, say so and "
    "ask for a `repo.read` first.\n\n"
    "Emit [DO:repo.method({...})] tokens inline; the user reviews each as "
    "a card and applies or rejects. Never describe an action and skip the "
    "token — the user only sees what you emit."
)

# The full repo allowlist. Force-gate in apps/rooms/pending.py
# (ALWAYS_GATE_VERBS) routes edit/write/exec through review regardless.
CODE_SERVER_ACTIONS = {
    "repo": ["read", "grep", "tree", "edit", "write", "exec"],
}


def _merged_server_actions(existing: dict | None, defaults: dict) -> tuple[dict, bool]:
    """Merge missing default verbs into an existing rooms agent allowlist."""
    merged: dict = {}
    changed = False
    for app_id, methods in (existing or {}).items():
        if isinstance(methods, list):
            merged[app_id] = list(methods)
        else:
            merged[app_id] = methods
    for app_id, methods in defaults.items():
        current = merged.get(app_id)
        if not isinstance(current, list):
            current = []
            merged[app_id] = current
            changed = True
        for method in methods:
            if method not in current:
                current.append(method)
                changed = True
    return merged, changed


async def _ensure_code_agent(
    session: aiohttp.ClientSession, base_url: str,
) -> dict:
    """Get-or-create the cli-code agent. Mirrors `_ensure_default_agent` in
    chat.py but seeds with code-focused defaults."""
    async with session.get(
        f"{base_url}/rooms/api/agents/{CODE_AGENT_ID}"
    ) as r:
        if r.status == 200:
            data = await r.json()
            if isinstance(data, dict) and not data.get("error") and data.get("id"):
                merged, changed = _merged_server_actions(
                    data.get("server_actions"), CODE_SERVER_ACTIONS
                )
                if changed:
                    async with session.put(
                        f"{base_url}/rooms/api/agents/{CODE_AGENT_ID}",
                        json={"server_actions": merged},
                    ) as update:
                        updated = await update.json()
                        if isinstance(updated, dict) and not updated.get("error"):
                            return updated
                return data
        elif r.status == 401:
            raise RuntimeError(
                "Daemon rejected the auth token. Check `auth_token` in "
                "emptyos.toml matches the running daemon's config."
            )
    payload = {
        "id": CODE_AGENT_ID,
        "name": CODE_AGENT_NAME,
        "system_prompt": CODE_SYSTEM_PROMPT,
        "server_actions": CODE_SERVER_ACTIONS,
        "gate_mode": "auto",
        # Pinned low for code work (parsing/analysis territory per
        # CLAUDE.md Rule 12). Default provider temperature is ~0.7,
        # which makes the model hedge + reword unnecessarily on code
        # questions where the model should commit to a concrete next step.
        "temperature": 0.3,
    }
    async with session.post(f"{base_url}/rooms/api/agents", json=payload) as r:
        result = await r.json()
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(f"Could not create cli-code agent: {result['error']}")
        return result


async def _bootstrap_then_repl(
    base_url: str, agent_override: str | None, once: str | None,
    auth_token: str,
) -> int:
    """Probe daemon → ensure cli-code exists → enter the shared REPL pinned
    to cli-code (or `--agent <id>` override)."""
    if not _probe_daemon(base_url):
        console.print(f"[red]Daemon not reachable at {base_url}.[/red]")
        console.print(
            "[dim]Start it with restart.bat (Windows) or "
            "`python -m emptyos start`.[/dim]"
        )
        return 1

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    # Pre-create the agent OUTSIDE the REPL so repl() finds it on its own
    # GET /api/agents/{id} probe. Passing CODE_AGENT_ID explicitly overrides
    # the shared last-room state from chat-state.json.
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        try:
            await _ensure_code_agent(session, base_url)
        except (RuntimeError, aiohttp.ClientError) as e:
            console.print(f"[red]{e}[/red]")
            return 1

    # repl() opens its own session; we pass the agent id to force cli-code
    # regardless of what `last_room_id` in chat-state.json says.
    target = agent_override or CODE_AGENT_ID
    return await repl(base_url, target, once, auth_token=auth_token)
