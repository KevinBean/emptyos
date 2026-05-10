"""RoutingEngine — kernel entry point.

Apps access via `self.engine("routing")`. Methods:

  find_path(topology, source, target, *, waypoints=None, exclude_edges=None)
      BFS shortest-hop path. Returns a RoutingResult with .found,
      .path (node ids), .edges (edge ids), .total_length_m, .notes.

  available() -> bool
      Always True (pure-Python).
"""

from __future__ import annotations

from emptyos.sdk import BaseEngine

from engines.models import NetworkTopo

from .bfs import RoutingResult, find_path


class RoutingEngine(BaseEngine):
    name = "routing"

    async def init(self) -> None:
        return None

    async def available(self) -> bool:
        return True

    async def health_check(self) -> dict:
        return {
            "status": "ok",
            "available": True,
            "engine_version": self.manifest.raw.get("engine", {}).get("version", "?"),
            "method": "bfs",
        }

    def find_path(
        self,
        topology: NetworkTopo | dict,
        source: str,
        target: str,
        *,
        waypoints: list[str] | None = None,
        exclude_edges: list[str] | None = None,
    ) -> RoutingResult:
        if isinstance(topology, dict):
            topology = NetworkTopo(**topology)
        return find_path(
            topology,
            source,
            target,
            waypoints=waypoints,
            exclude_edges=exclude_edges,
        )
