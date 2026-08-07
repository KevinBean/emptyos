# Routing and audit reference

## Full-domain coverage scan

Scan all rows for every conversation, even when the title suggests only one
topic. Record one of `delta`, `mentioned-no-delta`, `not-present`, or
`needs-review` in the digest. A digest is comprehensive because it checks every
domain, not because it creates a note in every domain.

| Domain | Typical durable content | Default destination | Specialist skill | Evidence rule |
|---|---|---|---|---|
| Personal chronology | Event, milestone, location/status change, lived experience | `50_Journal/{year}/{date}.md` plus an existing living note | `journal-planner`, `vault-info-ripple` | User-attested is valid; preserve date and context |
| Emotional and health | Trigger, mood, symptom, coping step, recurring pattern | `20_Areas/Health/mood-log.md` or another private health note | `healing-companion` | Separate observation, feeling, interpretation, and response |
| People and relationships | Contact fact, interaction, commitment, relationship change | Existing `30_Resources/People/@<person>.md` | `people-manager` | Do not state private judgments as external fact |
| Work and career | Employer/project context, feedback, role change, work decision, career option | Existing `60_Worklogs/{year}/{date}.md`, `20_Areas/Career/`, or active project note | `strategic-advisor` when appropriate | Separate historical context, current status, assumptions, and decisions |
| Projects and execution | Goal, requirement, decision, progress, blocker, next action | Existing `10_Projects/<project>/` note | project/task skill | Preserve decision rationale and ownership |
| Tasks and commitments | Explicit next step, deadline, promise, waiting item | Existing project, area, or journal task section | `task-management` | Do not create tasks from mere suggestions |
| Knowledge fragments | Definition, distinction, method, quote, reusable explanation, reference | Existing or new `30_Resources/KB/` or relevant resource note | `note-factory`, domain skill | Promote only verified or resolved claims; retain small fragments in the digest when no standalone note is justified |
| Calculations and engineering | Formula, units, worked example, boundary condition, design assumption | KB formula/case note and optional calculation record | relevant domain/calculator skill | Recompute and state units, assumptions, validity range, and checks |
| Tools, code, and calculators | Reusable code insight, automation, calculator or app specification | Existing `80_Code/`, `85_Software/`, KB note, or active app/project note | relevant engineering/app skill | Include test vectors, invalid inputs, version, and source equations |
| Finance and assets | Amount, transaction, plan, investment or tax decision | Existing `20_Areas/Finances/` tracker/plan | `finance-tracker` | Current figures and rules require authoritative verification |
| Legal, immigration, and administration | Application state, policy, deadline, evidence requirement | Existing immigration/citizenship project or administrative note | relevant tracker/strategic skill | Verify current rules with primary official sources |
| Media and creative work | Book/media insight, song/story/visual idea, creative decision | Existing `30_Resources/Books/`, `30_Resources/Entertainment/`, or creative project | `media-library` or creative skill | Separate source-derived insight from new creative interpretation |
| Preferences and operating principles | Durable preference, personal rule, workflow preference | Existing personal operating note or relevant area/project | `vault-info-ripple` | Treat user-stated preference as user-attested, not universal fact |
| Open questions and contradictions | Unresolved issue, alternative, uncertainty, later correction | Digest plus relevant project/KB note when actionable | domain skill | Preserve uncertainty and identify what would resolve it |
| Transient/no durable delta | Wording polish, temporary debugging, abandoned brainstorm, empty chat | Source + digest only, or ledger skip for empty/transient chats | none | Do not manufacture a derived note |

## Output path layers

| Layer | Standard | Sensitive |
|---|---|---|
| Immutable source | `30_Resources/conversations/originals/<provider>/YYYY-MM-DD-<slug>--<id-short>.md` | `40_Archive/AI Conversations/originals/<provider>/YYYY-MM-DD-<slug>--<id-short>.md` |
| Audited digest | `30_Resources/conversations/YYYY-MM-DD-<slug>.md` (native exports add `--<id-short>`) | `40_Archive/AI Conversations/<provider>/YYYY-MM-DD-<slug>.md` (native exports add `--<id-short>`) |
| Ingestion ledger | `30_Resources/conversations/ai-conversation-ingestion-ledger.md` | Same ledger, but use a neutral label when the title is sensitive |
| Derived delta | Existing journal/area/project/resource/worklog note selected by actionability | Existing private destination; never lower the source's privacy by copying raw excerpts |

The connected vault's `_vault-map.toml` overrides defaults for app-owned paths.
Search before creating and prefer an existing living note.

## Evidence graph contract

The source archive, digest, and every derived note form a reciprocal graph:

```text
provider conversation
  → immutable source archive
  → audited digest
  → derived note update(s)
  ↖ ledger row indexes the whole chain
```

Digest frontmatter:

```yaml
source_archive: "[[40_Archive/AI Conversations/originals/claude/...]]"
derived_notes:
  - "[[20_Areas/Career/...]]"
  - "[[30_Resources/KB/...]]"
```

Digest body:

