# Privacy — Threat Model & Defenses

EmptyOS is a personal AI workspace. The vault holds journals, contacts,
finances, jobs, relationships — material the user wouldn't paste into a
public chat window. The system is built so that material *stays* on the
user's machine, even as the same codebase is published openly and deployed
as a public live demo.

This document pins what the system protects against, what it doesn't, and
where each defense lives.

## What counts as "personal data"

For the purposes of this system, personal data is any string that meets at
least one of:

| Category | Examples |
|---|---|
| **Identity** | Real names, email addresses, phone numbers, government IDs |
| **Location** | Home/work coordinates, real residential paths, employer name |
| **Credentials** | API keys, OAuth tokens, private-key blocks, JWTs, Bearer headers |
| **Vault content** | Journal entries, person notes, finances, jobs, health notes |
| **Specific dates** | Birthdays, visa-grant dates, anniversaries — in a context that pins them to a person |

Generic strings that *could* be personal but normally aren't (a city name,
a public domain, a software product) are out of scope. Pattern coverage is
deliberately conservative — false positives mean the release scanner gets
ignored.

The editable surface is **`.eos-personal`** in the repo root — one regex per
line. Add patterns when a leak class is found.

## Layered defense model

| Layer | Trigger | What it does | Where |
|---|---|---|---|
| **L1. Release-time scan** | `git commit` / push / PR / `release-public.py` | Scans every tracked file for `.eos-personal` + `.eos-branding` matches; aborts on hit | `scripts/check-personal.py`, `scripts/check-branding.py`, `scripts/release-public.py`, `.github/workflows/release-safe.yml` |
| **L2. Demo-vault content scan** | `release-public.py` | Runs `outbound_scan` (secrets + personal patterns) over every file in `demo/vault/`; aborts on hit | `scripts/release-public.py:scan_demo_vault` |
| **L3. Tier filter at release** | `release-public.py` | Drops apps/plugins not in `core` + `standard` tiers; drops tests that bind to dropped apps; aborts if any tracked app declares `[app] private = true` | `scripts/release-public.py:filter_to_tiers` |
| **L4. Pre-cloud scan** | Every `Capability.execute()` against a cloud provider | Scans the outbound text for 7 secret patterns + `.eos-personal`; optional local-LLM classifier/redactor; surfaces to the cloud-consent gate | `emptyos/capabilities/outbound_scan.py`, `emptyos/capabilities/__init__.py:_consent_allows` |
| **L5. Cloud-consent gate** | Before any cloud call | User must opt in (or has set a policy) before personal/secret patterns leave the machine | `emptyos/capabilities/consent.py:CloudConsentManager` |
| **L6. Runtime response scrubber** | Every HTTP response when `presentation.enabled` (auto-on in demo) | Replaces `.eos-personal` matches with `***` in JSON + HTML bodies | `emptyos/web/server.py:PresentationMiddleware` |
| **L7. Syslog write-time scrubber** | Every `kernel.syslog.{info,warn,error,debug}` call | Replaces `.eos-personal` matches with `***` in the message + data dict before SQLite insert | `emptyos/kernel/syslog.py:_scrub` |
| **L8. Demo reset/seed cycle** | Every demo container restart | Wipes `data/` (per-visitor state) and re-seeds clean sample content; runs daily on the VPS | `emptyos.toml` `[demo]`, `apps/<id>/demo/seed.py` |
| **L9. App-level gates** | Manifest + filesystem | `apps/personal/` is gitignored; `[app] private = true` blocks release; `demo.hide_apps` filters at boot | `apps/personal/`, `apps/*/manifest.toml`, `demo/emptyos.toml` |

## Threat scenarios

### T1. Accidental commit
**Scenario.** A developer writes `Kevin Bian` into a docstring or sample
config; commits and pushes.
**Caught by.** L1 (pre-commit hook if installed; CI on every push;
release-public.py refuses to snapshot a dirty tree). Pattern coverage is
tested by `tests/test_privacy_patterns.py` so a broken regex doesn't
silently turn off the gate.
**Residual risk.** Patterns might miss a new shape; that's why the
pattern file is editable and `outbound_scan` provides a second-pass at
demo-vault scope.

