"""Projects — date-cascade over the task dependency graph.

Dependency edges between tasks are recorded (`depends_on` / `blocks`) but inert:
moving a blocking task's deadline never shifted its dependents, and a dependent
due *before* its blocker went unnoticed. This adds the missing time layer — a
forward pass over the DAG that proposes the date shifts a move implies, refuses
to cascade a cyclic graph, and checks a project for already-inconsistent dates.

No duration model exists, so this is **date-constraint propagation**, not
classic duration-CPM: the one rule is "a dependent's due must be ≥ its
blocker's due." Everything is **propose-not-apply** (`.claude/rules/
proposed-action.md`) — preview returns the shifts, the user reviews, apply
writes them under the project write-lock with a stale-line guard.

Pure graph/date functions are module-level (unit-tested without a daemon); the
three routes bind onto `ProjectsApp` in app.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.markdown_tasks import set_due, task_text, text_matches
from emptyos.sdk.utils import extract_due

if TYPE_CHECKING:
    from .app import ProjectsApp  # noqa: F401 — for type hints only


# ─── Bind to ProjectsApp class as ────────────────────────────────────
#   api_cascade_check   = _scheduling.api_cascade_check    # GET  …/cascade/check
#   api_cascade_preview = _scheduling.api_cascade_preview  # POST …/cascade/preview
#   api_cascade_apply   = _scheduling.api_cascade_apply    # POST …/cascade/apply
# Adding a route? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _due_of(task: dict) -> str:
    """A task's current due date (📅 / due:) or ''."""
    return extract_due(task.get("text", ""))


def build_edges(task_list: list[dict]) -> list[tuple[int, int]]:
    """Precedence edges (blocker_line → dependent_line), deduped.

    Both directions of the relation collapse to the same edge: A ``blocks`` B
    and B ``depends_on`` A both mean A precedes B. Only edges between two real
    tasks (resolved line ≥ 0) are kept.
    """
    lines = {t["line"] for t in task_list}
    seen: set[tuple[int, int]] = set()
    for t in task_list:
        me = t["line"]
        for b in t.get("blocks", []):
            ln = b.get("line", -1)
            if ln in lines and ln != me:
                seen.add((me, ln))
        for d in t.get("depends_on", []):
            ln = d.get("line", -1)
            if ln in lines and ln != me:
                seen.add((ln, me))
    return sorted(seen)


