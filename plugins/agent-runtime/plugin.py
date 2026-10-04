"""Agent Runtime — ephemeral CLI subprocess driver.

Lifted verbatim from apps/dogfood-agent/app.py:_run_subprocess. Two consumers
today: dogfood-agent (single-turn personas) and the rooms app (per-@mention
CLI participants). Each `run` call spawns one subprocess, drains stdout/stderr
with line callbacks, optionally ticks every N seconds while alive, kills on
timeout. Returns when the subprocess exits.

There is no persistent daemon here — `agent-runtime` is a thin coordination
layer. The user's main EmptyOS daemon stays the only long-lived process.

Design notes:
- `run(cmd, ...)` is the generic driver. CLI-agnostic.
- `claude_cli_run(...)` is a convenience wrapper for the Claude Code CLI shape
  (the only CLI both dogfood and rooms use today). Other CLIs (codex, gemini)
  land as sibling wrappers when their second consumer materializes.
- All file IO is opt-in: pass `stdout_path` to drain to a file *and* line
  callbacks fire too. Rooms uses callbacks (stream into thread JSONL); dogfood
  uses files (run dir is the source of truth for replays).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

from emptyos.sdk import BasePlugin

# Tools dogfood opens to claude-cli. Kept here as the default; callers can
# override per call. Same set as apps/dogfood-agent/app.py before the lift.
DEFAULT_CLAUDE_ALLOWED_TOOLS = "WebFetch,Bash,Read,Write,Edit"


def claude_is_result_line(line: bytes) -> bool:
    """claude-cli emits one JSON object per line in stream-json mode; the
    terminal one starts with `{"type":"result"`. Anchor to the line start so
    a `tool_result` event whose body quotes the same string can't trigger an
    early kill. Exposed as a module-level constant predicate so detached
    callers (`await_subprocess`) can pass it as `early_exit_on_line`."""
    return line.lstrip().startswith(b'{"type":"result"')


def claude_is_progress_line(line: bytes) -> bool:
    """Idle-watchdog progress signal for claude-cli. Assistant-text +
    heartbeat tokens are dropped; only actual tool activity counts. Substring
    match is robust to the nested `content[]` shape — tool_use lives inside
    an assistant message, tool_result inside a user message."""
    return b'"type":"tool_use"' in line or b'"type":"tool_result"' in line


def claude_stream_succeeded(stream_path: "Path | str") -> bool:
    """True iff the on-disk stream-json contains a terminal
    ``{"type":"result", ..., "subtype":"success"}`` line. Used to reconstruct
    a run's outcome when the in-process Popen handle is unavailable (returncode
    is None) — either after a daemon-restart reattach or in race-corner
    fallbacks. Returns False on any read error; the failure mode the caller
    wants is "no success sentinel found" either way."""
    try:
        from pathlib import Path as _Path
        p = _Path(stream_path)
        if not p.exists():
            return False
        for line in reversed(p.read_text(encoding="utf-8", errors="replace").splitlines()):
            if line.lstrip().startswith('{"type":"result"'):
                return '"subtype":"success"' in line
        return False
    except Exception:
        return False

# Generous default — long claude turns can run several minutes. Caller-supplied
# `timeout_s` always wins.
DEFAULT_TIMEOUT_S = 1800.0

# ── Sub-agent depth guard (OpenClaw borrow, 2026-06-11) ─────────────────────
# Runaway-recursion insurance: every spawn stamps EOS_AGENT_DEPTH=<depth+1>
# into the child's env; a spawn attempted at depth >= max_agent_depth is
# refused before exec. The user's daemon runs at depth 0 (env absent); a CLI
# it spawns is depth 1; anything that CLI spawns through a local-kernel
# agent-runtime is depth 2; and so on. The guard only sees env inheritance —
# a child calling back into the daemon over HTTP starts a fresh chain (the
# daemon is depth 0), so this caps subprocess recursion, not HTTP loops.
# Config: [plugins.agent-runtime] max_agent_depth (0 disables; default 3).
AGENT_DEPTH_ENV = "EOS_AGENT_DEPTH"
DEFAULT_MAX_AGENT_DEPTH = 3


def agent_depth_guard(base_env: dict, max_depth: int) -> str | None:
    """Check the current process's agent depth (from os.environ) against
    *max_depth*; stamp the child's depth into *base_env* in place. Returns an
    error string when the spawn must be refused, else None. Pure apart from
    the os.environ read — unit-testable via monkeypatch.setenv."""
    try:
        depth = max(0, int(os.environ.get(AGENT_DEPTH_ENV, "0") or "0"))
    except (TypeError, ValueError):
        depth = 0
    if max_depth > 0 and depth >= max_depth:
        return (
            f"agent depth limit: this process is already {depth} agent "
            f"level(s) deep (max_agent_depth={max_depth}); refusing to "
            "spawn another agent subprocess"
        )
    base_env[AGENT_DEPTH_ENV] = str(depth + 1)
    return None

# Built-in adapters for text-only CLIs (no streaming, no tool events). Each
# entry is a starting template; users override via
# [plugins.agent-runtime.clis.<id>] in emptyos.toml when their installed
# version uses different flags.
#
# `args` is a Python str.format template fed {prompt} and {system}. When the
# template lacks {system}, the system prompt is folded into the user prompt
# instead (best a non-claude CLI can do without --append-system-prompt).
#
# `binary` defaults to the cli_id itself if absent. `env_drop` follows the
# same pattern as claude-cli (CLAUDECODE) — usually empty for other CLIs.
DEFAULT_CLI_ADAPTERS: dict[str, dict] = {
    "codex": {
        "binary": "codex",
        # Codex (OpenAI) — `codex exec "<prompt>"` runs a one-shot.
        # Flag set is best-effort; user overrides via config.
        #
        # `-s read-only` mirrors the read-only posture the claude-cli path gets
        # from `--allowedTools Read,Grep,Glob,WebFetch`: a room participant
        # proposes via [DO:] tokens and never writes on its own
        # (.claude/rules/room-review-gate.md). Without it, codex inherits
        # `sandbox_mode` from ~/.codex/config.toml, which defaults to
        # workspace-write.
        #
        # CAVEAT — measured 2026-07-31, codex-cli 0.144.1 on Windows: this flag
        # is reported but NOT enforced. `codex exec -s read-only` logs
        # "sandbox: read-only" and then happily runs a PowerShell Set-Content
        # that creates the file. Codex sandboxing leans on OS primitives
        # (Seatbelt / Landlock+seccomp) that Windows lacks, so the flag is real
        # on macOS/Linux and cosmetic here. Keep it — CLAUDE.md rule 20 means
        # this code also runs where it bites — but do NOT treat it as the
        # containment boundary on Windows. There, the operative control is the
        # participant's `cwd`, which _dispatch_cli_turn defaults to the vault.
        # `--json` and `stream_json` are a matched pair: the flag makes stdout
        # line-delimited events, and the flag below tells consumers to parse
        # them rather than show the raw JSONL as the reply text. Override one
        # without the other and codex either loses its tool cards or prints
        # machine output at the user.
        # The prompt goes on stdin, not argv: `codex` resolves to codex.CMD on
        # Windows and cmd.exe cuts a multi-line argument at its first newline
        # (see the prompt_via_stdin note in text_cli_run). `-` tells codex to
        # read instructions from stdin, which is lossless on every platform.
        "args_template": ["exec", "--json", "-s", "read-only", "-"],
        "prompt_via_stdin": True,
        "stream_json": True,
        "supports_system": False,
        # Declared only where the unsandboxed write was actually measured, so
        # the guard stays narrow (audits.md). Consumers read this via
        # `cli_adapter_info()` to decide whether an implicit vault cwd is safe.
        "writes_unsandboxed": sys.platform == "win32",
    },
    "gemini": {
        "binary": "gemini",
        # Gemini CLI — `gemini -p "<prompt>"` is the common shape.
        "args_template": ["-p", "{prompt}"],
        "supports_system": False,
    },
    "aider": {
        "binary": "aider",
        # Aider — `aider --message "..." --no-auto-commits` runs one turn
        # without making a commit. Aider is repo-aware; cwd should be a
        # git repo for it to be useful (configure per-participant via cwd).
        "args_template": ["--message", "{prompt}", "--no-auto-commits", "--yes-always"],
        "supports_system": False,
    },
    "sgpt": {
        "binary": "sgpt",
        # shell-gpt — `sgpt "<prompt>"` is the one-shot shape. First
        # positional arg is the prompt; result on stdout.
        "args_template": ["{prompt}"],
        "supports_system": False,
    },
    "mods": {
        "binary": "mods",
        # Charm `mods` — `mods "<prompt>"` prints to stdout. Pipe-friendly
        # by design; works fine as a one-shot too.
        "args_template": ["{prompt}"],
        "supports_system": False,
    },
    "aichat": {
        "binary": "aichat",
        # aichat — `aichat "<prompt>"` runs a one-shot. `-e` would exec
        # the result; we don't want that here.
        "args_template": ["{prompt}"],
        "supports_system": False,
    },
    "goose": {
        "binary": "goose",
        # Block's `goose` — `goose run -t "<prompt>"` runs once with the
        # given text. Older versions used `--text`; -t works on current.
        "args_template": ["run", "-t", "{prompt}"],
        "supports_system": False,
    },
    "opencode": {
        "binary": "opencode",
        # sst's `opencode` — `opencode run "<prompt>"` is the documented
        # one-shot shape.
        "args_template": ["run", "{prompt}"],
        "supports_system": False,
    },
    "pi": {
        "binary": "pi",
        # Earendil Works' Pi Coding Agent — `pi -p` runs a one-shot in print
        # mode, reading the prompt from STDIN.
        #
        # It used to pass `-p {prompt} --append-system-prompt {system}`, and
        # both slots were truncated: pi installs as a .cmd shim, so it runs
        # through cmd.exe where a newline ends the command, and room prompts
        # are multi-line. Measured 2026-07-31 against qwen3.5-32k — an
        # instruction on line 2 was never seen ("Ignored. What would you like
        # me to do next?"); the same prompt on stdin came back answered.
        #
        # Note `--append-system-prompt` was NOT an escape from this: it takes
        # the ~1100-char room system prompt, which is multi-line too. Hence
        # supports_system=False — text_cli_run then folds it into a
        # "[System] … [User] …" preamble so both halves ride the pipe intact.
        "args_template": ["-p"],
        "prompt_via_stdin": True,
        "supports_system": False,
        # No default model label — Pi auto-picks based on whichever
        # provider key is in env (google if GEMINI_API_KEY, openai if
        # OPENAI_API_KEY, etc.). The DEFAULT_CLI_ADAPTERS entry can't
        # know which, so leave the chip empty until the user pins a
        # model in emptyos.toml.
        "model": "",
    },
}


class AgentRuntimePlugin(BasePlugin):
    name = "agent-runtime"

    # Re-exported as class attributes so callers can grab them off the
    # service instance without importing this module (the plugin's module
    # name has a hyphen, so `from plugins.agent-runtime.plugin import ...`
    # isn't a valid import).
    CLAUDE_RESULT_SENTINEL = staticmethod(claude_is_result_line)
    CLAUDE_PROGRESS_PREDICATE = staticmethod(claude_is_progress_line)
    claude_stream_succeeded = staticmethod(claude_stream_succeeded)

    async def connect(self):
        """No external service to connect to — subprocess.create_subprocess_exec
        is in stdlib. We just record availability for /system."""
        self._available = True

    async def available(self) -> bool:
        return self._available

    # ── Generic driver ──────────────────────────────────────────────────────

    async def run(
        self,
        cmd: list[str],
        *,
        cwd: str | Path,
        stdin_data: bytes | None = None,
        env: dict[str, str] | None = None,
        env_drop: list[str] | None = None,
        timeout_s: float | None = None,
        idle_timeout_s: float | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
        on_stdout_line: Callable[[bytes], None] | None = None,
        on_stderr_line: Callable[[bytes], None] | None = None,
        on_tick: Callable[[], Awaitable[None]] | None = None,
        tick_interval_s: float = 30.0,
        early_exit_on_line: Callable[[bytes], bool] | None = None,
        early_exit_grace_s: float = 30.0,
        progress_predicate: Callable[[bytes], bool] | None = None,
    ) -> dict:
        """Run *cmd* as a subprocess. Drain stdout/stderr concurrently.

        Returns ``{"returncode": int, "timeout": bool,
                  "idle_timeout": bool, "duration_s": float}``.

        On timeout, the process is killed and ``timeout`` is True. Drain tasks
        are cancelled before return so file handles release cleanly.

        ``idle_timeout_s`` kills the subprocess when no stdout activity
        arrives for that many seconds. By default "activity" = any stdout
        line, which is too generous for chatty CLIs (claude-cli streams
        assistant-text tokens and heartbeats even when wedged in an internal
        retry / plan-mode stall, so the run burns its full wall-clock budget
        producing no actual progress). Pass ``progress_predicate`` to redefine
        activity as "stdout lines matching a semantic-progress signal" —
        only those reset the idle timer; non-matching lines are still drained
        normally. Set ``idle_timeout_s`` to ``None`` to disable entirely.

        Both file paths and line callbacks may be passed simultaneously — the
        line is appended to the file *and* dispatched to the callback.

        If ``early_exit_on_line`` is provided, every stdout line is tested
        against it; the first match starts a ``early_exit_grace_s`` countdown
        after which the process is killed. Use this when a CLI emits a
        terminal sentinel (e.g. claude-cli's stream-json ``"type":"result"``)
        but doesn't always exit cleanly afterwards.
        """
        # Default env: parent env minus configured drops (CLAUDECODE in
        # dogfood's case — the inner claude-cli must not see the parent's
        # CLAUDECODE marker or it confuses session detection). Always copy —
        # the env_drop pops and the depth stamp below must not leak back
        # into a caller-supplied dict.
        base_env = dict(env) if env is not None else dict(os.environ)
        for k in (env_drop or []):
            base_env.pop(k, None)

        refusal = agent_depth_guard(base_env, self._max_agent_depth())
        if refusal:
            return {"returncode": -1, "timeout": False, "idle_timeout": False,
                    "duration_s": 0.0, "error": refusal}

        timeout = timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S

        started = time.time()
        last_stdout_at = started
        last_progress_at = started
        proc: asyncio.subprocess.Process | None = None
        timed_out = False
        idle_timed_out = False
        early_exit_event = asyncio.Event()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=(asyncio.subprocess.PIPE if stdin_data is not None
                       else asyncio.subprocess.DEVNULL),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env=base_env,
                limit=1024 * 1024,
            )
            assert proc.stdout is not None and proc.stderr is not None
            if stdin_data is not None and proc.stdin is not None:
                # Close stdin after writing. Several CLIs (codex among them)
                # read the prompt until EOF, so leaving the pipe open hangs
                # the turn until the timeout kills it.
                try:
                    proc.stdin.write(stdin_data)
                    await proc.stdin.drain()
                except Exception:
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass

            async def _drain(stream, path: Path | None, cb, is_stdout: bool = False):
                """Append every line to *path* (if set) and dispatch to *cb*
                (if set). One line at a time so partial-tick parsing sees
                fresh state mid-run."""
                nonlocal last_stdout_at, last_progress_at
                handle = None
                if path is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    handle = path.open("ab")
                try:
                    async for line in stream:
                        if is_stdout:
                            now = time.time()
                            last_stdout_at = now
                            if progress_predicate is not None:
                                try:
                                    if progress_predicate(line):
                                        last_progress_at = now
                                except Exception:
                                    # Predicate errors must not break the drain.
                                    pass
                        if handle is not None:
                            handle.write(line)
                            handle.flush()
                        if cb is not None:
                            try:
                                cb(line)
                            except Exception:
                                # Callback errors must not break the drain.
                                pass
                        if (
                            is_stdout
                            and early_exit_on_line is not None
                            and not early_exit_event.is_set()
                        ):
                            try:
                                if early_exit_on_line(line):
                                    early_exit_event.set()
                            except Exception:
                                pass
                finally:
                    if handle is not None:
                        try:
                            handle.close()
                        except Exception:
                            pass

            async def _tick():
                """Best-effort periodic callback while subprocess is alive.
                Ticks must never crash the run — exceptions are swallowed."""
                if on_tick is None:
                    return
                try:
                    while True:
                        await asyncio.sleep(tick_interval_s)
                        if proc.returncode is not None:
                            return
                        try:
                            await on_tick()
                        except Exception:
                            pass
                except asyncio.CancelledError:
                    raise

            idle_event = asyncio.Event()

            async def _idle_watchdog():
                """Sets `idle_event` when no progress signal has arrived for
                `idle_timeout_s`. Caller treats event-set as a kill signal.
                Polls every 10s — fine-grained enough to detect within ~10s
                of the deadline, cheap enough to not matter.

                "Progress" = lines matched by `progress_predicate` when set,
                else any stdout line. The predicate path is what catches
                claude-cli wedges where assistant-text/heartbeat tokens keep
                flowing but no tool_use/tool_result events land — stdout-only
                idle detection never trips in that case."""
                if idle_timeout_s is None:
                    return
                try:
                    while True:
                        await asyncio.sleep(10)
                        if proc.returncode is not None:
                            return
                        anchor = (
                            last_progress_at if progress_predicate is not None
                            else last_stdout_at
                        )
                        silence = time.time() - anchor
                        if silence >= idle_timeout_s:
                            idle_event.set()
                            return
                except asyncio.CancelledError:
                    raise

            tasks = [
                asyncio.create_task(_drain(proc.stdout, stdout_path, on_stdout_line, is_stdout=True)),
                asyncio.create_task(_drain(proc.stderr, stderr_path, on_stderr_line)),
                asyncio.create_task(_tick()),
                asyncio.create_task(_idle_watchdog()),
            ]

            async def _await_exit():
                """Wait for the subprocess, racing against early-exit + idle
                triggers. First of: subprocess exits / early-exit sentinel +
                grace / idle ceiling → kill. Without idle racing, a wedged
                claude-cli that stops emitting tool calls burns the full
                wall-clock budget producing nothing.
                """
                nonlocal idle_timed_out
                wait_task = asyncio.create_task(proc.wait())
                race_tasks: set[asyncio.Task] = {wait_task}
                early_task: asyncio.Task | None = None
                idle_task: asyncio.Task | None = None
                if early_exit_on_line is not None:
                    early_task = asyncio.create_task(early_exit_event.wait())
                    race_tasks.add(early_task)
                if idle_timeout_s is not None:
                    idle_task = asyncio.create_task(idle_event.wait())
                    race_tasks.add(idle_task)
                done, _pending = await asyncio.wait(
                    race_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for t in (early_task, idle_task):
                    if t is not None and t not in done:
                        t.cancel()
                if wait_task in done:
                    return
                if idle_task is not None and idle_task in done:
                    # Idle ceiling hit — kill immediately, no grace.
                    idle_timed_out = True
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        pass
                    return
                # Sentinel seen; grant the grace window then kill if needed.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=early_exit_grace_s)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        pass

            try:
                await asyncio.wait_for(_await_exit(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                # Bounded wait — on Windows, claude-cli spawns grandchildren
                # (Node + tool subprocs) that can inherit the stdout pipe.
                # After TerminateProcess on the parent, `proc.wait()` may
                # hang waiting on the pipe even though the parent's already
                # gone. 10s is generous; past that we abandon and let the
                # OS reap. Without this ceiling, claude_cli_run can hang
                # forever past timeout and meta.status never flips out of
                # "running".
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
                timed_out = True
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await asyncio.wait_for(t, timeout=5)
                except (Exception, asyncio.CancelledError):
                    # CancelledError doesn't inherit from Exception in 3.8+,
                    # so it would otherwise leak out and look like a test/run
                    # failure even though the cancel is the intended cleanup.
                    pass

            rc = proc.returncode if proc.returncode is not None else -1
            return {
                "returncode": rc,
                "timeout": timed_out,
                "idle_timeout": idle_timed_out,
                "duration_s": round(time.time() - started, 2),
            }
        except Exception:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            raise

    # ── Detached spawn + file-tail awaiter ──────────────────────────────────
    #
    # The `run()` driver above holds the subprocess via PIPE handles, so the
    # child dies when the parent process is killed (e.g. user runs restart.bat
    # mid-claude-cli). For long-running claude-cli runs that must survive
    # parent death, use `spawn_detached(cmd, ...)` instead — child is spawned
    # with OS-level detach flags and writes stdout/stderr straight to disk;
    # parent only tracks pid + start time. `await_subprocess(pid, ...)` then
    # polls the child via psutil + tails the stdout file to enforce timeout /
    # idle / early-exit semantics. Both halves can be invoked from different
    # daemon lifetimes — the awaiter on a fresh boot re-attaches to a child
    # spawned by a prior boot using create_time to detect PID reuse.

    def _supervision_dir(self) -> Path | None:
        """Resolve the persistent supervision directory:
        ``data/agent-runtime/supervised/``. Returns ``None`` when no kernel
        is wired (tests / pre-boot); set ``_state_dir_override`` on the
        instance to point elsewhere for fixtures."""
        override = getattr(self, "_state_dir_override", None)
        if override is not None:
            return Path(override)
        try:
            return self.kernel.config.data_dir / "agent-runtime" / "supervised"
        except Exception:
            return None

    def _supervision_file(self, key: str) -> Path | None:
        d = self._supervision_dir()
        if d is None:
            return None
        return d / f"{self._safe_key(key)}.json"

    @staticmethod
    def _safe_key(key: str) -> str:
        """Constrain caller-supplied keys to a filesystem-safe shape so we
        never let an external string pick a write target. Reject anything
        that, after normalization, doesn't match the original."""
        cleaned = "".join(c for c in key if c.isalnum() or c in "-_.")
        if not cleaned or cleaned != key:
            raise ValueError(f"invalid supervision key: {key!r}")
        return cleaned

    def list_supervised(self) -> list[dict]:
        """Return every persisted supervision record. Used at boot to
        re-attach to detached children spawned by a prior daemon lifetime."""
        import json
        d = self._supervision_dir()
        if d is None or not d.exists():
            return []
        out: list[dict] = []
        for f in sorted(d.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    def clear_supervision(self, key: str) -> bool:
        """Remove the persisted supervision record for *key*. Called after
        the awaiter finalizes a run. Returns True if a file was deleted."""
        f = self._supervision_file(key)
        if f is None or not f.exists():
            return False
        try:
            f.unlink()
            return True
        except OSError:
            return False

    def terminate(self, key: str, *, grace_s: float = 5.0) -> dict:
        """Kill the supervised child this plugin OWNS, then clear its record.

        This is the cancel primitive behind the run-center "swarm cancel" UX.
        It only ever touches a PID present in *this plugin's own* supervision
        store (``data/agent-runtime/supervised/<key>.json``), and only after
        the live PID's ``create_time`` matches the recorded one — the same
        PID-reuse guard ``await_subprocess`` uses. So it can never hit the
        user's main daemon (`:9000`) or the dogfood sidecar (`:9001`): those
        are not supervised children of agent-runtime. This is the sanctioned
        owned-PID exception to the hands-off rule in
        ``.claude/rules/daemon-handling.md`` — the same ownership model
        ``sandbox-pool`` uses to terminate its members.

        Sends terminate() to the PID and its child tree (claude-cli spawns
        grandchildren), waits ``grace_s`` for a clean exit, then kill()s any
        survivor. Returns ``{ok, killed, pid, reason}``.
        """
        import json
        sf = self._supervision_file(key)
        if sf is None or not sf.exists():
            return {"ok": False, "killed": False, "pid": None, "reason": "no_supervision_record"}
        try:
            record = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            return {"ok": False, "killed": False, "pid": None, "reason": "unreadable_record"}

        pid = record.get("pid")
        rec_ct = record.get("create_time")
        if not isinstance(pid, int):
            self.clear_supervision(key)
            return {"ok": True, "killed": False, "pid": pid, "reason": "no_pid"}

        try:
            import psutil
        except Exception:
            # psutil is a hard dep of the spawn/await path; if it's missing we
            # can't apply the create_time guard, so refuse rather than risk
            # killing a reused PID unguarded. Leave the record intact.
            return {"ok": False, "killed": False, "pid": pid, "reason": "psutil_unavailable"}

        killed = False
        reason = "terminated"
        try:
            p = psutil.Process(pid)
            if rec_ct is not None and abs(p.create_time() - rec_ct) > 1.0:
                # The original child is gone; this PID was reused by something
                # unrelated. Never kill it — just drop the stale record.
                self.clear_supervision(key)
                return {"ok": True, "killed": False, "pid": pid, "reason": "pid_reused"}
            procs = p.children(recursive=True)
            procs.append(p)
            for child in procs:
                try:
                    child.terminate()
                except Exception:
                    pass
            _gone, alive = psutil.wait_procs(procs, timeout=grace_s)
            for child in alive:
                try:
                    child.kill()
                except Exception:
                    pass
            killed = True
        except psutil.NoSuchProcess:
            reason = "already_gone"
        except Exception as exc:  # noqa: BLE001 — surface the reason, never raise
            reason = f"error:{exc}"

        # Drop the in-process Popen handle if we still hold it (same-lifetime
        # spawn), so a later await_subprocess doesn't trip over a dead handle.
        handle = getattr(self, "_detached_handles", {}).pop(pid, None)
        if handle is not None:
            try:
                handle.wait(timeout=0)
            except Exception:
                pass

        self.clear_supervision(key)
        return {"ok": True, "killed": killed, "pid": pid, "reason": reason}

    def spawn_detached(
        self,
        cmd: list[str],
        *,
        cwd: str | Path,
        stdout_path: Path,
        stderr_path: Path,
        env: dict[str, str] | None = None,
        env_drop: list[str] | None = None,
        supervision_key: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Spawn *cmd* with OS-level process-group detach so the child outlives
        the current process. stdout/stderr are redirected to *stdout_path* /
        *stderr_path* directly (no pipe through the parent). The parent does
        NOT hold the subprocess handle after this call returns.

        Returns ``{"pid": int, "started": float, "create_time": float | None}``,
        or ``{"error": str}`` without spawning when the agent depth limit is
        hit. ``create_time`` (via psutil) lets a later `await_subprocess`
        distinguish the original child from an unrelated process that reused
        the PID after a daemon restart.
        """
        import subprocess as _subprocess
        # Copy — see run(): mutations must not leak into a caller's dict.
        base_env = dict(env) if env is not None else dict(os.environ)
        for k in (env_drop or []):
            base_env.pop(k, None)

        refusal = agent_depth_guard(base_env, self._max_agent_depth())
        if refusal:
            return {"error": refusal}

        stdout_path = Path(stdout_path)
        stderr_path = Path(stderr_path)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)

        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            # CREATE_NEW_PROCESS_GROUP (0x200) + DETACHED_PROCESS (0x8) +
            # CREATE_NO_WINDOW (0x08000000). DETACHED_PROCESS removes the
            # child's console; without it, closing the parent's console
            # (which restart.bat effectively does) propagates a CTRL_BREAK
            # to grouped children that hadn't yet handled it.
            creationflags = (
                _subprocess.CREATE_NEW_PROCESS_GROUP
                | _subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
                | _subprocess.CREATE_NO_WINDOW
            )
        else:
            # POSIX: new session detaches from controlling terminal so a
            # SIGHUP to the parent doesn't propagate to the child.
            start_new_session = True

        # Open file handles for the child to inherit; close them in the
        # parent immediately after Popen returns — the child has dup'd copies.
        out_h = open(stdout_path, "ab")
        err_h = open(stderr_path, "ab")
        try:
            proc = _subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=base_env,
                stdin=_subprocess.DEVNULL,
                stdout=out_h,
                stderr=err_h,
                creationflags=creationflags,
                start_new_session=start_new_session,
                close_fds=True,
            )
        finally:
            try: out_h.close()
            except Exception: pass
            try: err_h.close()
            except Exception: pass

        started = time.time()
        create_time: float | None = None
        try:
            import psutil
            create_time = psutil.Process(proc.pid).create_time()
        except Exception:
            create_time = None

        # Keep the Popen handle alive so `await_subprocess` (in this same
        # process lifetime) can read the returncode. Windows reaps a PID as
        # soon as the last handle closes, so without this `psutil.Process(pid)`
        # would raise NoSuchProcess by the time we polled. After a daemon
        # restart this dict is empty and the awaiter falls back to
        # exited_before_attach semantics — caller reconstructs outcome from
        # the on-disk stdout (e.g. claude-cli's `"type":"result"` line).
        if not hasattr(self, "_detached_handles"):
            self._detached_handles = {}
        self._detached_handles[proc.pid] = proc

        # Persist a supervision record so a fresh daemon lifetime can find
        # this child and re-attach. The record is keyed by caller-supplied
        # identifier (e.g. dogfood run_id) so reattach is by-key, not by-pid
        # (PIDs reuse; keys don't).
        if supervision_key is not None:
            import json
            sf = self._supervision_file(supervision_key)
            if sf is not None:
                sf.parent.mkdir(parents=True, exist_ok=True)
                record = {
                    "key": supervision_key,
                    "pid": proc.pid,
                    "create_time": create_time,
                    "cmd": list(cmd),
                    "cwd": str(cwd),
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "started": started,
                    "metadata": dict(metadata or {}),
                }
                tmp = sf.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
                tmp.replace(sf)

        return {"pid": proc.pid, "started": started, "create_time": create_time}

    async def reattach(
        self,
        key: str,
        *,
        timeout_s: float | None = None,
        idle_timeout_s: float | None = None,
        progress_predicate: Callable[[bytes], bool] | None = None,
        early_exit_on_line: Callable[[bytes], bool] | None = None,
        early_exit_grace_s: float = 30.0,
        on_tick: Callable[[], Awaitable[None]] | None = None,
        tick_interval_s: float = 30.0,
        poll_s: float = 1.0,
        clear_on_finish: bool = True,
    ) -> dict:
        """Look up the supervision record for *key*, then drive
        ``await_subprocess`` against the recorded pid + stdout_path. Returns
        the awaiter's result dict, plus ``"reattached": True`` and the
        original metadata under ``"metadata"`` for the caller's finalizer.

        Raises ``KeyError`` when no record exists. Clears the supervision
        record after the awaiter returns (so a once-finalized run isn't
        re-attached on the next boot)."""
        import json
        sf = self._supervision_file(key)
        if sf is None or not sf.exists():
            raise KeyError(f"no supervision record for {key!r}")
        record = json.loads(sf.read_text(encoding="utf-8"))
        result = await self.await_subprocess(
            int(record["pid"]),
            stdout_path=Path(record["stdout_path"]),
            started=float(record.get("started") or time.time()),
            create_time=record.get("create_time"),
            timeout_s=timeout_s,
            idle_timeout_s=idle_timeout_s,
            progress_predicate=progress_predicate,
            early_exit_on_line=early_exit_on_line,
            early_exit_grace_s=early_exit_grace_s,
            on_tick=on_tick,
            tick_interval_s=tick_interval_s,
            poll_s=poll_s,
        )
        result["reattached"] = True
        result["metadata"] = record.get("metadata") or {}
        if clear_on_finish:
            self.clear_supervision(key)
        return result

    async def await_subprocess(
        self,
        pid: int,
        *,
        stdout_path: Path,
        started: float | None = None,
        create_time: float | None = None,
        timeout_s: float | None = None,
        idle_timeout_s: float | None = None,
        progress_predicate: Callable[[bytes], bool] | None = None,
        early_exit_on_line: Callable[[bytes], bool] | None = None,
        early_exit_grace_s: float = 30.0,
        on_tick: Callable[[], Awaitable[None]] | None = None,
        tick_interval_s: float = 30.0,
        poll_s: float = 1.0,
    ) -> dict:
        """Wait for the detached subprocess at *pid* to exit, enforcing
        timeout / idle / early-exit by tailing *stdout_path*. Returns the
        same dict shape as `run()` plus ``exited_before_attach``:

        ``{"returncode": int | None, "timeout": bool, "idle_timeout": bool,
           "duration_s": float, "exited_before_attach": bool}``.

        ``exited_before_attach`` is True when the pid was already gone when we
        first looked — typically a reattach after a daemon restart that missed
        the exit. In that case the file is read once for context and we return
        with ``returncode: None`` to signal "outcome unknown, caller should
        reconstruct from the stdout tail."

        If *create_time* is supplied and the live PID's create_time doesn't
        match, we treat the original child as gone (PID reuse).
        """
        import psutil
        stdout_path = Path(stdout_path)
        started = started if started is not None else time.time()
        deadline = started + (timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S)
        last_progress_at = started

        try:
            tail_pos = stdout_path.stat().st_size if stdout_path.exists() else 0
        except OSError:
            tail_pos = 0
        early_exit_at: float | None = None

        def _proc_alive() -> tuple[bool, "psutil.Process | None"]:
            try:
                p = psutil.Process(pid)
                if create_time is not None:
                    if abs(p.create_time() - create_time) > 1.0:
                        return False, None
                if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                    return False, p
                return True, p
            except psutil.NoSuchProcess:
                return False, None
            except Exception:
                return False, None

        async def _tail_once() -> None:
            nonlocal tail_pos, last_progress_at, early_exit_at
            try:
                if not stdout_path.exists():
                    return
                size = stdout_path.stat().st_size
                if size <= tail_pos:
                    return
                with open(stdout_path, "rb") as f:
                    f.seek(tail_pos)
                    chunk = f.read(size - tail_pos)
                    tail_pos = size
            except OSError:
                return
            now = time.time()
            for line in chunk.splitlines(keepends=True):
                if progress_predicate is not None:
                    try:
                        if progress_predicate(line):
                            last_progress_at = now
                    except Exception:
                        pass
                else:
                    # No predicate → any byte counts as progress.
                    last_progress_at = now
                if early_exit_on_line is not None and early_exit_at is None:
                    try:
                        if early_exit_on_line(line):
                            early_exit_at = now
                    except Exception:
                        pass

        # Drain anything written between spawn and now, then check liveness.
        await _tail_once()
        alive, p = _proc_alive()
        if not alive:
            return {
                "returncode": None,
                "timeout": False,
                "idle_timeout": False,
                "duration_s": round(time.time() - started, 2),
                "exited_before_attach": True,
            }

        timed_out = False
        idle_timed_out = False
        next_tick_at = time.time() + tick_interval_s

        try:
            while True:
                await asyncio.sleep(poll_s)
                await _tail_once()
                now = time.time()
                alive, p = _proc_alive()
                if not alive:
                    # Final drain — child may have flushed a last burst.
                    await _tail_once()
                    break
                if now >= deadline:
                    timed_out = True
                    break
                if idle_timeout_s is not None:
                    if (now - last_progress_at) >= idle_timeout_s:
                        idle_timed_out = True
                        break
                if early_exit_at is not None and (now - early_exit_at) >= early_exit_grace_s:
                    break
                if on_tick is not None and now >= next_tick_at:
                    try:
                        await on_tick()
                    except Exception:
                        pass
                    next_tick_at = now + tick_interval_s
        finally:
            if timed_out or idle_timed_out or early_exit_at is not None:
                # Best-effort kill — proc may already be gone (no-op then).
                try:
                    target = p if p is not None else psutil.Process(pid)
                    target.terminate()
                    try:
                        target.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        try: target.kill()
                        except Exception: pass
                except Exception:
                    pass

        # Prefer the in-process Popen handle for returncode (Windows reaps
        # PIDs eagerly so psutil-only lookup loses the rc). Falls back to
        # psutil when the handle isn't ours — e.g. reattach across daemon
        # restart goes through `exited_before_attach=True` above, so this
        # path is the "same-process await" case.
        rc: int | None = None
        handle = getattr(self, "_detached_handles", {}).pop(pid, None)
        if handle is not None:
            try:
                handle.poll()
                rc = handle.returncode
                if rc is None:
                    rc = handle.wait(timeout=2)
            except Exception:
                rc = None
        else:
            try:
                p2 = psutil.Process(pid)
                try:
                    rc = p2.wait(timeout=2)
                except psutil.TimeoutExpired:
                    rc = None
            except psutil.NoSuchProcess:
                rc = None
            except Exception:
                rc = None

        return {
            "returncode": rc,
            "timeout": timed_out,
            "idle_timeout": idle_timed_out,
            "duration_s": round(time.time() - started, 2),
            "exited_before_attach": False,
        }

    # ── Generic text-CLI adapter (Phase 23) ──────────────────────────────
    #
    # For CLIs that print their reply to stdout as plain text (codex, gemini,
    # cursor-agent, etc.). Doesn't stream, doesn't parse tool events — just
    # runs the binary, captures stdout, returns the text. Per-CLI config
    # lives in `[plugins.agent-runtime.clis.<cli_id>]` in emptyos.toml.

    def _max_agent_depth(self) -> int:
        """Configured agent-spawn depth cap. An explicit `max_agent_depth = 0`
        disables the guard; an unparseable value falls back to the default so
        a typo'd config can't silently remove it."""
        try:
            return int(self.config("max_agent_depth", DEFAULT_MAX_AGENT_DEPTH))
        except (TypeError, ValueError):
            return DEFAULT_MAX_AGENT_DEPTH

    def _resolve_cli_config(self, cli_id: str) -> dict:
        """Merge built-in defaults with user overrides from emptyos.toml.
        User config wins on every key."""
        defaults = DEFAULT_CLI_ADAPTERS.get(cli_id, {})
        overrides = (self.config("clis", {}) or {}).get(cli_id, {}) or {}
        merged = dict(defaults)
        merged.update(overrides)
        return merged

    def cli_adapter_info(self, cli_id: str) -> dict:
        """Safety-relevant facts about a text-CLI adapter, for callers deciding
        how much to trust it. Public because `_resolve_cli_config` is not.

        ``writes_unsandboxed`` is True when this CLI can write the filesystem
        despite whatever read-only flag its args carry — measured, not assumed
        (see the codex entry in DEFAULT_CLI_ADAPTERS). Absent/False means "no
        measured escape", NOT "proven contained": most adapters have simply
        never been tested. Treat it as a known-bad list, not a safety
        certificate.
        """
        cfg = self._resolve_cli_config(cli_id)
        return {
            "binary": cfg.get("binary") or cli_id,
            "writes_unsandboxed": bool(cfg.get("writes_unsandboxed")),
            "stream_json": bool(cfg.get("stream_json")),
            "supports_system": bool(cfg.get("supports_system")),
            # Display label for the responder chip. The buffered path reads it
            # off text_cli_run's return; the streaming path has no return to
            # read (its result is consumed inside the driver task), so it
            # resolves the same label from here instead.
            "model": cfg.get("model") or "",
        }

    async def text_cli_run(
        self,
        *,
        cli_id: str,
        prompt: str,
        system_prompt: str | None = None,
        cwd: str | Path,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
        on_stdout_line: Callable[[bytes], None] | None = None,
        extra_args: list[str] | None = None,
    ) -> dict:
        """Spawn a non-claude CLI in plain-text mode. Returns
        ``{text, returncode, timeout, duration_s, binary}`` or ``{error}``.

        The CLI's full stdout is captured and returned as a single text
        chunk. If `on_stdout_line` is set, raw bytes are still streamed to it
        so callers can yield progressive text upstream — useful when the
        target CLI prints incrementally.
        """
        cfg = self._resolve_cli_config(cli_id)
        binary_name = cfg.get("binary") or cli_id
        binary = shutil.which(binary_name) or ""
        if not binary:
            return {"error": f"{binary_name} CLI not on PATH"}

        # Build args from the template. If the CLI doesn't support a separate
        # system prompt (most don't), fold it into the user prompt prefix.
        supports_system = bool(cfg.get("supports_system"))
        eff_prompt = prompt
        if system_prompt and not supports_system:
            eff_prompt = f"[System]\n{system_prompt}\n\n[User]\n{prompt}"
        args_template = list(cfg.get("args_template") or ["{prompt}"])
        cmd = [binary]
        for tmpl in args_template:
            try:
                cmd.append(tmpl.format(prompt=eff_prompt, system=system_prompt or ""))
            except (IndexError, KeyError):
                cmd.append(tmpl)
        if extra_args:
            cmd += list(extra_args)

        # Per-CLI env_set overrides (e.g. point an openai-provider CLI at a
        # local Ollama endpoint via OPENAI_BASE_URL). Applied on top of either
        # the caller's env or os.environ.
        env_set = cfg.get("env_set") or {}
        effective_env = env
        if env_set:
            effective_env = dict(env if env is not None else os.environ)
            for k, v in env_set.items():
                effective_env[str(k)] = str(v)

        # Buffer stdout into a bytearray while also forwarding line events
        # (best-effort; not all CLIs print line-buffered).
        buf = bytearray()
        def _capture(line: bytes) -> None:
            buf.extend(line)
            if on_stdout_line is not None:
                try: on_stdout_line(line)
                except Exception: pass

        # Windows: `shutil.which` resolves npm-installed CLIs to a .CMD shim,
        # which runs through cmd.exe — where a newline TERMINATES the command.
        # A multi-line argv element is therefore silently truncated at its
        # first line: codex received a prompt of exactly "[System]" and
        # answered "What would you like me to work on?" (measured 2026-07-31).
        # An adapter whose CLI can take the prompt on stdin declares
        # `prompt_via_stdin` and puts "-" in its args_template instead.
        via_stdin = bool(cfg.get("prompt_via_stdin"))
        try:
            result = await self.run(
                cmd, cwd=cwd, env=effective_env,
                env_drop=cfg.get("env_drop") or [],
                timeout_s=timeout_s,
                on_stdout_line=_capture,
                stdin_data=eff_prompt.encode("utf-8") if via_stdin else None,
            )
        except Exception as e:
            return {"error": f"{cli_id} run failed: {e!s:.200s}"}
        if result.get("error"):
            return {"error": result["error"]}

        text = bytes(buf).decode("utf-8", errors="replace").strip()
        return {
            "text": text,
            "returncode": result.get("returncode", -1),
            "timeout": result.get("timeout", False),
            "duration_s": result.get("duration_s", 0.0),
            "binary": binary,
            # Informational label for UI surfaces. Purely a display string —
            # the actual model is picked by whatever flags ended up in the
            # subprocess command line. Keeping this honest is on the operator.
            "model": cfg.get("model") or "",
        }

    # ── Claude-CLI convenience wrapper ──────────────────────────────────────

    def resolve_claude_binary(self) -> str:
        """Return the absolute path to `claude`, or empty string if missing.
        Config override: `[plugins.agent-runtime] claude_binary = "..."`."""
        configured = self.config("claude_binary", "") or ""
        if configured:
            return configured
        return shutil.which("claude") or ""

    async def claude_cli_run(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        allowed_tools: str = DEFAULT_CLAUDE_ALLOWED_TOOLS,
        cwd: str | Path,
        model: str | None = None,
        effort: str | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
        on_stdout_line: Callable[[bytes], None] | None = None,
        on_stderr_line: Callable[[bytes], None] | None = None,
        on_tick: Callable[[], Awaitable[None]] | None = None,
        tick_interval_s: float = 30.0,
        timeout_s: float | None = None,
        idle_timeout_s: float | None = None,
        extra_args: list[str] | None = None,
        env_set: dict[str, str] | None = None,
    ) -> dict:
        """Spawn `claude -p <prompt> --output-format stream-json …`.

        `model` maps to claude-cli's ``--model`` (e.g. "sonnet" / "haiku" /
        "opus" / a full id like "claude-sonnet-4-6"). When omitted, claude-cli
        picks the user's configured default. `effort` maps to ``--effort``
        ("low" / "medium" / "high" / "max"). Both are passed through verbatim;
        any value invalid for the local claude-cli version surfaces as a
        non-zero exit captured in the return dict.

        `env_set` injects/overrides env vars for the subprocess — e.g. point
        claude-cli at an Anthropic-compatible endpoint (Ollama) via
        ``{"ANTHROPIC_BASE_URL": "http://localhost:11434", "ANTHROPIC_AUTH_TOKEN": "ollama"}``.
        Mirrors `text_cli_run`'s `env_set`; absent, behaviour is unchanged.

        Returns the same dict as `run()` plus ``"binary"`` (the resolved
        claude path) so the caller can record what ran. If the binary isn't
        on PATH, returns ``{"error": "claude CLI not on PATH"}`` without
        spawning.
        """
        binary = self.resolve_claude_binary()
        if not binary:
            return {"error": "claude CLI not on PATH"}
        cmd = self._build_claude_cmd(
            binary=binary, prompt=prompt, system_prompt=system_prompt,
            allowed_tools=allowed_tools, model=model, effort=effort,
            extra_args=extra_args,
        )
        effective_env = None
        if env_set:
            effective_env = dict(os.environ)
            for k, v in env_set.items():
                effective_env[str(k)] = str(v)
        result = await self.run(
            cmd,
            cwd=cwd,
            env=effective_env,
            env_drop=["CLAUDECODE"],
            timeout_s=timeout_s,
            idle_timeout_s=idle_timeout_s,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            on_stdout_line=on_stdout_line,
            on_stderr_line=on_stderr_line,
            on_tick=on_tick,
            tick_interval_s=tick_interval_s,
            early_exit_on_line=claude_is_result_line,
            early_exit_grace_s=30.0,
            progress_predicate=claude_is_progress_line,
        )
        result["binary"] = binary
        return result

    def _build_claude_cmd(
        self,
        *,
        binary: str,
        prompt: str,
        system_prompt: str | None,
        allowed_tools: str,
        model: str | None,
        effort: str | None,
        extra_args: list[str] | None,
    ) -> list[str]:
        """Single source of truth for the claude-cli argv. Shared by
        `claude_cli_run` (piped) and `claude_cli_spawn_detached` (detached)
        so the two paths can't drift on flag set."""
        cmd: list[str] = [
            binary,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            "--allowedTools",
            allowed_tools,
        ]
        if model:
            cmd += ["--model", str(model)]
        if effort:
            cmd += ["--effort", str(effort)]
        if system_prompt is not None:
            cmd += ["--append-system-prompt", system_prompt]
        if extra_args:
            cmd += list(extra_args)
        return cmd

    def claude_cli_spawn_detached(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        allowed_tools: str = DEFAULT_CLAUDE_ALLOWED_TOOLS,
        cwd: str | Path,
        stdout_path: Path,
        stderr_path: Path,
        model: str | None = None,
        effort: str | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        supervision_key: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Detached counterpart to `claude_cli_run` — spawns claude-cli with
        OS-level process-group detach so the child outlives the spawning
        daemon. Caller waits for completion separately via `await_subprocess`
        (or `reattach` after a daemon restart) using
        `progress_predicate=claude_is_progress_line` and
        `early_exit_on_line=claude_is_result_line`.

        Returns the same dict shape as `spawn_detached` plus
        ``"binary"``: the resolved claude path. Returns
        ``{"error": "claude CLI not on PATH"}`` without spawning if missing.
        """
        binary = self.resolve_claude_binary()
        if not binary:
            return {"error": "claude CLI not on PATH"}
        cmd = self._build_claude_cmd(
            binary=binary, prompt=prompt, system_prompt=system_prompt,
            allowed_tools=allowed_tools, model=model, effort=effort,
            extra_args=extra_args,
        )
        spawn = self.spawn_detached(
            cmd,
            cwd=cwd,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            env=env,
            env_drop=["CLAUDECODE"],
            supervision_key=supervision_key,
            metadata=metadata,
        )
        spawn["binary"] = binary
        return spawn
