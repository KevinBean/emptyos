"""Agent loop — tool-use turn driver.

Takes a user message, runs one "turn": call provider with tools → if the
model requests tool_use, dispatch tools, append tool_results, call again →
repeat until the model stops requesting tools, returns its final text, or
hits max_iters.

Provider-agnostic — the `ToolCapableProvider` ABC hides wire format
differences. Messages are stored in the provider's native shape; cross-
provider session resumption isn't supported in v1.

Events (emitted on the kernel EventBus + any per-session channel).
Every event additionally carries `trace_id` — one id per turn (nested
SubAgent turns inherit the parent's), stamped by `_emit` from the
`emptyos.sdk.trace` contextvar so think:executed + the tool-audit JSONL
correlate with the same id:
    agent:turn_start        {session_id, user_text}
    agent:iter_start        {session_id, iter}
    agent:text              {session_id, delta}
    agent:tool_call         {session_id, id, name, input}
    agent:tool_result       {session_id, id, display, is_error}
                            (display carries duration_ms for dispatched tools)
    agent:permission_requested (emitted by ToolConsentManager, not here)
    agent:done              {session_id, usage}
    agent:cancelled         {session_id}
    agent:max_iters         {session_id}
    agent:error             {session_id, error}

Tool hooks (on app_ref, optional):
    _before_tool_hooks  list of callables invoked after consent, before tool.run()
                        signature: hook(session_id, tool_name, input)
    _after_tool_hooks   list of callables invoked after tool.run()
                        signature: hook(session_id, tool_name, input, result | None)
    Both sync and async callables are supported. Exceptions are swallowed.
    Register via AgentApp.register_tool_hook(before=fn, after=fn).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from emptyos.capabilities.providers._tool_capable import (
    AgentTurn,
    NativelyAgenticProvider,
    TextBlock,
    ToolCapableProvider,
    ToolUseBlock,
)
from emptyos.capabilities import cloud_gate
from emptyos.sdk.agent_tools.base import Tool, feature_enabled
from emptyos.sdk import tool_isolation, trace
from emptyos.sdk.operation import OperationSpec, RetryPolicy, operation_tool_display
from emptyos.sdk.retry_policy import repeated_tag

if TYPE_CHECKING:
    from emptyos.capabilities.tool_consent import ToolConsentManager
    from emptyos.kernel.event_bus import EventBus


DEFAULT_MAX_ITERS = 25
# Low temperature — this is analytical/tool-use work, not creative writing.
DEFAULT_TEMPERATURE = 0.3

# Safety reflexes (Phase 2). Thresholds inside one turn.
# When a tool fails this many times in a row (across the turn), we append a
# synthetic "stop and re-plan" note to the next tool_result — nudges the model
# out of bandaid loops like the calculator-debug transcript.
ERROR_LOOP_THRESHOLD = 3
# Hard cap on Edits to the same file in one turn. Any more is almost certainly
# the agent thrashing (edit → fix → fix-the-fix) — force a pause.
EDIT_PATH_LIMIT = 5

# Appended to the tool_result the model reads next when the error-loop reflex
# fires. Module-top like every other model-facing string in this file. `{shape}`
# takes one of the two templates below: the nudge tells the model to stop
# repeating "the same shape", so it may only assert sameness when `repeated_tag`
# confirms the failure actually did repeat.
LOOP_GUARD_NUDGE = (
    "[loop-guard] That's {shape} this turn. "
    "STOP retrying the same shape. Read the error text carefully. Don't "
    "add workarounds on top of broken fixes. Call TaskList to re-plan from "
    "scratch, or ask the user for more context. Another bandaid will make it worse."
)
LOOP_GUARD_SHAPE_REPEATED = "the SAME failure ({tag}) {count} times in a row"
LOOP_GUARD_SHAPE_COUNTED = "error #{count} in a row"

# Context management (Phase 4). Rough char→token ratio of 4:1 for English/code
# is good enough for budgeting — we don't need exact counts, just "is this
# session getting huge". Triggering above the ceiling is cheap: one pass
# that summarizes stale tool_result bodies.
COMPACT_CHAR_BUDGET = 200_000  # ~50K tokens — compact when session exceeds this.
# Keep the most-recent N assistant↔tool pairs fully intact. Recent context
# matters; the model was just reasoning about it. Older stuff gets summarized.
COMPACT_KEEP_RECENT_TURNS = 5
# Bodies shorter than this aren't worth summarizing (the summary marker is
# longer than the body). Leave them alone.
COMPACT_MIN_BODY_CHARS = 400

# Context Packing (Phase 2). Per-tool-result compression — orthogonal to the
# whole-session _compact_history above. A large recoverable tool output is
# packed to a structural summary + a ctx_ ref the model can recover via the
# ContextRef tool. Only kinds with a deterministic Phase-1 reducer pack; Read /
# VaultQuery / Fetch wait for content-aware/markdown reducers. Fail-open: any
# miss returns the content unchanged.
PACK_TOOL_MIN_CHARS = 8_000  # ~2K tokens — below this a grep dump isn't worth packing


def _infer_pack_kind(tool_name: str, tool_input: dict | None) -> str | None:
    """Map a tool call to a Context Packing reducer kind, or None to skip.

    Phase 1 ships only query-independent structural reducers; the one tool whose
    output cleanly matches one is Grep in content mode (`path:line:text`)."""
    if tool_name == "Grep" and (tool_input or {}).get("mode") == "content":
        return "search_result"
    return None


async def _maybe_pack_tool_output(
    app_ref: Any, tool_name: str, tool_input: dict | None, content: str
) -> str:
    """Pack a large recoverable tool result; fail-open to unchanged content.

    Runs the synchronous packer off the event loop (Async Boundary rule) and
    stores the original behind a ctx_ ref under data/context/. Returns content
    unchanged on any miss: small output, no inferable kind, no kernel ref, or a
    packing error.
    """
    if not isinstance(content, str) or len(content) < PACK_TOOL_MIN_CHARS:
        return content
    kind = _infer_pack_kind(tool_name, tool_input)
    if kind is None:
        return content
    try:
        store_root = app_ref.kernel.config.data_dir / "context"
    except Exception:
        return content  # bare-stub app_ref (tests) or no kernel — skip silently
    try:
        from emptyos.context import pack_text

        result = await asyncio.to_thread(
            pack_text, content, kind, source=tool_name, store_root=store_root
        )
        return content if result.fail_open else result.text
    except Exception:
        return content


DEFAULT_SYSTEM_PROMPT = (
    "You are EmptyOS Agent — a coding companion integrated into the user's personal OS.\n"
    "\n"
    "Tools:\n"
    "• Read / Grep / Glob — inspect the codebase (auto-approved, non-destructive). "
    "  Relative paths resolve against the EmptyOS repo root (the directory with emptyos.toml).\n"
    "• Bash — shell commands. Shell metacharacters are supported (|, &&, ||, ;, redirects). "
    "  Read-only commands (git status/log/diff, ls, cat, rg, etc.) auto-approve; anything else "
    "  asks permission. CWD defaults to repo root.\n"
    "• Python — run a Python snippet in an isolated subprocess. Use print() to produce output. "
    "  Good for quick calculations, data transforms, regex checks, or prototyping before writing "
    "  to a file. Always asks permission.\n"
    "• Edit — exact string replacement in an existing file. Prefer Edit over Write for "
    "  targeted changes; the uniqueness check protects against rewriting the wrong spot.\n"
    "• Write — create a new file or full rewrite. Parent directories are created automatically. "
    "  Asks permission every time.\n"
    "• CallApp — invoke another EmptyOS app's method (task.add_task, journal.add_entry, "
    "  projects.add_task_to_project, etc.). Call with no args to list apps, or with just "
    "  an app_id to list its methods. This is how you wire results into the wider OS.\n"
    "• Skill — load a Claude-Code-compatible SKILL.md playbook on demand. The system prompt "
    "  lists available skills with short descriptions; call `Skill(op='load', name='…')` when "
    "  the task matches, then follow the playbook. Don't guess skill contents.\n"
    "• TaskList — plan-and-track scratchpad. For any request that takes 3+ distinct steps "
    "  or spans multiple files, start with `TaskList(tasks=[{id, content, status}, …])` to "
    "  lay out the plan. Flip exactly one task to `in_progress` at a time; mark `completed` "
    "  immediately after finishing it (don't batch). Pass the FULL list on every call — it "
    "  replaces prior state. Skip it for trivial single-step asks.\n"
    "• Fetch — make an HTTP request so you can verify your own work. After a code edit + "
    "  daemon restart, hit `http://localhost:9000/<prefix>/` or `/api/...` yourself to confirm "
    "  it actually works — do NOT ask the user to open a browser and report back. GET to "
    "  localhost/loopback/private-IP auto-approves; other methods or public URLs ask permission.\n"
    "• RestartDaemon — restart the EmptyOS daemon to pick up Python edits. After any successful "
    "  Write/Edit on a .py file, ask the user if they want you to RestartDaemon (it asks "
    "  permission anyway). Spawned detached — your chat session survives. Wait ~3–5s after, "
    "  then Fetch /api/health to confirm the daemon is back.\n"
    "• Screenshot — render a URL in a headless browser. Returns PNG path + body.innerText + "
    "  console/page errors. Use this when Fetch isn't enough — blank-page bugs, JS errors, "
    "  UI regressions. Auto-approves for localhost. Prefer Fetch for quick HTTP checks; "
    "  reach for Screenshot when you need to see what actually rendered.\n"
    "• SubAgent — delegate a well-scoped subtask to a nested agent turn. The subagent has its "
    "  own tool loop (same tools, same consent rules). Returns final text + tools used summary. "
    "  Use for parallelisable research, isolated single-file edits, or verification passes. "
    "  Don't nest more than 1 level deep. Always asks permission.\n"
    "\n"
    "Investigation order (follow every time you're asked to debug or modify code):\n"
    "1. Reproduce or observe the problem. Don't theorize from the user's description alone.\n"
    "2. Grep the codebase for prior art — how do OTHER apps/modules solve this? Match existing\n"
    "   patterns. Never invent a new pattern until you've confirmed the existing one doesn't fit.\n"
    "3. Read the relevant framework source (emptyos/web/server.py, emptyos/kernel/, emptyos/sdk/)\n"
    "   before asserting how it behaves. EmptyOS is FastAPI — NOT aiohttp, NOT flask, NOT starlette-direct.\n"
    "4. Read CLAUDE.md and .claude/rules/ — operational truth lives there (e.g. 'Python changes need\n"
    "   daemon restart; HTML hot-reloads from disk'). Don't skip this.\n"
    "5. If the user named a skill (e.g. /eos-new-app), load it with Skill(op='load', name='…')\n"
    "   BEFORE paraphrasing. Your memory of how an EmptyOS skill works is almost certainly wrong.\n"
    "\n"
    "Daemon lifecycle:\n"
    "• Python code changes (apps/*.py, plugins/*.py, emptyos/**/*.py) require a daemon restart.\n"
    "  You can run it yourself: call `RestartDaemon(reason='…')` — it asks the user for permission,\n"
    "  spawns restart.bat detached, returns control to you. Don't hand this back to the user\n"
    "  manually unless RestartDaemon isn't available.\n"
    "• HTML/CSS/JS under pages/ DOES hot-reload (read per request) — no restart needed.\n"
    "• After RestartDaemon succeeds, wait a moment and then VERIFY: Fetch /api/health first\n"
    "  (confirms the daemon is back up), then Fetch or Screenshot the actual endpoint you\n"
    "  changed. Never ask the user 'does it work now?' when you can check yourself.\n"
    "• If a route returns `{}` or an empty response, the app is probably returning a non-FastAPI\n"
    "  object (e.g. aiohttp Response) or the daemon hasn't been restarted since the last edit.\n"
    "  Check those TWO things before adding shadow routes.\n"
    "• If the page renders blank or JS is broken, Fetch won't see it — use Screenshot to get\n"
    "  body.innerText + console errors. That's the fastest way to tell apart 'HTML is empty'\n"
    "  from 'HTML looks right but JS errored on load'.\n"
    "\n"
    "Principles:\n"
    "• Work in small, verifiable steps. Read before you edit. Narrate what you find.\n"
    "• 'With you, not for you' — surface options, don't take destructive actions unprompted.\n"
    "• When a tool errors, STOP. Read the error content. Don't retry the same shape with a\n"
    "  tiny variation — and NEVER add bandaid fixes on top of a broken fix. Re-investigate.\n"
    "• For multi-step work (3+ distinct steps, spanning files, or debugging with >2 hypotheses),\n"
    "  call TaskList FIRST with the plan, then work against it. One `in_progress` at a time.\n"
    "• Prefer terse output: no markdown headers, no numbered 'next steps' lists, no 'Would you\n"
    "  like me to…' branches on every turn. Answer the question, show results, pick the next\n"
    "  concrete action, do it. A report is not a plan.\n"
    "\n"
    "DO NOT:\n"
    "• Invent framework patterns. If you're about to `import aiohttp.web` in an EmptyOS app,\n"
    "  `import flask`, or write a Django view — STOP. EmptyOS is FastAPI. Handlers return `dict`\n"
    "  (auto-JSON) or `fastapi.responses.HTMLResponse` / `FileResponse` for non-JSON.\n"
    '• Write a `@web_route("GET", "/")` handler to serve `pages/index.html`. The platform\n'
    "  auto-mounts pages/index.html at {prefix}/ when the pages/ directory exists\n"
    "  (see emptyos/web/server.py `_mount_loaded_app_routes`). A custom `/` handler SHADOWS\n"
    "  the auto-mount and breaks the UI.\n"
    "• Call `CallApp()` with no args (or just an app_id) just to discover what apps/methods\n"
    "  exist — the catalog is already in your system prompt. Go straight to\n"
    "  `CallApp(app_id='…', method='…', arguments={…})` using what you can see. The one-arg\n"
    "  list-methods form is only a fallback when the method name you need isn't obvious.\n"
    "• Guess file contents — Read them first. Hallucinated paths waste the user's time.\n"
    "• Make the old_string for Edit too short or ambiguous — copy a unique snippet with\n"
    "  surrounding context. If you get 'occurs N times', extend the snippet; don't jump to replace_all.\n"
    "• Chain more than 3–4 tool calls before reporting back.\n"
    "• Add shadow/bandaid routes, try/except-swallowing, or other workarounds to make symptoms\n"
    "  go away. Find the root cause. If you're adding a 4th attempt without verifying the 3rd,\n"
    "  you've lost the plot — stop and ask the user for more context.\n"
    "• Run non-read Bash commands (install, push, delete, network fetches) without saying why.\n"
    "• Embed secrets, credentials, or large raw vault content in your responses."
)


@dataclass
class AgentSession:
    """In-memory session state for one turn execution.

    `messages` is the growing list of provider-native message dicts. The agent
    app persists these to SQLite between turns; the loop operates on the
    in-memory list passed in and mutates it in place.
    """

    id: str
    messages: list[dict] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    provider_kind: str = "anthropic"  # "anthropic" | "openai" | "json"
    # True (default) = a human reviews each side-effecting action (interactive
    # eos chat, the rooms [DO:] gate). False = an UNSUPERVISED loop (autopilot
    # auto-apply, a headless staff/fix-drain agent) where side-effecting tools
    # are isolated/refused per the tool-execution-isolation policy. The default
    # keeps every current consumer on the unchanged inline path; an unsupervised
    # consumer opts in by constructing its session with supervised=False.
    supervised: bool = True

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self):
        self.cancel_event.set()


async def _emit(events, event_type: str, data: dict, source: str = "agent"):
    if events is None:
        return
    try:
        await events.emit(event_type, trace.stamp_trace(data), source=source)
    except Exception:
        pass


async def _meter_think(app_ref: Any, provider: Any, usage: dict | None) -> None:
    """Feed one agent-loop model round-trip into billing as ``think:executed``.

    ``run_turn`` calls ``provider.execute_tools()`` directly, bypassing
    ``BaseApp.think()`` — the only *other* emitter of ``think:executed`` (the
    event ``apps/public/standard/billing`` meters). Without this, every
    autonomous loop (``eos chat``, fix-agent, dogfood-agent) runs invisible to
    billing AND the trace-costs feature.

    Emitted on the KERNEL bus, not the per-session ``events`` bridge: the
    ``ConsoleBridge`` used by ``eos chat`` does not forward to the kernel, so a
    bridge-routed ``think:executed`` would never reach billing. ``trace_id`` is
    stamped from the ambient scope (``run_turn`` is ``@trace.trace_boundary``).

    No double-count: the tool-capable providers (openai_compat / anthropic_sdk)
    never emit ``think:executed`` themselves — only ``BaseApp.think`` does, and
    the loop does not go through it. ``turn.usage`` is per-round-trip (each
    ``execute_tools`` returns one response's usage), so one emit per iteration
    aggregates correctly. Normalizes the two provider usage dialects
    (OpenAI ``prompt/completion_tokens`` vs Anthropic ``input/output_tokens``).
    """
    if not usage:
        return
    events = getattr(getattr(app_ref, "kernel", None), "events", None)
    if events is None:
        return
    pt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    ct = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cost = usage.get("cost") or 0
    if not pt and not ct and not cost:
        return
    app_id = (
        getattr(getattr(app_ref, "manifest", None), "id", None)
        or getattr(app_ref, "id", None)
        or "agent"
    )
    event_data = {
        "provider": getattr(provider, "name", "unknown"),
        "app": app_id,
        "model": usage.get("model") or getattr(provider, "model", "") or "",
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "cached_tokens": int(
            usage.get("cached_tokens") or usage.get("cache_read_input_tokens") or 0
        ),
        "cost": cost,
    }
    try:
        await events.emit("think:executed", trace.stamp_trace(event_data), source="agent-loop")
    except Exception:
        pass


def _format_tool_result_for_provider(
    kind: str, tool_use_id: str, content: str, is_error: bool
) -> dict | list[dict]:
    """Provider-specific wire shape for injecting a tool_result back in messages.

    Anthropic: user message with content=[{type: "tool_result", tool_use_id, content, is_error}]
    OpenAI:    role="tool" message with tool_call_id + content (string)
    """
    if kind == "anthropic":
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
            "is_error": is_error,
        }
    # OpenAI / JSON fallback
    return {
        "role": "tool",
        "tool_call_id": tool_use_id,
        "content": content if not is_error else f"[error]\n{content}",
    }


def _estimate_chars(messages: list[dict]) -> int:
    """Rough total-chars estimate across messages, ignoring keys/structure.
    Fast — just counts string payloads. ~4 chars per token is a serviceable
    proxy for budgeting, good enough to trigger compaction."""
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for block in c:
                if not isinstance(block, dict):
                    continue
                inner = block.get("content") or block.get("text") or ""
                if isinstance(inner, str):
                    total += len(inner)
        # tool_calls on OpenAI assistant messages carry JSON args
        for tc in m.get("tool_calls") or []:
            args = (tc.get("function") or {}).get("arguments") or ""
            if isinstance(args, str):
                total += len(args)
    return total


def _compact_history(
    messages: list[dict],
    *,
    char_budget: int = COMPACT_CHAR_BUDGET,
    keep_recent_turns: int = COMPACT_KEEP_RECENT_TURNS,
    min_body_chars: int = COMPACT_MIN_BODY_CHARS,
    archive: list | None = None,
) -> tuple[list[dict], int]:
    """Replace stale tool_result bodies with short summaries.

    Only tool_result CONTENT is compacted — never assistant text, never user
    input, never tool_call shapes. The structural invariants OpenAI/Anthropic
    require across turn boundaries (matching tool_calls ↔ tool responses)
    stay intact, so compaction is idempotent and doesn't corrupt history.

    When `archive` is passed, each elided original body is appended to it as
    a dict — compaction is the only point where session context is destroyed,
    so callers persist the originals instead of silently discarding them
    (`.claude/rules/prompt-prefix-cache.md`; Reasonix's archive pattern).

    Returns `(new_messages, chars_saved)`.
    """
    if _estimate_chars(messages) <= char_budget:
        return messages, 0

    # Walk message indexes and mark user messages (openai tool messages + anthropic
    # user-with-tool_result blocks) as "candidate for compaction" UNLESS they fall
    # inside the last N turns. A "turn" here = one assistant message + its
    # downstream tool_result message (counted pairwise from the end).
    assistant_indexes = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    protect_from = 0
    if len(assistant_indexes) > keep_recent_turns:
        protect_from = assistant_indexes[-keep_recent_turns]
    # Nothing before `protect_from` gets touched structurally — only tool_result
    # bodies. Assistant text stays intact regardless.

    chars_saved = 0
    out: list[dict] = []
    for idx, m in enumerate(messages):
        if idx >= protect_from:
            out.append(m)
            continue
        # OpenAI tool message — compact its string content
        if m.get("role") == "tool" and isinstance(m.get("content"), str):
            body = m["content"]
            if len(body) >= min_body_chars:
                first_line = body.splitlines()[0] if body else ""
                summary = f"{first_line[:150]}  [… summarized — {len(body)} chars elided from older turn …]"
                chars_saved += len(body) - len(summary)
                if archive is not None:
                    archive.append(
                        {
                            "msg_index": idx,
                            "role": "tool",
                            "tool_call_id": m.get("tool_call_id"),
                            "content": body,
                        }
                    )
                m2 = dict(m)
                m2["content"] = summary
                out.append(m2)
                continue
            out.append(m)
            continue
        # Anthropic user-with-tool_result-blocks — compact each tool_result block
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            new_blocks: list = []
            touched = False
            for b in m["content"]:
                if (
                    isinstance(b, dict)
                    and b.get("type") == "tool_result"
                    and isinstance(b.get("content"), str)
                    and len(b["content"]) >= min_body_chars
                ):
                    body = b["content"]
                    first_line = body.splitlines()[0] if body else ""
                    summary = f"{first_line[:150]}  [… summarized — {len(body)} chars elided from older turn …]"
                    chars_saved += len(body) - len(summary)
                    if archive is not None:
                        archive.append(
                            {
                                "msg_index": idx,
                                "role": "tool_result",
                                "tool_use_id": b.get("tool_use_id"),
                                "content": body,
                            }
                        )
                    b2 = dict(b)
                    b2["content"] = summary
                    new_blocks.append(b2)
                    touched = True
                else:
                    new_blocks.append(b)
            if touched:
                m2 = dict(m)
                m2["content"] = new_blocks
                out.append(m2)
                continue
            out.append(m)
            continue
        out.append(m)
    return out, chars_saved


def _archive_compacted(app_ref: Any, session_id: str, dropped: list[dict]) -> None:
    """Persist compaction-elided tool_result originals to a per-session JSONL.

    One line per compaction event under
    ``<app data_dir>/compaction-archive/<session_id>.jsonl``. Nothing reads it
    yet — it exists so a long run's accumulated experience survives compaction
    instead of being destroyed (EdgeBench: retained context beats restarts;
    Reasonix archives dropped originals rather than deleting). Fail-soft: an
    archive write must never break the agent loop.
    """
    if not dropped:
        return
    try:
        data_dir = getattr(app_ref, "data_dir", None)
        if not data_dir:
            return
        arch_dir = data_dir / "compaction-archive"
        arch_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "items": dropped,
        }
        safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(session_id))
        with open(arch_dir / f"{safe_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _assistant_message_for_provider(kind: str, turn: AgentTurn) -> dict:
    """Shape the assistant message for storage in the provider's native format."""
    if kind == "anthropic":
        content = []
        for block in turn.assistant_blocks:
            if isinstance(block, TextBlock):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        return {"role": "assistant", "content": content}

    # OpenAI: text in content, tool_use blocks as tool_calls.
    # Use "" (not null) when there's no text — OpenAI's spec allows null but
    # Ollama rejects it with `invalid message content type: <nil>`. Empty
    # string is accepted by both OpenAI and Ollama.
    text = "".join(b.text for b in turn.assistant_blocks if isinstance(b, TextBlock))
    msg: dict = {"role": "assistant", "content": text or ""}
    tool_calls = []
    for block in turn.assistant_blocks:
        if isinstance(block, ToolUseBlock):
            import json as _json

            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {"name": block.name, "arguments": _json.dumps(block.input)},
                }
            )
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


