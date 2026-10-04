# MEMORY.md — Attested Memory

> **Memory in EmptyOS is lossless. The intelligence goes into trust annotation,
> not content rewriting.**

This is the framework for how EmptyOS reasons about *trust in its own memory*. It
is the contrarian position the substrate makes available: while the rest of the
field races to make memory "agentic" (auto-summarize, auto-merge, vector recall),
EmptyOS keeps memory a faithful mirror and pushes all the intelligence into
**non-destructive observability over memory** — who asserted a thing, how it was
derived, what supports it, when it was last confirmed. A false "this reminds me
of…" is worse than an honest "I don't know."

This document is **design + doctrine**, not a shipped subsystem. Most of its
primitives already exist scattered across the codebase (see § *What already
ships*); the framework's job is to *name the spine that unifies them* and the
small genuinely-new delta, so future work consolidates instead of reinventing.

---

## 1. Spine

> Every memory is a **claim** carrying its **provenance**. Trust is **derived**
> from provenance, never stored. Corrections **supersede**, never overwrite.
> Verification **observes** and asks, never rewrites.

Four sentences, four invariants. Everything below hangs off them.

In the Yogācāra frame of `docs/DESIGN.md` § *Consciousness Model*, this lives
precisely at the **manas ↔ ālaya** interface: the seventh consciousness (the
integrity / self-awareness loop) *observes* the storehouse's fidelity, but never
edits the eighth's seeds. The framework is "track each seed's lineage and current
potency without altering the seed."

It is the operational form of the Mirror-Wisdom constraint already in
`DESIGN.md:1004–1006`:

> The system does not impose structure the user didn't intend. It does not lose
> what the user valued. It does not add noise. It simply reflects. … every feature
> must move the vault closer to being a faithful mirror, never further away.

Attested Memory is "faithful mirror" turned into machinery: mark/gate/check the
seeds, never re-write them.

---

## 2. What already ships (read this before building anything)

The single most important section. The framework is **mostly consolidation** —
do not rebuild any of this:

| Framework piece | Status | Where |
|---|---|---|
| `author: user \| ai \| both` (authorship provenance) | **Shipped** (~11 consumers; no SDK helper yet) | `.claude/rules/authorship-boundary.md`; written directly by `kb`, `braindump`, `daily-brief`, `promote`, … |
| `verified_against:` / `implemented_in:` (correctness attestation) | **Shipped + health-checked** | `apps/public/standard/kb/indexes.py` (`_build_implementation_index`); `eos kb health` flags missing/unresolved |
| `references:` → clause resolution (source provenance) | **Shipped** | `apps/public/standard/kb/shared.py::_parse_citation` (line 230) + `indexes.py::_build_ref_index` |
| `superseded_by:` / `supersedes:` (non-destructive correction) | **Shipped, dark-flagged** | `kb/shared.py::resolve_supersession` (line 254); flag `feature.temporal-supersession.enabled` (`indexes.py:124`) |
| propose → review-gate → apply | **Shipped** | `emptyos/sdk/base_app.py::propose_action` (2866), `propose_kb_note` (2903), `propose_kb_extractions` (2951) |
| one-fact-per-note memory store | **Shipped** | `apps/public/standard/voice-assistant/memory.py` (aura-memory, `30_Resources/EmptyOS/voice-assistant/memory/`) |
| trust **derived, not stored** doctrine | **Shipped doctrine** | `.claude/rules/three-natures-lens.md` (圓成實 = mark/check, not store); `scripts/check_memory_rot.py` (surface, don't synthesize) |
| memory fidelity / health metric | **Analog shipped** | 14-dimension integrity audit (P1–P14), `DESIGN.md` § *Self-Awareness System* |
| `as_of:` + `lifecycle: snapshot` (point-in-time provenance) | **Ad-hoc** (used in ~5 apps; no classifier) | `daily-brief`, `business-case`, `nutrition`, … |
| `last_verified:` (recency of confirmation) | **Not built — genuinely new** | — |

### The genuine delta this framework introduces

Everything else is unification of the above. The *new* surface is exactly three
things — **all shipped** (`emptyos/sdk/attested_memory.py` pure core +
`BaseApp` wrappers; `tests/test_unit_attested_memory.py`):

1. **`last_verified:`** — a frontmatter field, distinct from `updated:`
   (`updated` = bytes changed; `last_verified` = confirmed still true). Written
   by `BaseApp.vault_confirm()`.
2. **A derived trust read** — `classify_trust(props) -> {trusted | tentative |
   suspect | retired}` (§ 4), surfaced as `BaseApp.vault_trust(path)`.
3. **A read-only fidelity audit** — `fidelity_audit(rows)` (§ 8), surfaced as
   `BaseApp.memory_audit()`. Pure + budgeted.

Two consumers of the audit are wired:

- **Hub fidelity dial** — `hub.panel_memory_fidelity` (renderer `bar`), the
  glanceable "% trusted" dial. Behind `[apps.hub] feature.memory-fidelity.enabled`.
- **Scheduled proposer** — `apps/extension/dev/memory-fidelity/` runs the audit
  on a cron and files **suspect** claims as review-gate "confirm or correct?"
  proposals (Apply → `confirm_claim` → bumps `last_verified`). Dark behind
  `[apps.memory-fidelity] enabled`; suspect-only by default (tentative would be
  a treadmill); cooldown ledger + per-sweep cap prevent flooding.

The framework **generalizes** the existing KB `resolve_supersession`; it does
**not** invent supersession, authorship, or the review gate.

---

## 3. The unit — a Claim

The note is the file; the **claim** is the unit of trust. For aura-memory
(already one-fact-per-note) a claim ≈ a note. For richer KB notes, claims ride on
claim-bearing fields. A claim carries:

| Part | Meaning | Backing field |
|---|---|---|
| **content** | the statement | the note body / a frontmatter field |
| **attestation** | `asserted` (user said it) · `inferred` (AI derived) · `imported` (external) · `observed` (system logged an event) | **extends** `author:` |
| **source** | where it came from — *embedded* for `inferred`, *linked* for `imported`/`observed` | `verified_against:`, `references:`, `(see [[journal/...]])` |
| **last_verified** | when last confirmed true — distinct from `updated:` | **new** (`as_of:` is its snapshot cousin) |
| **supersession** | non-destructive correction links | `superseded_by:` / `supersedes:` (shipped) |

Attestation is the one new categorical axis, and it is a *refinement* of `author:`
— `author: ai` + `attestation: inferred` is the high-suspicion case; `author:
user` + `attestation: asserted` is the high-trust case.

---

## 4. The trust function (the crux)

Trust is a **pure function over provenance facts** — never a stored number:

```
trust(attestation, author, last_verified_recency, supersession) ->
    {trusted | tentative | suspect | retired}
```

Examples:

- `asserted` + `user` + fresh → **trusted**
- `inferred` + `ai` + unverified → **tentative** (wants confirmation)
- time-sensitive + stale `last_verified` → **suspect** (triggers a fidelity check)
- superseded → **retired** (kept for audit, never deleted)

**Never store a confidence number.** A stored `confidence: 0.87` is *confidence
theater* — it launders a guess as certainty and then drifts. The vault stores
cheap, true, stable *facts about where memory came from*; "confidence" is a
**read**, computed fresh each time, never a saved opinion. This is the load-bearing
decision and it is already EmptyOS doctrine — the derived-not-stored move is
圓成實 in `.claude/rules/three-natures-lens.md` (don't reify a derived appearance
as fixed ground truth), and the "surface, don't synthesize" posture in
`scripts/check_memory_rot.py`.

The one real cost of derivation: the trust function is **policy living in code**,
so changing it shifts every verdict at once. The answer is not to store numbers —
it is to treat the function like a schema: **versioned, categorical (no magic
weights), and explainable** (every read returns the provenance facts that drove
the verdict). An explainable categorical function is auditable in a way a stored
float never is. That is the trade this framework defends.

---

## 5. Operations → existing mechanisms

| Verb | Does | Rides on |
|---|---|---|
| **attest** | record a claim + provenance (defaults derived from authorship/folder → low friction) | `vault_create_note` + `authorship-boundary.md` |
| **confirm** | mark still-true → bump `last_verified`; may promote `inferred` → `asserted` | review gate (`propose_action`) |
| **supersede** | record a contradicting claim, link old↔new, retire old | `resolve_supersession` (kb) + `proposed-action.md` |
| **assess** | derive trust (pure read) | *new* (§ 4) |
| **audit** | fidelity pass — surface stale/high-trust claims as proposals | scheduled staff agent (read-only) + integrity audit |

Note the asymmetry: **attest/confirm/supersede/audit** all already have backing
machinery; only **assess** (the derived read) is genuinely new code.

---

## 6. SDK surface

**Shipped** (`emptyos/sdk/base_app.py`, backed by the pure
`emptyos/sdk/attested_memory.py`):

```python
self.vault_trust(path) -> dict      # derived verdict (§4): {level, attestation, reasons, age_days, version}
self.vault_confirm(path) -> dict    # bump last_verified to today; returns fresh verdict
self.memory_audit() -> dict         # fidelity report (§8): {dial, total, counts, stale[], version}
```

`vault_trust` / `memory_audit` are **read-only** and **inert until called** — no
flag needed (they change nothing until a consumer invokes them). `vault_confirm`
is an explicit, reversible single-field write (`last_verified`), not the audit
path. The pure core is unit-tested without a daemon
(`tests/test_unit_attested_memory.py`).

Consumers: `hub.panel_memory_fidelity` (the dial) and
`apps/extension/dev/memory-fidelity/` (the scheduled proposer + its
`confirm_claim` apply-target).

**Not yet built** (per CLAUDE.md rule 9 — wait for the consumer):

```python
self.vault_attest(path, mode=..., source=...)   # record claim + provenance (use vault_create_note + author: today)
self.vault_supersede(old, new)                   # generalize kb/shared.py::resolve_supersession beyond KB
```

`vault_attest` would **wrap** `vault_create_note` + the authorship convention;
`vault_supersede` would **generalize** the KB-only `resolve_supersession`. Until
a real consumer needs them, use the shipped per-app paths (KB supersession,
`propose_*`) directly — do not build the SDK speculatively.

---

## 7. Invariants (framework law)

These resolve the design's open questions as law rather than per-case judgment:

1. **Immutable as memory.** Corrections append + supersede; never overwrite or
   delete. Aligns with the Mirror-Wisdom constraint and `autopilot-grants.md`
   (free-form vault writes are **never** autopilot-eligible).
2. **Trust derived, never stored.** No saved confidence number (§ 4).
3. **Inferred claims embed their evidence.** AI-inferred memory must snapshot its
   evidence into the note body at creation — *self-contained provenance*, never a
   link into ephemeral `data/` (chat runs, syslog), which breaks on demo-reset.
   Honors CLAUDE.md rule 19 (no vault content to cloud) — evidence is local text.
4. **Verification observes and asks.** Read-only audit + review-gate proposal,
   never auto-rewrite. The audit is a `propose_action`, not a vault write.
5. **Scope = machine-touched memory.** AI-authored/inferred claims + explicitly-
   curated user facts (aura-memory, KB) only. The ~4,000-note hand-written vault
   (journal, projects, areas) is **exempt** — enforced at the *write boundary*
   (the attest/trust helpers no-op on notes without the machine-touched marker),
   not by convention, so a bug can't leak the framework into the journal.
6. **No cloud content for verification.** "Still true?" checks stay local /
   structured; never ship vault content to a cloud model (rule 19).
7. **Trust derives from indexed frontmatter only** *(the performance keystone)* —
   no body read, no I/O, no LLM in `assess`. Secures hot-read latency and
   determinism (§ 8).
8. **Retired claims tier out of the hot index.** Superseded claims leave the
   working set (RAM) but never leave the vault (on disk, grep-able, indexed lazily
   for audit only).

What would break the story, stated so it's enforced against: if `assess` ever
read the note body, called an LLM, or scope leaked to the whole vault. All three
are constraints to enforce at the SDK boundary, not hopes.

---

## 8. Performance

The "recompute every read" instinct is *cheaper*, not more fragile, here —
because of invariant #7. `VaultIndex` (`emptyos/runtime/vault_index.py`) holds
**all frontmatter in RAM** after a one-time ~800ms boot scan for 3k+ notes, and
`find()` returns frontmatter with **zero I/O at query time**.

Three paths, by cost:

- **Hot read (`assess`)** — pure comparison over in-RAM frontmatter fields
  (`attestation`, `author`, `last_verified`, `superseded_by`). No file read.
  *Correction to the original framing:* `VaultIndex.find()` is an **O(N) linear
  scan with no per-field index** (it iterates `self._files.values()`), so this is
  *microseconds for ~3k notes*, **not** "nanoseconds." Still far cheaper than
  fetching a stored value from disk, and fine at this scale.
- **Current-claim view** — the supersession-tip set is maintained incrementally
  on `vault:changed` (the same mechanism that keeps `VaultIndex` fresh). Chains
  are short (a fact is corrected a handful of times over years) and walked only
  during audit.
- **Fidelity audit** — the *only* O(N) work, and it is the one to discipline:
  **scheduled** off the hot path (staff-agent cron, like existing nightly
  audits), **scoped** to machine-touched memory (hundreds of notes; the journal
  is exempt), **candidate-filtered** via an in-memory frontmatter query
  (`attestation in {...} AND last_verified < cutoff`), and **budgeted** (surface
  top-N by trust×staleness as proposals, never a flood — mirror the autopilot
  drain budget and `check_memory_rot.py`).

A system-level **fidelity dial** (% of machine-touched memory that is
trusted+fresh vs suspect+stale) is the read-only loop-closer — it turns "faithful
mirror" from aspiration into a number you watch move. It is a derived read over
the same in-RAM fields, computed on the schedule, not per-request.

> Note: `base_app_vault.py::vault_query`'s docstring (bound onto `BaseApp`;
> extracted from `base_app.py` in the 2026-07-10 god-object split) still calls
> the index "SQLite-backed" — it is actually plain in-RAM dicts. Stale comment,
> not a behavioral concern.

