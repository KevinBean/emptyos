"""engines/thermal — IEC 60287 cable ampacity (Phase A).

Public surface:
    ThermalEngine — BaseEngine entry point; apps use self.engine("thermal").
    iec60287.ampacity(input) — pure function, callable without the kernel.
"""

from .engine import ThermalEngine

__all__ = ["ThermalEngine"]
