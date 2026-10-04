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

Patterns are compiled **case-insensitively** (`emptyos/sdk/personal_patterns.py`),
so write `Kevin`, not `[Kk]evin`. This is deliberate: Windows paths are
case-insensitive, so a mis-cased vault or home directory is the *same* directory
while being a *different* string — and case-sensitive matching let exactly that
slip past the scan into tracked files (2026-07-17). Never rely on casing to keep
something out of a pattern's reach; use a word boundary or a more specific shape.

## Layered defense model

| Layer | Trigger | What it does | Where |
|---|---|---|---|
| **L1. Release-time scan** | `git commit` / push / PR / `release-public.py` | Scans every tracked file for `.eos-personal` + `.eos-branding` matches **and for credential shapes** (`SECRET_PATTERNS`, the same vocabulary L2/L4 use); aborts on hit. Secrets are scanned in *every* file — the `ALLOWLIST` mutes the personal class only, since the files most likely to hold a real token (`.claude/settings.local.json`, `data/personal-defaults.json`) are on it. Exempt a fixture line with an inline `check-secrets: ignore`. Findings print a redacted preview, never the matched text | `scripts/check-personal.py`, `scripts/check-branding.py`, `scripts/release-public.py`, `.github/workflows/release-safe.yml` |
| **L1b. Pre-staging scan** | `/preflight` (scopes `always` / `security` / `release`) | Same two scanners with `--include-untracked`, so a leak is caught in a **written-but-not-yet-staged** file. Tracked-only reported CLEAN while a new file held personal data (2026-08-14). `apps/personal/` stays excluded because it is a nested repo this one does not track; `engines/personal/` + `tests/personal/` became tracked on 2026-08-16 and are excluded **by explicit exemption** instead (`is_never_published`) — personal data legitimately lives in all three. The exemption is PERSONAL-class only: secrets are still scanned there | `scripts/preflight.py` `CHECKS[]` `args: ["--include-untracked"]` |
| **L2. Demo-vault content scan** | `release-public.py` | Runs `outbound_scan` (secrets + personal patterns) over every file in `demo/vault/`; aborts on hit | `scripts/release-public.py:scan_demo_vault` |
| **L3. Tier filter at release** | `release-public.py` | Drops apps/plugins/engines not in `core` + `standard` tiers; drops tests that bind to dropped apps; drops whole `tests/` subdirs via `drop_test_dirs` (`drop_tests_bound_to` globs `tests/test_*.py` NON-recursively, so `tests/personal/` had no pruning at all until 2026-08-16); **aborts if a held track/plugin/engine survives its removal** (a partial delete is never reported as a clean drop); **asserts the never-published subtrees are absent from the built snapshot** — the receipt that makes L1b's exemption safe, since a scanner that skips a path must be paired with a prune that removes it; aborts if any tracked app declares `[app] private = true`, except inside a never-published subtree (15 personal manifests set it and the snapshot never carries them) | `scripts/release-public.py:filter_to_tiers`, `emptyos/sdk/release_filter.py:prune_snapshot` + `assert_never_published_absent` |
| **L4. Pre-cloud scan** | Every `Capability.execute()` against a cloud provider | Scans the outbound text for the 9 `SECRET_PATTERNS` + `.eos-personal`; optional local-LLM classifier/redactor; surfaces to the cloud-consent gate | `emptyos/capabilities/outbound_scan.py`, `emptyos/capabilities/__init__.py:_consent_allows` |
| **L5. Cloud-consent gate** | Before any cloud call | User must opt in (or has set a policy) before personal/secret patterns leave the machine | `emptyos/capabilities/consent.py:CloudConsentManager` |
| **L6. Runtime response scrubber** | Every HTTP response when `presentation.enabled` (auto-on in demo) | Replaces `.eos-personal` matches with `***` in JSON + HTML bodies | `emptyos/web/server.py:PresentationMiddleware` |
| **L7. Syslog write-time scrubber** | Every `kernel.syslog.{info,warn,error,debug}` call | Replaces `.eos-personal` matches with `***` in the message + data dict before SQLite insert | `emptyos/kernel/syslog.py:_scrub` |
| **L8. Demo reset/seed cycle** | Every demo container restart | Wipes `data/` (per-visitor state) and re-seeds clean sample content; runs daily on the VPS | `emptyos.toml` `[demo]`, `apps/<id>/demo/seed.py` |
| **L9. App-level gates** | Manifest + filesystem | `apps/personal/` is gitignored; `[app] private = true` blocks release; `demo.hide_apps` filters at boot | `apps/personal/`, `apps/*/manifest.toml`, `demo/emptyos.toml` |

