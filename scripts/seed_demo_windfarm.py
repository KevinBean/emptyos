"""Seed a demo cable-reticulation project from the legacy tool example.

Reads `Cable-reticulation-tool/example_wind_farm_design.json` if present
(else accepts --src), writes EmptyOS-shape vault notes for project +
nodes + edges + cables, and copies a background image (SLD.jpg) into
the project directory so the topology page can render it behind the
network.

Daemon-free: writes directly to the vault. Idempotent — re-running with
the same --project replaces the project's notes (use --replace).

Usage:
    python scripts/seed_demo_windfarm.py
    python scripts/seed_demo_windfarm.py --src C:/path/to/example.json --project demo-windfarm --replace
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


_SRC_ENV = os.environ.get("EOS_DEMO_WINDFARM_SRC", "")
_BG_ENV = os.environ.get("EOS_DEMO_WINDFARM_BG", "")
DEFAULT_SRC = Path(_SRC_ENV) if _SRC_ENV else None
DEFAULT_BG = Path(_BG_ENV) if _BG_ENV else None
DEFAULT_PROJECT_ID = "demo-windfarm-50mw"
CANVAS_SCALE = 0.5  # legacy 2000x1500 → ~1000x750 (fits topology viewBox 1000x700)

# 1-core Cu 33 kV library entries that exist in the seeded Nexans library.
# Legacy spec is 3-core but our library only has 1-core 33kV Cu — close
# enough for a demo, ratings sit in the right order.
LIB_BY_SIZE: dict[int, str] = {
    95:  "nexans_19-33kv_cu_1c_95",
    150: "nexans_19-33kv_cu_1c_150",
    240: "nexans_19-33kv_cu_1c_240",
    300: "nexans_19-33kv_cu_1c_300",
    400: "nexans_19-33kv_cu_1c_400",
    500: "nexans_19-33kv_cu_1c_500",
    630: "nexans_19-33kv_cu_1c_630",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_vault_path() -> Path:
    """Read notes.path from emptyos.toml at the repo root."""
    cfg = Path(__file__).resolve().parent.parent / "emptyos.toml"
    if not cfg.exists():
        sys.exit(f"emptyos.toml not found at {cfg}")
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    p = (data.get("notes") or {}).get("path")
    if not p:
        sys.exit("notes.path missing from emptyos.toml")
    return Path(p)


def _frontmatter(fm: dict, body: str = "") -> str:
    """Render YAML frontmatter (block-style — flat-only is enforced).

    None values are *omitted* (not emitted as bare `key:`) — the vault
    YAML parser otherwise turns the empty value into an empty list and
    breaks downstream consumers that check `is None`.
    """
    lines = ["---"]
    for k, v in fm.items():
        if v is None:
            continue
        if isinstance(v, bool):
            lines.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
        elif isinstance(v, str):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    if body:
        lines.append(body)
    return "\n".join(lines)


def _write_note(path: Path, fm: dict, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_frontmatter(fm, body), encoding="utf-8")


def _kind_for(node_type: str) -> str:
    return {
        "substation": "substation",
        "rmu": "bus",
        "turbine": "turbine",
        "junction": "junction",
        "custom": "bus",
        "grouped": "bus",
    }.get(node_type, "bus")


def _scale_xy(x: float, y: float) -> tuple[float, float]:
    return round(x * CANVAS_SCALE, 1), round(y * CANVAS_SCALE, 1)


def _node_fm(n: dict, project_id: str) -> dict:
    ep = n.get("electricalProperties") or {}
    nx, ny = _scale_xy(n["x"], n["y"])
    kind = _kind_for(n.get("type", "bus"))
    is_slack = n.get("type") == "substation"
    fm: dict = {
        "id": n["id"],
        "tags": ["cable-node"],
        "project": project_id,
        "kind": kind,
        "label": n.get("label") or n["id"],
        "x": nx, "y": ny,
        "voltage_kv": ep.get("voltage"),
        "is_slack": is_slack,
        "created": _now(),
        "updated": _now(),
    }
    # Source nodes (turbines + grouped strings) inject power → negative load
    if kind in ("turbine",) or n.get("type") == "grouped":
        p_kw = float(ep.get("power") or 0) * 1000.0
        if p_kw > 0:
            pf = float(ep.get("powerFactor") or 0.95)
            fm["p_gen_kw"] = round(p_kw, 1)
            # Q from power factor (lagging convention)
            from math import tan, acos
            fm["q_gen_kvar"] = round(p_kw * tan(acos(pf)), 1)
    elif kind == "bus" and n.get("type") == "custom":
        # Met mast — tiny LV load
        p_kw = float(ep.get("power") or 0) * 1000.0
        if p_kw > 0:
            fm["p_load_kw"] = round(p_kw, 2)
    return fm


def _pick_library(cross_section: int) -> str | None:
    if cross_section in LIB_BY_SIZE:
        return LIB_BY_SIZE[cross_section]
    # Round up to next available size
    sizes = sorted(LIB_BY_SIZE)
    for s in sizes:
        if s >= cross_section:
            return LIB_BY_SIZE[s]
    return None


def _cable_fm(c: dict, project_id: str) -> dict:
    pp = c.get("physicalProperties") or {}
    sched = c.get("cableSchedule") or {}
    cs = int(pp.get("crossSection") or 240)
    lib_id = _pick_library(cs)
    return {
        "id": c["id"],
        "tags": ["cable"],
        "project": project_id,
        "label": sched.get("cableTag") or c["id"],
        "library_id": lib_id,
        "length_m": float(pp.get("totalLength") or 0) or None,
        "n_circuits": 1,
        "installation": "direct_buried",
        "bonding": "single_point",
        "burial_depth_m": 1.0,
        "spacing_mode": "trefoil",
        "grouped_cables": 3,
        "soil_thermal_resistivity_kmw": None,
        "ambient_temperature_c": None,
        "overrides": None,
        "created": _now(),
        "updated": _now(),
        "ampacity_a": None,
        "ampacity_method": None,
        "ampacity_at": None,
    }


def _edge_fm(e: dict, cable_for_edge: dict[str, str], project_id: str) -> dict:
    eid = e["id"]
    return {
        "id": eid,
        "tags": ["cable-edge"],
        "project": project_id,
        "from_node": e["source"],
        "to_node": e["target"],
        "length_m": None,
        "cable_id": cable_for_edge.get(eid),
        "kind": "cable",
        "created": _now(),
        "updated": _now(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC,
                        help="Legacy example JSON path")
    parser.add_argument("--bg", type=Path, default=DEFAULT_BG,
                        help="Background image to copy in")
    parser.add_argument("--project", default=DEFAULT_PROJECT_ID, help="Project id (slug)")
    parser.add_argument("--replace", action="store_true",
                        help="Wipe existing project dir before seeding")
    args = parser.parse_args()

    if args.src is None:
        sys.exit("--src required (or set EOS_DEMO_WINDFARM_SRC)")
    if not args.src.exists():
        sys.exit(f"source JSON not found: {args.src}")

    project_id = args.project

    vault = _read_vault_path()
    proj_dir = vault / "30_Resources" / "EmptyOS" / "cables" / project_id
    if proj_dir.exists() and args.replace:
        shutil.rmtree(proj_dir)
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "cables").mkdir(exist_ok=True)
    (proj_dir / "nodes").mkdir(exist_ok=True)
    (proj_dir / "edges").mkdir(exist_ok=True)

    data = json.loads(args.src.read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    cables = data.get("cables", [])

    # Copy background image (if available) into the project dir.
    bg_name = None
    if args.bg.exists():
        bg_name = "site-plan.jpg"
        shutil.copy2(args.bg, proj_dir / bg_name)

    # Project note
    proj_info = data.get("projectInfo") or {}
    project_fm = {
        "id": project_id,
        "name": proj_info.get("name") or "Demo Wind Farm 50 MW",
        "tags": ["cables-project"],
        "frequency_hz": 50.0,
        "ambient_temperature_c": 20.0,
        "soil_thermal_resistivity_kmw": 1.0,
        "conductor_max_temp_c": 90.0,
        "created": _now(),
        "updated": _now(),
        "n_cables": len(cables),
        # Background image hooks — topology.html reads these.
        "background_image": bg_name,
        "bg_x": 0,
        "bg_y": 0,
        "bg_w": int(data.get("canvasWidth", 2000) * CANVAS_SCALE),
        "bg_h": int(data.get("canvasHeight", 1500) * CANVAS_SCALE),
        "bg_opacity": 0.35,
    }
    body = (
        f"# {project_fm['name']}\n\n"
        f"Seeded from `example_wind_farm_design.json` "
        f"(legacy Cable-reticulation-tool reference data).\n\n"
        f"## Notes\n\n"
        f"{proj_info.get('description', '')}\n\n"
        f"- Voltage: {proj_info.get('voltage', '33 kV')}\n"
        f"- Capacity: {proj_info.get('totalCapacity', '50 MW')}\n"
        f"- Nodes: {len(nodes)} · Edges: {len(edges)} · Cables: {len(cables)}\n\n"
        f"## Tasks\n\n"
    )
    _write_note(proj_dir / f"{project_id}.md", project_fm, body)

    # Cables → write first so we can map edge.cable_id along the network
    # path. Legacy data ships `networkPath: [n1, n2, ...]` describing the
    # multi-hop route; we walk it pairwise and tag every edge between
    # consecutive nodes (junctions count). This is what makes the
    # schedule's `I (A)` / `V-drop %` columns join correctly back to the
    # topology — without it cables that route through junctions show
    # blank ampacity columns.
    edges_by_pair: dict[tuple[str, str], str] = {}
    for e in edges:
        edges_by_pair[(e["source"], e["target"])] = e["id"]
        edges_by_pair[(e["target"], e["source"])] = e["id"]

    cable_for_edge: dict[str, str] = {}
    for c in cables:
        fm = _cable_fm(c, project_id)
        _write_note(proj_dir / "cables" / f"{c['id']}.md", fm)
        path = c.get("networkPath") or []
        if len(path) < 2:
            # Fallback to start/end direct edge
            path = [c.get("startNodeId"), c.get("endNodeId")]
        for a, b in zip(path, path[1:]):
            eid = edges_by_pair.get((a, b))
            if eid:
                cable_for_edge[eid] = c["id"]

    # Nodes
    for n in nodes:
        fm = _node_fm(n, project_id)
        _write_note(proj_dir / "nodes" / f"{n['id']}.md", fm)

    # Edges (with cable_id fk where applicable)
    for e in edges:
        fm = _edge_fm(e, cable_for_edge, project_id)
        _write_note(proj_dir / "edges" / f"{e['id']}.md", fm)

    print(f"Seeded {project_id} -> {proj_dir}")
    print(f"  {len(nodes)} nodes · {len(edges)} edges · {len(cables)} cables")
    print(f"  background_image: {bg_name or '(none)'}")
    print(f"\nOpen: http://localhost:9000/cables/?id={project_id}")
    print(f"Topology: http://localhost:9000/cables/pages/topology.html?project={project_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
