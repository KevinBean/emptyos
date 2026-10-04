---
name: eos-agent-diff-review
description: Review a changeset an AI agent (or an earlier session) wrote from a plan, by hunting specification loss — the behaviour the replaced code had that the spec never mentioned. Executes old-vs-new instead of reading the diff, because this bug class is invisible in a diff and ships green. Use when the user says "codex finished, review it", "review the agent's work", "another session wrote this, check it", "did the agent break anything", or hands over a commit implementing a plan. NOT a general correctness sweep (use eos-bug-audit), NOT a quality/convention pass on your own changed code (use eos-simplify), NOT a findings-list review of an arbitrary diff (use /code-review).
---

# EmptyOS Agent Diff Review

An agent implemented a plan. The plan was yours. The diff looks fine. **The diff always looks fine** — that is the defining property of the bug class this skill exists to find.

Protection engineering has the discipline already: you do not commission a relay because the schematic is correct. You inject a fault and watch it trip, then inject the condition it must ignore and watch it hold. A relay wired backwards passes every drawing review ever held.

The plan is the drawing. **This skill is the injection.**

## The one hard rule

**Never conclude from reading the diff. Execute the replaced behaviour.**

Every finding this skill has ever produced died to a two-line script running old and new against the same input. Every one of them survived a careful diff read first, by two different reviewers, with passing tests.

## What you are hunting: specification loss

A spec is a lossy compression of intent. **The loss is not random — it concentrates wherever a rich abstraction was replaced by a thin one**, because the thin one only reproduces the behaviour visible at the call site.

Calibration receipts (both real, both shipped green):

| Rich thing | Thin surrogate | What silently vanished |
|---|---|---|
| `Config.get()` | `tomllib.load()` | The `EOS_<KEY>` env-override layer. The demo container injects its auth token by env and keeps none in the TOML, so every CLI command there stopped sending an `Authorization` header. 37 tests passed. |
| "is this writer racing?" | `asyncio.Lock.locked()` | *Who* holds the lock. The RMW detector fired on every correctly-locked writer and stayed silent on the unlocked ones it existed to find. Zero tests, in a changeset that tested four other things. |
| "is this a dated snapshot?" | file `mtime` | **Caught** — the agent looked at the actual directory and found named workspaces beside dated ones. |

The third was caught because the rich fact sat one `ls` away. The first two were missed because the rich fact sat one method call away, in a file the implementer had no reason to open. **Distance to the truth predicts the miss.**

## Prerequisites

- **A changeset** (a commit sha, or working-tree changes) **and the plan it implemented** — the plan file, the session brief, or the commit body. Without the plan you are doing `/code-review`, not this.
- **Nothing to install.** Phase 3's injection is almost always a pure Python import of the old and new symbol; it needs no daemon and no browser.
- **If a check genuinely needs a live daemon**, never restart `:9000` or `:9001` — lease a sandbox member (`POST /sandbox/api/lease`, then `/restart` after edits, then `DELETE` the lease). See `.claude/rules/daemon-handling.md` and `.claude/rules/sandbox-usage.md`.
- **Check for parallel-session work** before staging anything: `git status --short`, stage only the files you touched.

## Phase 1 — reconstruct the spec, then distrust it

1. Find the changeset: `git log --oneline -5`, then `git show --stat <sha>`. If uncommitted, `git status --short` — and note which files are *yours* vs a parallel session's (`.claude/rules/environment.md`).
2. Find the plan it executed: the plan file, the session brief in `{vault}/10_Projects/emptyos/log/_next/`, or the commit body.
3. **Ask the question no role in the loop owns:** *what did the code being replaced actually do?*

If you wrote the plan, you inherit its blind spots and cannot review your way out of them. A fresh context window is not fresh eyes. Execution is the only escape.

## Phase 2 — locate the replacement sites

Read the diff **only** to find where something got swapped, never to judge whether the swap is correct.

```bash
git show <sha> | grep -nE "^-.*(Config|Session|Client|Manager|Registry)\(" # rich thing removed
git show <sha> | grep -nE "^\+.*(tomllib|json\.load|urllib|open\()"        # thin thing added
```

Flag every hunk where the `-` side went through a class, property, or SDK helper and the `+` side reaches a primitive directly. Those are your injection points. Also flag:

- A branch copied verbatim from the code being replaced (it may be dead — see the `status >= 500` check that can never fire because `urlopen` raises on ≥400).
- Any predicate standing in for a richer question (`locked()` for "who holds it", `exists()` for "is it valid", `mtime` for "is it a snapshot").

## Phase 3 — inject the fault (the load-bearing step)

