# Intent — Garden

> Living design doc for `apps/garden/`. Edit as the app evolves.
> Birth certificate: `~/.claude/plans/contributes-garden-plot-inhabitants-synchronous-cosmos.md`.
> Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why

A contemplative overlay that renders Kevin's life as a programmatic SVG garden —
8 plots (one per wellbeing dimension), plants whose density and health mirror
real signals from existing apps. The wheel rule (CLAUDE.md #16) says dimensions
shape *what we build* but never appear as UI; garden is the one allowed
exception — a single mirror surface, not a feature added to other apps.

Two hard constraints:

1. **Zero coupling out.** No `[[contributes.garden.*]]` slot exists. No other
   app calls into garden. `git rm -rf apps/garden/ data/apps/garden/` removes
   every trace. Direction is one-way: garden reads from others.
2. **No asset pipeline.** Pure programmatic SVG via L-systems + curated
   palettes + SVG filters. No sprites, no ComfyUI, no AI image gen at runtime.

## Relationships

**Calls into** (`self.call_app(...)` or read helpers — all wrapped in try/except, degrade to empty):
- `wheel` (runtime module `emptyos.runtime.wheel.collect_signals`) — per-dimension intensity counts; the load-bearing read
- `projects` — `list_open` for occupational plot's bamboo entities
- `people` — `vault_query(tags=["person"])` last_contact for social plot
- `kb` — recent additions for intellectual plot
- `journal` — mood entries for emotional plot, milestones for spiritual
- `expense` — recent deltas for financial plot soil tone
- `weather` — recent observations for environmental plot particles

**Emits:**
- `garden:tick` — internal; fires after scheduler recompute, no external listeners expected

**Listens for** (`@on_event`):
- (none) — garden is pure read + render; no inbound state changes

## Open questions

- V1 ships with `appleton` as default theme based on mockup validation; users select via Settings panel. Theme picker UX may want preview thumbnails (V1.5).
- Click-through resolution: when a plant maps to a vault entity, we open `EOS_UI.timeline4D(path)`. For plants without an entity (signal-only intensity), click does nothing — should it open a "what fed this plot" explainer? (V2 candidate.)
- Hub mini panel renderer (`garden-mini`) is one-line registered in `apps/hub/pages/index.html`. This is the only outbound touch — a renderer name in hub. Acceptable per `.claude/rules/hub-panels.md` (renderer additions are documented contract, not coupling).

## Future

- Seasonal/time-of-day theme modulation (dawn palette, autumn drift)
- Vines connecting plots (cross-dimension correlation: e.g. exercise → mood)
- Themable cursor / hover state per theme
- Export single plot as SVG for blog/devlog embedding
- 6th theme contributed by users (each theme = one CSS + filter snippet)
- Plant-to-task affordance — *if and only if* dogfood shows it's wanted, and even then only as an outbound link (garden never accepts task creation from itself)
