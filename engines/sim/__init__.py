"""sim — native EMTP-class power-systems simulator.

Power-frequency band (50/60 Hz), Dommel companion-circuit method,
trapezoidal integration. Sparse Y assembled from element stamps,
LU-factored once at start (refactored on switch events).

Public surface:

    from .engine import SimEngine
    from .netlist import Netlist, parse_netlist
    from .stepper import SimParams, step
    from .result import SimResult, ProbeSeries, extract_phasor

Apps consume the engine via `self.engine("sim")` (BaseApp helper) and
either call `.solve(netlist)` directly or `.adapt_fault_distribution(network)`
to convert a fault-distribution `Network` into a netlist first.
"""

from .engine import SimEngine
from .netlist import Netlist, parse_netlist
from .result import ProbeSeries, SimResult, extract_phasor
from .stepper import SimParams, step

__all__ = [
    "SimEngine",
    "Netlist",
    "parse_netlist",
    "SimParams",
    "step",
    "SimResult",
    "ProbeSeries",
    "extract_phasor",
]
