# AI Agents — Setup Guide

This file is for AI coding agents that are **not Claude Code**. Claude Code reads `CLAUDE.md` automatically and needs no setup.

If you are Cursor, Windsurf, GitHub Copilot, Aider, Cody, Continue, or any other AI coding tool — this file tells you where EmptyOS keeps its context so you can self-configure.

## The Canonical Sources


| What | Where | Purpose |
|---|---|---|
| **System prompt** | `CLAUDE.md` | Full architecture, capabilities, conventions, development rules, gotchas |
| **Architecture doc** | `docs/DESIGN.md` | Deep design: runtime modes, capability system, consciousness model, UI philosophy |
| **Behavioral rules** | `.claude/rules/*.md` | Rules (docs-sync, vault-operator) |
| **Skills / procedures** | `.claude/skills/*/SKILL.md` | Reusable procedures |
| **Work loop** | `docs/ENGINEERING-WORK-LOOP.md` | How EmptyOS builds any feature: strategy → brainstorm → plan → work → review → compound-learning, each phase mapped to a live surface |
| **Public docs** | `README.md`, `docs/GETTING-STARTED.md`, `docs/APP-DEVELOPMENT.md` | External-facing |

Read `CLAUDE.md` first — it is the boot prompt. It loads the architecture, philosophy, and dev rules needed for coherent contributions.

> **Don't import an external "compound engineering" / agent-fleet framework.** EmptyOS already runs the full strategy→…→compound-learning loop, distributed across its own apps (`grill`, `app-builder`, `feature-pipeline`, `dogfood-agent`, `fix-agent`, KB lessons, `agent-bus`, session-wrapup). See `docs/ENGINEERING-WORK-LOOP.md` for the map and `docs/OPEN-SOURCE-BORROWING-PLAN.md` for why borrowed frameworks were taken as *ideas → docs*, not installed.

## Ground a plan before you propose it


Plans from non-Claude agents have repeatedly asserted file-level claims that a 30-second read refutes — a streak counter called an "orchestration loop", a "2+ consumers" threshold where the consumers were four different shapes, a "could be simplified" where the change would silently drop features. Run this pass before writing any plan (it is the non-Claude form of CLAUDE.md § Repo Evaluation + `.claude/rules/deep-research.md`):

1. **Closed-verdict check (borrows).** Before proposing to borrow from any external repo/library, run `python scripts/check_borrow_verdict.py <repo-or-library-name>` (greps `docs/OPEN-SOURCE-BORROWING-PLAN.md` + `docs/DEFERRED-WORK.md`; **exit 1 = a verdict already exists, STOP and read it**; exit 0 = clear to evaluate). Do not re-derive a closed verdict. (Two revisions of the same Prefect plan were re-derived against a verdict that already existed.)
2. **Read the file you cite — don't grep-and-assume.** Every "X is a consumer of pattern Y", "X already exists / doesn't exist", and "X could be simplified" claim requires **opening X and confirming it**. A `grep` hit is a lead, not a fact — `while True` is not "an orchestration loop".
3. **State what's lost.** A "could be simplified / replaced by Z" claim is invalid until you name what the current code does that Z drops (per-stage `weight`, `stop_after` preview, `RunBudget`, cross-stage state, …). If you can't name it, you haven't read it.
4. **Anti-abstraction gate.** Before proposing a new `sdk/` module or decorator layer, read CLAUDE.md rule 9 + the relevant `.claude/rules/*.md` (e.g. `staged-pipeline.md`). A second API over an existing primitive is drift, not reuse. The floor is **two real, same-shape consumers, proven by reading them** — not asserted.
5. **Grade your claims.** Mark each load-bearing claim in the plan `read-verified` or `unverified`. An `unverified` claim is a question for the user, not a finding to act on.

## Self-Configuration


Adapt the canonical sources to your own tool's format:

- **Cursor** — `.cursorrules` at root (condensed) or `.cursor/rules/*.md` (full). Copy the architecture + development rules from `CLAUDE.md`
- **Windsurf** — `.windsurfrules` at root. Similar to Cursor
- **GitHub Copilot** — `.github/copilot-instructions.md`. Auto-loaded
- **Aider** — `CONVENTIONS.md` at root, or pass `CLAUDE.md` via `--read`
- **Other** — consult your tool's docs for project-context conventions

Generated tool-specific files are yours to manage. Add them to `.gitignore` unless the team has agreed to commit them. The canonical sources are the ones listed above.

## Falling back from Claude Code (Codex / Antigravity CLI)

When Claude Code usage is constrained, a strong native agentic CLI is the
sanctioned fallback for **deep + agentic** work (coding *and* deep non-coding —
strategy, research, synthesis). These tools have their own Read/Edit/Bash loop;
they are **not** degraded the way an in-app `think` provider would be.

- **Codex CLI** (`codex`) reads **this file (`AGENTS.md`)** at the repo root. So
  follow the instruction above — **read `CLAUDE.md` first**, then the relevant
  `.claude/rules/*.md` for the task. Google **Antigravity CLI** is the Gemini-CLI
  successor; confirm its context-file convention from its docs on first use.
- **Routing:** send *narrow, well-scoped* work to the native CLI; reserve Claude
  Code for *wide* architectural sessions where its large context is the edge.
- **Proactive Reading & No New Rules:** Unlike Claude Code, your context does not magically auto-load the full contents of `.claude/rules/*.md` and `.claude/skills/*/SKILL.md`. To load them into your context, you **must** proactively use your tools: run `list_dir` (or `ls`) on `.claude/rules/` and `.agents/skills/` to find relevant filenames, use `grep_search` (or `grep`) for task-related keywords, and then use `view_file` (or `cat`) to read the actual file contents before you begin planning. Do **NOT** invent or write new rules and skills to compensate for your empty context — Claude Code reads the existing ones natively, so read what is already there first.
- **Sync `AGENTS.md` with `CLAUDE.md`:** Check the git commit history of `CLAUDE.md` against the last update of `AGENTS.md`. Review the recent diffs of `CLAUDE.md` and merge in new conceptual or architectural content only if it is relevant to non-Claude agents.
- **Same quality gate, model-agnostic:** run `build → conform → walk → simplify →
  commit → live-verify` (CLAUDE.md § EmptyOS Workflow). The tests catch a weaker
  model's mistakes regardless of which model wrote the diff.
- **Daemon hands-off still applies:** never restart `:9000`/`:9001`; lease a
  sandbox member (`.claude/rules/daemon-handling.md`).
- **Bounded vault/knowledge work needs no coding CLI at all** — use EmptyOS's own
  `assistant` / `staff` / `rooms` surfaces on a cheap `think` provider
  (openai-mini / local ollama), which offloads Claude entirely for those flows.

## Runtime vs Conversation


EmptyOS's runtime does not depend on any AI coding tool — it uses the `think` capability with swappable LLM providers (see `docs/DESIGN.md` "Capability System"). The AI coding tool only participates in **conversation mode**, which is where the system evolves (see `docs/DESIGN.md` "Three Runtime Modes").
