"""Loop registry — the single place that NAMES EmptyOS's feedback loops.

A "loop" here is a feedback path that turns model/agent output into checked,
reversible, improving work. EmptyOS grew ~19 of them across ~6 SDK modules,
5 apps, 2 plugins, and the preflight registry — but until this module there
was **no single surface that named them as one concept**. ``docs/
ENGINEERING-WORK-LOOP.md`` admits exactly this ("the one honest gap is
discoverability — these surfaces existed but weren't named as a single loop").

This module closes that gap with a declarative catalog. It is **pure data +
pure functions** — no ``self``, no kernel, no I/O — so it unit-tests without a
daemon and can be read by a CLI (``eos loops``), a doc generator, or a hub
panel without importing any app.

## The six stages

Every loop is scored on which of these it implements:

- ``friction`` — a signal/friction source (something surfaces work to do)
- ``act``      — an act/fix driver (something turns the signal into a change)
- ``gate``     — a gate/verify step (something checks the change is good)
- ``revert``   — a revert/rollback path (a way back when the change is bad)
- ``memory``   — memory/compounding (the loop gets better over runs)
- ``bound``    — a termination/budget/escalation condition (how the loop STOPS)

``bound`` is here because the field is unanimous that a loop without a stopping
condition "isn't a loop, it's a failure mode" — every serious framing (OpenAI's
"run until an exit condition", LangChain, the governed-workspace critique) treats
"how does it stop" as first-class. EmptyOS has the machinery (``run_budget``,
drain attempt/timeout budgets, the convergence zero-streak) but had not modelled
it as a stage until this registry.

A loop rarely covers all six. The marquee test-fix-verify loop does, by
composing several single-stage loops via the event bus.

## Status

- ``live`` — wired and enabled by default
- ``dark`` — built but gated behind a feature flag (``flag`` names the key)
- ``doc``  — described in doctrine, not a runnable code path

## LoopReceipt

The article "Loop Engineer, Not Prompt Engineer" names a proof object: **one
captured run** showing where the friction came from, which fixes ran, which
gates fired, whether the sandbox verify passed, whether a revert happened, and
what the system learned. ``LoopReceipt`` is that schema, and
``receipt_from_drain_state`` builds one from a real fix-drain ``run.json`` — so
the receipt is an artifact of an actual run, never hand-authored.

See ``.claude/rules/test-fix-verify-loop.md``, ``.claude/rules/self-audit-loops.md``,
``docs/ENGINEERING-WORK-LOOP.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ─── The five loop stages ────────────────────────────────────────────
FRICTION = "friction"
ACT = "act"
GATE = "gate"
REVERT = "revert"
MEMORY = "memory"
BOUND = "bound"
STAGES: tuple[str, ...] = (FRICTION, ACT, GATE, REVERT, MEMORY, BOUND)

STAGE_LABEL: dict[str, str] = {
    FRICTION: "friction/signal source",
    ACT: "act/fix driver",
    GATE: "gate/verify",
    REVERT: "revert/rollback",
    MEMORY: "memory/compounding",
    BOUND: "termination/budget",
}

# ─── Status values ───────────────────────────────────────────────────
LIVE = "live"
DARK = "dark"
DOC = "doc"


@dataclass(frozen=True)
class Loop:
    """One named feedback loop in EmptyOS."""

    id: str
    name: str
    summary: str
    stages: tuple[str, ...]
    status: str = LIVE
    flag: str | None = None  # exact dark-flag key when status == DARK
    components: tuple[str, ...] = ()  # file paths that implement it
    family: str = ""  # grouping: self-improve | primitive | self-audit | engineering | agent

    def __post_init__(self) -> None:
        bad = [s for s in self.stages if s not in STAGES]
        if bad:
            raise ValueError(f"loop {self.id!r}: unknown stage(s) {bad}")
        if self.status not in (LIVE, DARK, DOC):
            raise ValueError(f"loop {self.id!r}: bad status {self.status!r}")
        if self.status == DARK and not self.flag:
            raise ValueError(f"loop {self.id!r}: dark loop must name its flag")

    def covers(self, stage: str) -> bool:
        return stage in self.stages


# ─── The catalog ─────────────────────────────────────────────────────
# Grounded in the real modules (verified 2026-07-11). When a loop's status
# or flag changes, update it here — this is the source of truth the CLI reads.
LOOPS: list[Loop] = [
    # --- Family: self-improve (the marquee test-fix-verify loop + parts) ---
    Loop(
        id="test-fix-verify",
        name="Test-fix-verify (marquee)",
        summary="The composed self-improvement loop: friction source → fix "
        "driver → sandbox verify → revert, under drain budgets + a convergence "
        "stop-streak. Spans all six stages across apps.",
        stages=(FRICTION, ACT, GATE, REVERT, MEMORY, BOUND),
        status=DARK,
        flag="apps.dogfood-agent.fix_agent_enabled",
        components=(
            ".claude/rules/test-fix-verify-loop.md",
            "apps/extension/dev/dogfood-agent/",
            "apps/extension/dev/fix-agent/",
            "apps/extension/dev/trace-miner/",
            "plugins/sandbox-pool/plugin.py",
        ),
        family="self-improve",
    ),
    Loop(
        id="dogfood-agent",
        name="Dogfood persona runs",
        summary="Persona uses the system, surfaces friction, and actively "
        "re-runs the scenario to verify a fix took.",
        stages=(FRICTION, GATE),
        status=LIVE,
        components=("apps/extension/dev/dogfood-agent/friction.py",
                    "apps/extension/dev/dogfood-agent/runs.py"),
        family="self-improve",
    ),
    Loop(
        id="fix-agent",
        name="Fix-agent",
        summary="Turns a fix-prompt into a bounded patch in a worktree; "
        "py_compile-gates the merge; reverts the merge commit on bad verify.",
        stages=(ACT, GATE, REVERT),
        status=LIVE,
        components=("apps/extension/dev/fix-agent/runner.py",
                    "apps/extension/dev/fix-agent/merge.py",
                    "apps/extension/dev/fix-agent/verify.py"),
        family="self-improve",
    ),
    Loop(
        id="trace-miner",
        name="Trace-miner",
        summary="Daily syslog sweep groups recurring errors into signatures, "
        "scores them, emits fix-prompts, then passively verifies by watching "
        "for signature recurrence.",
        stages=(FRICTION, GATE, MEMORY),
        status=LIVE,
        components=("apps/extension/dev/trace-miner/",
                    "emptyos/sdk/miner_state.py"),
        family="self-improve",
    ),
    Loop(
        id="fix-drain",
        name="Fix-drain orchestrator",
        summary="Walks the pending fix-prompt queue, drives fix-agent per item "
        "under attempt/timeout budgets, and auto-reverts on verify-failed.",
        stages=(ACT, GATE, REVERT, BOUND),
        status=DARK,
        flag="apps.dogfood-agent.fix_agent_enabled",
        components=("apps/extension/dev/dogfood-agent/drain.py",),
        family="self-improve",
    ),
    Loop(
        id="bug-audit-convergence",
        name="Bug-audit convergence",
        summary="Outer loop: audit → drain → re-audit, self-terminating after "
        "two zero-new-fix rounds. The closest thing to a fully autonomous "
        "compounding loop.",
        stages=(FRICTION, ACT, GATE, MEMORY, BOUND),
        status=DARK,
        flag="apps.dogfood-agent.fix_agent_enabled",
        components=("apps/extension/dev/dogfood-agent/drain.py",),
        family="self-improve",
    ),
    # --- Family: agent (the interactive tool-loop) ---
    Loop(
        id="agent-tool-loop",
        name="Agent tool-loop",
        summary="The read→edit→test turn driver, with inner guards: error-loop "
        "stop-and-replan, edit-path limit, plan/refactor gates, /revert "
        "edit-history, and compaction→archive for episodic recall.",
        stages=(FRICTION, GATE, REVERT, MEMORY, BOUND),
        status=LIVE,
        components=("emptyos/sdk/agent_loop.py",),
        family="agent",
    ),
    Loop(
        id="episodic-memory",
        name="Episodic memory",
        summary="Agents recall their own past sessions; the reader that closes "
        "agent_loop's previously write-only compaction archive.",
        stages=(MEMORY,),
        status=LIVE,
        components=("emptyos/sdk/episodic.py",),
        family="agent",
    ),
    # --- Family: primitive (reusable SDK loop machinery) ---
    Loop(
        id="deep-loop",
        name="Deep-loop (gap-driven deepening)",
        summary="STORM-style: answer → gap questions → re-retrieve → merge, "
        "bounded by a deepening-round cap. Live in explore; dark in "
        "kb-gap-miner's research path.",
        stages=(FRICTION, ACT, BOUND),
        status=LIVE,
        components=("emptyos/sdk/deep_loop.py",),
        family="primitive",
    ),
    Loop(
        id="pipeline",
        name="Staged pipeline",
        summary="Resumable multi-stage generation: stop_after preview, "
        "resume-from-partial, budget pause. Persists run state.",
        stages=(GATE, REVERT, MEMORY, BOUND),
        status=LIVE,
        components=("emptyos/sdk/pipeline.py", "emptyos/sdk/run_registry.py"),
        family="primitive",
    ),
    Loop(
        id="run-budget",
        name="Per-run budget",
        summary="Per-run cost ceiling: estimate → reserve → reconcile, with an "
        "approval pause when a single action would blow the cap.",
        stages=(GATE, BOUND),
        status=LIVE,
        components=("emptyos/sdk/run_budget.py",),
        family="primitive",
    ),
    Loop(
        id="autopilot-budget",
        name="Per-actor monthly cap",
        summary="Monthly spend ceiling per actor — the outer budget the "
        "per-run ceiling nests inside.",
        stages=(GATE, BOUND),
        status=LIVE,
        components=("emptyos/sdk/autopilot.py",),
        family="primitive",
    ),
    Loop(
        id="miner-state",
        name="Miner state",
        summary="Shared plumbing for miner loops: signature hashing, "
        "scored-finding memory, recency scoring.",
        stages=(FRICTION, MEMORY),
        status=LIVE,
        components=("emptyos/sdk/miner_state.py",),
        family="primitive",
    ),
    Loop(
        id="shape-ledger",
        name="Shape-validation ledger",
        summary="Prediction ledger for the Tier-1 shape-validation gate: one "
        "generate→validate→verdict row per run — grade-your-own-prediction so "
        "the ability gate can relax on evidence.",
        stages=(GATE, MEMORY),
        status=LIVE,
        components=("emptyos/sdk/shape_ledger.py",
                    "engines/shape_validation/"),
        family="primitive",
    ),
    Loop(
        id="conformance",
        name="Conformance gate",
        summary="Objective numeric gate: an engine's output vs a published "
        "reference number — verified or it can't ship.",
        stages=(GATE,),
        status=LIVE,
        components=("emptyos/sdk/conformance.py",),
        family="primitive",
    ),
    # --- Family: self-audit (turn the tools on the system) ---
    Loop(
        id="preflight",
        name="Preflight self-audit",
        summary="Scope-gated runner for ~35 check-*/audit scanners; exit code "
        "= gate failures. Homes: /preflight (session start) + release gate.",
        stages=(FRICTION, GATE),
        status=LIVE,
        components=("scripts/preflight.py",),
        family="self-audit",
    ),
    Loop(
        id="insights-ledger",
        name="Insights ledger",
        summary="Each insights run records its proposals; the next run reads "
        "the scorecard first (still open / recurred N× / how old) — "
        "predict-and-check compounding.",
        stages=(FRICTION, MEMORY),
        status=LIVE,
        components=("scripts/insights_ledger.py",),
        family="self-audit",
    ),
    # --- Family: engineering (calculator variant) ---
    Loop(
        id="feature-pipeline",
        name="Conformance-gated repair",
        summary="Autonomous engineering repair: run conformance → fail → "
        "fix-prompt → fix-agent → re-run until pass or budget, bracketed by "
        "two human checkpoints.",
        stages=(FRICTION, ACT, GATE, BOUND),
        status=DARK,
        flag="apps.feature-pipeline.feature.eng-autopilot.enabled",
        components=("apps/extension/dev/feature-pipeline/conformance_gate.py",),
        family="engineering",
    ),
    Loop(
        id="rag-eval",
        name="RAG retrieval eval",
        summary="Retrieval-quality benchmark (MRR/recall/hit@1) over KB notes.",
        stages=(GATE,),
        status=LIVE,
        components=("apps/extension/dev/rag-eval/",),
        family="engineering",
    ),
    Loop(
        id="kb-gap-miner",
        name="KB gap-miner",
        summary="Sweeps Q&A history for repeatedly-asked questions the KB "
        "can't answer; proposes KB extractions into the review gate.",
        stages=(FRICTION, MEMORY),
        status=DARK,
        flag="apps.kb-gap-miner.enabled",
        components=("apps/extension/dev/kb-gap-miner/",),
        family="engineering",
    ),
    # --- Family: learning (adaptive reading feedback) ---
    Loop(
        id="adaptive-reading",
        name="Adaptive reading assistance",
        summary="Visible page text surfaces likely hard words; the reader can "
        "open, mark known/hard, or save them. Cloud consent and explicit modes "
        "gate action; feedback and a bounded derived-response cache tune later "
        "suggestions. Off/site-pause reverses the overlay, while text, item, "
        "scan-rate, and cache caps stop unbounded work.",
        stages=(FRICTION, ACT, GATE, REVERT, MEMORY, BOUND),
        status=LIVE,
        components=(
            "apps/extension/english-learning/dictionary/reading.py",
            "tools/chrome-extension/reading-assist.js",
            "tools/chrome-extension/sidepanel.js",
        ),
        family="learning",
    ),
    # --- Family: brand (the distribution engine) ---
    Loop(
        id="brand-distribution",
        name="Brand distribution engine",
        summary="A hand-written essay deploys → publish:deployed carries the "
        "newly-published slugs → promote auto-drafts LinkedIn/X/Reddit "
        "adaptations (publish adapt_* recipes, voice-aware) into pending review "
        "cards + a per-essay distribution tracker + a Telegram ping. The human "
        "Applies every outbound; the weekly devlog drafter covers shipped work. "
        "Bounded to the new posts per deploy, seeded on first deploy so it can't "
        "draft the back catalogue.",
        stages=(FRICTION, ACT, GATE, MEMORY, BOUND),
        status=DARK,
        flag="promote.distribution-engine.enabled",
        components=(
            "apps/extension/dev/promote/distribution.py",
            "apps/public/standard/publish/deploy.py",
            "apps/public/standard/publish/writer.py",
        ),
        family="brand",
    ),
]


# ─── Query helpers (pure) ────────────────────────────────────────────
def all_loops() -> list[Loop]:
    return list(LOOPS)


def by_id(loop_id: str) -> Loop | None:
    for lp in LOOPS:
        if lp.id == loop_id:
            return lp
    return None


def by_status(status: str) -> list[Loop]:
    return [lp for lp in LOOPS if lp.status == status]


def by_family(family: str) -> list[Loop]:
    return [lp for lp in LOOPS if lp.family == family]


def loops_covering(stage: str) -> list[Loop]:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    return [lp for lp in LOOPS if lp.covers(stage)]


def stage_coverage() -> dict[str, int]:
    """How many loops implement each stage — the 'is the loop complete as a
    system' view."""
    return {s: len(loops_covering(s)) for s in STAGES}


