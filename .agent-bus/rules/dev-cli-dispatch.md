---
paths:
  - "plugins/agent-runtime/**"
  - ".claude/skills/eos-agent-diff-review/**"
---

# Dev CLI Dispatch — when a Claude Code session hands work to another CLI

This is a **session-workflow** rule, not an app feature. `.claude/rules/multi-cli-participants.md`
documents how the *rooms app* spawns claude-cli/codex/gemini as chat participants at
runtime. This rule is the sibling for the *other* direction: when Claude Code, working
on the EmptyOS codebase itself, should shell out to another coding-agent CLI for a
subtask instead of doing the work in-session. Same binaries, same landmines, different
layer — don't conflate the two.

## The routing table

| Task shape | Handle in-session (Claude) | Dispatch to another CLI |
|---|---|---|
| Architecture decisions, judgment calls, anything touching CLAUDE.md conventions or `.claude/rules/` | ✅ — deepest context on EmptyOS's own doctrine lives here | — |
| Mechanical bulk edits (rename across N files, boilerplate scaffolding, repetitive find/replace with a clear spec) | — | Codex — cheaper, parallelizable, EmptyOS already treats its output as review-by-execution, not trust |
| Long-document ingestion / multimodal at volume (huge PDFs, many screenshots) | — | Gemini, if configured — long context, native multimodal |
| Anything ambiguous, anything the dispatched CLI would need to *infer* an EmptyOS convention rather than follow an explicit spec | ✅ | — |

When in doubt, keep it in-session. The dispatch only pays off when the subtask is
genuinely mechanical **and** the spec is explicit enough that no judgment call is
needed mid-task — Codex garbling a convention it had to guess at costs more in review
time than doing the edit directly would have.

## Review by execution, never by trust

Dispatched output is never merged on the strength of "it looks right." Run it, diff
it, verify it does what it claims — the `eos-agent-diff-review` skill is the
downstream mechanism for exactly this (it hunts for *specification loss*: behavior the
replaced code had that the diff never mentions, invisible to a plain read-the-diff
pass). Memory: `feedback_review_codex_output_by_executing` records this as a repeated,
confirmed practice — roughly 85% right, with failures clustering in predictable
places rather than randomly, which is what makes the review pass tractable rather than
a full re-derivation.

## Mechanism — and the landmines are identical to rooms' CLI participants

Dispatch is a **non-interactive** subprocess call (`Bash`, or a `fork` agent that
shells out), mirroring the shape `agent-runtime`'s CLI adapters already use for room
participants. Because it's the same binaries on the same machine, the Windows-specific
failure modes documented in `.claude/rules/multi-cli-participants.md` apply
identically here — they are not a rooms-app quirk, they are a property of the CLI
itself:

- **A multi-line prompt on argv silently truncates** when the resolved binary is an
  npm `.CMD` shim (`shutil.which`/`where codex` → `codex.CMD`, which runs through
  `cmd.exe`, where a newline terminates the command). The CLI exits 0 and answers
  fluently — to only the first line. Prefer piping the prompt on **stdin**
  (`codex exec -`) over passing it as an argv string when the prompt is anything but a
  single line.
- **`-s read-only` / sandbox flags are not a containment boundary on Windows.**
  Measured against codex-cli: the sandbox helper can error, and the command then
  re-runs *without* the sandbox and succeeds — a clean "sandbox: read-only" log line is
  not evidence a write was prevented. Don't dispatch anything you wouldn't be
  comfortable having write to disk; the flag is a hint, not a guarantee.

## Cross-references

- `.claude/rules/multi-cli-participants.md` — the rooms-app runtime plumbing (adapters,
  streaming, the same Windows landmines) this rule's dispatch mechanism mirrors.
- `.claude/skills/eos-agent-diff-review/SKILL.md` — the review-by-execution pass for
  whatever a dispatched CLI produced.
- `.claude/rules/debugging.md` — root-cause discipline; applies once a dispatched
  change is found wrong, same as any other bug.
- Memory: `project_claude_code_fallback_plan`, `feedback_review_codex_output_by_executing`.
