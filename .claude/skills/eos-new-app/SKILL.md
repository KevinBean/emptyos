# EmptyOS New App

Scaffold a new core app end-to-end with every EmptyOS convention wired in from the start — manifest, capabilities, UI helpers, settings panel, deep-link routing, test file, vault-map entry, and release-tier registration. The goal is that the app is **runnable, testable, and release-ready** the moment this skill finishes.

## When to Use

- User says "new app", "create app", "scaffold app `<id>`", "add an app for `<purpose>`"
- Before starting any app that doesn't exist under `apps/` or `apps/personal/`
- **Not** for apps whose logic is personal → those go under `apps/personal/` and skip release-tier registration

## Process

Run steps in order. The grill at Step 1 is mandatory unless the user provides a `spec=<path>` arg pointing at an existing grill spec note (see "Pre-grilled spec" below) — naming and dependencies are hard to change later, and trivial-looking apps usually have a non-trivial sub-pattern hiding.

### Pre-grilled spec

If the user invokes this skill with `spec=<vault-path-to-spec.md>` (or pastes a grill spec note inline), skip Step 1 and read the spec instead. Specs are produced by:
- The web grill app at `/grill/` (recipe `new-app`)
- A previous Claude Code session that ran the Step 1 grill below

A valid spec has `tags: [grill-spec]` + `recipe: new-app` in frontmatter, plus a "Scaffold checklist" section. If frontmatter or checklist is missing, treat it as freeform notes and run Step 1.

---

### Step 1: Grill the Spec

The old version of this skill asked a 10-item form in one shot. That works for the 5th CRUD list app — it bites for everything else, because the model and the user both miss the manifest knobs that hurt later (hub-panel? voice-intent? addons? boards-view-layer? settings panel? hash routing? vault_map entry?). The grill below front-loads those by walking through them with reasons attached.

**Phase 0 — Gate.** One question:

> Is this a trivial CRUD list app (matches the auto-UI shape: list + add + delete + optional stats), or a new shape (canvas / dashboard / generator / instrument / reading-surface)?

If "trivial CRUD": skip Phase 1 with these defaults — verb=add/list/delete, data_shape=vault notes OR data/ JSON (ask which), no hub panel, no voice intent, no detail view, capabilities=[read, write]. Re-ask only the genuinely required fields (id, name, description, where data lives). Skip Phase 2 brainstorm too; go straight to Phase 3 spec, then Phase 4 review.

If "new shape": run Phase 0.5 → Phase 1 → Phase 2 → Phase 3 → Phase 4 in order. Trivial CRUD also runs Phase 0.5 — duplicate-app catches are highest-value here.

**Phase 0.5 — Prior-art pass.** Before asking the user anything else, spawn a single Explore agent (quick breadth, ~60s budget) with the working title/verb to surface:

1. **Existing apps with overlapping verbs or data shapes** — under `apps/` and `apps/personal/`. If found, list with one-line summary of what they do today.
2. **SDK helpers that already cover what's being asked** — `emptyos/sdk/` (e.g. `VaultLibrary`, `BaseApp.embed_text`, `srs.py`, `parse_llm_json`). If a helper exists, the new app should consume it, not reimplement.
3. **`.claude/rules/` files this app will likely trigger** — based on the verb, name which rules to read before Phase 1 (e.g. "verb=track-over-time → read `boards-as-view-layer.md`, `hub-panels.md`").
4. **Plugins that already provide the capability** — if the app would just wrap an existing plugin (e.g. `playwright`, `comfyui`), flag it.

Prompt template for the Explore agent:

> Quick prior-art scan for a new EmptyOS app. Working title: `<title>`. Verb: `<verb-from-Phase-0 or "TBD">`. Find:
> 1. Existing apps under `apps/` and `apps/personal/` with overlapping verbs/data — list id + one-line summary.
> 2. SDK helpers in `emptyos/sdk/` that would be relevant — list path + one-line of what it does.
> 3. `.claude/rules/` files likely to apply — list filename + why.
> 4. Plugins in `plugins/` that already provide the underlying capability.
> Keep total report under 250 words. Fail soft — if nothing is found, say so in one line.

Surface the findings as a "Prior art" block back to the user. Then ask **one decision question**:

