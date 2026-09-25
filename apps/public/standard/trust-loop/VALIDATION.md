# Validation Plan and Claim - Trust Loop

## Validation objective

Demonstrate that the deterministic engine reproduces the openly published ABB
clause 2.2 worked example and obeys the structural and refusal properties named
by `ALGORITHM.md`.

## Claimed applicability domain

The present claim is limited to the exact published radial three-phase example
and to the structural properties exercised by daemon-free tests. It is not a
validation claim over the full input range or over full IEC 60909 studies.

## Reference cases

| Case id | Source | Independence | Expected | Tolerance | Required |
|---|---|---|---:|---:|---|
| `abb-tap2-400kva-20kv` | ABB TAP No. 2, clause 2.2 | external published answer; same source carries the method | 14,943 A | 0.5% | yes |

The executable declaration is
`[[provides.conformance.fault]]` in `manifest.toml`. Its payload and expected
value are supplied by `_conf_published_inputs` and
`_conf_published_expected`.

## Tolerance rationale

ABB prints rounded intermediate impedances. Using the printed total impedance
reproduces 14,943 A; carrying full precision produces approximately 14,934.6 A.
A zero tolerance would require reproducing display rounding rather than the
mathematics. The 0.5% band accommodates the source precision while still
rejecting materially different readings of the equations.

## Coverage matrix

| Dimension | Normal/reference | Boundary/extreme | Invalid/refusal |
|---|---|---|---|
| published numeric anchor | covered | one point only | n/a |
| referral | round-trip property | fixed ratio examples | zero voltage refused |
| impedance | positive components | monotonic sweeps | negative/zero total refused |
| transformer data | published case | dominance property | inconsistent loss/impedance refused |
| voltage factor | published value | linearity property | non-positive refused |
| units/rounding | SI inputs + printed rounding reconstruction | limited | malformed API values refused |
| topology | one radial chain | not covered | alternative topologies not implemented |

## Structural checks

- `PROP-DOM-01`: transformer dominance for the published topology.
- `PROP-MONO-01`: increasing impedance cannot increase fault current.
- `PROP-LIN-01`: current scales linearly with voltage factor for fixed
  impedance data.
- `PROP-PYTH-01`: total impedance satisfies the R/X magnitude identity.
- `ALG-REF-01`: referring an impedance down and back returns the original.

## Acceptance rule

The mandatory published case must execute at least one declared method, and
every executed method must pass its field tolerance. Empty or entirely skipped
results do not constitute a pass. Structural and refusal unit tests must also be
green for a release verification run.

## Failure routing

- Incorrect source transcription -> Stage 1.
- Incorrect or ambiguous calculation contract -> Stage 2.
- Incorrect code -> Stage 3.
- Correct engine but wrong UI/report transport -> Stage 5 or 6.

After a repair, rerun this gate and every downstream gate.

## Validation limitations

One source and one numeric case demonstrate the mechanism but are insufficient
for a production validation claim. Required future evidence includes
independent cases, limits of every supported input dimension, unit/rounding
boundaries, and source-to-code equation review.

## Current evidence

The live endpoint re-derives the verdict on demand. A durable conformance
receipt containing commit, environment, timestamp, and result is not yet
persisted; therefore assurance status remains `demonstration`.
