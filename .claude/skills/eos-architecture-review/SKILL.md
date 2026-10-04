---
name: eos-architecture-review
description: Architecture-level system review (check mode) or growth/repair (fix mode), driven by missing connections and underused capabilities rather than a checklist. Use when the user says "review the system", "system health", "architecture review", or "audit the architecture" (check mode), or "grow the system", "prune events", "fix wiring", or "wire the orphans" (fix mode). Bare "check" / "audit" / "review" / "fix" / "what's next" are NOT this skill — they belong to whichever surface the user actually named, or to eos-session-resume for "what's next". NOT for per-file code review (use eos-simplify), environment/daemon probing (use env-check / preflight), or KB note↔calculator/reference consistency (use eos-kb-audit).
---

# EmptyOS System Check & Fix

Two modes: **check** (thorough step-by-step architecture review) and **fix** (identify issues and resolve them).

## When to Use

- User says "review the system", "system health", "how's the OS", "architecture review", "audit the architecture" → **check mode**
- User says "grow the system", "wire the orphans", "what should merge" → **fix mode**

> **Bare "check" / "audit" / "review" / "fix" / "improve" / "what's next" are NOT this skill**,
> in the body as well as the frontmatter. They name no surface, and every other audit skill
> answers to them too. Route them to whichever surface the user actually named — or to
> `eos-session-resume` for "what's next". Being already loaded is not a reason to claim them.
- User says "check connections", "prune events", "fix wiring", "topology health" → **fix mode (connections)**
- Periodic check-in on system health and completeness
- After adding new apps, plugins, or external services
- When planning the next development session

## Philosophy

EmptyOS grows organically. Growth is not a checklist — it's driven by:
1. **What connections are missing** — apps that should talk but can't
2. **What capabilities are underutilized** — speak/listen/draw providers exist but few apps use them
3. **What the user actually uses** — high-traffic apps deserve richer features
4. **What external services exist but aren't absorbed** — DNA intake opportunities
5. **What UI is missing** — an app without UI is half an app

連接 (Connect) is one of the six lifecycle verbs from EmptyOS's 唯识 consciousness model. The value of the system is not in individual apps — it's in the connections between them (因缘和合, dependent origination). Dead connections are noise; missing connections are lost potential.

---

## Reference files (read on demand)

| File | Read when |
|---|---|
| `<SKILL_DIR>/completeness-scan.md` | Check mode Step 2.5 — the paste-and-run app-completeness script |
| `<SKILL_DIR>/growth-dimensions.md` | Fix mode Phase 2/3 — the 6 growth dimensions, REGROW, and the absorption path |

## Check Mode — Thorough Step-by-Step Architecture Review

When the user asks to "check" the system, run a structured diagnostic that walks through each layer. **Complete each step fully before moving to the next.** Present findings at each step, don't batch everything into one summary.

### Prerequisites

The EmptyOS daemon must already be running on `:9000`. Probe it:

```bash
curl -s -m 5 http://127.0.0.1:9000/api/apps | python -c "import sys,json; print(f'Apps: {len(json.load(sys.stdin))}')"
```

**If it is down, do NOT start it.** `python -m emptyos start`, `restart.bat`, and
`taskkill` against `:9000` are forbidden from a Claude session
(`.claude/rules/daemon-handling.md`) — the daemon is user-owned, and one spawned
from a tool inherits that tool's process group and dies with it, which is why the
backgrounded `&` form this section used to recommend never actually worked. Ask
Kevin to run `restart.bat`, then re-probe.

**A leased sandbox member is only a partial substitute here** — unusually for this
codebase, that escape hatch does not fully apply to *this* skill. `integrity`
resolves paths from `config.path.parent`, which on a pool member is
`sandbox-900N/` with no `.eos-personal` and no `tests/`, so **P1/P2/P5/P10/P12
collapse**. Only the source-derived dimensions (P3, P4, P6, P7, P9, P13) are
comparable off `:9000`. If you review on a sandbox, say which dimensions the
scores actually cover.

### Step 1: Scale & Vitals

Quick pulse check — is the system alive and at expected size?

```bash
curl -s http://localhost:9000/integrity/api/audit
```

