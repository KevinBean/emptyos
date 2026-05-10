"""Claude CLI provider — uses Claude Code CLI (free with Max subscription).

Calls the `claude` command as a subprocess. Same interface as any provider.
Apps call self.think() and don't know it's a subprocess underneath.

Features absorbed from AI Phone Agent:
- stream-json output format for tool-use status events
- Global lock (CLI can only handle one call at a time)
- Per-line idle timeout + absolute max timeout
- Tool status formatting (Reading file.md, Searching for '...')
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

from emptyos.capabilities.providers._tool_capable import NativelyAgenticProvider

# Semaphore — Claude CLI can only handle one concurrent call
_claude_sem = asyncio.Semaphore(1)

# Substrings that identify a Claude usage/rate-limit response. These appear in
# stdout (not stderr) on a Max-plan limit hit, sometimes with exit code 0 — so
# we can't rely on returncode alone to decide "this isn't a real answer".
_LIMIT_MARKERS = (
    "hit your limit",
    "usage limit",
    "rate limit",
    "· resets ",
    "resets at ",
    "5-hour limit",
    "weekly limit",
    "quota exceeded",
    "credit balance",
)


def _looks_like_limit_error(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in _LIMIT_MARKERS)


def _messages_to_prompt(messages: list[dict], prompt: str = "") -> str:
    """Flatten a chat messages array into a single prompt the CLI can accept.

    The last message (if role=user) becomes the live question; everything
    before it is a labeled transcript so the model has turn context.
    """
    if not messages:
        return prompt
    msgs = list(messages)
    # Find last user message — that's the live question
    last_user_idx = None
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            last_user_idx = i
            break
    history = msgs[:last_user_idx] if last_user_idx is not None else msgs
    current = msgs[last_user_idx].get("content", "") if last_user_idx is not None else prompt

    lines = []
    if history:
        lines.append("[Prior conversation]")
        for m in history:
            role = m.get("role", "")
            content = m.get("content", "")
            if not content:
                continue
            label = {"user": "User", "assistant": "Assistant", "system": "System"}.get(
                role, role.title()
            )
            lines.append(f"{label}: {content}")
        lines.append("")
        lines.append("[Current message]")
    lines.append(current)
    return "\n".join(lines)


# Windows CreateProcess caps the command line at ~32767 chars. Stay well under
# so `--append-system-prompt` + other args + prompt still fit; above the cap,
# we pipe the prompt through stdin instead. Applied on every platform because
# Linux limits are much higher and the stdin path is uniformly safe.
_ARG_LIMIT_BYTES = 28000


def _plan_prompt_channel(prompt: str, system: str, other_args_bytes: int) -> tuple[bool, str, str]:
    """Decide whether to pass the prompt as a CLI arg or via stdin.

    Returns (use_stdin, effective_prompt, effective_system).
    If system alone still pushes total over the limit, fold it into the prompt
    body and drop `--append-system-prompt` (system is returned as "").
    """
    prompt_b = len(prompt.encode("utf-8", errors="replace"))
    system_b = len(system.encode("utf-8", errors="replace"))

    # Fits entirely on the command line
    if other_args_bytes + system_b + prompt_b < _ARG_LIMIT_BYTES:
        return (False, prompt, system)

    # Prompt via stdin; if system still fits as a CLI arg, keep it there
    if other_args_bytes + system_b < _ARG_LIMIT_BYTES:
        return (True, prompt, system)

    # System prompt itself is too big — fold into prompt body
    folded = f"[System]\n{system}\n\n[User]\n{prompt}" if system else prompt
    return (True, folded, "")


def _format_tool_status(tool_name: str, tool_input: dict) -> str:
    """Format a tool call into a human-readable status line."""
    if tool_name == "Read":
        path = tool_input.get("file_path", "")
        return f"Reading {path.split('/')[-1]}" if path else "Reading file"
    elif tool_name == "Edit":
        path = tool_input.get("file_path", "")
        return f"Editing {path.split('/')[-1]}" if path else "Editing file"
    elif tool_name == "Write":
        path = tool_input.get("file_path", "")
        return f"Writing {path.split('/')[-1]}" if path else "Writing file"
    elif tool_name == "Grep":
        return f"Searching for '{tool_input.get('pattern', '')[:30]}'"
    elif tool_name == "Glob":
        return f"Finding files: {tool_input.get('pattern', '')[:30]}"
    elif tool_name == "Bash":
        return f"Running: {tool_input.get('command', '')[:40]}"
    elif tool_name == "WebSearch":
        return f"Searching web: {tool_input.get('query', '')[:30]}"
    elif tool_name == "WebFetch":
        return f"Fetching: {tool_input.get('url', '')[:40]}"
    else:
        return f"Using {tool_name}"


class ClaudeCLIThinkProvider(NativelyAgenticProvider):
    """Think via Claude Code CLI subprocess — runs its own agent loop internally.

    Tagged `NativelyAgenticProvider`: the agent app delegates the whole turn to
    this provider's `execute_stream()` rather than driving a tool-use loop. The
    CLI uses its own built-in tools (Read, Grep, Glob, WebSearch, WebFetch)
    with its own permission model. EmptyOS-level tool_consent does not apply.
    """

    name = "claude-cli"
    capacity = 1  # only one concurrent call

    @property
    def native_tool_summary(self) -> str:
        base = "Claude's built-in Read/Grep/Glob/WebSearch/WebFetch"
        if self.mcp_enabled:
            return base + " + EmptyOS MCP (Bash/Write/Edit/CallApp/VaultQuery/TaskList/Fetch)"
        return base

    # Tools Claude is allowed to use (built-in)
    ALLOWED_TOOLS = "Read,Grep,Glob,WebSearch,WebFetch"

    # MCP tools added when bridge is enabled (namespaced by MCP server name)
    MCP_SERVER_NAME = "emptyos"
    MCP_BRIDGE_TOOLS = ["Bash", "Write", "Edit", "CallApp", "VaultQuery", "TaskList", "Fetch"]

    # The CLI runs locally but delegates inference to Anthropic's API, so
    # from a data-egress standpoint this is a cloud provider.
    @property
    def is_cloud(self) -> bool:
        return True

    def __init__(
        self,
        model: str = "",
        max_tokens: int = 4096,
        timeout: int = 0,
        cwd: str = "",
        effort: str = "low",
        mcp_enabled: bool = False,
        mcp_port: int = 9000,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout or 300  # per-line idle timeout (5 min)
        self.max_timeout = 900  # absolute max (15 min)
        self.cwd = cwd or ""  # working directory (vault path)
        self.effort = effort  # low, medium, high, max
        self.mcp_enabled = mcp_enabled  # bridge EmptyOS tools via MCP
        self.mcp_port = mcp_port  # EmptyOS daemon port for MCP proxy
        self._claude_path: str | None = None
        self._mcp_config_path: str | None = None  # path to temp mcp-config.json

    async def available(self) -> bool:
        if self._claude_path is None:
            self._claude_path = shutil.which("claude") or ""
        return bool(self._claude_path)

    async def health(self) -> dict:
        if self._claude_path is None:
            self._claude_path = shutil.which("claude") or ""
        if not self._claude_path:
            return {
                "available": False,
                "reason": "`claude` CLI not on PATH",
                "recovery": {
                    "kind": "service",
                    "id": "claude-cli",
                    "url": "",
                    "hint": "Install Claude Code CLI (https://claude.com/claude-code) and ensure `claude` is on PATH",
                },
            }
        return {"available": True, "reason": None, "recovery": None}

    def _mcp_args(self) -> list[str]:
        """Return extra CLI args for the MCP bridge, or [] if disabled/unavailable.

        Writes a temp mcp-config.json pointing to emptyos.mcp_server (stdio).
        The config is cached per provider instance; a new file is written on
        first call or if the port changed.
        """
        if not self.mcp_enabled:
            return []
        if self._mcp_config_path is None:
            config = {
                "mcpServers": {
                    self.MCP_SERVER_NAME: {
                        "command": sys.executable,
                        "args": ["-m", "emptyos.mcp_server"],
                        "env": {"EMPTYOS_PORT": str(self.mcp_port)},
                    }
                }
            }
            try:
                fd, path = tempfile.mkstemp(prefix="eos-mcp-", suffix=".json")
                with os.fdopen(fd, "w") as f:
                    json.dump(config, f)
                self._mcp_config_path = path
            except Exception:
                return []  # silently skip MCP on config write failure

        mcp_tool_names = ",".join(
            f"mcp__{self.MCP_SERVER_NAME}__{t}" for t in self.MCP_BRIDGE_TOOLS
        )
        allowed = f"{self.ALLOWED_TOOLS},{mcp_tool_names}"
        return ["--mcp-config", self._mcp_config_path, "--allowedTools", allowed]

    async def execute(
        self, *, prompt: str = "", system: str = "", messages: list[dict] | None = None, **kwargs
    ) -> str:
        if not await self.available():
            raise RuntimeError("claude CLI not found in PATH")

        effective_prompt = _messages_to_prompt(messages, prompt) if messages else prompt

        await _claude_sem.acquire()
        try:
            mcp_extra = self._mcp_args()
            allowed = (
                mcp_extra[mcp_extra.index("--allowedTools") + 1]
                if "--allowedTools" in mcp_extra
                else self.ALLOWED_TOOLS
            )
            base_cmd = [
                self._claude_path,
                "-p",
                "--output-format",
                "text",
                "--no-session-persistence",
                "--dangerously-skip-permissions",
                "--allowedTools",
                allowed,
                "--effort",
                kwargs.get("effort", self.effort),
            ]
            if mcp_extra and "--mcp-config" in mcp_extra:
                base_cmd.extend(["--mcp-config", mcp_extra[mcp_extra.index("--mcp-config") + 1]])
            if self.model:
                base_cmd.extend(["--model", self.model])

            other_bytes = sum(len(s.encode("utf-8", errors="replace")) + 1 for s in base_cmd)
            use_stdin, eff_prompt, eff_system = _plan_prompt_channel(
                effective_prompt, system, other_bytes
            )

            cmd = list(base_cmd)
            if not use_stdin:
                cmd.append(eff_prompt)
            if eff_system:
                cmd.extend(["--append-system-prompt", eff_system])

            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if use_stdin else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.cwd or None,
            )
            stdin_data = eff_prompt.encode("utf-8") if use_stdin else None
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data), timeout=self.timeout
            )

            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                raise RuntimeError(
                    f"claude CLI failed (exit {proc.returncode}): {(err or out)[:200]}"
                )

            # Usage-limit response on exit 0 — treat as failure so the capability
            # chain falls through to the next provider (openai, ollama, …).
            if _looks_like_limit_error(out) and len(out) < 400:
                raise RuntimeError(f"claude CLI usage limit: {out[:200]}")

            return out
        finally:
            _claude_sem.release()

    async def execute_stream(
        self, *, prompt: str = "", system: str = "", messages: list[dict] | None = None, **kwargs
    ):
        """Stream from Claude CLI — text mode for reliability, with tool events.

        Yields:
          {"text": str, "done": bool} — text content chunks
          {"tool_status": str, "tool": str} — tool-use status events (stream-json only)
        """
        if not await self.available():
            raise RuntimeError("claude CLI not found in PATH")

        effective_prompt = _messages_to_prompt(messages, prompt) if messages else prompt

        await _claude_sem.acquire()

        proc = None
        try:
            use_stream_json = kwargs.get("stream_json", False)
            fmt = "stream-json" if use_stream_json else "text"

            mcp_extra = self._mcp_args()
            allowed = (
                mcp_extra[mcp_extra.index("--allowedTools") + 1]
                if "--allowedTools" in mcp_extra
                else self.ALLOWED_TOOLS
            )
            base_cmd = [
                self._claude_path,
                "-p",
                "--output-format",
                fmt,
                "--no-session-persistence",
                "--dangerously-skip-permissions",
                "--allowedTools",
                allowed,
                "--effort",
                kwargs.get("effort", self.effort),
            ]
            if mcp_extra and "--mcp-config" in mcp_extra:
                base_cmd.extend(["--mcp-config", mcp_extra[mcp_extra.index("--mcp-config") + 1]])
            # stream-json requires --verbose to actually emit events; without
            # it, the CLI silently degrades to a single terminal result marker.
            if use_stream_json:
                base_cmd.append("--verbose")
            if self.model:
                base_cmd.extend(["--model", self.model])

            other_bytes = sum(len(s.encode("utf-8", errors="replace")) + 1 for s in base_cmd)
            use_stdin, eff_prompt, eff_system = _plan_prompt_channel(
                effective_prompt, system, other_bytes
            )

            cmd = list(base_cmd)
            if not use_stdin:
                cmd.append(eff_prompt)
            if eff_system:
                cmd.extend(["--append-system-prompt", eff_system])

            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if use_stdin else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.cwd or None,
                limit=1024 * 1024,
            )

            if use_stdin and proc.stdin is not None:
                try:
                    proc.stdin.write(eff_prompt.encode("utf-8"))
                    await proc.stdin.drain()
                    proc.stdin.close()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            start_time = time.time()

            if use_stream_json:
                # stream-json mode: parse JSON events for tool status
                final_result = ""
                text_yielded = False
                while True:
                    try:
                        raw_line = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=self.timeout
                        )
                    except TimeoutError:
                        proc.kill()
                        yield {"text": f"\n[Timeout] Claude idle {self.timeout}s", "done": True}
                        return

                    if not raw_line:
                        break
                    if time.time() - start_time > self.max_timeout:
                        proc.kill()
                        yield {"text": f"\n[Timeout] {self.max_timeout}s max", "done": True}
                        return

                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        etype = event.get("type")
                        if etype == "assistant":
                            for block in event.get("message", {}).get("content", []):
                                if block.get("type") == "tool_use":
                                    yield {
                                        "tool_status": _format_tool_status(
                                            block.get("name", ""), block.get("input", {})
                                        ),
                                        "tool": block.get("name", ""),
                                        "done": False,
                                    }
                                elif block.get("type") == "text" and block.get("text"):
                                    yield {"text": block["text"], "done": False}
                                    text_yielded = True
                        elif etype == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield {"text": delta["text"], "done": False}
                                text_yielded = True
                        elif etype == "result":
                            final_result = event.get("result", "")
                            # claude-cli's `result` event carries authoritative
                            # billing data (total_cost_usd + usage breakdown).
                            # Surface it as a usage chunk so the agent loop can
                            # forward it to the UI footer. Without this, the
                            # CLI footer shows no cost for native-mode turns.
                            result_usage = event.get("usage") or {}
                            total_cost = event.get("total_cost_usd")
                            if result_usage or total_cost is not None:
                                merged_usage = dict(result_usage)
                                if total_cost is not None:
                                    merged_usage["cost"] = total_cost
                                yield {"usage": merged_usage, "done": False}
                    except (json.JSONDecodeError, KeyError):
                        continue
                await proc.wait()
                # Check for usage-limit or non-zero exit before completing the stream
                if proc.returncode != 0 and not text_yielded:
                    stderr_data = await proc.stderr.read() if proc.stderr else b""
                    err = stderr_data.decode("utf-8", errors="replace").strip()
                    raise RuntimeError(
                        f"claude CLI failed (exit {proc.returncode}): {(err or final_result)[:200]}"
                    )
                if _looks_like_limit_error(final_result) and not text_yielded:
                    raise RuntimeError(f"claude CLI usage limit: {final_result[:200]}")
                if final_result and not text_yielded:
                    yield {"text": final_result, "done": False}
            else:
                # text mode: buffer the first output so we can detect a usage-limit
                # response (short message, maybe exit 0) BEFORE yielding it as the
                # answer. Once past the detection window, stream line-by-line.
                GUARD_BYTES = 256
                GUARD_TIMEOUT = 8.0  # seconds — if claude is thinking it'll exceed this
                buffer = ""
                guard_start = time.time()

                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            proc.stdout.read(4096), timeout=GUARD_TIMEOUT
                        )
                    except TimeoutError:
                        # Real inference — drop guard, emit buffer, stream rest
                        break
                    if not chunk:
                        # Stream closed before we had enough to decide
                        break
                    buffer += chunk.decode("utf-8", errors="replace")
                    # If buffer is substantial, claude is actually answering — flush and move on
                    if len(buffer) >= GUARD_BYTES:
                        break
                    if time.time() - guard_start > GUARD_TIMEOUT:
                        break

                # At end-of-stream (short response) check for limit marker before yielding
                if proc.returncode is None:
                    # Might have just exited — let it settle
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=0.5)
                    except TimeoutError:
                        pass

                if _looks_like_limit_error(buffer):
                    # Don't yield — let the capability fall back
                    raise RuntimeError(f"claude CLI usage limit: {buffer.strip()[:200]}")
                if proc.returncode is not None and proc.returncode != 0 and not buffer.strip():
                    stderr_data = await proc.stderr.read() if proc.stderr else b""
                    err = stderr_data.decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {err[:200]}")

                # Emit buffer as first output
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    yield {"text": line + "\n", "done": False}
                if buffer:
                    yield {"text": buffer, "done": False}
                    buffer = ""

                # Continue streaming remaining output (if proc still running)
                if proc.returncode is None:
                    async for chunk in proc.stdout:
                        if time.time() - start_time > self.max_timeout:
                            proc.kill()
                            yield {"text": f"\n[Timeout] {self.max_timeout}s max", "done": True}
                            return
                        text = chunk.decode("utf-8", errors="replace")
                        buffer += text
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            yield {"text": line + "\n", "done": False}
                    if buffer:
                        yield {"text": buffer, "done": False}
                    await proc.wait()

                # Final returncode check — if claude exited non-zero after streaming
                # some content, we can't unsay it, but we can log for debugging.
                # Don't raise here (consumer already received content).

            yield {"text": "", "done": True}

        finally:
            # Always release lock + kill process if still running
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            _claude_sem.release()