def summary() -> dict:
    """One-shot rollup for a CLI / panel / doc header."""
    return {
        "total": len(LOOPS),
        "live": len(by_status(LIVE)),
        "dark": len(by_status(DARK)),
        "doc": len(by_status(DOC)),
        "stage_coverage": stage_coverage(),
        "dark_flags": sorted({lp.flag for lp in by_status(DARK) if lp.flag}),
    }


# ─── LoopReceipt — the article's named proof object ──────────────────
@dataclass(frozen=True)
class LoopReceipt:
    """One captured pass through the test-fix-verify loop.

    The structured artifact "Loop Engineer, Not Prompt Engineer" names: where
    the friction came from, which fixes ran, which files changed, which gates
    fired, whether the sandbox verify passed, whether a revert happened, and
    what the system learned. Built from a real run — never hand-authored.
    """

    run_id: str
    friction_source: str  # e.g. "dogfood-agent" | "trace-miner"
    friction_text: str
    fix_attempts: int
    files_changed: tuple[str, ...]
    gates_fired: tuple[str, ...]  # e.g. ("py_compile", "sandbox-verify")
    sandbox_verified: bool | None  # None = verify never ran
    reverted: bool
    learned: str  # retry-context / lesson / outcome note

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "friction_source": self.friction_source,
            "friction_text": self.friction_text,
            "fix_attempts": self.fix_attempts,
            "files_changed": list(self.files_changed),
            "gates_fired": list(self.gates_fired),
            "sandbox_verified": self.sandbox_verified,
            "reverted": self.reverted,
            "learned": self.learned,
        }


