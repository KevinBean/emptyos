"""Multi-layer soil resistivity inversion engine.

Forward apparent-resistivity computation for horizontally stratified earth
(Stefanesco recursion + Hankel-transform DLF) and inverse Levenberg-Marquardt
fitting of layer parameters from electrode-array measurements.

See DESIGN.md for math, validation plan, and roadmap.
"""

from .soil_model import SoilModel
from .geometry import ElectrodeArray
from .kernel import stefanesco_recursion, kernel
from .forward import forward_apparent_resistivity

__all__ = [
    "SoilModel",
    "ElectrodeArray",
    "stefanesco_recursion",
    "kernel",
    "forward_apparent_resistivity",
]
