"""SimEngine — the BaseEngine entry point loaded by the kernel.

Apps access via `self.engine("sim")`. Methods:

  parse(data: dict) -> Netlist
      Parse a netlist dict (already-loaded TOML/JSON) into the internal form.

  solve(netlist) -> SimResult
      Run the time-stepper to completion and return waveforms + phasors.

  adapt_fault_distribution(network) -> Netlist
      Convenience wrapper — converts a fault-distribution Network into a
      time-domain netlist via the bundled adapter, with default sim params
      sized for the network length.

  available() -> bool
      True iff numpy + scipy.sparse are importable.
"""

from __future__ import annotations

from typing import Any

from emptyos.sdk import BaseEngine

from .netlist import Netlist, parse_netlist
from .result import SimResult
from .stepper import SimParams, step


class SimEngine(BaseEngine):
    name = "sim"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._numpy = False
        self._scipy = False

    async def init(self):
        try:
            import numpy  # noqa: F401
            self._numpy = True
        except ImportError:
            self._numpy = False
        try:
            import scipy.sparse  # noqa: F401
            self._scipy = True
        except ImportError:
            self._scipy = False

    async def available(self) -> bool:
        return self._numpy and self._scipy

    async def health_check(self) -> dict:
        return {
            "status": "ok" if (self._numpy and self._scipy) else "unavailable",
            "available": self._numpy and self._scipy,
            "numpy": self._numpy,
            "scipy": self._scipy,
            "engine_version": self.manifest.raw.get("engine", {}).get("version", "?"),
        }

    # ── Public API ────────────────────────────────────────────────────

    def parse(self, data: dict) -> Netlist:
        return parse_netlist(data)

    def solve(self, netlist: Netlist, params: SimParams | None = None) -> SimResult:
        if not (self._numpy and self._scipy):
            raise RuntimeError("sim engine unavailable: numpy + scipy required")
        sp = params or netlist.params
        return step(netlist.elements, netlist.n_nodes, netlist.probes, sp)

    def adapt_fault_distribution(self, network: Any, **kwargs) -> Netlist:
        """Build a netlist from a fault-distribution Network. Lazy-imports
        the adapter so the engine loads cleanly even if fault-distribution
        isn't installed in this deployment."""
        from .adapters.fault_distribution import network_to_netlist
        return network_to_netlist(network, **kwargs)