# Gated guidance appended to the system prompt only when the hash-edit dark flag
# is on ([apps.agent] feature.hash-edit.enabled). Dark deployments never see it.
HASH_EDIT_HINT = (
    "\n\nLarge-edit shortcut: for replacing a contiguous block in a big file, call "
    "Read with include_hashes=true to get per-line hashes, then Edit with "
    "start_line + end_line + expected_hash (the start line's hash) instead of a long "
    "old_string. Cheaper, and a stale anchor is rejected instead of editing the wrong span."
)

# Gated guidance: appended only when [apps.<driver>] feature.refactor-verify.enabled
# is on. Closes the "left a call site behind" failure the harness benchmark surfaced —
# economical loops under-execute multi-site refactors on weaker models.
REFACTOR_VERIFY_HINT = (
    "\n\nRefactoring discipline (multi-site changes): after a rename, an API change, or a "
    "pattern refactor that should touch multiple places, BEFORE you finish the turn — Grep for "
    "the OLD symbol/pattern. If any occurrence remains outside the files you already edited, you "
    "missed a site: go edit it. Re-Grep until zero remain. This catches the common failure of "
    "leaving call sites behind after the obvious edits."
)

# Option B (the hard gate): injected as a synthetic user turn when the model
# made edits and is about to finish, under the same refactor-verify flag. The
# soft hint (above) primes; this interrupts the finish and demands the self-check
# even from a weak model that skipped the hint. Fires at most once per turn.
REFACTOR_VERIFY_GATE_MSG = (
    "Before you finish: you edited files this turn. If that was a rename, an API change, or a "
    "multi-site refactor, run Grep NOW for the OLD symbol/pattern you were changing. If ANY "
    "occurrence remains outside the files you already edited, that's a missed call site — fix it, "
    "then re-Grep to confirm zero remain. If the change was genuinely single-site (or nothing "
    "remains), just confirm that and finish."
)

