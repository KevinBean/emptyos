"""Agent-bench — compare providers on tool-use scenarios.

Parallel surface to the text-bench in `app.py`. Where text-bench scores a
single `think()` call on latency, agent-bench scores a multi-turn tool-use
loop on *did the task actually get done*, via a deterministic verifier.

Four subjects:
  - claude-external:  `claude -p <task>` subprocess using Claude Code's built-in tools
  - eos+claude:       AgentSession, provider="claude" → native-agentic loop (ClaudeCLIThinkProvider)
  - eos+openai:       AgentSession, provider="openai" → our run_turn loop
  - eos+ollama:       AgentSession, provider="ollama" → our run_turn loop

Scratch isolation is the whole safety story: every run gets its own
data/apps/model-bench/agent_scratch/<run_id>/ directory; scenarios only
reference paths inside it. Last N scratches are kept for eyeball review.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.sdk import BaseApp


KEEP_SCRATCHES = 7  # FIFO — last N scratch dirs preserved for inspection
DEFAULT_MAX_ITERS = 15
SUBPROCESS_PER_ITER_TIMEOUT = 30  # seconds per iteration for claude-external


# ── Subject identifiers ──────────────────────────────────────────────

ALL_SUBJECTS = (
    "claude-external",           # baseline: plain Claude Code, empty dir
    "claude-code-eos",           # Claude Code in EOS project root (CLAUDE.md loaded)
    "eos+claude",                # EOS agent harness + claude-cli
    "eos+openai",                # EOS harness + configured OpenAI model
    "eos+openai:gpt-4.1-mini",
    "eos+openai:gpt-5.4-nano",
    "eos+openai:gpt-5.4-mini",
    "eos+openai:gpt-5.4",
    "eos+ollama",                # EOS harness + configured Ollama model (qwen3.5:latest)
    "eos+ollama:qwen3.5:4b",    # smaller 4B variant

)


def _parse_subject(subject_id: str) -> tuple[str, str | None]:
    """Split 'eos+openai:gpt-5.4' → ('eos+openai', 'gpt-5.4').

    Handles multi-colon model names like 'eos+ollama:qwen3.5:9b'
    by splitting only on the first colon after the provider part.
    Returns (subject_id, None) for subjects without a model override.
    """
    if subject_id.startswith("eos+") and ":" in subject_id:
        base, model_override = subject_id.split(":", 1)
        return base, model_override
    return subject_id, None


# ── Data shapes ──────────────────────────────────────────────────────

@dataclass
class VerifyResult:
    ok: bool
    notes: str = ""


@dataclass
class AgentScenario:
    id: str
    title: str
    description: str
    # Plain-text instruction handed to each subject. May reference `{scratch}`
    # — substituted with the scratch dir absolute path before the run.
    task_template: str
    # Prepare the scratch dir (copy fixtures, pre-create directories, etc.).
    setup: Callable[[Path], None]
    # Post-run deterministic check.
    verify: Callable[[Path], VerifyResult]
    tags: list[str] = field(default_factory=list)
    expected_tool_floor: int = 1
    max_iters: int = DEFAULT_MAX_ITERS
    edit_path_limit: int = 5   # passed to run_turn; raise for open-ended app creation
    # When True, eos+ subjects run in the EOS project root instead of scratch.
    # The task template receives `{bench_app_path}` (absolute path under apps/)
    # rather than `{scratch}`. Created app is moved to scratch after the run
    # for verification, then deleted — keeping the project clean.
    # claude-external always uses scratch regardless of this flag.
    eos_use_project_root: bool = False


@dataclass
class AgentRunResult:
    run_id: str
    scenario_id: str
    subject_id: str
    ok: bool
    tool_calls: int
    tool_errors: int
    iterations: int
    wall_ms: int
    usage: dict = field(default_factory=dict)
    efficiency: float = 0.0          # floor / max(tool_calls, 1)
    notes: str = ""
    error: Optional[str] = None
    transcript_path: str = ""
    timestamp: str = ""
    scratch_path: str = ""
    # --- Grouping + provenance (learning loop) ------------------------
    run_group_id: str = ""           # set by the batch; one click → one group
    variant_id: str = ""             # e.g. "strict-prompt" vs "" (baseline)
    eos_git_sha: str = ""            # `git rev-parse HEAD` at run time
    system_prompt_hash: str = ""     # sha256(prompt)[:12]; "" for claude-external
    # --- Diagnostics derived from transcript at save time -------------
    tool_histogram: dict = field(default_factory=dict)   # {Read: 3, Bash: 9, ...}
    error_categories: dict = field(default_factory=dict) # {timeout: 1, bash_shell_limitation: 4, ...}
    # Actual model string (e.g. "qwen3.5:latest", "gpt-4.1-mini",
    # "claude-opus-4-7"). Distinct from subject_id which is the wrapper.
    subject_model: str = ""
    # --- Cost + reliability (A bundle) --------------------------------
    cost_usd: float = 0.0                # computed from usage tokens + price table
    rep_index: int = 0                   # 0-based index among N repetitions
    # The system prompt overlay state at run time. Helps post-hoc analysis
    # distinguish "ollama without overlay" from "ollama with overlay v1".
    overlay_applied: bool = False


# ── Scratch dir management ───────────────────────────────────────────

def scratch_root(data_dir: Path) -> Path:
    """data/apps/model-bench/agent_scratch/"""
    return data_dir / "agent_scratch"


def transcripts_root(data_dir: Path) -> Path:
    return data_dir / "agent_transcripts"


def make_run_id(scenario_id: str, subject_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short = uuid.uuid4().hex[:6]
    # Filesystem-safe: replace +, /, and : (model suffixes like qwen3.5:9b)
    safe_subject = subject_id.replace("+", "_").replace("/", "_").replace(":", "_")
    return f"{scenario_id}__{safe_subject}__{ts}__{short}"


def prepare_scratch(data_dir: Path, run_id: str, setup: Callable[[Path], None]) -> Path:
    """Create + setup a scratch dir. Returns an ABSOLUTE path so the dir is
    unambiguous when substituted into task instructions. A relative path
    would tempt the model to "absolute-ize" it by prefixing `/`, which on
    Windows resolves against the drive root and on Linux against the fs
    root — both wrong. Always return absolute."""
    root = scratch_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    scratch = (root / run_id).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    setup(scratch)
    return scratch


def prune_old_scratches(data_dir: Path, keep: int = KEEP_SCRATCHES):
    root = scratch_root(data_dir)
    if not root.exists():
        return
    dirs = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in dirs[keep:]:
        try:
            shutil.rmtree(old, ignore_errors=True)
        except Exception:
            pass


# ── Event collector — buffers loop events for post-run counting ──────

class EventCollector:
    """Drop-in replacement for kernel EventBus that buffers and counts.

    The agent loop calls `events.emit(etype, data, source=...)` on every
    agent:* event. We record them verbatim + maintain running counts so
    the runner can produce an AgentRunResult without re-parsing.
    """

    def __init__(self):
        self.events: list[dict] = []
        self.tool_calls = 0
        self.tool_errors = 0
        self.iterations = 0
        self.usage: dict = {}

    async def emit(self, etype: str, data: dict, source: str = "agent"):
        rec = {"type": etype, **data}
        self.events.append(rec)
        if etype == "agent:tool_call":
            self.tool_calls += 1
        elif etype == "agent:tool_result" and data.get("is_error"):
            self.tool_errors += 1
        elif etype == "agent:iter_start":
            self.iterations += 1
        elif etype == "agent:done":
            self.usage = data.get("usage") or {}


def save_transcript(data_dir: Path, run_id: str, events: list[dict]) -> Path:
    root = transcripts_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{run_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, default=str) + "\n")
    return path


# ── Subject runners ──────────────────────────────────────────────────

SCRATCH_SYSTEM_SUFFIX = (
    "\n\n## Scratch dir pinning\n"
    "You are running in benchmark mode. All file operations MUST stay under "
    "the scratch directory provided in the user message.\n"
    "\n"
    "CRITICAL: Always pass ABSOLUTE paths to tools — never bare relatives. "
    "Tools that accept a `path` or `pattern` argument resolve relatives "
    "against the EmptyOS repo root, NOT the scratch dir. If you pass "
    "`sdk/*.py` you will get the WRONG directory. Always prefix with the "
    "scratch absolute path from the task instruction.\n"
    "\n"
    "Do NOT read, write, or edit files outside the scratch dir. If the task "
    "cannot be completed within that dir, stop and say so — do not widen scope."
)


# Overlay for small/local models (ollama, LM Studio, etc.). They share a
# repeatable "1-tool-and-stop" failure mode: Grep returns results, model
# says "task complete" without following through on Edit. Frontier models
# don't need this hand-holding — and it can actually hurt them by adding
# process noise. So we apply it ONLY when provider heuristics say small.
SMALL_MODEL_OVERLAY = (
    "\n\n## Procedural discipline (apply strictly)\n"
    "You have a documented tendency to stop too early. Follow these rules:\n"
    "\n"
    "1. If Grep returns ONE OR MORE matches and the task asks you to modify\n"
    "   or replace something, you MUST call Edit (or Write) for each affected\n"
    "   file. Do NOT conclude the task after Grep alone.\n"
    "2. If Edit fails with 'not found' or 'occurs N times', the whitespace or\n"
    "   surrounding context may differ from what you expect. Use Read to see\n"
    "   the exact bytes, then retry Edit with a unique snippet.\n"
    "3. If you Read a file, and the task asked for modifications, you MUST\n"
    "   proceed to Edit or Write. Reading alone is never sufficient.\n"
    "4. Never declare 'no changes needed' UNLESS Grep across the specified\n"
    "   path literally returns zero matches.\n"
    "5. If the task lists numbered steps (1., 2., 3.), execute every step\n"
    "   in order. Skipping = failure. When the final step is 'write X to\n"
    "   Y', that Write/Edit call is mandatory before you stop.\n"
)


def _provider_prompt_overlay(provider) -> str:
    """Return a provider-tier-specific system prompt overlay, or ''.

    Heuristic: local providers (``is_cloud`` is False — covers ollama,
    LM Studio, and anything else served over a private-IP host) get the
    `SMALL_MODEL_OVERLAY`. Cloud providers get nothing — they don't need
    the scaffolding and it just adds tokens.
    """
    if getattr(provider, "is_cloud", True) is False:
        return SMALL_MODEL_OVERLAY
    return ""


async def run_eos_agent_subject(
    *,
    app: "BaseApp",
    scenario: AgentScenario,
    subject_id: str,          # "eos+claude" | "eos+openai" | "eos+ollama"
    scratch: Path,
    run_id: str,
    data_dir: Path,
    run_group_id: str = "",
    variant_id: str = "",
    apply_overlay: bool = True,
) -> AgentRunResult:
    """Drive the eos-agent loop in-process with a forced provider."""
    import copy as _copy
    from emptyos.sdk.agent_loop import (
        AgentSession, DEFAULT_SYSTEM_PROMPT, run_native_turn, run_turn,
    )
    from emptyos.sdk.agent_tools import build_registry
    from emptyos.capabilities.providers._tool_capable import NativelyAgenticProvider

    base_sid, model_override = _parse_subject(subject_id)
    alias = base_sid.split("+", 1)[1]   # "claude" / "openai" / "ollama"

    # Reuse the agent app's resolver — but FIRST translate the friendly
    # benchmark alias to the actual provider identity. Without this,
    # `eos+claude` silently resolves to whatever ToolCapable provider
    # comes first in the chain (openai_compat) — a pre-existing bug in
    # the agent app's exact-name matcher.
    agent_app = app.kernel.apps.instances.get("agent")
    if agent_app is None:
        agent_app = await app.kernel.apps.load("agent")
    provider = _resolve_bench_subject_provider(agent_app, alias)
    if provider is not None and model_override and hasattr(provider, "model"):
        provider = _copy.copy(provider)
        provider.model = model_override
    if provider is None:
        return _failed_result(
            run_id, scenario, subject_id, scratch,
            error=f"no tool-capable provider resolved for alias {alias!r}",
            run_group_id=run_group_id, variant_id=variant_id,
        )
    is_native = isinstance(provider, NativelyAgenticProvider)

    tools = build_registry()
    sess = AgentSession(
        id=run_id,
        messages=[],
        provider_kind="native" if is_native else provider.kind,
    )

    overlay = _provider_prompt_overlay(provider) if apply_overlay else ""
    overlay_was_applied = bool(overlay)

    if scenario.eos_use_project_root:
        # Run in the real EOS project so the model has full context (CLAUDE.md,
        # existing apps, SDK). Create a bench-namespaced app for clean teardown.
        eos_root = Path(__file__).resolve().parent.parent.parent
        short = run_id.split("__")[-1]
        # Derive names from scenario id so each scenario gets its own namespace.
        bench_slug = scenario.id.replace("-app", "").replace("-", "-")
        bench_app_id = f"bench-{bench_slug}-{short}"
        bench_app_dir = eos_root / "apps" / bench_app_id
        bench_app_path = str(bench_app_dir).replace("\\", "/")
        system = DEFAULT_SYSTEM_PROMPT + overlay
        task = (
            scenario.task_template.split("\n\n")[0] + "\n\n"
            f"Create the app at apps/{bench_app_id}/ "
            f"(benchmark output location — do not change this path)."
        )
    else:
        system = DEFAULT_SYSTEM_PROMPT + SCRATCH_SYSTEM_SUFFIX + overlay
        bench_app_dir = None
        task = scenario.task_template.replace("{scratch}", str(scratch).replace("\\", "/"))

    collector = EventCollector()
    t0 = time.monotonic()
    err: str | None = None

    try:
        if is_native:
            await run_native_turn(
                session=sess, user_text=task, provider=provider,
                events=collector, system=system,
            )
        else:
            await run_turn(
                session=sess, user_text=task, provider=provider,
                tools=tools, tool_consent=None,   # auto-approve in bench
                events=collector, app_ref=app,
                system=system, max_iters=scenario.max_iters,
                edit_path_limit=scenario.edit_path_limit,
            )
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    # If project-root mode: save app permanently to gesture-logs/, copy to scratch
    # for the verifier, then remove from apps/ to keep the project clean.
    if bench_app_dir is not None and bench_app_dir.exists():
        try:
            safe_sid = subject_id.replace("+", "_").replace(":", "_").replace("/", "_")
            logs_name = scenario.id.replace("-app", "") + "-logs"
            log_dir = data_dir / logs_name / f"{safe_sid}__{run_id.split('__')[-1]}"
            log_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(bench_app_dir, log_dir)
            shutil.copytree(bench_app_dir, scratch / bench_app_dir.name)
            shutil.rmtree(bench_app_dir, ignore_errors=True)
        except Exception:
            pass

    wall_ms = int((time.monotonic() - t0) * 1000)
    transcript_path = save_transcript(data_dir, run_id, collector.events)
    verify = scenario.verify(scratch)
    ok = _overall_ok(verify, err, collector.tool_calls, scenario)
    notes = _combine_notes(verify, collector.tool_calls, scenario)
    return AgentRunResult(
        run_id=run_id,
        scenario_id=scenario.id,
        subject_id=subject_id,
        ok=ok,
        tool_calls=collector.tool_calls,
        tool_errors=collector.tool_errors,
        iterations=collector.iterations,
        wall_ms=wall_ms,
        usage=collector.usage,
        efficiency=_efficiency(scenario, collector.tool_calls),
        notes=notes,
        error=err,
        transcript_path=str(transcript_path),
        timestamp=datetime.now(timezone.utc).isoformat(),
        scratch_path=str(scratch),
        run_group_id=run_group_id,
        variant_id=variant_id,
        eos_git_sha=_git_sha(),
        system_prompt_hash=_prompt_hash(system),
        tool_histogram=_tool_histogram(collector.events),
        error_categories=_error_categories(collector.events),
        subject_model=_resolve_subject_model(provider, collector.usage),
        cost_usd=_compute_cost_usd(
            _resolve_subject_model(provider, collector.usage),
            collector.usage,
        ),
        overlay_applied=overlay_was_applied,
    )


async def run_claude_external_subject(
    *,
    scenario: AgentScenario,
    scratch: Path,
    run_id: str,
    data_dir: Path,
    run_group_id: str = "",
    variant_id: str = "",
) -> AgentRunResult:
    """Run raw `claude -p <task>` as a subprocess inside the scratch dir."""
    task = scenario.task_template.replace("{scratch}", str(scratch).replace("\\", "/"))
    # Augment with the same pinning instruction used for eos-agent runs
    task_full = (
        f"You are in a benchmark scratch directory: {scratch}\n"
        "Do all work inside this directory. Do not touch files outside it.\n\n"
        f"TASK:\n{task}"
    )

    # Detect claude CLI
    claude_path = shutil.which("claude")
    if not claude_path:
        return _failed_result(
            run_id, scenario, "claude-external", scratch,
            error="claude CLI not found on PATH — install Claude Code to run this subject",
        )

    cmd = [
        claude_path,
        "-p", task_full,
        "--output-format", "stream-json",
        "--verbose",   # required alongside stream-json
        "--max-turns", str(scenario.max_iters),
        "--dangerously-skip-permissions",
    ]

    events: list[dict] = []
    tool_calls = 0
    tool_errors = 0
    iterations = 0
    usage: dict = {}

    t0 = time.monotonic()
    err: str | None = None
    timeout_s = scenario.max_iters * SUBPROCESS_PER_ITER_TIMEOUT

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(scratch),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            err = f"subprocess timeout after {timeout_s}s"
            stdout, stderr = b"", b""

        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            events.append(ev)
            # stream-json events have shape like:
            #   {"type": "assistant", "message": {"content": [{"type": "tool_use", ...}]}}
            msg = ev.get("message") or {}
            content = msg.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_calls += 1
            # Usage info attached to the final result event
            if "usage" in ev:
                usage = ev["usage"]
            if "total_cost_usd" in ev:
                usage["cost_usd"] = ev["total_cost_usd"]

        # Iteration count: number of assistant-role messages emitted
        iterations = sum(1 for ev in events if ev.get("type") == "assistant")

        if proc.returncode and proc.returncode != 0 and not err:
            err = (
                f"claude exited with {proc.returncode}; "
                f"stderr: {stderr.decode('utf-8', errors='replace')[:400]}"
            )
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    wall_ms = int((time.monotonic() - t0) * 1000)
    transcript_path = save_transcript(data_dir, run_id, events)
    verify = scenario.verify(scratch)
    ok = _overall_ok(verify, err, tool_calls, scenario)
    notes = _combine_notes(verify, tool_calls, scenario)
    # Claude-external emits different-shaped events (stream-json). Derive a
    # histogram by walking the assistant-message tool_use blocks.
    tool_hist: Counter[str] = Counter()
    for ev in events:
        msg = ev.get("message") or {}
        content = msg.get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_hist[block.get("name") or "?"] += 1
    return AgentRunResult(
        run_id=run_id,
        scenario_id=scenario.id,
        subject_id="claude-external",
        ok=ok,
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        iterations=iterations,
        wall_ms=wall_ms,
        usage=usage,
        efficiency=_efficiency(scenario, tool_calls),
        notes=notes,
        error=err,
        transcript_path=str(transcript_path),
        timestamp=datetime.now(timezone.utc).isoformat(),
        scratch_path=str(scratch),
        run_group_id=run_group_id,
        variant_id=variant_id,
        eos_git_sha=_git_sha(),
        system_prompt_hash="",   # claude-external owns its own system prompt
        tool_histogram=dict(tool_hist),
        error_categories={},     # stream-json doesn't surface per-tool errors cleanly
        subject_model=_model_from_stream_events(events) or "",
        cost_usd=_compute_cost_usd(
            _model_from_stream_events(events) or "",
            usage,
        ),
        overlay_applied=False,   # claude-external owns its own prompt
    )


async def run_claude_code_eos_subject(
    *,
    scenario: AgentScenario,
    scratch: Path,
    run_id: str,
    data_dir: Path,
    run_group_id: str = "",
    variant_id: str = "",
) -> AgentRunResult:
    """Run `claude -p task` in the EOS project root with CLAUDE.md loaded.

    Simulates 'Tool A' from the article — the same claude CLI but with full
    EmptyOS context (CLAUDE.md, existing apps/ patterns, shared SDK). Newly
    created app dirs are moved to scratch after the run for verification and
    removed from the live project, leaving the repo clean.
    """
    eos_root = Path(__file__).resolve().parent.parent.parent
    apps_dir = eos_root / "apps"

    claude_path = shutil.which("claude")
    if not claude_path:
        return _failed_result(
            run_id, scenario, "claude-code-eos", scratch,
            error="claude CLI not found on PATH — install Claude Code to run this subject",
            run_group_id=run_group_id, variant_id=variant_id,
        )

    # Use a bench-namespaced app id so the created dir is unambiguous —
    # detecting "new dirs in apps/" is fragile when a same-named app already exists.
    short = run_id.split("__")[-1]   # 6-char hex suffix
    bench_app_id = f"bench-gesture-{short}"
    bench_app_dir = apps_dir / bench_app_id

    task = scenario.task_template.split("\n\n")[0]  # strip scratch hint — irrelevant here
    task_full = (
        "You are working inside the EmptyOS project. "
        "Follow all conventions in CLAUDE.md.\n\n"
        f"TASK:\n{task}\n\n"
        f"Create the app at apps/{bench_app_id}/ "
        f"(this is the required output location for the benchmark — do not change this path)."
    )

    cmd = [
        claude_path, "-p", task_full,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(scenario.max_iters),
        "--dangerously-skip-permissions",
    ]

    events: list[dict] = []
    tool_calls = 0
    iterations = 0
    usage: dict = {}
    t0 = time.monotonic()
    err: str | None = None
    timeout_s = scenario.max_iters * SUBPROCESS_PER_ITER_TIMEOUT

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(eos_root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            err = f"subprocess timeout after {timeout_s}s"
            stdout, stderr_b = b"", b""

        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            events.append(ev)
            msg = ev.get("message") or {}
            content = msg.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_calls += 1
            if "usage" in ev:
                usage = ev["usage"]
            if "total_cost_usd" in ev:
                usage["cost_usd"] = ev["total_cost_usd"]

        iterations = sum(1 for ev in events if ev.get("type") == "assistant")

        if proc.returncode and proc.returncode != 0 and not err:
            err = (
                f"claude exited with {proc.returncode}; "
                f"stderr: {stderr_b.decode('utf-8', errors='replace')[:400]}"
            )
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    wall_ms = int((time.monotonic() - t0) * 1000)
    transcript_path = save_transcript(data_dir, run_id, events)

    # Save app permanently to gesture-logs/, copy to scratch for verifier,
    # then remove from apps/ to keep the project clean.
    if bench_app_dir.exists():
        try:
            log_dir = data_dir / "gesture-logs" / f"claude-code-eos__{run_id.split('__')[-1]}"
            log_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(bench_app_dir, log_dir)
            shutil.copytree(bench_app_dir, scratch / bench_app_id)
            shutil.rmtree(bench_app_dir, ignore_errors=True)
        except Exception:
            pass

    verify = scenario.verify(scratch)
    ok = _overall_ok(verify, err, tool_calls, scenario)
    notes = _combine_notes(verify, tool_calls, scenario)

    tool_hist: Counter[str] = Counter()
    for ev in events:
        msg = ev.get("message") or {}
        content = msg.get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_hist[block.get("name") or "?"] += 1

    detected_model = _model_from_stream_events(events) or ""
    return AgentRunResult(
        run_id=run_id,
        scenario_id=scenario.id,
        subject_id="claude-code-eos",
        ok=ok,
        tool_calls=tool_calls,
        tool_errors=0,
        iterations=iterations,
        wall_ms=wall_ms,
        usage=usage,
        efficiency=_efficiency(scenario, tool_calls),
        notes=notes,
        error=err,
        transcript_path=str(transcript_path),
        timestamp=datetime.now(timezone.utc).isoformat(),
        scratch_path=str(scratch),
        run_group_id=run_group_id,
        variant_id=variant_id,
        eos_git_sha=_git_sha(),
        system_prompt_hash="",   # CLAUDE.md is the system prompt; not hashed here
        tool_histogram=dict(tool_hist),
        error_categories={},
        subject_model=detected_model,
        cost_usd=_compute_cost_usd(detected_model, usage),
        overlay_applied=False,
    )


# ── Provenance + diagnostics helpers ─────────────────────────────────

_CACHED_GIT_SHA: tuple[Path, str] | None = None


def _git_sha() -> str:
    """Return the eos repo's current HEAD sha, or '' if unavailable.

    Cached for the process lifetime — restart the daemon to pick up a new
    commit. That's a feature: results from one boot are on one sha.
    """
    global _CACHED_GIT_SHA
    repo = Path(__file__).resolve().parent.parent.parent
    if _CACHED_GIT_SHA is not None and _CACHED_GIT_SHA[0] == repo:
        return _CACHED_GIT_SHA[1]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=5,
        )
        sha = out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        sha = ""
    _CACHED_GIT_SHA = (repo, sha)
    return sha


def _prompt_hash(prompt: str) -> str:
    if not prompt:
        return ""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


# ── Cost estimation ──────────────────────────────────────────────────

# Price table: (input_usd_per_M, output_usd_per_M). Keyed by a prefix of
# the model string (case-insensitive) so suffixed variants match —
# e.g. "claude-opus-4-7[1m]" matches "claude-opus-4-7".
# Sources: OpenAI + Anthropic published pricing as of late 2025. Local
# models (ollama) are free. Missing model → 0.
_MODEL_PRICES_PER_M_TOKENS: list[tuple[str, tuple[float, float]]] = [
    # OpenAI — gpt-5.4 family (short-context rates)
    ("gpt-5.4-pro",        (30.00, 180.00)),
    ("gpt-5.4-mini",       (0.75,   4.50)),
    ("gpt-5.4-nano",       (0.20,   1.25)),
    ("gpt-5.4",            (2.50,  15.00)),
    # OpenAI — gpt-5.x family
    ("gpt-5.2-pro",        (21.00, 168.00)),
    ("gpt-5.2",            (1.75,  14.00)),
    ("gpt-5.1",            (1.25,  10.00)),
    ("gpt-5-pro",          (15.00, 120.00)),
    ("gpt-5-mini",         (0.25,   2.00)),
    ("gpt-5-nano",         (0.05,   0.40)),
    ("gpt-5",              (1.25,  10.00)),
    # OpenAI — gpt-4.x family
    ("gpt-4.1-nano",       (0.10,   0.40)),
    ("gpt-4.1-mini",       (0.40,   1.60)),
    ("gpt-4.1",            (2.00,   8.00)),
    ("gpt-4o-mini",        (0.15,   0.60)),
    ("gpt-4o",             (2.50,  10.00)),
    # Anthropic Claude 4.x
    ("claude-opus-4-7",    (15.00, 75.00)),
    ("claude-opus-4",      (15.00, 75.00)),
    ("claude-sonnet-4-6",   (3.00, 15.00)),
    ("claude-sonnet-4",     (3.00, 15.00)),
    ("claude-haiku-4-5",    (0.80,  4.00)),
    ("claude-haiku-4",      (0.80,  4.00)),
    # Local
    ("qwen3.5",            (0.0, 0.0)),
    ("qwen2",              (0.0, 0.0)),
    ("llama3",             (0.0, 0.0)),
    ("llama2",             (0.0, 0.0)),
    ("mistral",            (0.0, 0.0)),
]


def _lookup_price(model: str) -> tuple[float, float] | None:
    m = (model or "").lower()
    if not m:
        return None
    for prefix, rates in _MODEL_PRICES_PER_M_TOKENS:
        if m.startswith(prefix.lower()):
            return rates
    return None


def _token_counts(usage: dict) -> tuple[int, int]:
    """Normalize input+output token counts across provider shapes.

    OpenAI / Ollama: `prompt_tokens`, `completion_tokens`.
    Anthropic:      `input_tokens`, `output_tokens` (+ cache-related).
    """
    if not isinstance(usage, dict):
        return 0, 0
    input_toks = usage.get("prompt_tokens")
    if input_toks is None:
        input_toks = usage.get("input_tokens", 0)
    output_toks = usage.get("completion_tokens")
    if output_toks is None:
        output_toks = usage.get("output_tokens", 0)
    try:
        return int(input_toks or 0), int(output_toks or 0)
    except (TypeError, ValueError):
        return 0, 0


def _compute_cost_usd(model: str, usage: dict) -> float:
    """Estimate USD cost of a run. Prefers provider-reported cost fields,
    falls back to token counts × price table, falls back to 0."""
    if not isinstance(usage, dict):
        return 0.0
    for key in ("cost", "total_cost_usd", "cost_usd"):
        v = usage.get(key)
        if v is not None:
            try:
                return round(float(v), 6)
            except (TypeError, ValueError):
                pass
    price = _lookup_price(model)
    if price is None:
        return 0.0
    pi, po = price
    inp, out = _token_counts(usage)
    return round((inp * pi + out * po) / 1_000_000, 6)


def _resolve_bench_subject_provider(agent_app, alias: str):
    """Map a benchmark subject alias to the actual provider instance.

    The agent app's `_resolve_provider` matches on `provider.name` exactly,
    but the benchmark labels (`claude`, `openai`, `ollama`) are *friendly*
    names that don't match any provider's class name. So `eos+claude`
    would silently fall through to the first ToolCapable provider — which
    would mean every `eos+claude` row was actually run on openai_compat.

    Here we match by semantics:
      - "claude"  — name "claude-cli" OR "anthropic_sdk"
      - "openai"  — OpenAI-compat with api endpoint at api.openai.com
      - "ollama"  — OpenAI-compat pointed at localhost:11434
      - any other — fall through to exact match via agent_app
    """
    from emptyos.capabilities.providers._tool_capable import (
        NativelyAgenticProvider, ToolCapableProvider,
    )

    think = agent_app.kernel.capability("think")
    candidates = list(think.providers)
    for chain in think._domains.values():
        candidates.extend(chain)
    for chain in think._buckets.values():
        candidates.extend(chain)
    # Dedup by identity
    seen_ids = set()
    uniq = []
    for p in candidates:
        if id(p) in seen_ids:
            continue
        seen_ids.add(id(p))
        uniq.append(p)

    def _is_agent_usable(p):
        return isinstance(p, (ToolCapableProvider, NativelyAgenticProvider))

    def _endpoint(p):
        # openai_compat keeps the endpoint; other providers don't.
        return str(getattr(p, "endpoint", "") or getattr(p, "base_url", "") or "").lower()

    alias = alias.lower()

    if alias == "claude":
        for p in uniq:
            if _is_agent_usable(p) and p.name in ("claude-cli", "anthropic_sdk"):
                return p
    elif alias == "ollama":
        for p in uniq:
            if _is_agent_usable(p) and p.name == "openai_compat":
                ep = _endpoint(p)
                # Ollama defaults to :11434. Also match the hostname "ollama"
                # for containerized setups.
                if "11434" in ep or "ollama" in ep:
                    return p
    elif alias == "openai":
        for p in uniq:
            if _is_agent_usable(p) and p.name == "openai_compat":
                ep = _endpoint(p)
                if "openai.com" in ep or ep == "":
                    # Empty endpoint = default SDK base = OpenAI prod.
                    return p

    # Fallback: defer to agent_app's exact-name matcher (preserves old behavior)
    return agent_app._resolve_provider(alias)


def _resolve_subject_model(provider, usage: dict) -> str:
    """Return the actual model string for an eos+ run.

    Order of preference:
    1. usage["model"] — OpenAI-compat + Ollama both set this on the Turn
    2. provider.model — native-agentic providers (claude-cli) expose it
    3. getattr(provider, "_model", None) — some providers keep it private
    4. "" — unknown
    """
    if isinstance(usage, dict):
        m = usage.get("model")
        if m:
            return str(m)
    for attr in ("model", "_model"):
        m = getattr(provider, attr, None)
        if m:
            return str(m)
    return ""


def _model_from_stream_events(events: list[dict]) -> str:
    """Extract model string from a claude-external stream-json transcript.

    The CLI emits a system/init event at start carrying `"model": "..."`.
    Fall back to any assistant message's `message.model` if that's missing.
    """
    for ev in events:
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            m = ev.get("model")
            if m:
                return str(m)
    for ev in events:
        msg = ev.get("message") or {}
        if isinstance(msg, dict) and msg.get("model"):
            return str(msg["model"])
    return ""


def _tool_histogram(events: list[dict]) -> dict[str, int]:
    """Count tool_call events by tool name."""
    counts: Counter[str] = Counter()
    for e in events:
        if e.get("type") == "agent:tool_call":
            counts[e.get("name") or "?"] += 1
    return dict(counts)


_ERROR_PATTERNS: list[tuple[str, str]] = [
    # (category, substring match — lowercased)
    ("timeout",               "timed out"),
    ("timeout",               "timeout"),
    ("bash_shell_limitation", "metacharacter"),
    ("command_not_found",     "command not found"),
    ("command_not_found",     "not on path"),
    ("missing_target",        "file not found"),
    ("missing_target",        "not a directory"),
    ("missing_target",        "no such file"),
    ("edit_ambiguous",        "occurs"),        # our Edit's "occurs N times"
    ("edit_not_found",        "old_string not found"),
    ("binary_file",           "not a utf-8"),
    ("permission_denied",     "denied by user"),
    ("bad_args",              "bad arguments"),
    ("bad_args",              "is required"),
    ("invalid_target",        "unknown subject_id"),
    ("invalid_target",        "app"),           # e.g. "app 'ghost' not found"
]


def _categorize_error(snippet: str, display: dict) -> str:
    """Classify a failed tool result into a broad category.

    Uses the error snippet string embedded by the agent loop (from the
    tool's ToolResult.content). Falls back to tool-specific display
    signals (e.g. Bash exit_code) when no snippet matches.
    """
    s = (snippet or "").lower()
    for cat, needle in _ERROR_PATTERNS:
        if needle in s:
            return cat
    # Tool-specific display fallbacks
    if display:
        name = display.get("name")
        if name == "Bash":
            ec = display.get("exit_code")
            if isinstance(ec, int) and ec != 0:
                return "bash_nonzero_exit"
    return "other"


def _error_categories(events: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for e in events:
        if e.get("type") != "agent:tool_result" or not e.get("is_error"):
            continue
        cat = _categorize_error(e.get("error_snippet") or "", e.get("display") or {})
        counts[cat] += 1
    return dict(counts)


def _efficiency(scenario: AgentScenario, tool_calls: int) -> float:
    """Efficiency ratio capped at 1.0.

    = min(1.0, floor / max(tool_calls, 1))
      - tool_calls == floor  → 1.0 (perfect)
      - tool_calls > floor   → < 1 (wasted calls)
      - tool_calls < floor   → still 1.0, because verifying success with fewer
                               calls than the floor either means the floor is
                               too conservative or the run failed outright
                               (captured by ok=False anyway).
    """
    if tool_calls <= 0:
        return 0.0
    return round(min(1.0, scenario.expected_tool_floor / tool_calls), 3)


def _overall_ok(verify: VerifyResult, err: str | None, tool_calls: int,
                scenario: AgentScenario) -> bool:
    """Success = verifier passed AND no runner error AND agent actually used
    at least one tool when the scenario requires it.

    Without this floor, read-only scenarios like `explain-structure` could
    pass just because the agent replied in prose without using a tool.
    """
    if err is not None:
        return False
    if not verify.ok:
        return False
    if scenario.expected_tool_floor > 0 and tool_calls == 0:
        return False
    return True


def _combine_notes(verify: VerifyResult, tool_calls: int,
                   scenario: AgentScenario) -> str:
    note = verify.notes
    if scenario.expected_tool_floor > 0 and tool_calls == 0:
        suffix = "no tool calls recorded — agent answered without using tools"
        note = f"{note}; {suffix}" if note else suffix
    return note


def _failed_result(
    run_id: str, scenario: AgentScenario, subject_id: str, scratch: Path,
    *, error: str, run_group_id: str = "", variant_id: str = "",
) -> AgentRunResult:
    return AgentRunResult(
        run_id=run_id,
        scenario_id=scenario.id,
        subject_id=subject_id,
        ok=False,
        tool_calls=0, tool_errors=0, iterations=0, wall_ms=0,
        error=error,
        timestamp=datetime.now(timezone.utc).isoformat(),
        scratch_path=str(scratch),
        run_group_id=run_group_id,
        variant_id=variant_id,
        eos_git_sha=_git_sha(),
    )


# ── Orchestration ────────────────────────────────────────────────────

SubjectRunner = Callable[..., Awaitable[AgentRunResult]]


async def run_scenario(
    *,
    app: "BaseApp",
    scenario: AgentScenario,
    subject_ids: list[str],
    data_dir: Path,
    run_group_id: str = "",
    variant_id: str = "",
    apply_overlay: bool = True,
    reps: int = 1,
) -> list[AgentRunResult]:
    """Run one scenario across N subjects × `reps` repetitions each.

    `run_group_id` tags every produced result so downstream UI can group
    them as one batch (one "Run every scenario" click → one group).
    `variant_id` lets the same subject be benchmarked with different
    prompt overlays without losing its identity.
    `reps` >= 2 exposes stochastic variance — useful for non-deterministic
    providers where a single pass/fail may be misleading.
    """
    if not run_group_id:
        run_group_id = new_run_group_id()
    if reps < 1:
        reps = 1
    results: list[AgentRunResult] = []
    for sid in subject_ids:
        for rep_idx in range(reps):
            run_id = make_run_id(scenario.id, sid)
            scratch = prepare_scratch(data_dir, run_id, scenario.setup)
            try:
                if sid == "claude-external":
                    res = await run_claude_external_subject(
                        scenario=scenario, scratch=scratch,
                        run_id=run_id, data_dir=data_dir,
                        run_group_id=run_group_id, variant_id=variant_id,
                    )
                elif sid == "claude-code-eos":
                    res = await run_claude_code_eos_subject(
                        scenario=scenario, scratch=scratch,
                        run_id=run_id, data_dir=data_dir,
                        run_group_id=run_group_id, variant_id=variant_id,
                    )
                elif sid.startswith("eos+"):
                    res = await run_eos_agent_subject(
                        app=app, scenario=scenario, subject_id=sid,
                        scratch=scratch, run_id=run_id, data_dir=data_dir,
                        run_group_id=run_group_id, variant_id=variant_id,
                        apply_overlay=apply_overlay,
                    )
                else:
                    res = _failed_result(
                        run_id, scenario, sid, scratch,
                        error=f"unknown subject_id {sid!r}",
                        run_group_id=run_group_id, variant_id=variant_id,
                    )
            except Exception as e:
                res = _failed_result(
                    run_id, scenario, sid, scratch,
                    error=f"runner crashed: {type(e).__name__}: {e}",
                    run_group_id=run_group_id, variant_id=variant_id,
                )
            res.rep_index = rep_idx
            results.append(res)
    prune_old_scratches(data_dir)
    return results


def new_run_group_id() -> str:
    """Opaque batch id. One `Run every scenario` click → one id."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"grp_{ts}_{uuid.uuid4().hex[:6]}"


# ── Persistence ──────────────────────────────────────────────────────

def results_path(data_dir: Path) -> Path:
    return data_dir / "agent_results.json"


def load_results(data_dir: Path) -> list[dict]:
    p = results_path(data_dir)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_results(data_dir: Path, new_results: list[AgentRunResult]):
    existing = load_results(data_dir)
    for r in new_results:
        existing.append(asdict(r))
    if len(existing) > 500:
        existing = existing[-500:]
    results_path(data_dir).write_text(
        json.dumps(existing, indent=2, default=str), encoding="utf-8",
    )
