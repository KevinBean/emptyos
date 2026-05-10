"""engines/models — in-tree Pydantic data contracts.

Single source of truth for power-systems / EM / thermal data shapes
shared across engines (sim, em, thermal, lines) and apps (cables,
lines, earthing, studies, …).

Eventually extractable to a standalone `emptyos-power-contracts`
package. Until a non-EmptyOS consumer needs it, lives in-tree.
"""

from .soil import SoilModel, SoilLayer
from .geometry import ConductorGeometry, CableGeometry
from .network import NetworkTopo, NodeRecord, EdgeRecord
from .line import LineParameters
from .cable import (
    CableRecord,
    CableLibraryEntry,
    AmpacityInput,
    AmpacityResult,
    InstallationType,
    BondingType,
)
from .project import ProjectSettings, resolve_override, resolve_override_with_source
from .loadflow import (
    BusLoad,
    SegmentImpedance,
    LoadFlowInput,
    LoadFlowResult,
    BusVoltage,
    SegmentFlow,
    PVBusResult,
    SegmentSizingCheck,
    SegmentViolation,
    SizingResult,
    ShortCircuitInput,
    ShortCircuitResult,
    BusShortCircuit,
)

__all__ = [
    "SoilModel",
    "SoilLayer",
    "ConductorGeometry",
    "CableGeometry",
    "NetworkTopo",
    "NodeRecord",
    "EdgeRecord",
    "LineParameters",
    "CableRecord",
    "CableLibraryEntry",
    "AmpacityInput",
    "AmpacityResult",
    "InstallationType",
    "BondingType",
    "ProjectSettings",
    "resolve_override",
    "resolve_override_with_source",
    "BusLoad",
    "SegmentImpedance",
    "LoadFlowInput",
    "LoadFlowResult",
    "BusVoltage",
    "SegmentFlow",
    "PVBusResult",
    "SegmentSizingCheck",
    "SegmentViolation",
    "SizingResult",
    "ShortCircuitInput",
    "ShortCircuitResult",
    "BusShortCircuit",
]