# No-action gate: injected once when the model's final response carries neither a
# visible reply nor a tool call — the user would see nothing. Measured on the local
# 9.7B model: it read a spec, spent its output on hidden reasoning, and stopped with
# empty content, so the file was never written (model-bench engineer-calc 0/6;
# 4/4 with the gate on, where it fired once per run — measured 2026-09-21).
# Keyed on the empty reply, not on guessing whether the task wanted a change, so a
# question answered in words never trips it. The message claims only what is known:
# earlier tool calls in the turn may already have done work, and a weak model told
# "nothing was done" could repeat a non-idempotent step.
NO_ACTION_GATE_MSG = (
    "Your last response was empty — no reply and no tool call. If the task is not "
    "finished, continue it now with the tool calls it needs. If it is finished, say "
    "briefly what you did. Do not repeat a step you have already completed."
)


def _is_empty_finish(turn: AgentTurn) -> bool:
    """True when a finishing turn carries no visible text and no tool call."""
    if turn.tool_uses:
        return False
    return not any(isinstance(b, TextBlock) and b.text.strip() for b in turn.assistant_blocks)


@trace.trace_boundary
async def run_turn(
    *,
    session: AgentSession,
    user_text: str,
    provider: ToolCapableProvider,
    tools: dict[str, Tool],
    tool_consent: ToolConsentManager | None,
    events: EventBus | None,
    app_ref: Any = None,  # passed to Tool.run() as `app`
    system: str = DEFAULT_SYSTEM_PROMPT,
    max_iters: int = DEFAULT_MAX_ITERS,
    temperature: float = DEFAULT_TEMPERATURE,
    edit_path_limit: int = EDIT_PATH_LIMIT,
    orient_plan: dict | None = None,  # pre-turn plan from _orient(); drives nudge
    user_content: Any = None,  # provider-native parts (text + images) — emptyos/sdk/attachments.py
) -> AgentTurn:
    """Run one user turn to completion (stop_reason != tool_use or max_iters).

    Mutates `session.messages` in place with the full transcript for this turn.
    Returns the final AgentTurn (last provider round-trip). ``user_content``,
    when given, is the opening message's content instead of ``user_text`` (a
    part list carrying images); ``user_text`` still names the turn in events.
    """
    kind = provider.kind
    session.provider_kind = kind

    # Append the user message in the provider's native shape
    session.messages.append({"role": "user", "content": user_content if user_content is not None else user_text})
    await _emit(
        events,
        "agent:turn_start",
        {
            "session_id": session.id,
            "user_text": user_text,
        },
    )

    # Gated: teach the line-anchored editing workflow only when the dark flag
    # is on. Off → the system prompt is byte-identical to before.
    if feature_enabled(app_ref, "hash-edit"):
        system = system + HASH_EDIT_HINT
    if feature_enabled(app_ref, "refactor-verify"):
        system = system + REFACTOR_VERIFY_HINT

    # Serialize tools once per turn — schemas don't change mid-turn
    wire_tools = [t.to_wire(kind) for t in tools.values()]
    last_turn: AgentTurn | None = None

    # Safety-reflex state (Phase 2). Turn-scoped; reset every user message.
    consecutive_errors = 0  # 2.1 — appended nudge when ≥ ERROR_LOOP_THRESHOLD
    # Coarse shape of each consecutive error, so the guard can tell a model
    # exploring three different failures from one stuck on the same failure.
    error_tags: list[str] = []
    edit_counts: dict[str, int] = {}  # 2.3 — count Edit calls per path
    _plan_nudge_sent = False  # only inject the plan reminder once per turn
    _made_edits = False  # turn touched files (drives the refactor-verify gate)
    _verify_forced = False  # refactor-verify gate already fired this turn (force once)
    _no_action_forced = False  # no-action gate already fired this turn (force once)

    def _maybe_loop_guard(
        content: str, counter: int, tag: str = ""
    ) -> tuple[str, int]:
        """Increment the consecutive-error counter and, if we've hit the
        loop-guard threshold, append a stop-and-replan nudge to the content
        the model will read on the next iteration. Shared by every error path
        (unknown tool, denied, edit-guard, tool.run failure, tool returned ok=False)
        so the counter actually reflects total errors this turn.

        ``tag`` is the coarse shape of this error (usually the tool name). The
        nudge has always told the model to "stop retrying the same shape", but
        the counter only knew how MANY errors had happened — never whether they
        were the same one, so it read identically to a model exploring three
        different failures and a model stuck on one. `repeated_tag` supplies the
        missing half, and the nudge now makes the claim only when it is true.
        """
        counter += 1
        error_tags.append(tag or "unknown")
        if counter >= ERROR_LOOP_THRESHOLD:
            # counter >= threshold means these were consecutive errors with no
            # intervening success, so the last two appends are always from the
            # current run — the comparison never reaches across a reset.
            stuck_on = repeated_tag(error_tags[-1:], error_tags[-2:-1])
            shape = (
                LOOP_GUARD_SHAPE_REPEATED.format(tag=stuck_on, count=counter)
                if stuck_on
                else LOOP_GUARD_SHAPE_COUNTED.format(count=counter)
            )
            content = (content or "") + "\n\n" + LOOP_GUARD_NUDGE.format(shape=shape)
        return content, counter

    for iter_idx in range(max_iters):
        if session.cancelled:
            await _emit(events, "agent:cancelled", {"session_id": session.id})
            raise asyncio.CancelledError()

        await _emit(
            events,
            "agent:iter_start",
            {
                "session_id": session.id,
                "iter": iter_idx,
            },
        )

        # ── Session compaction (Phase 4.1) ──
        # If the message history has grown past the budget, summarize old
        # tool_result bodies in place. Structural invariants (tool_calls ↔
        # tool_result pairing) are preserved — OpenAI won't 400 on the
        # compacted history. Recent turns stay intact.
        dropped: list[dict] = []
        compacted, saved = _compact_history(session.messages, archive=dropped)
        if saved > 0:
            session.messages = compacted
            # Archive the elided originals before they're gone for good —
            # compaction is the one deliberate context-destruction point
            # (`.claude/rules/prompt-prefix-cache.md`).
            _archive_compacted(app_ref, session.id, dropped)
            await _emit(
                events,
                "agent:compacted",
                {
                    "session_id": session.id,
                    "chars_saved": saved,
                    "archived_items": len(dropped),
                    "message_count": len(session.messages),
                },
            )

        try:
            # The loop calls the provider directly, past the capability chain,
            # so the monthly spend cap is checked here — before every round-trip,
            # since a long turn can cross the cap part-way through.
            kernel = getattr(app_ref, "kernel", None)
            app_id = getattr(getattr(app_ref, "manifest", None), "id", None)
            blocked = cloud_gate.check(kernel, provider, "think", app_id)
            if blocked:
                raise cloud_gate.error(kernel, blocked, "think", f"agent turn via {provider.name}")
            turn = await provider.execute_tools(
                messages=session.messages,
                system=system,
                tools=wire_tools,
                temperature=temperature,
            )
        except Exception as e:
            # str(e) is empty for asyncio.TimeoutError and some connection errors —
            # include the type so the UI never shows a bare "Error:".
            await _emit(
                events,
                "agent:error",
                {
                    "session_id": session.id,
                    "error": str(e) or type(e).__name__,
                    "type": type(e).__name__,
                },
            )
            raise

        last_turn = turn
        # Meter this round-trip's LLM usage into billing (think:executed on the
        # kernel bus) — the loop bypasses BaseApp.think(), so without this the
        # autonomous path is invisible to billing + trace-costs.
        await _meter_think(app_ref, provider, turn.usage)
        # Persist the assistant turn in messages
        session.messages.append(_assistant_message_for_provider(kind, turn))

        # Narrate text + tool_calls to listeners
        for block in turn.assistant_blocks:
            if isinstance(block, TextBlock) and block.text:
                await _emit(
                    events,
                    "agent:text",
                    {
                        "session_id": session.id,
                        "delta": block.text,
                    },
                )
            elif isinstance(block, ToolUseBlock):
                await _emit(
                    events,
                    "agent:tool_call",
                    {
                        "session_id": session.id,
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    },
                )

        if turn.stop_reason != "tool_use":
            # No-action gate: an empty finish gets one nudge to actually act.
            if (
                not _no_action_forced
                and _is_empty_finish(turn)
                and feature_enabled(app_ref, "no-action-gate")
            ):
                _no_action_forced = True
                # Drop the empty assistant message first: Anthropic rejects a
                # non-final message with empty content, so keeping it would turn
                # this nudge into a 400 on the next call.
                session.messages.pop()
                session.messages.append({"role": "user", "content": NO_ACTION_GATE_MSG})
                await _emit(events, "agent:no_action_gate", {"session_id": session.id})
                continue
            # Refactor-verify gate (Option B): if the turn made edits and is about
            # to finish, force ONE re-grep/self-check pass before stopping. Catches
            # "left a call site behind" that the soft hint misses on weak models.
            # Fires at most once per turn (then we let it finish) — no infinite loop.
            if (
                _made_edits
                and not _verify_forced
                and feature_enabled(app_ref, "refactor-verify")
            ):
                _verify_forced = True
                session.messages.append(
                    {"role": "user", "content": REFACTOR_VERIFY_GATE_MSG}
                )
                await _emit(
                    events, "agent:refactor_verify_gate", {"session_id": session.id}
                )
                continue
            await _emit(
                events,
                "agent:done",
                {
                    "session_id": session.id,
                    "usage": turn.usage,
                    "stop_reason": turn.stop_reason,
                },
            )
            return turn

        # Dispatch tool_uses. Collect tool_results in a single user message.
        tool_result_blocks: list[dict] = []
        tool_result_messages: list[dict] = []  # OpenAI needs separate role=tool per call

        for tu in turn.tool_uses:
            if session.cancelled:
                await _emit(events, "agent:cancelled", {"session_id": session.id})
                raise asyncio.CancelledError()

            tool = tools.get(tu.name)
            if tool is None:
                err = f"error: tool {tu.name!r} is not registered in this session"
                err, consecutive_errors = _maybe_loop_guard(
                    err, consecutive_errors, tag=f"unknown-tool:{tu.name}")
                await _emit(
                    events,
                    "agent:tool_result",
                    {
                        "session_id": session.id,
                        "id": tu.id,
                        "is_error": True,
                        "display": {"name": tu.name, "error": err},
                    },
                )
                _append_result(tool_result_blocks, tool_result_messages, kind, tu.id, err, True)
                continue

            # Resolve per-call permission (Bash may override its class default)
            tool_default = getattr(tool, "permission_for", None)
            perm_default = tool_default(tu.input) if callable(tool_default) else tool.permission
            summary = (
                tool.permission_summary(tu.input)
                if hasattr(tool, "permission_summary")
                else f"{tu.name}(...)"
            )

            allowed = True
            if tool_consent is not None:
                allowed = await tool_consent.check(
                    session_id=session.id,
                    tool=tu.name,
                    input=tu.input,
                    summary=summary,
                    tool_default=perm_default,
                )

            if not allowed:
                err = "denied by user"
                err, consecutive_errors = _maybe_loop_guard(
                    err, consecutive_errors, tag=f"denied:{tu.name}")
                await _emit(
                    events,
                    "agent:tool_result",
                    {
                        "session_id": session.id,
                        "id": tu.id,
                        "is_error": True,
                        "display": {"name": tu.name, "denied": True},
                    },
                )
                _append_result(tool_result_blocks, tool_result_messages, kind, tu.id, err, True)
                continue

            # ── Plan mode gate ──
            # When the session is in plan mode, block any non-readonly tool.
            # Agent must investigate + propose; user flips plan mode off with
            # /execute (or /scrap) before anything actually changes.
            _plan_active = (
                bool(getattr(app_ref, "_plan_modes", {}).get(session.id, False))
                if app_ref is not None
                else False
            )
            if _plan_active and not tool.is_readonly(tu.input or {}):
                gate_msg = (
                    f"[plan mode] Tool {tu.name!r} is blocked while planning. "
                    "Only read-only tools (Read, Grep, Glob, Skill, TaskList, Screenshot, "
                    "Fetch-GET) are allowed. Investigate, draft a plan (inline or via "
                    "TaskList), then STOP — the user will type /execute to leave plan mode "
                    "or /scrap to discard the plan."
                )
                gate_msg, consecutive_errors = _maybe_loop_guard(
                    gate_msg, consecutive_errors, tag=f"plan-gate:{tu.name}")
                await _emit(
                    events,
                    "agent:tool_result",
                    {
                        "session_id": session.id,
                        "id": tu.id,
                        "is_error": True,
                        "content": gate_msg,
                        "display": {"name": tu.name, "gated": "plan_mode"},
                        "error_snippet": gate_msg[:300],
                    },
                )
                _append_result(
                    tool_result_blocks, tool_result_messages, kind, tu.id, gate_msg, True
                )
                continue

            # ── Safety reflex 2.3: Edit loop-guard ──
            # Hard-cap repeated edits to the same file in one turn. If the
            # agent is on the 6th edit of foo.py, something is wrong —
            # force a pause so it Reads fresh and re-plans instead of
            # chaining patches.
            if tu.name == "Edit":
                edit_path = (tu.input or {}).get("path", "") or ""
                edit_counts[edit_path] = edit_counts.get(edit_path, 0) + 1
                if edit_counts[edit_path] > edit_path_limit:
                    guard_msg = (
                        f"error: edit loop-guard — you've Edited {edit_path!r} {edit_counts[edit_path]} "
                        f"times in this turn (limit: {edit_path_limit}). STOP. Re-Read the file from "
                        f"disk, reconsider the whole approach, and if the right fix isn't obvious, "
                        f"ask the user. Don't keep patching. (User can raise the cap with /grant-edits N.)"
                    )
                    guard_msg, consecutive_errors = _maybe_loop_guard(
                        guard_msg, consecutive_errors, tag=f"edit-cap:{tu.name}")
                    await _emit(
                        events,
                        "agent:tool_result",
                        {
                            "session_id": session.id,
                            "id": tu.id,
                            "is_error": True,
                            "content": guard_msg,
                            "display": {"name": tu.name, "guard": "edit_loop", "path": edit_path},
                            "error_snippet": guard_msg[:300],
                        },
                    )
                    _append_result(
                        tool_result_blocks, tool_result_messages, kind, tu.id, guard_msg, True
                    )
                    continue

            # ── Pre-tool hooks ──
            if app_ref is not None:
                for _hook in getattr(app_ref, "_before_tool_hooks", ()):
                    try:
                        _r = _hook(session.id, tu.name, tu.input)
                        if inspect.isawaitable(_r):
                            await _r
                    except Exception:
                        pass

            # ── Tool-execution isolation (dark-flagged) ──
            # Side-effecting tools on an UNSUPERVISED session are jailed (Bash)
            # or refused (no executor yet) instead of running raw in the daemon.
            # Flag off OR readonly OR supervised → "inline" → unchanged path.
            # See emptyos/sdk/tool_isolation.py.
            iso_flag = False
            if app_ref is not None and hasattr(app_ref, "app_config"):
                try:
                    iso_flag = bool(app_ref.app_config(tool_isolation.FLAG_KEY, False))
                except Exception:
                    iso_flag = False
            iso_decision = tool_isolation.decide(
                tool_name=tu.name,
                is_readonly=tool.is_readonly(tu.input or {}),
                supervised=getattr(session, "supervised", True),
                flag_on=iso_flag,
            )
            if iso_decision == "refuse":
                refuse_msg = (
                    f"error: [isolation] tool {tu.name!r} is side-effecting and this "
                    "session is unsupervised — it has no isolation executor, so it is "
                    "refused rather than run raw in the daemon. Run this in a supervised "
                    "session, or restrict the unsupervised agent to read-only + Bash."
                )
                refuse_msg, consecutive_errors = _maybe_loop_guard(
                    refuse_msg, consecutive_errors, tag="bash-refused")
                await _emit(
                    events,
                    "agent:tool_result",
                    {
                        "session_id": session.id,
                        "id": tu.id,
                        "is_error": True,
                        "content": refuse_msg,
                        "display": {"name": tu.name, "gated": "isolation"},
                        "error_snippet": refuse_msg[:300],
                    },
                )
                _append_result(
                    tool_result_blocks, tool_result_messages, kind, tu.id, refuse_msg, True
                )
                continue

            _t_tool = time.monotonic()
            op_result = None

            async def _run_tool_inline():
                if iso_decision == "isolate":
                    jail = tool_isolation.jailed_workspace(app_ref, session.id)
                    return await tool_isolation.run_bash_jailed(
                        tool, app_ref, tu.input or {}, jail_dir=jail
                    )
                return await tool.run(app_ref, **(tu.input or {}))

            try:
                op_runner = getattr(app_ref, "operation", None) if app_ref is not None else None
                if op_runner is not None:
                    tool_input = tu.input or {}
                    tool_readonly = tool.is_readonly(tool_input)
                    op_spec = OperationSpec(
                        name=f"agent.tool.{tu.name}",
                        actor=f"agent:{session.id}",
                        input=tool_input,
                        safety="stable" if tool_readonly else "gated",
                        readonly=tool_readonly,
                        timeout_s=None,
                        retry=RetryPolicy(max_attempts=0),
                        metadata={
                            "session_id": session.id,
                            "tool": tu.name,
                            "tool_call_id": tu.id,
                            "isolation": iso_decision,
                        },
                    )
                    op_result = await op_runner.run(op_spec, _run_tool_inline)
                    if op_result.ok:
                        result = op_result.value
                        content = result.content
                        is_error = not result.ok
                        display = result.display or {}
                    else:
                        err = op_result.error
                        message = err.message if err else "operation failed"
                        kind_label = err.kind if err else "bug"
                        content = f"error: [{kind_label}] {message}"
                        is_error = True
                        display = {"operation": operation_tool_display(op_result)}
                else:
                    result = await tool.run(app_ref, **(tu.input or {}))
                    content = result.content
                    is_error = not result.ok
                    display = result.display or {}
            except Exception as e:
                content = f"error: {e}"
                is_error = True
                display = {"name": tu.name, "exception": str(e)}
                if op_result is not None:
                    display["operation"] = operation_tool_display(op_result)

            # Tool latency — the missing half of the LLM-vs-tool time split
            # (think calls already carry latency_ms on think:executed). Flows
            # to the agent:tool_result event via `display`, and back onto
            # result.display so after-hooks (tool-audit JSONL) can read it.
            display["duration_ms"] = round((time.monotonic() - _t_tool) * 1000)
            if op_result is not None:
                op_display = operation_tool_display(op_result)
                if is_error and op_result.ok:
                    op_display = {
                        **op_display,
                        "ok": False,
                        "error": {
                            "kind": "tool_error",
                            "message": content[:500]
                            if isinstance(content, str)
                            else str(content)[:500],
                            "recoverable": True,
                            "suggested_next": "read the tool result and adjust the next tool call",
                        },
                    }
                display["operation"] = op_display
            if not is_error:
                result.display = display

            # ── Post-tool hooks ──
            if app_ref is not None:
                for _hook in getattr(app_ref, "_after_tool_hooks", ()):
                    try:
                        _r = _hook(session.id, tu.name, tu.input, result if not is_error else None)
                        if inspect.isawaitable(_r):
                            await _r
                    except Exception:
                        pass

            # ── Context Packing (Phase 2): compress large recoverable output ──
            # Pack the model-facing content of a successful read-type tool; the
            # original is recoverable via the ContextRef tool. Read-type tools
            # never reach the Write/Edit reflex below, so this seam is clean.
            if not is_error:
                content = await _maybe_pack_tool_output(app_ref, tu.name, tu.input, content)

            # ── Safety reflex 2.2: Python-edit daemon-restart nudge ──
            # Any successful Write/Edit on a .py file inside the EmptyOS repo
            # means the running daemon still holds the old bytecode. Nudge the
            # model to tell the user + Fetch-verify after restart. Cheap to
            # append; the model reads it on the next iteration.
            if not is_error and tu.name in ("Write", "Edit", "MultiEdit", "DeleteFunction"):
                _made_edits = True  # arms the refactor-verify gate at finish time
            if not is_error and tu.name in ("Write", "Edit"):
                edited_path = (
                    (tu.input or {}).get("path")
                    or (display.get("path") if isinstance(display, dict) else None)
                    or ""
                )
                if isinstance(edited_path, str) and edited_path.endswith(".py"):
                    content = (
                        (content or "")
                        + "\n\n[daemon-hint] You edited Python. The running daemon still has the "
                        "OLD code — tell the user to restart it (`restart.bat` or Ctrl+C + "
                        "`python -m emptyos start`), then Fetch the affected endpoint to verify "
                        "the change actually took effect."
                    )
                # Push onto the shared edit-history stack for /revert. The
                # display dict carries the pre-edit bytes already (Write and
                # Edit both surface `previous_content`). Host app may not have
                # _push_edit (tests use bare stubs) — defend accordingly.
                if hasattr(app_ref, "_push_edit") and isinstance(display, dict):
                    entry = {
                        "path": display.get("path") or edited_path,
                        "action": display.get("action")
                        or ("edit" if tu.name == "Edit" else "overwrite"),
                        "previous_content": display.get("previous_content", ""),
                    }
                    if entry["path"]:
                        app_ref._push_edit(session.id, entry)

            # ── Safety reflex 2.1: Error-loop detector ──
            # Normal tool-result path — update the counter and, if we've crossed
            # the threshold, append a stop-and-replan nudge. Other error paths
            # (unknown tool, denied, edit-guard) feed the same counter via
            # `_maybe_loop_guard()` above so bandaid loops get caught across
            # every failure shape.
            if is_error:
                content, consecutive_errors = _maybe_loop_guard(
                    content, consecutive_errors, tag=f"tool-error:{tu.name}")
            else:
                # Reset both halves together — a stale tag list would outlive the
                # run it described and grow across a long turn.
                consecutive_errors = 0
                error_tags.clear()

            # Include a truncated error snippet so benchmark + debug tools can
            # categorize failures without re-running. Skipped on success to
            # avoid duplicating potentially-huge tool outputs into the event bus.
            error_snippet = content[:300] if is_error and isinstance(content, str) else None
            # Forward the actual tool output to the UI, capped so a giant grep
            # doesn't flood the WebSocket. Model still gets the untruncated
            # version via _append_result below.
            ui_content = content if isinstance(content, str) else str(content)
            if len(ui_content) > 8000:
                ui_content = ui_content[:8000] + f"\n... (truncated from {len(content)} chars)"
            await _emit(
                events,
                "agent:tool_result",
                {
                    "session_id": session.id,
                    "id": tu.id,
                    "is_error": is_error,
                    "content": ui_content,
                    "display": {"name": tu.name, **display},
                    "error_snippet": error_snippet,
                },
            )
            _append_result(tool_result_blocks, tool_result_messages, kind, tu.id, content, is_error)

        # Append tool_results in the provider's native shape
        if kind == "anthropic":
            session.messages.append({"role": "user", "content": tool_result_blocks})
        else:
            session.messages.extend(tool_result_messages)

        # Plan nudge (Phase 5): at iteration 3, if an orient plan was supplied
        # and hasn't been injected yet, remind the model of its own plan steps.
        # Uses a plain user message (valid for both Anthropic and OpenAI) so no
        # tool_use/tool_result pairing invariant is disturbed.
        if orient_plan and not _plan_nudge_sent and iter_idx == 2:
            steps = orient_plan.get("investigation_plan") or []
            if steps:
                nudge = (
                    "[Plan reminder — steps you said you'd take: "
                    + "; ".join(f"{i + 1}. {s}" for i, s in enumerate(steps[:5]))
                    + "]"
                )
                session.messages.append({"role": "user", "content": nudge})
                _plan_nudge_sent = True
                await _emit(
                    events,
                    "agent:plan_nudge",
                    {
                        "session_id": session.id,
                        "iter": iter_idx,
                    },
                )

    # Exhausted iterations
    await _emit(events, "agent:max_iters", {"session_id": session.id, "iters": max_iters})
    return last_turn or AgentTurn(stop_reason="max_tokens")


