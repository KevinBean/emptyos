# Engineering Assurance Index - Trust Loop

## Product and status

- App: `trust-loop`
- Assurance method: `trust-loop-v1`
- Status: **demonstration**
- Calculation: three-phase prospective fault current at the LV terminals of an
  MV/LV transformer, using the simplified radial method documented by ABB
  Technical Application Paper No. 2, clause 2.2.

This app demonstrates a complete evidence chain and a runnable conformance gate.
It is not presented as a production IEC 60909 study tool.

## Current claim

For the exact ABB 400 kVA worked case, the deterministic engine reproduces the
published 14,943 A result within the reviewed 0.5% tolerance. The app, report,
and conformance gate use the same engine execution.

## Claim boundary

The claim does not extend to arbitrary network topologies, unbalanced faults,
motor/converter contribution, minimum-fault studies, the current-edition
transformer correction factor, or equipment-duty decisions. One published case
does not validate the full input domain.

## Evidence chain

| Stage | Package | Current state |
|---|---|---|
| 1 | [Source Evidence](SOURCE-PACK.md) | partial - public source and case are identified; clause-level review receipt and archived source path remain open |
| 2 | [Calculation Specification](ALGORITHM.md) | implemented - scope, equations, limits, acceptance target, and stable requirement ids |
| 3 | [Engine Implementation](IMPLEMENTATION.md) | implemented - pure engine, traceability, and daemon-free unit tests |
| 4 | [Validation](VALIDATION.md) | demonstration - one published case plus structural/refusal tests |
| 5 | [Application](INTENT.md) | implemented - thin API/UI over the deterministic engine |
| 6 | [Reporting](REPORT-SPEC.md) | implemented - engine-returned calculation steps render the working |
| 7 | [Release Verification](RELEASE-VERIFICATION.md) | partial - API runs in push CI; unit and Chromium suites are local/on-demand |

## Executable artifacts

- `fault_current.py` - deterministic calculation and calculation-step output.
- `app.py` - thin API, method-registry adapter, conformance fixture, report.
- `manifest.toml` - method, conformance, and assurance declarations.
- `tests/test_unit_trust_loop.py` - daemon-free mathematics and report tests.
- `tests/test_sys_trust_loop.py` - API and browser user-path tests.

## Latest receipts

Durable, versioned assurance receipts are not implemented yet. Live conformance
is re-derivable through `POST /trust-loop/api/conformance/run`. Because a live
response is not a retained release receipt, this app remains
`status = "demonstration"`.

## Open deviations

1. Add or link checked clause-level evidence for the ABB method.
2. Make the authoritative source archive location discoverable from the source
   package.
3. Add independent reference cases and domain-boundary coverage before making a
   production validation claim.
4. Persist versioned conformance, hand-check, and release-verification receipts.
5. Run the unit and Chromium suites in the release gate against the packaged
   build.
