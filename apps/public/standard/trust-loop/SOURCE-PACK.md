# Source Evidence Package - Trust Loop

## Engineering question

What source evidence supports the simplified radial three-phase fault-current
calculation and the numeric case used to judge the engine?

## Authoritative source

| Field | Value |
|---|---|
| Publisher | ABB SACE |
| Title | *Technical Application Paper No. 2 - MV/LV transformer substations: theory and examples of short-circuit calculation* |
| Document id | `1SDC007101G0202` |
| Edition/date | February 2008 |
| Relevant section | clause 2.2 and its worked example |
| Official URL | https://library.e.abb.com/public/2c522f583c884a4fbdf3968e1fdf1481/1SDC007101G0202.pdf |
| Recorded SHA-256 | `8AA1E5DF48B80A68270FC610A21F32949A3903033C070869D05662D6E9CA4FF6` |
| Role | open worked-example carrier; not a current governing design standard |

The underlying method follows IEC 60909. The ABB paper is used because its
worked answer is public and independently inspectable.

## KB evidence records

The author's mounted vault contains these reusable records:

- `abb-tap-2-mv-lv-transformer-substations-short-circuit` - source identity,
  provenance, reading map, and currency warning.
- `case-abb-400-kva-transformer-fault-current` - published inputs, independent
  reproduction, result, and validation limit.
- `abb-radial-short-circuit-method-corrections` - equation corrections,
  current-standard cross-check, and scope boundary.
- `abb-tap-2-fact-check-and-currency` - currency assessment.

Public clones need not contain the private vault. The identifiers remain stable
links when that KB is mounted.

## Relevant evidence

| Evidence | Source location | Used by |
|---|---|---|
| series impedance referral and summation | clause 2.2 | `ALG-REF-01`, `ALG-SUM-01` |
| current-form source impedance | clause 2.2 | `ALG-NET-01` |
| transformer test-data impedance | clause 2.2 | `ALG-TR-01` |
| 400 kVA worked example | clause 2.2 worked case | `VAL-ABB-01` |
| printed 14,943 A answer | clause 2.2 worked case | conformance expected value |

## Source ambiguity register

The paper prints current- and power-based source-impedance expressions with
different powers of the voltage factor. They disagree on consistent data. The
current form agrees with the same-page equation, IEC 60909-0:2016 section 6.2,
the paper's intermediate value, and its final worked result. Stage 2 records the
chosen interpretation; Stage 1 records that the conflict exists.

## Currency and applicability

This is an educational application paper from 2008, not a current design
standard or product catalogue. Its simplified example omits parts of a current
full IEC 60909 study. It is suitable as a regression anchor for the documented
simplified method, not as authority for final equipment duty.

## Review status

**Partial.** Source identity, hash, worked-case transcription, reproduction, and
currency warning are recorded. The assurance package does not yet contain:

- a discoverable archived source path tied to the recorded hash;
- checked `kind: clause` records for the exact relevant text;
- a retained source-review receipt naming reviewer and review date.

Until those exist, Stage 1 must not be represented as fully approved.
