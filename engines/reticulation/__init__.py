"""engines/reticulation — radial AC load-flow + segment sizing.

Public surface:
    ReticulationEngine — kernel entry point; apps use self.engine("reticulation").
    solve_load_flow(input) — pure function, callable without the kernel.
    check_sizing(input, result) — pure function; flag overload + V-drop.
"""

from .engine import ReticulationEngine
from .loadflow import solve_load_flow
from .shortcircuit import compute_short_circuit
from .sizing import check_sizing

__all__ = [
    "ReticulationEngine",
    "solve_load_flow",
    "compute_short_circuit",
    "check_sizing",
]
