# Model-Ability Gating — features visible by model strength

EmptyOS gates features by capability **presence** (is there a `think` provider?
— tour `requires`, `/system`). This rule adds gating by capability **ability**:
is the active model *strong enough* for this feature? A weak model (gpt-4o-mini,
ollama qwen) does bounded/structured tasks well (diagrams, charts, tags, grounded
Q&A) but garbles intricate generation (3D scenes, frame animation, long
reasoning). Without ability-gating, a weak deployment exposes those and they look
broken — which is exactly what made viz's 3D option a liability on the mini-only
portfolio.

**Reference implementation:** `apps/viz/` (shape picker). Taxonomy:
`emptyos/capabilities/ability.py`. Frontend: `EOS_UI.abilityGate` /
`abilityBannerHtml` / `abilityMeets` in `eos-components.js`.

## The taxonomy

Three ordered tiers — **intrinsic model strength**, orthogonal to cost
(`EOS_UI.MODEL_COSTS`) and distinct from the tier-picker's UX presets
(fast/standard/pro):

| Tier | Good for | Examples |
|---|---|---|
| `weak` | short structured tasks, classification, tags, simple diagrams | openai-nano, ollama ≤~3B (`qwen2.5:1.5b`, `phi3:mini`) |
| `standard` | diagrams, grounded Q&A, summaries, most code | gpt-4o-mini, ~7–9B locals, claude-haiku |
| `strong` | intricate code, 3D scenes, deep reasoning, large synthesis | gpt-4o, gpt-5, claude sonnet/opus, claude-cli, ≥~70B, deepseek-r1 |

`classify(provider_name, model)` maps by name patterns (weak/standard markers
checked before strong-family markers so `gpt-4o-mini` → standard, not strong).
Unknown → `standard` (never over-restrict). Operators override per provider:
`[capabilities.think.<provider>] ability = "strong"`.

## How a feature declares + enforces a minimum

Two coupled moves — **route** + **gate**:

1. **Route (backend):** pass `min_ability=` to the think call. The capability
   reorders the resolved chain to try providers meeting the bar first; if none
   does, it runs the best available and flags `last_provenance().under_powered`.
   ```python
   html = await self.think(prompt, domain="code", min_ability="strong")
   ```
   `min_ability=None` (default) = current behaviour, byte-for-byte.

2. **Gate (frontend):** disable the affordance the active model can't meet and
   **explain** — never silent-hide. Expose the feature→min_ability map + the
   active ability from an app endpoint (reuse `BaseApp.model_ability(domain)`),
   then in the page:
   ```js
   var d = await fetch('/myapp/api/shapes').then(r=>r.json());        // {shapes, active_ability}
   opt.disabled = !EOS_UI.abilityMeets(d.active_ability, min);        // gray the <option>
   gateEl.innerHTML = EOS_UI.abilityBannerHtml({minAbility, ability, model, provider});
   ```
   Re-evaluate on `EOS_UI.modelPill({..., onSwitch})` so switching the model
   live re-opens the feature.

The platform endpoint `/api/capabilities/think/effective?app=&domain=` returns
`active_ability` + per-provider `ability` — `EOS_UI.fetchActiveAbility()` /
`abilityGate()` wrap it for any app.

## When NOT to gate (the important half)

Gating is friction. Apply it **only** where a weak model genuinely produces bad
output. Do **not** gate:

- **Bounded/structured calls** — summaries, tags, classification, extraction,
  short grounded Q&A, mermaid/chart/diagram generation. These are exactly what
  weak models are good at.
- **Cheap per-item loops** — gating each adds a resolution round-trip for no gain.
- **Background/non-UI think** (reactor, staff, scheduler) — no affordance to gate;
  if a job needs a strong model, set `min_ability=` for routing and let it run.
- **Anything that already works on the demo's ollama** — if it's fine there, it's
  fine everywhere; don't add a gate.

Over-gating makes a capable deployment feel crippled and a weak one feel broken.
The bar for adding a gate: *the feature visibly fails on a standard model.*

## Posture

- **Degrade-and-explain, never silent-hide** — mirror the tour `requires`-rewrite.
  A gated feature stays visible, disabled, with "needs a stronger model — switch
  via the model pill or add a key in Settings → BYOK".
- **Soft routing, not hard-fail** — `min_ability` is a preference; if unmet, the
  call still runs (flagged under-powered) rather than raising. The UI gate is
  what spares the user the bad output; the backend flag is for provenance.
- **Feature-level, not app-level** (v1) — gate a shape/verb/button, not a whole
  app. App-level visibility-by-ability is out of scope.

## Validity gate — companion axis to the ability gate

The ability gate is a **proxy**: it blocks intricate 3D behind `strong` models
because we couldn't otherwise tell good output from bad. Where a **validity
gate** exists, that proxy can relax — the gate is ground truth, so what matters
shifts from *who generated* to *whether the output is valid*.

The first validity gate is the Tier-1 shape-validation engine
(`engines/shape_validation/`): deterministic OCP/AABB checks that catch
physically implausible geometry ("weird shapes") after compile. Wired into the
eos-cad part path (`apps/extension/engineering/cad/compile_cq.py`) and the articulated
path (`engines/articulated/testing.py` `TestContext.check_assembly`).

