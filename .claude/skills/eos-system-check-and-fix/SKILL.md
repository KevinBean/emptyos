# EmptyOS System Check & Fix

Two modes: **check** (thorough step-by-step architecture review) and **fix** (identify issues and resolve them).

## When to Use

- User says "check", "audit", "review the system", "how's the OS", "system health" → **check mode**
- User says "fix", "grow", "improve", "what's next" → **fix mode**
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

## Check Mode — Thorough Step-by-Step Architecture Review

When the user asks to "check" the system, run a structured diagnostic that walks through each layer. **Complete each step fully before moving to the next.** Present findings at each step, don't batch everything into one summary.

### Prerequisites

EmptyOS daemon must be running on localhost:9000. If not:
```bash
cd D:\emptyos && python -m emptyos start &
# Wait for startup, verify with:
curl -s http://localhost:9000/api/apps | python -c "import sys,json; print(f'Apps: {len(json.load(sys.stdin))}')"
```

### Step 1: Scale & Vitals

Quick pulse check — is the system alive and at expected size?

```bash
curl -s http://localhost:9000/integrity/api/audit
```

Report:
- Total score (X/130, Y%)
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
- Cycle count — 0 is healthy, any cycles are critical
- Data coupling — which vault folders have multiple direct readers
- Top fan-in nodes (most depended on — these are load-bearing)
- Top fan-out nodes (most dependencies — these are fragile)

### Step 2.5: App Completeness Scan

Filesystem-only check (runs even if the daemon is down). Verifies every app has the canonical file set.

```bash
PYTHONIOENCODING=utf-8 python -c "
import sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path

NO_PAGE_OK = {'tour'}
NO_TEST_OK = {'_example', 'tmpl', 'tests', 'test-app'}

def find_test(app_id, scope):
    norm = app_id.replace('-', '_')
    # Check both core tests/ and tests/personal/ regardless of scope —
    # engineering personal apps (cer-hosting etc.) put tests in core dir.
    cands = [
        f'test_sys_{app_id}.py', f'test_sys_{norm}.py',
        f'personal/test_{norm}.py', f'personal/test_sys_{norm}.py',
    ]
    for c in cands:
        if (Path('tests') / c).exists(): return True
    for p in Path('tests').rglob('*.py'):
        n = p.name
        if n.startswith(f'test_sys_{norm}_') or n.startswith(f'test_{norm}_') or n.startswith(f'test_dogfood_{norm}'):
            return True
    return False

def scan(root, scope):
    apps = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith('_') or d.name == 'personal': continue
        if not (d/'manifest.toml').exists(): continue  # stub dir, skip
        apps.append({
            'id': d.name,
            'core_ok': (d/'app.py').exists(),
            'page': (d/'pages'/'index.html').exists(),
            'test': find_test(d.name, scope),
            'seed': (d/'demo'/'seed.py').exists(),
            'readme': (d/'README.md').exists(),
        })
    return apps

core = scan(Path('apps'), 'core')
personal = scan(Path('apps/personal'), 'personal')

def fmt_section(name, apps):
    n = len(apps); t = sum(a['test'] for a in apps); r = sum(a['readme'] for a in apps)
    print(f'{name}: {n} apps · {t} with smoke test · {r} with README')

print('=== App Completeness ===')
fmt_section('Core', core)
fmt_section('Personal', personal)
print()

gaps = []
for a in core + personal:
    if not a['core_ok']:
        gaps.append(f'- {a[\"id\"]}: missing app.py')
    if not a['page'] and a['id'] not in NO_PAGE_OK:
        gaps.append(f'- {a[\"id\"]}: missing pages/index.html')
    if not a['test'] and a['id'] not in NO_TEST_OK:
        norm = a['id'].replace('-', '_')
        gaps.append(f'- {a[\"id\"]}: missing test_sys_{norm}.py')
    if a['readme']:
        gaps.append(f'- {a[\"id\"]}: README.md present (anti-pattern, Rule 6)')

if gaps:
    print('Real gaps:')
    for g in gaps: print(g)
else:
    print('Real gaps: none')
print()
print('(Templates/scaffolding excluded:', ', '.join(sorted(NO_TEST_OK)) + ')')
"
```

