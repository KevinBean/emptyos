# Multi-Layer Soil Resistivity Inversion Engine — Design Document

| Field | Value |
|---|---|
| Engine | `engines/soil/` |
| Version | 0.2 (draft, pre-implementation of optimiser; forward kernel scaffolded) |
| Date | 2026-05-04 |
| Status | Forward kernel + soil model + Stefanesco recursion implemented; filter coefficients TODO; inverter not started |
| Validation reference | `RS_TUT1.F09` — SES CDEGS RESAP v20.0, "East Central Substation — A Two-Layer Soil Model" |
| Reference standards | IEEE Std 80-2013, IEEE Std 81 |

The naming convention "RESAP-style" is dropped from this document; the engine is just **`soil`**. RESAP is referenced only as the canonical numerical-validation target, the same way IEEE Std 80 is referenced as the standards target.

---

## 1. Executive Summary

`engines/soil/` performs **inverse soil-resistivity analysis**: given apparent resistivity (or apparent resistance) measurements taken along a traverse with a known electrode configuration, it returns an **n-layer horizontally stratified earth model** (resistivities ρ₁…ρₙ and thicknesses h₁…hₙ₋₁) that minimises the RMS error between measured and computed apparent resistivities.

The engine targets **general multi-layer** soils (n ≥ 2, practical limit ~5–6) and uses a **Hankel-transform forward kernel evaluated by a digital linear filter** (Guptasarma & Singh 1997). Inversion is done by **damped Levenberg–Marquardt** with a **Steepest-Descent** fallback.

