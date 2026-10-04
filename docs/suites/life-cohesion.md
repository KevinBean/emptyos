# Life Suite — Cohesion Report

> First per-suite cohesion pass under the suite catalog (`suites.toml`).
> Read-verified 2026-07-18 against app code (file:line anchors below).
> Purpose: (a) name the overlaps/missing bonds/merge candidates inside the
> Life suite, (b) derive the read contract the pilot surface app needs.
> Merge candidates are FLAG-ONLY — wire first, merge last (eos-restructure
> discipline). Topology/wiring facts are evidence, not the arbiter of
> membership; product jobs are.

## Members examined

journal, daily-brief, expense, people, quotes, worklog, worklog-capture,
(replay — examined and **moved out**, see Membership corrections).

## The "today" overlap — three meanings, never composed

| App | What "today" means | Rendered by |
|---|---|---|
| journal | one calendar day's entries + mood + milestone + three-things | `GET /journal/api/today` (`journal/app.py:166`), `slot_today` |
| worklog | one work day's project items with status | `GET /worklog/api/day` (`worklog/app.py:276`), `panel_active` (`:643`) |
| daily-brief | a generated news brief + a command-center snapshot of *other apps'* today-state | `panel_today` (`daily-brief/app.py:403`), `snapshots.py` |

These are three different jobs (personal day, work day, world + rollup), so
none is redundant — but nothing composes them. The user reconstructs their day
by opening three pages. **This is the pilot surface's job: one day timeline.**

Two structural facts sharpen it:

1. **daily-brief is already a proto-surface.** `snapshots.py` hard-codes
   `try_call_app` pulls from task, projects, journal (`get_summary`,
   `snapshots.py:129`), and people (`:142-145`). It proves the demand for a
   composed view — implemented as point-to-point coupling, exactly what the
   contribution-slot inversion replaces.
2. **journal is a de-facto activity timeline.** The reactor writes AI-marked
   breadcrumbs into the daily note (`reactor/app.py:88-113` →
   `journal._add_entry(as_ai=True)`) for expense (big spends only,
   `reactions_life.py:31-63`) and people (`:134-168`). So part of the "life
   timeline" already exists *as prose inside journal*, selectively and
   invisibly to any structured reader. The surface must read the sources
   directly, not scrape journal prose.

## Missing bonds

- **No shared read shape.** Each member's rows differ (journal
  `{time,mood,emoji,text,auto}` `journal/parser.py:32`; worklog
  `{text,status}` in project groups `worklog/app.py:151`; expense
  `{date,amount,description,category,source}` `expense/app.py:82`). Nothing
  can render a combined day without bespoke adapters — this is the gap the
  `timeline_items` contract closes.
- **Worklog events are all `internal`** (`worklog/manifest.toml:33-37`, by
  design — no journal ripple wanted), and expense/people ripples are
  selective. So a *pull* model (contribution methods) is the right transport
  for the surface; subscribing to events would miss most of the data.
- **Expense, people, quotes are leaf providers** — zero outward `call_app`,
  zero `optional_apps`. Fine as atoms; the suite bond has to come from the
  surface side.

## Merge candidates (flag-only — NOT actioned)

- **worklog-capture → worklog.** Capture is already a hard-dep satellite
  (`apps=["worklog"]`; every Apply lands via `worklog.log_work`
  `worklog-capture/app.py:178`). It stores only machine telemetry (queue
  dirs), never vault. Candidate: fold into worklog as a module
  (multi-module-apps pattern) in a future session. Until then it stays a
  Life member (its output is work-day data).
- **quotes** — content rotation (data/ JSON store, no date scoping,
  `quotes/app.py:27`), not life *record* data. Weak member; kept for now
  (its panel is a Life-dashboard garnish), revisit at the suite's second
  cohesion pass. Not a merge target.

## Membership corrections (applied to suites.toml)

- **replay moved Life → Automation.** Read evidence: it is a workflow
  recipe/replay engine — hard dep on rooms, steps dispatched through the
  rooms review gate (`replay/replay.py:99,144`), recipes under
  `30_Resources/EmptyOS/recipes/` with **no day-scoped surface at all**. It
  was excluded from the pilot for shape reasons and on inspection is not a
  Life app, period.

## The read contract (pilot design output)

One narrow, purpose-built contract — deliberately NOT the boards `list_all`
(none of the members has it, and their shapes genuinely differ):

```toml
# member manifest
[[contributes.life.timeline]]
id = "journal-timeline"
method = "timeline_items"
```

```python
async def timeline_items(self, days: int = 1) -> list[dict]:
    """Items for the Life day timeline, most recent first.
    days=1 → today only; days=7 → the last week.
    Item: {ts: ISO str, title: str, kind: str, href: str, body?: str}
    Extra keys allowed (mood, status, amount…); consumers ignore unknowns.
    Return [] (or None) when nothing — fail-soft per contribution contract.
    """
```

- Transport: the surface calls
  `call_contributions("life", "timeline", days=n)` (generic collector,
  `base_app.py:1384` — fail-soft, manifest-ordered, zero hard deps).
- Each member also exposes `GET /<app>/api/timeline-items?days=n` (thin
  wrapper) so the contract is HTTP-testable per member and export-safe.
- Pilot members: **journal** (entries incl. `auto` AI breadcrumbs, marked
  `kind: "journal-auto"` so the surface can style provenance), **worklog**
  (work items with status), **expense** (rows with amount/category).
  daily-brief deliberately not in the pilot (its brief is generated content,
  not a life record; revisit as a header slot on the surface later).
  people's cadence panels (`panel_reach_out`) are nudges, not timeline items
  — a "now" rail candidate for the surface's second iteration.

## Go/no-go verdict (to be filled after dogfooding)

- [ ] Surface dogfooded against real vault data (sandbox-verified, UI walk).
- [ ] Question: does the one-timeline view beat opening journal + worklog +
      expense + hub? Verdict + evidence here.
