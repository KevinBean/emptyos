# EmptyOS Restructure

Topology-driven app restructuring — analyze the live dependency graph, identify natural clusters, wire orphans, and execute merges following established patterns.

## When to Use

- User says "restructure", "merge apps", "consolidate", "simplify apps" → **merge mode**
- User says "wire", "connect orphans", "fix topology" → **wire mode**
- User says "analyze structure", "check clusters", "what should merge" → **analyze mode**
- After the system grows past a complexity threshold
- When orphan apps accumulate (centrality 0, no graph edges)

## Philosophy

Restructuring follows the topology, not opinion. The live dependency graph (`/api/topology`, `/api/apps/clusters`) reveals natural organisms — groups of apps with high internal weight. Apps with zero connections are orphans. Merging is the last step, not the first.

**Wire first, merge second.** An orphan that gains one `call_app` edge joins an existing cluster naturally. Only merge when two apps share the same domain, the same data, and the graph confirms they belong together.

The system already has 5 successful merges as precedent. Every merge follows the same mixin pattern. Don't invent new patterns.

---

## Analyze Mode — Read the Topology

### Step 1: Pull Live Data

```bash
# Clusters with internal weight
curl -s http://localhost:9000/api/apps/clusters | python -m json.tool

# Full topology graph (nodes + edges)
curl -s http://localhost:9000/api/topology | python -m json.tool

# Layered view
curl -s http://localhost:9000/api/topology/layers | python -m json.tool
```

### Step 2: Identify Natural Signals

From the cluster data, extract:

| Signal | How to Read |
|---|---|
| **High internal weight** | Healthy cluster — apps naturally connected. Don't touch. |
| **Centrality > 30** | Hub app — many dependencies flow through it. Good merge host. |
| **Centrality = 0** | Orphan — no graph connections. Wire candidate. |
| **Weight = 0 cluster** | Dead zone — apps grouped by name heuristics, not real edges. |
| **Misplaced apps** | App in a cluster where it doesn't share domain. Clustering artifact. |

### Step 3: Map Dependencies

```bash
# Find all call_app edges (the real dependency graph)
grep -rn "call_app" apps/ apps/personal/ --include="*.py" | grep -v _retired | grep -v __pycache__

# Find all event emissions
grep -rn "self.emit(" apps/ apps/personal/ --include="*.py" | grep -v _retired | grep -v __pycache__

# Find manifest-declared deps
grep -rn "apps\s*=\s*\[" apps/ apps/personal/ --include="manifest.toml" | grep -v _retired
```

### Step 4: Report

Present findings as:
1. **Living clusters** (weight > 20) — leave alone
2. **Orphans** (centrality 0) — wire candidates
3. **Merge candidates** — apps in same domain, same data, confirmed by graph
4. **Split candidates** — apps > 3000 lines with distinct internal modules

---

## Wire Mode — Connect Orphans

Before merging, wire orphans into the graph. Each wire = one `call_app` edge or event subscription.

### Wire Pattern

1. **Identify the orphan** and its natural parent cluster (by domain)
2. **Find the data flow** — what data does the orphan produce that the cluster consumes, or vice versa?
3. **Add the edge**:
   - `call_app`: orphan calls parent's API method, or parent calls orphan's
   - Event: orphan emits an event that reactor/parent listens to
   - Manifest: declare the dependency in `[requires] apps = [...]`
4. **Verify**: restart, check `/api/apps/clusters` — orphan should join the cluster

### Wire Checklist

For each orphan, answer:
- What data does this app produce? (vault notes, metrics, state)
- What other app would consume that data? (aggregators, dashboards, reactors)
- What's the minimal edge? (one `call_app` or one event emit)

### Example Wires

