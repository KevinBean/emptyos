---
name: eos-study-notes-digest
description: Digest a synthesis document — training notes, a course summary, a hand-written design brief — into the KB when the KB ALREADY HOLDS the standards it draws on. The work is not transcription — survey for overlap first, verify every claim against the held full text, keep only the additive layer, then check what the notes require against the engines that implement it. Use when the user pastes or hands over study/training/briefing notes and says "digest this", "add this to the KB", "check this against the standards", or "file these notes". NOT for a source PDF (use vault-source-digest), NOT for atomizing a held standard into clauses (use eos-kb-atomize), NOT for post-write consistency scanning alone (use eos-kb-audit).
---

# Study Notes Digest

A synthesis document is someone's **compression of sources you already have**.
That makes it a different job from digesting a source:

| Input | Skill | The work |
|---|---|---|
| A source PDF | `vault-source-digest` | extract verbatim text + clause notes |
| A held `reference` note's archive | `eos-kb-atomize` | slice it into `clause` notes |
| **A synthesis of held sources** | **this skill** | **verify, keep only what's additive, check the code** |

The failure mode here is not a bad extraction. It is **transcribing confidently**:
restating equations the KB already states better, carrying a claim that is
directionally right and operatively wrong, and inheriting a superseded edition.

## Step 1 — Survey for overlap BEFORE writing anything

The single highest-value step, and the one that is tempting to skip because the
document looks new.

For each major section of the source, ask whether the KB already covers it —
and check **both corpora**, they are different:

```
30_Resources/EmptyOS/kb/{notes,sources}/   # clause-atomized standards, implemented_in → engines
30_Resources/KB/<domain>/{concepts,lessons,formulas,cases,guides,references}/
```

```bash
ls 30_Resources/EmptyOS/kb/sources/ | grep -iE '<standard-slug>'
grep -ril "<distinctive phrase>" 30_Resources/KB/ | head
```

**Expect the KB to win on equations and lose on judgment.** A mature KB holds
clause text and formula notes that are more precise than any synthesis. What it
usually lacks is the *practice layer* — decision rules, scenario sets, which
limit each number is compared against, what the deliverable looks like.

Write **only the additive layer**. Where the source restates something the KB
already has, cite the existing note instead of re-deriving it. A digest that
duplicates a better note has made the KB worse.

> **zsh landmine.** `FILES="a.md b.md"; grep x $FILES` passes the whole string as
> **one filename** — zsh does not word-split. Every result reads ABSENT and the
> overlap survey silently reports a clean sheet. Use an array
> (`FILES=(a.md b.md)`) or list paths literally, and sanity-check with
> `echo ${#FILES[@]}`. This has produced a false "nothing is covered" verdict.
> See `.claude/rules/environment.md` § macOS / zsh.

## Step 2 — Verify every claim against the held full text

Not "does this look right". Open the clause.

```bash
grep -n -A6 "1\.4\.51" 30_Resources/EmptyOS/kb/sources/_fulltext/<STANDARD>.md
```

Four error classes, in rising order of how badly they read on a signed sheet:

| Class | Shape | Example |
|---|---|---|
| **Clause misfiling** | right content, wrong citation | a fence rule cited to §5.2.4 when it is §5.2.8 |
| **Lossy summary** | directionally right, operative half dropped | "max sag under max fault" for a definition that reads max **load** *and* max fault |
| **Invented decomposition** | a published figure split into parts the source never publishes | `2440` written as `2140 + 300` when the clause says 2440 *includes* the 300 |
| **Superseded edition** | the source cites an edition the KB has replaced | notes citing IEEE 605-**2008** where the vault holds **2023**, clause numbers moved |

The superseded-edition class deserves its own pass, because nothing flags it and
it silently invalidates every clause number in the document:

```bash
ls 30_Resources/EmptyOS/kb/sources/ | grep -i "<standard>" | head -3   # which edition is held?
```

When editions differ, **map the clauses and record the mapping in the note** —
a client requirement citing the old edition is not wrong, it just needs a bridge.

Also check the direction the source cannot: **is an existing KB note now stale?**
A synthesis written months ago may be describing a caveat that the code has since
closed. Correct those notes in the same pass and say so — that is a real finding,
not housekeeping.

Anything you cannot verify gets `[unverified]` inline, per CLAUDE.md § Working
Agreements. **Do not smooth it into confident prose**, and do not reason on top
of it. A source document that flags its own gaps is telling you something; keep
the flag.

