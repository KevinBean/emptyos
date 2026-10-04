---
paths:
  - "apps/**"
  - "emptyos/web/static/**"
---
# Hub Panels Rule — Contribution-Based Hub Aggregation

The hub (`/hub/`) is a panel aggregator. Apps declare `[[contributes.hub.panel]]` in their manifest and implement a matching `panel_*` instance method. The hub calls each contributor in parallel, dispatches to a named renderer, and fails soft per panel. Hub code has zero knowledge of which apps contribute what.

**First reference implementation:** `apps/public/core/task/` `pulse-stats` + `todays-tasks`, `apps/public/standard/projects/` `upcoming-deadlines`, `apps/public/core/hub/` (own panels). Full docs at `docs/APP-DEVELOPMENT.md` § "Hub Panel Contributions".

**Sibling namespace — `hub-life.panel` (deliberate, don't converge).** The personal life dashboard (`apps/personal/hub-life/`) aggregates the UNION of `hub-life.panel` + `hub.panel` contributions (`resolve_panels`), while core `/hub/` reads only `hub.panel`. So the parallel namespace IS the scoping mechanism: a panel declared `[[contributes.hub-life.panel]]` appears on the life dashboard but never leaks onto the generic hub. This was reviewed 2026-07-10 (architecture review) and kept — replacing it with a `surface = "life"` field on `hub.panel` would rebuild the same filter with core-hub churn. It's the same shape as any app-defined slot (`cable.calculator`, `work.deliverable`): declare into `hub.panel` for both surfaces, into `hub-life.panel` for life-only.

**Two slots, one aggregator.** Keeping the namespaces separate never required keeping two *aggregators*, and for a long time there were two — byte-identical, and they drifted where it counted. Since 2026-08-16 both boards delegate to `emptyos/sdk/panels.py::resolve_panels(contributions, *, call, include_lazy, only, on_error)`; each host supplies only its slot list. A third panel host writes a slot list and a delegation, never a third copy.

**`only=` is why `lazy` means anything.** A host's single-panel endpoint must pass `only=<id>` so the narrowing happens *before* any contributor is called. Both hubs used to resolve every contributor and discard all but one, which cost the whole board per refresh (a flat 4.1s on a 114-panel hub) and executed the very lazy panels the declaration exists to defer — including a 60s cached LLM synthesis. The symptom is unreadable if you don't know to look for it: two unrelated panels time *identically*, because neither figure is about the panel you asked for.

## Principles

1. **Panels are data + renderer name, not HTML.** Apps describe state; the hub ships the visual.
2. **One universal contribution type.** All hub content — alerts, lists, tiles, bars, countdowns, chips — uses `[[contributes.hub.panel]]`. Renderer choice controls shape.
3. **Fail-soft, not fail-hard.** `None` from a panel method = drop silently. Exceptions are caught, logged to syslog, and do not break other panels.
4. **Apps don't import each other to appear on the hub.** If app A wants a tile showing data from app B, A calls B via `self.call_app("b", "method")` inside its own `panel_*` method. Hub doesn't broker.
5. **The hub is not a bulletin board.** One panel per app is typical. More than two means you're probably coupling too much of the app to the hub; put detail in the app's own page.

## When to add a panel

- Your app has a glanceable signal — a count, a ritual prompt, a recent item, a progress bar — that the user would act on from the home screen.
- The signal changes on events your app already emits.

## When NOT to add a panel

- Detail views, admin panels, configuration, search UI → those live in `apps/<id>/pages/index.html`.
- Data that updates faster than once per minute → link to it from a lightweight stat tile instead.
- Every internal piece of your app's state → pick at most two glanceables.

## Manifest shape

```toml
[[contributes.hub.panel]]
id = "my-panel"              # unique across all apps (check /hub/debug/panels)
method = "panel_my_thing"    # instance method on this app
renderer = "stat-tile"       # a key of RENDERERS in emptyos/web/static/eos-hub-renderers.js
title = "Optional header"    # used by renderers that show a title
priority = 150               # lower = higher on page
group = "dashboard"          # optional; groups panels visually
limit = 5                    # optional cap on list output
lazy = true                  # optional; placeholder + hydrate on scroll
```

## Priority bands

- **10–49** hero chrome (weather, pinned chips)
- **50–149** cognitive layer (alerts, slots, today's tasks, AI insights)
- **150+** ambient layer (dashboard tiles, goals, countdowns, month compare, yesterday, quote)
  — ⚠ **invisible on `/hub/`.** This band aggregates but does not render there (see
  the next paragraph); it surfaces only on the personal `hub-life` dashboard. 63 of
  102 live contributions sit here. If you want your panel on the core hub, author
  it **below 150** — do not read this table as "dashboard tiles go at 150+".

**What the home surfaces actually render (as of the 2026-06 home-companion redesign).** The bands above still drive aggregation, but the home front-end is opinionated about what it *shows*. Since 2026-10-03 there are **two hosts of one policy**: `/portal/` (the landing surface — its home board under the composer, `apps/public/standard/portal/pages/portal-home.js`) and `/hub/` (`apps/public/core/hub/pages/hub.js`). Both call `EOS_HUB_RENDERERS.visibleBlocks()` in `emptyos/web/static/eos-hub-renderers.js`, which also owns the renderer map and the companion lanes; the styles are `eos-hub-renderers.css`. Change the policy there, never in a host:

- **Ambient band (priority ≥ 150) is dropped entirely** ("decision 3") — not dimmed-below-a-divider. A panel authored at 150+ aggregates at `/hub/api/panels` but **does not appear on `/hub/`**. Author at <150 if you want it visible.
- **Cognitive band (priority < 150) renders in the "Explore" section**, except: `task`/`calendar`-source panels are suppressed (the Now/Today lanes surface them), `hero-weather` is consumed by the header chip, and `hub-welcome` is replaced by the greeting.
- **`quick-add` / `outcome-box` renderers** are pulled into the host's `.hub-quick-zone` (`#hub-quick` on the hub, `#portal-quick` on portal) near the top.
- **`pinned-refs` always shows** regardless of band (honours the 📌 Pin-to-home promise).

So `/hub/api/panels` remains the live aggregator (both hosts' `loadExplore` consume it) — the mechanism is **not** dead — but a contribution only reaches the user if it clears these filters. Lanes (`/hub/api/digest`) are the primary surface; panels are the secondary "Explore" catalog.

## Method shape

```python
async def panel_my_thing(self) -> dict | list[dict] | None:
    data = ...
    if not data:
        return None  # drops silently
    return {...}     # shape matches the declared renderer
```

Always `async def`. Return `None` for "nothing to show right now". The hub applies `limit` when the return is a list.

## Renderer contracts (quick reference)

See `docs/APP-DEVELOPMENT.md` for the full table. Common picks:
- **`stat-tile` + `group = "dashboard"`** — one number tile, most common contribution.
- **`bar` + `group = "goals"`** — a progress bar with label + detail.
- **`countdown-tile` + `group = "countdowns"`** — a days-remaining card.
- **`plain-list` / `chips`** — list of links. Canonical row keys: `title` + `subtitle` + `href` (+ optional `icon`). The renderer also tolerates the legacy aliases `label` / `sub` / `name` / `date` on read so old contributors don't break — but new code should write the canonical keys. Lazy `plain-list` panels safely render nothing while `data == null` (pre-hydration).
- **`task-list`** — actionable rows with checkboxes.
- **`stat-tile` (no group)** — standalone tile.

If no existing renderer fits, don't inline HTML. Add a renderer to `apps/public/core/hub/pages/index.html` with a documented data contract, then use it.

## Lazy panels

Use `lazy = true` when your method can take >500ms (LLM call, big vault scan, network fetch). The hub renders a placeholder on first paint and lazily loads the panel when it scrolls into view. Already applied to `ai-insights` on the hub app.

**A full-board hydrate has a per-contributor budget.** `/hub/api/panels/all`
(and hub-life's twin — the `/debug/panels` listing) runs every lazy contributor
at once through `asyncio.gather`, which waits for the slowest, so one stuck LLM
panel held the whole listing (12 s → >90 s on the same tree, 2026-09-12). Each
contributor now gets `[apps.hub] panel_timeout_s` / `[apps.hub-life]
panel_timeout_s` (default `emptyos.sdk.panels.DEFAULT_PANEL_BUDGET_S`, 12 s —
under the test client's 15 s and above the slowest healthy lazy panel measured;
`0` disables). The single-panel `/api/panel/{id}` hydrate stays unbounded on
purpose, so a slow panel can still finish when asked for alone. A contributor
that raises its *own* `TimeoutError` (a socket) is reported as `failed`, never
as the budget — `asyncio.timeout(...).expired()` is the only signal that the
budget fired.

**A budget expiry MARKS the row, it never deletes it.** The row comes back with
`data: null` and a human `unavailable` reason, logged as `timed out after Ns`.
This is not politeness: the budget cannot tell a stuck panel from a healthy one
**queued behind a shared lock**, and the only caller that sets a budget is a
listing whose product is the *complete* set of contributions. Measured
2026-09-12 — two LLM-backed panels blew a 12 s budget on every full-board
hydrate and were silently deleted from the one page that exists to enumerate
them. Both mechanisms are in play and neither is a fault: the think chain's
first provider holds a one-slot semaphore, so concurrent LLM panels queue; and
on a cold cache the work itself is slow (`repo-review.recent-convergence`
measured **0.0 s warm and 144 s cold** — the warm figure is what first made
this look like pure queue wait, so take a single timing of a cached panel as
evidence of nothing). Either way the budget is reading wall-clock it cannot
attribute, which is exactly why it may bound latency but must not decide
existence. A contributor that *raises* is still dropped, which is the
pre-existing fail-soft contract.

Cancelling a contributor mid-think is safe only because the providers reap
their subprocess on `CancelledError` too, not just on their own timeout
(`claude_cli.py` / `codex_cli.py`, fixed the same day — before that a
budget-cancelled think left a `claude` process running until its stdout pipe
filled).

## Grouping

Panels sharing a `group` value render together. The first panel's renderer handles the group. Useful for:
- `dashboard` — `stat-tile` grid from many apps
- `goals` — `bar` list
- `countdowns` — `countdown-tile` row
- `month-compare` — `compare-tile` grid

To contribute into an existing group, match the group name and the renderer (`stat-tile` for dashboard, `bar` for goals, etc.).

## Enforced invariants

`scripts/check_hub_panels.py` (preflight `--scope ui`, **gates**) statically pins
the two rules above that are provable from the manifest + the renderer bundle:

- **The renderer must exist** in `eos-hub-renderers.js`'s `RENDERERS` map. A typo paints a red
  `Unknown renderer: <name>` box on the home screen. Deliberate exception: an
  inline `# hub-panel-check: ignore renderer <name>` at the manifest site.
- **The panel id must be unique across all apps** (DOM collision; lazy hydration
  via `/hub/api/panel/{id}` would fetch the wrong one).

Both are silent on a healthy tree. `_retired/` apps are skipped (the loader skips
them too), and `[[contributes.hub-life.panel]]` is not checked — that namespace
targets the gitignored personal dashboard, so declaring it **is** the opt-out for
a life-only renderer. The ambient band is reported as an advisory, never gated.

## Debug

`/hub/debug/panels` shows every panel's raw data + rendered preview. Per-panel reload button. Use this before asking "why doesn't my panel show up" — it's usually the data shape not matching the renderer, or the method returning `None`.

## Graduation from addons

The `[contributes.<app>.<slot>]` manifest pattern (mentioned in `addons.md` as a graduation path) is implemented for `hub.panel`. Addons remain for **user-configured URL templates** (simple data in `emptyos.toml`); hub panels are for **code contributions from apps** (app exposes a method, hub calls it). They're complementary, not alternatives.
