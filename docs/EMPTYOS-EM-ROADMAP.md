# emptyos-em — Numerical Computation Platform Roadmap

> **Sibling project to EmptyOS.** A pure-numerical Python package for power-systems / electromagnetics analysis. EmptyOS is one consumer; CLI, Jupyter, batch jobs are others. EmptyOS-side adapter lives in `engines/em/` and re-exports from the sibling repo.

## Meta-question (answer before committing past Horizon 1)

**Why build, not wrap?** The default path is wrap-first: integrate CDEGS / OpenDSS / pandapower / NEC2 behind the EmptyOS data contract, replace pieces with native kernel code only when the wrap is genuinely insufficient (regulatory, custom physics, IP independence, no commercial path). This roadmap assumes that question is answered "build, eventually, but prove the shell first."

**The product is the integration**, not the solvers. Vault-backed, version-controlled, scriptable, AI-augmented engineering work where every calculation is a first-class artifact. SES doesn't have that. If the project drifts into "build six solvers" and EmptyOS becomes incidental UI chrome, it has gone off-philosophy regardless of math quality.

## Architecture (three layers, one contract, two engine families)

```
EmptyOS apps (apps/cables, lines, earthing, lightning, studies,
              interference, traction, cp, sim …)
                                 ↓
              engines/models/ (in-tree Pydantic adapter)
                                 ↑ re-exports
              emptyos-power-contracts (standalone package)
                                 ↓
   ┌──────────────────────────────────────────────────────────────┐
   │  In-tree light-compute substrate (peer engines)              │
   │  engines/lines/  · engines/em/resap/  · engines/thermal/     │
   └──────────────────────────────────────────────────────────────┘
                  ↓                                  ↓
   ┌─────────────────────────────┐    ┌─────────────────────────────┐
   │  engines/sim/ — PSCAD-class  │    │  emptyos-em (sibling repo)  │
   │  Time-domain (Dommel + trap) │    │  Frequency-domain + BEM/MoM │
   │  EMTP-class transient solver │    │  CDEGS-class                │
   │  Component library, controls │    │  splits/ malt/ malz/ hifreq/│
   │  In-tree, v0.1               │    │  green/ mesh/ linalg/       │
   │  Runner: apps/sim/           │    │  validation/                │
   │  validation/                 │    │  No dedicated runner app    │
   └─────────────────────────────┘    └─────────────────────────────┘
```

Note: SPLITS covers fault distribution (CDEGS legacy name: FCDIST). Six native engines in the em family; `engines/sim` (PSCAD-class) and `engines/thermal` (IEC 60287) are peer in-tree engines, not nested under em.

**Non-negotiables:**
- Kernels know nothing about EmptyOS. No FastAPI, no vault, no UI imports — applies to both `engines/sim/` (in-tree but self-contained) and `emptyos-em` (sibling repo).
- `emptyos-em` is a separate repo with its own release cadence and CI. `engines/sim/` lives in-tree because it's already there and the EMTP component library is tightly coupled to power-systems use cases EmptyOS owns.
- The data contract package is the integration point — Pydantic models that **both** engine families depend on. Rename target: `emptyos-power-contracts` (or keep `emptyos-em-contracts` if the dual-family scope is documented inside).
- Shared light-compute engines (TRALIN line constants, RESAP soil fitting) sit *between* the contracts and the heavy engines. Both sim and em depend on them; neither pulls the other's heavy dependencies.
- Validation suite is first-class. Analytical limits, published reference cases, cross-checks across engine families (sim vs em on the same network → must agree at DC / power-frequency), convergence studies, regression tests.

## Shared substrate (build once, modules are cheap)

**Six engines, not seven.** FCDIST and SPLITS are the same solver — SPLITS is the newer name; treat as one.

| Layer | Used by | Difficulty |
|---|---|---|
| Geometry | RESAP, TRALIN, MALT, MALZ, HIFREQ | Low |
| Mesh | MALT, MALZ, HIFREQ | Medium |
| Green's functions | RESAP, MALT, MALZ, HIFREQ | **Very high** (Sommerfeld integrals) |
| Linear algebra (dense → ACA/H-matrix) | MALT, MALZ, HIFREQ | High at scale |
| Network (sparse MNA) | SPLITS | Low |

Modules become thin specializations: RESAP = Green's + nonlinear LSQ. TRALIN = Carson + matrix algebra. SPLITS = sparse MNA. MALT = mesh + Green's + dense LA. MALZ = MALT + per-segment internal impedance. HIFREQ = mesh + full-Maxwell Green's + compressed LA + freq sweep.

## What CDEGS actually is — engines vs orchestration

A reading of the CDEGS documentation in `{vault}/99_Attachments/cdegs/` (24 PDFs) reveals that most of the SES product line is **orchestration over the six engines, not new physics**. This is exactly the layer EmptyOS apps are good at. Mapping:

| CDEGS tool | Category | EmptyOS equivalent |
|---|---|---|
| RESAP | Engine — soil fitting | `engines/em/resap` |
| TRALIN | Engine — line params | `engines/em/tralin` |
| SPLITS (= FCDIST) | Engine — sparse network | `engines/em/splits` |
| MALT, MALZ | Engine — BEM grounding | `engines/em/malt`, `engines/em/malz` |
| HIFREQ | Engine — full-Maxwell MoM | `engines/em/hifreq` |
| AutoGround | Orchestration | `apps/earthing` (IEEE 80 + sensitivity loops) |
| AutoGridPro | Orchestration (project mgmt) | `apps/studies` (vault-backed pipelines) |
| ROWCAD | Spatial / corridor editor (overhead transmission corridors) | `apps/lines` + `EOS_MAP` corridor view |
| CorrCAD (on/offshore) | Cathodic protection workflow | `apps/cp` (uses HIFREQ + chemistry) |
| SESShield-3D | Lightning shielding | `apps/lightning` — **rolling-sphere geometry, no solver, Horizon 1** |
| SESTrainSimulator | Traction interference | `apps/traction` (TRALIN + transient) |
| SESTLC, MultiFields-Pro | AC/EMF interference | `apps/interference` (TRALIN + HIFREQ) |
| SESCurvefitDigitizer, GISGRND, SESCAD | Preprocessors | absorbed into respective app UIs |

**Key insight.** The orchestration apps (right column, lower half) can mostly ship without native engines, by calling wrapped CDEGS / OpenDSS / pandapower / NEC2 behind the data contract. Native engines replace those wraps over the horizons. *This is the wrap-first execution path.* The EmptyOS-as-shell value proposition is unlocked at the orchestration layer first; native engines mature underneath a stable app surface.

**`apps/lines` vs `apps/cables` boundary.** `apps/lines` is the overhead-transmission corridor app — ROWCAD-equivalent spatial editor for towers, conductors, ROW geometry. `apps/cables` is the cable-reticulation app reproduced from `KevinBean/Cable-reticulation-tool` — covers cable schedules, network nodes (turbines/substations/BESS), routing, ampacity, sheath bonding. Both consume `engines/lines/` for parameter calculation (Carson for overhead, Pollaczek for cable), but they are peer apps with distinct UIs and distinct user workflows. They do not share a parent app.

## Two engine families: PSCAD-class (sim) and CDEGS-class (em)

PSCAD is the time-domain twin of the frequency-domain CDEGS family. Both are in scope; both share more than their separate vendor histories suggest. EmptyOS already has the time-domain side started — `engines/sim/` is a Dommel companion-circuit + trapezoidal integration solver, which *is* the EMTP/PSCAD method. The frequency-domain side is the `engines/em/` work this roadmap describes. They are peer engine families, not a sequence.

The mistake to avoid is duplicating the parts that overlap. The clean split:

```
            engines/models/ (in-tree adapter, re-exports contracts)
                              ↑
            emptyos-power-contracts (standalone Pydantic package)
                              ↑
        ┌─────────────────────┴───────────────────────┐
        │  engines/lines/        engines/em/resap/    │
        │  (overhead + cable     (soil fitting)       │
        │   params, in-tree)                          │
        └─────────────────────┬───────────────────────┘
                              ↓
              ┌──────────────────────────┐  ┌─────────────────────────┐
              │  engines/sim/ (in-tree)  │  │  emptyos-em (sibling)   │
              │  PSCAD-class             │  │  CDEGS-class            │
              │  Dommel + trap           │  │  SPLITS, MALT, MALZ,    │
              │  Components, controls    │  │  HIFREQ                 │
              │  Time-domain transients  │  │  3D fields, GPR, touch  │
              └──────────────────────────┘  └─────────────────────────┘
```

### Overlap matrix — what is shared, what is peer-specific

| Concern | sim / PSCAD-class | em / CDEGS-class | Status |
|---|---|---|---|
| Soil characterization | Consumes ρ-layered model for ground return | Consumes ρ-layered model for Green's | **Shared:** RESAP, one engine |
| Line / cable R/L/C matrices | Consumes for branch impedance | Consumes for fault-distribution branches | **Shared:** TRALIN, one engine |
| Network topology | Branches, nodes, sources | Same | **Shared model**, distinct solvers |
| Component library (machines, controls, FACTS, HVDC, sources, fault models) | Yes — defining feature of EMTP-family | No — CDEGS doesn't model components | **sim-only** |
| Time-domain integration | Yes — trapezoidal, variable-step optional | No | **sim-only** |
| Spatial field solving (3D potentials, currents, fields) | No | Yes — BEM (MALT/MALZ), MoM (HIFREQ) | **em-only** |
| Grounding analysis (GPR, touch/step) | Consumes Z_grid as input impedance | Computes from geometry | em produces, sim consumes |
| Fault analysis | Transient: surge propagation, recovery voltage | Steady-state: current distribution | Different solvers, same network |
| Lightning | Surge propagation through tower/line/grid | High-frequency fields, induced currents | Both relevant, complementary |
| Power-electronics, HVDC, FACTS | Yes — switching, control loops | No | **sim-only** |
| EMI / induced voltage on parallel conductors | Limited (time-domain coupling) | Yes — TRALIN + HIFREQ | em primary |

### Three substrate tiers, ranked by who pays for them

1. **Pure-data substrate.** `emptyos-power-contracts` — a small standalone Pydantic package (own pyproject) that EmptyOS, `emptyos-em` (sibling repo), and any non-EmptyOS consumer (CLI, Jupyter) all depend on. Models: SoilModel, ConductorGeometry, NetworkTopo, LineParameters, AmpacityInput/Result, GridResponse, EMResponse, TransientResponse. Inside EmptyOS, the in-tree adapter `engines/models/` re-exports these models with any EmptyOS-specific helpers (vault-friendly serialization, override resolution) layered on top — apps import from `engines/models`, never directly from `emptyos-power-contracts`. The contracts package is the version-pinned wire format; `engines/models` is the in-process Python convenience layer.
2. **Light-compute substrate (small, both engine families depend on).** RESAP soil fitting (in-tree at `engines/em/resap` for now; can move) and TRALIN line-constants (in-tree at `engines/lines`). Lightweight pure-Python — sim shouldn't pull the heavy em sibling-repo dep just to compute branch impedances. `engines/lines` and `engines/thermal` are in-tree, peer to `engines/sim/`.
3. **Heavy-compute substrate (em-only).** Green's functions, mesh, BEM, MoM, Sommerfeld. Stays in `emptyos-em` (sibling repo). sim and thermal never need it.

### App-layer composition: studies span both families

The orchestration apps (`apps/studies`, `apps/earthing`, `apps/lightning`, `apps/interference`) compose results across both families:

- **Fault study** = SPLITS (steady-state distribution, em) + sim (transient surge waveform) on the same network. Side-by-side in one vault note.
- **Lightning study** = sim (surge propagation through tower/line/grid) + HIFREQ (high-frequency induced fields, when at horizon 4) + `apps/lightning` (rolling-sphere shielding). One incident, three views.
- **Interference study** = TRALIN (line params) + HIFREQ (induced voltages) + sim (time-domain validation of the steady-state numbers).

The `compare_methods()` framework extends naturally here: methods on a fault-study endpoint can include `method = "splits-only"`, `method = "sim-only"`, `method = "both"` (cross-validate), `method = "cdegs-wrap"`, `method = "pscad-wrap"`. Same input, multiple computational paths, vault-recorded provenance.

### What this means for the roadmap

- **`engines/sim/` (PSCAD-class) is not a separate horizon.** Already in-tree at v0.1, with `apps/sim/` as its dedicated runner and at least one consumer (`apps/personal/fault-distribution` calls `self.engine("sim")`). Matures in parallel with em horizons; its component library + custom-component story is its own roadmap.
- **TRALIN lands in `engines/lines/`** (in-tree, peer to `engines/sim/`), not `engines/em/tralin/`. Shared service; both engine families consume it. `engines/lines/` covers both overhead (Carson) and cable (Pollaczek/Wedepohl, Phase C) parameters.
- **RESAP stays in `engines/em/resap/` for now** (in-tree under the em namespace, but importable without the heavy sibling-repo `emptyos-em`). Its outputs are `emptyos-power-contracts.SoilModel` so sim's adapters consume directly without em coupling.
- **`engines/thermal/` is in-tree, peer to sim and lines.** Not in the sibling repo — it's lightweight, EmptyOS-coupled, and shares the contracts package only.
- **`apps/em` runner does NOT exist.** Asymmetric with `apps/sim/`: sim is invoked directly via the runner for ad-hoc circuit simulation; em is consumed by orchestration apps (earthing, interference, lightning, traction, cp) which give it the per-domain context it needs. If a generic em runner ever earns its keep, add it then; for now, no.
- **PSCAD wrap is a parallel option to CDEGS wrap** under Gate 0 — same wrap-first principle, different vendor. Wrapping PSCAD is harder than wrapping CDEGS because PSCAD's automation surface is weaker, but it's still the cheap path versus rebuilding EMTP from zero.

The architectural goal is that an engineer doing a substation study never thinks "I need PSCAD for this part and CDEGS for that part." They use EmptyOS apps, the apps select the right engine family per question, and provenance shows which one ran.

## Cables: not one engine, a cluster of concerns

"Cable calculation" sounds like it should be a single engine but isn't — it's a cluster of physics that decomposes across the substrate we've already drawn, plus **one genuinely new engine** (thermal). Treating "cables" as one box obscures where the work actually lives.

### Decomposition

