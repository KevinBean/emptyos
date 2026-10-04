---
paths:
  - "scripts/shard_ledger.py"
  - "emptyos/sdk/worktree.py"
---

# Parallel Shard Runs — N agents, N worktrees, one merge arbiter

A **shard run** fans a backlog out across several agents, each in its own git
worktree, with the main session acting as **merge arbiter**: rebase, gate,
merge or send back. It is the right shape when the work is genuinely
independent and file-disjoint, and the wrong shape for anything landing in
shared code.

Written 2026-08-28 from the first real run (5 shards against the app
gap-analysis registry). Everything below is measured on this repo, not
inferred — the numbers are what forced the design decisions.

## When to shard / when not

**Shard** when each unit touches exactly one app directory, the units have no
ordering dependency, and each is provable by a daemon-free test.

**Do not shard** when the fix lands in `emptyos/sdk/**`,
`emptyos/web/static/**`, `emptyos/kernel/**`, or a manifest/loader schema.
Those are shared by construction — "disjoint shards" is not achievable there
and two agents will silently write over each other. Route them to a serial
pass. Say this in the shard brief, so an agent that *discovers* it needs a
shared file stops and reports instead of doing it.

Bound the run by a **stated budget** (N items, a time box), never by backlog
exhaustion — CLAUDE.md § Autonomous Loops. A registry with 436 open rows is not
a session.

## Selecting shards

- **One app directory per shard**, and check candidates against
  `git status --short` first: never shard onto a file another session has
  dirty.
- **Name-scope every new test file** — `tests/test_unit_gap_<shard>.py`. All
  shards share `tests/`, so that directory is only disjoint if the filenames
  are.
- An **optional, defensively-read** manifest key is fine (apps that do not
  declare it are simply inferred). Changing the manifest *schema* is not.

## The verification contract — the part that surprises people

**The full test suite cannot gate inside a worktree.** `tests/conftest.py` pins
`BASE_URL` to a fixed `localhost:9000`, and 231 of 875 test files are
daemon-backed. A worktree agent running them tests **main-tree code, not its
own** — a false green exactly where the change lives. Worse, N agents each
write `TEST_PREFIX` fixtures into the *real* vault while the cleanup sweep is
per-session, so they clobber each other's fixtures mid-run.

The shard gate is therefore the **daemon-free subset only**:

```bash
FILES=$(ls tests/test_unit_*.py tests/test_sdk_*.py | grep -v -E 'test_unit_search_(candidates|voice)\.py')
python -m pytest $FILES -q --timeout=300
```

Three non-obvious things in that command are load-bearing:

1. **A collection error aborts the entire run.** Two files
   (`test_unit_search_candidates.py`, `test_unit_search_voice.py`) fail to
   import on main, so a naive glob runs *zero* tests and reports red. Every
   shard would have read that as its own damage.
2. **`--ignore` is inert against explicitly-named files.** The shell expands
   the glob before pytest sees it, so `--ignore` silently changes nothing —
   the fix that looks right and does nothing. Filter the *file list* in the
   shell instead.
3. **Raise `--timeout` when gates run concurrently.** Two full suites on one
   box will trip a 120s per-test limit on slow-but-healthy tests and
   manufacture failures. A false red at the merge gate is worse than a slow
   gate.

Daemon-backed tests belong to the arbiter, run serially on merged main where
`:9000` actually serves the merged code — never to a shard.

## Baseline first: the gate is differential, not absolute

**"Merge only if green" is usually not achievable.** Measured here: the
daemon-free suite is already **35 failed / 8834 passed / 563 skipped** on an
untouched base commit.

So run the gate on the **base commit in a clean worktree** before dispatching,
save the failing node ids, and gate each shard on *no failure outside that
set*. Then confirm no baseline-failing file belongs to a shard's app — when
none does, attribution is clean and any new failure is unambiguously the
shard's.

Tell the agents the baseline set explicitly. An agent seeing 35 red that does
not know they are inherited will try to fix them, or keep "fixing" its own
change until something turns green.

Watch for baseline failures that are themselves **gates**. In this run
`test_unit_check_undefined_names.py::test_repo_is_clean` was already failing,
so the repo-wide undefined-name protection was off and every agent had to run
`pyflakes` on its own changed files instead. A red gate in the baseline is a
missing safety net, not just a red row.

## The ledger must be append-only

Status writes from N concurrent agents need `open(path, "a")` plus one JSON
line per row — the `emptyos/sdk/fix_queue.py::append_ledger` idiom.
`scripts/shard_ledger.py` is the generic form (`append` / `status`, last row
wins per shard).

**Do not use `scripts/insights_ledger.py` for this.** It persists through
`miner_state.save_state`, a whole-file `write_text` rewrite — a read-modify-
write that loses entries under concurrency. Reconciling shard rows into the gap
registry or the insights `gap` lens is the **arbiter's** job, done serially
after the run, never an agent's.

## The merge arbiter

Merge in a dedicated worktree, or in the main tree only when it is clean —
never rebase/merge in a main tree carrying another session's uncommitted work.

Per shard: rebase onto main → verify the shard's changed-file set does not
intersect the dirty set → run the gate → diff failures against baseline →
`git merge --ff-only`.

**Main will move during a 6-minute gate.** Re-running the gate on every rebase
is an unwinnable race against an active parallel session. What settles it is
cheaper and stronger: check whether the intervening commits touch any file the
shard touches. If they do not, the gate result still holds and the merge is
sound; if they do, re-gate. In this run main moved twice mid-gate and the
intervening commits hit only `scripts/` and a rule file.

A purely **additive** intervening change — another shard's new, independent
test file — does not invalidate a gate either.

## What to require of a shard agent

- **Confirm the gap is real by reading the code before building.** Registry
  rows can describe work that is partly done, or explicitly advise *not*
  building.
- **Prove the test fails without the fix, by mutation, once per behaviour
  claimed** — not once overall (`.claude/rules/audits.md` § Failure mode 3).
  This run caught two decorative tests that way, each passing under mutation
  for an incidental reason. Module-absence alone proves almost nothing: a new
  module's tests trivially error at collection.
- **Fix your code, not the pre-existing test.** A new `app_config` read inside
  an existing code path will break tests that construct the app via
  `object.__new__`; degrade the flag read rather than editing the test.
- **Dark by default** for anything changing existing behaviour, with the
  off-state stated as byte-identical.
- **Report the failure set, not a verdict** — the arbiter does the diff.

Expect an agent to die mid-run on an API error. Resume it from its transcript
rather than re-dispatching; it keeps its context and its worktree.

## Cross-references

- `.claude/rules/test-fix-verify-loop.md` — the single-fix worktree loop this
  generalises; its regression gate documents the same
  daemon-backed-cannot-gate rule.
- `.claude/rules/audits.md` § Failure mode 3 — green-because-it-checks-nothing;
  the mutation requirement above is its per-shard form.
- `.claude/rules/environment.md` § Parallel-session staging — never
  `git add -A`; stage and commit in one chained command.
- `.claude/rules/daemon-handling.md` — `:9000` / `:9001` are the user's; a
  shard agent never restarts, kills, or writes to them.
- `emptyos/sdk/worktree.py` — worktree creation + `py_compile` pre-merge gate,
  already shared by `app-builder` and `fix-agent`; reuse rather than re-roll.
- `.claude/rules/sandbox-usage.md` — when a shard genuinely needs a daemon,
  lease a pool member with `source_root` pointed at its worktree.
