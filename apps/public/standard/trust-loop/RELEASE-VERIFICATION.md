# Release Verification Contract - Trust Loop

## Release identity

This file defines the required gate. Actual release evidence must be generated
for a specific version, commit, package, environment, and execution time.

## Included components

A release candidate must identify the versions/hashes of:

- Source Evidence Package;
- `ALGORITHM.md`;
- `fault_current.py` and method version;
- Trust Loop app manifest/API/UI;
- report generator;
- conformance and E2E suites.

## Required test matrix

| Layer | Suite | Environment | Required for verified release | Current automation |
|---|---|---|---|---|
| assurance structure | `check_engineering_assurance.py` | daemon-free | yes | preflight apps/docs/release |
| engine | `tests/test_unit_trust_loop.py` | daemon-free Python | yes | local/on-demand |
| API/conformance | API class in `tests/test_sys_trust_loop.py` | throwaway CI daemon | yes | every push |
| browser | interactive class in `tests/test_sys_trust_loop.py` | Chromium + throwaway daemon | yes | local/on-demand |
| report consistency | unit + API + browser report tests | both | yes | split across the suites |
| packaged build | reference journey against packaged artifact | release sandbox | yes | not yet retained |

## Mandatory end-to-end journey

1. Open the real Trust Loop page.
2. Load the published ABB case.
3. Submit the real UI/API payload.
4. Confirm the rendered current matches the engine and carries method version.
5. Confirm the conformance panel shows expected, computed, deviation, tolerance,
   and verdict.
6. Generate the report and confirm it matches the rendered result.
7. Change one impedance input.
8. Confirm the result changes and the published-case anchor disappears.
9. Confirm the narrow layout remains usable and no JavaScript errors occur.

## Environment matrix

A verified release must record operating system, Python version, EmptyOS
version, browser version for interactive tests, and dependency lock/package
inventory. The demonstration does not yet retain this as one receipt.

## Packaging checks

- App manifest, Python modules, page assets, and assurance documents are present.
- The calculation runs without a model or network provider.
- The source link and claim boundary remain visible.
- A clean installation can load the app.
- The packaged code is the same commit named by the receipts.

## Known limitations

- Only one external numeric reference case.
- No current durable source-review, conformance, hand-check, or release receipt.
- Unit and Chromium suites are not both hard gates on every push.
- This is a demonstration of the method, not approved design software.

## Deviations and skips

A required failed or skipped check prevents `verified` or `released` status.
A demonstration may expose the incomplete evidence, but it must not hide it
behind a green label.

## Failure routing

| Failure | Owner stage |
|---|---|
| wrong source/case | 1 |
| wrong equation/scope/tolerance rationale | 2 |
| wrong deterministic result | 3 |
| conformance mismatch | 2 or 3 after triage |
| API/UI transport, units, stale badge | 5 |
| report divergence or missing disclosure | 6 |
| packaging/browser/environment regression | 7 |

Rerun the owning stage's checks and all downstream gates after a fix.

## Release decision

Current decision: **demonstration only**. Promotion requires the missing durable
receipts, independent validation breadth appropriate to the claim, and a green
run of the complete required matrix against the packaged build.
