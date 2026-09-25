# Calculation Report Specification - Trust Loop

## Report purpose and audience

The report lets an engineer inspect how one prospective fault-current result
was produced. It is a checkable demonstration sheet, not a signed design study.

## Required identity

Every durable report must identify:

- calculation title and case;
- run timestamp or run id;
- app and engine/method versions;
- calculation-specification version;
- source and conformance anchor, when applicable.

The current live Markdown report identifies method and source but does not yet
persist all version/run fields. That is an open release-evidence gap.

## Required inputs

Every engine input must appear with its value and unit:

`u_net`, `i_k_net`, `c`, `r_mv`, `x_mv`, `s_n`, `u_2n`,
`vk_pct`, `pk_pct`, `r_lv`, and `x_lv`.

## Required calculation lines

Each derived line carries:

| Field | Requirement |
|---|---|
| group | position in the derivation |
| symbol | the engineering symbol |
| formula | symbolic relationship |
| substitution | actual numeric operands |
| value | full numeric value used downstream |
| display value | readable formatting only |
| unit | explicit engineering unit |
| note | ambiguity, assumption, or source decision at its point of effect |

The derivation runs from given inputs through supply network, MV cable,
transformer, LV cable, summed R/X/Z, and final `I_k3`.

## Source and ambiguity disclosure

The report cites ABB Technical Application Paper No. 2, clause 2.2. The network
impedance line states that the current form is used and that the paper also
prints a conflicting power form. Applicability limits link back to
`ALGORITHM.md`.

## Validation statement

The published-case comparison applies only when the full input payload matches
the published fixture. When any input changes, the report/UI must say that no
published-case anchor applies; a green claim cannot follow arbitrary inputs.

## Rounding policy

Calculations retain full floating-point precision. Formatting may shorten a
display value but must not feed rounded values back into downstream equations.
The report may explain source rounding separately.

## Supported formats

- Live HTML table rendered from the API result.
- Markdown from `GET/POST /trust-loop/api/report`.
- Print-to-PDF from the browser.

A dedicated signed PDF is not currently claimed.

## Hand-check procedure

For a release verification:

1. Check the source-impedance line from its displayed substitution.
2. Check one transformer line.
3. Check the total R/X/Z line.
4. Recalculate the final current from the displayed full-precision total.
5. Record reviewer, date, differences, and report hash.

No durable hand-check receipt exists yet.

## Consistency invariant

The report consumes the `steps` returned by the same engine execution as the
headline result. No report renderer may reimplement an engineering formula.
`TestCalculationReport` and the system report tests enforce this boundary.
