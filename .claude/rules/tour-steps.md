# Tour Steps Rule — Apps Contribute Walkthrough Stops

The product tour is **not** a static onboarding overlay. It walks the user through real EmptyOS pages: each step navigates to a real route and uses `EOS_UI.spotlight()` to highlight a real DOM element. Apps contribute steps via manifest (`[[contributes.tour.step]]`); the tour orchestrator (`emptyos/web/static/eos-tour.js`) reads them from `/tour/api/steps` and drives the walkthrough.

**Reference implementation:** `apps/tour/` (the orchestrator app), `apps/task/manifest.toml` (`task.capture` step), `apps/journal/manifest.toml` (`journal.write` step).

## Principles

1. **Steps are data, not code.** A row in `manifest.toml` declares the route + selector + copy. No imports, no JS hooks per step.
2. **The tour walks real pages.** Each step is a `location.href = step.route` jump (full page load — apps live at distinct prefixes), then `EOS_UI.spotlight()` on the named selector. State persists in `localStorage` (`eos.tour.v1`) so the orchestrator can resume after the page load.
3. **Capability gating is automatic.** A step with `requires = ["think"]` whose required capability has no available provider is auto-rewritten to point at `/system?capability=think` with a "set this up first" body. The tour never lands on a button that can't fire.
4. **Apps don't import the tour app.** The kernel's `call_contributions("tour", "step")` aggregator surfaces every step; tour code has zero knowledge of which apps contribute what. New app installed → new steps appear automatically.
5. **Spotlight is forgiving.** If the selector is missing, the orchestrator falls back to a center-screen tooltip so the tour still progresses. Don't ship a step that *requires* the selector exist; ship one that's still useful in fallback.
6. **Cross-machine state is best-effort.** `data/apps/tour/state.json` records `dismissed` + `last_step` for analytics and reload-resume; `localStorage` is the load-bearing one for the active session.

## When NOT to add a step

- The action lives behind authentication, paywall, or an irreversible side-effect — tour steps must be safe to *show*, not necessarily safe to fire.
- The page only renders meaningfully after the user has data — don't spotlight an empty state.
- The app is rarely used or highly specialized — every extra step costs attention; aim for ≤6 core steps in a fresh-clone tour.
- The "step" is really documentation — write it in `docs/`, link from a tooltip, don't make every reader watch it.

## Manifest shape

```toml
[[contributes.tour.step]]
id = "task.capture"             # globally unique (check /tour/debug/steps)
group = "core"                  # core / per-app / advanced; informational
priority = 200                  # lower = earlier; defaults to 100
route = "/task/"                # navigated to via location.href
spotlight = "#add-text"         # CSS selector (any document.querySelector arg)
title = "Capture a task"        # tooltip header (one short line)
body = "Type, hit Enter…"       # tooltip body (HTML allowed; keep ≤2 sentences)
requires = ["read", "write"]    # capabilities; missing → step rewritten to /system
```

## Priority bands (convention)

- **0–99** entry: home welcome, "what is this"
- **100–299** core verbs: capture, task, journal, search
- **300–599** per-app stops: a single deep stop per important app
- **600–899** advanced: settings, integrations, plugins
- **900+** closer: link back to inspector / docs / "you're done"

## Spotlight selector tips

- Use `id` selectors when possible — they're stable and unique.
- Avoid CSS classes that might be added by future styling (`.btn`, `.input`) — they'll grab the first match, often the wrong one.
- Prefer the input over the surrounding card — the user's hands go to the input.
- If the page lazy-renders, the selector polls for up to 6s before falling back to center-screen.

## Debug

`/tour/debug/steps` shows every contributed step + its resolved form (after capability filtering). Use this when "my step doesn't show" — usually the route is wrong or the spotlight selector doesn't exist on that page.

## Trigger surfaces

- **Home** (`/`): a "Take the tour" banner appears when `data/apps/tour/state.json` doesn't exist yet, and stays available as a manual trigger after dismiss.
- **`EOS.tour.start()`** can be called from anywhere — link a "Show me how" affordance from any app's empty state.
- Tour resume on every page load is automatic when `localStorage.eos.tour.v1.active === true`.

## Graduation paths

- **Per-step analytics**: today the orchestrator only POSTs `last_step` to `/tour/api/state`. If we want completion funnels, extend `state.json` to record `seen_at` per step and surface in `/tour/debug/steps`.
- **Branching tours**: today the order is one flat priority list. If a step needs to skip when a condition is met, add a `condition` field to the manifest and evaluate it server-side in `api_steps()`.
- **Per-app onboarding tours** (vs. system-wide): if an app wants its own `/<app>?tour=1` walkthrough, expose its own slot (`[[contributes.<app>.tour.step]]`) and reuse `EOS_UI.spotlight()` directly — don't fork the orchestrator.
