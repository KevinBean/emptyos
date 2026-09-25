---
paths:
  - "scripts/check-personal.py"
  - "scripts/check-branding.py"
  - "scripts/release-public.py"
  - "scripts/release.py"
  - ".eos-personal*"
  - ".eos-branding"
  - "emptyos/capabilities/outbound_scan.py"
  - "emptyos/sdk/release_filter.py"
  - "docs/PRIVACY.md"
---

# Personal-Data Gate — the detail behind CLAUDE.md rule 13

Moved out of CLAUDE.md § Development Rules (2026-09-25).

Two pattern classes, two homes, one gate (`scripts/check-personal.py`, which is what the pre-commit hook runs): **personal** shapes live in `.eos-personal`; **credential** shapes live in `SECRET_PATTERNS` (`emptyos/capabilities/outbound_scan.py`), shared with the demo-vault and pre-cloud scans. Never add a key regex to `.eos-personal` — it is self-allowlisted, so a pattern there is unenforced and sits in git history. Since 2026-09-24 it and `.eos-personal-subs` are dropped from every public snapshot, and the release scans the snapshot with the private pattern file and the allowlist off (`docs/PRIVACY.md`). Secrets are scanned in every file (the allowlist mutes personal only); exempt a fixture line with an inline `check-secrets: ignore`.

**"Git-tracked" stopped implying "published" on 2026-08-16**, when this private repo started tracking `engines/personal/` + `tests/personal/`: those subtrees are pruned from every public snapshot, so the PERSONAL class skips them via `is_never_published` (`emptyos/sdk/release_filter.py`, where the list sits beside the prune that enforces it and the assertion that proves it). The SECRET class does not skip them — a leaked credential is compromised the moment it enters git history, public or not.

**Shipped defaults count as code (rule 15).** Seed content, a template, a fallback hostname — anything a fresh user gets without configuring anything — in `apps/public/` or `apps/extension/` must read as generic product content. Caught twice: `publish`'s default branding-framework template shipped the author's personal positioning as every new site's seed note; `ppt`'s export fallback silently pointed at the author's own demo server. No scanner for this — it's a review-time judgment call.
