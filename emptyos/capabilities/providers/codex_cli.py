"""Think via the Codex CLI subprocess (`codex exec --json`).

**Why this exists beside `claude_cli.py`.** Codex authenticates against the
CLI's own signed-in subscription session rather than API credits, so it is a
*second free strong model* — and, unlike the `openai` provider, it keeps working when the API key's
credit balance is exhausted. That is not hypothetical: on 2026-09-01 the OpenAI
balance ran out, `OpenAICompatThinkProvider.execute` raised 429, and
`_think_with_provider` swallowed it (`except Exception: return None`) and fell
through to claude-cli with nothing written to syslog. Two full reviews were
labelled gpt-5.4 and were actually claude-cli. A second subscription-backed
model removes the single point of failure that made that silent.

Three things here are load-bearing and none are obvious:

1. **The prompt goes on stdin, always.** `shutil.which("codex")` resolves to an
   npm `codex.CMD` shim on Windows, which runs through `cmd.exe`, where a
   newline *terminates the command*. A multi-line prompt on argv is silently
   truncated and the CLI answers the first line fluently, exit 0
   (.claude/rules/multi-cli-participants.md). Think prompts are multi-line by
   construction, so there is no argv path at all — not even a short one.

2. **`error` items are not answer text.** Codex reports its own
   skills-context-budget warning as `{"type":"item.completed","item":
   {"type":"error","message":"Skill descriptions were shortened…"}}` on a
   perfectly successful turn. `claude_run_stream.transform_stream_json_obj`
   maps that to a text chunk, which is right for RENDERING a room turn (show
   the user the error) and wrong here — reusing it would prepend that warning
   to every `think()` result. So this module reads the dialect directly and
   keeps `agent_message` and `error` apart. Do not "simplify" this back onto
   the shared parser without re-reading this paragraph.

3. **The system prompt is folded into the user text.** Same argv reason as (1);
   there is no safe flag channel for multi-line text through a `.CMD` shim.

Cost note for anyone choosing between providers: a trivial turn measured 21,603
input tokens (9,984 cached) before reaching the prompt — codex loads skills and
hooks per spawn. Free on a subscription, but it is not a low-latency provider.
"""

from __future__ import annotations

import asyncio
import json
import shutil

from emptyos.capabilities import Provider

# One concurrent call. The spawn is heavy (see the token note above) and the
# CLI holds its own session; parallel turns buy nothing and multiply the
# startup cost. Mirrors claude-cli's `capacity = 1`.
_codex_sem = asyncio.Semaphore(1)


def extract_codex_reply(stdout: str) -> tuple[str, list[str]]:
    """``(answer, errors)`` from a `codex exec --json` stream.

    Pure and stdout-only so it is testable without spawning anything. Returns
    every `agent_message` joined, and `error` items separately — never merged,
    per (2) in the module docstring. Unparseable lines are skipped rather than
    raising: codex interleaves non-JSON warnings on stdout, and one stray line
    must not discard a completed answer.
    """
    parts: list[str] = []
    errors: list[str] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except Exception:
            continue
        if not isinstance(evt, dict) or evt.get("type") != "item.completed":
            continue
        item = evt.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        elif itype == "error":
            msg = str(item.get("message") or "").strip()
            if msg:
                errors.append(msg)
    return "\n".join(parts).strip(), errors


class CodexCLIThinkProvider(Provider):
    """Think via the Codex CLI. Text in, text out — no tool loop."""

    name = "codex"
    capacity = 1

    # The CLI runs locally but inference happens at OpenAI, so by data egress
    # this is cloud and passes the consent gate like any other (CLAUDE.md
    # rule 18). Auth is the CLI's own subscription session — no API key of ours
    # crosses the boundary, which is what `auth_mode` distinguishes.
    @property
    def is_cloud(self) -> bool:
        return True

    # Billed to a subscription, not per call: the monthly spend cap
    # (emptyos/capabilities/spend_cap.py) does not count or block it.
    metered = False

    @property
    def auth_mode(self) -> str:
        return "login"

    def __init__(self, model: str = "", timeout: int = 0, cwd: str = ""):
        self.model = model
        self.timeout = timeout or 300
        self.cwd = cwd or ""
        self._codex_path: str | None = None

    async def available(self) -> bool:
        if self._codex_path is None:
            self._codex_path = shutil.which("codex") or ""
        return bool(self._codex_path)

    async def health(self) -> dict:
        if not await self.available():
            return {
                "available": False,
                "reason": "`codex` CLI not on PATH",
                "recovery": {
                    "kind": "service",
                    "id": "codex-cli",
                    "url": "",
                    "hint": "Install the Codex CLI and sign in, then ensure "
                            "`codex` is on PATH",
                },
            }
        return {"available": True}

    async def execute(
        self, *, prompt: str = "", system: str = "", messages: list[dict] | None = None, **kwargs
    ) -> str:
        if not await self.available():
            raise RuntimeError("codex CLI not found in PATH")

        body = prompt or ""
        if messages:
            body = "\n\n".join(
                f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in messages
            ) or body
        if system:
            body = f"[System]\n{system}\n\n[User]\n{body}"
        if not body.strip():
            raise RuntimeError("codex: empty prompt")

        cmd = [self._codex_path, "exec", "--json"]
        model = kwargs.get("model") or self.model
        if model:
            cmd.extend(["--model", model])
        # Signals intent and matches the rooms adapter. NOT a containment
        # boundary on Windows — the sandbox helper can fail and the command
        # re-runs unsandboxed (.claude/rules/multi-cli-participants.md). A think
        # call has no reason to touch the filesystem, so this is the posture,
        # not the protection.
        cmd.extend(["-s", "read-only"])
        cmd.append("-")   # read the prompt from stdin — see (1) in the docstring

        async with _codex_sem:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd or None,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=body.encode("utf-8")),
                    timeout=self.timeout,
                )
            except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError):
                # wait_for cancels communicate() but never signals the child;
                # reap it or the subprocess outlives the call and holds pipes
                # open for the life of the daemon. An outer cancellation (a
                # caller's own budget) lands here as CancelledError — same leak.
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
                raise

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace").strip()
        answer, errors = extract_codex_reply(out)

        if answer:
            # An `error` item alongside a real answer is codex talking about
            # itself (the skills-budget warning fires on healthy turns), so the
            # answer stands.
            return answer
        detail = "; ".join(errors) or err or out[:200] or "no agent_message in reply"
        raise RuntimeError(f"codex CLI produced no answer: {detail[:300]}")
