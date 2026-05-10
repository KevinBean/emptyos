"""Element library — Dommel companion-circuit primitives.

See `[[dommel-companion-model]]` (vault: 30_Resources/KB/power-systems/formulas/)
once written. Reference: H.W. Dommel, "Digital Computer Solution of
Electromagnetic Transients in Single- and Multiphase Networks", IEEE Trans
PAS, 1969.

Trapezoidal integration → each storage element becomes a conductance in
parallel with a history current source updated each timestep.

Convention used here: nodes are integer indices into a Y matrix; node index
0 is reserved for the ground reference (its row/column are dropped before
solve). Each element implements `stamp(Y, n_nodes)` (sparse-matrix builder
contributions) and `history_current(self) -> np.ndarray of shape (n_nodes,)`
returning the per-step Norton current injected at each node by this element's
trapezoidal history term.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ── Sparse-matrix builder helper ─────────────────────────────────────


@dataclass
class StampAccumulator:
    """Accumulates (row, col, value) triplets for sparse Y matrix assembly."""
    rows: list[int] = field(default_factory=list)
    cols: list[int] = field(default_factory=list)
    vals: list[complex] = field(default_factory=list)

    def add(self, row: int, col: int, val: complex):
        self.rows.append(row)
        self.cols.append(col)
        self.vals.append(val)

    def add_2port(self, n_pos: int, n_neg: int, y: complex):
        """Stamp a 2-port admittance y between nodes n_pos and n_neg."""
        self.add(n_pos, n_pos, y)
        self.add(n_neg, n_neg, y)
        self.add(n_pos, n_neg, -y)
        self.add(n_neg, n_pos, -y)


# ── Elements ─────────────────────────────────────────────────────────


class Element:
    """Base — every element subclasses this. Subclasses must define `id`."""

    def stamp(self, acc: StampAccumulator) -> None:
        raise NotImplementedError

    def init_history(self, n_nodes: int) -> None:
        """Allocate per-element history vectors."""

    def update_history(self, v_node: np.ndarray) -> None:
        """Called each timestep AFTER solve; update internal history state."""

    def history_inject(self, i_h: np.ndarray) -> None:
        """Add this element's Norton current contribution to i_h (length n_nodes)."""

    def source_inject(self, i_h: np.ndarray, t: float) -> None:
        """Add this element's source-current contribution at time t."""


@dataclass
class Resistor(Element):
    id: str
    n_pos: int
    n_neg: int
    r: float

    def stamp(self, acc: StampAccumulator) -> None:
        if self.r <= 0:
            raise ValueError(f"Resistor {self.id} R must be positive (got {self.r})")
        acc.add_2port(self.n_pos, self.n_neg, complex(1.0 / self.r))


@dataclass
class Inductor(Element):
    """Single-port inductor. For coupled conductors use MutualInductorBlock."""
    id: str
    n_pos: int
    n_neg: int
    L: float
    dt: float = 0.0  # set by stepper before init_history
    _i_prev: float = 0.0  # branch current at previous step (pos -> neg)
    _v_prev: float = 0.0  # branch voltage at previous step (pos - neg)

    def _G(self) -> float:
        return self.dt / (2.0 * self.L)

    def stamp(self, acc: StampAccumulator) -> None:
        if self.L <= 0:
            raise ValueError(f"Inductor {self.id} L must be positive (got {self.L})")
        if self.dt <= 0:
            raise RuntimeError(f"Inductor {self.id}: dt not set (stepper bug)")
        acc.add_2port(self.n_pos, self.n_neg, complex(self._G()))

    def history_inject(self, i_h: np.ndarray) -> None:
        # Companion form: i(t) = G·v(t) + I_h, with I_h = i(t-Δt) + G·v(t-Δt).
        # Move I_h to the RHS of KCL: leaves n_pos as i_h[n_pos] -= I_h, enters n_neg.
        ih = self._i_prev + self._G() * self._v_prev
        i_h[self.n_pos] -= ih
        i_h[self.n_neg] += ih

    def update_history(self, v_node: np.ndarray) -> None:
        v_now = float(v_node[self.n_pos].real - v_node[self.n_neg].real)
        ih_old = self._i_prev + self._G() * self._v_prev
        i_now = self._G() * v_now + ih_old
        self._i_prev = i_now
        self._v_prev = v_now


