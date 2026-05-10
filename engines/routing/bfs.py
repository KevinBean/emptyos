"""BFS shortest-path on a NetworkTopo.

Treats edges as undirected (cable network is electrically bidirectional).
Returns hop-shortest path; if multiple equal-hop paths exist, the one
discovered first by BFS wins. Supports:

  - waypoints: must visit each in order (source → wp1 → wp2 → ... → target)
  - exclude_edges: edge ids to treat as removed (re-routing around faults)

Total length is summed from EdgeRecord.length_m where present; segments
without length contribute 0 and the result emits a note.
"""

from __future__ import annotations

from collections import defaultdict, deque

from pydantic import BaseModel, Field

from engines.models import NetworkTopo


class RoutingResult(BaseModel):
    found: bool
    path: list[str] = Field(default_factory=list, description="Node ids in order")
    edges: list[str] = Field(default_factory=list, description="Edge ids traversed")
    total_length_m: float | None = None
    hops: int = 0
    notes: list[str] = Field(default_factory=list)


def _bfs_segment(
    adj: dict[str, list[tuple[str, str]]],
    excluded: set[str],
    source: str,
    target: str,
) -> tuple[list[str], list[str]] | None:
    """Single BFS source→target. Returns (nodes, edges) or None."""
    if source == target:
        return [source], []
    parent: dict[str, tuple[str, str]] = {}  # child -> (parent, edge_id)
    visited = {source}
    q: deque[str] = deque([source])
    while q:
        u = q.popleft()
        for v, eid in adj[u]:
            if eid in excluded or v in visited:
                continue
            visited.add(v)
            parent[v] = (u, eid)
            if v == target:
                # Reconstruct.
                nodes = [target]
                edges: list[str] = []
                cur = target
                while cur in parent:
                    p, e = parent[cur]
                    edges.append(e)
                    nodes.append(p)
                    cur = p
                nodes.reverse()
                edges.reverse()
                return nodes, edges
            q.append(v)
    return None


def find_path(
    topology: NetworkTopo,
    source: str,
    target: str,
    *,
    waypoints: list[str] | None = None,
    exclude_edges: list[str] | None = None,
) -> RoutingResult:
    node_ids = {n.id for n in topology.nodes}
    if source not in node_ids:
        return RoutingResult(found=False, notes=[f"source '{source}' not in topology"])
    if target not in node_ids:
        return RoutingResult(found=False, notes=[f"target '{target}' not in topology"])
    for wp in waypoints or []:
        if wp not in node_ids:
            return RoutingResult(
                found=False, notes=[f"waypoint '{wp}' not in topology"]
            )

    adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    edge_lookup: dict[str, tuple[str, str, float | None]] = {}
    for e in topology.edges:
        adj[e.from_node].append((e.to_node, e.id))
        adj[e.to_node].append((e.from_node, e.id))
        edge_lookup[e.id] = (e.from_node, e.to_node, e.length_m)

    excluded = set(exclude_edges or [])
    waypoints = waypoints or []

    # Stitch source → wp1 → wp2 → ... → target. Each leg uses BFS.
    sequence = [source, *waypoints, target]
    full_nodes: list[str] = []
    full_edges: list[str] = []
    for a, b in zip(sequence, sequence[1:]):
        seg = _bfs_segment(adj, excluded, a, b)
        if seg is None:
            return RoutingResult(
                found=False,
                notes=[f"no path from '{a}' to '{b}' (excluded={sorted(excluded)})"],
            )
        nodes, edges = seg
        if full_nodes:
            full_nodes.extend(nodes[1:])  # avoid duplicating join node
        else:
            full_nodes.extend(nodes)
        full_edges.extend(edges)

    # Total length — None contributions emit a single note.
    total = 0.0
    missing_len: list[str] = []
    for eid in full_edges:
        _, _, L = edge_lookup[eid]
        if L is None:
            missing_len.append(eid)
        else:
            total += L

    notes: list[str] = []
    if missing_len:
        notes.append(
            f"length missing on edge(s) {missing_len}; total_length_m omits them"
        )
    return RoutingResult(
        found=True,
        path=full_nodes,
        edges=full_edges,
        total_length_m=total if not missing_len else (total or None),
        hops=len(full_edges),
        notes=notes,
    )
