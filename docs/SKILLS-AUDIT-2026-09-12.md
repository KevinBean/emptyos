# Skills Audit — 2026-09-12

Evaluation of every skill visible from this machine: 80 project skills in
`.claude/skills/`, their 66 Codex twins in `.agents/skills/`, 38 bundled
skills in `skills/`, and 46 user-global skills in `~/.claude/skills/` —
118 distinct project/bundled slugs, 130 distinct including the 12 that exist
only in the user-global store.

Method: the four existing skill checkers (`check_skills.py --strict`,
`check_skill_script_sync.py`, `check_skill_vault_sync.py`,
`check_skill_security.py`) as the baseline, plus five measurements those
checkers do not make — dead path references inside skill bodies, invocation
history from this project's session logs (2026-08-11 → 2026-09-12, 1,268
transcripts), size against the 250-line progressive-disclosure threshold,
harness visibility per tree, and trigger-phrase collisions across
descriptions. Every finding is graded `counted` (a script measured it),
`read-verified` (a file was opened and read), or `inferred` (judgment from
descriptions alone).

Baseline: the 2026-06-08 skill audit was graduated into `check_skills.py`.
Its hard contract holds — 0 frontmatter failures across 146 dirs. The
findings below are all in classes that checker does not cover.

---

## 1. Verdict in one table

| Class | Count | Severity | Where |
|---|---|---|---|
| Bundled skills **invisible to Claude Code** (only in `skills/`, never synced to `~/.claude/skills`) | 4 | high | § 2 |
| Skills **only in the per-machine user store**, referenced by 24 repo files, not in git | 12 | high | § 2 |
| Mirror drift `.claude/` vs `.agents/` (Codex twin behind, one sibling missing) | 6 files | med (gated) | § 3 |
| Bundled ↔ user-global drift (`sync_user_skills.py` not run since edits) | 20 files | med | § 3 |
| `.agent-bus` mirror out of sync | 2 | med (gated) | § 3 |
| Dead path references inside skill bodies | 16 skills / 45 refs | med | § 4 |
| Routing target that names a skill that no longer exists | 1 | med | § 4 |
| Trigger collisions — one user phrase claimed by 3+ skills | 5 phrases | med | § 5 |
| Descriptions with no NOT-for boundary | 14 | low | § 5 |
| Descriptions over the 1024-char Agent Skills spec limit | 7 | low | § 5 |
| Over 250 lines with no reference siblings | 22 | low | § 6 |
| Daemon-hitting skills with no prerequisites section | 5 | low | § 6 |
| Never invoked in the 33-day log window | 96 of 130 | info | § 7 |
| Security scan high band | 0 | — | clean |

No skill is broken at the frontmatter level. The real defects are
**distribution** (§ 2) and **drift** (§ 3): the skill set is authored in four
trees with three different sync mechanisms and no single owner, so the
harness on this machine sees a different set than the daemon agent, than
Codex, than a fresh clone.

---

## 2. Distribution — which tree does each reader actually see  `read-verified`