```python
# sleep → healing: mood+sleep correlation
# In sleep/app.py, after logging sleep:
await self.emit("sleep:logged", {"hours": hours, "quality": quality})

# In healing/app.py or reactor, listen for sleep data:
# reactor chain: sleep:logged → journal entry

# weather → briefing: already consumed?
# Check: grep "weather" apps/personal/briefing/*.py
# If not: briefing._weather() calls weather.current()

# recipes → nutrition: meal planning
# nutrition.log_meal() could suggest recipes via call_app("recipes", "search")
```

### Verification

```bash
# Before wiring: check centrality
curl -s http://localhost:9000/api/apps/clusters | python -c "
import json, sys
for c in json.load(sys.stdin):
    for a in c['apps']:
        if a['centrality'] == 0:
            print(f\"  orphan: {a['id']} (in cluster '{c['name']}')\")"

# After wiring: same check — orphan should now have centrality > 0
```

---

## Merge Mode — Execute a Merge

### Prerequisites

1. Both apps are in the **same directory tier** (both core or both personal)
2. The topology confirms they belong together (shared cluster, or wired edge)
3. The host app has higher centrality than the absorbed app

### The Merge Pattern (established by 5 prior merges)

```
Before:  host-app/app.py              absorbed-app/app.py
         host-app/pages/index.html     absorbed-app/pages/index.html

After:   host-app/
         +-- app.py              <- imports new mixin
         +-- absorbed.py         <- AbsorbedMixin(self.app) with all logic
         +-- __init__.py         <- if not present
         +-- pages/index.html    <- gains new tab
         +-- manifest.toml       <- union of capabilities + events

Retired: apps/personal/_retired/absorbed-app/   <- preserved, never deleted
```

### Three Merge Types

| Type | When | Pattern | Example |
|---|---|---|---|
| **Mixin absorption** | Absorbed app has capabilities (read/write/think) | `class AbsorbedMixin: def __init__(self, app): self.app = app` — mixin calls `self.app.read()`, `self.app.think()` | finance <- net-worth, retirement |
| **Module delegation** | Absorbed app is pure computation | `class AbsorbedMixin: def __init__(self, app): ...` — no capability calls, just functions | cable <- pulling, hdd, rating |
| **UI consolidation** | Absorbed app is mostly a view of other apps' data | Host absorbs the `call_app` aggregation logic, UI becomes a tab | hub <- dashboard |

### Step-by-Step Merge Execution

#### 1. Prepare the module

```python
# absorbed.py (new file in host app dir)
"""Absorbed — {description}.

Migrated from standalone {absorbed-id} app.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import HostApp

class AbsorbedMixin:
    def __init__(self, app: HostApp):
        self.app = app

    # Move all methods from absorbed/app.py here
    # Change self.read() -> self.app.read()
    # Change self.think() -> self.app.think()
    # Change self.emit() -> self.app.emit()
    # Change self.call_app() -> self.app.call_app()
    # Keep method names identical for call_app compatibility
```

#### 2. Wire into host app.py

```python
# In host app.py
from .absorbed import AbsorbedMixin

class HostApp(BaseApp):
    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._absorbed = AbsorbedMixin(self)

    # Expose absorbed methods for call_app compatibility
    async def absorbed_method(self, **kwargs):
        return await self._absorbed.method(**kwargs)

    # Route web endpoints
    @web_route("GET", "/api/absorbed/data")
    async def api_absorbed_data(self, request):
        return await self._absorbed.get_data()
```

#### 3. Merge manifest.toml

```toml
# Union of capabilities
[requires]
capabilities = ["read", "write", "think", "search"]  # union of both
apps = ["task", "journal"]  # union of both

# Union of events
[provides.events]
emits = ["host:done", "absorbed:logged"]  # keep both prefixes

# Union of CLI commands
[provides.cli]
commands = ["host", "absorbed-cmd"]  # keep both working
```

#### 4. Merge UI (tabbed)

Add a tab to `pages/index.html` for the absorbed app's view. The absorbed app's HTML becomes a tab panel. Use existing EOS tab pattern:

```html
<div class="tabs">
    <button class="tab active" onclick="switchTab('main')">Main</button>
    <button class="tab" onclick="switchTab('absorbed')">Absorbed</button>
</div>
<div id="tab-main" class="tab-content active">...</div>
<div id="tab-absorbed" class="tab-content">...</div>
```

#### 5. Retire the absorbed app

```bash
# Move to _retired (core or personal, matching source)
mv apps/personal/absorbed-app/ apps/personal/_retired/absorbed-app/
# Or for core:
mv apps/absorbed-app/ apps/_retired/absorbed-app/
```

#### 6. Update callers

```bash
# Find all call_app("absorbed-id", ...) references
grep -rn 'call_app("absorbed-id"' apps/ apps/personal/ --include="*.py" | grep -v _retired

# Update to call_app("host-id", "absorbed_method", ...)
# Host must expose the same method names for backward compat
```

#### 7. Verify

```bash
# Restart EmptyOS
# Check the app loads
curl -s http://localhost:9000/api/apps/host-id | python -m json.tool

# Check absorbed endpoints work under host prefix
curl -s http://localhost:9000/host-id/api/absorbed/data

# Check topology — absorbed app gone, host has higher centrality
curl -s http://localhost:9000/api/apps/clusters | python -m json.tool

# Run smoke tests
pytest tests/ -v -k "host_id or absorbed_id"
```

### Release Safety

| Scenario | Safe? | Rule |
|---|---|---|
| Core + core merge | Yes | Update `release.toml` — remove absorbed id, host id stays |
| Personal + personal merge | Yes | Invisible to release — `apps/personal/` excluded |
| Core absorbs personal | No | Personal logic enters released code — don't do this |
| Personal absorbs core | No | Core app disappears from release — don't do this |

**Rule: merges stay within the same directory tier.** All proposed merges follow this naturally.

After a core merge, update `release.toml`:
```toml
# Remove absorbed app id, keep host app id
apps = [
    # "vault-analytics",  # absorbed into app-analytics
    "app-analytics",       # now includes vault analytics
]
```

---

## Current Merge Candidates (from topology analysis)

Confirmed by live graph data, April 2026:

### Tier: Personal (apps/personal/)

| Host (centrality) | Absorb | Type | Result |
|---|---|---|---|
| `healing` (25) | sleep(0), workout(0), habits(0) | mixin | Wellness hub, 4 tabs |
| `finance` (10) | expense(26) | mixin | Finance gains expense tab |
| `briefing` (69) | digest(16), review(15) | UI consolidation | Daily/weekly modes |
| `cable` (0) | sheath-voltage(0) | module | Engineering suite |
| `studio` (16) | comfyui-app(6) | UI consolidation | Unified image studio |

### Tier: Core (apps/)

| Host (centrality) | Absorb | Type | Result |
|---|---|---|---|
| `app-analytics` (29) | vault-analytics(25) | mixin | Unified analytics |
| `app-gen` (4) | plugin-gen(0) | module | Unified generator |

### Priority Order

1. Wire orphans first (sleep, workout, habits, weather, recipes, bookmarks, reminders, quickref)
2. Personal merges (healing cluster, then finance+expense, then briefing cluster)
3. Core merges (analytics, generators)
4. Verify topology improvement after each merge

---

## Anti-patterns

- **Don't merge by vibes** — only merge when the graph confirms the connection
- **Don't cross tiers** — core stays core, personal stays personal
- **Don't lose method names** — external callers use `call_app("old-id", "method")`, host must expose same methods
- **Don't delete retired apps** — move to `_retired/`, never `rm -rf`
- **Don't merge aggregators** — hub, assistant, staff have high centrality but low internal weight. They reach outward. Merging them creates mega-apps.
- **Don't merge standalone engineering apps** unless they share computation (cable + sheath-voltage share cable math, so that merge is valid)