**Out of scope** for v1: forward-only studies as a public API (the kernel is exposed but no CLI), exponential / vertical / sloped soil models, full F05/F09 file generation, ground-grid analysis (that's `engines/earthing/`).

**Consumer apps:** `apps/earthing/` (IEEE 80 grid analysis) consumes converged soil models via `self.engine("soil")`.

---

## 2. Background

A four-electrode resistivity sounding measures `R = V_P1P2 / I` at a series of electrode spacings `a`. The measured resistance is converted to **apparent resistivity** via a geometry-dependent factor:

$$\rho_a = K_g \cdot R$$

For a horizontally layered earth, ρ_a depends on the layer parameters in a non-trivial integral form (§6). The **inverse problem** is to recover the layers from the ρ_a curve.

The output feeds downstream substation-grounding computation per IEEE Std 80.

---

## 3. Reference Test Case (`RS_TUT1.F09`)

Used as the canonical validation vector for every release.

**Site:** East Central Substation
**Electrode configuration:** Wenner (4-pin, equal spacing)
**System of units:** Metric

**Measurements (5 points):**

| Point | a (m) | C1–C2 (m) | Apparent ρ (Ω·m) | Apparent R (Ω) |
|---|---|---|---|---|
| 1 | 2 | 6 | 190.0 | 15.1197 |
| 2 | 4 | 12 | 183.0 | 7.2813 |
| 3 | 8 | 24 | 147.0 | 2.9245 |
| 4 | 16 | 48 | 118.0 | 1.1738 |
| 5 | 32 | 96 | 107.0 | 0.5322 |

**Reference-converged 2-layer model:**

| Layer | ρ (Ω·m) | Thickness (m) | K (reflection coeff.) | ρ-contrast |
|---|---|---|---|---|
| 1 (top soil) | 190.0000 | 4.733190 | — | — |
| 2 (bottom) | 105.5163 | ∞ | **−0.28589** | **0.55535** |

**RMS error:** 1.8882 % &nbsp;&nbsp;&nbsp; **Average discrepancy:** 1.43 %

**Sanity check:**
K = (105.5163 − 190) / (105.5163 + 190) = **−0.28589** ✓
contrast = 105.5163 / 190 = **0.5553** ✓

**Implementation note.** The reference F09 reports the air half-space as "Infinite"; the originating GUI represents it internally as `1.0 × 10¹⁸` Ω·m. Our engine never feeds air into the recursion at all (see §6.2) — the air–top-soil interface K = −1 is reported by convention in post-processing only.

---

## 4. Functional Requirements

| ID | Requirement |
|---|---|
| FR-01 | Accept measurements as (spacing, apparent resistance) **or** (spacing, apparent resistivity) pairs, plus electrode geometry (current/potential probe positions). |
| FR-02 | Support **Wenner**, **Schlumberger**, **Dipole–Dipole**, and **general 4-electrode** arrays. Equal-spacing simplifications must auto-detect. |
| FR-03 | User selects **target number of layers** n (2 ≤ n ≤ 8). Default = 2. |
| FR-04 | User can **lock** any individual ρᵢ or hᵢ. Locked params are excluded from optimisation. |
| FR-05 | User can specify **initial estimates** or request **auto-estimate**. |
| FR-06 | User can choose **Steepest Descent** (robust) or **Levenberg–Marquardt** (faster, may give extreme values). Default: LM. |
| FR-07 | User can set **target RMS accuracy** (default 2.5 %), **max iterations** (default 500), **min step size** (default 1e-4 p.u.). |
| FR-08 | Engine returns: converged layer model, RMS error, average discrepancy, per-point discrepancy table, K and ρ-contrast at each interface. |
| FR-09 | Engine produces measured-vs-computed comparison at the user's spacings AND at a dense grid for plotting. |
| FR-10 | Diagnostics output split into three views: **Issues List** (warnings/errors), **Computation Trace** (iteration log), **Computation Results** (final model). |
| FR-11 | Each measurement row has an **active flag** so the user can include/exclude points without deleting them. The optimiser uses only active rows; inactive rows are still plotted as ghosted points. |
| FR-12 | Support **multiple traverses** in one project (combine, average, or analyse separately). |
| FR-13 | User can set **Upper / Lower Limit** envelopes on soil model parameters. These become bounds for LM. |
| FR-14 | User can toggle **"Account for electrode depth"** for probe-length corrections. *(deferred to v1.1)* |
| FR-15 | Reproduce `RS_TUT1.F09` results to within ±0.5 % on every layer parameter and within ±0.1 % on RMS. |

## 5. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-01 | Forward kernel evaluation < 5 ms per spacing for n ≤ 6 layers. |
| NFR-02 | Full inversion (5 measurements, 2 layers) < 1 second on a typical laptop. |
| NFR-03 | Numerically stable for ρ ratios up to 10⁶ and h ratios up to 10³. |
| NFR-04 | Deterministic — no random seed unless documented. |
| NFR-05 | Pure-function core; no global state. |
| NFR-06 | Cross-platform (Windows / macOS / Linux). |
| NFR-07 | SI units throughout — Ω·m for resistivity, m for length (imperial conversion only at I/O). |

---

## 6. Mathematical Foundations

### 6.1 Apparent resistivity for an n-layer horizontally stratified earth

For a four-electrode array on a horizontally layered earth surface,

$$\rho_a = K_g \cdot R = K_g \cdot \frac{V_{P1P2}}{I}$$

where the **geometric factor** is

$$K_g = \frac{2\pi}{\dfrac{1}{r_{C1P1}} - \dfrac{1}{r_{C1P2}} - \dfrac{1}{r_{C2P1}} + \dfrac{1}{r_{C2P2}}}$$

For **Wenner** with equal spacing a:  $K_g^{\text{Wenner}} = 2\pi a$, $\rho_a = 2\pi a R$.

For **Schlumberger** with current spacing 2L and potential spacing 2ℓ (L ≫ ℓ):  $K_g^{\text{Schl}} = \pi (L^2 - \ell^2)/(2\ell)$.

These assume **point electrodes on a half-space surface**. Sunde's probe-length correction is deferred to v1.1.

### 6.2 Kernel function (Stefanesco recursion)

The surface potential due to a unit point current source on layered earth:

$$V(r) = \frac{\rho_1 I}{2\pi} \int_0^\infty \left[1 + 2K(\lambda)\right] J_0(\lambda r)\,d\lambda$$

We use the **resistivity-transform form** (Slichter / Pekeris) for numerical robustness.

Start with $T_n(\lambda) = \rho_n$. For $i = n-1$ down to 1:

$$T_i(\lambda) = \frac{T_{i+1}(\lambda) + \rho_i \tanh(\lambda h_i)}{1 + \dfrac{T_{i+1}(\lambda)}{\rho_i} \tanh(\lambda h_i)}$$

Then $K(\lambda) = \tfrac{1}{2}\bigl(T_1(\lambda)/\rho_1 - 1\bigr)$.

**Implementation guards.**
1. When `λ·h_i > 50`, substitute `tanh(λ·h_i) = 1.0` to avoid `inf · 0` in the recursion.
2. The recursion takes only **soil** layers — air is *not* a layer in `rho` / `h`. The "air = 1×10¹⁸" convention from §3 is a *display* value used only when reporting K at the air–top interface (always −1 by construction). The recursion asserts `all(ρ_i < 1e12 for ρ_i in rho)`.

### 6.3 Apparent resistivity as a Hankel transform — general 4-electrode

Define the geometric sum

$$G = \frac{1}{r_{C1P1}} - \frac{1}{r_{C1P2}} - \frac{1}{r_{C2P1}} + \frac{1}{r_{C2P2}}, \qquad K_g = \frac{2\pi}{G}$$

Then

$$\rho_a = \rho_1 \left[ 1 + \frac{K_g}{\pi} \int_0^\infty K(\lambda)\bigl[J_0(\lambda r_{C1P1}) - J_0(\lambda r_{C1P2}) - J_0(\lambda r_{C2P1}) + J_0(\lambda r_{C2P2})\bigr]\,d\lambda \right]$$

The half-space term simplifies to $\rho_1$ exactly by the definition of $K_g$. We do *not* carry a redundant $K_g G/(2\pi)$ form — it's numerically equivalent but invites floating-point drift between the two halves.

For **Wenner** (a special case with C1, P1, P2, C2 at 0, a, 2a, 3a):

$$\rho_a^{\text{Wenner}}(a) = \rho_1\left[1 + 4a\int_0^\infty K(\lambda)\bigl[J_0(\lambda a) - J_0(2\lambda a)\bigr]\,d\lambda\right]$$

### 6.4 Hankel transform via digital linear filter

The integral above is oscillatory and not closed-form for n > 2. Standard practice (Ghosh 1971) is a **digital linear filter**:

$$\int_0^\infty f(\lambda)\,J_\nu(\lambda r)\,d\lambda \approx \frac{1}{r}\sum_{j=1}^{N_f} w_j \cdot f(\lambda_j),\qquad \lambda_j = \frac{e^{(j - j_0)\Delta x}}{r}$$

**Filter choice:**

| Filter | Points | Pts/decade | Accuracy |
|---|---|---|---|
| **Guptasarma & Singh 1997 (61-pt J₀ short)** — default | 61 | ~10 | ~10⁻⁵ |
| Guptasarma & Singh 1997 (120-pt J₀ long) — high-precision option | 120 | ~20 | ~10⁻⁷ |
| Anderson 1979 (lagged 801-pt) — what RESAP uses | up to 801 | varies | ~10⁻⁶ |
| Key 2009 (201-pt J₀) — alternative | 201 | ~30 | ~10⁻⁸ |

We ship Guptasarma & Singh 61-pt J₀ as default and expose 120-pt as a "high-precision" mode. Coefficients live in `engines/soil/data/gupta_singh_*.txt`, loaded once at module import.

### 6.5 Reflection coefficient and contrast (auxiliary outputs)

At each interface i (between layer i and i+1):

$$K_i = \frac{\rho_{i+1} - \rho_i}{\rho_{i+1} + \rho_i} \in [-1, +1] \qquad \text{Contrast}_i = \frac{\rho_{i+1}}{\rho_i}$$

Direct post-processing of the converged ρ vector. The air–top-soil interface is reported as K₀ = −1 by convention (matching the reference F09).

### 6.6 The inverse problem

Given measurement vector **m**, geometry vector **g**, layer count n, and free parameter vector **p** = (ρ₁,…,ρₙ, h₁,…,hₙ₋₁) of length 2n − 1, find **p\*** that minimises:

$$F(\mathbf{p}) = \sqrt{\frac{1}{M}\sum_{i=1}^M \left(\frac{\rho_a^{\text{forward}}(g_i;\mathbf{p}) - m_i}{m_i}\right)^2}$$

Locked parameters are removed from **p**, reducing dimension. Bounds (ρ > 0, h > 0) are enforced via log-transformation.

**Weighting.** v1 uses unweighted relative residuals. The reference RESAP additionally down-weights points with high apparent uncertainty (large spacings, low SNR). v1.x adds a `weights` column to the input CSV (defaulting to 1.0); the residual becomes `r_i ← w_i · (forward_i − m_i)/m_i`.

### 6.7 Why log-transformation of parameters helps

Resistivities and thicknesses span many orders of magnitude (10 Ω·m wet clay vs 10⁴ Ω·m bedrock). Optimising on **log(ρ)** and **log(h)**:

1. Enforces positivity automatically.
2. Equal-relative-step behaviour across the parameter space.
3. Better-conditioned Jacobian for LM.

---

## 7. Algorithms

### 7.1 Forward solver (single ρ_a evaluation)

```
function forward_apparent_resistivity(geometry, soil_model):
    rho = soil_model.resistivities       # length n  (NEVER includes air)
    h   = soil_model.thicknesses         # length n-1
    Kg  = geometric_factor(geometry)

    acc = 0.0
    for (sign, r) in geometry.electrode_pairs():
        partial = 0.0
        for j in 1..N_filter:
            λ = exp((j - j0) * Δx) / r
            T = stefanesco_recursion(λ, rho, h)     # T_1(λ)
            K = 0.5 * (T / rho[0] - 1.0)            # kernel
            partial += w_j * K
        partial /= r                                 # filter normalisation
        acc += sign * (1.0/r + 2.0 * partial)

    return Kg * rho[0] * acc / (2.0 * pi)
```

**Sanity check.** Uniform soil ⇒ K(λ) = 0 ⇒ acc = G ⇒ result = K_g · ρ₁ · G/(2π) = ρ₁. ✓

**Stefanesco recursion (numerically safe form):**

```
function stefanesco_recursion(λ, rho, h):
    n = len(rho)
    T = rho[n-1]                          # bottom layer
    for i = n-2 down to 0:
        x = λ * h[i]
        if x > 50.0:
            t = 1.0                       # tanh saturated
        else:
            t = tanh(x)
        T = (T + rho[i] * t) / (1.0 + (T / rho[i]) * t)
    return T
```

### 7.2 Initial estimate engine

1. **Surface ρ:** ρ₁ ≈ ρ_a at the smallest spacing.
2. **Asymptotic deep ρ:** ρ_n ≈ ρ_a at the largest spacing (or extrapolated trend).
3. **Layer count.** Default to user-specified `n_layers`. Auto-suggestion is offered only as a hint:
   - Strictly monotonic ρ_a(a) on log–log → suggest n = 2.
   - One interior extremum (minimum = K-type, maximum = H-type) → suggest n = 3.
   - Two extrema → suggest n = 4.

   This rule counts *extrema*, not inflection points, and is heuristic only — the reference RESAP makes the user choose `n` and so does this engine. Auto-suggestion never overrides `InversionConfig.n_layers`.
4. **Thickness seed.** Use the spacing at which ρ_a is at the geometric mean of (ρ₁, ρ_n) as the order of magnitude of the dominant layer thickness.
5. **Multi-layer.** Divide the ρ_a curve into segments per extremum, assign each segment a candidate layer.

### 7.3 Optimisation engines

#### 7.3.1 Levenberg–Marquardt (primary)

Use `scipy.optimize.least_squares(..., method='trf')` with:

- Residuals: `r_i = (forward_i − m_i) / m_i` (per-point relative).
- Variables: `x = [log(ρ_1)…log(ρ_n), log(h_1)…log(h_{n-1})]`.
- Bounds: `[log(ρ_min), log(ρ_max)]` and `[log(h_min), log(h_max)]`, where `(ρ_min, ρ_max)` and `(h_min, h_max)` come verbatim from `InversionConfig` — defaults `(1.0, 1e6) Ω·m` and `(1e-2, 1e3) m`. Lower ρ bound is 1.0 Ω·m (not 0.01) — physically meaningful for substation soils, avoids the optimiser exploring sea-water values for noise.
- Jacobian: `'2-point'` finite-difference initially; switch to analytic via the kernel's parameter derivatives once profiling shows it matters.
- Tolerances: `ftol = xtol = gtol = 1e-8`, `max_nfev = 500 * (2n − 1)`.

#### 7.3.2 Steepest Descent (fallback / parity with reference)

```
loop until converged:
    g = ∇F(p)
    g_normalised = g / |g_max_component|
    p_new = p - α * g_normalised
    if oscillation_detected: halve α
    if rms_change < min_step_size: stop
    if iter > max_iter: stop
    if rms < target_accuracy: stop
```

Slower but more stable for poorly-conditioned problems.

#### 7.3.3 Multi-start & global fallback

For problematic data, run LM from `multi_start` randomised starting points sampled by **log-uniform Latin-Hypercube** within the same bounds used by LM (linear-space LHS over `(1, 1e6)` wastes 99 % of samples on bedrock-like values). Keep the lowest-RMS result.

Global fallback `scipy.optimize.differential_evolution` is offered only when `n ≤ 4` — wall-clock cost grows superlinearly past that, and degeneracy at n ≥ 5 is better addressed by regularisation (v1.2) than by global search.

### 7.4 Parameter locking

A boolean mask aligned with the parameter vector. Locked entries are sliced out of the optimisation variable vector and re-inserted at every forward call.

### 7.5 Convergence criteria

Stop when **any** of:

- **Steepest-Descent path only:** relative RMS change over last 25 iterations < `step_size_threshold` (default 1e-4). The LM path uses LM's own `ftol`/`xtol`/`gtol` and does not need this duplicate criterion.
- Achieved RMS < `target_accuracy` (default 2.5 %).
- Iteration count ≥ `max_iter` (default 500).
- LM internal `ftol`/`xtol`/`gtol` triggered.

Report which criterion fired.

---

## 8. Software Architecture

### 8.1 Module breakdown

```
engines/soil/
├── __init__.py
├── DESIGN.md             # this document
├── soil_model.py         # SoilModel dataclass, K, contrast
├── geometry.py           # ElectrodeArray, geometric factors, electrode_pairs()
├── filters.py            # DLF coefficients (Guptasarma & Singh, optionally others)
├── kernel.py             # Stefanesco recursion
├── forward.py            # forward apparent-resistivity solver
├── initial.py            # auto initial-estimate engine               (v0.3)
├── inverse.py            # LM, Steepest Descent, multi-start          (v0.4)
├── diagnostics.py        # RMS, discrepancy, K, contrast, warnings    (v0.5)
├── data/
│   ├── gupta_singh_61_j0.txt       # filter coefficients (TODO: load)
│   └── gupta_singh_120_j0.txt      # high-precision option
└── tests/
    ├── test_soil_model.py
    ├── test_kernel.py
    ├── test_forward_uniform.py     # works without filter coefficients
    ├── test_forward_two_layer.py   # vs Sunde closed-form              (v0.3)
    └── test_inverse_reference.py   # vs RS_TUT1.F09                    (v0.4)
```

### 8.2 Core data structures

```python
@dataclass(frozen=True)
class SoilModel:
    resistivities: tuple[float, ...]      # length n, all > 0
    thicknesses:   tuple[float, ...]      # length n-1, all > 0

    def n_layers(self) -> int: ...
    def reflection_coefficients(self) -> tuple[float, ...]: ...
    def contrast_ratios(self) -> tuple[float, ...]: ...

@dataclass(frozen=True)
class ElectrodeArray:
    kind: Literal["wenner", "schlumberger", "dipole_dipole", "general"]
    spacings: tuple[float, ...]          # interpretation depends on `kind`

@dataclass(frozen=True)
class Measurement:
    array: ElectrodeArray
    apparent_resistance: float | None    # one of these two must be set
    apparent_resistivity: float | None
    active: bool = True                   # FR-11
    comment: str = ""

@dataclass(frozen=True)
class InversionConfig:
    n_layers: int
    method: Literal["lm", "steepest_descent"]
    max_iter: int = 500
    target_accuracy_pct: float = 2.5
    step_size_threshold: float = 1e-4
    locked_resistivities: tuple[bool, ...] | None = None
    locked_thicknesses:   tuple[bool, ...] | None = None
    initial_model: SoilModel | None = None      # None ⇒ auto-estimate
    bounds_resistivity: tuple[float, float] = (1.0, 1e6)
    bounds_thickness:   tuple[float, float] = (1e-2, 1e3)
    multi_start: int = 1                          # > 1 ⇒ multi-start

@dataclass(frozen=True)
class InversionResult:
    soil_model: SoilModel
    rms_error_pct: float
    average_discrepancy_pct: float
    per_point_discrepancy_pct: tuple[float, ...]
    iterations: int
    convergence_reason: str
    warnings: tuple[str, ...]
```

### 8.3 Public API

```python
def forward(model: SoilModel, array: ElectrodeArray) -> float | NDArray: ...
def invert(measurements: Sequence[Measurement], config: InversionConfig) -> InversionResult: ...
```

---

## 9. Validation Plan

### 9.1 Unit tests

| Test | What it checks |
|---|---|
| `test_soil_model.test_K_two_layer` | K = (ρ₂−ρ₁)/(ρ₂+ρ₁) on the supplied case |
| `test_kernel.test_uniform_soil` | K(λ) = 0 for ρ₁ = ρ₂ |
| `test_kernel.test_high_contrast_limits` | K(λ) → ±1 as ρ₂/ρ₁ → ∞ or 0 (after `λh` → 0 limit) |
| `test_forward_uniform.test_returns_rho` | ρ_a(any a) = ρ for uniform soil (works without filter coefficients) |
| `test_forward_two_layer.test_vs_sunde` | Wenner closed-form Sunde series — 2-layer case |

### 9.2 Integration test (ship-blocker)

`test_inverse_reference.py` against §3 must produce:

| Quantity | Target | Tolerance |
|---|---|---|
| ρ₁ (top layer) | 190.0000 Ω·m | ±0.5 % |
| ρ₂ (bottom)    | 105.5163 Ω·m | ±0.5 % |
| h₁              | 4.733190 m   | ±1.0 % |
| K (interface)   | −0.28589    | ±0.001 |
| Contrast ratio  | 0.55535     | ±0.001 |
| RMS error       | 1.8882 %    | ±0.10 % |
| Avg discrepancy | 1.43 %      | ±0.10 % |

Per-point ρ_calc targets must be **lifted verbatim from `RS_TUT1.F09`** before this table is treated as a regression target — they are not transcribed here to avoid confusion with indicative numbers.

### 9.3 Equivalence diagnostic

Resistivity inversion has a known non-uniqueness (Maillet 1947). After convergence, compute the Jacobian `J = ∂r_i/∂x_j` at the optimum (free — LM produced it on its last step). Take its singular values `σ_1 ≥ … ≥ σ_p`. If `σ_1/σ_p > 100`, flag as ill-conditioned and report which right-singular vector(s) correspond to the small `σ` — these name the unresolved parameter combinations (typically `ρ·h` for thin conductive layers).

---

## 10. Edge Cases and Error Handling

| Case | Behaviour |
|---|---|
| Fewer measurements than free parameters (M < 2n−1) | Refuse: "under-determined; reduce layers or add measurements". |
| Negative or zero apparent resistivity in input | Reject row with explicit error, list the offending row. |
| Initial ρ or h at exactly zero | Auto-replace with 1e-6 of the geometric mean of valid values + warning. |
| LM fails to converge in 500 iter | Auto-fall-back to Steepest Descent + multi-start; if still failing, return best-so-far with `convergence_reason = "max_iter_no_convergence"`. |
| Resistivities span > 6 orders of magnitude in converged model | Warn "extreme resistivity values; cross-check with alternative method". |
| Shortest spacing > 1.2 m | Warn "shortest spacing may be too large to resolve a reliable surface layer". |
| Two adjacent measurement spacings are identical | Treat as one duplicate and warn. |
| User locks **all** parameters | Skip optimiser; just compute forward + RMS. |

---

## 11. Tech Stack

| Component | Choice | Rationale |
|---|---|---|
| Language | **Python 3.11+** | matches EmptyOS engines/* |
| Numerics | NumPy, SciPy | LM solver, Bessel functions, vectorised filter evaluation |
| Optional plotting | matplotlib | downstream consumer (apps/soil/) renders log–log curves |
| Testing | `pytest` | matches `tests/` convention |

**Build vs buy.** `pyGIMLi` and `SimPEG` both ship horizontal-layer DC inversion with the same Hankel-DLF stack. We reimplement from scratch because:
- The validation contract is bit-for-bit reproduction of reference F09 outputs — easier with no third-party numerics in the path.
- Zero heavy dependencies (just NumPy + SciPy) keeps EmptyOS lean.
- Deterministic reference behaviour for QA.

If either upstream library exposes a stable, deterministic, bounds-aware `invert_dc_1d()` in the future, revisit.

---

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Filter coefficient transcription errors | Use a published, peer-reviewed table (Guptasarma & Singh 1997, Geophysics 62(3), 1997). Unit-test against analytic 2-layer cases. |
| R2 | Non-uniqueness of inversion | Multi-start + Jacobian SVD diagnostic + explicit user warning. |
| R3 | LM convergence to local minimum | Multi-start (log-LHS) + Steepest-Descent fallback. DE global search only for `n ≤ 4`; for `n ≥ 5`, escalate to v1.2 regularisation, not brute force. |
| R4 | Apparent resistance vs apparent resistivity input mode confusion | Strict CSV schema validation; never silently convert. |
| R5 | Probe-length effect ignored in v1 | Real reference feature; ship checkbox visible-but-disabled in v1, implement Sunde correction in v1.1. |
| R6 | Unit confusion (metric vs imperial) | Internal SI throughout; conversion is a thin I/O wrapper. |
| R7 | Sharp local maxima (K-type / H-type curves) | Initial-estimate engine counts both up and down extrema. |
| R8 | Per-point uncertainty / weighting absent in v1 | Document the equal-weight assumption in v1 output. Add `weight` column to CSV in v1.x. |

---

## 13. Roadmap

| Version | Scope |
|---|---|
| **0.2 (this)** | Architecture + math + scaffolded `soil_model.py`, `kernel.py`, `forward.py` (uniform-soil sanity test passes). Filter coefficients placeholder. |
| **0.3** | Real Guptasarma–Singh 61-pt coefficients loaded; 2-layer forward test vs Sunde series; auto initial-estimate engine; Steepest Descent. |
| **0.4** | Levenberg–Marquardt + parameter locking + multi-start. `RS_TUT1.F09` integration test passes. |
| **0.5** | CSV I/O + diagnostics + warnings + Jacobian SVD condition number. |
| **1.0** | Reference-case passes all tolerances; user docs; integrated with `apps/earthing/` via `self.engine("soil")`. |
| **1.1** | Probe-length corrections (Sunde). |
| **1.2** | Plotting + Tikhonov regularisation for n ≥ 5 + per-point weighting. |
| **2.0** | F05 round-trip generator (optional QA aid). |

---

## Appendix A — Filter Coefficients

The Guptasarma & Singh (1997) 61-point J₀ short filter is uniquely defined by:

- abscissa shift `a = -5.0825`
- spacing `s = 0.142687`
- 61 weights w₁…w₆₁

These are tabulated in the paper's Appendix and reproduced in dozens of open-source resistivity codes (`pyGIMLi`, `resipy`, `SimPEG`). They will live as a Python tuple in `engines/soil/data/gupta_singh_61_j0.txt`, loaded once at `filters` module import. Until that file is populated, `filters.j0_filter()` raises `NotImplementedError` with instructions.

The 120-point variant (`a = -8.388`, `s = 0.0901`) ships as a high-precision option.

---

## Appendix B — Glossary

| Term | Meaning |
|---|---|
| **a** | Wenner electrode spacing (m) |
| **DLF** | Digital Linear Filter — Hankel transform via convolution sum |
| **Discrepancy (Di)** | Per-point relative error between measured and computed ρ_a, in % |
| **Geometric factor (K_g)** | Multiplier converting measured resistance to apparent resistivity |
| **K (reflection coefficient)** | (ρ₂−ρ₁)/(ρ₂+ρ₁) at an interface, range [−1, +1] |
| **Levenberg–Marquardt** | Damped Gauss-Newton non-linear least-squares method |
| **p.u.** | Per-unit (dimensionless / fractional) |
| **RMS error** | √(mean of squared per-point discrepancies) |
| **Stefanesco recursion** | Bottom-up evaluation of the resistivity-transform kernel for layered earth |
| **Steepest Descent** | First-order gradient method |
| **Wenner / Schlumberger / Dipole-Dipole** | Standard 4-electrode array configurations |

---

## References

1. **IEEE Std 80-2013** — *IEEE Guide for Safety in AC Substation Grounding*.
2. **IEEE Std 81** — *Guide for Measuring Earth Resistivity, Ground Impedance, and Earth Surface Potentials*.
3. **Sunde, E.D.** (1949) *Earth Conduction Effects in Transmission Systems*, Dover.
4. **Stefanesco, S., Schlumberger, C. & M.** (1930) "Sur la distribution électrique potentielle autour d'une prise de terre ponctuelle…", *J. de Physique et le Radium*.
5. **Ghosh, D.P.** (1971) "The application of linear filter theory to the direct interpretation of geoelectrical resistivity sounding measurements", *Geophys. Prospecting* 19, 192–217.
6. **Anderson, W.L.** (1979) "Numerical integration of related Hankel transforms…", *Geophysics* 44, 1287–1305.
7. **Guptasarma, D. & Singh, B.** (1997) "New digital linear filters for Hankel J₀ and J₁ transforms", *Geophys. Prospecting* 45, 745–762.
8. **Koefoed, O.** (1979) *Geosounding Principles, 1: Resistivity Sounding Measurements*, Elsevier.
9. **Maillet, R.** (1947) "The fundamental equations of electrical prospecting", *Geophysics* 12(4).
10. **Dawalibi, F. & Barbeito, N.** (1991) "Measurements and computations of the performance of grounding systems buried in multilayer soils", *IEEE Trans. PWRD* 6(4).
11. **SES Ltd.** — *RESAP User's Manual*, CDEGS v20 (2025) — referenced as the canonical numerical-validation target.