```markdown
## Evidence chain

| Derived note | Change | Evidence class | Reciprocal link |
|---|---|---|---|
| [[target-note]] | Appended decision and next action | user-attested | verified |
```

Append to every changed derived note:

```markdown
## Conversation evidence

- 2026-07-25 · Based on [[digest-note]] · source [[source-archive]] ·
  `claude:<provider-id>` · Updated: <section or claim> ·
  Evidence: <short self-contained summary or necessary excerpt> ·
  Class: `verified|self-corrected|user-attested`.
```

For a user-authored note, append this inside a clearly AI-owned section and set
`author: both`. For an AI-created derived note, set `author: ai`. Do not replace
user prose, and do not put sensitive raw excerpts into a less-private note.

Verify after writing:

1. the digest links to the source and every derived note
2. every derived note links back to the digest and source
3. the provider ID agrees across source, digest, derived note, and ledger
4. the evidence class agrees with the fact-check table
5. the ledger lists all derived destinations

## No-mutation disposition contract

`derived_notes: []` does not mean that routing was performed. When every
non-transient domain is `not-present` or `mentioned-no-delta`, record an
explicit `no-durable-delta` reason. When any non-transient domain is `delta`,
append a `## Delta disposition` section that maps that domain to one
disposition. A table already inside `## Routing` remains valid:

| Disposition | Completion effect | Required evidence |
|---|---|---|
| `digest-contained` | Complete only after routing review | Explain why the audited digest is the appropriately sized durable destination; record bilingual search queries and every candidate note checked, or `no candidate found` |
| `reused-no-change` | Complete | Wikilink the existing note and state what was compared and why no text changed |
| `duplicate-of` | Complete | Wikilink the canonical conversation digest and state the identity/overlap basis |
| `deferred-unverified` | Incomplete | State what remains unverified and what would resolve it |

Use this exact table shape:

```markdown
## Delta disposition

| Domain | Disposition | Target | Reason | Routing evidence |
|---|---|---|---|---|
| Knowledge fragments | digest-contained | This digest | One small verified distinction; no separate note warranted. | queries: risk matrix; 风险矩阵 · checked: [[existing-risk-note]] |
```

A generic “no living note updated” paragraph is not evidence. A
`needs-review` domain is incomplete until resolved. Do not use
`reused-no-change` merely because a related note exists: compare the delta to
the target note and record the no-change basis. Derived notes list only notes
actually created or modified; referenced-but-unchanged targets remain in the
disposition table so the evidence graph does not falsely claim a mutation.

Schema compliance is not semantic completion. A bulk migration may add missing
frontmatter, source links, and coverage tables, but it must not close durable
deltas with a shared boilerplate reason such as "the legacy run left no
reciprocal evidence." Such records stay incomplete until a conversation-level
review compares the delta against actual living-note targets.

For `digest-contained`, `Routing evidence` is mandatory and uses this compact
grammar so the deterministic auditor can verify its presence:

`queries: <English terms>; <中文词> · checked: <wikilinks or no candidate found>`

The evidence is a receipt, not proof by assertion. During review, actually run
the listed bilingual searches and open every plausible candidate before
recording it. If a candidate already covers the delta, use
`reused-no-change`; if it should change, mutate it and add reciprocal evidence.
Reserve `digest-contained` for a small, conversation-scoped fragment whose
promotion would create retrieval noise.

## Claim classes

- `verified`: checked against arithmetic or an authoritative source
- `self-corrected`: an earlier error is explicitly resolved later; verify the
  final form before promotion
- `user-attested`: valid evidence of the user's own experience/status, but not
  an independently verified external fact
- `overstated`: directionally plausible but stronger than evidence
- `wrong`: contradicted by calculation or authoritative evidence
- `unverified`: important but not checked or not checkable
- `not-a-factual-claim`: preference, feeling, brainstorm, or hypothetical

## Fact-check note format

```markdown
## Fact-check notes

| Claim | Class | Check | Durable treatment |
|---|---|---|---|
| ... | verified | ... | Used in [[note]] |
| ... | user-attested | User report in source | Journal only; no external-fact claim |
| ... | wrong | ... | Preserved in source, corrected here, excluded from KB |
```

For time-sensitive claims include the verification date and link the primary
source. Do not use search-result snippets as evidence.

## Calculations and calculators

A promoted calculation records:

- purpose and governing equation
- inputs with units and provenance
- assumptions and sign convention
- intermediate values
- result with sensible precision
- independent or hand-check
- validity limits and unresolved uncertainty

A promoted calculator specification additionally records:

- input validation and unit normalization
- expected failure states
- at least three verification cases: nominal, boundary, and invalid
- reference implementation/source equations
- version or date when standards/rules are time-sensitive

## Sensitive material

Use a private path and `private` tag for:

- emotional/mental or physical health
- relationship conflict or intimate assessment
- identity documents or location details
- financial amounts/accounts
- confidential employer/project material

Keep the ingestion ledger minimally identifying when the title itself is
sensitive. A neutral label plus provider ID is sufficient.