`apps/public/standard/agent/skills.py:39-42` reads all four roots (bundled →
`.agents` → `.claude` → user, later wins). Claude Code reads only
`.claude/skills/` + `~/.claude/skills/`. Codex reads only `.agents/skills/`.
`scripts/sync_user_skills.py` copies **user → bundled** (its docstring: the
product skills "are authored in the per-machine store and mirrored into the
git-tracked `skills/` tree"). Nothing copies bundled → user.

### 2a. Four bundled skills Claude Code cannot see

`geo-spatial-analyst`, `growth-content-strategist`, `growth-hacker`,
`growth-seo-specialist` exist in `skills/` (committed 2026-06-22) and nowhere
else. They were authored in the repo, so the user→bundled sync never carries
them into `~/.claude/skills`, and they are absent from this session's skill
listing. `eos-geo-audit` routes to two of them ("use geo-spatial-analyst",
"use growth-seo-specialist") — a route Claude Code cannot follow.

Fix: either copy them into `~/.claude/skills` once (and let the existing sync
keep them aligned), or move the four into `.claude/skills/` + `.agents/skills/`
like every other repo-authored skill. The second is cleaner: `skills/` then
stays what its docstring says it is — the mirror of the user store.

### 2b. Twelve skills exist only in `~/.claude/skills`

| Skill | Lines | Repo files referencing it |
|---|---|---|
| vault-source-digest | 337 (+2 scripts) | 18 |
| vault-info-ripple | 144 | 8 |
| vault-ai-conversation-digest | 202 | 5 |
| eos-deploy-homepc | 152 | 5 |
| life-communication-written | 399 | 4 |
| life-communication-playbook | 192 | 1 |
| life-communication-speaking | 199 | 0 |
| creative-suno-archive | 163 | 1 |
| vault-yt-digest | 100 (+1 script) | 1 |
| tool-youtube-transcript | 42 (+1 script) | 1 |
| tool-academic-search | 101 (+1 script) | 1 |
| music-caption-rewriter | 185 (+templates) | 0 |

`vault-source-digest` is the most-routed-to skill in the whole set (18
repo files say "use vault-source-digest") and is the KB's canonical PDF
ingestion path, yet it is not in git. A fresh clone, the daemon's discovery on
another machine, and Codex all lack it. `eos-remote-verify` references
`.claude/skills/eos-deploy-homepc/SKILL.md`, a path that has never existed in
the repo.

Fix: run `sync_user_skills.py` for the eight that carry no personal content
(the `vault-*`, `tool-*`, `creative-suno-archive`, `music-caption-rewriter`).
The `life-communication-*` three and `eos-deploy-homepc` are Kevin-specific
by design; that is a legitimate reason to keep them user-global, but then
repo skills must stop routing to them by name as if they shipped — or route
with an explicit "(user-global, may be absent)" note.

### 2c. `docs/SKILLS.md` counts 34 bundled against 38 on disk — correct

`generate_skills_doc.py::_gitignored` drops any skill dir git is told to
ignore. `.gitignore:90-95` names five bundled skills as personal
(`life-strategic-advisor`, `life-job-evaluator`, `creative-mv-generator`,
`dev-claude-md-optimizer`, `tool-icon-creator`); four of the five exist on this
machine, are untracked, and are correctly excluded. `git ls-tree HEAD skills/`
= 34, on-disk = 38, difference = exactly those four. The generator and git
agree; there is no defect here. `[read-verified: git ls-files + check-ignore]`

An earlier draft of this section claimed the four were *tracked* and the ignore
therefore inert. That was wrong and was never measured — `git ls-files --cached`
returns nothing for all four. Recorded rather than deleted because the wrong
version recommended "resolving one way or the other", and one of those ways
(`git add -f`) would publish the PII skills.

---

## 3. Drift — three mirrors, all behind  `counted`

### 3a. `.claude/` → `.agents/` (Codex twin) — gated by `check_skill_script_sync.py`, currently red

| Skill | `.claude` (lines, last commit) | `.agents` (lines, last commit) |
|---|---|---|
| eos-design-system-audit | 687, 2026-09-12 | 627, 2026-09-05 |
| eos-commit-mine | 270, 2026-09-10 | 208, 2026-08-24 |
| eos-architecture-review | 504, 2026-09-03 | 485, 2026-08-28 |
| eos-wedge-postmortem | 354, 2026-09-06 | 342, 2026-08-16 |
| tool-blender-comfyui-video | 234, 2026-09-03 | 237, 2026-07-31 (+ uncommitted edits, + `references/native-3d-environment.md` untracked, present only in `.agents`) |

Direction is `.claude` ahead in four of five — the opposite of what the
checker's docstring recorded (".agents ahead every time"). The fifth is
mid-edit in the working tree and has grown a reference sibling on the Codex
side only. Copy `.claude` → `.agents` for the four; resolve the fifth by hand
after the in-flight edit lands.

Fourteen skills exist in `.claude/` with no `.agents/` twin at all, every one
created 2026-08-16 or later (eos-adversarial-review, -anchor-worked-example,
-artifact-render-check, -citation-verify, -failure-attribution,
-index-drift-sweep, -picture-pack-author/-review, -recency-check,
-remote-verify, -retrieval-diagnose, -sandbox-verify, -send-preflight,
-study-notes-digest). The checker calls this "legitimate — Codex-only or
Claude-only". The dates say otherwise: nothing about `eos-mutation-verify`'s
sibling `eos-failure-attribution` or `eos-sandbox-verify` is Claude-specific;
they simply post-date the last full mirror. `[inferred from dates]`

### 3b. `skills/` (bundled) ↔ `~/.claude/skills` (user) — no checker

20 files differ: 14 `SKILL.md` bodies, 2 living-memory sidecars
(`fiction-memory.md`, `strategic-memory.md` — expected, per docs/SKILLS.md),
`tool-pdf-reader/pdf_tool.py` (a **script**, so behaviour differs by tree),
and 3 leftovers only in the user store (`creative-mv-generator/*-LEGACY.md`,
`tool-flow-stills/SKILL.md.pre-promote-2026-08-14`). The two LEGACY files
carry six dead script references each and are exactly the kind of stale
sibling a progressive-disclosure reader would open.

There is no checker for this pair. `check_skill_vault_sync.py` covers
repo↔vault (3 opted-in skills, 1 drifted: `creative-suno-composer`, 6 lines
only in the vault); `check_skill_script_sync.py` covers `.claude`↔`.agents`.
The bundled↔user pair is the one that decides what a fresh clone ships, and
it is unwatched.

### 3c. `.agent-bus/skills` — gated by `check_skills.py`, currently red

`eos-design-system-audit` and `eos-mutation-verify` are out of sync with the
canonical bus store (both edited today). `eos bus import` after the current
edits land.

---

## 4. Dead references inside skill bodies  `counted`, then `read-verified` per row

A skill that names a path that no longer resolves sends the reader on a
search or, worse, gets a confident answer about a file that moved.

| Skill | Dead reference(s) | Likely cause |
|---|---|---|
| eos-model-bench-scenario-audit | `apps/assistant/app.py`, `apps/capture/app.py`, `apps/focus/app.py`, `apps/model-bench/app.py`, `apps/publish/app.py` | pre-track flat `apps/` layout |
| eos-design-system-audit | `apps/kb/pages/flipbook.css`, `apps/reader/pages/index.html` | same |
| eos-session-wrapup/report-templates.md | `apps/projects/app.py` | same |
| vault-source-digest (user) | `apps/kb/shared.py` | same |
| eos-sdk-extract | `emptyos/sdk/vault_stats_mixin.py`, `tests/test_utils.py` | renamed/removed |
| eos-mutation-verify | `tests/test_unit_verification.py` | example never created |
| eos-article-diagrams | `scripts/validate_palette.js` | lives under the dataviz skill, not `scripts/` |
| tool-blender-comfyui-video | `scripts/check_hybrid_video.py` | never written |
| eos-remote-verify | `.claude/skills/eos-deploy-homepc/SKILL.md` | user-global only (§ 2b) |
| eos-fde-engagement | `docs/app-info.md`, `docs/capability-match.md`, `docs/demo-script.md`, `docs/handoff.md` | template outputs described as if present |
| life-strategic-advisor | `.claude/skills/strategic-advisor/strategic-memory.md` | skill renamed to `life-strategic-advisor`; sidecar path in the "read it first" instruction is wrong |
| creative-fiction-writer | `.claude/skills/creative-fiction-writer/fiction-memory.md` | skill lives in `skills/` + user store, not `.claude/skills` |
| dev-claude-md-optimizer (+ TARGET-STRUCTURE.md) | `.claude/rules/{meta-notes,project-lifecycle,skill-routing,system-integration,vault-operations}.md` | describes a rules layout this repo never had |
| creative-youtube-channel/scripts.md | `scripts/generate_{animated_mv,cover,multiscene_mv}.py` | scripts retired |
| life-communication-speaking (user) | `scripts/analyze_audio.py`, `scripts/speaking_recorder.py` | scripts not shipped beside the skill |
| creative-mv-generator/*-LEGACY.md (user) | 6 `scripts/*.py` each | stale leftovers, delete |

The two living-memory sidecar rows matter most: `life-strategic-advisor`'s
SKILL.md tells the reader to open a file at a path that does not exist, so the
"read it first" contract silently fails and the advisor runs without its
memory. `[read-verified: the instruction line names the old slug]`

One routing target is dangling: `life-communication-playbook` routes to
`life-speaking-practice`, which was renamed `life-communication-speaking`.

Nothing checks path references inside skill bodies. `check_memory_rot.py`
does this for auto-memory; the same shape (path token → exists?) over
`SKILL.md` bodies would have caught all 16 rows, with placeholders (`foo`,
`<id>`, `NAME`) excluded — the false-positive filter this pass needed.

---

## 5. Trigger routing  `inferred` from descriptions, collisions `counted`

### 5a. Five phrases claimed by three or more skills

| Phrase | Skills claiming it |
|---|---|
| "audit" / "audit this app" | eos-architecture-review (bare "audit"), eos-bug-audit, dev-app-optimizer, eos-kb-audit, eos-security-review |
| "review" | eos-simplify (bare "review"), eos-architecture-review, eos-page-design-review |
| "what's missing" / "feature gaps" | dev-app-optimizer, eos-usecase-audit, eos-app-gap-analysis |
| "what's next" | eos-architecture-review (fix mode), eos-session-resume |
| "digest" | vault-source-digest, vault-ai-conversation-digest, eos-ai-conversation-ingest, vault-yt-digest, eos-study-notes-digest, eos-kb-atomize |

"digest" is fine — each is disambiguated by source type. The others are
not. `eos-architecture-review` claims bare "check", "audit", "fix", "grow",
"improve", "what's next": six generic verbs that every other audit skill also
lives under. `eos-simplify` claims bare "review". Both should drop the bare
verb and keep only the qualified phrase ("review the system", "check my
work").

### 5b. Two skills for one job

`eos-ai-conversation-ingest` (repo, 739 lines, the largest skill in the set)
and `vault-ai-conversation-digest` (user-global, 202 lines) both claim
"archive / digest / file a pasted AI chat". Their routing is split-brain:
`eos-port-fidelity-audit` and `eos-ai-conversation-ingest` itself point one
way; `vault-note-factory`, `eos-vault-study-pack` and `vault-source-digest`
point the other. The repo one has no NOT-for clause and no prerequisites
section despite hitting the daemon. Decide which is canonical, make the other
route to it in one line, and retire its body.

`dev-app-optimizer` ("any web app or app suite", no NOT-for) overlaps
`eos-app-gap-analysis` on every trigger and is the only skill whose NOT-for
appears in *another* skill's description rather than its own.

### 5c. Descriptions without a boundary (14)

env-check, eos-ai-conversation-ingest, eos-devlog-publish, eos-orgs-create,
eos-orgs-list, eos-orgs-member-add, eos-orgs-run-scenario, eos-screenshot,
eos-session-resume, preflight, tool-blender-comfyui-video,
creative-mv-generator, dev-app-optimizer, dev-claude-md-optimizer (plus
user-only music-caption-rewriter, tool-youtube-transcript, vault-info-ripple,
vault-yt-digest). `check_skills.py --strict` reports only 1 of these
(`eos-artifact-render-check`, which lacks a *use-when* cue) because its
NOT-for heuristic accepts a boundary anywhere in the body. The four `eos-orgs-*`
are narrow enough not to need one; the rest do.

### 5d. Over the spec limit (7)

The Agent Skills frontmatter spec caps `description` at 1024 characters:
eos-insights (1482), eos-usecase-audit (1383), eos-week-review (1239),
eos-ui-walk (1217), eos-life-insights (1180), eos-repo-extract (1041),
eos-design-system-audit (1026). This harness displayed all of them, so it
does not truncate `[read-verified: listing at session start]`; other
harnesses may. Each of the seven carries a 3-5 clause "Distinct from" tail
that belongs in the body.

---

## 6. Shape  `counted`

### 6a. Progressive disclosure

32 skills exceed 250 lines; 22 of those have **no** reference sibling —
everything loads on invocation. Largest without siblings: eos-design-system-audit
(687), tool-flow-stills (642), creative-mv-art-director (555),
eos-architecture-review (504), creative-media-library (450), dev-app-optimizer
(408), eos-page-design-review (402), eos-restructure (383), eos-ui-walk (371),
eos-usecase-audit (368), eos-new-plugin (366), creative-suno-composer (362),
eos-wedge-postmortem (354), tool-word-document (352). Six of the ten skills
that were never invoked *and* exceed 350 lines are in this list.

### 6b. Missing prerequisites (from `check_skills.py --strict`)

eos-ai-conversation-ingest, eos-new-usecase, eos-sandbox-verify,
eos-suite-cohesion, eos-verify-destructive-sink hit the daemon or Playwright
with no preflight/prerequisites section.

### 6c. Housekeeping

`.claude/skills/eos-mutation-verify/__pycache__/` and
`~/.claude/skills/vault-source-digest/__pycache__/` exist on disk (untracked;
gitignore holds). `.claude/skills/_retired/` holds one skill; nothing else
has ever been retired, which given § 7 is itself a finding.

---

## 7. Usage — 33 days of session logs  `counted`

Counting rule, stated because the first draft of this section did not have one
and its table did not add up: a name is counted only if it resolves to a real
skill directory (repo, bundled, or user-global) or to a known built-in. Harness
commands that are not skills (`/clear`, `/model`, `/compact`, `/goal`, `/theme`,
`/login`) are excluded. Window 2026-08-11 → 2026-09-12, 1,268 transcripts.

| Channel | Invocations |
|---|---|
| `Skill` tool | 163 |
| slash command | 356 |
| **combined** | **519** |

| Invocations | Skill |
|---|---|
| 189 | eos-session-wrapup |
| 152 | eos-simplify |
| 24 | eos-mutation-verify |
| 18 | artifact-design *(built-in)* |
| 17 | eos-adversarial-review |
| 14 | eos-repo-extract |
| 11 | eos-architecture-review |
| 8 | eos-design-system-audit, life-communication-written, vault-info-ripple |
| 7 | life-job-evaluator |
| 6 | dev-app-optimizer, eos-sdk-extract |
| 5 | claude-in-chrome *(built-in)* |
| 4 | eos-app-gap-analysis, eos-session-resume, eos-ui-walk |
| 3 | eos-worktree-gc, tool-pdf-reader |
| 2 | preflight, life-strategic-advisor, dataviz *(built-in)*, design *(built-in)*, loop *(built-in)* |
| 1 | 18 more, of which 15 are project/bundled/user skills |

34 of the 130 skills were invoked at all; **96 were never invoked**. Fifteen of
the 34 were used exactly once. Two skills account for 66 % of all invocations.

The 96 never-invoked include every `vault-*` except `vault-info-ripple` and
`vault-source-digest`, every `growth-*` and `geo-spatial-analyst` (invisible
until this audit's fix, § 2a), every `eos-orgs-*`, most `tool-*`, all four
`creative-mv-*` / `creative-youtube-*`, and 11 of the 14 skills created since
2026-08-16.

Caveats that keep this from being a deletion list: the window is 33 days;
Codex invocations are not in these logs; a skill can shape behaviour by being
read without the `Skill` tool; and several (`eos-wedge-postmortem`,
`eos-fix-drain`, `eos-release-public`) are event-triggered by design. Usage is
evidence for **where to spend maintenance**, not for retirement on its own.
`eos-session-resume` at 4 against `eos-session-wrapup` at 189 is worth a look:
the pair is designed as a loop and one side of it is not being closed.

## 8. What to do, in order

1. **Fix distribution first** (§ 2). Move the 4 bundled-only skills into
   `.claude/` + `.agents/`; sync the non-personal user-only skills into
   `skills/` via `sync_user_skills.py`; annotate the Kevin-specific ones
   wherever a repo skill routes to them. This is the one change that alters
   what a fresh clone and the daemon agent can do.
   **Done 2026-09-12 — see § 9 for what the pre-commit review changed.**

2. **Close the three drifts** (§ 3): copy `.claude` → `.agents` for four
   skills, mirror the 14 unmirrored ones, `eos bus import`, delete the three
   stale user-store leftovers. Add the bundled↔user pair to a checker — the
   `check_skill_script_sync.py` shape fits, with the sidecar exclusion
   `sync_user_skills.py` already encodes.
3. **Repair the 16 dead-reference rows** (§ 4), starting with the two
   living-memory sidecar paths and the dangling `life-speaking-practice`
   route. Then graduate the scan: a `check_skill_refs.py` over `SKILL.md`
   bodies with the placeholder exclusions this pass needed, registered
   advisory in `scripts/preflight.py` `docs` scope.
4. **De-greedy the two routers** (§ 5a): strip bare "audit"/"check"/"review"/
   "what's next" from `eos-architecture-review` and `eos-simplify`. Pick a
   canonical AI-conversation skill (§ 5b) and turn the other into a one-line
   redirect.
5. **Trim the seven over-limit descriptions** to ≤1024 by moving the
   "Distinct from" tails into a `## When NOT to use` body section, and add a
   boundary to the ten descriptions that lack one.
6. **Split the 14 largest no-sibling skills** (§ 6a) on touch, not in bulk —
   `eos-design-system-audit` and `eos-architecture-review` first, since they
   are both used and both over 500 lines.
7. Regenerate `docs/SKILLS.md`.

Not recommended: retiring the 88 unused skills on this evidence alone
(§ 7 caveats). Revisit with a 90-day window that includes Codex logs.

---

## 9. Step 1, as executed  `read-verified`

### What landed

**Four moved out of `skills/`** into `.claude/skills/` + `.agents/skills/` +
the `.agent-bus/` mirror: `geo-spatial-analyst`, `growth-content-strategist`,
`growth-hacker`, `growth-seo-specialist`. All copies sha256-identical to
`HEAD:skills/<slug>/SKILL.md`. Nothing in `release.toml`, `suites.toml`, the
packaging scripts or the store referenced them by path, and
`release-public.py:678` inventories `.agents/skills` as well as `skills/`, so
they stay in the public snapshot. They were landing in the matrix's `other`
theme bucket, so `PROJECT_THEMES` gained four rows and a comment saying why a
non-`eos-*` name can need one.

**Five synced into `skills/`** with their scripts: `vault-source-digest`,
`vault-yt-digest`, `tool-youtube-transcript`, `tool-academic-search`,
`creative-suno-archive`.

**Three of the eight candidates were reclassified on reading**, which is the
part § 2b got wrong by judging from the file list:

- `music-caption-rewriter` is a **third-party** MiniMax skill (1,022 files,
  installed via `npx skills add MiniMax-AI/MiniMax-Music3`). Not ours to ship.
- `vault-info-ripple` is written in Kevin's voice around a career example.
  It joins the personal set, not the shipped one.
- `vault-ai-conversation-digest` was synced and then **withdrawn before the
  commit**. It writes KB notes to `{vault}/30_Resources/KB/<domain>/<kind>s/`
  with `kind: standard`, while `vault-source-digest` — synced in the same
  change — says in bold "do NOT use the stale `30_Resources/KB/...` paths".
  Both trees exist in the real vault and preflight scans both as separate
  corpora, so neither skill is simply wrong; but shipping two skills that give
  opposite instructions about one path is not something this change should
  decide. Blocked on the § 5b canonical-conversation-skill decision.

**Five personal skills annotated at eight route sites** (not "every repo
route" as an earlier draft of this section claimed — the first pass annotated
four and the hostile review found four more): `life-communication-{written,
playbook,speaking}`, `eos-deploy-homepc`, `vault-info-ripple`. The one that
mattered most was `skills/life-people-manager/SKILL.md:3` — a **shipped**
skill routing a fresh clone to a skill that exists only in the author's home
directory.

### Defects the pre-commit review caught, and the fix

A skill that was fine living outside git is not automatically fine inside it.
Five classes, all introduced *by the act of shipping*:

| Defect | Fix |
|---|---|
| `digest_pdf.py` hardcoded `D:/emptyos` three times, including the `.eos-personal` lookup — which returned `[]` on any clone not at that path, silently disabling the PII layer the skill advertises as a hard gate | `_repo_root()` from `__file__`; now loads 18 patterns, measured |
| Every script-bearing skill invoked its script at `{home}/.claude/skills/<slug>/…` — a scrub placeholder no shell expands, pointing outside the repo, while the shipped script sits at `skills/<slug>/` | repo-relative paths |
| `vault-yt-digest` shipped the author's machine as setup ("already-installed dependencies", a Python-3.13-pinned `yt-dlp.exe` path the script does not even consult — it uses `shutil.which`) | generic prose |
| `creative-suno-archive` + `vault-yt-digest` named `D:/emptyos/scripts/…` and `D:/emptyos/emptyos.toml` in prose | repo-relative |
| `vault-source-digest` shipped the dead `apps/kb/shared.py` reference this audit had already recorded at § 4, plus a route to a slug that does not exist (`ai-conversation-digest`) | `apps/public/standard/kb/shared.py`; route → `eos-ai-conversation-ingest` |

### Waived, with reasons

- **Behaviour bugs inside the copied scripts** — `yt_digest.py` decodes
  `yt-dlp` output in the console's codepage rather than UTF-8 (cp1252
  mojibake on a CJK title), reuses a bundle directory so a second `--shots`
  run collides on frame names, and labels every exactly-four-author paper
  "et al." in `academic.py`. All pre-existing in the user-global originals and
  unchanged by the move. Fixing them is a separate scope; recorded here so the
  next toucher has them.
- **`tool-academic-search` is Windows-only** in its venv path
  (`LOCALAPPDATA` + `Scripts/`). It follows the user-home-venv convention,
  which is itself Windows-phrased. Settling it means deciding whether `skills/`
  is a Windows-only tier — a question above this change.
- **`docs/SKILLS.md` records two `.agent-bus` drift rows** belonging to another
  session's in-flight edits. The doc regenerates every wrapup and self-heals;
  reaching into those two skills to clear it would mean touching another
  session's uncommitted work.
- **The `skills/` tree is outside the skill gates.** `check_skills.py` scans
  `.claude/skills` + `.agents` + `~/.claude/skills`; `check_skill_security.py`
  scans `.claude/skills`; `check-branding.py` exempts `skills/` entirely. So
  the only gate the five newly-shipped skills pass through is
  `check-personal.py`, and they are the only skills shipping executable `.py`.
  All five pass the frontmatter contract when linted by hand. This change
  doubles the size of an unwatched tree — which makes § 8 step 2's
  "add the bundled↔user pair to a checker" more load-bearing, not less.

### Corrections to this report

Three claims in the first draft were wrong and are fixed above: § 2c asserted
four bundled skills were tracked when `git ls-files` returns nothing for them;
§ 7's table did not reconcile with its own totals because it had no stated
counting rule; and § 3b's "20 files differ" does not reproduce (21 after
newline normalisation, and the enumeration omits two `references/` siblings
that `sync_user_skills.py` can never sync, since it copies only top-level
`*.md`). The § 2b fix line — "run `sync_user_skills.py` for the eight" — was
also not executable as written: the script refuses to create a bundled skill
that does not already exist, so the directories had to be made first.

---

## 10. Steps 2-7, as executed  `read-verified`

### What landed

**Step 2 — drift.** The Codex mirror went from 6 drifted files to 2 and from 15
one-tree-only skills to 1; the 14 newer skills that had never been mirrored now
are, and the two stale `.agent-bus` entries were re-synced. Direction was
diffed per pair rather than assumed, which mattered once: the Codex copy of
`eos-architecture-review` held a `find_test` helper the Claude copy lacked, and
reading it showed the Claude side had replaced it with an index-based version
for a stated reason (app-id collisions cause false negatives). A sync in the
"obvious" direction would have reverted that.

The bundled↔user checker § 8 step 2 asked for **already existed** —
`sync_user_skills.py --check` does exactly that comparison, scrub included. It
was simply never registered. Registering it beat writing a second scanner that
would have had to duplicate the scrub and then drift from it. Its docstring
said "Not registered in `scripts/preflight.py`"; that sentence is now false and
was rewritten.

The three "stale user-store leftovers" § 3b named were **not deleted**, because
reading them refuted the finding: `creative-mv-generator/REFERENCE-LEGACY.md`
is deliberately preserved and named in the live `REFERENCE.md` ("preserved …
for diagnosis of old projects"). § 3b judged them from a file listing. Same
mistake class as the § 2c error.

**Step 3 — dead references.** 16 files to 2. The survivors are one QA script
(`scripts/check_hybrid_video.py`) that a skill's step 8 tells the reader to run
and nobody ever wrote, in a file another session is editing. Recorded as a
named `KNOWN_OPEN` with a paired test that goes red if the exemption outlives
the defect, rather than silenced.

Graduated to `scripts/check_skill_refs.py` (advisory, preflight `docs` +
`skills`, the latter with `--user`) + `tests/test_unit_check_skill_refs.py`.
Three reference classes are legitimately non-repo-relative and opt out with an
inline `<!-- skill-refs: ignore — why -->` at the file, never a central
allowlist.

**Step 4 — routing.** `eos-architecture-review` and `eos-simplify` no longer
claim bare "audit" / "check" / "review" / "fix" / "what's next", **in their
bodies as well as their frontmatter** — the first pass edited only the
description, which left each skill's own `## When to Use` table re-claiming
every verb the description had just given away. `vault-ai-conversation-digest`
became a redirect to the canonical `eos-ai-conversation-ingest`.

**Step 5 — descriptions.** All 7 over the 1024-char Agent Skills cap are under
it (921-1002). 17 gained a NOT-for boundary, 1 a missing trigger cue, 4 a
prerequisites section. `check_skills.py --strict` passes clean for the first
time, across 168 skills.

**Step 6 — splits.** `eos-design-system-audit` 687 → 339 + a 378-line check
catalog; `eos-architecture-review` 504 → 360 + two siblings. Both spines carry
the mandatory `## Reference files (read on demand)` table. Verified line-for-
line that no content was dropped.

**Step 7** regenerated `docs/SKILLS.md`.

### What the pre-commit review changed

Two hostile reviewers found 33 findings. The three that mattered most were all
cases of a fix that looked complete and was not:

| Found | Why it matters |
|---|---|
| The de-greedy edit was frontmatter-only; both bodies still claimed every bare verb | The frontmatter governs *selection*, the body governs *behaviour once loaded*. The change undid itself the moment either skill was invoked. |
| **10 of the new checker's 12 directory prefixes and 11 of its 12 extensions could be deleted with all 13 tests green** | The coverage was concentrated on two filters and a live-corpus pin. Dropping `md` alone would have killed the sidecar and rules-path classes this audit called its most important rows. |
| A daemon-handling safety rule moved into a sibling file during the split | The progressive-disclosure contract is explicit that every safety rule stays in the spine. Moved back, at the step that needs it. |

Six more were factual errors in text written this session: a prerequisites
block that gated a filesystem-only skill on a daemon it does not need, two
probes aimed at `/api/health` where the real dependency is an authenticated
route that 401s in the same state, an instruction to add a test to
`tests/helpers.py` (which pytest never collects, so the test would silently
never run), a brand-island token list that re-blessed the exact `:root`
override a rename had fixed, and a reference pointer placed after the phase
that needs it.

### Mutation evidence

The checker was mutation-verified twice, because the first battery's result was
not trustworthy: the runner left a mutation unrestored and its "restored file is
green" line did not hold. Restoration is now checked against a saved hash.

| Mutation | Outcome |
|---|---|
| directory alternation trimmed to two prefixes | RED |
| extension list trimmed to `py` | RED |
| parent-traversal exclusion deleted | RED |
| marker widened to a bare `ignore` | RED |
| `scan()` walks no trees | RED |
| `os.walk` narrowed to depth 1 | RED |
| placeholder filter switched off | pinned (caught by a sibling test) |
| a comment-only edit | GREEN, correctly tolerated |

Two of those went red only after the tests were repaired. Both first drafts
were vacuous in the way this report keeps describing: the traversal fixture
used a path that `exists()` answers True for, so it passed with the exclusion
deleted; and the walk-depth assertion looked for one `/` in a finding's path,
which a top-level file satisfies.

### Still open, and not mine

Two hard gates stay red, both on files another session is editing:
`tool-blender-comfyui-video`'s Codex-mirror drift and `creative-suno-composer`'s
vault drift. Direction on the second is a human call by its checker's own
contract.

