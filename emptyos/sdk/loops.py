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

from dataclasses import dataclass

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
        "re-runs the scenario to verify a fix took. Its `bound` is wall-clock, "
        "and was under-declared here until 2026-08-01: `_RUN_TIMEOUT_S` "
        "(15 min) plus an `idle_timeout_s` (180 s default) kill a persona "
        "subprocess that stalls. Note what does NOT bound it: `budget_turns` in "
        "every scenario's frontmatter is **inert** — parsed by `_scenario_meta`, "
        "defaulted to 15, put in a dict, and read by nothing. 40+ scenarios "
        "declare a turn budget (12/15/16/18) that has never capped a run. A "
        "declared bound that does not bind is worse than none, because the "
        "author believes the loop is bounded; see docs/DEFERRED-WORK.md.",
        stages=(FRICTION, GATE, BOUND),
        status=LIVE,
        components=("apps/extension/dev/dogfood-agent/friction.py",
                    "apps/extension/dev/dogfood-agent/runs.py",
                    "apps/extension/dev/dogfood-agent/app.py"),
        family="self-improve",
    ),
    Loop(
        id="fix-agent",
        name="Fix-agent",
        summary="Turns a fix-prompt into a bounded patch in a worktree; "
        "py_compile-gates the merge; reverts the merge commit on bad verify. "
        "Its `bound` is wall-clock on BOTH halves and was under-declared here "
        "until 2026-08-01: the CLI subprocess is killed at `_RUN_TIMEOUT_S` "
        "(20 min, `shared.py`) → `status: timeout`, and the verify poll gives "
        "up at a 25-minute deadline (`verify.py::_poll_verify`) → "
        "`verify-timeout`, so a hung dogfood can never wedge it. Distinct from "
        "fix-drain's bound, which counts *attempts across* prompts; this one "
        "stops a single run that will not finish.",
        stages=(ACT, GATE, REVERT, BOUND),
        status=LIVE,
        components=("apps/extension/dev/fix-agent/runner.py",
                    "apps/extension/dev/fix-agent/merge.py",
                    "apps/extension/dev/fix-agent/verify.py",
                    "apps/extension/dev/fix-agent/shared.py"),
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
        "under attempt/timeout budgets, and auto-reverts on verify-failed. "
        "Its bound stage classifies rather than counts (2026-07-30): a prompt "
        "that fails the same STAGE twice is blocked as repeated_defect, so the "
        "blocked note says what kept breaking instead of just 'out of attempts'. "
        "Runs unattended on `fix_drain_schedule` since 2026-08-16 — until then "
        "only the GENERATOR was scheduled, so the queue filled nightly and "
        "drained only when a human ran it (measured: 15 pending, oldest 34 "
        "days, 17 of 34 closes done by hand). Two selection guards keep the "
        "unattended path honest: `source: ui-walk` and `kind: missing` are "
        "both skipped, because each needs a human decision the cron cannot "
        "make.",
        stages=(ACT, GATE, REVERT, BOUND),
        status=DARK,
        flag="apps.dogfood-agent.fix_agent_enabled",
        components=("apps/extension/dev/dogfood-agent/drain.py",
                    "apps/extension/dev/dogfood-agent/scheduled.py",
                    "apps/extension/dev/dogfood-agent/shared.py",
                    "emptyos/sdk/retry_policy.py"),
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
        status=DARK,
        flag="apps.agent.feature.episodic-memory.enabled",
        components=("emptyos/sdk/episodic.py",),
        family="agent",
    ),
    Loop(
        id="cockpit-attention-push",
        name="Cockpit attention push",
        summary="Cockpit watches every agent session's state; on a debounced "
        "transition into waiting/stuck it nudges the human via proactive_notify "
        "(dedup + the proactive gate) — the human is the actor that resumes the "
        "session. Bounded by a one-sweep debounce + the proactive daily cap.",
        stages=(FRICTION, ACT, BOUND),
        status=DARK,
        flag="apps.cockpit.feature.attention-push.enabled",
        components=("apps/extension/dev/cockpit/tailer.py",),
        family="agent",
    ),
    # --- Family: primitive (reusable SDK loop machinery) ---
    Loop(
        id="session-plans",
        name="Session plan claim/close",
        summary="Bounded problem → ordered session-sized tasks; resume claims "
        "exactly one (a per-row claim stamped `YYYY-MM-DD #sid8` before work), "
        "wrapup closes only that one with a disposition. Bounds by construction: "
        "a finite task list finishes and leaves the index, which is what a track "
        "(a work area) can never do.",
        stages=(FRICTION, ACT, GATE, REVERT, MEMORY, BOUND),
        status=LIVE,
        components=(
            ".claude/rules/session-plans.md",
            ".claude/skills/eos-session-resume/SKILL.md",
            ".claude/skills/eos-session-wrapup/SKILL.md",
        ),
        family="primitive",
    ),
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
    Loop(
        id="proactive-dispatch",
        name="Proactive dispatch gate",
        summary="The restraint machinery under every inbound nudge: master "
        "enable, per-kind mute, quiet hours, daily cap, min-gap, and dedup — so "
        "the system never trains the human to ignore it. The bound substrate the "
        "attention-push and other proactive loops pass through.",
        stages=(GATE, BOUND),
        status=LIVE,
        components=("emptyos/sdk/proactive.py",),
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
        id="kb-fact-integrity",
        name="KB fact integrity",
        summary="A standard's tables and equations, transcribed once and read "
        "from the page image: friction = the engine-backed unverified notes "
        "kb_verification_audit lists and the kb app's health buckets; gate = "
        "conformance `source` + the rendered table blocks + row provenance "
        "validated on load; memory = verified_against_pdf / checked dates. "
        "DOC because the act stage is a person or a session running "
        "eos-citation-verify on the page images — nothing here can mark a "
        "note verified (2026-09-30 IEC 60949 incident).",
        stages=(FRICTION, GATE, MEMORY),
        status=DOC,
        components=(
            "scripts/kb_verification_audit.py",
            "scripts/gen_kb_tables.py",
            "scripts/check_engineering_assurance.py",
            "engines/provenance.py",
            "apps/public/standard/kb/shared.py",
            ".claude/skills/eos-citation-verify/SKILL.md",
        ),
        family="engineering",
    ),
    Loop(
        id="correction-miner",
        name="Correction miner",
        summary="Repeated user corrections mined out of the agent's own "
        "transcripts, clustered by term-lift, proposed with quoted evidence. "
        "Accept/dismiss is durable, so a theme is judged once. Proposes only — "
        "the memory or rule is still hand-written.",
        stages=(FRICTION, GATE, MEMORY),
        status=LIVE,
        components=("scripts/mine_corrections.py",),
        family="self-audit",
    ),
    Loop(
        id="gate-driven-fix",
        name="Gate-driven fix loop",
        summary="Operator loop over the hard-gate scanners: an executable "
        "objective gate (check_done.sh) names 'done', each iteration fixes one "
        "gate finding, pins it with a regression test red-proven in BOTH "
        "directions, and commits citing that test. The gate is two halves on "
        "purpose — scanners alone can be satisfied by deleting the offending "
        "code, so it also re-runs every test the loop has added "
        "(.loop-regression-tests.txt). Bounded by a 15-iteration cap, a .STOP "
        "sentinel checked at the top of each pass, and the gate reaching 0. "
        "Receipts append-only via scripts/loop_receipt.sh. Its standing limit "
        "is ownership, not budget: a gate red because of ANOTHER session's "
        "uncommitted work is not fixable here and must be reported, not forced "
        "(CLAUDE.md § Parallel-session staging).",
        stages=(FRICTION, ACT, GATE, MEMORY, BOUND),
        status=LIVE,
        components=(
            "check_done.sh",
            "scripts/loop_receipt.sh",
            "scripts/preflight.py",
            ".claude/rules/gate-driven-fix-loop.md",
        ),
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
        id="model-probe",
        name="Local model ability probes",
        summary="Nightly deterministic probe suite over local think providers "
        "(JSON strictness, instruction-following, num_ctx needle, consistency, "
        "bilingual); scores → ability ledger → evidence-based tier overrides; "
        "notifies only on a regression. Local-only, budget-bounded.",
        stages=(GATE, MEMORY, BOUND),
        status=DARK,
        flag="apps.model-bench.feature.probe-autorun.enabled",
        components=(
            "apps/extension/dev/model-bench/probes.py",
            "apps/extension/dev/model-bench/graders.py",
        ),
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
    Loop(
        id="harness-compile",
        name="Bounded harness compilation",
        summary="Model Bench reflects over Hub's deterministic route fast path, "
        "scores isolated source candidates on frozen train/holdout cases, and "
        "retains lineage plus the winner for human review; it never hot-loads "
        "generated code into Hub.",
        stages=(FRICTION, ACT, GATE, MEMORY, BOUND),
        status=DARK,
        flag="apps.model-bench.feature.harness-compile.enabled",
        components=(
            "apps/extension/dev/model-bench/harness_compiler.py",
            "apps/extension/dev/model-bench/behavioural_runner.py",
        ),
        family="engineering",
    ),
    Loop(
        id="cad-guard",
        name="CAD shape-validation guard",
        summary="eos-cad's own author→validate→repair loop, layered on the "
        "shared Tier-1 shape-validation gate (see the `shape-ledger` primitive "
        "loop this rides for its `memory` stage). Friction: a hard shape "
        "violation (non-manifold, zero-volume) from compiling a proposed part "
        "through the cadquery venv. Act: `shared.build_cad_repair_user_msg` "
        "feeds the violations back for one regenerate. Gate: the same check "
        "runs again on the repaired tree; on the always-on side, "
        "`compiling.py::api_compile` also now genuinely refuses (`ok: False`) "
        "on a hard violation at export time, not just at propose time — closing "
        "the 2026-07-27 gap where the gate computed a verdict nobody read "
        "(`.claude/rules/model-ability.md` § Validity gate). Bound: "
        "`max_cad_guard_turns` (default 1 retry). The propose-time repair loop "
        "is dark (pays a real cadquery-venv compile per turn, unlike the "
        "pure/cheap 2D draft-guard sibling); the compile-time refusal is "
        "always on. Registered 2026-08-22 alongside the fix.",
        stages=(FRICTION, ACT, GATE, MEMORY, BOUND),
        status=DARK,
        flag="apps.cad.feature.cad-guard.enabled",
        components=(
            "apps/extension/engineering/cad/generate.py",
            "apps/extension/engineering/cad/compiling.py",
            "apps/extension/engineering/cad/shared.py",
            "emptyos/sdk/shape_ledger.py",
        ),
        family="engineering",
    ),
    Loop(
        id="cad-visual-critic",
        name="CAD visual critic",
        summary="A vision-LLM grades a screenshot of the current viewport "
        "render against the brief that produced it (topology/proportions/"
        "placement) — the gate structural validation can't express, since a "
        "part can be a perfectly valid solid and still be the wrong shape. "
        "Manual trigger, not an automatic retry loop: unlike robot-modeller's "
        "compile-time critic (its own `act`/`bound` inside the designer/"
        "builder loop), eos-cad's propose step has no rendered viewport to "
        "screenshot until AFTER a proposal is applied — so this is a "
        "check-against-intent button, gate-only, no wired act/bound. The "
        "mechanism (message-building, JSON parsing, signal formatting) is "
        "shared via `emptyos/sdk/visual_critic.py`, extracted from "
        "robot-modeller's critic.py as the second consumer (CLAUDE.md rule 9). "
        "Registered 2026-08-22.",
        stages=(GATE,),
        status=DARK,
        flag="apps.cad.feature.visual-critic.enabled",
        components=(
            "emptyos/sdk/visual_critic.py",
            "apps/extension/engineering/cad/generate.py",
            "apps/personal/robot-modeller/critic.py",
        ),
        family="engineering",
    ),
    Loop(
        id="kb-butler",
        name="KB butler",
        summary="Autonomous KB-maintenance loop (trace-miner-shaped): a "
        "scheduled sweep of kb.health() surfaces findings, triages them, repairs "
        "symmetric backlinks, drafts proposals into the review gate, and reflects "
        "on corpus convergence. Bounded by the cron cadence + reflection schedule.",
        stages=(FRICTION, ACT, GATE, MEMORY, BOUND),
        status=DARK,
        flag="apps.kb-butler.enabled",
        components=("apps/extension/dev/kb-butler/",),
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
            "apps/public/englishos/dictionary/reading.py",
            "tools/chrome-extension/reading-assist.js",
            "tools/chrome-extension/sidepanel.js",
        ),
        family="learning",
    ),
    # --- Family: brand (content quality + distribution) ---
    Loop(
        id="mv-production",
        name="MV production gates",
        summary="The staged music-video pipeline's own gate/revert half: six "
        "stages can stop a run (creative-basis, plan-review, art-review, "
        "still-review, motion-proof, clips) ordered cheap-first, with "
        "per-scene and per-segment checkpoints so a rejection rewinds one "
        "scene rather than the film. Registered 2026-07-28 after an audit "
        "found the most expensive human-in-the-loop process in the system was "
        "the one never modelled as a loop. Its `bound` stage was rebuilt "
        "2026-08-01: `motion-proof` used to raise on any partial pass, so a "
        "run that had already paid the GPU to learn which scenes cannot be "
        "animated threw that away and died — three of the four video runs on "
        "disk stopped exactly there, and `motion-audition`, the one recovery "
        "stage, sits downstream and was unreachable by the failure it "
        "recovers from. A partial proof now persists the failing scenes and "
        "pauses for a human; only a zero-pass proof is a failure.",
        stages=(GATE, REVERT, BOUND),
        status=LIVE,
        components=("apps/personal/music-studio/visual.py",
                    "docs/MV-GENERATION-WORKFLOW.md"),
        family="brand",
    ),
    Loop(
        id="mv-blockout-authoring",
        name="MV blockout authoring retry",
        summary="Author camera geometry -> render depth -> gate -> re-author "
        "with the gate's own complaint. Registered 2026-07-30 when its bound "
        "stage stopped being a flat 3-attempt ceiling: a defect the author "
        "keeps reproducing now stops the loop as repeated_defect instead of "
        "buying two more renders, while a shrinking defect set is allowed to "
        "keep converging. ~10s CPU per cycle against ~4min GPU for the clip "
        "it protects.",
        stages=(FRICTION, ACT, GATE, BOUND),
        status=DARK,
        flag="apps.music-studio.feature.geometry-camera.enabled",
        components=("apps/personal/music-studio/blockout.py",
                    "scripts/check_depth_sequence.py",
                    "emptyos/sdk/retry_policy.py"),
        family="brand",
    ),
    Loop(
        id="mv-stale-run-sweep",
        name="MV stale-run sweep",
        summary="A render that dies with the daemon leaves status=running on "
        "disk forever — Pipeline._drive can only record an error from inside a "
        "live except, and every Resume surface filters on paused|error, so the "
        "orphan is unreachable while claiming to be live. A boot sweep marks "
        "it interrupted (keyed off state-file mtime, skipping anything in the "
        "in-memory active map) so the existing surfaces find it.",
        stages=(FRICTION, REVERT),
        status=LIVE,
        components=("apps/personal/music-studio/app.py",
                    "emptyos/sdk/run_registry.py"),
        family="brand",
    ),
    Loop(
        id="mv-reference-pack",
        name="MV reference-pack gate",
        summary="Validates the production input that decided the most and was "
        "governed by nothing: identity anchor + manifest hashes + review "
        "verdicts, plus consistency with the scene plan (an anchor against a "
        "plan where no scene can show a face is a hard fail). Absent manifest "
        "is legacy, not failure, so the existing catalogue still renders.",
        stages=(GATE,),
        status=DARK,
        flag="apps.music-studio.feature.reference-pack-gate.enabled",
        components=("apps/personal/music-studio/visual.py",),
        family="brand",
    ),
    Loop(
        id="mv-plan-review",
        name="MV plan review (pre-GPU art gate)",
        summary="One text-only model call comparing the storyboard with "
        "art-direction.md before any image is generated — catches a scene that "
        "contradicts its own contract for a fraction of a cent instead of a "
        "full still pass. Fails open on a reviewer outage; violations feed the "
        "art ledger.",
        stages=(GATE,),
        status=DARK,
        flag="apps.music-studio.feature.plan-review.enabled",
        components=("apps/personal/music-studio/visual.py",
                    "apps/personal/music-studio/prompts.py"),
        family="brand",
    ),
    Loop(
        id="mv-final-art-review",
        name="MV final-master art review",
        summary="The loop that judges a finished master and sends it back: three "
        "separate verdicts (technical / asset-finish / art), a shot-disposition "
        "pass, and a director revision doc that names each rejected element and "
        "its replacement. It is the only MV loop that has ever caught a "
        "capability-reel result — v5 of Log Out passed every automated gate and "
        "was rejected by hand the next day, producing v6. Registered as DOC "
        "because the acting half is a human reading frames: the gates report, the "
        "revision doc records, and nothing automates the judgement. Two real "
        "weaknesses, both measured 2026-07-31: coverage is unenforced (the v6 "
        "review sampled no frame across three of eight shots, so its verdict "
        "covered 5/8 and did not say so), and the review reads the prohibition "
        "documents without the intent documents — an audit that did exactly that "
        "called an authored silhouette motif a stray proxy and blocked a correct "
        "release. Pixel evidence settles existence, never legitimacy. A third, "
        "measured on 梦幻泡影 the same day, is why this loop carries no `bound`: "
        "it has no termination condition at all, and it shows. Scene 05 ran "
        "blender-repair v1 through v8; `final-review.json` records three "
        "director sessions, every one `request_changes`, and **no `approve` "
        "verdict was ever reached** — the film shipped anyway, by another route, "
        "while its run sat at `motion-proof` 52%. A human loop still needs a "
        "stopping rule; exiting sideways is not convergence, and nothing here "
        "distinguishes 'the director is still iterating' from 'this loop is "
        "stuck'. `retry_policy.classify_retry` is the shape that would answer "
        "it, but the per-attempt defect set it needs is not recorded here.",
        stages=(GATE, REVERT, MEMORY),
        status=DOC,
        components=(".claude/skills/tool-blender-comfyui-video/references/"
                    "asset-finish-gate.md",
                    ".claude/skills/tool-blender-comfyui-video/references/"
                    "art-direction-from-technical-master.md",
                    ".claude/skills/tool-blender-comfyui-video/scripts/"
                    "check_hybrid_video.py",
                    "emptyos/sdk/media/review.py"),
        family="brand",
    ),
    Loop(
        id="mv-art-ledger",
        name="MV art-direction ledger",
        summary="Cross-song memory for the MV pipeline: one row per art "
        "verdict with the contract clause it violated, and a recurring-failure "
        "block seeded into the next song's reviewer once the same code has "
        "been rejected on two different songs. Closes the `memory` gap that "
        "made every MV start from zero. Registered LIVE 2026-07-28 and audited "
        "2026-07-31, which is the lesson: a registered stage is not an earning "
        "one. It was writing rows that taught nothing — 219 of 225 had an "
        "empty contract_clause because ART_REVIEW (96% of rows) never asked "
        "the model for one, 66 were unit-test fixtures written into the real "
        "ledger by a hardcoded repo-root path, and one song counted as two "
        "under its dated and undated names, halving the distinct-song bar. "
        "All three fixed 2026-08-01; check the data before trusting the flag.",
        stages=(MEMORY,),
        status=LIVE,
        components=("emptyos/sdk/art_ledger.py",
                    "apps/personal/music-studio/visual.py"),
        family="brand",
    ),
    Loop(
        id="publish-framework-eval",
        name="Publish framework evaluator",
        summary="Grades an article draft against its declared framework via a "
        "multi-lens LLM scorecard with deterministic validation (the model "
        "judges; code checks the shape) — a content-quality gate before publish, "
        "with the scorecard persisted for the next draft.",
        stages=(GATE, MEMORY),
        status=DARK,
        flag="apps.publish.feature.framework-eval.enabled",
        components=("apps/public/standard/publish/framework.py",),
        family="brand",
    ),
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