### T2. Seed-data contamination
**Scenario.** A future script copies the operator's real vault content
into `demo/vault/` (intentionally for a refresh, or accidentally via a
typo in a path).
**Caught by.** L2 — `scan_demo_vault` runs `outbound_scan` over every
file, catches both personal patterns and high-confidence secrets.

### T3. Cloud provider leak
**Scenario.** An app's `self.think()` call passes vault content to a
cloud LLM; the model echoes it back; the response gets logged or
re-rendered.
**Caught by.** L4 surfaces what's about to leave (the user sees the
findings in the consent prompt). L5 lets the user block. L6 scrubs
the response on its way back to the browser. L7 scrubs anything that
hits the syslog DB along the way.
**Residual risk.** The cloud provider still *received* the text — the
scrub layers operate on the local machine. If `cloud.consent = always`
(the demo default), there's no human-in-the-loop on the way out. Demo
mitigates this by being BYOK-only and stateless.

### T4. Demo state persistence between visitors
**Scenario.** Visitor A pastes their email into an EmptyOS demo form;
visitor B visits 10 minutes later and sees it.
**Caught by.** L8 — `reset_on_restart = true` + a daily restart cron
on the VPS wipe `data/` and re-seed. Visitor state survives within a
single container lifetime but not across restarts.
**Residual risk.** Within a single visitor's session window, other
concurrent visitors of the same container can see what they typed.
Demo isn't multi-tenant — single-process, shared state. The single-user
pin in `docs/AUTH.md` reinforces this.

## What we deliberately don't protect against

- **A compromised dev machine.** If the user's laptop is owned by an
  attacker, EmptyOS can't help. The vault is plain markdown on disk;
  `.eos-personal` is a code-leak gate, not a disk-encryption story.
- **Vault data the user pastes into a third-party AI through their
  browser.** Browser-side flows that don't go through EmptyOS bypass
  every layer here. We don't intercept the OS clipboard.
- **stdout/stderr capture files (`data/eos-stdout.log`,
  `data/eos-stderr.log`, `data/daemon.log`).** These are written by the
  daemon's launcher script, not by Python code, so the syslog scrubber
  (L7) doesn't reach them. The demo restart cycle wipes them; on
  self-hosted long-running deployments they accumulate unscrubbed.
  Mitigation: don't enable presentation mode AND keep
  `data/` excluded from any external backup that ships off-host.
- **The cloud provider's own retention.** Once content reaches
  OpenAI/Anthropic, their privacy policy applies — not ours.
- **Determined network observers.** EmptyOS does TLS via the reverse
  proxy. End-to-end encryption to specific recipients is out of scope.

## How to extend a defense

| You want to | Edit |
|---|---|
| Add a new personal-pattern shape | `.eos-personal` — one regex per line, then run `python -m pytest tests/test_privacy_patterns.py` |
| Add a personal pattern to a specific app's hidden state | `[app] private = true` in `apps/<id>/manifest.toml` |
| Hide an app from the public demo only | `demo.hide_apps` in `demo/emptyos.toml` |
| Add a third-party brand to the user-facing strings ban | `.eos-branding` — one regex per line |
| Tighten the runtime scrubber to cover a new content-type | `emptyos/web/server.py:PresentationMiddleware.dispatch` |

## Verification

The demo audit runs from any shell that can reach `demo.binbian.net`:

```bash
COOKIE='Cookie: eos_session=demo'
B='https://demo.binbian.net'
# Pull broad sample of user-data endpoints, then grep for .eos-personal hits
# (full script lives in this session's transcript; not yet automated).
```

The full audit was performed manually 2026-05-16 and came back clean across
38 KB of user-data endpoints + 12 patterns + 4 extra paranoia patterns
(employer, email, neighbouring cities).

The pattern coverage itself is asserted in `tests/test_privacy_patterns.py`
on every CI run via the `@pytest.mark.api` marker.