## Step 3 — Write the additive notes

Match the house conventions of the corpus you are writing into — read a
neighbouring note first rather than inventing frontmatter:

```bash
head -20 30_Resources/KB/<domain>/lessons/$(ls 30_Resources/KB/<domain>/lessons | head -1)
grep -m1 "^kind:" 30_Resources/KB/<domain>/guides/*.md   # the kind vocabulary is per-dir
```

- One idea per note; `kind` from the corpus's own vocabulary (it may include
  kinds the MOC's tally predates — `guide` did).
- Put `implemented_in:` on anything a calculator implements, and **state the
  implementation status honestly** in the body, including what is *not* built.
- Every note carries a **Provenance** section: what it was digested from, what
  was verified this session against which artifact, and what was corrected.
  Without it the next reader cannot tell a checked claim from a carried one.

## Step 4 — Check what the notes require against the code

This is what makes the digest worth more than a filing exercise. For each note
that names a calculation, ask the three questions:

1. **Is it implemented?** Find the engine and read it.
2. **Is it anchored?** A conformance case against a published worked example, or
   nothing (`.claude/rules/audits.md` § Failure mode 3).
3. **Is it wired?** The one that bites — a constant can exist, be correct, and be
   **consumed by nothing**.

```bash
grep -rn "<constant_or_symbol>" --include="*.py" apps engines | head
```

A defined-but-unconsumed constant is the highest-value finding this step
produces, because it looks implemented from every angle except use. Report the
gap register ordered by consequence, with a recommended build order that
separates cheap arithmetic increments from genuine builds.

**Write the gap report to a file**, not into the chat —
`30_Resources/EmptyOS/kb/outputs/<topic>-calculator-check-<date>.md`. That
subdirectory is the documented home for AI-authored reports and is skipped by
the KB audits.

## Step 5 — Validate, then decide the source's fate

```bash
python scripts/kb_link_audit.py                      # EmptyOS KB
python scripts/kb_link_audit.py --root "30_Resources/KB"   # personal KB
python scripts/kb_claim_audit.py --root "30_Resources/KB"
```

Triage the output — two known false-positive classes:

- **Cross-corpus `related:` links.** `--root` cannot resolve a personal-KB note
  pointing at `30_Resources/EmptyOS/kb/...`. Confirm the target exists by hand;
  do not "fix" a link that works.
- **`implemented_in` into `apps/personal/`** — gitignored, absent on a machine
  that does not hold it.

Then run `/eos-kb-audit` for the full consistency gate rather than re-deriving it.

**Only then answer "do we still need the source document?"** The honest test is
a coverage probe per source section, not an impression:

```bash
SECTIONS=("distinctive phrase 1" "distinctive phrase 2")
NOTES=(path/a.md path/b.md)
for t in "${SECTIONS[@]}"; do
  printf "%-32s %s\n" "$t" "$(grep -ril "$t" $NOTES | wc -l)"
done
```

Sections that legitimately do **not** belong in the KB — a build specification,
an open-decisions list, a glossary — are not covered by a KB note and should not
be forced into one. Give them a real home (an appendix to the gap report, a
project note) before dropping the source, or keep the source.

## When NOT to use this

- **The input is a source PDF** → `vault-source-digest`.
- **The KB does not hold the underlying standards** → there is nothing to verify
  against; digest the sources first, or accept the notes as `[unverified]`
  throughout and say so.
- **The document is one idea** → write the note, skip the ceremony.
- **The document is a source of record you must keep verbatim** (a client
  standard, a signed report) → file it, do not digest it into paraphrase.

## Cross-references

- `.claude/rules/audits.md` § Failure mode 3 — anchored-vs-green; Step 4's
  second question.
- `.claude/rules/environment.md` § macOS / zsh — the Step 1 landmine.
- `.claude/rules/vault-operator.md` — KB integrity scans + `--root` resolution.
- `.claude/skills/eos-kb-audit` — the post-write consistency gate (Step 5).
- `.claude/skills/eos-citation-verify` — the harder question, for a package's
  source pack: does the transcribed *figure* match the page.
- `.claude/skills/eos-index-drift-sweep` — the lossy-summary shape (Step 2's
  second class) as it appears in an **index** rather than a source document.
- `/Users/kb/Main Vault/_claude/skills/vault-source-digest` — the PDF sibling.
