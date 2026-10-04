

Live inventory is authoritative — `eos app list`, or browse `apps/`. Every app is self-documenting: `eos app info <id>`. Don't maintain an app catalog here — it drifts.

**Four grouping layers, not substitutes:** `release.toml` **tiers** (what ships), `suites.toml` **suites** (9 descriptive product chapters; surface apps compose, atom apps own data), `store_category` (launcher sections), and the `workspaces` app's per-machine **spaces**. They drift independently — **when you add an app, add it to the tier, the suite, and (for engineering) the Space.** Contract, checkers, and the load-bearing `private` / `delivery` fields → `.claude/rules/app-grouping.md`.

To scaffold a new app, invoke the `eos-new-app` skill (or `eos-new-plugin`). **Engineering calculators** follow `docs/ENGINEERING-APP-WORKFLOW.md` with `docs/TRUST-LOOP.md` as the assurance contract — every obligation carries a receipt naming *what runs it* → `.claude/rules/engineering-workflow.md`. For **any feature**, the general loop (strategy → brainstorm → plan → work → review → compound-learning) is `docs/ENGINEERING-WORK-LOOP.md`; there is no missing orchestration layer to build (`docs/OPEN-SOURCE-BORROWING-PLAN.md` for borrowed-framework verdicts).

### Store (per-user install gate)

`/store` is the per-user install/enable gate for apps + plugins + skills. State at `data/store/installed-{apps,plugins}.json`; loader.enabled_ids = installed - disabled ∪ essentials (`ESSENTIAL_APPS = {store, settings, hub}`; `ESSENTIAL_PLUGINS = {health}`). Demo mode bypasses the gate. The Marketplace installs third-party apps and plugins through one propose→preview→confirm pipeline; plugins run code at boot so a static `skill_scan` is mandatory on every plugin install. Full contract: `.claude/rules/store.md`.
