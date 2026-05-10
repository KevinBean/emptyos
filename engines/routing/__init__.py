"""engines/routing — graph routing on NetworkTopo.

Public surface:
    RoutingEngine       — kernel entry point; apps use self.engine("routing").
    find_path(topo, ..) — pure function; BFS shortest-hop path.
    RoutingResult       — pydantic result model.
"""

from .engine import RoutingEngine
from .bfs import find_path, RoutingResult

__all__ = ["RoutingEngine", "find_path", "RoutingResult"]
