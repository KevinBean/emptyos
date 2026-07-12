# Model Pill Rule — Visible, Switchable Provider on Every Think Surface

`EOS_UI.modelPill()` is the shared toolbar chip that shows which `think` provider is about to spend the user's budget, with click-to-switch. Input-side mirror of `EOS_UI.provenance()`: the latter shows where bytes flowed (cloud/local), the pill shows who's paying for the compute.

**Reference implementation:** `apps/viz/pages/index.html` (header `.right`).
**Component source:** `emptyos/web/static/eos-components.js` (`EOS_UI.modelPill`, `EOS_UI.MODEL_COSTS`, helpers `_modelCostFor` + `_modelCostIcon`).
**Backend:** `GET /api/capabilities/think/effective?app=<id>&domain=<d>` in `emptyos/web/server.py`.
**Settings keys it reads/writes:** `think.app.<id>` (single-provider override).

## Why this exists

The free-first policy in `emptyos.toml` (claude-cli → ollama → openai-mini → openai) works, but it's invisible at the use site. Without a pill, the first time claude-cli rate-limits and the chain falls back to paid mini, the user has no signal. The pill makes the cost class glanceable + the policy directly editable from the app surface.

## When to mount the pill

Drop it in when **all** are true:
- The app calls `self.think()` (or `self.think_stream()`) from a user-initiated path (button, form submit, chat send).
- The think output is the app's *value* — generating content, answering questions, planning, drafting.
- The user benefits from per-app overrides (e.g. "always use claude in viz, ollama in podcast").

Skip when:
- Background-only think calls (reactor handlers, scheduler jobs, staff agents) — no UI surface to mount on.
- Aggregator / chrome apps (hub, settings, store, system, topology, search) — no think calls.
- Pure pipeline apps that don't think (publish, geocode, routing).
- Per-participant model is the right granularity (rooms participants are configured individually; the app-level pill there is informational, not authoritative).

## Usage

```html
<span id="model-pill"></span>
<script>
  EOS_UI.modelPill({
    app: 'viz',                  // required — app id matching manifest
    mount: '#model-pill',        // CSS selector or element
    domain: 'code',              // optional — affects chain resolution
    onSwitch: function(prov) {   // optional — fired after switch persists
      // e.g. surface a toast, re-render a preview, etc.
    },
  });
</script>
```

Returns `{refresh: function}` — call `refresh()` to re-poll provider state (rarely needed; the pill re-reads on every page load).

## Toolbar placement convention

- **Right side of the app header**, near the `⚙ Settings` button (most natural position; mirrors where users look for app-level state).
- **Same row as Reload / Export / status chips** — pill is a peer of those, not a primary action.
- When a `<title>` and other chips coexist, pill goes between status chips and Settings.

## Cost taxonomy

`EOS_UI.MODEL_COSTS` maps provider name → cost class. Lookup is prefix-based (`openai-mini` → `openai-` → `paid` if not directly matched). Variants ending `:free` (e.g. `openrouter:deepseek/foo:free`) override `mixed` to `free`.

| Class | Icon | Meaning |
|---|---|---|
| `free` | 🆓 | Free via subscription (claude-cli with Max sub) |
| `local` | 🔒 | Local, fully offline (ollama) |
| `paid` | 💰 | Per-token API cost (openai*, paid openrouter) |
| `human` | 👤 | Human-in-the-loop (skip in dropdown) |
| `unknown` | · | Unmapped — extend `EOS_UI.MODEL_COSTS` |

Apps SHOULD NOT hand-roll cost classification. Extend `EOS_UI.MODEL_COSTS` so every pill instance benefits.

## Switch semantics

- Click pill → modal popover listing every reachable provider in the chain + a "↺ Use chain default" row.
- Click a provider → `POST /settings/api/set {key: "think.app.<id>", value: "<provider>"}`. Persists across restarts.
- Switch takes effect on the **next** `self.think()` call (`base_app_think.py::think`, bound onto `BaseApp`, reads the setting per-call, no daemon restart).
- Click "↺ Use chain default" → clears the override, app reverts to per-domain capability chain.

**Sticky per-app**, not per-session. Matches the user mental model: "I want viz to always use claude." For per-session overrides, the existing tier picker (`EOS_UI.tierPicker()` + `/model <prov>` slash command) is the right surface.

## Coexistence with other model pickers

- `EOS_UI.tierPicker()` (4-tier modal: auto/fast/standard/pro) — still useful for *per-session* tier presets (agent, assistant). Pill = persistent default, tier picker = one-session override. Both can coexist (assistant has both).
- Per-participant model fields in rooms — the participant config IS the per-participant override. App-level pill in rooms is informational + sets the default for new agent-type participants.

## Enforcement

`scripts/check_ai_native.py` (preflight `--scope apps`, advisory) reports two
shapes: **dark AI** (backend `think`, no chip, no assistant reach) and **split
AI chrome** — an app that mounts the pill on a *secondary* page but not on
`pages/index.html` (+ the sibling `.js` it loads). The second exists because the
chip scan ORs across pages, so a multi-page app could pass while its **primary**
surface — where nearly every visit lands — spent the user's budget invisibly.
That is exactly how kb shipped with the pill on `docs.html` only (found by hand,
2026-07-11; now pinned by `tests/test_unit_check_ai_native.py`).

**Every user-path surface that spends `think` gets its own pill** — not one per
app. If a page legitimately has no AI path, opt out at the call site with an
inline `<!-- ai-native: ignore split-chrome (why) -->` comment.

## Anti-patterns

- **Hand-rolled "current model" labels** — replace with the shared pill. The pill knows about override sources (📌 pinned vs chain) and reachability — bespoke labels won't.
- **Forcing a provider in code** — don't pass `provider=` in `self.think()` calls just because the pill should drive it. The settings override path handles this; bypassing it breaks the pill's UX.
- **Per-domain pills** — settings store is per-app, not per-(app,domain). If a single app legitimately needs different providers per domain, the pill is not the right surface; lobby for a `think.app.<id>.domains.<d>.providers` settings extension first.
- **Auto-tinting the pill amber for big inputs** — premature. Ship the basics first.

## Graduation paths

- **Auto-mount via manifest** — `[provides.model_pill]` in manifest, kernel injects the chip via `eos-components.js` on every contributing app's page (same pattern as the 4D timeline 📅 button). Build when 3+ apps want it AND drop-in friction is real. Today: drop-in component per app, manual mount.
- **Per-domain override** — when 2+ apps need different providers per domain in the same app, extend the settings schema to `think.app.<id>.domains.<d>.providers` and add a `domain` selector to the pill popover.
- **One-shot override** — a "use openai for just this turn" path. Would need a `provider=` pass-through on the Generate/Submit buttons; the pill could surface it as a "for this turn only" row in the popover.