Report:
- Total score (X/140, Y%)
- App count, endpoint count, total LOC
- Any dimensions scoring below 10 — list them with scores
- Compare against last known state if available

### Step 2: Architecture Layers

Check the structural depth and topology health.

```bash
curl -s http://localhost:9000/api/topology/layers
```

Report:
- Layer breakdown (L0 Providers → L7 Composition) with node counts
- Cycle count — 0 is healthy, any cycles are critical. This is `cycles` (hard `calls_app` loops only). `stats.cycle_groups` is different: it also folds in `optional_calls_app` rings, so a large group there is soft coupling, not a broken hierarchy (`emptyos/web/topology.py` `_analyze_layers`)
- Data coupling — which vault folders have multiple direct readers
- Top fan-in nodes (most depended on — these are load-bearing)
- Top fan-out nodes (most dependencies — these are fragile)

### Step 2.5: App Completeness Scan

Filesystem-only check (runs even if the daemon is down). Verifies every app has the
canonical file set — manifest, app.py, pages/, a smoke test — and flags any `README.md`
inside an app dir (Rule 6: apps self-document via `eos app info`).

**Read `<SKILL_DIR>/completeness-scan.md` and run the script there.** It is a single
paste-and-run block; the test discovery inside it is index-based for a reason the file
explains, so do not simplify it into a per-app grep.

### Step 3: Capability Utilization

How well are the 16 capabilities being used across apps?

```bash
curl -s http://localhost:9000/api/topology
```

Parse edges where `type=uses_capability` to count apps per capability. Report:
- Each capability with app count and % of total apps
- Underutilized capabilities (< 10% usage) — these represent dormant potential
- Provider availability vs. actual usage

### Step 4: Connectivity & Event Health

Is the event bus alive? Are apps talking to each other?

From the topology data, report:
- Total app-to-app calls (calls_app edges) — more = healthier interconnection
- Total events emitted (emits_event) vs listened (listens_event)
- Unheard events — emitted but no listener wired
- Orphan apps — no incoming or outgoing connections beyond capabilities
- Top event emitters and top event listeners

For connection-specific issues, classify:

| Issue | Action | Priority |
|-------|--------|----------|
| **Unheard events** (emitted, no listener) | Wire into reactor for journal ripple, OR remove emit if noise | Medium |
| **Orphan apps** (no edges in/out) | Add `requires.apps` or `provides.events.emits` to manifest | Medium |
| **Dead reactor handlers** (listens to event no one emits) | Remove handler from reactor | Low |
| **Missing cross-app calls** (apps that should share data) | Add `call_app()` integration | High |
| **Circular dependencies** (A→B→A) | Break cycle by using events instead of direct calls | Critical |

### Step 5: Six Verbs (唯識) Health

The consciousness model — is the system metabolically alive?

```bash
curl -s http://localhost:9000/integrity/api/audit
```

From `P10 Six Verbs`, report each verb:
- 吸收 Absorb, 生長 Grow, 扎根 Root, 連接 Connect, 涌現 Emerge, 反省 Reflect
- Points per verb (X/6) and which layers are active (skill, app, agent, scheduled, events, api)
- Any verb with missing layers = growth opportunity

### Step 6: Integrity Dimensions

Walk through each of the 14 dimensions from the integrity audit:

| Dimension | What to check |
|---|---|
| P1 Generatable | Can the system generate new apps? |
| P2 Reusable | SDK modules, frontend adoption % |
| P3 Connected | Orphans, unheard events |
| P4 Atomic | Any monolith apps? Undecomposed? |
| P5 Self-Testing | Health plugin, human fallback |
| P6 Expressive | Custom UI coverage % |
| P7 Self-Documenting | All apps documented? |
| P8 Vault External | Any hardcoded vault paths? |
| P9 Reactive Vault | Activity events, vault writers |
| P10 Six Verbs | See Step 5 |
| P11 Security | High/medium issues |
| P12 Privacy | Personal data leaks |
| P13 Scale | LOC distribution, largest app |
| P14 Wellbeing Balance | Is each wellbeing dimension served by the last 30 days of activity (journal `#tags`, habit completions)? A lens on what to build next (rule 16), never a UI fix |

For any dimension < 10, explain specifically what's wrong and what would fix it.

### Step 6.5: AI-Native Score (optional lens)