| Cable concern | Physics | Where it belongs | New work? |
|---|---|---|---|
| Cable electrical parameters (R, L, C, sequence Z, semicon/sheath/armour modelling) | EM in layered cylindrical geometry — Pollaczek / Wedepohl, semicon dispersion | **`engines/lines/`** — extend TRALIN beyond overhead lines | Extension (layered cylinder modes ≠ Carson half-space) |
| Sheath bonding & induced voltages (cross-bonded, single-point bonded, solidly bonded) | Steady-state EM coupling in parallel circuits | **em-family** — TRALIN params + SPLITS network | Mostly free once params land |
| Cable transient analysis (switching surges, sheath overvoltages, ferroresonance) | Time-domain wave propagation, frequency-dependent line model | **sim-family** — `engines/sim/` with FD-line element | sim-engine v0.2 element library work (already on the deck per memory) |
| Cable lightning surge propagation | Same as transient + injection model | sim-family | Same as above |
| Cable EMI / induced voltages on parallel conductors (pipelines, telecom) | TRALIN params + HIFREQ field calc | em-family — `apps/interference` | Free once interference app exists |
| **Ampacity (current rating, steady-state)** | **1D thermal conduction with thermal resistances — IEC 60287** | **NEW: `engines/thermal/`** | Yes — closed-form, but a real new engine |
| Cyclic / emergency loading | Time-varying thermal — IEC 60853 | `engines/thermal/` Horizon 2 | Yes |
| Non-standard thermal (ducts, risers, multi-cable interaction beyond IEC) | 2D FEM thermal | `engines/thermal/` Horizon 3 — or cloud FEM wrap | Yes; expensive |
| Cable routing / sizing / reticulation (which feeder, what cross-section, voltage drop, losses) | Power-flow + economic sizing | App-layer — `apps/cables` calling SPLITS / pandapower | Mostly orchestration |
| Mechanical installation (pulling tension, sidewall pressure, bending radii) | Capstan equation, mechanical statics | `engines/cable-mech/` (small, optional) or skip; closed-form, fits in app | Small, low priority |
| Spatial routing in 3D environment (trays, racks, conduits) | Geometry / pathfinding | App-layer — already covered by `tool-blender-cable-routing` skill | Already exists |

### What this means

**One genuinely new engine: `engines/thermal/`** — in-tree, peer to `engines/sim/` and `engines/lines/`. IEC 60287 is mostly analytical (thermal-circuit equivalents — same R, π/T topology as the electrical analog, but with thermal resistances and capacitances). Per-cable installation type (direct buried, in duct, in air, in pipe-type, riser) has its own formula set. CYMCAP is the commercial tool here; nothing in CDEGS or PSCAD overlaps directly. **Algorithm reference + validation source already exists** in `KevinBean/Cable-reticulation-tool` (JS) — see § "Cable Reticulation Tool" below. Effort: ~3 weeks for Phase A subset, ~3 months for full IEC 60287 coverage.

**`engines/lines/` becomes the line/cable parameter engine, not just the line engine.** The extension from overhead (Carson half-space) to cable (layered cylinder + Pollaczek) is real work — different math, different geometry, but the *output* (R/L/C matrices, modal decomposition) is the same shape. Both sim and em consume the same way. This was already implicit in the `engines/lines/` placement; cables make it explicit.

**`engines/sim/`'s v0.2 element library is the cable transient story.** Frequency-dependent line models (Marti, Universal Line Model) are the standard EMTP-class way to do cable transients. This is on the existing sim roadmap per memory; mentioning it here only to clarify that no separate "cable sim engine" is needed.

**`apps/cables` is the orchestration app.** Just like `apps/earthing` orchestrates RESAP + MALT + IEEE 80, `apps/cables` orchestrates `engines/lines` (parameters) + `engines/thermal` (ampacity) + SPLITS (network sizing) + sim (transient validation) + Blender skill (3D routing). Cable reticulation = app-layer concern, not a new engine.

### Updated tier diagram

```
                emptyos-power-contracts (standalone Pydantic package)
                              ↑
                    engines/models/ (in-tree adapter + helpers)
                              ↑
   ┌──────────────────────────┼──────────────────────────────────┐
   │   In-tree light-compute substrate (peer engines)            │
   │   engines/lines/  (overhead Carson + cable Pollaczek)        │
   │   engines/em/resap/  (soil fitting)                          │
   │   engines/thermal/  ← NEW (IEC 60287/60853, port from JS)   │
   └──────────────────────────────────────────────────────────────┘
                  ↓                                  ↓
   ┌──────────────────────┐              ┌──────────────────────┐
   │  engines/sim/        │              │  emptyos-em          │
   │  PSCAD-class         │              │  (sibling repo)      │
   │  In-tree, v0.1       │              │  CDEGS-class         │
   │  Runner: apps/sim/   │              │  splits/ malt/ malz/ │
   │  Cable transients    │              │  hifreq/ green/      │
   │  via FD-line (v0.2)  │              │  No dedicated runner │
   └──────────────────────┘              └──────────────────────┘
                  ↓                                  ↓
                       App-layer composition
              apps/cables · apps/lines · apps/earthing
              apps/interference · apps/lightning · apps/cp
              apps/traction · apps/studies
```

### Decision gate addition

**Decided** for `engines/thermal/`: native Python port from the existing JS implementation in `KevinBean/Cable-reticulation-tool`, with CIGRE TB 880 regression as the gate. CYMCAP wrap deferred until a user with a license actually needs it (added later as another `[[provides.methods.ampacity]]` entry, not built proactively). Reasoning: the algorithm is well-known and validated; the JS tool is the spec; native Python gives batch/headless/cross-checked compute and unblocks the multi-engine cable apps.

Effort honest split: **Phase A** (3 most-used installation types — direct buried, in duct, in air — plus dielectric/sheath/conductor/thermal-resistance modules and TB 880 regression passing) ≈ 3 weeks. **Full IEC 60287 coverage** (pipe-type, riser, multi-cable interaction, edge cases) ≈ 3 months thereafter.

## Cable Reticulation Tool: existing artifact, integration plan

`KevinBean/Cable-reticulation-tool` (private GitHub) is a substantial existing tool that overlaps strongly with what `apps/cables` would be. The integration question is not "what to build" but "what to import, what to bridge, what to defer."

### What it already is (don't rebuild)

- **React + Canvas browser app**, no build step. Network editor: nodes (turbines, substations, BESS) + edges (physical connections) + cables (electrical, with networkPath).
- **Cable Schedule** — spreadsheet-style batch calculation workspace, 91 columns, designed for 50–100+ cables per project. Auto-runs IEC 60287 derating on every cable on load. *This is the killer UX EmptyOS doesn't have today* — a boards-class table over engineered records with hierarchical overrides and live calculation.
- **Cable Dialog** — 5-tab detailed editor with override semantics (cable override → cable property → project default → system default).
- **Unified field system** — 69 cable fields defined in one place (`cable-field-definitions.js`); both Schedule and Dialog auto-generated. Same single-source-of-truth pattern as EmptyOS's `[[provides.methods]]` calculator framework, but JS-side.
- **IEC 60287 thermal calculator** (Oct 2025) — dual modes (inverse θ→I, forward I→θ), 5-tab professional UI, ~100 thermal fields beyond the cable schema. **This is `engines/thermal/` already built** — in JavaScript.
- **CIGRE TB 880 validation** — 7 benchmark test cases wired, ±0.5A target accuracy. **This is the validation/ suite for thermal already built.**
- **Cable library** — 500+ Nexans cables with full electrical/thermal properties. Vault-able reference dataset.
- **Cable economics** (Oct 2025) — lifecycle CAPEX/OPEX/carbon comparison. Out of scope for the engine roadmap but valuable.
- **Other features** — BFS pathfinding for cable routes, single-line diagram generation, DXF/CSV/PNG export, Firebase auth deployment, background-image calibration for site plans.

### What this changes in the roadmap

1. **`engines/thermal/` algorithm is not a from-scratch build.** A working IEC 60287 implementation exists in JavaScript; the work is a careful Python port against TB 880 regression, not algorithm research.
2. **`apps/cables` is a clean reproduction, not an integration.** The existing tool is reference + validation source; EmptyOS reproduces it natively on the advanced architecture (vault-backed, calculator-framework, Python compute, shared EOS_UI substrates). Reasoning in the next subsection.
3. **CIGRE TB 880 validation suite for thermal is reusable** — 7 benchmark cases already wired in the JS tool; port directly as the Python regression suite.
4. **Two generalizable EOS_UI substrates emerge** from the reproduction — `engineeringSchedule` (batch-calculating spreadsheet over engineered records, hierarchical overrides) and `networkCanvas` (node/edge canvas with operation modes). Both pay back across `apps/network`, `apps/grid`, `apps/lines`, `apps/studies`.

