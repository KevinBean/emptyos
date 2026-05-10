"""Network topology — nodes + edges. Shared across sim, em, cables.

Deliberately minimal. Domain-specific attributes live on the
consuming app's record types (CableRecord etc.) and are referenced
by id from the topology.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


NodeKind = Literal[
    "bus", "substation", "turbine", "bess", "load", "source", "ground", "junction", "other"
]


class NodeRecord(BaseModel):
    id: str
    kind: NodeKind = "bus"
    label: str | None = None
    x: float | None = None  # planar layout, optional
    y: float | None = None
    voltage_kv: float | None = None
    metadata: dict = Field(default_factory=dict)


class EdgeRecord(BaseModel):
    id: str
    from_node: str
    to_node: str
    kind: str = "branch"  # cable | line | transformer | switch | …
    length_m: float | None = None
    record_ref: str | None = Field(
        None, description="Optional pointer to a domain record (e.g. CableRecord.id)."
    )
    metadata: dict = Field(default_factory=dict)


class NetworkTopo(BaseModel):
    nodes: list[NodeRecord] = Field(default_factory=list)
    edges: list[EdgeRecord] = Field(default_factory=list)

    def node(self, id: str) -> NodeRecord | None:
        for n in self.nodes:
            if n.id == id:
                return n
        return None

    def edge(self, id: str) -> EdgeRecord | None:
        for e in self.edges:
            if e.id == id:
                return e
        return None