def receipt_from_drain_state(state: dict) -> LoopReceipt:
    """Build a LoopReceipt from a real fix-agent / fix-drain ``run.json`` state.

    Reads the live fix-agent shape (``verify_context``, ``compile_check``,
    ``verify_result``, ``revert_commit``, ``diff_stat``) first, then falls back
    to simpler synthetic keys, so both a real run and a hand-built minimal state
    yield a receipt rather than raising. The status field drives
    ``sandbox_verified`` (``verified`` → True, ``verify-failed`` /
    ``verify-timeout`` → False, anything pre-verify → None) and ``reverted``.
    """
    status = str(state.get("status") or "")
    ctx = state.get("verify_context") if isinstance(state.get("verify_context"), dict) else {}
    compile_check = state.get("compile_check") if isinstance(state.get("compile_check"), dict) else {}
    verify_result = state.get("verify_result") if isinstance(state.get("verify_result"), dict) else {}

    verified: bool | None
    if status == "verified":
        verified = True
    elif status in ("verify-failed", "verify-timeout"):
        verified = False
    else:
        verified = None

    merged = bool(state.get("merged") or state.get("merge_commit")) or status in (
        "merged", "verifying", "verified", "verify-failed", "verify-timeout", "reverted"
    )
    ran_verify = verified is not None or bool(verify_result) or status == "reverted"
    gates: list[str] = []
    if merged or compile_check:
        gates.append("py_compile")
    if ran_verify:
        gates.append("sandbox-verify")

    # Changed files: prefer the py_compile file list (clean), then the
    # git diff --stat string, then synthetic keys.
    files = (state.get("files_changed") or state.get("changed_files")
             or compile_check.get("files"))
    if not files and isinstance(state.get("diff_stat"), str):
        files = [line.split("|", 1)[0].strip()
                 for line in state["diff_stat"].splitlines()
                 if "|" in line and line.split("|", 1)[0].strip()]
    files = files or []
    if isinstance(files, str):
        files = [files]

    # Friction source: dogfood persona runs carry persona/scenario in ctx;
    # trace-miner prompts carry an explicit source. Fall back to synthetic keys.
    source = (state.get("source") or state.get("friction_source")
              or ("dogfood-agent" if ctx.get("persona") else None)
              or state.get("persona") or "unknown")

    friction_text = (state.get("friction_text") or state.get("friction")
                     or ctx.get("key") or state.get("summary")
                     or state.get("filename") or "")

    # What the system learned: the verify summary, then a lesson/retry note,
    # then the fix commit message, then the terminal status.
    commits = state.get("commits") if isinstance(state.get("commits"), list) else []
    learned = (state.get("learned") or verify_result.get("summary")
               or state.get("retry_context") or state.get("outcome")
               or (commits[0] if commits else None) or status or "")

    return LoopReceipt(
        run_id=str(state.get("run_id") or state.get("id") or ""),
        friction_source=str(source),
        friction_text=str(friction_text),
        fix_attempts=int(state.get("attempts") or state.get("attempt") or 1),
        files_changed=tuple(str(f) for f in files),
        gates_fired=tuple(gates),
        sandbox_verified=verified,
        reverted=bool(state.get("reverted")) or status == "reverted"
        or bool(state.get("revert_commit")),
        learned=str(learned),
    )
