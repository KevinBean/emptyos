"""Segment sizing checks — utilisation vs ampacity + voltage-drop limits.

Pure function over a LoadFlowInput + LoadFlowResult pair. The caller is
responsible for populating SegmentImpedance.rated_a (typically from
engines/thermal ampacity output) — segments with rated_a=None get a
utilisation of None and are skipped for overload flagging.
"""

from __future__ import annotations

from engines.models import (
    LoadFlowInput,
    LoadFlowResult,
    SegmentSizingCheck,
    SegmentViolation,
    SizingResult,
)


def check_sizing(
    input: LoadFlowInput | dict,
    result: LoadFlowResult | dict,
    *,
    voltage_low_pu: float = 0.95,
    voltage_high_pu: float = 1.05,
) -> SizingResult:
    if isinstance(input, dict):
        input = LoadFlowInput(**input)
    if isinstance(result, dict):
        result = LoadFlowResult(**result)

    seg_by_edge = {s.edge_id: s for s in input.segments}
    flows_by_edge = {f.edge_id: f for f in result.segment_flows}
    vd_limit = input.voltage_drop_limit_pct

    checks: list[SegmentSizingCheck] = []
    violations: list[SegmentViolation] = []

    for f in result.segment_flows:
        seg = seg_by_edge.get(f.edge_id)
        rated = seg.rated_a if seg else None
        util = (100.0 * f.current_a / rated) if rated and rated > 0 else None

        ok = True
        if util is not None and util > 100.0:
            ok = False
            violations.append(
                SegmentViolation(
                    edge_id=f.edge_id,
                    type="overload",
                    actual=f.current_a,
                    limit=rated,
                    severity="violation",
                    message=f"current {f.current_a:.1f} A exceeds ampacity {rated:.1f} A ({util:.0f}%)",
                )
            )
        if f.voltage_drop_pct > vd_limit:
            ok = False
            violations.append(
                SegmentViolation(
                    edge_id=f.edge_id,
                    type="voltage_drop",
                    actual=f.voltage_drop_pct,
                    limit=vd_limit,
                    severity="violation",
                    message=f"V-drop {f.voltage_drop_pct:.2f}% exceeds limit {vd_limit:.2f}%",
                )
            )

        checks.append(
            SegmentSizingCheck(
                edge_id=f.edge_id,
                current_a=f.current_a,
                ampacity_a=rated,
                utilization_pct=util,
                voltage_drop_pct=f.voltage_drop_pct,
                voltage_drop_limit_pct=vd_limit,
                ok=ok,
            )
        )

    # Bus voltage band checks (separate from per-segment V-drop).
    for bv in result.bus_voltages:
        if bv.voltage_pu < voltage_low_pu:
            violations.append(
                SegmentViolation(
                    edge_id=bv.node_id,
                    type="voltage_low",
                    actual=bv.voltage_pu,
                    limit=voltage_low_pu,
                    severity="violation",
                    message=f"bus {bv.node_id} V={bv.voltage_pu:.4f} pu below {voltage_low_pu:.2f}",
                )
            )
        elif bv.voltage_pu > voltage_high_pu:
            violations.append(
                SegmentViolation(
                    edge_id=bv.node_id,
                    type="voltage_high",
                    actual=bv.voltage_pu,
                    limit=voltage_high_pu,
                    severity="warning",
                    message=f"bus {bv.node_id} V={bv.voltage_pu:.4f} pu above {voltage_high_pu:.2f}",
                )
            )

    return SizingResult(
        checks=checks,
        violations=violations,
        ok=all(c.ok for c in checks)
        and not any(v.type in ("voltage_low", "voltage_high") and v.severity == "violation" for v in violations),
    )
