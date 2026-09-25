"""Pin check_route_500 in BOTH directions.

A scanner sitting at zero on a healthy tree has proved nothing until it has
been watched going red for each shape it claims to cover
(`.claude/rules/audits.md`). The two RED cases below are the two real defects
that motivated the checker, reduced to their skeletons:

* `direct_arg` — the shape in `read-the-room` (4 routes, all reachable).
* `local_var` — the shape in `life-tree`. An earlier draft of the checker
  required the argument to mention `request.`, which caught `direct_arg` and
  was **blind to this one** — so this case is the regression pin on the rule
  itself, not just on the tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import check_route_500 as mod  # noqa: E402


RED = {
    "direct_arg": '''
@web_route("GET", "/api/items/{filename}")
async def api_item(self, request):
    filename = require_path_segment(request.path_params.get("filename", ""), "filename")
    return self.items.detail(filename)
''',
    "local_var": '''
@web_route("GET", "/api/nodes/{node_id}")
async def api_node(self, request):
    raw = request.path_params.get("node_id") or ""
    nid = require_path_segment(raw)
    return {"id": nid}
''',
    "guard_in_a_different_function": '''
@web_route("GET", "/api/a/{i}")
async def api_a(self, request):
    return {"i": require_path_segment(request.path_params.get("i", ""))}

def elsewhere(raw):
    return path_segment_error(raw, "i")
''',
}

GREEN = {
    "guarded_inline": '''
@web_route("GET", "/api/nodes/{node_id}")
async def api_node(self, request):
    raw = request.path_params.get("node_id") or ""
    refused = path_segment_error(raw, "node id")
    if refused:
        return {"error": refused}
    return {"id": require_path_segment(raw)}
''',
    "guarded_via_local_helper": '''
@web_route("POST", "/api/incidents/{did}/apply")
async def api_apply(self, request):
    did, err = _safe_seg(request.path_params.get("did", ""), "draft id")
    if err:
        return err
    return {"did": did}
''',
    "wrapped_in_try": '''
@web_route("GET", "/api/x/{i}")
async def api_x(self, request):
    try:
        i = require_path_segment(request.path_params.get("i", ""))
    except Exception:
        return {"error": "bad id"}
    return {"i": i}
''',
    "not_a_route_builder_raising_is_correct": '''
def node_path(self, id=""):
    return self._entity_path(require_path_segment(id))
''',
    "route_without_the_helper": '''
@web_route("GET", "/api/list")
async def api_list(self, request):
    return {"items": []}
''',
    "opted_out_at_the_call_site": '''
@web_route("GET", "/api/gen/{run_id}")
async def api_gen(self, request):
    """Fetch a generated run.

    route-500: ignore — run_id is minted server-side, never user-supplied.
    """
    return {"id": require_path_segment(request.path_params.get("run_id", ""))}
''',
}


@pytest.mark.parametrize("name", sorted(RED))
def test_flags_the_unguarded_shapes(name):
    assert mod.unguarded_routes(RED[name]), f"{name} should have been flagged"


@pytest.mark.parametrize("name", sorted(GREEN))
def test_passes_the_guarded_shapes(name):
    assert not mod.unguarded_routes(GREEN[name]), f"{name} should NOT be flagged"


def test_reports_line_and_function_name():
    hits = mod.unguarded_routes(RED["local_var"])
    assert len(hits) == 1
    _lineno, func = hits[0]
    assert func == "api_node"


def test_syntax_error_is_survivable_not_fatal():
    """A file mid-edit must not take the whole preflight down."""
    assert mod.unguarded_routes("def broken(:\n    pass") == []


def test_healthy_tree_is_clean():
    """The forward-looking half — this is what makes it a gate."""
    scanned, findings = mod.scan(ROOT / "apps")
    assert scanned > 0, "scanned nothing — the walk is broken, not the tree clean"
    assert findings == [], f"unguarded routes: {findings}"
