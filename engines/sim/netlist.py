"""Netlist — graph + element list. Parser converts dict/TOML/JSON to internal form.

Schema (TOML or equivalent JSON):

  schema_version = 1

  [sim]
  f0_hz = 50.0
  dt_s = 50e-6
  t_end_s = 0.16

  [[nodes]]                 # node table — first node MUST be "ground"
  id = "ground"
  [[nodes]]
  id = "central_grid"

  [[elements]]
  type = "R"                # R / L / C / M / Switch / VSource / ISource
  id = "Rg_central"
  n_pos = "central_grid"
  n_neg = "ground"
  r = 0.15623

  [[elements]]
  type = "M"                # mutual inductor block (Carson n×n L)
  id = "ohl_span_42"
  ports_pos = ["c_phase_42", "c_sky_42"]
  ports_neg = ["c_phase_43", "c_sky_43"]
  L_matrix = [[L11, L12], [L12, L22]]   # symmetric

  [[probes]]
  name = "v_central"
  kind = "node"
  node = "central_grid"

  [[probes]]
  name = "i_skywire_span0"
  kind = "branch"
  element = "ohl_span_0"
  port = 1                  # for mutual blocks; absent for 2-port

`parse_netlist(data: dict)` returns a Netlist that the engine consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .elements import (
    BranchCurrentProbe,
    Capacitor,
    Element,
    IdealSwitch,
    Inductor,
    ISourceSinusoidal,
    MutualBranchProbe,
    MutualInductorBlock,
    NodeProbe,
    Resistor,
    VSourceSinusoidal,
)
from .stepper import SimParams


@dataclass
class Netlist:
    nodes: list[str]                       # node names; nodes[0] == "ground"
    elements: list[Element]
    probes: list                           # NodeProbe | BranchCurrentProbe | MutualBranchProbe
    params: SimParams = field(default_factory=SimParams)
    schema_version: int = 1

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)


def parse_netlist(data: dict) -> Netlist:
    """Build a Netlist from a dict (already-loaded TOML or JSON)."""
    schema_version = int(data.get("schema_version", 1))
    if schema_version != 1:
        raise ValueError(f"unsupported netlist schema_version {schema_version}")

    sim_cfg = data.get("sim", {}) or {}
    params = SimParams(
        f0_hz=float(sim_cfg.get("f0_hz", 50.0)),
        dt_s=float(sim_cfg.get("dt_s", 50e-6)),
        t_end_s=float(sim_cfg.get("t_end_s", 0.16)),
        fault_t_s=sim_cfg.get("fault_t_s"),
    )

    raw_nodes = data.get("nodes") or []
    if not raw_nodes:
        raise ValueError("netlist has no nodes")
    node_names = [n["id"] for n in raw_nodes]
    if node_names[0] != "ground":
        raise ValueError("first node must be 'ground' (the reference node, index 0)")

    node_idx = {name: i for i, name in enumerate(node_names)}

    def _n(name: str) -> int:
        if name not in node_idx:
            raise ValueError(f"unknown node {name!r}")
        return node_idx[name]

    elements: list[Element] = []
    by_id: dict[str, Element] = {}
    for raw in data.get("elements") or []:
        t = raw["type"]
        eid = raw["id"]
        if eid in by_id:
            raise ValueError(f"duplicate element id {eid!r}")

        if t == "R":
            el = Resistor(id=eid, n_pos=_n(raw["n_pos"]), n_neg=_n(raw["n_neg"]),
                          r=float(raw["r"]))
        elif t == "L":
            el = Inductor(id=eid, n_pos=_n(raw["n_pos"]), n_neg=_n(raw["n_neg"]),
                          L=float(raw["L"]))
        elif t == "C":
            el = Capacitor(id=eid, n_pos=_n(raw["n_pos"]), n_neg=_n(raw["n_neg"]),
                           C=float(raw["C"]))
        elif t == "M":
            ports_pos = [_n(p) for p in raw["ports_pos"]]
            ports_neg = [_n(p) for p in raw["ports_neg"]]
            L_mat = np.asarray(raw["L_matrix"], dtype=float)
            el = MutualInductorBlock(id=eid, n_pos=ports_pos, n_neg=ports_neg, L=L_mat)
        elif t == "Switch":
            el = IdealSwitch(
                id=eid,
                n_pos=_n(raw["n_pos"]),
                n_neg=_n(raw["n_neg"]),
                t_close=float(raw.get("t_close", float("inf"))),
                initially_closed=bool(raw.get("initially_closed", False)),
            )
        elif t == "VSource":
            el = VSourceSinusoidal(
                id=eid,
                n_pos=_n(raw["n_pos"]),
                n_neg=_n(raw["n_neg"]),
                V_re=float(raw["V_re"]),
                V_im=float(raw.get("V_im", 0.0)),
                f_hz=float(raw.get("f_hz", params.f0_hz)),
                r_int=float(raw.get("r_int", 1e-3)),
            )
        elif t == "ISource":
            el = ISourceSinusoidal(
                id=eid,
                n_pos=_n(raw["n_pos"]),
                n_neg=_n(raw["n_neg"]),
                I_re=float(raw["I_re"]),
                I_im=float(raw.get("I_im", 0.0)),
                f_hz=float(raw.get("f_hz", params.f0_hz)),
            )
        else:
            raise ValueError(f"unknown element type {t!r} on element {eid!r}")
        elements.append(el)
        by_id[eid] = el

    probes: list = []
    for raw in data.get("probes") or []:
        name = raw["name"]
        kind = raw.get("kind", "node")
        if kind == "node":
            probes.append(NodeProbe(name=name, node=_n(raw["node"])))
        elif kind == "branch":
            element_id = raw["element"]
            if element_id not in by_id:
                raise ValueError(f"probe {name!r} references unknown element {element_id!r}")
            if "port" in raw:
                probes.append(MutualBranchProbe(name=name, element_id=element_id,
                                                port=int(raw["port"])))
            else:
                probes.append(BranchCurrentProbe(name=name, element_id=element_id))
        else:
            raise ValueError(f"unknown probe kind {kind!r}")

    return Netlist(
        nodes=node_names,
        elements=elements,
        probes=probes,
        params=params,
        schema_version=schema_version,
    )