How AI-native is the app tree — backend LLM use vs assistant reach vs visible AI UI?

```bash
python scripts/check_ai_native.py        # tier counts + dark-AI list (no daemon needed)
```

Report the one-line scorecard (exemplar / partial / dark / no-ai counts, avg score) and the dark-AI count trend vs the last check. **Don't triage the dark list here** — if the user wants the AI lens in depth (per-app matrix, gap ranking, DARK_OK triage), hand off to the `eos-ai-native-audit` skill; this step is just the vital sign.

### Step 7: Improvements List

Finally, the prioritized action items.

```bash
curl -s http://localhost:9000/api/topology/improvements
```

Report:
- Count by priority: critical / high / medium / low
- List each item with description and recommended action
- Identify which items could be fixed immediately vs. need planning

### Step 8: Growth Verdict

Synthesize all 7 steps into a brief verdict:
- Overall health: Thriving / Healthy / Needs Attention / Critical
- Strongest dimensions
- Weakest dimensions
- Top 3 recommended next actions (with priority) — fold any "Real gaps" from Step 2.5 into this list
- Whether the system has grown well since last check

**Only after the full check is complete**, ask the user if they want to fix any issues found.

---

## Fix Mode — Identify Issues and Resolve Them

When the user asks to "fix", "grow", or "improve", work the improvements list.

### Phase 1: Audit Current State (READ-ONLY)

Query three live APIs in parallel (EmptyOS must be running on localhost:9000):

```bash
# 1. Improvements — prioritized actionable fix list
curl -s http://localhost:9000/api/topology/improvements

# 2. Architecture layers — depth, cycles, critical path, data coupling
curl -s http://localhost:9000/api/topology/layers

# 3. Integrity audit — 14-dimension score out of 140
curl -s http://localhost:9000/integrity/api/audit
```

**Read the improvements list first.** It already aggregates topology + integrity into prioritized items with file paths. If there are critical/high items, fix those before anything else.

For deeper investigation of a specific app:
```bash
# Focused subgraph — all dependencies for one app
curl -s http://localhost:9000/api/topology/node/{app_id}

# Vault data health — check if notes have expected structure
curl -s "http://localhost:9000/api/vault/reconcile?folder={vault_folder}&tags={expected_tags}&fields={expected_fields}"
```

### Phase 2: Identify Growth Opportunities

**Read `<SKILL_DIR>/growth-dimensions.md` now** — Phases 2 and 3 classify every
opportunity by dimension, and the definitions live there.

Based on the API responses, classify:

| Type | Signal | Example |
|------|--------|---------|
| **Missing connection** | Two apps should talk but don't | healing → nutrition |
| **Underutilized capability** | Provider exists, few apps use | `draw` only 2 apps |
| **Unheard events** | Emitted but nobody listens | dead signals |
| **Shallow app** | Few endpoints vs expected depth | app with 2 endpoints that should have 10 |
| **Infrastructure gap** | Platform service missing | shared component not extracted |
| **Data coupling** | Multiple apps read same vault folder directly | should go through owning app |

### Phase 3: Prioritize

The `/api/topology/improvements` response is already sorted by priority:
- **critical** — cycles, broken dependencies → fix immediately
- **high** — integrity violations scoring < 7/10 → fix this session
- **medium** — data coupling, monolith apps, unheard event streams, integrity dimensions two points short, a lifecycle verb at 3/6 → plan and execute
- **low** — integrity dimensions one point short → do when touching that app (the list no longer flags an app for lacking `pages/`: auto-UI is the default, Dev Rule 7)

**Within the same priority, prefer:**
1. Load-bearing apps (highest fan_in from layers API) — fixing these helps the most apps
2. Apex apps (highest fan_out) — these are most sensitive to breakage
3. High-coupling data folders — reducing coupling prevents cascading changes

### Phase 4: Execute the Improvement Round

Work the improvements list top to bottom:

```
1. GET /api/topology/improvements          ← get the list
2. Fix item #1                             ← edit files, enrich vault, wire events
3. GET /api/topology/improvements          ← re-check (list should shrink)
4. Fix item #2                             ← repeat
5. ...until list is empty or only long-term items remain
```