If the user asks for the **full per-app table** (`--full-table` or "show the full app table"), expand the script to print one row per app with columns `id | core | page | test | seed | readme` for both `apps/` and `apps/personal/`. Default output stays scannable.

Report:
- Counts (apps, smoke-test coverage, README count)
- "Real gaps" — entries that fail the contract and aren't in the intentional-exception allowlist
- Anti-pattern: any `README.md` inside an app dir — Rule 6 says apps self-document via `eos app info`, not READMEs

### Step 3: Capability Utilization

How well are the 7 capabilities being used across apps?

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

Walk through each of the 13 dimensions from the integrity audit:

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

For any dimension < 10, explain specifically what's wrong and what would fix it.

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

# 3. Integrity audit — 13-dimension score out of 130
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
- **medium** — data coupling, monolith apps → plan and execute
- **low** — missing UI pages, unheard events → do when touching that app

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

```bash
# Re-check improvements — count should drop
curl -s http://localhost:9000/api/topology/improvements | python -c "import sys,json; d=json.load(sys.stdin); print(f'Remaining: {d[\"total\"]} ({d[\"by_priority\"]})')"

# Verify integrity score improved
curl -s http://localhost:9000/integrity/api/audit | python -c "import sys,json; d=json.load(sys.stdin); print(f'Score: {d[\"total_score\"]}/{d[\"max_score\"]} ({d[\"pct\"]}%)')"
```

---

## Growth Dimensions

6 dimensions the system grows along. **UI grows FROM every other dimension** — not separate.

```
1. BREADTH  — More apps
2. DEPTH    — Richer backends (more endpoints, deeper features)
3. LINKS    — More event connections (fewer unheard events, fewer orphans)
4. INFRA    — Shared platform services (VaultIndex, data layer, SDK modules, components)
5. ABSORB   — External services → plugins → native
6. REGROW   — Rethink from root, consolidate fragmented apps, simplify
```

Each growth session should:
- Touch at least 2 dimensions
- Prioritize daily-use apps
- Leave the system testable (`python -m emptyos health`)

---

## Dimension 6: REGROW — Rethink from Root

Sometimes the right growth move isn't adding features — it's questioning whether the current structure is right.

### When to Regrow

- **User switches between 3+ apps for one workflow** — the apps should be one
- **Data is duplicated across apps** — same vault folder read by multiple apps
- **Composition app exists just to glue others** — the glue layer signals a missing unified app
- **The data structure changed** — vault-first data means the app should follow the data

### Regrow Process

1. **Notice friction** — "why do I need 4 apps for job applications?"
2. **Question structure** — "if I grew this from scratch, would it look the same?"
3. **Follow the data** — vault folders are the natural unit. One folder = one view in the app.
4. **Build infrastructure first** — if the regrow reveals a platform gap, build that before the app
5. **Consolidate** — merge apps into one with modules. Keep all endpoints, reorganize by user workflow.
6. **Retire old apps** — remove the fragments, redirect URLs

---

## Absorption Process

When absorbing an external service:
1. Audit the external service (endpoints, features, data)
2. Compare with existing EmptyOS apps (gap table)
3. Classify: absorb concepts vs keep external
4. Execute: enhance existing apps or create thin wrappers
5. Document the boundary in CLAUDE.md

Evolution path: WRAP → ABSORB → REPLACE → SHED

---

## Key APIs (localhost:9000)

| Endpoint | What it returns |
|----------|----------------|
| `GET /api/topology` | Full graph: nodes + edges (raw) |
| `GET /api/topology/layers` | Layered analysis: depth, cycles, critical path, data coupling, fan-in/out |
| `GET /api/topology/improvements` | **Start here.** Prioritized fix list with file paths |
| `GET /api/topology/node/{id}` | Focused subgraph for one node (1st + 2nd degree neighbors) |
| `GET /integrity/api/audit` | 13-dimension integrity score out of 130 |
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
| `apps/personal/integrity/app.py` | Integrity audit: 13-dimension scoring |

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