@dataclass
class Capacitor(Element):
    id: str
    n_pos: int
    n_neg: int
    C: float
    dt: float = 0.0
    _i_prev: float = 0.0
    _v_prev: float = 0.0

    def _G(self) -> float:
        return 2.0 * self.C / self.dt

    def stamp(self, acc: StampAccumulator) -> None:
        if self.C <= 0:
            raise ValueError(f"Capacitor {self.id} C must be positive (got {self.C})")
        if self.dt <= 0:
            raise RuntimeError(f"Capacitor {self.id}: dt not set")
        acc.add_2port(self.n_pos, self.n_neg, complex(self._G()))

    def history_inject(self, i_h: np.ndarray) -> None:
        # Companion: i(t) = G·v(t) + I_h, with I_h = -i(t-Δt) - G·v(t-Δt).
        # Same RHS convention as L: leaves n_pos, enters n_neg.
        ih = -(self._i_prev + self._G() * self._v_prev)
        i_h[self.n_pos] -= ih
        i_h[self.n_neg] += ih

    def update_history(self, v_node: np.ndarray) -> None:
        v_now = float(v_node[self.n_pos].real - v_node[self.n_neg].real)
        ih_old = -(self._i_prev + self._G() * self._v_prev)
        i_now = self._G() * v_now + ih_old
        self._i_prev = i_now
        self._v_prev = v_now


@dataclass
class MutualInductorBlock(Element):
    """k-port mutually-coupled inductor block from Carson n×n L.

    Branch equation for k coupled conductors:
        v_k(t) = Σ_j L[k,j] · di_j/dt
    Trapezoidal companion: v(t) = (2L/Δt)(i(t) - i(t-Δt)) - v(t-Δt)
        ⇒ i(t) = G·v(t) + ih,  G = (Δt/2)·L^-1,  ih = i(t-Δt) + G·v(t-Δt)

    Stamps as a k×k admittance block between (n_pos[i], n_neg[i]) ports.
    Pre-inverts L once at init.
    """

    id: str
    n_pos: list[int]    # length k
    n_neg: list[int]    # length k
    L: np.ndarray       # shape (k, k), real symmetric positive-definite
    dt: float = 0.0
    _G: np.ndarray = field(default=None, repr=False)  # (Δt/2)·L^-1
    _i_prev: np.ndarray = field(default=None, repr=False)  # length k
    _v_prev: np.ndarray = field(default=None, repr=False)  # length k

    def __post_init__(self):
        self.L = np.asarray(self.L, dtype=float)
        k = self.L.shape[0]
        if self.L.shape != (k, k):
            raise ValueError(f"MutualInductorBlock {self.id}: L must be square")
        if len(self.n_pos) != k or len(self.n_neg) != k:
            raise ValueError(f"MutualInductorBlock {self.id}: n_pos/n_neg length mismatch")

    def _ensure_G(self) -> None:
        if self.dt <= 0:
            raise RuntimeError(f"MutualInductorBlock {self.id}: dt not set")
        if self._G is None or getattr(self, "_dt_at_invert", -1) != self.dt:
            self._G = (self.dt / 2.0) * np.linalg.inv(self.L)
            self._dt_at_invert = self.dt
        if self._i_prev is None:
            self._i_prev = np.zeros(self.L.shape[0])
            self._v_prev = np.zeros(self.L.shape[0])

    def stamp(self, acc: StampAccumulator) -> None:
        self._ensure_G()
        k = self.L.shape[0]
        # Each (i,j) entry of G adds an admittance between port i and port j
        # Diagonal: G[i,i] is the i-th port self-conductance (between n_pos[i] and n_neg[i]).
        # Off-diagonal: G[i,j] couples port i and port j.
        # Stamp: for ports modeled as (a_i, b_i) = (n_pos[i], n_neg[i]),
        #   I_a_i = Σ_j G[i,j] · (V_a_j - V_b_j)
        # → contributes to Y as +G[i,j] at (a_i, a_j), -G[i,j] at (a_i, b_j),
        #                    -G[i,j] at (b_i, a_j), +G[i,j] at (b_i, b_j).
        for i in range(k):
            ai, bi = self.n_pos[i], self.n_neg[i]
            for j in range(k):
                aj, bj = self.n_pos[j], self.n_neg[j]
                g = complex(self._G[i, j])
                acc.add(ai, aj, g)
                acc.add(ai, bj, -g)
                acc.add(bi, aj, -g)
                acc.add(bi, bj, g)

    def history_inject(self, i_h: np.ndarray) -> None:
        self._ensure_G()
        # Per-port companion: i_branch_i(t) = Σ_j G[i,j]·v_j(t) + I_h_i,
        # I_h_i = i_prev_i + Σ_j G[i,j]·v_prev_j. Same KCL-RHS sign convention
        # as the 2-port L/C — leaves n_pos[i], enters n_neg[i].
        ih = self._i_prev + self._G @ self._v_prev
        for i in range(self.L.shape[0]):
            i_h[self.n_pos[i]] -= ih[i]
            i_h[self.n_neg[i]] += ih[i]

    def update_history(self, v_node: np.ndarray) -> None:
        self._ensure_G()
        k = self.L.shape[0]
        v_now = np.array([
            float(v_node[self.n_pos[i]].real - v_node[self.n_neg[i]].real)
            for i in range(k)
        ])
        ih_old = self._i_prev + self._G @ self._v_prev
        i_now = self._G @ v_now + ih_old
        self._i_prev = i_now
        self._v_prev = v_now