For vault data coupling fixes:
```
1. GET /api/vault/reconcile?folder=...&tags=...    ← check note structure
2. POST /api/vault/enrich {paths, tags, defaults}  ← add missing tags (safe, never overwrites)
3. GET /api/vault/reconcile                         ← verify compliance
4. Migrate app code to vault_query() or call_app()  ← swap implementation
5. GET /api/topology/improvements                   ← confirm item resolved
```

For connection fixes (unheard events, orphans):

1. **Wire it** — add `@on_event` handler in reactor with journal ripple:
   ```python
   @on_event("app:event_name")
   async def on_app_event(self, event):
       self._log_action("app:event_name", f"summary: {event.data}")
   ```
2. **Prune it** — remove the `self.emit()` call if the event serves no purpose
3. **Bridge it** — if two apps should communicate, add event emission on one side and a listener on the other

For each fix:
1. **Backend first** — add missing endpoints (they're the soil)
2. **UI grows from backend** — each new endpoint enables new UI
3. **Extract shared components** — as you write specific pages, identify reusable pieces
4. **Test** — verify in browser at localhost:9000
5. **Re-check** — `/api/topology/improvements` should show fewer items

### Verify After Fixes

> **Health-probe safety rule.** Against a running daemon use
> `curl -s http://127.0.0.1:9000/api/health`; offline use `python scripts/preflight.py`.
> **Never `python -m emptyos health`** — it boots a kernel and opens a syslog SQLite
> handle while the user's daemon holds the same files (`.claude/rules/daemon-handling.md`).

```bash
# Re-check improvements — count should drop
curl -s http://localhost:9000/api/topology/improvements | python -c "import sys,json; d=json.load(sys.stdin); print(f'Remaining: {d[\"total\"]} ({d[\"by_priority\"]})')"

# Verify integrity score improved
curl -s http://localhost:9000/integrity/api/audit | python -c "import sys,json; d=json.load(sys.stdin); print(f'Score: {d[\"total_score\"]}/{d[\"max_score\"]} ({d[\"pct\"]}%)')"
```

---

## Growth Dimensions, REGROW, and absorption

The 6 dimensions the system grows along (UI grows FROM every other dimension,
never separately), the REGROW decision, and the WRAP → ABSORB → REPLACE → SHED
evolution path all live in `<SKILL_DIR>/growth-dimensions.md`. Phase 2 above
points you there at the moment you need them; this heading is the back-reference.

## Key APIs (localhost:9000)

| Endpoint | What it returns |
|----------|----------------|
| `GET /api/topology` | Full graph: nodes + edges (raw) |
| `GET /api/topology/layers` | Layered analysis: depth, cycles, critical path, data coupling, fan-in/out |
| `GET /api/topology/improvements` | **Start here.** Prioritized fix list with file paths |
| `GET /api/topology/node/{id}` | Focused subgraph for one node (1st + 2nd degree neighbors) |
| `GET /integrity/api/audit` | 14-dimension integrity score out of 140 |
| `GET /integrity/api/verbs` | Six Verbs health detail |
| `GET /api/vault/reconcile?folder=&tags=&fields=` | Check vault notes against expected structure |
| `POST /api/vault/enrich` | Add missing tags/defaults to vault notes (safe, never overwrites) |

## Key Reference Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | System DNA |
| `docs/DESIGN.md` | Architecture + UI philosophy |
| `emptyos/web/server.py` | Topology APIs |
| `emptyos/runtime/vault_index.py` | VaultIndex: reconcile, enrich, query |
| `emptyos/runtime/vault_map.py` | DEFAULT_PATHS: app → vault folder mapping |
| `emptyos/web/static/topology.html` | Live topology visualization |
| `apps/personal/integrity/app.py` | Integrity audit: 14-dimension scoring |

## Anti-Patterns

- Don't build apps nobody will use
- Don't add complexity before connections
- Don't build infrastructure without apps that need it
- Don't replace external services that work well
- Don't write generic templates before specific pages
- An app without UI is half an app
- A page without a backend is a wireframe
- Never remove an event that another app *could* listen to — only prune truly dead signals
- Prefer event-based communication over direct `call_app()` (events over imports principle)
- `README.md` inside an app directory — apps self-document via `eos app info` (Rule 6); a README is a smell, not a feature
