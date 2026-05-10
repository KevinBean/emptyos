"""Load-flow data contracts — input/output for engines/reticulation.

Single-phase equivalent (line-to-neutral) internally; three-phase data
is converted at the boundary via the `phases` field on LoadFlowInput.
Reuses NetworkTopo from engines/models/network.py — segments here are
the electrical impedance complement to EdgeRecord (same id).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .network import NetworkTopo


class BusLoad(BaseModel):
    """Load or generator at a node.

    Two bus kinds:

    * `PQ` (default) — constant-power: both p_kw and q_kvar are fixed.
      Positive = consumption, negative = generation. q_kvar is the
      committed reactive draw/injection.
    * `PV` — voltage-regulated: p_kw is fixed (+ load / − gen), |V| at
      this bus is held at `v_setpoint_pu` (fraction of slack voltage).
      The solver iterates Q-injection from a generator at this node
      between [`q_min_kvar`, `q_max_kvar`] to satisfy the setpoint.
      `q_kvar` is the initial guess / fallback when limits saturate.
      Q limits express **generator reactive output** at the bus (positive
      = injection into the bus). When saturated, the bus reverts to PQ
      at the clamped limit.
    """

    node_id: str
    p_kw: float = 0.0
    q_kvar: float = 0.0
    bus_kind: Literal["PQ", "PV"] = "PQ"
    v_setpoint_pu: float | None = Field(
        None, gt=0,
        description="PV setpoint, fraction of slack |V|. Required when bus_kind='PV'.",
    )
    q_min_kvar: float | None = Field(
        None,
        description="Lower limit on generator reactive output at this bus (kvar, +ve = injection).",
    )
    q_max_kvar: float | None = Field(
        None,
        description="Upper limit on generator reactive output at this bus (kvar, +ve = injection).",
    )


class SegmentImpedance(BaseModel):
    """Electrical impedance of one edge. Total values, not per-km.

    rated_a is optional — when present, sizing.check_sizing flags
    segments with current > rated_a as overloaded. Typically populated
    from engines/thermal ampacity output upstream.
    """

    edge_id: str
    r_ohm: float = Field(..., ge=0)
    x_ohm: float = Field(0.0, ge=0)
    rated_a: float | None = Field(None, gt=0)


class LoadFlowInput(BaseModel):
    """Radial AC load-flow input. Must be a tree rooted at slack_node."""

    topology: NetworkTopo
    slack_node: str
    slack_voltage_kv: float = Field(..., gt=0, description="Line-to-line, kV")
    phases: Literal[1, 3] = 3
    loads: list[BusLoad] = Field(default_factory=list)
    segments: list[SegmentImpedance] = Field(default_factory=list)
    max_iterations: int = 30
    tolerance_pu: float = 1e-6
    voltage_drop_limit_pct: float = Field(
        5.0, description="Default per-segment voltage-drop limit for sizing checks."
    )
    metadata: dict = Field(default_factory=dict)


class BusVoltage(BaseModel):
    node_id: str
    voltage_kv: float = Field(..., description="Magnitude, line-to-line, kV")
    voltage_pu: float
    angle_deg: float


class SegmentFlow(BaseModel):
    edge_id: str
    current_a: float = Field(..., description="Magnitude, A (per phase)")
    p_flow_kw: float = Field(..., description="Three-phase active power into 'from' end")
    q_flow_kvar: float
    p_loss_kw: float
    q_loss_kvar: float
    voltage_drop_pct: float = Field(
        ..., description="100*(|V_from|-|V_to|)/|V_from|"
    )


class PVBusResult(BaseModel):
    """Solved state for a PV bus."""

    node_id: str
    v_setpoint_pu: float
    v_solved_pu: float
    q_inject_kvar: float = Field(
        ..., description="Generator reactive output (+ve = injection into bus)."
    )
    saturated: bool = False
    saturation_limit: Literal["min", "max"] | None = None


class LoadFlowResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    converged: bool
    iterations: int
    bus_voltages: list[BusVoltage]
    segment_flows: list[SegmentFlow]
    pv_buses: list[PVBusResult] = Field(default_factory=list)
    total_loss_kw: float
    total_load_kw: float
    notes: list[str] = Field(default_factory=list)
    method: str = "backward_forward_sweep"
    method_version: str = "0.2.0"


# ── Short circuit ───────────────────────────────────────────────────


class ShortCircuitInput(BaseModel):
    """3-phase symmetric short-circuit input (IEC 60909-style).

    Reuses the LoadFlowInput topology contract (slack node, segments,
    voltage). The sweep walks the radial tree from the slack to every
    bus and accumulates path impedance.

    `source_mva_3ph` is the upstream grid short-circuit capacity at the
    slack bus (3-phase). When provided, an equivalent source impedance
    Z_src = c · V_LL² / S_src is added in series. Omit to treat the slack
    as an infinite bus (worst-case fault current).
    """

    topology: NetworkTopo
    slack_node: str
    slack_voltage_kv: float = Field(..., gt=0, description="Line-to-line, kV")
    phases: Literal[1, 3] = 3
    segments: list[SegmentImpedance] = Field(default_factory=list)
    voltage_factor: float = Field(
        1.10,
        gt=0,
        description=(
            "IEC 60909 c-factor. Use 1.10 for max fault current (breaker "
            "rating, LV+MV); 0.95 for min fault current (protection reach)."
        ),
    )
    source_mva_3ph: float | None = Field(
        None, gt=0,
        description="Upstream grid short-circuit MVA at slack. Omit for infinite-bus.",
    )


class BusShortCircuit(BaseModel):
    """Prospective 3-phase fault values at one bus."""

    node_id: str
    r_thev_ohm: float
    x_thev_ohm: float
    z_thev_ohm: float
    isc_3ph_ka: float = Field(..., description="Symmetric initial short-circuit current, kA per phase.")
    ssc_mva: float = Field(..., description="Apparent fault MVA = sqrt(3) · V_LL · Isc.")


class ShortCircuitResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    buses: list[BusShortCircuit]
    voltage_factor: float
    source_mva_3ph: float | None = None
    notes: list[str] = Field(default_factory=list)
    method: str = "iec_60909_radial_thevenin"
    method_version: str = "0.1.0"


# ── Sizing ──────────────────────────────────────────────────────────


class SegmentSizingCheck(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    edge_id: str
    current_a: float
    ampacity_a: float | None
    utilization_pct: float | None
    voltage_drop_pct: float
    voltage_drop_limit_pct: float
    ok: bool


class SegmentViolation(BaseModel):
    edge_id: str
    type: Literal["overload", "voltage_drop", "voltage_low", "voltage_high"]
    actual: float
    limit: float
    severity: Literal["warning", "violation"] = "violation"
    message: str = ""


class SizingResult(BaseModel):
    checks: list[SegmentSizingCheck]
    violations: list[SegmentViolation]
    ok: bool
