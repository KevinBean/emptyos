---
name: eos-citation-verify
description: Verify an engineering package's source citations against the primaries it claims — is each source actually held, does each transcribed figure match the page, and does the tier label tell the truth. Use when the user says "check the citations", "are the standards referenced properly", "verify the source pack", before signing off a calculator's Stage 1, or when a value is about to be repeated as fact in a report. NOT for whether links resolve (that is kb_link_audit / kb_claim_audit) and NOT for the document-structure contract (that is check_engineering_assurance.py).
---

# Citation Verify

Scanners answer *"is the paperwork consistent?"*. This answers *"is the citation
true?"* — and those come apart, because **no checker in the repo opens a PDF.**
`check_engineering_assurance.py` validates document structure,
`check_traceability_targets.py` resolves row targets, `kb_claim_audit.py`
resolves paths. All three pass on a package whose numbers were transcribed
wrong, whose source is not on the machine, or whose tier label is a wish.

Written after a pass where all four scanners were green and the package still
had an unheld source backing a shipped constant, a tier pointing at a table that
did not exist, and an attribution that does not survive checking — one of which
was printed into the generated calculation sheet as flat fact.

## The five questions, in order

Each is cheap and each has caught a real defect. Do not skip 1 to get to 3.

### 1. Is the source actually held?

For every row of the source pack's `Held` column, resolve the path.

- A path that resolves → good, go to Q3.
- **Prose instead of a path** — "supplied 2026-09-01", "on file", "provided by
  the client" — is the finding. It reads as held and is not retrievable.
- Before reporting it missing, search properly: by filename, by *title* (a
  brochure downloaded from the publisher is often `714---Web.pdf`, not
  `TB714.pdf`), and by first-page text across the likely folders. In the founding
  pass the "missing" brochure was sitting in `~/Downloads` under its catalogue
  number and the whole finding changed to "file it".

### 2. Does the tier label match reality?

A package that tiers its sources (`cited` / `referenced` / …) is making a claim
about its own evidence. Check the label against Q1:

- `cited` on a source that is not held is **false** — that is the defect.
- `referenced` / `not opened` on an unheld source is **honest**, and is the
  right answer until someone opens it. Say so; do not "fix" it by upgrading.

### 3. Does each transcribed figure match the page?

Open the primary. Compare **every** number the pack states, not a sample.

```bash
python3 -c "
import fitz; d = fitz.open('<pdf>')
for i in range(d.page_count):
    t = d[i].get_text()
    if '<a distinctive string>' in t: print('PDF page', i+1); print(t[:2000]); break"
```

Two things to record while you are in there, because they are free and they are
what makes the verification worth more than a diff:

- **A sentence in the source that independently confirms an assumption** the
  package makes. The founding pass found the brochure stating *"the width of the
  asphalt layer corresponds to the width of the model"* — which is the app's
  full-width scope, confirmed by the source rather than asserted by the author.
- **Printed page vs PDF page.** Cite the printed number (that is what a reader
  turns to) but note the offset if they differ.

### 4. Does the attribution survive, not just the number?

The subtle one, and the one scanners can never reach. A figure can be
transcribed perfectly from a source that does not say it.

Ask what kind of document the cited source *is*. If a materials value is
attributed to a standard about *operating conditions*, that is a category
mismatch and the attribution is probably wrong even though you cannot open the
standard to prove it. Corroborate three ways:

- What does the rest of the corpus say that source covers? A KB lesson recording
  *"right physics, wrong clause"* about the same standard is strong evidence.
- Does a source you **do** hold cite it, and for what? If TB 963 invokes a
  standard for its *soil* value, that standard is a soil table, not a pavement one.
- Does the number appear in any held source at all? A value matching nothing you
  hold has an unknown origin regardless of what it is labelled.

An unverified attribution is a **question, not a premise** (CLAUDE.md § Working
Agreements). Record the doubt with its grounds; do not silently keep asserting it
and do not delete the value.

### 5. Where does the claim get repeated?

Grep for the citation outside the source pack. This is where a bad attribution
does damage:

```bash
grep -rn "<the standard number>" apps/<pkg> engines/ --include=*.py --include=*.md
```

A doubted claim inside a comment is a note. The same claim inside a **generated
report, a UI blurb, or a picker label** is the package telling an engineer
something untrue in a document they sign. Fix those first, and check sibling apps
— a shared dataset propagates one bad attribution into every consumer.

## Fixing what you find

- **Unheld source** → file it beside its siblings, using the naming convention
  already there. Then re-run Q3 against it; a newly filed source is unverified.
- **Doubted attribution** → do not delete the value and do not keep the claim.
  Record it as data with its grounds (a per-row provenance tier beats a prose
  note, because a consumer can read it), correct every user-facing repetition,
  and add a `docs/DEFERRED-WORK.md` row whose trigger is *obtaining the primary*.
- **A tier pointing at something that does not exist** → build the thing or drop
  the claim. Prose describing a table is not a table.
- **Receipts** — state what you compared **and what you did not**. "Clause
  numbering and titles only; the equation text was not compared
  character-for-character" is worth more than a receipt implying full coverage.
  A receipt that overstates is the vacuous-pass failure of
  `.claude/rules/audits.md` § Failure mode 3, in prose form.

## When NOT to use this

- **The primaries are not obtainable at all.** Then the honest move is the tier
  label, not a verification pass.
- **Link/path resolution** — `kb_link_audit.py`, `kb_claim_audit.py`,
  `check_traceability_targets.py` already do that, deterministically, in seconds.
- **Document structure** — `check_engineering_assurance.py`.
- **A single value with an obvious source** you can check in one read.

## Cross-references

- `.claude/rules/audits.md` § Failure mode 3 — green because it checks nothing;
  this skill is the human-judgment half that no scanner covers.
- `.claude/rules/deep-research.md` — Q4 is its *triangulate + adversarially
  refute* moves applied to one citation.
- `.claude/rules/self-audit-loops.md` — the engineering-assurance rows, and why
  every obligation needs a receipt naming what runs it.
- `.claude/skills/eos-mutation-verify/SKILL.md` — the sibling discipline for
  tests. Same question in a different domain: has this ever actually been proven?
- `docs/TRUST-LOOP.md` — Stage 1 (Source) is what this verifies.
