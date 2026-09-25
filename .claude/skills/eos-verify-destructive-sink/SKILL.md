---
name: eos-verify-destructive-sink
description: Prove whether a caller-supplied id can reach a destructive filesystem sink (unlink / rmtree / overwrite / directory move) by canary-verifying it at the correct traversal depth, then fix at the path-builder choke-point. Use when a scanner, review, or hunch flags a path built from an HTTP path param or request body, when the user says "check for path traversal", "can this delete files", "verify this guard", or after adding any route that deletes or overwrites by id. NOT for finding candidates across the repo (run scripts/check_path_builders.py first), NOT for correctness bugs in calculators (use eos-bug-audit), NOT for turning a proven heuristic into a permanent checker (use eos-graduate-audit).
---

# Verify a destructive sink

A path built from a caller-supplied id, feeding `unlink()` / `rmtree()` /
`write_text()` / a directory rename, is the highest-severity bug class in
EmptyOS: it escapes the vault and destroys data the user never backed up.
The 2026-07-19 audit found **16 exploitable instances** across unrelated apps.

This skill is the *verification* half. Finding candidates is
`scripts/check_path_builders.py`; fixing the class broadly is
`.claude/rules/` work. Here you prove one instance, or clear it.


## Prerequisites

A canary needs a live route, so the daemon must be up — but never `:9000`. Lease a
sandbox member (`.claude/rules/sandbox-driven-testing.md`) and canary there: the whole
point of this skill is to find out whether a path reaches `unlink`, and finding out on
the real vault is the outcome it exists to prevent.

## Why this needs a skill: the failure is a FALSE ALL-CLEAR

A traversal probe that comes back "not found" looks exactly like a working
guard. In the audit it meant a **wrong probe** three separate times. You do
not get a stack trace telling you your payload was too shallow — you get a
reassuring green.

Everything below exists to stop you filing "safe" on an exploitable route.

## Step 1 — count the depth BEFORE writing a payload

Read the path template and count how many directories deep the target sits
*inside the vault root*.

```
30_Resources/EmptyOS/sim/{run_id}.md        -> 3 deep
30_Resources/EmptyOS/kb/notes/{slug}.md     -> 4 deep
```

- **depth N** lands at the vault root — still *contained*.
- **depth N+1** escapes the vault. **This is the one that matters.**

Probe at N *and* N+1. A depth-2 probe against a 3-deep template proves
nothing, and that is the exact mistake that made sim look safe.

## Step 2 — probe on a SANDBOX, never :9000

```
POST /sandbox/api/lease   {"session_id":"<stable>","purpose":"verify traversal"}
POST /sandbox/api/lease/_/restart   {"session_id":"<stable>"}
```

`:9000` is mounted on the user's real vault. A working exploit there destroys
real data. Lease a member (`.claude/rules/sandbox-usage.md`), restart it so it
carries your code, and probe `:9002`.

Use Python `urllib` against `127.0.0.1` (not `localhost` — IPv6), with the
bearer token from `emptyos.toml [network] auth_token`.

## Step 3 — canary, then assert SURVIVAL

Create a file *you own* at the exact path the traversal targets, then probe,
then assert it still exists. A canary is the only evidence that distinguishes
"guard worked" from "payload missed".

```python
canary = vault.parent / "PLAYWRIGHT-TEST-canary.md"   # depth N+1 target
canary.write_text("x", encoding="utf-8")
r = call("DELETE", f"/{app}/api/{thing}/..%5C..%5C..%5C..%5CPLAYWRIGHT-TEST-canary")
print("survived:", canary.exists())     # False = EXPLOITABLE
canary.unlink(missing_ok=True)
```

`%5C` (backslash), not `%2F`: Starlette URL-decodes before routing, so a
forward slash splits the path segment and never matches the route. A backslash
survives inside one segment and *is* a separator on Windows.

## Step 4 — interrogate a negative result

If the canary survived, you have TWO hypotheses, not one:

1. the guard works, or
2. your probe never reached the sink.

Distinguish them before writing a verdict:

- Print the **raw response**. `{"error": "questions array required"}` means the
  route rejected your payload shape — the guard was never exercised.
- Check the **error text**. "invalid run id" is a guard firing. "not found" is
  ambiguous: it can mean the traversal resolved somewhere that does not exist.
- Re-probe at **depth+1 and depth+2**.
- If HTTP cannot reach the sink cleanly, verify the builder **directly** at
  unit level (`_progress_path("../../evil") is None`) and say that is what
  you did.

Never record "safe" on the strength of a probe that failed validation.

## Step 5 — fix at the choke-point, not the route

Every call site funnels through one path builder. Guard there, so no caller
can forget it.

```python
from emptyos.sdk.utils import require_path_segment

def _run_path(self, run_id: str) -> str:
    return template.replace("{run_id}", require_path_segment(run_id, "run id"))
```

- `require_path_segment` **raises** — right for a builder with many call sites.
- `safe_path_segment` returns `""` — use when a caller wants a soft miss;
  return `None` and make **every** caller handle it (grep them all first — one
  builder had nine call sites across three modules).
- Add a route-level check too when you want an in-band `{"error": ...}` rather
  than a 500.

**Platform containment is not a substitute.** `VaultIndex` refuses to write
outside the vault, but it cannot stop a write landing somewhere unintended
*inside* it. Both layers are needed.

## Step 6 — pin both directions, then re-verify live

A pin that only tests rejection lets a fix that breaks every legitimate id
pass. Assert the traversal is refused **and** that real ids still work,
including the ones the app itself generates (`demo-rc-<hex>`, `<slug>-<hex>`).

Then re-run the canary probe against a restarted sandbox, and confirm the
normal lifecycle (create → read → delete) still works end to end.

## Reporting

State the depth you probed at, that a canary was used, and which layer holds
the guard. "Blocked" without a canary is an assertion, not evidence.

If you ran the app's pytest slice, run `python scripts/check_vault_test_leak.py`
afterwards — those tests hit `:9000` and write to the real vault.

## Cross-references

- `scripts/check_path_builders.py` — finds candidates (advisory; ~43% false
  positives, since it cannot see route-level regex guards)
- `.claude/rules/sandbox-usage.md` — lease/restart/release contract
- `.claude/rules/daemon-handling.md` — never restart `:9000`
- `emptyos/sdk/utils.py` — `safe_path_segment` / `require_path_segment`
- `emptyos/runtime/vault_index.py` — `_contained`, the platform backstop
- `.claude/rules/vault-operator.md` — the test-leak guard
