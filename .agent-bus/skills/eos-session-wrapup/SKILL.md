---
name: eos-session-wrapup
description: End-of-session housekeeping in sequence — docs-sync (patch CLAUDE.md and derived docs to match the codebase), personal-data + branding leak checks, record a dev log so it references accurate numbers, then commit scoped changes (POSIX git -m, parallel-session-safe) + push with upstream tracking, and live-verify against the :9000 daemon. Use at the end of a meaningful development session, or when the user says "wrapup", "wrap up", "session done", "devlog", "docs-sync", "sync docs", "commit and push", or "verify it works", or after adding/removing apps or plugins. Distinct from eos-week-review (personal life, not dev work).
---

# EmptyOS Session Wrapup

End-of-session housekeeping: sync docs, check for personal data leaks, then record what happened. Run all steps in sequence — docs-sync first (so CLAUDE.md reflects current state), safety checks second (catch leaks before they persist), then devlog (so the log references accurate numbers).

## When to Use

- End of a meaningful development session
- User says "wrapup", "wrap up", "session done", "devlog", "docs-sync", "sync docs"
- After adding/removing apps or plugins
- After any session that changed code

## Reference files (read on demand)

`SKILL_DIR` = `D:/emptyos/.claude/skills/eos-session-wrapup/`. The harness loads
only this file; read a sibling with the Read tool when you reach the step that
needs it.

| File | Priority | Read when |
|---|---|---|
| `<SKILL_DIR>/docs-sync-commands.md` | Required for Steps 1, 4, 5 | Before scanning manifests, gathering session data, or regenerating the site — it holds every command + the CLAUDE.md patch patterns |
| `<SKILL_DIR>/report-templates.md` | Required for every step's output | When writing a step's report, the devlog entry, or the next-session brief body |

---

## Process

### Step 1: Docs Sync — Update CLAUDE.md to match reality

Scan the codebase and patch documentation sections that are derived from code.
Read `<SKILL_DIR>/docs-sync-commands.md` §Step 1 for the scan commands, the
`release.toml` tier rules, the public-docs list, and the exact CLAUDE.md patch
patterns. Report per `report-templates.md` §Step 1; if counts already match,
report "no changes needed" and skip patching.

#### Safety rules

- **Never modify manual sections** — only patch lines matching exact count patterns
- **Preserve formatting** — only change numbers, not surrounding text
- Do not commit files that contain secrets

---

### Step 2: Release Safety Check

Run both safety scanners. If violations are found, fix them before proceeding.

```bash
python scripts/check-personal.py
python scripts/check-branding.py
```

**If violations found:** report each (file, line, pattern), fix them — replace
personal data with generic placeholders, branding with generic terms — then
re-run until clean. This ensures no personal information or unwanted branding
leaks into the codebase between releases.

---

### Step 3: Vault Ripple — Propagate facts that changed in this session

A Claude Code session produces three durable outputs: **code** (git + devlog), **behavioral learnings** (auto-memory), and **vault-tracked facts that changed** (this step). Steps 1+2 handle code-side state. This step handles vault-side facts so notes don't go stale silently.

**The ripple is NOT scoped to personal-life domains.** The test for "should this ripple" is **vault footprint**, not "code vs. life." Any durable fact a vault note references can go stale — a career posture *and* a renamed helper a KB `implemented_in:` points at. There are two classes, each with its own mechanism. A session can trigger one, both, or neither.

#### When to run — two fact classes

**Class A — personal-life facts** (career, status, finance, strategy, project, relationship, health). Routed to the `vault-info-ripple` skill (user-global skill — lives in `~/.claude/skills`, not in the repo). Signals:

- Career / posture changes — "switching to X", "stopped pursuing Y", "we decided to leave Z"
- Status changes — "the offer came in", "the interview happened", "RFI is now active", "deadline moved to…"
- Strategic decisions — "we're picking A over B", "dropping the C plan", "the experiment finished"
- Income / finance shifts — new salary number, expense pattern change, financial decision
- Project pivots — "Foo project is dead", "Bar is now the priority", "rebuilding Baz"
- Relationship / health updates — anything the user stated about people, body, energy

**Class B — system/technical facts that vault notes document.** A "pure-code" session routinely lands these, and they make KB / `docs/` / `.claude/rules/` notes silently wrong. This is exactly the gap a personal-life-only ripple misses — **don't skip it because the session was "just code."** Signals:

- A symbol a KB `formula`/`pattern` note's `implemented_in:` points at was **renamed, moved, or removed** (e.g. extracting a helper to the SDK, deleting an inline path).
- A pattern, convention, or approach a KB `lesson`/`pattern`/`concept` note (or a `.claude/rules/*.md` file) **teaches as current** was **superseded** — the note now describes how it *used* to work.
- An app / plugin / capability a vault note names was **retired, renamed, or had its contract changed**.
- A `docs/` page or a `_vault-map.toml` entry references a path / function / flag this session changed.

#### Skip when

Skip only when **no durable fact that any vault note references changed**. Genuinely skippable:

- Pure exploration without commitments ("let's investigate X" without a decision).
- Internal code with **no vault footprint** — a new app's manifest, a private refactor of a path no KB note cites, a UI tweak, a dependency bump, a scanner cleanup.

The discriminator is *vault footprint*: a refactor that renames a symbol a KB note cites HAS one (Class B); a refactor of an uncited internal helper does NOT. If nothing qualifies, **skip silently** — report "Vault Ripple: no fact changes this session, skipped." Do NOT invent fact changes to justify running.

#### How to run — route by class

**Class A → `vault-info-ripple` skill** (user-global skill — lives in `~/.claude/skills`, not in the repo). One-line summary per fact. It scans Kevin's personal-life zones for the **old** state, surfaces what's stale, proposes edits, and logs a wellbeing-tagged milestone to today's journal.

```
Skill: vault-info-ripple
Args: <one-line-per-fact summary of what changed>
```

**Class B → KB / docs / rules scan (NOT `vault-info-ripple`).** Its personal journal-milestone + wellbeing-dimension shape is wrong for "renamed a helper." Instead:

1. Run the mechanical KB integrity scanners (commands in `docs-sync-commands.md` §Step 3) — they catch broken claims / links deterministically.
2. For prose that *teaches* the now-changed fact (a `lesson` note describing the old pattern, a rule describing the old convention), targeted-`Explore` over `30_Resources/EmptyOS/kb/`, `docs/`, and `.claude/rules/` for the old name / approach. Surface stale notes with path + 1-line "what's now wrong."
3. **Surface, don't auto-edit.** Present the stale KB / docs / rules notes; the user approves. Same propose-before-apply discipline as `vault-info-ripple` and `.claude/rules/proposed-action.md`. (Note: `.claude/rules/*` are *code-side* docs synced in Step 1's spirit, but a KB `lesson` that teaches a now-dead pattern is a genuine vault fact — flag it here.)

#### Safety

