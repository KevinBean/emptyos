# Missing Referenced Artifact — resolve, report, stop

When the user names a file, path, doc, or artifact that does not exist, the
failure mode is **not** "I couldn't find it." It is **substituting a plausible
scope and working confidently on the wrong thing.** An invented scope produces
real edits against imagined requirements, and the divergence stays invisible
until the user reads the diff — by which point the session is spent.

The rule: **never substitute a plausible scope for a named artifact that is
absent.** Resolve it against the repo's real convention, report the gap, and
stop.

## Verify absence properly — three checks, in order

"Not there" has three meanings and they lead to three different answers.

| Check | How | What it distinguishes |
|---|---|---|
| 1. Does the path exist? | `cat <path>`, `ls` | present vs absent |
| 2. Did it *ever* exist? | `git log --oneline -3 -- <path>` | **deleted or renamed** vs **never written** |
| 3. Is there a real home for this artifact class? | grep the repo + the map below | wrong-path typo vs genuinely unwritten |

**Skipping check 2 is the common miss.** A path with history was deleted by
someone — possibly this week, possibly by a parallel session — and
`git show <sha>:<path>` may recover exactly what the user meant. That is a
different conversation from one that was never written, and reporting the
second when the first is true wastes the recovery.

Check 3 is what turns "missing" into an actionable answer. A named artifact
almost always has a canonical home in this repo; the user pointing at the wrong
one is far more likely than the artifact not existing.

**The map below is a shortcut for check 3, never its definition.** An artifact
class absent from it means the map is incomplete, *not* that no convention
exists — go look (`grep`, `git log`, the skill that owns that artifact) before
concluding anything. Treating the table as exhaustive would reintroduce the
fall-through this rule exists to close. Verify a row before leaning on it; a
stale map inside a rule about not inventing is the rule undoing itself.

## Convention map — where EmptyOS artifacts actually live

| The user says | The real home |
|---|---|
| "next-session brief", "where we left off", "the handoff" | `{vault}/10_Projects/emptyos/log/_next/<track>.md`, indexed by `_next/_index.md`. **Never `docs/`.** Reach it with `/eos-session-resume [track]`. |
| "the session plan", "the task list for this" | `{vault}/10_Projects/emptyos/log/_plans/` — see `.claude/rules/session-plans.md` |
| "the devlog", "what happened on <date>" | `{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md` |
| "we said we'd build X later" | `docs/DEFERRED-WORK.md` |
| "the verdict on <external repo>" | `docs/OPEN-SOURCE-BORROWING-PLAN.md` + the `*-borrow-verdict` memories |
| "the app's design doc / why it exists" | `apps/<id>/INTENT.md` — see `.claude/rules/intent-doc-lifecycle.md` |

Add a row when a session burns time discovering one.

## Report shape

Three things, then stop:

1. **What is absent, and in which sense** — never existed / deleted in `<sha>` /
   exists at another path.
2. **The nearest real thing**, named by path, with the live options when there
   are several.
3. **What you need** to proceed — a name, a corrected path, or the scope in the
   user's own words.

Do not stage, edit, or "start on the obvious part while we sort this out." A
partial edit against an unconfirmed scope is the same bug, smaller.

## When NOT to apply this

- **The artifact is one you are about to create.** A path that does not exist
  yet is the entire point of a `Write`.
- **A single unambiguous near-match** — the user typed `sesion-brief.md` and
  exactly one neighbour exists. Open it, say which one you opened, continue.
  Two candidates is not unambiguous.
- **The path is a pattern, not a promise** — a glob, a scanner's walk over
  directories that may legitimately be empty, a fresh-clone check that should
  skip rather than fail (`.claude/rules/audits.md`).
- **The absence IS the finding** — an audit reporting a missing test, doc, or
  binding should record it and keep going, not halt.

## Lineage

`.claude/skills/eos-session-resume/SKILL.md` already carried this discipline
for exactly one artifact class: *"tell the user no brief was written by the last
wrapup and ask how they'd like to start. Do NOT invent context"* (and the
sibling clause for an index row pointing at a deleted track file). It held only
when the user invoked that skill. On 2026-08-28 a session opened with a bare
path — `docs/next-session-brief.md`, a file that has never existed in this repo —
and the discipline fired on judgment alone, with nothing written down behind it.
This rule is that clause lifted out of the one skill so it covers any missing
referenced artifact.

## Cross-references

- `.claude/skills/eos-session-resume/SKILL.md` — the original clause, and the
  right tool when the missing artifact is a session brief
- `.claude/rules/session-plans.md` — plans vs `_next/` briefs (task vs story)
- `.claude/rules/time-dimension.md` — read the past before acting; check 2 above
  is that rule applied to a single path
- `.claude/rules/debugging.md` — root cause before fix; same refusal to act on a
  guess, at the bug layer
- CLAUDE.md § Working Agreements — scope discipline: produce only the artifact
  that was asked for. This rule is its precondition — you cannot honour a scope
  you had to invent.
