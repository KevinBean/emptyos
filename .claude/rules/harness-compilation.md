---
paths:
  - "apps/extension/dev/model-bench/harness_compiler.py"
  - "apps/extension/dev/model-bench/behavioural_runner.py"
  - "apps/extension/dev/model-bench/manifest.toml"
  - "apps/extension/dev/model-bench/pages/**"
  - "apps/public/core/hub/app.py"
  - "tests/test_unit_model_bench_harness_compiler.py"
  - "tests/test_unit_hub_route.py"
---

# Harness Compilation — generated code is a candidate artifact, never live state

`model-bench/harness_compiler.py` is EmptyOS's first bounded model-written-code
experiment. It optimises one pure Hub routing fast path. It is not a general
optimizer, compiler, or permission to make application code self-modifying.

## Required boundary

- The target starts with a human-authored typed contract, runnable baseline,
  allowed-primitives catalogue, frozen labelled training cases, and a holdout
  set the reflection model never sees.
- Persist every candidate as readable source with its parent, validation errors,
  metric, provider provenance, and operational estimates. Candidate faults are
  scored/reported; they do not abort the search or disappear from history.
- Validate generated source with the AST allowlist before execution. Execute it
  only when `EOS_SANDBOX_POOL_MEMBER=1`, through the subprocess runner inside a
  leased sandbox. A Python subprocess by itself is not an OS sandbox.
- Evaluate the winning training candidate against holdout exactly once. A win is
  a proposal for human review, tests, and an ordinary code change. Never import,
  patch, or hot-load it into Hub at runtime.
- Keep operational pressure in the metric. For this pilot that means quality
  minus language-model fallback rate, with latency and estimated cost visible.
- Capability handles stay narrow. Never expose arbitrary Python callbacks,
  filesystem/network authority, environment access, or cloud credentials to a
  generated candidate.

## Architecture boundary

Build this feature on `GraphRunner` and `RunRegistry`. Do not add a second SDK
optimizer or run store. The present loop has one atomic propose/evaluate action,
so `Pipeline` would add no stage-preview or resume value. Graduate a shared
compiler only after a second read-verified, same-shape target exists.

Lineage: DSPy Flex 3.3.0 + GEPA, studied 2026-08-15. Borrowed the candidate-source,
diagnostic-feedback, frozen-eval, and lineage disciplines; neither dependency nor
their pickle checkpoints/raw-host-callback bridge was imported.