def _append_result(
    anthropic_blocks: list[dict],
    openai_messages: list[dict],
    kind: str,
    tool_use_id: str,
    content: str,
    is_error: bool,
):
    """Append a tool_result in the right shape based on provider kind."""
    result = _format_tool_result_for_provider(kind, tool_use_id, content, is_error)
    if kind == "anthropic":
        anthropic_blocks.append(result)  # type: ignore[arg-type]
    else:
        openai_messages.append(result)  # type: ignore[arg-type]


# ── Native-agent turn (for providers that run their own tool loop) ────────


@trace.trace_boundary
async def run_native_turn(
    *,
    session: AgentSession,
    user_text: str,
    provider: NativelyAgenticProvider,
    events: EventBus | None,
    system: str = DEFAULT_SYSTEM_PROMPT,
    temperature: float = DEFAULT_TEMPERATURE,
) -> str:
    """Run one turn by delegating to a natively-agentic provider.

    Unlike `run_turn`, this does not drive a tool-use loop — it hands the turn
    to the provider (e.g. claude-cli), streams its narration events through to
    listeners, and persists the final assistant text. The provider's built-in
    tools do their own work; our Tool registry and tool_consent are bypassed.

    Messages are stored as flat `{role, content: str}` pairs (not block arrays)
    — that's the shape natively-agentic providers consume and emit.
    """
    session.messages.append({"role": "user", "content": user_text})
    session.provider_kind = "native"

    await _emit(
        events,
        "agent:turn_start",
        {
            "session_id": session.id,
            "user_text": user_text,
            "native": True,
        },
    )
    await _emit(events, "agent:iter_start", {"session_id": session.id, "iter": 0})

    # Synthetic tool_call events indexed by an incrementing counter — the
    # provider's stream gives us status strings but no IDs.
    tool_event_counter = [0]

    async def _emit_tool_status(status: str, name: str):
        tool_event_counter[0] += 1
        eid = f"native_{tool_event_counter[0]}"
        await _emit(
            events,
            "agent:tool_call",
            {
                "session_id": session.id,
                "id": eid,
                "name": name or "tool",
                "input": {"summary": status},
                "native": True,
            },
        )
        # Natively-agentic tools don't surface result payloads back to us —
        # the CLI has already consumed them. Emit a matched result so the UI
        # can close the pair visually.
        await _emit(
            events,
            "agent:tool_result",
            {
                "session_id": session.id,
                "id": eid,
                "is_error": False,
                "display": {"name": name or "tool", "status": status, "native": True},
            },
        )

    accumulated_text = ""
    last_text_chunk = ""
    last_usage: dict = {}
    try:
        # stream_json=True asks the provider to emit tool narration events
        # (where supported — claude-cli uses --output-format stream-json).
        # Providers that don't know this kwarg ignore it.
        async for chunk in provider.execute_stream(
            messages=session.messages,
            system=system,
            prompt=user_text,
            temperature=temperature,
            stream_json=True,
        ):
            if session.cancelled:
                await _emit(events, "agent:cancelled", {"session_id": session.id})
                raise asyncio.CancelledError()

            if "tool_status" in chunk:
                await _emit_tool_status(chunk.get("tool_status", ""), chunk.get("tool", ""))
                continue

            text = chunk.get("text", "")
            done = chunk.get("done", False)
            if text:
                # Some providers (claude-cli) emit the final full result as the
                # last text chunk — duplicates what was already streamed. Detect
                # and skip to avoid doubled output.
                if done or text != last_text_chunk:
                    accumulated_text += text
                    last_text_chunk = text
                    if not done:
                        await _emit(
                            events,
                            "agent:text",
                            {
                                "session_id": session.id,
                                "delta": text,
                            },
                        )

            if "usage" in chunk:
                last_usage = chunk["usage"] or {}
                await _emit(
                    events,
                    "agent:usage",
                    {
                        "session_id": session.id,
                        "usage": last_usage,
                    },
                )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await _emit(events, "agent:error", {"session_id": session.id, "error": str(e)})
        raise

    session.messages.append({"role": "assistant", "content": accumulated_text})
    await _emit(
        events,
        "agent:done",
        {
            "session_id": session.id,
            "usage": last_usage,
            "stop_reason": "end_turn",
            "native": True,
        },
    )
    return accumulated_text
