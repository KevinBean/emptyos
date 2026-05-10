"""Hankel-transform digital linear filters (DLF).

A DLF approximates

    ∫_0^∞ f(λ) J_ν(λ r) dλ  ≈  (1/r) Σ_j w_j · f(b_j / r)

where (b_j, w_j) are precomputed (abscissa, weight) pairs.

Filter coefficients live in `data/<name>.txt` in the **libdlf two-column format**
(https://github.com/emsig/libdlf):

    # any number of comment lines starting with '#'
    base                weight
    8.26989508568e-06   3.30220475766e-04
    1.07739669946e-05  -8.41464033580e-04
    ...

The default J_0 filter is the Guptasarma–Singh (1997) 61-point short filter,
copied verbatim from libdlf. See data/README.md for provenance.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class HankelFilter:
    """A digital linear filter for a Hankel transform of given Bessel order."""

    name: str
    order: int                            # Bessel order ν (0 or 1)
    bases: tuple[float, ...]              # b_j  =  λ_j · r
    weights: tuple[float, ...]            # w_j

    @property
    def n(self) -> int:
        return len(self.weights)

    def evaluate(self, f: Callable[[float], float], r: float) -> float:
        """Approximate ∫_0^∞ f(λ) J_ν(λ r) dλ for the configured Bessel order."""
        acc = 0.0
        for b, w in zip(self.bases, self.weights):
            acc += w * f(b / r)
        return acc / r


def _parse_filter_file(path: Path) -> tuple[tuple[float, ...], tuple[float, ...]]:
    bases: list[float] = []
    weights: list[float] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"{path}: expected 'base weight' per line, got: {line!r}")
        try:
            b, w = float(parts[0]), float(parts[1])
        except ValueError:
            # First non-comment line in libdlf files is a header like 'base   j0' — skip it.
            continue
        bases.append(b)
        weights.append(w)
    if not bases:
        raise ValueError(f"{path}: no numeric (base, weight) rows found")
    return tuple(bases), tuple(weights)


_CACHE: dict[str, HankelFilter] = {}


def load_filter(name: str, order: int) -> HankelFilter:
    """Load and cache a named filter from `data/<name>.txt`."""
    if name in _CACHE:
        return _CACHE[name]
    path = _DATA_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"Filter coefficient file {path} not found. Available filters live in "
            f"engines/soil/data/ in the libdlf two-column format. See data/README.md."
        )
    bases, weights = _parse_filter_file(path)
    flt = HankelFilter(name=name, order=order, bases=bases, weights=weights)
    _CACHE[name] = flt
    return flt


def j0_filter(precision: str = "default") -> HankelFilter:
    """Default J_0 Hankel filter.

    `precision` ∈ {"default", "high"}:
      - "default" → Guptasarma–Singh 61-pt short
      - "high"    → Guptasarma–Singh 120-pt long
    """
    if precision == "default":
        return load_filter("gupta_singh_61_j0", order=0)
    if precision == "high":
        return load_filter("gupta_singh_120_j0", order=0)
    raise ValueError(f"unknown precision {precision!r}")