---

## 9. When NOT to use it

Two whole families of "agentic memory" are **rejected** by this framework — and
the reasons are framework law, not taste:

- **Metabolism (auto merge / decay / distill).** A lossy, generative operation on
  the user's source of truth. It violates the Mirror-Wisdom constraint ("does not
  lose what the user valued, does not add noise") and it is a free-form vault
  write, which `autopilot-grants.md` says is never autopilot-eligible. An
  auto-consolidator is the system imposing structure the user didn't intend.
- **Associative / vector recall.** Trades precision for recall in the one domain
  where a confident-but-wrong memory is poison, and it usually drags in a vector
  store that breaks the grep-able, plain-text, you-own-it substrate. Claim identity
  uses a **deterministic key** (slug for memory, field-path for KB), never fuzzy
  matching — same key + same content → *confirm*; same key + new content →
  *supersede*; new key → *new claim*.

Also:

- **Don't annotate the bulk episodic vault.** Invariant #5 — the hand-written
  journal/projects are exempt; defaults keep them so.
- **Don't store a confidence number** (§ 4).
- **Don't add an escape-hatch "pin this trust" that stores a verdict.** If a user
  wants to pin trust, that is itself just another *attestation* (`asserted` +
  `user`), which the derived function already reads — no stored override needed.