- **Always surface before applying** — both classes propose; the user approves each batch. Wrapup never auto-applies vault edits.
- **One-line summaries, not transcripts** — feed distilled facts, not raw conversation (transcript-shaped is `vault-ai-conversation-digest`'s job, wrong tool here).
- **Don't double-write to the journal** — Class A logs to today's journal; Step 4 (devlog) writes the project log under `10_Projects/emptyos/log/`. Different files, no conflict. Class B does **not** write a journal milestone (it's not a wellbeing-shaped event).

---

### Step 3.5: Knowledge Capture — record what this session *learned*

Step 3 propagates facts that **changed**. This step captures a fact that is
**new** — a durable lesson the session produced that no note yet holds. Different
question, so a separate step; do not fold it into the ripple's staleness framing.

**Ask once:** did this session produce a transferable fact that outlives the
files it touched?

#### The bar — all three, or it isn't a lesson

1. **Transferable.** It holds beyond this session's files. *"The button was
   misaligned"* is not. *"A rendered-DOM audit must settle animations before
   measuring"* is.
2. **Statable in one sentence.** If you can't, it isn't a lesson yet — it's
   still a story. Write the sentence first; if it comes out as a narrative,
   skip.
3. **Not already held.** Grep the destination before proposing. Restating what
   a rule already says is not capture.

#### Qualifying shapes and where each belongs

| Shape | Destination |
|---|---|
| A **measured result that overturns an assumption** (INT8 beat fp16 3:29 vs 16:44 — paging, not arithmetic) | KB `lesson` note via `vault-note-factory`, **plus** a `.claude/rules/dev-gotchas.md` row if it will bite code again |
| A **defect class that will recur** (a check green because it checks nothing) | `.claude/rules/audits.md` or `dev-gotchas.md`; graduate to a real checker via `eos-graduate-audit` when the signal is mechanical |
| A **build / borrow / build-nothing verdict** | `docs/OPEN-SOURCE-BORROWING-PLAN.md`, **plus** a `docs/DEFERRED-WORK.md` row when it defers a substantive feature, **plus** a `*-borrow-verdict` memory |
| A **domain fact** (a clause's behaviour, a validation anchor, a method) | KB note via `vault-source-digest` / `vault-note-factory` |
| **Guidance about how I should work** (a correction that recurred) | auto-memory, `feedback` type — never the KB |

#### Two ordering constraints

- Runs **before Step 4**, so the devlog can name what was captured.
- If the destination is a **repo file** (`dev-gotchas.md`, a rule, CLAUDE.md,
  `DEFERRED-WORK.md`), the edit must land **before Step 7's commit** — otherwise
  the capture misses the commit and surfaces as an orphan diff next session.

#### Safety

- **Propose, don't write.** Name the candidate, its destination, and the
  one-sentence form; the user approves each. Same discipline as Step 3 and
  `.claude/rules/proposed-action.md`.
- **Skip loudly, and often.** "No durable lesson this session" is the common and
  correct outcome — 4 of 136 devlogs carry one, and that ratio is roughly
  honest. **Do not invent a lesson to justify the step.** A KB accreting a note
  per session is worse than one accreting none.
- **Not a transcript digest.** Same boundary as Step 3: distilled facts only.
  Turning a whole conversation into notes is `vault-ai-conversation-digest`'s
  job, invoked deliberately, not here.

---

### Step 4: Dev Log — Record what happened this session

Write a structured session summary to `{vault}/10_Projects/emptyos/log/YYYY-MM-DD.md`.
Gather the data with the commands in `docs-sync-commands.md` §Step 4 (which also
covers the append-don't-overwrite check for multi-session days), then write the
entry using the template + format rules in `report-templates.md` §Step 4.

Keep it brief: 10-25 lines. A record, not a tutorial.

---

### Step 5: Site Sync — Regenerate EmptyOS live site

If the vault is connected and the session changed app/plugin inventory,
capabilities, or system architecture, run the generator + rebuild trigger from
`docs-sync-commands.md` §Step 5. **Do not auto-push** — publishing to
`eos.binbian.net` is a deliberate user action.

If no changes detected → report "Site: up to date, no changes."

---

### Step 5.5: Close the claimed plan task

Skip entirely if this session didn't claim one (no plan was open at resume, or
the work was unbounded track work). Contract: `.claude/rules/session-plans.md`.

A conversation may have run **several claim cycles serially** (claim → close →
claim the next). Close **every** task it claimed, each with its own disposition
and the same `session` date. For each one, find its plan under
`{vault}/10_Projects/emptyos/log/_plans/` and:

1. Set that row's `status: done`, a `disposition`, and `session: <today>`.
   Dispositions (from `fix_queue.DISPOSITIONS`): `shipped` (built + landed) ·
   `deferred` (decided later — say where the trigger is recorded) · `declined`
   (decided against — say why) · `dismissed` (turned out unnecessary) · `done`
   (generic).
2. Clear `active_task: ""`. **Do this even when the task didn't finish** — leave
   `status: active` → `queued`, append what's left to the task cell, and clear
   the claim so the next session can pick it up. A stale claim blocks the plan.
3. **Close only the claimed task.** Work that turned out to be a different task
   gets **appended as a new row**, never folded into this one. Silently widening
   the claimed task is how the one-session-one-task constraint dies.
4. If that was the last open task → move the whole file to `_plans/done/<slug>.md`.
   The closed file **is** the record; there's no separate ledger. Say so in the
   report so the user sees the plan finished.

If a `blocked` row's blocker cleared this session, flip it to `queued` and drop
the `[blocked-human]` / `[decision-Kevin]` tag.

Report as: `Plan: <slug> · <id> → done (shipped) · N of M tasks closed` — or
`Plan: none claimed`.

---

### Step 6: Next Session Brief — Write a primer for the next conversation

While the session's context is still fresh, write a concise primer the **next** Claude can read to skip the warmup. Briefs are stored **per work track** so parallel tracks (engines, career, publish, infra) can't clobber each other when their wrapups land back-to-back.

```
{vault}/10_Projects/emptyos/log/_next/
  _index.md             ← list of active tracks + last_touched dates
  <track>.md            ← the on-deck card for that track
```

The skill writes/updates **one track's brief** per wrapup, plus the index. Other tracks are never touched. Long-form history lives in dated `YYYY-MM-DD.md` files; per-track briefs are just on-deck cards.

#### Pick the track

Identify which track this session advanced — by tags on the dated log, by the apps/files touched, or by asking the user if ambiguous. Common tracks at time of writing:

- `em-engines` — `engines/` work + cable/lightning/interference/earthing apps
- `career` — jobs app, outreach, applications, interview prep
- `publish-site` — `apps/public/standard/publish/`, `eos.binbian.net` content
- `core-infra` — kernel, SDK, capabilities, runtime, web framework
- `apps-other` — UI work on apps that don't fit the above

If the session straddles two tracks, write to **one** brief (whichever was the primary focus) and mention the secondary in that brief's "Open threads". If a brand-new track is needed, create the file with `track: <slug>` in frontmatter and add a row to `_index.md`. Slugs are kebab-case.

#### The frontmatter contract (`/eos-session-resume` reads these fields)

```yaml
type: next-session-brief
track: <track-slug>
written: <YYYY-MM-DD HH:MM>
last_session: <YYYY-MM-DD>
last_session_title: <Session Title>
threads_cleared: <N>     # open threads from previous brief resolved this session
threads_added: <N>       # new open threads introduced this session
threads_carried: <N>     # open threads still open at end of this session
```

The body (sections + a verbatim `git status --short` **working-tree snapshot**,
which resume diffs to detect drift) and the thread-count sourcing rules are in
`report-templates.md` §Step 6. Lead with concrete state, end with one specific
next move. Pad nothing.

#### Skip conditions

If the session was trivial (typo fix, single-line change, doc-only) write only the frontmatter + a one-line "Where things stand". Don't bulk it up.

If a previous brief exists for **the same track** and the session **didn't actually advance** any of its open threads (e.g. user pivoted to unrelated work *within the same track*), preserve the old recommended starting move alongside the new one — mark it `## Carried over from <date>`. Don't silently drop it.

This carry-over rule applies **only within a track**. If the session worked on a different track entirely, that other track's brief stays untouched at its existing path; nothing to carry over because nothing collided.

---

### Step 7: Commit & Push — Land this session's code changes

Run only after Steps 1–2 are clean (docs synced, secret + branding scans pass). This formalizes the standing practice "after a wrapup passes, commit the session's work" (memory `feedback_commit_after_wrapup`). The vault steps (3–6) write to the external vault, not the repo, so this commit is just the repo-side code + docs-sync edits.

Commands: `docs-sync-commands.md` §Step 7.

#### Safety — stage ONLY files scoped to this task

A parallel Claude session or Kevin may have staged/modified files concurrently. **NEVER `git add -A` / `git add .`** — see `.claude/rules/environment.md` § Parallel-session staging.

- Review `git status --short` first; stage explicitly by path, only what THIS session touched.
- Decline to stage unfamiliar changes that belong to another session — surface them in the report and leave them for their owner. (Tip: if you want Step 6's brief working-tree snapshot to read `(clean)`, run this commit before capturing that snapshot.)
- One logical change per commit; scope the message to exactly what was staged.
- If the push is rejected (remote moved under a parallel session), pull/rebase and retry — **never force-push**.
- Re-confirm `git log --oneline -1` shows YOUR commit at HEAD.

---

### Step 8: Live-Verify — confirm the change works against a running daemon

After committing, verify the session's changes actually work end-to-end. Probe
`GET http://127.0.0.1:9000/api/health`, then hit the route / page / CLI this
session touched and confirm the expected behavior (status code + response shape,
or visible UI result). Commands: `docs-sync-commands.md` §Step 8.

#### If offline — do NOT restart :9000 yourself

`:9000` is user-owned. Per `.claude/rules/daemon-handling.md`, Claude **never** runs `restart.bat` / `taskkill` / `python -m emptyos start` against it. Two valid paths:

- **Ask Kevin to restart** (`restart.bat`), then verify; or
- **Lease a sandbox member** and verify there without touching :9000 (`.claude/rules/sandbox-driven-testing.md`): `POST /sandbox/api/lease` → restart the member → probe its `<host>`.

---

### Step 9: Report Summary

After all steps, output the brief summary in `report-templates.md` §Step 9, then
suggest as a follow-up at next session start: `/eos-session-resume` (or
`/eos-session-resume <track>` to jump straight to a specific track).

## Vault Connection

This skill requires vault connection for the devlog step. Check `.claude/vault-connection.json` first.
The project log directory is: `{vault}/10_Projects/emptyos/log/`

## Relationship to Other Systems

- **Reactor journal ripple**: auto-logs individual commits as one-liners to daily journal (breadcrumbs, private)
- **This skill**: structured session summaries to project log + docs sync (end of session, private)
- **`/eos-devlog-publish`**: promotes session log sections into public posts on `eos.binbian.net` (discretionary, opt-in)
- **Git history**: raw commit messages (terse, code-focused)

Four layers: breadcrumbs (reactor) → private summary (this skill) → public post (eos-devlog-publish) → raw history (git). Each is deliberately separate so private dev notes never leak to the public site without an explicit promote step.
