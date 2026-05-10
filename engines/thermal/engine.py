"""ThermalEngine — kernel-loaded entry point.

Apps access via `self.engine("thermal")`. Methods:

  ampacity(input: AmpacityInput) -> AmpacityResult
      IEC 60287 forward calculation: given installation + ambient,
      return continuous current rating at the conductor max temperature.

  conductor_temperature(input: AmpacityInput, current_a: float) -> AmpacityResult
      Inverse: given a current, return the conductor temperature.

  available() -> bool
      Always True (pure-Python, no optional deps in Phase A).
"""

from __future__ import annotations

from typing import Any

from emptyos.sdk import BaseEngine

from engines.models import AmpacityInput, AmpacityResult

from .iec60287 import compute_ampacity, compute_conductor_temperature


class ThermalEngine(BaseEngine):
    name = "thermal"

    async def init(self) -> None:
        return None

    async def available(self) -> bool:
        return True

    async def health_check(self) -> dict:
        return {
            "status": "ok",
            "available": True,
            "engine_version": self.manifest.raw.get("engine", {}).get("version", "?"),
            "method": "iec60287",
            "phase": "A",
            "installation_types": ["direct_buried", "in_duct", "in_air"],
        }

    # --- domain methods (pure; no kernel coupling) ---

    def ampacity(self, input: AmpacityInput | dict) -> AmpacityResult:
        if isinstance(input, dict):
            input = AmpacityInput(**input)
        return compute_ampacity(input)

    def conductor_temperature(
        self, input: AmpacityInput | dict, current_a: float
    ) -> AmpacityResult:
        if isinstance(input, dict):
            input = AmpacityInput(**input)
        return compute_conductor_temperature(input, current_a)
