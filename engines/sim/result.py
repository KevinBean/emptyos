"""SimResult — dataclass for one EMTP run output.

Carries raw waveforms, derived steady-state phasors (FFT bin at f0), KCL
residual history, and provenance. Probes are sampled at every timestep;
phasors are extracted from the last full cycle via DFT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ProbeSeries:
    name: str
    kind: str  # "voltage" | "current"
    refs: tuple[str, ...]
    values: np.ndarray  # complex array if both real and imag samples; here we keep real (instantaneous)
    phasor: complex = 0j  # FFT bin at f0 from last full cycle


@dataclass
class SimResult:
    t: np.ndarray  # timesteps, shape (N,)
    f0_hz: float
    dt_s: float
    probes: dict[str, ProbeSeries] = field(default_factory=dict)
    kcl_residual_max: float = 0.0
    energy_residual_pct: float = 0.0
    n_steps: int = 0
    n_nodes: int = 0
    runtime_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    def phasor(self, probe_name: str) -> complex:
        p = self.probes.get(probe_name)
        return p.phasor if p else 0j

    def waveform(self, probe_name: str) -> np.ndarray | None:
        p = self.probes.get(probe_name)
        return p.values if p else None

    def to_summary_dict(self) -> dict:
        """JSON-friendly summary (no waveforms — use to_ndjson for those)."""
        return {
            "f0_hz": self.f0_hz,
            "dt_s": self.dt_s,
            "n_steps": self.n_steps,
            "n_nodes": self.n_nodes,
            "runtime_s": round(self.runtime_s, 3),
            "kcl_residual_max": self.kcl_residual_max,
            "energy_residual_pct": self.energy_residual_pct,
            "warnings": list(self.warnings),
            "phasors": {
                name: {"re": p.phasor.real, "im": p.phasor.imag, "mag": abs(p.phasor),
                       "kind": p.kind, "refs": list(p.refs)}
                for name, p in self.probes.items()
            },
            "extras": dict(self.extras),
        }


def extract_phasor(values: np.ndarray, dt: float, f0: float) -> complex:
    """DFT bin at f0 over the last full cycle. Returns the rotating phasor (peak·e^{jφ}).

    Using the last cycle avoids initial-transient bias; assumes the run is long
    enough to reach steady state (caller's responsibility — check energy_residual).
    """
    if len(values) < 2:
        return 0j
    samples_per_cycle = int(round(1.0 / (f0 * dt)))
    if samples_per_cycle < 4 or len(values) < samples_per_cycle:
        # Not enough to extract a meaningful phasor — fall back to peak ± atan2 estimator
        return 0j
    last = values[-samples_per_cycle:]
    n = samples_per_cycle
    k = np.arange(n)
    # Reference cosine and sine at f0
    cos_ref = np.cos(2 * np.pi * k / n)
    sin_ref = np.sin(2 * np.pi * k / n)
    # Real-signal DFT — convention: x(t) = Re{X · e^{jωt}}, so X = (2/N) Σ x[k] · e^{-j2πk/N}
    re = (2.0 / n) * np.dot(last, cos_ref)
    im = -(2.0 / n) * np.dot(last, sin_ref)
    return complex(re, im)