For each site, run the old and the new against the same input and print both. Two lines is normal.

```python
python -c "
from emptyos.cli._common import bearer_headers   # new
from emptyos.kernel.config import Config          # old
import os; os.environ['EOS_NETWORK_AUTH_TOKEN'] = 'secret-from-env'
print('new:', bearer_headers(cfg))
print('old:', Config(cfg).auth_token)
"
```

Vary the input across: **present**, **absent**, **empty**, and **shadowed by env**. The bug lives in the case the spec never named.

For a **detector or predicate**, construct both cases explicitly — the one it must catch and the one it must ignore — and check it does not get them backwards. A detector is a claim about the world; the only way to check a claim is to build the world.

Never restart `:9000` to do this. Prefer a pure import, or lease a sandbox member (`.claude/rules/sandbox-driven-testing.md`). See `.claude/rules/daemon-handling.md`.

## Phase 4 — audit the tests it shipped

The agent's tests are **evidence of its model, not evidence about the code.** It cannot test a contract it never knew existed, so a green suite is not a signal here.

Score them:

- Does any test compare new behaviour against the thing it replaced? (Usually no. That absence *is* the finding.)
- Is the one behaviour with subtle semantics the one left untested? (It usually is — the implementer tested what it understood.)
- Does a test encode a claim about the world without constructing the negative case?

**Be ready to be wrong.** In one review the reviewer's finding ("key the throttle on `(handler, event_type)`") was refuted by a test the agent had written — three catch-all handlers across ~540 event names meant the reviewer's version would flood the log exactly when you were reading it to debug. A test written by a third party arbitrates between two parties who are both guessing.

## Phase 5 — fix at the root, then pin it

1. Restore the lost behaviour at the shared helper, not at each call site.
2. **Write the parity test the plan should have shipped**: run old and new against a matrix of inputs and assert they agree. This is characterization testing; what is new is knowing which line needs it — *any line where a rich thing became a thin thing.*
3. Run the scoped slice (`.claude/rules/testing.md`), then `check-personal` + `check-branding`.
4. Commit only the files you touched (`git add <paths>`, never `-A`); verify `HEAD` is yours.

## Report format

```
Agent Diff Review — <sha>, N files

Verdict: <faithful / faithful-with-defects / diverged from plan>

Specification loss:
  - emptyos/cli/_common.py — tomllib replaced Config.get, dropping the EOS_* env
    layer. Consumer: docker-compose.demo.yml injects the token by env.
    Proven by: old/new side-by-side, `bearer_headers()` returns {} vs the token.

Correct, and better than the plan:
  - run-folder sweep gated on name pattern, not mtime — the plan would have
    deleted a named workspace.

Test-coverage gaps (where the next bug will be):
  - zero tests compare _common against Config.

Fixed / Pinned / Flagged: ...
```

Lead with what the agent got *right* when it beat the plan. It calibrates the reader and it is usually true.

## When NOT to use this

- **Your own uncommitted work** → `/eos-simplify` (conventions + reuse) or `/verify`.
- **A general hunt for correctness bugs** in code nobody just rewrote → `/eos-bug-audit` (async wedges, RMW races, the scanners).
- **A findings-list review of an arbitrary diff** → `/code-review`.
- **A pure-addition changeset.** No code was replaced, so there is no surrogate and nothing to inject. Read it normally.
- **Architecture / wiring** → `/eos-architecture-review`.

## Anti-patterns

- **Merging on a green suite.** Both calibration bugs shipped green. The suite tests what the implementer understood.
- **Reviewing your own plan by re-reading it.** You will re-derive the same omission. Run something.
- **Blaming the model.** In both receipts the hole was in the *plan* — an implementer that had written the plan itself would have produced the same bug. Fix the loop (make the plan ship the parity test), not the vendor.
- **Copying a branch you did not understand.** If the diff preserves a check, verify it can fire.

## Cross-references

- `.claude/rules/debugging.md` — root cause before fix; this is its review-time twin.
- `.claude/rules/testing.md` — the four layers; a parity test is a unit test with a contract.
- `.claude/rules/audits.md` — false-positive discipline: test a heuristic against 3 healthy cases.
- `.claude/rules/daemon-handling.md` + `.claude/rules/sandbox-driven-testing.md` — where it is safe to execute.
- `.claude/rules/deep-research.md` — "read the primary source, don't infer from the tally" is the same move, applied to prose.
- `.claude/rules/dev-cli-dispatch.md` — the *upstream* decision (when to hand work to another CLI in the first place); this skill is the downstream review once it's done.
- Memory: `feedback_review_codex_output_by_executing`.
