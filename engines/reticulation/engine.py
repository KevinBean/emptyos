"""ReticulationEngine — kernel-loaded entry point.

Apps access via `self.engine("reticulation")`. Methods:

  load_flow(input) -> LoadFlowResult
      Backward/forward sweep on a radial network. Raises ValueError if
      the topology has a cycle or disconnected components.

  check_sizing(input, result) -> SizingResult
      Flag overloads + voltage-drop + bus-voltage-band violations.

  size_and_solve(input) -> dict[result, sizing]
      Convenience wrapper — solve + check in one call.

  available() -> bool
      Always True (pure-Python).
"""

from __future__ import annotations

from emptyos.sdk import BaseEngine

from engines.models import (
    LoadFlowInput,
    LoadFlowResult,
    ShortCircuitInput,
    ShortCircuitResult,
    SizingResult,
)

from .loadflow import solve_load_flow
from .shortcircuit import compute_short_circuit
from .sizing import check_sizing


class ReticulationEngine(BaseEngine):
    name = "reticulation"

    async def init(self) -> None:
        return None

    async def available(self) -> bool:
        return True

    async def health_check(self) -> dict:
        return {
            "status": "ok",
            "available": True,
            "engine_version": self.manifest.raw.get("engine", {}).get("version", "?"),
            "method": "backward_forward_sweep",
            "phase": "A",
            "topologies": ["radial"],
        }

    # ── Domain methods (pure; no kernel coupling) ──────────────────

    def load_flow(self, input: LoadFlowInput | dict) -> LoadFlowResult:
        return solve_load_flow(input)

    def check_sizing(
        self,
        input: LoadFlowInput | dict,
        result: LoadFlowResult | dict,
        *,
        voltage_low_pu: float = 0.95,
        voltage_high_pu: float = 1.05,
    ) -> SizingResult:
        return check_sizing(
            input,
            result,
            voltage_low_pu=voltage_low_pu,
            voltage_high_pu=voltage_high_pu,
        )

    def size_and_solve(self, input: LoadFlowInput | dict) -> dict:
        result = self.load_flow(input)
        sizing = self.check_sizing(input, result)
        return {"result": result, "sizing": sizing}

    def short_circuit(
        self, input: ShortCircuitInput | dict
    ) -> ShortCircuitResult:
        return compute_short_circuit(input)