---

## 10. Cross-references

- `docs/DESIGN.md` § *Consciousness Model* (八识) + § Mirror Wisdom — the frame
  this operationalizes; the manas↔ālaya interface is exactly where trust lives.
- `.claude/rules/three-natures-lens.md` — derived-not-stored (圓成實); a stale
  memory recited as current truth is the canonical 遍計所執 instance.
- `.claude/rules/authorship-boundary.md` — `author:` is the provenance field
  `attestation` refines; `EOS_UI.provenance()` is the render-time sibling.
- `.claude/rules/proposed-action.md` — propose/preview/confirm; **confirm** and
  **supersede** ride this gate; staleness check = "did the past move?".
- `.claude/rules/time-dimension.md` — past→future→now; `last_verified` is the
  temporal recency axis; verification observes, never rewrites.
- `.claude/rules/autopilot-grants.md` — free-form vault writes are never
  autopilot-eligible, which is why every memory mutation is propose-gated.
- `apps/public/standard/kb/shared.py::resolve_supersession` + flag
  `feature.temporal-supersession.enabled` — the supersession primitive to
  generalize.
- `emptyos/sdk/base_app.py::propose_action` / `propose_kb_note` /
  `propose_kb_extractions` — the shipped review-gate path.
- `emptyos/runtime/vault_index.py` — the in-RAM frontmatter index that makes the
  derived trust read cheap (invariant #7).
- `scripts/check_memory_rot.py` — the existing read-only "surface, don't
  synthesize" scanner; the closest live ancestor of the fidelity audit.
- CLAUDE.md rule 9 (extract on the 2nd consumer) + rule 19 (no vault content to
  cloud).