### Decision: reproduce native with the advanced architecture

The existing tool becomes the **specification, algorithm reference, and validation source** — not the implementation we mount. Reasons the bridge-as-JS path was rejected:

- React + Canvas + localStorage + Firebase doesn't fit EmptyOS conventions (vault as source of truth, event bus, capabilities, calculator framework, server-side compute, hash-routed details, EOS_UI shared components). Bridging means dragging a parallel stack we then maintain forever.
- Browser-only JS calculation rules out batch jobs, headless CLI, server-side cross-validation, and non-cable-UI consumers (`apps/studies` running ampacity as one pipeline stage). These are exactly the cases where `compare_methods()` and the engine architecture earn their keep.
- The tool's internal patterns — "Unified Field System" with one-source-of-truth for 69 fields, hierarchical override resolution, batch-calculating Cable Schedule — are JS-side reinventions of substrates EmptyOS already has (`[[provides.methods]]`, manifest-driven UI, boards). Reproducing them on the EmptyOS substrate generalizes them; bridging keeps them locked into the cable app.
- The `app.js` file is 8,853 lines and the repo carries 100+ overlapping `.md` design docs — historical drift that a clean reproduction shouldn't inherit.
- Multi-engine future (thermal + lines + sim transients + em sheath bonding) needs Python-side orchestration; bridging delays that for no compounding gain.

### What to extract from the existing tool (not what to embed)

| Asset | Extract as | Where |
|---|---|---|
| IEC 60287 algorithm (dielectric, sheath, conductor losses, thermal resistances, dual-mode θ↔I) | Port to Python | `engines/thermal/iec60287/` |
| CIGRE TB 880 — 7 benchmark cases | Port as pytest regression suite | `engines/thermal/validation/cigre_tb880/` |
| 69-field cable schema | Translate to Pydantic | `engines/models/cable.py` (`CableRecord`, `AmpacityInput`, `AmpacityResult`) |
| Hierarchical override semantics (cable override → cable property → project default → system default) | Generalize to a SDK helper | `BaseApp.resolve_override(value, *layers)` |
| 500+ Nexans cable library | Export to vault JSON | `{vault}/30_Resources/cables/library.json` (vault-able, swappable) |
| Cable Schedule UX pattern (spreadsheet + batch calc + override badges) | Generalize | `EOS_UI.engineeringSchedule({columns, rows, overrides, onEdit, onBatchCalc})` |
| Network canvas (nodes + edges + cable routing) | Generalize | `EOS_UI.networkCanvas({nodes, edges, onAddNode, onConnect, modes})` — usable by `apps/network`, `apps/grid`, future graph apps |
| BFS pathfinding for cable routes through node graph | Backend method | `engines/lines/routing.py` (or app-level if it stays cable-specific) |
| Single-line diagram generation | App method | `apps/cables` |
| DXF / CSV / PNG / JSON export | EOS_UI helper | export-utils generalization |
| Lifecycle cost (CAPEX / OPEX / carbon) | Separable app | `apps/cables` initially; possible extraction to `apps/lifecycle-cost` over engineered records |