**Wiring status, corrected 2026-07-27 — on these two paths the gate is
observe-and-ledger only.** The claim that findings "flow through
`CompileReport.errors`/`warnings` so the compile-retry loop regenerates on hard
violations" described an intended capability, not code:

- `ValidationReport.errors()` / `.warnings()` (`report.py:64-76`) exist and are
  shaped for `CompileReport`, but have **zero production call sites**.
- The CAD compile route decides `ok` from `res.get("ok")` +the `CAD_COMPILE_OK`
  marker (`cad/compiling.py:116`), never from `validation["ok"]` — a hard
  `INVALID_BREP` / `ZERO_VOLUME` still returns `ok: True` with the report
  attached. It is written to the ledger (`compiling.py:161`) and nothing gates.
- `TestContext.check_assembly` maps *every* violation to `severity="warning"`
  (`engines/articulated/testing.py:162`), so shape findings can never fail a
  compile on the articulated path. (Sound today, since `validate_assembly` is
  soft-only — but the mapping is hard-coded, not driven by `.errors()`.)

The one path where a hard violation genuinely fails a response and drives a
regenerate is the 2D sibling `cad/draft_validate.py` — it imports
`ValidationReport` directly and its loop ANDs `geo_rep.ok` into the result
(`cad/generate.py:242-258`). Wire the 3D paths the same way if the gate should
bite there; until then, don't rely on it.

The second is the **media validity gate** (`emptyos/sdk/media/review.py`,
`review_audio`/`review_video` → `MediaVerdict{ok, hard, soft}`): deterministic
ffprobe + ffmpeg `astats` checks that catch a silent/empty/too-short/clipped
*generated* audio output before it ships (the audio analogue of "weird shape";
borrowed from OpenMontage's post-render self-review). Same `hard`/`soft` shape,
same regenerate-on-hard posture — wired into the podcast pipeline's `audio`
stage behind `[apps.podcast] feature.output-review.enabled` (dark), one
regenerate attempt on a hard fail. Fails open (ok + soft note) when ffmpeg is
absent or output is unparseable. The frame-sampling slice shipped 2026-07-03:
`review_video` runs ffmpeg `freezedetect`+`blackdetect` and hard-fails a clip
≥90% frozen/black (the frozen-end-state an unseekable timeline records as);
first consumer is viz's Export-MP4 self-check behind
`[apps.viz] feature.output-review.enabled` (dark).

The third is the **depth validity gate** (`scripts/check_depth_sequence.py`,
2026-07-28): FLAT / RANGE / SMOOTH checks over a rendered blockout depth
sequence, gating the geometry-guided MV path before it spends ~4 minutes of GPU
on a control signal that has collapsed onto one surface. It returns the **same
`MediaVerdict`** — no ffprobe machinery is shared and none should be, since a
depth sequence is a control signal rather than a rendered artifact, but the
claim is identical in all three and the shared type is what lets a caller
(`music-studio/blockout.py`) treat every gate alike. Unlike the other two it is
**live, not dark**, and its complaint feeds a retry loop that re-authors the
scene against the gate's own words. Its DRIFT signal was demoted to advisory
after falsely rejecting a legitimate crane-up reveal three times running —
`.claude/rules/audits.md` discipline applied to a gate that had already cost
three GPU renders.

Posture mirrors the ability gate:
- **Degrade-and-explain** — a hard violation should surface a verdict, not a
  silent failure. Live on the 2D draft path; **not** on the CAD part workspace —
  the `✗ weird shape` chip described here was lost in the layouts migration
  (no consumer under `cad/pages/` or `eos-cad-views/` reads `validation`), so
  today a hard violation there is ledger-only and invisible to the user.
- **Soft routing** — soft smells (scale, floating, interpenetration) annotate but
  pass; only hard violations (non-manifold / zero-volume / empty) fail a compile.
- A **prediction ledger** (`data/shape_validation/ledger.jsonl`, written via
  `emptyos/sdk/shape_ledger.py`) records every generate→validate verdict. Once it
  shows a weak model's 3D output passes the validity gate as reliably as a strong
  model's, the ability gate for that shape can be lowered **on evidence** rather
  than by the conservative default. Until that data exists, keep the ability gate
  as-is — the two gates compose.

**Generation-side complement — the curated shape-prior.** A validity gate catches
weird shapes *after* compile; a *shape-prior* prevents them *before*. KB
`kind: pattern` notes are injected as few-shot into the generation prompt via
`emptyos/sdk/pattern_examples.py` (`resolve_pattern_examples`). The cad part path
seeds eos-cad/1 anatomy examples (`shared.DEFAULT_SHAPE_PRIOR`) that teach the exact
discipline the gate enforces — proud cutting tools (no coincident faces →
no `INVALID_BREP`) and never subtracting the whole body (no `ZERO_VOLUME`). Prior
reduces; gate catches what slips. Same injector backs viz's per-shape web-code
few-shots; a third consumer should reuse it, not re-roll.

## Cross-references
- `.claude/rules/model-pill.md` — the active-provider chip; `abilityGate` is its
  sibling and re-evaluates on `onSwitch`.
- `.claude/rules/tour-steps.md` — capability-**presence** gating (`requires`); this
  is the **ability** analogue.
- `EOS_UI.provenance()` / `BaseApp.last_provenance()` — output-side marker;
  carries `under_powered` when a call ran below its requested ability.
- `emptyos/capabilities/ability.py` — taxonomy + `classify()` + `meets()`.