> Prior art found above. Proceed with a new app, or extend an existing one / contribute a hub-panel/voice-intent to it?

If the user picks extend/contribute, stop this skill and hand off (read the relevant rule file and edit the existing app's manifest). If they pick "proceed with new app", run Phase 1 — but feed the SDK helpers + rule files into the grill so the answers are informed (e.g. "since `VaultLibrary` exists, data shape question defaults to vault-notes-with-tag").

If the Explore agent returns nothing useful, proceed to Phase 1 silently — don't pad with empty results.

**Phase 1 — Grill.** Act as an ICT Business Analyst, not a templater. The job is to extract requirements the user hasn't stated yet — users, jobs-to-be-done, acceptance criteria, non-functionals, out-of-scope — *before* nailing down implementation details. Ask one question at a time and wait for the answer. Each question's "Why" line must be sent with the question — it teaches the user what the answer drives, so they can give a better one.

**Phase 1A — Problem & Users (BA front-half).** Anchor *what* and *who* before *how*.

1. **Problem statement.** In one sentence: what user pain does this remove, or what user goal does this unlock? *Why:* Forces a real "why" before we name a verb. If you can't say it in a sentence, the app isn't ready.
2. **Primary user(s).** Who's the user (you alone / family / team / public demo visitor / another app)? *Why:* Drives `apps/` vs `apps/personal/`, demo-mode visibility, privacy posture, and whether the UI needs onboarding.
3. **User stories.** Give 3-5 in the form: *As a <user>, I want to <action> so that <outcome>*. *Why:* Each story becomes one acceptance test in Step 6 and one row in the scaffold checklist. Fewer than 3 = the app is a feature, not an app; more than 5 = it's two apps.
4. **Acceptance criteria.** For the top story, what's the Given/When/Then? *Why:* This is the test we'll write first. If you can't state it, the story isn't sharp enough.
5. **Out-of-scope.** What is this app deliberately NOT going to do? *Why:* Saves a future rewrite. Examples: "no sharing across users", "no AI generation in v1", "no mobile-specific layout". Out-of-scope items go into the spec note and the `next` section of Step 10.
6. **Non-functional requirements.** Any constraints on: latency (<200ms? async ok?), offline behaviour (works without daemon? without internet?), privacy (any cloud calls allowed?), accessibility (keyboard-only? screen-reader?)? *Why:* These shape capability choice + cloud-consent posture + whether `[provides.export]` matters. (CLAUDE.md Rules 18-20.)

**Phase 1B — Solution shape (BA back-half).** Now the technical-decoding questions, informed by Phase 0.5 prior art.

7. **Verb.** What single verb does this app give the user? *Why:* Apps are atoms — one verb each. If you need more than 5 words, it's two apps. (CLAUDE.md Principle 4.)
8. **Data shape.** Where does the data live — markdown vault notes / `data/<id>/*.json` / derived from another app / stateless? *Why:* Drives vault_map entry, frontmatter convention, which `self.read/write/vault_*` calls you need, and whether boards can render it (`.claude/rules/boards-as-view-layer.md`). If Phase 0.5 surfaced `VaultLibrary` or another reusable, default toward consuming it.
9. **Surfaces.** Beyond its own page, which surfaces does it appear on — hub panel / voice intent / boards view / tour step / addons slot? *Why:* Each surface is a manifest contribution slot. Picking them now means scaffolded; retrofitting means a follow-up PR. See `.claude/rules/hub-panels.md`, `voice-intents.md`, `tour-steps.md`, `addons.md`.
10. **Events.** What events does it emit on user actions? *Why:* Events are how the reactor + other apps see your work; no emits = invisible.
11. **Capabilities.** Which of {read, write, think, search, speak, listen, draw, animate, see} does it require? *Why:* Manifest declares them — kernel validates on load; missing capability = boot fail. Cross-check Phase 1A.6 non-functionals: if "no cloud" was a constraint, prefer providers tagged `is_cloud=False`.
12. **Sub-patterns.** Any of these patterns to adopt now: room-review-gate / multi-CLI participants / slash command palette / standalone export? *Why:* Each is documented in `.claude/rules/` — adopting at scaffold is hours; retrofitting is days.
13. **Privacy / branding.** Anything in the app that touches third-party brand names, personal data patterns, or should be hidden from `demo.enabled`? *Why:* Rule 13 + 14 (no personal data, no third-party brand in user-facing text) + `[app] private = true` for demo hiding. Cheaper to flag now than during release-public.py audit.
14. **Name/id.** Now: kebab-case id and display name? *Why:* Last, because the verb + data answers may have already shifted the right name.

**Phase 2 — Brainstorm pass (mandatory for "new shape", skip for trivial CRUD).** With answers in hand, propose 2-3 alternative shapes the app could take (e.g. "as a vault-notes app with a hub panel" vs. "as a derived view over `apps/task` data" vs. "as a generator with no persistent storage") and let the user pick. Often the first answer to Phase 1 is the obvious shape; the brainstorm surfaces the non-obvious shape that would have been better. State the tradeoff for each (what it makes cheap, what it makes expensive later) — don't just list names.

**Phase 3 — Write the spec note (BA-shaped).** Write the answers + decisions to `{vault}/30_Resources/EmptyOS/grill/new-app-<id>-<ts>.md` with frontmatter `tags: [grill-spec]`, `recipe: new-app`. The note is a lightweight BA artifact — anyone reading it should understand who the app is for and what "done" means, without re-running the grill.

Sections, in this order:

1. **Problem & users** — problem statement + primary users (from Phase 1A.1-2)
2. **User stories** — bulleted list, each in As/I-want/so-that form (Phase 1A.3)
3. **Acceptance criteria** — Given/When/Then per top story, minimum one (Phase 1A.4); these become Step 6 test names
4. **Out-of-scope** — explicit non-goals (Phase 1A.5)
5. **Non-functional requirements** — latency / offline / privacy / accessibility (Phase 1A.6)
6. **Solution shape** — verb, data shape, surfaces, events, capabilities, sub-patterns, name/id (Phase 1B)
7. **Prior art consumed** — apps/SDK/rules from Phase 0.5 the new app builds on (so the next grill knows this app already covered ground X)
8. **Scaffold checklist** — bullet list of files to create with manifest fields filled, one bullet per artifact (manifest.toml, app.py, pages/index.html if any, tests/test_sys_<id>.py, release.toml line, vault-map entry)

This way the spec survives if the session crashes mid-scaffold, and downstream `/eos-new-app spec=<path>` invocations can re-use it. It also doubles as a PR description and a future debugging reference ("why does this app exist?").

**Phase 4 — Review gate (mandatory).** Before touching `apps/<id>/`, post the BA spec summary back to the user — problem + users + stories + acceptance + out-of-scope + non-functionals + solution shape + scaffold checklist — and ask one question:

> Spec looks like this. OK to scaffold, or anything to change?

Wait for explicit go-ahead. Common late corrections: "actually make it personal", "drop the hub panel", "rename to `<x>`", "data should be data/ JSON not vault". Edit the spec note in place and re-post the diff; don't move on until the user confirms. **Skip the review only if** the user invoked with `spec=<path>` (they already authored it) or said "just scaffold it" / "no review" up front.

If the user picks `personal`, the app goes under `apps/personal/` and Steps 6 + 7 are skipped.

---

### Step 2: Pre-flight

```bash
# Check id is free
ls apps/<id> apps/personal/<id> 2>/dev/null && echo "CLASH" || echo "OK"

# Check id doesn't shadow a plugin
ls plugins/<id> 2>/dev/null && echo "CLASH" || echo "OK"
```

Abort and ask for a new id if either clashes.

---

### Step 3: Create `apps/<id>/manifest.toml`

Template (fill from Step 1 spec):

```toml
[app]
id = "<id>"
name = "<Display Name>"
version = "1.0.0"
description = "<1-line description>"
dimensions = ["<dimension>"]

[app.entry]
module = "app"
class = "<PascalCase>App"

[provides.cli]
commands = ["<id>"]

[provides.web]
prefix = "/<id>"

[provides.events]
emits = ["<id>:added", "<id>:updated"]

[requires]
capabilities = [<from spec>]
apps = [<from spec>]
services = []
events = []

[provides.settings]
schema = [
    {key = "<id>.<setting>", label = "<Label>", type = "number|text|boolean|select", default = <default>},
]
```

Omit `[provides.settings]` if the spec said "none". Omit `[provides.events]` if emits is empty.

---

### Step 4: Create `apps/<id>/app.py`

Skeleton must use capabilities (not raw tools), declare prompts as UPPERCASE constants, and wire the declared events.

```python
"""<Display Name> — <1-line description>."""

from __future__ import annotations

import logging
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

log = logging.getLogger("emptyos.<id>")

# ── Prompts (see CLAUDE.md §Development Rules 12) ──────────────
# Keep UPPERCASE, include negative examples, use system= kwarg in calls.
<ID>_SYSTEM = """You are a <role>.
Rules:
- <rule 1>
- <rule 2>

Do NOT:
- <what must not happen>
- <another failure mode>
"""


class <PascalCase>App(BaseApp):
    async def on_start(self):
        log.info("<id> started")

    # ── CLI ────────────────────────────────────────────────────
    @cli_command("list")
    async def cli_list(self):
        items = await self.list_items()
        for it in items:
            print(it)

    # ── Web API ────────────────────────────────────────────────
    @web_route("GET", "/api/items")
    async def api_list(self, request):
        return await self.list_items()

    @web_route("POST", "/api/items")
    async def api_add(self, request):
        body = await request.json()
        item = await self.add(body.get("text", ""))
        await self.emit("<id>:added", {"id": item["id"]})
        return item

    # ── Methods (callable via self.call_app) ───────────────────
    async def list_items(self) -> list[dict]:
        # TODO: vault_query or data/ read
        return []

    async def add(self, text: str) -> dict:
        # TODO: vault_create_note or data/ write
        return {"id": "", "text": text}
```

Rules the skeleton must satisfy — verify before writing:
- `BaseApp` subclass, not a plain class
- All I/O via `self.read/write/search/think/vault_*` — no `open()`, no `requests`, no raw `subprocess`
- Hardcoded vault paths forbidden → use `self.vault_config("key")`
- If the app has `[provides.settings]`, read values with `self.app_config("<key>", <default>)`
- If the app calls an LLM, always pass `system=<ID>_SYSTEM` and set `temperature=` explicitly (0.1–0.3 parsing, 0.3–0.5 analysis, 0.6–0.8 creative)
- **DO NOT write a `@web_route("GET", "/")` handler to serve `pages/index.html`.** The platform auto-mounts `pages/index.html` at `{prefix}/` whenever the `pages/` directory exists (see `emptyos/web/server.py` `_mount_loaded_app_routes`). A custom `/` handler shadows the auto-mount and breaks the UI.
- **EmptyOS is FastAPI, not aiohttp.** Never import from `aiohttp.web`. Route handlers return plain `dict` (auto-serialized to JSON) or `fastapi.responses.HTMLResponse` / `FileResponse` for non-JSON content.

---

### Step 5: Decide whether you need a custom `pages/index.html`

**Default: skip this step.** New CRUD apps render via the auto-UI (`emptyos/web/auto_ui.py`) using the shared component bundle — list with hover-revealed trash buttons, "+ Add" modal with inferred fields, stats tiles where applicable. No HTML to write, full CRUD on first boot.

**Write a custom `pages/index.html` only when**:
- The app's primary surface isn't a list (deep-canvas like `canvas`/`improv`/`board`, single-shot generators like `voice-assistant`, dashboards with bespoke layouts).
- The app needs interactions auto-UI doesn't synthesize (drag-to-reorder, inline cell edit, custom widgets like cover pickers / audio recorders / map views).
- The app is a "reading surface" (journal, blog post viewer) where the layout *is* the product.

If none of those apply, ship without `pages/`. **Migration path** when an author wants to upgrade: open `http://localhost:9000/<id>/`, view source, save as `apps/<id>/pages/index.html`, edit. The platform serves `pages/index.html` in preference to auto-UI when both exist.

---

### Step 5b (only if Step 5 said "yes"): Create `apps/<id>/pages/index.html`

Use the shared helpers; do not reinvent modals, cards, or detail routing.

Template outline:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title><Display Name></title>
<link rel="stylesheet" href="/static/theme.css">
<link rel="stylesheet" href="/static/eos-components.css">
<link rel="stylesheet" href="/static/eos-keys.css">
</head>
<body>
<div class="app-shell">
  <header class="app-header">
    <h1><Display Name></h1>
    <!-- Mandatory when [provides.settings] exists -->
    <button class="btn-settings" onclick="openAppSettings()">&#9881; Settings</button>
  </header>
  <main id="main"><!-- list / detail views --></main>
</div>

<script src="/static/eos.js"></script>
<script src="/static/eos-components.js"></script>
<script src="/static/eos-keys.js"></script>
<script>
// ── Settings panel (mandatory if [provides.settings] exists) ──
var _appSettings = EOS_UI.settingsPanel({
    id: '<id>-settings-panel',
    title: '<Display Name> Settings',
    fields: [
        // one entry per schema row in manifest.toml
    ],
});
function openAppSettings() { _appSettings.open(); }

// ── Hash routing (mandatory if detail view exists) ────────────
var _route = EOS_UI.hashRoute({
    onShow: function(id) { showDetail(id); },
    onHide: function() { hideDetail(); },
});
function showDetail(id) { /* render detail */ _route.set(id); }
function hideDetail() { /* close detail */ _route.clear(); }

// ── Initial load ──────────────────────────────────────────────
async function load() {
    var items = await fetch('/<id>/api/items').then(r => r.json());
    // render list — use EOS_UI.statCards / EOS_UI.modal as needed
    // vault paths? use EOS.noteActions(path), NEVER plain esc(path)
}
load().then(function(){ _route && _route.init && _route.init(); });
</script>
</body>
</html>
```

Omit the settings block if no `[provides.settings]`. Omit the `hashRoute` block if no detail view. Never omit the stylesheet + `eos-components.js` imports.

---

### Step 6: Create `tests/test_sys_<id>.py`

Model on an existing test file (e.g. `tests/test_sys_task.py`). Aim for **10+ cases** across API + UI per `.claude/rules/testing.md`.

```python
"""System app tests: <Display Name> — N use cases."""

import pytest
from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, click_first, switch_tab, wait_for_toast


@pytest.mark.api
class Test<Pascal>API:
    def test_list_structure(self, http_client):
        data = assert_list_response(http_client.get("/<id>/api/items"))
        if data:
            assert "id" in data[0]

    def test_add_emits_event(self, http_client):
        r = http_client.post("/<id>/api/items", json={"text": TEST_PREFIX + "item"})
        assert_dict_response(r)

    # … add 4–5 more API cases: stats, filters, edge cases, persistence


@pytest.mark.interactive
class Test<Pascal>UI:
    def test_page_loads(self, page, base_url):
        page.goto(base_url + "/<id>/")
        assert_no_js_errors(page)

    def test_add_flow(self, page, base_url):
        page.goto(base_url + "/<id>/")
        # click add → fill form → submit → wait_for_toast
        ...

    # … add 3–4 more UI cases: settings open, detail route (if any), filter
```

If the app stores data anywhere `conftest.py` doesn't already clean up, add a cleanup block there keyed on `TEST_PREFIX`.

---

### Step 7: Register in `release.toml` (skip if personal)

Open `release.toml` and add `"<id>"` to the appropriate tier's `apps = [...]` list. Default is `standard` unless the spec said `core`.

```bash
python scripts/package-release.py --check
```

Must exit clean.

---

### Step 8: Vault-Map Entry (skip if no vault data)

If the app writes user-authored notes to the vault, declare where. Vault-map lives at `{vault}/30_Resources/EmptyOS/_vault-map.toml`. Read the current vault connection from `.claude/vault-connection.json`, open the file, and add:

```toml
[<id>]
path = "30_Resources/EmptyOS/<id>"
description = "<what the app stores>"
```

`self.vault_write(...)` will land under that path; `self.vault_config("path")` reads it. **Never hardcode a vault path in `app.py`.**

---

### Step 8.5: Mark the app installed in the Store gate

**Why this step exists.** The Store maintains per-user install state at `data/store/installed-apps.json`. Its first-boot seed (`store_state.seed_if_missing`) only fires when that file is **missing** — once any daemon has booted on this machine, the file persists across reboots. A new app under `apps/<id>/` is *discovered* by `app_loader.discover()` but **excluded from `enabled_ids()`** because it's not in the installed set, so the boot loop skips it and no routes are mounted → `/<id>/` returns **404** even though every scaffolded file is correct. Same applies to plugins (`installed-plugins.json`).

**The fix** — run before asking the user to restart:

```bash
cd D:/emptyos && python -c "
from pathlib import Path
from emptyos.runtime import store_state
store_state.mark_installed(Path('data'), 'apps', '<id>', '<version-from-manifest>')
print('installed:', store_state.is_installed(Path('data'), 'apps', '<id>'))
"
```

This writes one entry into `data/store/installed-apps.json` and is safe to run while the daemon is up (it's just a JSON write; the daemon picks it up on the next boot). It does **not** require an HTTP call to `/store/api/install/...` — that endpoint does the same JSON write and tells you to restart anyway.

**For plugins** scaffolded via `/eos-new-plugin`, use `'plugins'` as the kind: `store_state.mark_installed(Path('data'), 'plugins', '<id>', '<version>')`.

**Skip this step if** `data/store/installed-apps.json` doesn't exist yet (fresh-clone first boot — the seed will install everything automatically) or `[demo] enabled = true` in `emptyos.toml` (demo mode bypasses the gate).

---

### Step 9: Verify

```bash
# Restart the daemon so the new app is loaded
cd D:/emptyos
# ask user to run restart.bat if daemon is running, then:

# Confirm the app registered
curl -s http://localhost:9000/api/apps | python -c "import sys,json; ids=[a['id'] for a in json.load(sys.stdin)]; print('<id>' in ids)"

# Self-documenting info
python -m emptyos app info <id>

# Run its test file (daemon must be up)
pytest tests/test_sys_<id>.py -v
```

All three must succeed. If `eos app info` fails, the manifest is malformed. If tests fail against an empty implementation, that's expected — stub the handlers to return valid shapes so the baseline tests pass.

---

### Step 10: Report

```
New App Scaffolded: <id>

Files created:
  apps/<id>/manifest.toml
  apps/<id>/app.py
  apps/<id>/pages/index.html
  tests/test_sys_<id>.py

Wired:
  release.toml     → <tier> tier
  _vault-map.toml  → <id> path registered (or "N/A — no vault data")

Verified:
  eos app info <id>         OK
  pytest test_sys_<id>.py   <N> passed, <M> skipped

Next:
  1. Implement the TODO methods in apps/<id>/app.py
  2. Flesh out the UI — list render + add form
  3. When a second app needs a pattern you just wrote, extract to sdk/
  4. Once the UI has real content, run /eos-design-review apps/<id>/pages/index.html
     — catches theme-bootstrap missing, phantom tokens, doubled signals,
     and archetype-mismatched chrome before they harden
  5. iOS check (any page with fixed/sticky elements or div-onclick handlers):
       python scripts/check-ios-safe-area.py apps/<id>/pages/index.html
     Catches the 5 iOS Safari bug families (notch overlap, home-indicator
     overlap, hardcoded notch padding, 100vh viewport collapse, div-onclick
     silent drop). The scanner runs in CI on every push, so failing
     here means the PR won't merge — cheaper to catch now.
  6. Before commit: run /eos-simplify
  7. End of session: run /eos-session-wrapup
```

## Safety

- **Never** scaffold over an existing app — if `apps/<id>` exists, abort and ask.
- **Never** commit personal config into the app's manifest or code — per-machine settings go in `emptyos.toml` `[apps.<id>]`, read via `self.app_config()`.
- **Never** add a wellbeing-wheel picker, dimension tag prompt, or wheel visual to the UI (CLAUDE.md §Development Rules 16). `dimensions = [...]` in manifest is manifest-only metadata.
- **Never** reference third-party brands in user-facing text (prompts, UI labels, error messages) — plugin integrations are the only exception.
- Don't hand-write release-tier entries for `personal` apps — they stay under `apps/personal/` and are gitignored.

## Relationship

- **Once the UI is fleshed out (not before — needs real content to evaluate)** → `/eos-design-review apps/<id>/pages/index.html`. Catches theme-bootstrap regressions, phantom tokens, doubled signals, and archetype-mismatched chrome (e.g. an instrument with too many controls, a list with a competing hero) at the cheapest possible point — when the patterns aren't yet calcified.
- Pre-commit after building out the scaffold → `/eos-simplify`
- End of session → `/eos-session-wrapup` (updates CLAUDE.md counts, safety scan, devlog)
- System health / what to build next → `/eos-system-check-and-fix`