> [!note] The patterns file is itself disclosure — and is no longer published
> `.eos-personal` contains, by construction, the literal strings it guards, so
> shipping it published that list. Two other tracked files carried the same
> strings: the pattern-coverage test (its example inputs) and
> `sync_user_skills.py` (its substitution table). Raised 2026-08-14; closed
> 2026-09-24:
>
> - `.eos-personal` and the new `.eos-personal-subs` (the substitution table,
>   moved out of the script) are in `release-public.py`'s `CRUFT_PATHS`.
> - The test's real examples moved to `tests/personal/privacy_examples.py`, a
>   subtree no public snapshot carries; in a public clone those cases skip.
> - The release scans the snapshot with the **private** pattern file and the
>   allowlist **off** (`check-personal.py --patterns … --no-allowlist`), so no
>   allowlisted file can carry personal data into a release again. An empty
>   pattern set there fails instead of reporting clean. The scan covers every
>   file in the snapshot (`--root`), including any the release generated; a public clone's own
>   `release-safe.yml` run has no personal patterns, so only its secret class
>   is live there.
>
> Public tags v0.5.5–v0.6.4 still carried those files until 2026-10-05, when
> the public history was reset to a single v0.8.0 commit (`--reset-history`)
> and every older tag and release entry was deleted. A third-party fork made
> in April 2026, before that history began, still holds a copy; a rewrite on
> our side cannot reach it. A later step is to ship a redacted
> `.eos-personal.example` so a fork gets a working template.

## Threat scenarios

### T1. Accidental commit
**Scenario.** A developer writes the user's full real name into a
docstring or sample config; commits and pushes.
**Caught by.** L1 (pre-commit hook if installed; CI on every push;
release-public.py refuses to snapshot a dirty tree). Pattern coverage is
tested by `tests/test_unit_privacy_patterns.py` so a broken regex doesn't
silently turn off the gate.
**Residual risk.** Patterns might miss a new shape; that's why the
pattern file is editable and `outbound_scan` provides a second-pass at
demo-vault scope.

### T1b. Accidental credential commit
**Scenario.** A developer pastes a real API key into a config default,
a test, or a docstring; commits and pushes.
**Caught by.** L1 — since 2026-08-14 `check-personal.py` also runs
`SECRET_PATTERNS`. Before that it loaded `.eos-personal` only, which holds
identity/path/coordinate patterns and **zero credential shapes**, so a real
`sk-ant-...` passed the commit gate cleanly while this doc and the
`eos-security-review` / `eos-release` skills already advertised API-key
coverage. Both directions are pinned by
`tests/test_unit_check_personal_secrets.py`.
**Residual risk.** Detection is prefix/format-based, so a bare
high-entropy token with no recognisable prefix (a database password, an
internal service key) is not matched — this is a known-shape gate, not an
entropy scanner. And the gate stops the *commit*, not the *exposure*: a key
that reached a push is already compromised and must be rotated at the
provider, since deleting the line leaves it in git history.

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
| Add a new personal-pattern shape | `.eos-personal` — one regex per line, then run `python -m pytest tests/test_unit_privacy_patterns.py` |
| Add a new **credential** shape | `SECRET_PATTERNS` in `emptyos/capabilities/outbound_scan.py` — one entry serves L1 (commit gate), L2 (demo vault) and L4 (pre-cloud) at once. Do **not** put key regexes in `.eos-personal`: it is git-tracked and allowlisted, so a pattern there is disclosed and unenforced against itself. Calibrate first (`.claude/rules/audits.md`) — this class gates |
| Exempt a secret-shaped test fixture | Inline `check-secrets: ignore` on the matching line or the one above it — never a file-level allowlist entry |
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

The pattern coverage itself is asserted in `tests/test_unit_privacy_patterns.py`,
which needs no daemon. It runs in CI's offline architecture-guards step, so a
daemon that fails to boot cannot suppress it; the `@pytest.mark.api` marker also
keeps it in the daemon-up job.
