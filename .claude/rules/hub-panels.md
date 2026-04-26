# Hub Panels Rule — Contribution-Based Hub Aggregation

The hub (`/hub/`) is a panel aggregator. Apps declare `[[contributes.hub.panel]]` in their manifest and implement a matching `panel_*` instance method. The hub calls each contributor in parallel, dispatches to a named renderer, and fails soft per panel. Hub code has zero knowledge of which apps contribute what.

**First reference implementation:** `apps/task/` `pulse-stats` + `todays-tasks`, `apps/projects/` `upcoming-deadlines`, `apps/personal/hub/` (own panels). Full docs at `docs/APP-DEVELOPMENT.md` § "Hub Panel Contributions".

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
renderer = "stat-tile"       # one of the 20 renderers in pages/index.html
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

The hub draws an "Ambient" divider between layers. Ambient panels are dimmed slightly.

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
- **`plain-list` / `chips`** — list of links.
- **`task-list`** — actionable rows with checkboxes.
- **`stat-tile` (no group)** — standalone tile.

If no existing renderer fits, don't inline HTML. Add a renderer to `apps/personal/hub/pages/index.html` with a documented data contract, then use it.

## Lazy panels

Use `lazy = true` when your method can take >500ms (LLM call, big vault scan, network fetch). The hub renders a placeholder on first paint and lazily loads the panel when it scrolls into view. Already applied to `ai-insights` on the hub app.

## Grouping

Panels sharing a `group` value render together. The first panel's renderer handles the group. Useful for:
- `dashboard` — `stat-tile` grid from many apps
- `goals` — `bar` list
- `countdowns` — `countdown-tile` row
- `month-compare` — `compare-tile` grid

To contribute into an existing group, match the group name and the renderer (`stat-tile` for dashboard, `bar` for goals, etc.).

## Debug

`/hub/debug/panels` shows every panel's raw data + rendered preview. Per-panel reload button. Use this before asking "why doesn't my panel show up" — it's usually the data shape not matching the renderer, or the method returning `None`.

## Graduation from addons

The `[contributes.<app>.<slot>]` manifest pattern (mentioned in `addons.md` as a graduation path) is implemented for `hub.panel`. Addons remain for **user-configured URL templates** (simple data in `emptyos.toml`); hub panels are for **code contributions from apps** (app exposes a method, hub calls it). They're complementary, not alternatives.
