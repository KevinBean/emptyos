# Algorithm — three-phase prospective fault current at MV/LV transformer terminals

Stage 2 of the trust loop. This is the specification the engine is written
against and the gate is written from, produced *before* the code: an
implementation with no spec has nothing to be checked against, and "it looks
right" is not a check.

**Source.** ABB *Technical Application Paper No. 2* — MV/LV transformer
substations (document `1SDC007101G0202`), clause 2.2 and its worked example.
Freely downloadable, no login:
[library.e.abb.com — 1SDC007101G0202.pdf](https://library.e.abb.com/public/2c522f583c884a4fbdf3968e1fdf1481/1SDC007101G0202.pdf)
The method follows IEC 60909; the paper is the citable, openly-published
carrier of the worked example, which is what a gate needs.

Implemented in: `fault_current.py` · Gated by: `[[provides.conformance.fault]]`

---

## 1. Scope

A fault at the low-voltage terminals of an MV/LV transformer is fed through
a chain of impedances: the utility network, the MV cable, the transformer
itself, and the LV cable. Each is referred to the LV base, summed, and the
current follows from Ohm's law with a voltage factor.

This computes **how much current that fault draws**. It does not decide
whether anything survives it — that is a different calculation entirely.

## 2. Inputs

| Symbol | Meaning | Units |
|---|---|---|
| `U_net` | supply network nominal voltage | V |
| `I_k_net` | network fault current (or `S_k_net`, converted) | A |
| `c` | voltage factor | — |
| `R_mv`, `X_mv` | MV cable, at MV potential | Ω |
| `S_n` | transformer rated power | VA |
| `U_2n` | transformer secondary rated voltage | V |
| `vk_pct` | transformer voltage drop on test | % |
| `pk_pct` | transformer load loss on test | % |
| `R_lv`, `X_lv` | LV cable, at LV potential | Ω |

## 3. Outputs

| Symbol | Meaning | Units |
|---|---|---|
| `i_k3_a` | three-phase prospective fault current | A |
| `r_total`, `x_total`, `z_total` | summed impedance at the LV base | Ω |
| `contributions` | per-element R and X at the LV base, and share of `z_total` | Ω, % |
| `ratio` | referral ratio `K = U_net / U_2n` | — |

## 4. Method

### 4.1 Referral

Everything upstream of the transformer is expressed at the LV base through

```
K = U_net / U_2n
Z_LV = Z_MV / K²
```

### 4.2 Network

```
Z_Q = c · U_net / (√3 · I_k_net)
X_Q = 0.995 · Z_Q
R_Q = 0.1 · X_Q
```

Both splits are stated by the source. `R_Q` is **not** derived as
`√(Z² − X²)`: that identity looks equivalent and gives `0.0999·Z` rather
than `0.0995·Z`. The difference never reaches the answer, but a method
that claims to follow a source should follow what the source says.

**A note on which form the paper actually uses.** The paper prints *two*
expressions for the network impedance — the current form above, and a power
form `Z = c²U²/S″_k` for when the fault level is known as apparent power.
They carry different powers of `c`, so **they disagree by a factor of `c`**
even when the two datasets describe the same network. In this example the
stated `S″_k` (500 MVA) and the stated `I″_k` (14.4 kA) agree to 0.2 %
(√3·20 kV·14.4 kA = 498.8 MVA), yet the impedances differ by 9.7 %:
0.882 Ω against 0.968 Ω.

The worked example uses the current form. Implementing the power form
instead overstates the network impedance by ten percent and reproduces
neither the paper's intermediate value nor its final answer. Recorded here
because it is exactly the kind of discrepancy a worked example exposes and
prose does not — the reason § 6.1 anchors on a case rather than on the
equations alone.

### 4.3 Transformer

```
Z_T = (U_2n² · vk_pct) / (100 · S_n)
P_T = (pk_pct · S_n) / 100          (load loss)
I_2n = S_n / (√3 · U_2n)
R_T = P_T / (3 · I_2n²)
X_T = √(Z_T² − R_T²)
```

### 4.4 Sum and solve

```
R_k = ΣR      X_k = ΣX      Z_k = √(R_k² + X_k²)
I″_k3 = c · U_2n / (√3 · Z_k)
```

## 5. Applicability limits

Stated because a calculator that hides its limits is worse than one that
refuses. These are the paper's simplified method, not a full current-edition
IEC 60909 study. Specifically **not** included:

- the IEC 60909-0 transformer correction factor `K_T`
- motor contribution to the fault
- unbalanced faults (line-to-line, line-to-earth)
- alternative operating topologies, or minimum-fault conditions
- the peak asymmetrical value `i_p` (a separate clause of the paper)

Consequence: this reproduces the paper's arithmetic exactly, and a real
equipment-duty assessment approximately. Use it as a regression case for the
method, not as a final duty result. Domain guards: every impedance must be
non-negative, `S_n`, `U_2n`, `U_net`, `I_k_net` and `vk_pct` strictly
positive, and `R_T ≤ Z_T` (otherwise `X_T` is imaginary and the transformer
test data is inconsistent).

## 6. Acceptance targets

### 6.1 The published case (the anchor)

ABB TAP No. 2, clause 2.2 worked example — published inputs:

| Quantity | Value |
|---|---|
| Supply network | 20 kV, 500 MVA / 14.4 kA |
| Voltage factor | c = 1.1 |
| MV cable | R = 360 mΩ, X = 335 mΩ |
| Transformer | 400 kVA, 20/0.4 kV, vk 4 %, pk 3 % |
| LV cable, 5 m | R = 0.388 mΩ, X = 0.395 mΩ |

**Published result: `I_k3 = 14 943 A = 14.95 kA`.**

The engine must reproduce it within **0.5 %**. The band is not guesswork —
the residual is fully explained. The paper prints its total impedance
rounded to two significant figures, `Z_Tk = 0.017 Ω`, and divides with that
rounded value:

```
1.1 · 400 / (√3 · 0.017)        = 14 943 A     ← the printed answer
1.1 · 400 / (√3 · 0.01700992)   = 14 934.6 A   ← full precision
```

So an implementation carrying full precision *must* land ~0.06 % below the
printed figure, and one that hits 14 943 A exactly is either rounding its
own intermediates to match or has an error that happens to cancel. The
engine's remaining intermediates agree with every printed value:

| Quantity | Printed | Engine |
|---|---|---|
| `R_Tk` | 0.01256 Ω | 0.0125671 Ω |
| `X_Tk` | 0.01147 Ω | 0.0114631 Ω |
| `Z_Tk` | 0.017 Ω | 0.0170098 Ω |
| `I_2n` | 577 A | 577.35 A |
| `Z_T` | 0.016 Ω | 0.016 Ω |

A tolerance tighter than the source's own rounding would fail correct code;
one much looser would pass wrong code.

### 6.2 Structural checks (hold for every input, not one point)

| Property | Why it must hold |
|---|---|
| **Referral consistency** | referring an impedance by `K²` and back must return it unchanged |
| **Dominance** | the transformer is the largest term in this topology — its share must exceed every other |
| **Monotonicity** | more impedance anywhere must never increase the current |
| **Voltage-factor linearity** | `I` scales linearly with `c` |
| **Pythagoras** | `Z_k² = R_k² + X_k²` for the summed values |

### 6.3 Refusals

Each limit in § 5 is checked both ways: a legal input is accepted, an illegal
one is refused with a reason.

## 7. What this does not buy

Passing means the arithmetic matches a published example for one topology.
It says nothing about whether the network data is right, whether the
simplifications in § 5 are acceptable for your study, or whether the answer
should be used for equipment duty — which is where the difficulty actually
lives.