**What to skip:** Firebase auth (use EmptyOS auth), localStorage persistence (use vault), the no-build-step ESM imports (use EmptyOS's static module pattern), the dual `index.html` / `app.html` / `login.html` deployment story, the 100+ design `.md` files (start clean).

### Architecture of the reproduction

```
apps/cables/                        ← orchestration app
├── manifest.toml                   [provides.methods.ampacity, sizing, voltage_drop, derating]
├── app.py                          vault-backed projects, override resolution, batch orchestration
├── pages/
│   ├── index.html                  project list (boards-style, hash-routed)
│   ├── editor.html                 network canvas + cable schedule (per project, hash-routed)
│   └── library.html                Nexans library browser
└── tests/test_sys_cables.py

engines/thermal/                    ← NEW — peer to engines/sim, engines/em
├── manifest.toml
├── engine.py                       ThermalEngine — exposes ampacity(), thermal_response()
├── iec60287/                       modular by physics chapter
│   ├── conductor_losses.py
│   ├── dielectric_losses.py
│   ├── sheath_losses.py
│   ├── thermal_resistances.py
│   └── installation_types.py       direct buried, in duct, in air, in pipe-type, riser
├── iec60853/                       Phase B — cyclic loading
└── validation/
    ├── cigre_tb880/                7 benchmark cases (port from existing tool)
    ├── cigre_tb963/                FEM benchmarks (Phase C)
    └── analytical/                 closed-form limit checks

engines/models/  (or emptyos-power-contracts)
├── soil.py                         SoilModel  (also used by RESAP)
├── cable.py                        CableRecord, AmpacityInput, AmpacityResult, CableLibraryEntry
├── network.py                      NetworkTopo, NodeRecord, EdgeRecord
└── project.py                      ProjectSettings, override-resolution helpers
```

**Cross-app composition:**

- `apps/cables` calls `self.engine("thermal").ampacity(input)` for batch derating.
- `apps/cables` registers `[[provides.methods.ampacity]]` with `method = "native"` (engines/thermal) and later `method = "cdegs-cable"` (wrap), `method = "cymcap"` (wrap). `compare_methods()` cross-checks.
- `apps/studies` consumes `apps/cables.run_schedule()` as one pipeline stage.
- Sheath bonding analysis composes `engines/lines` (cable params, when Pollaczek lands) + `engines/em` (induced-voltage steady state).
- Cable transients compose `engines/sim` (FD-line element, sim v0.2 deck) + the same network model.

### Phasing — sub-roadmap for the cable reproduction

**Phase A — bones (Horizon 1, weeks).**
1. Pydantic data contract (`engines/models/cable.py`, `network.py`, `project.py`).
2. Port IEC 60287 to `engines/thermal/` — start with the most-used installation types (direct buried, in duct, in air). Defer pipe-type, riser, and exotic installations.
3. Port the 7 CIGRE TB 880 cases as pytest regression suite. Target: same ±0.5A accuracy the JS tool already achieves.
4. `apps/cables` scaffold — project list, project detail page with a basic cable schedule (table only, no canvas yet), batch-derating run button. Vault-backed persistence.
5. Import Nexans library to `{vault}/30_Resources/cables/library.json`. Library browser page.
6. Register `[[provides.methods.ampacity]]` with `method = "native"`.

**Phase B — UX (Horizon 1 → Horizon 2).**
1. `EOS_UI.engineeringSchedule()` substrate — extracted from cable-schedule needs, designed for reuse by future engineering apps.
2. `EOS_UI.networkCanvas()` substrate — node/edge canvas with operation modes.
3. `apps/cables` editor combining schedule + canvas, hash-routed per project.
4. Hierarchical override UI (badges: 🟠 OVERRIDE, 🟢 CABLE VALUE, 🔵 PROJECT DEFAULT). Backed by `BaseApp.resolve_override()`.
5. BFS pathfinding for cable routes; SLD generation; export (DXF/CSV/PNG).

**Phase C — depth (Horizon 2).**
1. IEC 60853 cyclic loading.
2. Lifecycle cost (CAPEX/OPEX/carbon) — possibly factored as `apps/lifecycle-cost`.
3. `engines/lines/pollaczek.py` for cable parameters (R/L/C in layered cylindrical geometry) — feeds sheath bonding via em-family.
4. Sheath bonding analysis app (uses `engines/lines` + `engines/em`).

**Phase D — depth+ (Horizon 3+).**
1. CIGRE TB 963 FEM thermal for non-standard installations — wrap a Python FEM library (FEniCS / scikit-fem) or implement 2D thermal directly.
2. Cable transients via `engines/sim` v0.2 frequency-dependent line element. Cross-validates against em-family sheath-overvoltage steady state.
3. `compare_methods()` flagging discrepancies between native, CDEGS-wrap, and CYMCAP-wrap implementations.

### What this commits us to

- **Real Python port effort** for IEC 60287 — not weeks of JS embedding, but weeks-to-months of careful porting against TB 880 regression. Justified because: thermal becomes a first-class engine, batch/headless/cross-checked, and the algorithm becomes intelligible code rather than buried in an 8k-line file.
- **Two new generalizable EOS_UI substrates** (`engineeringSchedule`, `networkCanvas`) — these have value beyond cables and pay back across `apps/network`, `apps/grid`, `apps/lines`, `apps/studies`.
- **Nexans library as vault data** sets the precedent for other engineering reference catalogues (overhead conductors, transformers, tower geometries) all living in `{vault}/30_Resources/`.
- **The existing Cable Reticulation Tool stays useful** during the reproduction as a live reference: side-by-side comparison runs (manually, then via `compare_methods()` once the Python is up) verify the port. Once Phase A passes TB 880 regression and reproduces ~5 real project cases within tolerance, the existing tool can be archived or kept as a parallel reference — but is no longer in EmptyOS's dependency surface.

### Reproduction knock-ons elsewhere in the roadmap

- **`engines/thermal/` Horizon 1** — algorithm research is already done by the JS tool; Phase A is a careful Python port + TB 880 regression rather than a from-scratch build. ~3 weeks effort instead of months.
- **Validation suite for thermal** — CIGRE TB 880 cases already curated in the JS tool; ported as pytest, they are the Phase A → Phase B gate (Decision Gate 2).
- **Cable library as vault data** — `{vault}/30_Resources/cables/library.json` sets a precedent for other engineering reference catalogues (overhead conductors, transformers, tower geometries) all vault-resident.
- **Two new EOS_UI substrates** — `EOS_UI.engineeringSchedule()` and `EOS_UI.networkCanvas()` extracted in Phase B pay back across `apps/network`, `apps/grid`, `apps/lines`, `apps/studies`, future graph apps.
- **Cable economics** — `apps/cables` ships with lifecycle costing in Phase C. Possibly extractable later as a generic `apps/lifecycle-cost` over engineered records of any kind (cables → transformers → entire substations).
- **Hierarchical override resolution** — Phase B extracts `BaseApp.resolve_override(value, *layers)` from cable-app needs; available to any other EmptyOS app dealing with default→project→entity override chains.

## Existing artifact inventory & reuse strategy

Beyond the Cable Reticulation Tool, **the user's GitHub + local notebook contain reference implementations or near-finished tools for most Horizon-1 deliverables**. This is not a from-scratch roadmap; it is largely a **port + validate + integrate** roadmap, with the EmptyOS substrate (vault, calculator framework, EOS_UI) providing the long-lived shell that the JS prototypes don't have.

### Catalogue

Local `{vault}/.../calculators/` (single-file Python / JS / HTML):

| Source | Algorithm | Lines | Maps to |
|---|---|---|---|
| `Kennelly Formula/Kennelly Fornula.py` | Buried-cable temperature via image method (analytical) | 79 | `engines/thermal/kennelly.py` — closed-form alternative + cross-check vs IEC 60287 T4 |
| `cable_drum/` | Cable drum capacity (winding layers, length, bending radius) | 459 | `engines/cable-mech/drum.py` (small new engine) — `apps/cables` workshop method |
| `electrical_stress/` | Coaxial E-field at conductor & insulation OD; nominal + impulse | 322 | `engines/cables/electrical_stress.py` — `apps/cables` `[[provides.methods.stress]]` |
| `resistance_calculator/` | R(T) temperature scaling per material | 186 | Already in `engines/thermal/iec60287/conductor_losses.py`. **Skip; duplicate.** |
| `emf/` (Python + JS + HTML) | 3D Biot-Savart + method-of-images E-field; catenary conductors | 624 (JS core) + 2315 (UI) | `engines/em/biot_savart.py` + `apps/interference` Phase A |
| `short circuit/` | Dual-method short-circuit calc (likely impedance + MVA methods) | 646 | `apps/earthing` short-circuit method, or `apps/studies` |

GitHub repos (private, mostly JS/HTML SPAs):

| Repo | Status | Maps to | Effort |
|---|---|---|---|
| `Cable-reticulation-tool` | Reference + IEC 60287 + 500-cable Nexans library + TB 880 fixtures | `engines/thermal/` Phase A + `apps/cables` Phase A | Already in plan |
| `cyclic-loading-cable-rating` | **IEC 60853-1 implementation** (React+JS); 9-step calc + localStorage | `engines/thermal/iec60853/` (Phase B) | Days, not weeks |
| `BackfillFEM` | **JS FEM thermal** for cable + duct + economic + cable library import | `engines/thermal/fem/` (Phase D) — also informs duct system & economic modules | Weeks (FEM port + numpy/scipy/scikit-fem option) |
| `lightning-protection-calculator` | **Already 3D**: AS 1768 RSM + IEEE 998 EGM + Three.js viz; ~6900 lines | **Replaces our `apps/lightning` Phase 1 stub** — port to Python backend, keep Three.js viz | Days for backend port; Three.js viz can be kept as-is |
| `EMF` | 3D Biot-Savart + method-of-images, contour + chart viz | `engines/em/biot_savart/` + `apps/interference` Phase A — magnetic + electric field along profiles, grids, contours | Days for Python port |
| `power-system-simulation` | Dommel-method JS workbook, Pi-section, Bergeron, IEC 60287 — **educational + functional** | Reference for `engines/sim/` v0.2; algorithm-level cross-check; consider porting the Bergeron line model | Reference, not direct port (engines/sim already in-tree) |
| `pandapower-ui` | pandapower wrap (cloud function + UI) | `engines/em/splits/` wrap-first path; `apps/studies` integration | Architecture reference; the Python wrap belongs in `engines/em/` regardless |
| `conductor_selection` | Overhead conductor ampacity (ACSR + custom, IEEE 738) | `engines/lines/ampacity.py` — overhead Carson cousin to `engines/thermal/` (IEC 60287 covers cables, not bare overheads) | Days for IEEE 738 Python port |
| `Cable_schematic` | Single-line diagram generator | `apps/cables` SLD method (Phase B) — extract pattern; consider `EOS_UI.singleLineDiagram()` substrate | Direct port |
| `cable-pulling` | Capstan-equation pulling tension + pit + sidewall pressure | `engines/cable-mech/pulling.py` — `apps/cables` `[[provides.methods.pulling]]` | Days |
| `AutoCable` | **Knowledge vault, not code** (markdown notes: IEC 60502, IEEE 575 sheath voltage, pulling pits) | Reference material — relevant notes go to `{vault}/30_Resources/cables/` | N/A |
| `app-protection-tool` | Protection coordination (early stage) | **Out of scope per roadmap** § "Not in scope" — leave alone |
| `ieee493-calculator` | IEEE 493 reliability of industrial/commercial power systems | **Out of scope** — separate roadmap if/when reliability becomes a track |
| `calculation-system` | Generic calculation framework (early stage, JS) | Reference for what was already attempted; EmptyOS's `[[provides.methods]]` calculator framework supersedes |
| `network-tool` | Empty repo | Skip |

### Strategic implications

1. **Most Horizon-1 Phase A work is already algorithm-implemented.** The "weeks of careful porting" estimate per track collapses for tracks where a working JS reference exists. Phase A becomes: **transcribe + Pydantic-ify + pytest the JS reference's outputs** as regression baseline, then add edge cases.

2. **`apps/lightning` Phase 1 (just shipped) is incomplete relative to existing artifact.** What we wrote: 2D RSM only, IEC 62305 LPL, SVG viz. What `lightning-protection-calculator` already does: AS 1768 RSM **and** IEEE 998 EGM, 3D Three.js scene with rolling-sphere animation, equipment spheres, top + side canvas + 3D triple view, save/load. **Action: keep our `engines/thermal/`-style minimal Python core (`rolling_sphere.py`) for headless/batch use, but port the EGM module + adopt the 3D Three.js viz from the JS tool.** Reframe `apps/lightning` Phase 1 = "port AS 1768 + IEEE 998 backend, integrate Three.js viz from JS tool."

3. **Track 4 — Interference (EMF/EMI) earns Horizon-1 status.** With `EMF/emf_calculation_core.js` already implementing 3D Biot-Savart with catenary conductor sag and method-of-images E-field, `apps/interference` Phase A is days of porting, not weeks. Add as Track 4 to the Horizon-1 parallel deliverable list.

4. **Track 5 — Cable mechanical (pulling + drum) earns Horizon-1 status as a small-scope deliverable.** `cable-pulling` + `cable_drum` are both single-file engines that fit a small `engines/cable-mech/` namespace. Phase A: pulling tension along a path with pits + sidewall pressure + drum capacity. Useful immediately to substation construction users.

5. **Validation strategy upgrades.** Every native engine that has a JS reference gets a built-in cross-check from day one: a thin pytest harness reads the JS implementation's known outputs (or runs it via a headless playwright for regenerable fixtures) and asserts the Python within tolerance. This is **stronger than CIGRE-only validation** because it pins behaviour to a peer implementation the user has already trusted.

6. **`pandapower-ui` confirms the wrap-first path is real and lived-in for SPLITS.** The wrap belongs in `engines/em/splits/wrap_pandapower.py` as the default `[[provides.methods.fault_distribution]]` until native SPLITS lands. The cloud-function pattern (offload to a remote Python worker) is one valid execution mode for the wrap; in-process pandapower is another. Both register as separate methods, `compare_methods()` validates against each other.

7. **`engines/lines/` covers both overhead and cable parameters.** `conductor_selection` (IEEE 738 ampacity for bare overheads) belongs alongside `engines/thermal/` (IEC 60287 ampacity for cables) — different physics, parallel APIs. Update tier diagram to show both as in-tree light-compute peers.

8. **The Cable Reticulation Tool's "extract not embed" doctrine generalizes to every artifact in the catalogue.** Reproducing JS prototypes natively on EmptyOS substrate (vault state, calculator framework, EOS_UI components) generalizes their patterns; bridging keeps each pattern locked in its prototype shell. Same trade-off, applied uniformly.

### Updated Horizon-1 parallel tracks

The Horizon-1 first-step set expands from 3 tracks to 6, with most reusing existing JS reference implementations:

- **Track 1 — CDEGS-engines closed-form** (earthing IEEE 80 + RESAP uniform; mostly new code)
- **Track 2 — Cable reproduction Phase A** (IEC 60287 from `Cable-reticulation-tool`; in flight)
- **Track 3 — Lightning shielding** (RSM + EGM from `lightning-protection-calculator`; **upgrade our 2D stub**)
- **Track 4 — Interference EMF/EMI** (Biot-Savart + image-method from `EMF`; new track)
- **Track 5 — Cable mechanical** (pulling + drum from `cable-pulling` + `cable_drum`; new small track)
- **Track 6 — Overhead conductor ampacity** (IEEE 738 from `conductor_selection`; new small track on `engines/lines/`)

Add small calculator methods immediately (each is an hour or two): Kennelly buried-temperature on `engines/thermal/`, electrical-stress (coaxial E-field) as a method on `apps/cables`. Both are single-formula closed-form ports usable as `compare_methods()` cross-checks.

## Pluggable methods — same app surface, swappable solver

EmptyOS already has the substrate to make wrap-first work cleanly: the **calculator framework**.

- Each engine app exposes multiple methods via `[[provides.methods.<endpoint>]]` in its manifest: `method = "native"` (emptyos-em), `method = "cdegs"` (wrap, where licensed), `method = "opendss"` (wrap, where applicable), `method = "pandapower"` (wrap, for SPLITS), etc.
- `BaseApp.compare_methods()` runs all available methods on the same input and returns a side-by-side comparison — a built-in validation surface in addition to the formal `validation/` suite below.
- `BaseApp.last_compute_provenance()` records which method ran (version, runtime, hardware). Every vault-backed study captures it.
- Reference implementation: `apps/personal/fault-distribution` v0.3.0 — calls `self.engine("sim")`, registers `[[provides.methods.solve]]`. **Lives in `apps/personal/` (gitignored)**, so fresh-clone users won't see it; until a public minimal example is extracted, treat the pattern's authoritative description as `emptyos/sdk/base_app.py` (`engine`, `list_methods`, `resolve_method`, `compare_methods`, `last_compute_provenance`). Extracting a public minimal calculator-framework example into `apps/` is a worthwhile follow-up.

This is load-bearing for the wrap-first strategy: **the same EmptyOS app surface serves both wrapped and native engines**. Users with a CDEGS license use the wrap; users without it fall back to native (when ready) or open wraps. Engines mature behind a stable app surface.

Existing engine pattern in EmptyOS: `engines/<name>/` accessed via `self.engine("name")` from app code. `engines/sim/` is a working precedent — native EMTP-style power-systems engine validated to within 0.04% of CDEGS for RT-07.

## Horizons

### Horizon 1 — Foundations across three tracks (months 1–6)
**Goal: prove the data contract and the EmptyOS-as-shell value proposition across three tracks (CDEGS-engines / cable-reproduction / lightning). Stop here unless it earns the next horizon.**

**Track 1 — CDEGS-class closed-form.**
- `emptyos-power-contracts` package — Pydantic models settled. **Spend more time here than feels comfortable** — every later decision propagates from this.
- IEEE 80 closed-form calculator (`apps/earthing/`)
- RESAP: uniform + two-layer soil (analytical Green's tractable)
- TRALIN: Carson series + finite earth for typical overhead (in `engines/lines/`)
- SPLITS: simple radial networks
- `apps/studies/` orchestration layer: pipeline state in vault, re-run only affected stages.

**Track 2 — Cable reproduction Phase A (parallel; weeks).**
- `engines/thermal/` Phase A: IEC 60287 port from `KevinBean/Cable-reticulation-tool` for direct buried, in duct, in air installation types. ~3 weeks.
- CIGRE TB 880 regression suite — 7 cases ported as pytest. **Phase A passes ±0.5A on all 7 cases** before Phase B starts.
- `apps/cables` scaffold: vault-backed projects, basic cable schedule (table only, no canvas yet), batch-derating button.
- Nexans library imported to `{vault}/30_Resources/cables/library.json`.
- `[[provides.methods.ampacity]]` registered with `method = "native"`.

**Track 3 — Visible win: lightning shielding.**
- **`apps/lightning`** — port `KevinBean/lightning-protection-calculator` backend (AS 1768 RSM + IEEE 998 EGM) to Python; integrate Three.js viz directly (no rebuild needed). Recognizably the SESShield-3D core. Cheap; vivid; the screenshot. **Status: 2D RSM stub shipped; needs upgrade to 3D + EGM per existing artifact.**

**Track 4 — Interference (EMF/EMI).**
- **`engines/em/biot_savart/`** — port `KevinBean/EMF` repo: 3D Biot-Savart for catenary conductors + method-of-images E-field. Pure-Python + numpy.
- **`apps/interference`** Phase A: profile + grid + contour magnetic field around a power line; method-of-images electric field; presets for typical 132/220/400 kV transmission corridors.

**Track 5 — Cable mechanical.**
- **`engines/cable-mech/`** — port `cable-pulling` (capstan + sidewall pressure + pit logic) and `cable_drum` (winding capacity + bending radius). Small.
- **`apps/cables`** Phase A registers `[[provides.methods.pulling]]` + `[[provides.methods.drum_capacity]]`.

**Track 6 — Overhead conductor ampacity.**
- **`engines/lines/ampacity.py`** — port `KevinBean/conductor_selection` (IEEE 738 for ACSR + custom conductors).
- **`apps/lines`** consumes via `[[provides.methods.ampacity]]`. Companion to `engines/thermal/` (cables).

**Smallest first step (decision gate):** at least 4 of 6 tracks' first deliverable in parallel — earthing IEEE 80 closed-form + cable-reproduction Phase A passing TB 880 + lightning shielding (3D, both methods) + interference EMF Phase A. Validate RESAP/IEEE-80 against two published cases; lightning + interference cross-checked against the JS reference repos. Decide whether the EmptyOS surface adds value over Jupyter / the JS prototypes before committing further.

### Horizon 2 — BEM grid solver, uniform soil (months 6–12)
- Mesh layer with adaptive sizing.
- Green's function (uniform) — closed form, easy.
- Dense LAPACK wrapper.
- MALT + MALZ for arbitrary geometry, uniform soil.
- Validation: reproduce East Central Substation reference (R_g = 0.423 Ω) sub-percent.

Stop being a calculator, start being a solver.

### Horizon 3 — Layered soil (months 12–24)
- **Sommerfeld integrals.** Real numerical analysis: contour deformation, asymptotic acceleration, interpolation tables. Months not weeks.
- Multilayer RESAP fitting.
- MALT/MALZ in N-layer soil.
- Validation against published two-layer benchmarks (IEEE PES literature).

Production-grade low-frequency grounding analysis. What most working engineers actually need.

### Horizon 4 — High frequency (months 24–48)
- Full-Maxwell Green's functions (propagation, retardation).
- HIFREQ as MoM solver over the same mesh substrate.
- Frequency sweep orchestration via WorkerPool (long-running jobs already supported by EmptyOS — same pattern as ComfyUI GPU jobs).
- Validation against FEKO / NEC2 / experimental data.

Hardest horizon. Most readily deferred or de-scoped. Benefits from PhD-level computational EM on the team.

### Horizon 5 — Scale (months 36+)
- H-matrix or FMM compression (h2lib, hlibpro, or similar).
- Distributed parallelism for sweeps.
- 10,000+ segment problems tractable.

## EmptyOS integration patterns (already exist, just consume)

- **WorkerPool** — long-running solves submit jobs, return job ID, push progress via WebSocket, write result to vault note. Same pattern as `comfyui` plugin's GPU queue.
- **Cloud consent gate** — extend from "cloud LLM" to "cloud HPC compute." User clicks "run HIFREQ sweep," sees ~$X estimate, approves, job goes to remote worker.
- **Vault as pipeline state** — every stage's inputs + intermediates stored as a study. Soil ρ changes → re-run TRALIN downstream only.
- **Apps are editors, studies/ is the workflow.** Engineers think "I'm designing a substation," not "I'm running module 5."

## Validation as architecture

Peer to the kernel, not in `tests/`. Contains:

- **Analytical test cases** — hemispherical electrode, two-rod mutual resistance. Machine-precision check in fine-mesh limit.
- **Reference cases** — East Central Substation, IEEE 80 worked examples, EPRI handbook. Each = vault note: source, inputs, expected outputs, tolerance.
- **Cross-checks** — TRALIN vs OpenDSS line constants, SPLITS vs pandapower fault, HIFREQ vs NEC2 free-space.
- **Convergence studies** — h, h², h⁴ asymptotic rates verified.
- **Regression tests** — every solved case becomes one. Horizon-3 work breaks Horizon-1 result → CI catches.

A solver without this is unfalsifiable, therefore untrustworthy.

## Honest scope

Three engine families and several app-reproduction tracks in scope. **The existing-artifact catalogue (above) cuts the per-track Phase-A effort substantially** — most Horizon-1 deliverables are *port + validate* rather than *design + build*.

- **`engines/sim/` (PSCAD-class, in-tree):** v0.1 shipping; v0.2 element library (FD-line for cable transients, machines, controls) is its own ongoing roadmap. `power-system-simulation` repo provides algorithm-level reference for Bergeron + Pi-section line models. Person-months ongoing.
- **`engines/em/` (CDEGS-class, sibling repo):** 5–10 person-years at production quality across Horizons 2–4 if **all six em-family engines** (RESAP, TRALIN-in-lines, SPLITS, MALT, MALZ, HIFREQ) are built natively. The shared em substrate is ~60% of that and pays for itself across all six. **`pandapower-ui` is the wrap-first path for SPLITS** — meaningful Horizon-1 value with weeks of work.
- **`engines/thermal/` (in-tree):** ~2 weeks Phase A (port from JS); ~6 weeks for full IEC 60287 coverage; **Phase B (IEC 60853 cyclic) ~1 week** with the `cyclic-loading-cable-rating` repo as direct reference; Phase D (FEM) ~3-6 weeks with `BackfillFEM` as reference and scikit-fem / FEniCS underneath.
- **`engines/lines/` (in-tree):** overhead Carson + IEEE 738 ampacity (from `conductor_selection`) is days, not weeks; cable Pollaczek/Wedepohl (Phase C, no JS reference) remains weeks of careful EM math.
- **`engines/em/biot_savart/` (in-tree):** 3D Biot-Savart magnetic + method-of-images E-field — port `EMF` repo; days. Powers `apps/interference` Horizon-1.
- **`engines/cable-mech/` (in-tree, new small):** pulling + drum from `cable-pulling` + `cable_drum`. Days; small.
- **`apps/cables` reproduction:** Phase A weeks (parallel with thermal Phase A); Phases B–D scale with sim/em horizons they depend on.
- **`apps/lightning`, `apps/interference`, `apps/lines` orchestration apps:** each Horizon-1 Phase A is days-to-week with a JS reference plus existing EmptyOS app patterns.

Without the kernel/shell split, without shared substrate, without validation-as-architecture, the project produces parallel half-working solvers that don't compose. The architecture above is the only shape that makes "all of them" possible at all.

The wrap-first path collapses em scope dramatically: orchestration apps + lightning + interference + Horizon 1 closed-form solvers + cable-reproduction Phase A can ship in **months rather than years** — and the existing-artifact catalogue says it's closer to weeks for most tracks. Horizons 2–4 only execute on the engines that actually need native rebuilding. Thermal does not have a wrap path most users can use (CYMCAP is licensed and weakly automatable), so it's native from day one regardless.

## Not in scope

To prevent mission creep, this roadmap does **not** cover:

- **Power-flow / OPF / state estimation** — use pandapower or PowerFactory; SPLITS handles fault distribution but not load-flow optimization.
- **Protective relay coordination** — separate physics + standards (IEEE 242), possibly a future wrap; not addressed here.
- **Arc-flash analysis (IEEE 1584)** — separate physics, separate workflow; could be a future app, not in this roadmap.
- **Generic transient stability (TS) studies** — sim handles network transients but is not a TS tool. Use PowerWorld / DIgSILENT for swing-equation rotor-angle studies.
- **GIS / cadastre / land management** — `EOS_MAP` is a viewer, not a GIS database.
- **Mechanical / structural analysis of towers, poles, foundations** — separate domain (PLS-CADD, etc.); explicitly outside electrical/EM/thermal scope.
- **HVDC link control system design** — sim's component library will eventually cover HVDC primitives, but full converter control design is out.
- **Energy market / dispatch / economic simulation** — separate domain.

Out-of-scope items can become future roadmaps in their own right. Bringing them into this one would dissolve the focus.

## Decision gates

0. **Wrap or build, per engine.** Default execution path is wrap-first: license CDEGS where the user has a seat; OpenDSS/pandapower/NEC2 elsewhere — wrapped behind the data contract and the calculator-framework method system. Native engine work begins only when (a) wrap reveals a contract-shaped need that the wrap can't satisfy, (b) regulatory / IP independence demands native, or (c) wrap licensing economics fail at user scale. **Special cases:** `engines/sim/` is already native (no wrap path that makes sense for EMTP-class given EmptyOS coupling). `engines/thermal/` is decided native from day one (Cable Reticulation Tool's JS implementation is the algorithm reference; CYMCAP wrap deferred until a licensee actually needs it).
1. **After Horizon 1 first-step parallel deliverables (~weeks):** all three tracks' Phase-A artifacts in hand — earthing IEEE 80 + cable Phase A passing TB 880 + lightning shielding. Does EmptyOS-as-shell add value over Jupyter / the existing JS Cable Tool? If no, stop or fully commit to wrap-existing-solvers and don't proceed past Horizon 1.
2. **Cable reproduction Phase A → Phase B gate:** `engines/thermal/` Phase A passes CIGRE TB 880 regression to ±0.5A on all 7 cases AND `apps/cables` reproduces ≥3 real project cases within the same tolerance as the JS tool. Until both pass, don't start Phase B (UX substrates).
3. **After Horizon 1 complete (~6 months):** Is `emptyos-power-contracts` holding up across all four modules + the cable schema? Refactor now if not — every later horizon compounds the cost.
4. **Before Horizon 3 (em layered soil / Sommerfeld):** Is there a numerical-EM-literate contributor? Sommerfeld work without one is a multi-year detour.
5. **Before Horizon 4 (em high-frequency):** Is the use case real? Lightning/transient analysis is research-grade; most substation work doesn't need it.

## Repo shape (when starting)

Three locations, each with its own validation suite peer to its kernel:

```
emptyos-power-contracts/           ← standalone Pydantic package (small, stable)
├── pyproject.toml
└── src/emptyos_power_contracts/
    ├── soil.py                    SoilModel
    ├── geometry.py                ConductorGeometry, CableGeometry
    ├── network.py                 NetworkTopo, NodeRecord, EdgeRecord
    ├── line.py                    LineParameters
    ├── cable.py                   CableRecord, AmpacityInput, AmpacityResult
    ├── em.py                      GridResponse, EMResponse
    └── transient.py               TransientResponse

D:/emptyos/                        ← EmptyOS repo (this repo)
├── engines/
│   ├── models/                    in-tree adapter; re-exports contracts + EmptyOS helpers
│   ├── sim/                       PSCAD-class, v0.1 shipping
│   │   ├── elements.py · netlist.py · stepper.py · result.py
│   │   └── validation/            CDEGS RT-07 cross-check, etc.
│   ├── lines/                     overhead Carson + cable Pollaczek (Phase C)
│   │   └── validation/
│   ├── thermal/                   IEC 60287/60853, port from JS
│   │   ├── iec60287/  iec60853/
│   │   └── validation/cigre_tb880/  ← 7 cases ported from JS tool
│   └── em/
│       └── resap/                 soil fitting, in-tree (lightweight)
│           └── validation/
└── apps/                          orchestration apps
    ├── cables/    ← reproduction of KevinBean/Cable-reticulation-tool
    ├── lines/     ← overhead corridors (ROWCAD-equivalent)
    ├── earthing/ lightning/ studies/ interference/
    ├── traction/ cp/ sim/         (sim/ is the engines/sim runner)
    └── personal/                  (gitignored — fault-distribution lives here)

emptyos-em/                        ← sibling repo (heavy, optional install)
├── pyproject.toml
├── src/emptyos_em/
│   ├── splits/                    fault distribution (CDEGS legacy: FCDIST)
│   ├── malt/  malz/  hifreq/
│   ├── green/                     Sommerfeld integrals (Horizon 3)
│   ├── mesh/                      adaptive segment generation
│   └── linalg/                    dense → ACA/H-matrix wrappers
├── validation/                    peer to src/
│   ├── analytical/  references/  crosschecks/  convergence/
└── tests/
```

**Dependency direction.** `emptyos-em` and EmptyOS both depend on `emptyos-power-contracts`. EmptyOS optionally depends on `emptyos-em` (only users who want CDEGS-class native solvers install it; users wrapping CDEGS or skipping em-family work don't). `emptyos-em` does not depend on EmptyOS.

## References

### CDEGS how-to manuals (`{vault}/99_Attachments/cdegs/`)

Vendor PDFs grouped by engine family. Use these as the behavioural spec when reproducing CDEGS workflows or validating native engines.

**Cross-cutting / orchestration**
- `How-to Manuals - Common Topics and Procedures.pdf`
- `AutoGroundDesign.pdf`, `AutogridPro.pdf`

**Earthing / grounding (MALT, MALZ → `engines/em/`)**
- `Ground.pdf`, `GISGRND.pdf`, `FENCE.pdf`
- `Suburban.pdf`, `URBAN.PDF`

**Soil resistivity (RESAP → `engines/soil/`)**
- Coverage in `Ground.pdf` + `How-to Manuals` (no standalone RESAP manual in the set)

**Lines, cables, towers (TRALIN, RLC, SPLITS/FCDIST → `engines/em/`, `engines/cables/`)**
- `RLC.PDF`, `TOWER.PDF`, `CAPIND.pdf`

**EMF / AC interference (HIFREQ + SESTLC → `engines/em/`, `engines/interference/`)**
- `SESTLC - EMF and AC Interference.pdf`
- `ACTotalInterferenceStudy.pdf`
- `SESEnviroPlus.pdf`

**Lightning / shielding (`engines/em/lightning/`, `apps/lightning/`)**
- `Lightn.pdf`
- `SESShield-3D-Direct Lightning Stroke Shielding of a Substation.pdf`
- `Quick Start Guide SESShield-3D.pdf`
- `Importing DXF Drawing Into SESShield-3D.pdf`

**Cathodic protection (out of current scope)**
- `Quick Start Guide CorrCAD (Onshore).pdf` / `(Offshore).pdf`
- `Quick Start Guide SESCPCalculator.pdf`

**Other**
- `ROWCAD (Right-of-Way) User_s Guide.pdf`
- `Quick Start Guide SESTrainSimulator.pdf`
- `SESCurvefitDigitizer.pdf`

### Vault knowledge base

Distilled walkthroughs and lessons from the manuals + working sessions:

- `30_Resources/conversations/2026-04-30-ieee-80-clauses-cdegs-walkthrough.md`
- `30_Resources/conversations/2026-04-30-cdegs-module-landscape-and-learning-path.md`
- `30_Resources/conversations/2026-04-28-cdegs-substation-earthing.md`
- `30_Resources/conversations/2026-04-28-cdegs-fcdist-fault-distribution.md`
- `30_Resources/KB/power-systems/concepts/cdegs-module-landscape.md`
- `30_Resources/KB/power-systems/lessons/cdegs-*.md`, `fcdist-*.md`