@dataclass
class IdealSwitch(Element):
    """Ideal switch — closed = 0 Ω, open = 10⁹ Ω. Closes at t_close (one-shot)."""
    id: str
    n_pos: int
    n_neg: int
    t_close: float = float("inf")  # +inf = stays open forever
    initially_closed: bool = False
    _closed: bool = False

    OPEN_R: float = 1e9
    CLOSED_R: float = 1e-6

    def is_closed_at(self, t: float) -> bool:
        return self.initially_closed or t >= self.t_close

    def stamp(self, acc: StampAccumulator) -> None:
        # Stepper passes current state via _closed before stamp
        r = self.CLOSED_R if self._closed else self.OPEN_R
        acc.add_2port(self.n_pos, self.n_neg, complex(1.0 / r))


@dataclass
class VSourceSinusoidal(Element):
    """Sinusoidal voltage source. Modeled as Thevenin: V_phasor in series with
    R_int (default 1e-3 Ω). Implementation: insert internal node, R_int between
    n_pos and internal, ideal V between internal and n_neg as a current source
    via Norton transform. For simplicity we use Thevenin → Norton conversion.

    To keep node count low, we model as Norton: I_n(t) = V(t)/R_int in parallel
    with G_int = 1/R_int.
    """
    id: str
    n_pos: int
    n_neg: int
    V_re: float
    V_im: float
    f_hz: float
    r_int: float = 1e-3

    def stamp(self, acc: StampAccumulator) -> None:
        acc.add_2port(self.n_pos, self.n_neg, complex(1.0 / self.r_int))

    def source_inject(self, i_h: np.ndarray, t: float) -> None:
        # v(t) = Re{ (V_re + j V_im) · e^{j 2π f t} } = V_re·cos − V_im·sin
        omega_t = 2 * np.pi * self.f_hz * t
        v_t = self.V_re * np.cos(omega_t) - self.V_im * np.sin(omega_t)
        i_n = v_t / self.r_int
        i_h[self.n_pos] += i_n
        i_h[self.n_neg] -= i_n


@dataclass
class ISourceSinusoidal(Element):
    """Sinusoidal current source. Convention: positive current flows from n_pos
    *into* the source (and back out at n_neg). To inject current INTO node X, set
    n_pos = ground, n_neg = X — then i(t) flows from ground to X.

    Equivalent simpler convention used here: current flows from n_neg to n_pos
    externally (i.e. injected INTO n_pos, withdrawn FROM n_neg).
    """
    id: str
    n_pos: int
    n_neg: int
    I_re: float
    I_im: float
    f_hz: float

    def stamp(self, acc: StampAccumulator) -> None:
        # Ideal current source: no admittance contribution
        return

    def source_inject(self, i_h: np.ndarray, t: float) -> None:
        omega_t = 2 * np.pi * self.f_hz * t
        i_t = self.I_re * np.cos(omega_t) - self.I_im * np.sin(omega_t)
        i_h[self.n_pos] += i_t
        i_h[self.n_neg] -= i_t


@dataclass
class NodeProbe:
    """Voltage probe — samples node voltage relative to ground each step."""
    name: str
    node: int
    kind: str = "voltage"

    def sample(self, v_node: np.ndarray) -> float:
        return float(v_node[self.node].real)


@dataclass
class BranchCurrentProbe:
    """Current through a 2-port element (R/L/C/switch). For mutual blocks use
    MutualBranchProbe with a port index."""
    name: str
    element_id: str
    kind: str = "current"

    def sample(self, elements_by_id: dict, v_node: np.ndarray) -> float:
        el = elements_by_id[self.element_id]
        if isinstance(el, Resistor):
            return float((v_node[el.n_pos].real - v_node[el.n_neg].real) / el.r)
        if isinstance(el, Inductor):
            # i = G·v + ih_old (after update_history this is _i_prev)
            return float(el._i_prev)
        if isinstance(el, Capacitor):
            return float(el._i_prev)
        if isinstance(el, IdealSwitch):
            r = el.CLOSED_R if el._closed else el.OPEN_R
            return float((v_node[el.n_pos].real - v_node[el.n_neg].real) / r)
        raise TypeError(f"BranchCurrentProbe doesn't support {type(el).__name__}")


@dataclass
class MutualBranchProbe:
    """Current through one port (conductor) of a MutualInductorBlock."""
    name: str
    element_id: str
    port: int
    kind: str = "current"

    def sample(self, elements_by_id: dict, v_node: np.ndarray) -> float:
        el = elements_by_id[self.element_id]
        assert isinstance(el, MutualInductorBlock), \
            f"MutualBranchProbe needs MutualInductorBlock, got {type(el).__name__}"
        if el._i_prev is None:
            return 0.0
        return float(el._i_prev[self.port])
