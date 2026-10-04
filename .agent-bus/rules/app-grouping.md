---
paths:
  - "suites.toml"
  - "release.toml"
  - "scripts/check_suites.py"
  - "scripts/check-tier-folder.py"
  - "scripts/generate_apps_doc.py"
  - "scripts/generate_tiers_doc.py"
  - "apps/**/workspaces/**"
  - "apps/**/life/**"
  - "products/**"
---

# App Grouping — tiers, suites, store categories, spaces

Moved out of CLAUDE.md § Apps (2026-09-25). CLAUDE.md keeps the one-paragraph
summary; this file keeps the contract and the measured drift.

**Suites (product chapters).** `suites.toml` (repo root) is the descriptive catalog grouping tracked apps into 9 named suites (Tasks & Projects, Knowledge, Life, Companion, Studio, Automation + the Engineer, English and Build lines) — the middle layer between distributions (release tiers) and apps. Two app kinds by convention: **surface apps** (one per suite, compose members via `[[contributes.<suite>.<slot>]]` + `call_contributions`, own no data — pilot: `apps/public/standard/life/`, dark) and **atom apps** (own their data + verbs — everything else). Purely presentational: the kernel never reads it, tiers stay literal, all apps equal at runtime. Validated by `scripts/check_suites.py` (preflight, gates on unresolvable ids). Per-suite cohesion passes run via the `eos-suite-cohesion` skill; reference report `docs/suites/life-cohesion.md`.

**Four grouping layers, not three.** Apps are grouped four ways and they are not substitutes: `release.toml` **tiers** decide what ships, `suites.toml` **suites** are the descriptive product chapters above, `store_category` drives launcher/store sections, and the `workspaces` app's **spaces** (`data/apps/workspaces/overrides.json`) are runtime, goal-oriented groupings a user curates — with `derive_bundle` as a deliberate one-way Space→tier authoring path. The overlap is real and accepted, and it drifts: as of 2026-08-28 the `engineer` suite and the `engineering` space held the **same 28 apps**, and registering `cable-bonding` in the suite alone moved them to 29 vs 28 the same day. `workspaces` never reads `suites.toml`, and the space lives in gitignored per-machine state, so nothing can sync them for you. When you add an app, add it to the tier, the suite, **and** (for engineering) the Space.

Two of the four are checked (2026-08-31). `check-tier-folder.py` runs at `scope: ["apps", "release"]` — so it fires at `/preflight`, not only at release — and walks a tier's `plugins`/`skills`/`engines` as well as its `apps`, because it previously modelled the relationship over one array and missed a skill renamed out from under `[tiers.standard]`. `check_suites.py` covers every feature-bearing group, not just `public/`, and prints its remaining exemptions as `note:` lines rather than skipping them silently; suite coverage is 154/154 with 9 suites. **Still unchecked: `store_category` and Space membership.** Nothing validates a `store_category` against the allowed set, so a typo silently files an app under a phantom launcher section; and the Space — gitignored per-machine state, so nothing in the repo can see it.

Two fields are load-bearing rather than descriptive. A suite over a held track **must** set `private = true`: `filter_suites_toml` keys on exactly that, so without it the public snapshot ships a browsable menu of app ids nobody outside can install. And every tier carries `delivery`, naming how it actually reaches a user (a `[targets.*]`, a `products/*/product.toml`, a Dockerfile, or `none`) — seven read `none`, and they are not one kind of thing: four (`labs`, `dev`, `english-learning`, `business`) are a folder invariant plus documentation, `demo` is documentation only, `basic-engineer` is documented intent, and `plus` has no binding *yet*. `delivery` is free prose validated by nothing, so it can rot exactly as a tier's skill list did.
