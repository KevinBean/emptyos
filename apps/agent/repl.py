"""Terminal REPL for the agent (eos chat).

Extracted from app.py to keep the core agent atomic. `cmd_chat` is a
module-level async function bound onto AgentApp via class-attribute
assignment in app.py, which preserves the @cli_command decorator so the
CLI loader picks it up the same as any other command.

Also owns SLASH_COMMANDS (the canonical list shared with the web UI via
api_slash_commands in routes.py) and the two chat-footer helpers.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from pathlib import Path

from emptyos.sdk import cli_command
from emptyos.sdk.agent_loop import (
    AgentSession, run_turn, run_native_turn,
    DEFAULT_SYSTEM_PROMPT, DEFAULT_MAX_ITERS, EDIT_PATH_LIMIT,
)


# ─────────────────────────────────────────────────────────────────────
# Slash commands — single source of truth for both terminal and web UI.
#
# Each command is client-side: the transport (CLI / web) executes it
# without shipping the line to the LLM. The `args` field advertises
# what the command takes so autocomplete UIs can hint.
# ─────────────────────────────────────────────────────────────────────
SLASH_COMMANDS = [
    {"name": "/help",     "args": "",          "help": "List available commands."},
    {"name": "/status",   "args": "",          "help": "Show session, provider, and tool info."},
    {"name": "/model",    "args": "[provider]", "help": "Show or set the provider for this session."},
    {"name": "/tools",    "args": "",          "help": "List the agent's tools and their permission level."},
    {"name": "/stats",    "args": "",          "help": "Show session totals — turns, tokens, cost."},
    {"name": "/skills",   "args": "",          "help": "List Claude-Code-compatible skills available to the agent."},
    {"name": "/tasks",    "args": "",          "help": "Show the current TaskList (if the agent has set one this session)."},
    {"name": "/clear",    "args": "",          "help": "Clear the transcript view (keeps session history)."},
    {"name": "/new",      "args": "",          "help": "Start a new session."},
    {"name": "/sessions", "args": "",          "help": "List recent sessions."},
    {"name": "/resume",   "args": "<id|name>", "help": "Switch to a previous session (id prefix or name)."},
    {"name": "/rename",   "args": "<name>",    "help": "Rename the current session."},
    {"name": "/delete",   "args": "<id>",      "help": "Delete a session (not the current one)."},
    {"name": "/revert",   "args": "[n]",       "help": "Undo the last Write/Edit this session. `/revert 3` undoes the last 3."},
    {"name": "/grant-edits", "args": "[n]",    "help": "Raise the per-turn edit-loop-guard cap (default 5) for this session. Use when a big refactor needs many edits to one file."},
    {"name": "/grant-iters", "args": "[n]",    "help": "Raise the per-turn max_iters (default 25) for this session. Use after 'Stopped at max_iters' when the task legitimately needs more steps."},
    {"name": "/plan",     "args": "",          "help": "Enter plan mode — read-only investigation only; Write/Edit/Bash-write/RestartDaemon blocked until /execute."},
    {"name": "/execute",  "args": "",          "help": "Leave plan mode. The plan you drafted is now greenlit — proceed and make the changes."},
    {"name": "/scrap",    "args": "",          "help": "Leave plan mode without executing. Discard the draft plan; back to normal chat."},
    {"name": "/context",  "args": "",          "help": "Show active session context — message count, provider, plan mode, limits."},
    {"name": "/settings", "args": "",          "help": "Open or print agent settings."},
    {"name": "/archive",  "args": "",          "help": "Summarise the session and save it to the vault as a memory note."},
    {"name": "/quit",     "args": "",          "help": "End the session (CLI only)."},
]


def _chat_usage_tokens(usage: dict) -> tuple[int, int]:
    """Normalize Anthropic (input/output) and OpenAI (prompt/completion) shapes."""
    if not usage:
        return (0, 0)
    pt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    ct = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    return (int(pt), int(ct))


def _chat_fmt_cost(c: float) -> str:
    if c <= 0:
        return "$0"
    if c < 0.0001:
        return "<$0.0001"
    return f"${c:.4f}"


@cli_command("chat", help="Start an interactive agent session (tool-use REPL)")
async def cmd_chat(self, session_id: str = ""):
    """Interactive terminal session."""
    from rich.console import Console
    console = Console()

    if not session_id:
        rec = self._create_session(name="CLI session")
        session_id = rec["id"]
    elif not self._get_session(session_id):
        console.print(f"[red]Session {session_id!r} not found[/red]")
        return

    provider_name = self._default_provider_name()
    provider = self._resolve_provider(provider_name)
    if provider is None:
        console.print(f"[red]No tool-capable provider available (tried {provider_name!r}).[/red]")
        console.print("  Install `anthropic>=0.40` and set ANTHROPIC_API_KEY, or wire OpenAI/Ollama.")
        return

    # Attach terminal permission UI to the consent manager
    tool_consent = self.service("tool_consent")
    from rich.prompt import Prompt
    # Drop Rich's default "colon-space" suffix; our prompt is already self-terminating.
    Prompt.prompt_suffix = " "

    # Discover skills once at session start — used by both the completer
    # (shows `/<skill-name>` alongside slash commands) and the slash handler
    # (invokes the skill when the user types `/<skill-name>`).
    try:
        from apps.agent.skills import discover_skills
        skill_catalog = discover_skills(self.repo_root)
    except Exception:
        skill_catalog = {}

    # Main input: prompt_toolkit session with slash-command tab completion
    # and persistent history. Falls back to Rich Prompt.ask if prompt_toolkit
    # can't run (e.g. non-TTY piped stdin — Rich handles that more gracefully).
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.shortcuts import CompleteStyle

        slash_cmds = list(SLASH_COMMANDS)  # snapshot for closure
        skill_items = sorted(skill_catalog.values(), key=lambda s: s.name)

        class _SlashCompleter(Completer):
            """Show slash commands + skills the moment the user types `/`.
            Only activates for text starting with `/` so ordinary prose doesn't
            trigger a menu."""
            def get_completions(self_inner, document, complete_event):
                text = document.text_before_cursor
                if not text.startswith("/"):
                    return
                # Only match on the first token — don't re-trigger after a space
                # (so `/eos-new-app some args` doesn't keep suggesting).
                first = text.split(None, 1)[0]
                if first != text:
                    return
                needle = text.lower()
                for c in slash_cmds:
                    name = c["name"]
                    if name.lower().startswith(needle):
                        yield Completion(
                            name,
                            start_position=-len(text),
                            display=name,
                            display_meta=c.get("help", ""),
                        )
                for s in skill_items:
                    candidate = f"/{s.name}"
                    if candidate.lower().startswith(needle):
                        desc = (s.description or "").strip()
                        yield Completion(
                            candidate,
                            start_position=-len(text),
                            display=candidate,
                            display_meta=(desc[:80] + "…") if len(desc) > 80 else desc,
                        )

        # Shift+Tab cycles through three modes:
        #   ask  → auto (approve-all) → plan (read-only) → ask …
        # Mirrors Claude Code's mode-cycle behaviour.
        _MODE_CYCLE = ["ask", "auto", "plan"]
        _mode_state = {"current": "ask"}

        _kb = KeyBindings()

        @_kb.add("s-tab")
        def _cycle_mode(event):
            tc = self.service("tool_consent")
            cur = _mode_state["current"]
            nxt = _MODE_CYCLE[(_MODE_CYCLE.index(cur) + 1) % len(_MODE_CYCLE)]
            _mode_state["current"] = nxt
            if nxt == "plan":
                self._plan_modes[session_id] = True
                if tc:
                    tc.set_policy("ask")
            elif nxt == "ask":
                self._plan_modes[session_id] = False
                if tc:
                    tc.set_policy("ask")
            else:  # auto
                self._plan_modes[session_id] = False
                if tc:
                    tc.set_policy("auto")
            label = {"ask": "ask  (default)", "auto": "auto  ✓ approve-all", "plan": "plan  ⚑ read-only"}[nxt]
            console.print(f"\n[bold cyan]mode →[/bold cyan] [dim]{label}[/dim]")
            event.app.current_buffer.reset()

        history_dir = Path.home() / ".config" / "emptyos"
        history_dir.mkdir(parents=True, exist_ok=True)
        pt_session = PromptSession(
            completer=_SlashCompleter(),
            history=FileHistory(str(history_dir / "chat_history")),
            complete_while_typing=True,
            complete_style=CompleteStyle.READLINE_LIKE,  # inline dropdown on `/`
            key_bindings=_kb,
        )
        pt_prompt = HTML("<b>▸</b> ")
    except Exception:
        pt_session = None
        pt_prompt = None

    async def terminal_ui(req):
        console.print(f"\n[yellow]⚠  Permission requested[/yellow]  {req.summary}")
        console.print(f"  tool: [bold]{req.tool}[/bold]")
        for k, v in (req.input or {}).items():
            console.print(f"  {k}: [dim]{v}[/dim]")
        choice = await asyncio.to_thread(
            Prompt.ask,
            "Approve?",
            choices=["y", "s", "n"],
            default="n",
        )
        if choice == "y":
            return (True, "once")
        if choice == "s":
            return (True, "session")
        return (False, "once")

    if tool_consent:
        tool_consent.set_ui(terminal_ui)

    is_native = self._is_native_provider(provider)
    mode_banner = "native agent" if is_native else "EmptyOS loop"
    native_tools = getattr(provider, "native_tool_summary", "") if is_native else ""
    tool_count = 0 if is_native else len(self._tools)
    model_str = getattr(provider, "model", "") or ""
    model_display = f"{provider.name}" + (f" {model_str}" if model_str else "")
    tools_label = native_tools if (is_native and native_tools) else f"{tool_count} tool{'s' if tool_count != 1 else ''}"
    console.print(
        f"\n[bold cyan]Coding Agent[/bold cyan]  "
        f"[dim]· {model_display} · {tools_label} · session {session_id}[/dim]"
    )
    if is_native:
        console.print(
            f"[dim yellow]native mode — tool consent handled by {provider.name}[/dim yellow]"
        )
    # Warn loudly when the global policy bypasses all permission prompts.
    # This can happen if the settings UI set tool_policy=auto. The user
    # should know — silent auto-approve is the opposite of "with you, not for you".
    effective_policy = tool_consent.policy if tool_consent else "ask"
    if effective_policy == "auto":
        console.print(
            "[bold yellow]⚠  tool_policy = auto — ALL tool calls are auto-approved without asking.[/bold yellow]\n"
            "[dim yellow]   Use Shift+Tab to switch to ask mode, or go to /settings → Agent → Tool Policy.[/dim yellow]"
        )
    console.print("[dim]/help for commands · Shift+Tab = cycle mode · Ctrl+C to stop a turn · /quit to exit[/dim]\n")

    # Per-turn + session accumulators rendered in the agent:done footer.
    turn_state = {"start": 0.0, "tools": 0, "first_text": True}
    session_state = {"in": 0, "out": 0, "cost": 0.0, "turns": 0}
    # Most recent TaskList — updated on each TaskList tool call, readable via /tasks.
    task_state = {"tasks": []}
    # Edit history now lives on self._edit_stacks (see setup) and is
    # populated by run_turn directly — /revert calls self._revert_last_edits.

    class ConsoleBridge:
        async def emit(self_inner, etype, data, source="agent"):
            if etype == "agent:text":
                delta = data.get("delta", "")
                if turn_state["first_text"] and delta:
                    console.print("[bold cyan]●[/bold cyan] ", end="")
                    turn_state["first_text"] = False
                console.print(delta, end="")
            elif etype == "agent:tool_call":
                turn_state["tools"] += 1
                tname = data.get("name")
                # TaskList is a first-class plan/progress view — render as a
                # checkbox panel instead of raw JSON input.
                if tname == "TaskList":
                    tasks = (data.get("input") or {}).get("tasks") or []
                    task_state["tasks"] = list(tasks)
                    console.print("\n[cyan]▶ TaskList[/cyan]")
                    for t in tasks:
                        status = (t.get("status") or "pending").lower()
                        if status == "completed":
                            mark, color = "[x]", "green"
                        elif status == "in_progress":
                            mark, color = "[~]", "yellow"
                        else:
                            mark, color = "[ ]", "dim"
                        tid = t.get("id") or "?"
                        content = t.get("content") or ""
                        console.print(f"  [{color}]{mark}[/{color}] [dim]{tid}.[/dim] {content}")
                else:
                    console.print(
                        f"\n[cyan]▶ {tname}[/cyan] "
                        f"[dim]{json.dumps(data.get('input') or {})}[/dim]"
                    )
            elif etype == "agent:tool_result":
                display = data.get("display") or {}
                mark = "[red]✗[/red]" if data.get("is_error") else "[green]✓[/green]"
                name = display.get("name", "?")
                # Capture every successful Write/Edit onto the undo stack so
                # Edit-history capture now happens in loop.run_turn and is
                # stored on self._edit_stacks[session_id] — the ConsoleBridge
                # no longer needs to track it separately.
                # TaskList: the tool_call panel already rendered the list.
                # Skip the success line to avoid a duplicate.
                if name == "TaskList" and not data.get("is_error"):
                    counts = display.get("counts") or {}
                    if counts:
                        console.print(
                            f"  [dim]→ {counts.get('completed',0)}/{counts.get('total',0)} done · "
                            f"{counts.get('in_progress',0)} in progress[/dim]"
                        )
                    return
                if data.get("is_error"):
                    # Show the actual error message so the user can diagnose,
                    # not just `{'name': 'Bash'}`. Tool content carries the detail.
                    err = (data.get("error_snippet") or data.get("content") or "")
                    err = err.splitlines()[0][:200] if err else ""
                    extra = f"  [dim red]{err}[/dim red]" if err else ""
                    console.print(f"  {mark} {name}{extra}")
                else:
                    # Success: keep the concise display dict so tool output stays glanceable.
                    console.print(f"  {mark} {name}  [dim]{display}[/dim]")
            elif etype == "agent:done":
                usage = data.get("usage") or {}
                pt, ct = _chat_usage_tokens(usage)
                # Server-side providers populate `cost` with cache-aware
                # math (openai_compat._calc_cost_with_cache +
                # anthropic_sdk._calc_cost). Missing → show $0, never fabricate.
                provider_cost = usage.get("cost")
                turn_cost = float(provider_cost) if isinstance(provider_cost, (int, float)) and provider_cost > 0 else 0.0
                # Detect cache hits across both provider shapes
                cached = (
                    int(usage.get("cached_tokens") or 0)
                    or int(usage.get("cache_read_input_tokens") or 0)
                )
                elapsed = time.perf_counter() - turn_state["start"] if turn_state["start"] else 0.0
                session_state["in"] += pt
                session_state["out"] += ct
                session_state["cost"] += turn_cost
                session_state["turns"] += 1

                # Single-line footer — dim, bullet-separated, indented like a gutter.
                parts = [f"{elapsed:.1f}s"]
                total = pt + ct
                if total:
                    parts.append(f"{total:,} tokens")
                if cached and pt:
                    pct = int(100 * cached / pt)
                    parts.append(f"{pct}% cache")
                if turn_state["tools"]:
                    parts.append(f"{turn_state['tools']} tool{'s' if turn_state['tools'] != 1 else ''}")
                if turn_cost > 0:
                    parts.append(_chat_fmt_cost(turn_cost))
                if self._plan_modes.get(session_id, False):
                    parts.append("[yellow]plan mode[/yellow]")
                console.print(f"\n[dim]  · {' · '.join(parts)}[/dim]")
            elif etype == "agent:compacted":
                saved = data.get("chars_saved") or 0
                count = data.get("message_count") or 0
                console.print(
                    f"\n[dim]  · compacted history — saved ~{saved:,} chars "
                    f"({count} messages)[/dim]"
                )
            elif etype == "agent:max_iters":
                iters = data.get("iters") or DEFAULT_MAX_ITERS
                console.print(
                    f"\n[yellow]Stopped at max_iters ({iters}).[/yellow] "
                    f"[dim]Use /grant-iters N to raise the cap for this session, "
                    f"then send a follow-up like 'continue where you left off'.[/dim]"
                )
            elif etype == "agent:cancelled":
                console.print("\n[yellow]Cancelled.[/yellow]")
            elif etype == "agent:error":
                # Never render empty — empty str() on asyncio.TimeoutError &co.
                err = data.get("error") or data.get("type") or "unknown error"
                console.print(f"\n[red]Error: {err}[/red]")

    bridge = ConsoleBridge()

    # Ctrl+C during a turn cancels the turn; Ctrl+C at the prompt exits.
    # We install a SIGINT handler that checks whether a turn is in flight:
    # if yes, cancel the task (REPL survives); if no, raise KeyboardInterrupt
    # so prompt_toolkit / the outer loop unwind normally. Restored on exit.
    _turn_ref: dict = {"task": None}
    _running_loop = asyncio.get_event_loop()

    def _handle_sigint(signum, frame):
        t = _turn_ref["task"]
        if t is not None and not t.done():
            _running_loop.call_soon_threadsafe(t.cancel)
        else:
            raise KeyboardInterrupt()

    try:
        _old_sigint = signal.signal(signal.SIGINT, _handle_sigint)
    except (ValueError, OSError):
        # Not on main thread — leave default behavior.
        _old_sigint = None

    try:
        while True:
            # Re-derive prompt each turn so plan-mode toggling is visible.
            in_plan = self._plan_modes.get(session_id, False)
            if pt_session is not None:
                from prompt_toolkit.formatted_text import HTML as _HTML
                _cur_mode = _mode_state.get("current", "ask")
                if in_plan or _cur_mode == "plan":
                    _p = _HTML("<b>⚑</b> <b>▸</b> ")
                elif _cur_mode == "auto":
                    _p = _HTML("<ansiyellow>✓</ansiyellow> <b>▸</b> ")
                else:
                    _p = _HTML("<b>▸</b> ")
                try:
                    user_text = await pt_session.prompt_async(_p)
                except (EOFError, KeyboardInterrupt):
                    raise
            else:
                _p = "[bold yellow]⚑[/bold yellow] [bold]▸[/bold]" if in_plan else "[bold]▸[/bold]"
                user_text = await asyncio.to_thread(
                    Prompt.ask, _p, default="", show_default=False
                )
            user_text = user_text.strip()
            if not user_text:
                continue

            # ─── Client-side slash commands ─────────────────
            if user_text.startswith("/"):
                parts = user_text.split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd in ("/quit", "/exit"):
                    break
                if cmd == "/help":
                    for sc in SLASH_COMMANDS:
                        sig = f"{sc['name']} {sc['args']}".strip()
                        console.print(f"  [cyan]{sig:<22}[/cyan] [dim]{sc['help']}[/dim]")
                    continue
                if cmd == "/status":
                    _model = getattr(provider, "model", "") or ""
                    _mstr = f"{provider.name}" + (f" · {_model}" if _model else "")
                    console.print(
                        f"[dim]session={session_id}  model={_mstr}  "
                        f"mode={mode_banner}  tools={tool_count}[/dim]"
                    )
                    continue
                if cmd == "/tools":
                    if is_native:
                        console.print(f"[dim]Native agent — tools handled by {provider.name}.[/dim]")
                        if native_tools:
                            console.print(f"[dim]{native_tools}[/dim]")
                    else:
                        for t in self._tools.values():
                            console.print(
                                f"  [cyan]{t.name:<10}[/cyan] "
                                f"[dim]perm={t.permission}[/dim]  {t.description}"
                            )
                    continue
                if cmd == "/clear":
                    console.clear()
                    continue
                if cmd == "/new":
                    rec = self._create_session(name="CLI session")
                    session_id = rec["id"]
                    session_state["in"] = session_state["out"] = 0
                    session_state["cost"] = 0.0
                    session_state["turns"] = 0
                    self._plan_modes.pop(session_id, None)  # fresh session — normal mode
                    self._edit_limits.pop(session_id, None)
                    self._iter_limits.pop(session_id, None)
                    console.print(f"[green]New session {session_id}[/green]")
                    continue
                if cmd == "/sessions":
                    sessions = self._sessions.list_sessions()
                    if not sessions:
                        console.print("[dim]  no sessions yet.[/dim]")
                        continue
                    for s in sessions[:20]:
                        marker = "[cyan]●[/cyan]" if s["id"] == session_id else " "
                        last = (s.get("last_message") or s.get("created") or "")[:16].replace("T", " ")
                        # message_count includes both user+assistant+tool rows; halve for turn-ish estimate
                        mc = s.get("message_count") or 0
                        console.print(
                            f"  {marker} [cyan]{s['id']}[/cyan]  "
                            f"[dim]{s.get('name','')[:28]:<28}  {mc:>4} msgs  {last}[/dim]"
                        )
                    console.print(f"[dim]  {len(sessions)} total · ● = current[/dim]")
                    continue
                if cmd == "/resume":
                    if not arg:
                        console.print("[yellow]  usage: /resume <id-prefix-or-name>[/yellow]")
                        continue
                    needle = arg.strip().lower()
                    sessions = self._sessions.list_sessions()
                    # Match on id prefix first (unambiguous), then fall back to name contains
                    exact = [s for s in sessions if s["id"].lower().startswith(needle)]
                    named = [s for s in sessions if needle in (s.get("name") or "").lower()]
                    candidates = exact or named
                    if not candidates:
                        console.print(f"[red]  no session matches {arg!r}. Try /sessions.[/red]")
                        continue
                    if len(candidates) > 1 and not exact:
                        console.print(f"[yellow]  ambiguous — {len(candidates)} matches. Use a more specific id/name.[/yellow]")
                        for s in candidates[:5]:
                            console.print(f"    [cyan]{s['id']}[/cyan]  [dim]{s.get('name','')}[/dim]")
                        continue
                    target = candidates[0]
                    session_id = target["id"]
                    session_state["in"] = session_state["out"] = 0
                    session_state["cost"] = 0.0
                    session_state["turns"] = 0
                    # Restore the provider the session was last using, so resuming
                    # a gpt-4.1 session after you /model-switched to ollama doesn't
                    # silently change the model mid-conversation.
                    stored_provider = (target.get("provider") or "").strip()
                    switched_msg = ""
                    if stored_provider and stored_provider != provider.name:
                        resumed_provider = self._resolve_provider(stored_provider)
                        if resumed_provider is not None:
                            provider = resumed_provider
                            is_native = self._is_native_provider(provider)
                            mode_banner = "native agent" if is_native else "EmptyOS loop"
                            tool_count = 0 if is_native else len(self._tools)
                            native_tools = getattr(provider, "native_tool_summary", "") if is_native else ""
                            _model = getattr(provider, "model", "") or ""
                            switched_msg = f" · model: {provider.name}" + (f" {_model}" if _model else "")
                    console.print(
                        f"[green]  resumed[/green] [cyan]{session_id}[/cyan]  "
                        f"[dim]{target.get('name','')} · {target.get('message_count', 0)} msgs{switched_msg}[/dim]"
                    )
                    continue
                if cmd == "/rename":
                    if not arg:
                        console.print("[yellow]  usage: /rename <new name>[/yellow]")
                        continue
                    self._sessions.update_session(session_id, name=arg.strip())
                    console.print(f"[green]  renamed[/green] [cyan]{session_id}[/cyan] → [dim]{arg.strip()}[/dim]")
                    continue
                if cmd == "/delete":
                    if not arg:
                        console.print("[yellow]  usage: /delete <id-prefix>[/yellow]")
                        continue
                    needle = arg.strip().lower()
                    sessions = self._sessions.list_sessions()
                    exact = [s for s in sessions if s["id"].lower().startswith(needle)]
                    if not exact:
                        console.print(f"[red]  no session matches {arg!r}.[/red]")
                        continue
                    if len(exact) > 1:
                        console.print("[yellow]  ambiguous — use a longer prefix:[/yellow]")
                        for s in exact[:5]:
                            console.print(f"    [cyan]{s['id']}[/cyan]  [dim]{s.get('name','')}[/dim]")
                        continue
                    target = exact[0]
                    if target["id"] == session_id:
                        console.print("[red]  cannot delete the current session. /resume elsewhere first.[/red]")
                        continue
                    self._sessions.delete_session(target["id"])
                    console.print(f"[green]  deleted[/green] [cyan]{target['id']}[/cyan] [dim]{target.get('name','')}[/dim]")
                    continue
                if cmd == "/revert":
                    try:
                        n = int(arg) if arg else 1
                    except ValueError:
                        console.print(f"[yellow]  usage: /revert [n]  — got {arg!r}[/yellow]")
                        continue
                    result = self._revert_last_edits(session_id, n)
                    if result.get("empty"):
                        console.print("[dim]  no Write/Edit to revert this session.[/dim]")
                        continue
                    reverted_ok = 0
                    for r in result.get("reverted", []):
                        if r.get("ok"):
                            mode = r.get("mode", "restored")
                            verb = "deleted (was created)" if mode == "deleted" else f"restored [dim]({r.get('action','edit')})[/dim]"
                            console.print(f"  [green]↶[/green] {verb} [cyan]{r['path']}[/cyan]")
                            reverted_ok += 1
                        else:
                            console.print(f"  [red]✗[/red] could not revert {r.get('path')}: {r.get('error','?')}")
                    if reverted_ok > 0:
                        hint = " · daemon still holds old bytecode if you edited .py — restart to pick up" if result.get("python_edits") else ""
                        console.print(
                            f"[dim]  reverted {reverted_ok} · {result.get('remaining',0)} remain on the undo stack{hint}.[/dim]"
                        )
                    continue
                if cmd == "/grant-edits":
                    if not arg:
                        new_cap = max(EDIT_PATH_LIMIT * 4, 20)
                    else:
                        try:
                            new_cap = int(arg)
                        except ValueError:
                            console.print(f"[yellow]  usage: /grant-edits [n]  — got {arg!r}[/yellow]")
                            continue
                        if new_cap < 1:
                            console.print("[yellow]  /grant-edits N must be >= 1[/yellow]")
                            continue
                    self._edit_limits[session_id] = new_cap
                    console.print(
                        f"[green]  edit-loop-guard cap raised to {new_cap}[/green] "
                        f"[dim](default {EDIT_PATH_LIMIT}) for this session. "
                        f"Resets on /new.[/dim]"
                    )
                    continue
                if cmd == "/grant-iters":
                    if not arg:
                        new_cap = max(DEFAULT_MAX_ITERS * 2, 50)
                    else:
                        try:
                            new_cap = int(arg)
                        except ValueError:
                            console.print(f"[yellow]  usage: /grant-iters [n]  — got {arg!r}[/yellow]")
                            continue
                        if new_cap < 1:
                            console.print("[yellow]  /grant-iters N must be >= 1[/yellow]")
                            continue
                    self._iter_limits[session_id] = new_cap
                    console.print(
                        f"[green]  max_iters raised to {new_cap}[/green] "
                        f"[dim](default {DEFAULT_MAX_ITERS}) for this session. "
                        f"Resets on /new. Next turn will run up to {new_cap} tool-use rounds.[/dim]"
                    )
                    continue
                if cmd == "/plan":
                    if self._plan_modes.get(session_id, False):
                        console.print("[dim]  already in plan mode — use /execute to proceed or /scrap to discard.[/dim]")
                    else:
                        self._plan_modes[session_id] = True
                        console.print(
                            "[bold yellow]⚑ plan mode ON[/bold yellow]  "
                            "[dim]Write/Edit/Bash-write/RestartDaemon/CallApp are blocked. "
                            "Investigate + propose. `/execute` to greenlight · `/scrap` to discard.[/dim]"
                        )
                    continue
                if cmd == "/execute":
                    if not self._plan_modes.get(session_id, False):
                        console.print("[dim]  not in plan mode — nothing to execute. Use /plan to enter it first.[/dim]")
                    else:
                        self._plan_modes[session_id] = False
                        console.print(
                            "[bold green]✓ plan mode OFF — executing[/bold green]  "
                            "[dim]Tools unblocked. Your next message nudges the agent to proceed with the plan it drafted.[/dim]"
                        )
                    continue
                if cmd == "/scrap":
                    if not self._plan_modes.get(session_id, False):
                        console.print("[dim]  not in plan mode — nothing to scrap.[/dim]")
                    else:
                        self._plan_modes[session_id] = False
                        console.print(
                            "[bold red]✗ plan scrapped — plan mode OFF[/bold red]  "
                            "[dim]Forget the draft; back to normal chat.[/dim]"
                        )
                    continue
                if cmd == "/model":
                    if not arg:
                        _model = getattr(provider, "model", "") or ""
                        _mstr = f"{provider.name}" + (f" · {_model}" if _model else "")
                        console.print(f"[dim]current: {_mstr}[/dim]")
                    else:
                        new_provider = self._resolve_provider(arg, strict=True)
                        if new_provider is None:
                            console.print(
                                f"[red]Provider {arg!r} not available.[/red] "
                                f"[dim](sections must be in \\[capabilities.think] providers list to load)[/dim]"
                            )
                        else:
                            provider = new_provider
                            is_native = self._is_native_provider(provider)
                            mode_banner = "native agent" if is_native else "EmptyOS loop"
                            tool_count = 0 if is_native else len(self._tools)
                            native_tools = getattr(provider, "native_tool_summary", "") if is_native else ""
                            # Per-session provider (so /resume restores it) + persistent
                            # default (so future `eos chat` sessions pick the same one).
                            self._sessions.update_session(session_id, provider=provider.name)
                            settings_svc = self.service("settings")
                            persisted = False
                            if settings_svc:
                                try:
                                    settings_svc.set("agent.default_provider", provider.name)
                                    persisted = True
                                except Exception:
                                    pass
                            _model = getattr(provider, "model", "") or ""
                            _mstr = f"{provider.name}" + (f" · {_model}" if _model else "")
                            suffix = " · saved as default" if persisted else ""
                            console.print(
                                f"[green]Switched to {_mstr}[/green] "
                                f"[dim]({mode_banner}, tools={tool_count}{suffix})[/dim]"
                            )
                    continue
                if cmd == "/skills":
                    try:
                        from apps.agent.skills import discover_skills
                        catalog = discover_skills(self.repo_root)
                    except Exception as e:
                        console.print(f"[red]error discovering skills: {e}[/red]")
                        continue
                    if not catalog:
                        console.print("[dim]  no skills installed.[/dim]")
                        continue
                    for s in sorted(catalog.values(), key=lambda x: x.name):
                        console.print(
                            f"  [cyan]{s.name:<34}[/cyan] [dim]\\[{s.source}][/dim]  {s.description}"
                        )
                    console.print(f"[dim]  {len(catalog)} skill(s) — ask me to use one by name[/dim]")
                    continue
                if cmd == "/tasks":
                    tasks = task_state["tasks"]
                    if not tasks:
                        console.print("[dim]  no task list set this session. Ask the agent to plan something.[/dim]")
                        continue
                    for t in tasks:
                        status = (t.get("status") or "pending").lower()
                        if status == "completed":
                            mark, color = "[x]", "green"
                        elif status == "in_progress":
                            mark, color = "[~]", "yellow"
                        else:
                            mark, color = "[ ]", "dim"
                        tid = t.get("id") or "?"
                        console.print(f"  [{color}]{mark}[/{color}] [dim]{tid}.[/dim] {t.get('content','')}")
                    done = sum(1 for t in tasks if (t.get("status") or "").lower() == "completed")
                    console.print(f"[dim]  {done}/{len(tasks)} done[/dim]")
                    continue
                if cmd == "/stats":
                    _t = session_state["turns"]
                    if not _t:
                        console.print("[dim]  no turns yet this session.[/dim]")
                    else:
                        console.print(
                            f"[dim]  {_t} turn{'s' if _t != 1 else ''} · "
                            f"{session_state['in']:,} in · {session_state['out']:,} out · "
                            f"{_chat_fmt_cost(session_state['cost'])}[/dim]"
                        )
                    continue
                if cmd == "/context":
                    # Mirrors the WS /context handler in app.py — reads the
                    # persisted session rather than relying on an in-scope
                    # AgentSession, which doesn't exist until the first turn.
                    sess_record = self._get_session(session_id) or {}
                    msgs = sess_record.get("messages", []) or []
                    char_count = sum(
                        len(str(m.get("content", ""))) for m in msgs
                    )
                    plan_active = self._plan_modes.get(session_id, False)
                    console.print(
                        f"[dim]  session     : {session_id}[/dim]"
                    )
                    console.print(
                        f"[dim]  provider    : {provider.name}"
                        + (f" · {getattr(provider, 'model', '')}" if getattr(provider, "model", "") else "")
                        + "[/dim]"
                    )
                    console.print(f"[dim]  messages    : {len(msgs)} ({char_count:,} chars)[/dim]")
                    console.print(f"[dim]  plan mode   : {'ON' if plan_active else 'off'}[/dim]")
                    console.print(f"[dim]  edit limit  : {self._edit_limits.get(session_id, EDIT_PATH_LIMIT)}[/dim]")
                    console.print(f"[dim]  iter limit  : {self._iter_limits.get(session_id, DEFAULT_MAX_ITERS)}[/dim]")
                    console.print(f"[dim]  tool hooks  : {len(self._before_tool_hooks)} before, {len(self._after_tool_hooks)} after[/dim]")
                    continue
                if cmd == "/archive":
                    console.print("[dim]  Summarising session…[/dim]")
                    result = await self._archive_session(session_id)
                    if result.get("ok"):
                        console.print(
                            f"[green]  archived:[/green] [dim]{result.get('note_path', '')}[/dim]"
                        )
                        if result.get("url"):
                            console.print(f"[dim]  {result['url']}[/dim]")
                    else:
                        console.print(f"[red]  archive failed:[/red] {result.get('error', 'unknown error')}")
                    continue
                if cmd == "/settings":
                    if settings := self.service("settings"):
                        console.print(f"  default_provider = [cyan]{settings.get('agent.default_provider') or '(default)'}[/cyan]")
                        console.print(f"  max_iters        = [cyan]{settings.get('agent.max_iters') or DEFAULT_MAX_ITERS}[/cyan]")
                        console.print(f"  tool_policy      = [cyan]{settings.get('agent.tool_policy') or 'ask'}[/cyan]")
                        console.print("  [dim](change via `eos settings` or the web settings panel)[/dim]")
                    else:
                        console.print("[red]Settings service unavailable.[/red]")
                    continue
                # Skill invocation: /<skill-name> [extra user intent].
                # Load the SKILL.md and run it as this turn's user message so
                # the model reads the playbook and acts on it immediately.
                # Shared with the web path via `_expand_skill_slash`.
                expanded = self._expand_skill_slash(user_text)
                if expanded:
                    skill_key = cmd[1:]
                    skill = skill_catalog.get(skill_key)
                    console.print(
                        f"[dim]  loaded skill[/dim] [cyan]{skill.name if skill else skill_key}[/cyan] "
                        f"[dim]({skill.source if skill else '?'})[/dim]"
                    )
                    user_text = expanded
                    # fall through — user_text is now the skill-prefixed message
                else:
                    console.print(
                        f"[yellow]Unknown command {cmd!r}. Try /help, /skills, "
                        f"or type the first letter of a command/skill after `/`.[/yellow]"
                    )
                    continue

            provider_kind = "native" if is_native else provider.kind
            sess = AgentSession(
                id=session_id,
                messages=self._load_provider_messages(session_id),
                provider_kind=provider_kind,
            )
            self._live_sessions[session_id] = sess

            # Reset per-turn counters for the footer.
            turn_state["start"] = time.perf_counter()
            turn_state["tools"] = 0
            turn_state["first_text"] = True

            pre_len = len(sess.messages)
            cli_system = DEFAULT_SYSTEM_PROMPT + "\n\n" + self._runtime_info_block(provider, is_native)
            scaffold = self._app_scaffold_block(user_text, is_native)
            if scaffold:
                cli_system = cli_system + "\n\n" + scaffold
            if self._plan_modes.get(session_id, False):
                cli_system += (
                    "\n\n⚑ PLAN MODE ACTIVE — you are in read-only investigation phase. "
                    "Write, Edit, Bash (non-readonly), RestartDaemon, CallApp, and "
                    "Fetch non-GET are BLOCKED and will return a gate error. Use Read, "
                    "Grep, Glob, Skill, TaskList, Screenshot, and Fetch-GET to gather "
                    "context. Draft a clear plan — either as inline text or via TaskList "
                    "— then STOP and wait for the user to /execute (leave plan mode + "
                    "proceed) or /scrap (discard). Do not try to work around the gate."
                )
            # Orient-before-act (CLI path)
            cli_orient_text = user_text
            console.print("[dim]  orienting…[/dim]", end="\r")
            try:
                orient_plan = await asyncio.wait_for(
                    self._orient(user_text, session_id), timeout=12.0
                )
            except asyncio.TimeoutError:
                orient_plan = None
            if orient_plan:
                orient_block = self._orient_block(orient_plan)
                if is_native:
                    cli_system = cli_system + "\n\n" + orient_block
                else:
                    cli_orient_text = orient_block + "\n\n" + user_text
                tt = orient_plan.get("task_type") or ""
                subj = orient_plan.get("subject") or ""
                steps = len(orient_plan.get("investigation_plan") or [])
                label = f"{tt}: {subj}" if tt and subj else (tt or "orient")
                console.print(f"[dim]  {label} — {steps} steps planned[/dim]")
            else:
                console.print("                    ", end="\r")  # clear the orienting… line

            try:
                if is_native:
                    turn_coro = run_native_turn(
                        session=sess,
                        user_text=user_text,
                        provider=provider,
                        events=bridge,
                        system=cli_system,
                    )
                else:
                    turn_coro = run_turn(
                        session=sess,
                        user_text=cli_orient_text,
                        provider=provider,
                        tools=self._tools,
                        tool_consent=tool_consent,
                        events=bridge,
                        app_ref=self,
                        system=cli_system,
                        max_iters=self._iter_limit_for(session_id),
                        orient_plan=orient_plan,
                        edit_path_limit=self._edit_limit_for(session_id),
                    )
                _turn_ref["task"] = asyncio.create_task(turn_coro)
                try:
                    await _turn_ref["task"]
                finally:
                    _turn_ref["task"] = None
            except asyncio.CancelledError:
                console.print("\n[yellow]Cancelled. Press Ctrl+C again or type /quit to exit.[/yellow]")
            except Exception as e:
                # Some exceptions (asyncio.TimeoutError, ConnectionResetError)
                # have empty str() — fall back to the class name so the user
                # sees something actionable instead of a bare "Error:".
                msg = str(e) or type(e).__name__
                console.print(f"\n[red]Error: {msg}[/red]")

            for m in sess.messages[pre_len:]:
                self._persist_message(session_id, m, provider_kind)

            console.print()  # blank line between turns
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if _old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, _old_sigint)
            except (ValueError, OSError):
                pass
        if tool_consent:
            tool_consent.set_ui(None)
            tool_consent.set_policy("ask")  # always reset on exit — Shift+Tab auto-mode must not leak
        self._live_sessions.pop(session_id, None)
        _t = session_state["turns"]
        if _t:
            console.print(
                f"\n[dim]  {_t} turn{'s' if _t != 1 else ''} · "
                f"{session_state['in'] + session_state['out']:,} tokens · "
                f"{_chat_fmt_cost(session_state['cost'])}  ·  session {session_id}[/dim]"
            )
        else:
            console.print(f"\n[dim]  session {session_id} saved.[/dim]")