def detect_cycle(edges: list[tuple[int, int]], nodes: set[int]) -> list[int] | None:
    """Kahn topsort; return the nodes left in a cycle, or None if acyclic."""
    indeg = {n: 0 for n in nodes}
    adj: dict[int, list[int]] = {n: [] for n in nodes}
    for a, b in edges:
        adj[a].append(b)
        indeg[b] += 1
    queue = [n for n in nodes if indeg[n] == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen == len(nodes):
        return None
    return sorted(n for n in nodes if indeg[n] > 0)


def _topo_order(edges: list[tuple[int, int]], nodes: set[int]) -> list[int]:
    """Kahn order (caller has already ruled out cycles)."""
    indeg = {n: 0 for n in nodes}
    adj: dict[int, list[int]] = {n: [] for n in nodes}
    for a, b in edges:
        adj[a].append(b)
        indeg[b] += 1
    queue = sorted(n for n in nodes if indeg[n] == 0)
    order: list[int] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for m in sorted(adj[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
                queue.sort()
    return order


def _identity(full_text: str) -> str:
    """The stable task identity used by the apply-time stale guard.

    ``_parse_tasks`` keeps the whole line text (with the 📅 date); the apply
    guard compares against ``task_text(line)``, which strips the date and other
    trailing markers. Reconstruct a line and run it through the same stripper so
    the two always agree — the date is exactly what a cascade changes.
    """
    return task_text(f"- [ ] {full_text}")


def _row(task_by_line: dict[int, dict], line: int, old: str, new: str) -> dict:
    return {"line": line, "text": _identity(task_by_line[line]["text"]),
            "old_due": old, "new_due": new}


def _graph_context(task_list: list[dict]):
    """Shared setup for the cascade + violation passes.

    Returns ``(task_by_line, edges, nodes, current_dues, cycle_nodes)`` —
    ``cycle_nodes`` is None when acyclic; both callers refuse to proceed on a
    cycle (with their own result shape).
    """
    task_by_line = {t["line"]: t for t in task_list}
    edges = build_edges(task_list)
    nodes = set(task_by_line)
    current = {ln: _due_of(t) for ln, t in task_by_line.items()}
    return task_by_line, edges, nodes, current, detect_cycle(edges, nodes)


def cascade(task_list: list[dict], moved_line: int, new_due: str) -> dict:
    """Forward-propagate a task's new due through its dependents.

    Returns ``{ok, shifts, cycle, cycle_nodes}``. Each shift is
    ``{line, text, old_due, new_due}`` — a dependent whose due now falls before
    a (possibly just-moved) blocker, proposed forward to the blocker's due. A
    dependent with no due date that a blocker now constrains is proposed too
    (``old_due == ""``). Writes nothing.
    """
    task_by_line, edges, nodes, current, cyc = _graph_context(task_list)
    if moved_line not in task_by_line:
        return {"ok": False, "error": "task not found", "shifts": []}
    if cyc:
        return {"ok": False, "error": "dependency cycle — cannot cascade",
                "cycle": True, "cycle_nodes": cyc, "shifts": []}

    preds: dict[int, list[int]] = {n: [] for n in nodes}
    for a, b in edges:
        preds[b].append(a)

    new_map = dict(current)
    new_map[moved_line] = new_due
    shifts = []
    for n in _topo_order(edges, nodes):
        if n == moved_line:
            continue
        constraint = max((new_map[p] for p in preds[n] if new_map[p]), default="")
        if constraint and (not current[n] or current[n] < constraint):
            new_map[n] = constraint
            shifts.append(_row(task_by_line, n, current[n], constraint))
        else:
            new_map[n] = current[n]
    return {"ok": True, "cycle": False, "shifts": shifts,
            "moved": {"line": moved_line, "text": _identity(task_by_line[moved_line]["text"]),
                      "old_due": current[moved_line], "new_due": new_due}}


def find_violations(task_list: list[dict]) -> dict:
    """Dependents already due before a blocker — proposed forward to the latest
    blocker due. ``{ok, cycle, violations}``; each violation is a shift row."""
    task_by_line, edges, nodes, current, cyc = _graph_context(task_list)
    if cyc:
        return {"ok": False, "error": "dependency cycle", "cycle": True,
                "cycle_nodes": cyc, "violations": []}

    worst: dict[int, str] = {}
    for a, b in edges:
        if current[a] and current[b] and current[b] < current[a]:
            if b not in worst or current[a] > worst[b]:
                worst[b] = current[a]
    violations = [_row(task_by_line, b, current[b], new) for b, new in sorted(worst.items())]
    return {"ok": True, "cycle": False, "violations": violations}


# ─── Routes (bound onto ProjectsApp) ─────────────────────────────────


async def _tasks_for(self, project_id: str):
    """(target_path, resolved_task_list) or (None, None) when absent."""
    target = self._find_project_file(project_id)
    if not target:
        return None, None
    content = await self.read(str(target))
    _, _, task_list = self._parse_tasks(content)
    return target, self._resolve_dependencies(task_list)


@web_route("GET", "/api/projects/{id}/cascade/check")
async def api_cascade_check(self, request):
    """Dates already inconsistent with the dependency edges (dependent due before
    its blocker). Read-only — proposes fixes, writes nothing."""
    project_id = request.path_params.get("id", "")
    _, task_list = await _tasks_for(self, project_id)
    if task_list is None:
        return {"error": "Project not found"}
    return find_violations(task_list)


@web_route("POST", "/api/projects/{id}/cascade/preview")
async def api_cascade_preview(self, request):
    """Preview the shifts moving one task's due to ``due`` would imply. Body:
    ``{line, due}``. Read-only."""
    project_id = request.path_params.get("id", "")
    body = await self.read_json(request)
    try:
        line = int(body.get("line", -1))
    except (TypeError, ValueError):
        return {"error": "line must be an integer"}
    due = str(body.get("due", "")).strip()
    if not due:
        return {"error": "due required"}
    _, task_list = await _tasks_for(self, project_id)
    if task_list is None:
        return {"error": "Project not found"}
    return cascade(task_list, line, due)


@web_route("POST", "/api/projects/{id}/cascade/apply")
async def api_cascade_apply(self, request):
    """Apply reviewed shifts. Body: ``{shifts: [{line, text, new_due}]}``. Each
    line is re-verified against its expected text (stale guard) before its 📅 is
    rewritten, all under one project write-lock."""
    project_id = request.path_params.get("id", "")
    body = await self.read_json(request)
    shifts = body.get("shifts") or []
    if not isinstance(shifts, list) or not shifts:
        return {"error": "shifts required"}

    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    applied = 0
    skipped = 0
    async with self.write_lock(f"projects:{project_id}"):
        content = await self.read(str(target))
        lines = content.split("\n")
        for s in shifts:
            if not isinstance(s, dict):
                skipped += 1
                continue
            try:
                ln = int(s.get("line", -1))
            except (TypeError, ValueError):
                skipped += 1
                continue
            new_due = str(s.get("new_due", "")).strip()
            expected = str(s.get("text", "")).strip()
            if ln < 0 or ln >= len(lines) or not new_due:
                skipped += 1
                continue
            if expected and not text_matches(lines[ln], expected):
                skipped += 1  # the line moved since preview — don't clobber
                continue
            lines[ln] = set_due(lines[ln], new_due)
            applied += 1
        if applied:
            await self.write(str(target), "\n".join(lines))
    if applied:
        await self.emit("projects:dates_cascaded", {"id": project_id, "applied": applied})
    return {"ok": True, "applied": applied, "skipped": skipped}
