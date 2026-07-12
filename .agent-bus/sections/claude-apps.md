

Live inventory is authoritative — `eos app list`, or browse `apps/` + `apps/personal/`. Every app is self-documenting: `eos app info <id>` generates docs from manifest + code. Don't maintain an app catalog here — it drifts.

To scaffold a new app, invoke the `eos-new-app` skill (or `eos-new-plugin` for plugins). It generates manifest, `app.py`, `pages/`, and a `tests/test_sys_<id>.py` skeleton.

To build an **engineering calculator** (algorithm doc → requirements → KB notes → pure engine → method-registry app → conformance → `basic-engineer` release bundle), follow `docs/ENGINEERING-APP-WORKFLOW.md` — the in-app pipeline (`/grill/` engineering-calculator recipe → `/app-builder/` → `/kb/` Digest doc → `engines/` → `[[provides.methods]]` → `[[provides.conformance]]` → `/release/`), optionally run autonomously via `feature-pipeline`'s conformance-gated loop.

For **any feature** (not just calculators), the general loop — strategy → brainstorm → plan → work → review → compound-learning, each phase mapped to a live surface — is `docs/ENGINEERING-WORK-LOOP.md`. There is no missing orchestration layer to build and no external "compound engineering" framework to import; the loop is already distributed across `grill` / `app-builder` / `feature-pipeline` / `dogfood-agent` / `fix-agent` / KB lessons / `agent-bus` / session-wrapup. See `docs/OPEN-SOURCE-BORROWING-PLAN.md` for the borrowed-frameworks verdict (ideas → docs, never installed).

### Store (per-user install gate)

`/store` is the per-user install/enable gate for apps + plugins + skills (Obsidian-community-plugins-style). State at `data/store/installed-{apps,plugins}.json`; loader.enabled_ids = installed - disabled ∪ essentials. `ESSENTIAL_APPS = {store, settings, hub}`; `ESSENTIAL_PLUGINS = {health}`. Demo mode bypasses the gate. The **Marketplace** tab installs third-party **apps and plugins/connections** from a GitHub repo / zip / local folder / registry through one `kind`-threaded propose→preview→confirm pipeline (apps → `apps/<category>/<id>/`, plugins → flat `plugins/<id>/`); plugins run code at boot so a static `skill_scan` is mandatory on every plugin install. "Connections" are just plugins (`connector`-tagged), surfaced as a filtered tab. Full contract: `.claude/rules/store.md`.
