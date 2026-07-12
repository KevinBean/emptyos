---
name: eos-security-review
description: Review an EmptyOS app for defensive security posture — cloud-consent compliance, vault-to-cloud data leaks, XSS via wrong escapers, PII/secrets in tracked code, and auth assumptions — bounded by the existing AGENTS.md security rules and scripts/check-*.py scanners. Use when the user says "security review", "audit this app for security", "check the security posture", "is this app safe to ship", or before opening an app to a public/demo deployment. DEFENSIVE / AUDIT ONLY — refuses offensive use (writing exploits, evasion, attacking third parties). DO NOT use for — a full design audit (use eos-page-design-review / eos-design-system-audit), per-file code cleanup (use eos-simplify), or correctness bugs (use /code-review).
---

# EmptyOS Security Review

A defensive read on one EmptyOS app: does it honour the system's security contract before
it ships or goes public? Bounded by the **existing** rules and scanners — this skill checks
compliance, it does not invent a new security framework.

This is the security sibling of `eos-page-design-review`. Borrowed *idea* from a
cybersecurity-skills catalog (names + framework mappings only — no attack steps, no
auto-loaded skill fleet). See `docs/OPEN-SOURCE-BORROWING-PLAN.md` (S5).

## Hard boundary — defensive only

This skill **only** does defensive review and authorized audit. It will:
- ✅ Find missing cloud-consent gating, vault-to-cloud leaks, XSS holes, PII in tracked
  files, unsafe auth assumptions, and map them to the rule they violate.
- ✅ Propose the minimal fix (the right escaper, the consent path, the `.eos-personal` pattern).

It will **refuse**:
- ❌ Writing exploits, payloads, or proof-of-concept attacks.
- ❌ Detection/consent-gate evasion, or any "make it bypass the gate" request.
- ❌ Attacking, scanning, or targeting third-party systems.
- ❌ Mass/credential-stuffing tooling.

If the request is offensive, stop and say so. Authorized pentest / CTF framing does not
change this skill's scope — it reviews EmptyOS apps for *their own* hardening.

## Inputs

Ask the user for:
1. **Target** — app id or path (`apps/public/standard/journal` / `journal`).
2. **Mode** — `review` (findings only, propose-don't-edit) or `apply` (fix in place).
3. **Deployment context** (optional) — is this headed for `network.mode = "public"` or a
   demo container? Public-bound apps get stricter checks.

If only an app id is given, scan its `manifest.toml`, `app.py` (+ helper modules), and
`pages/*.html`.

## Phase 1 — Read the app

Load the manifest, the Python, and the pages. Cross-reference the contract:
- AGENTS.md rule 18 — cloud consent mandatory (non-localhost providers).
- AGENTS.md rule 19 — no vault data to cloud by default.
- AGENTS.md rule 13 — no PII / personal paths / secrets in tracked code (`.eos-personal`).
- AGENTS.md rule 14 — no third-party branding in user-facing strings (`.eos-branding`).
- `.Codex/rules/shared-frontend.md` — `esc()` / `escAttr()` XSS discipline.
- `docs/AUTH.md` — single-user pin; no per-user identity inside the daemon.
- `.Codex/rules/autopilot-grants.md` — verbs that are never autopilot-eligible.

## Phase 2 — The checklist (map every finding to a rule + a scanner)

| Dimension | What to look for | Rule | Existing scanner |
|---|---|---|---|
| **Cloud consent** | `self.think/draw/speak` etc. that could hit a cloud provider without going through the capability chain; any app-level cloud-specific code path | rule 18 | — (manual; capability gate is the enforcement) |
| **Vault → cloud** | large vault excerpts embedded in a system prompt that hits a cloud model; raw note bodies sent off-box without per-request opt-in | rule 19 | — |
| **XSS** | user/vault content interpolated into HTML with the wrong escaper (`esc` for text vs `escAttr` for attributes), or no escaper | shared-frontend | `scripts/check-attr-escaper.py` |
| **PII / secrets** | personal paths, names, coords, API keys, tokens in tracked files | rule 13 | `scripts/check-personal.py` |
| **Branding** | third-party brand names in user-facing strings | rule 14 | `scripts/check-branding.py` |
| **Auth** | per-user identity assumptions, a users table, anything beyond the single-user pin | docs/AUTH.md | — |
| **Autopilot** | a `[[provides.verbs]]` entry marked `eligibility = "stable"` that is actually free-form / irreversible / outbound | autopilot-grants | `eos verb describe <verb>` |
| **Write boundary** | unvalidated loose field shapes, vault read-modify-write without a lock | dev-gotchas | — |

Run the static scanners against the target before reading by hand — they catch the
mechanical cases fast:

```
python scripts/check-personal.py
python scripts/check-branding.py
python scripts/check-attr-escaper.py
```

## Phase 3 — Report

For each finding: **dimension · severity (high/med/low) · file:line · the rule it violates ·
the minimal fix**. Group by severity. A clean app gets an explicit "no findings" — do not
manufacture issues to look thorough (the `audits.md` false-positive trap).

In `apply` mode, fix only what's unambiguous (wrong escaper → right escaper; a leaked path
→ move to `emptyos.toml`). Anything requiring a judgment call stays a proposed finding.

## Phase 4 — Wire back (optional)

If the review surfaces a *recurring* class of issue (same hole in ≥3 apps), that's a
platform fix, not a per-app one (AGENTS.md feedback `platform_fix_for_n_app_bugs`): graduate
it into a `scripts/check-*.py` scanner per `.Codex/rules/audits.md`, and add it to the
defensive-checks index (`30_Resources/EmptyOS/kb/notes/moc-defensive-security-checks.md`).

## Cross-references

- `.Codex/skills/eos-page-design-review/SKILL.md` — the shape this skill mirrors.
- `scripts/check-personal.py`, `check-branding.py`, `check-attr-escaper.py` — the scanners.
- `30_Resources/EmptyOS/kb/notes/moc-defensive-security-checks.md` — the defensive index.
- `docs/OPEN-SOURCE-BORROWING-PLAN.md` — why this skill exists (cybersecurity-skills borrow).
