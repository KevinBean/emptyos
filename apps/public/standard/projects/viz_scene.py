"""Projects — Mermaid Gantt scene generation via the artifact capability.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: composing the LLM brief that turns a project + its task
list into a Mermaid Gantt diagram, and the cached-or-generate flow that
calls `self.artifact(...)` and persists the resulting record_id back to
the project's frontmatter.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._find_project_file``,
``self._read_project``, ``self._parse_tasks`` (defined on the spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import ProjectsApp  # noqa: F401 — for type hints only


# ─── Bind to ProjectsApp class as ────────────────────────────────────
#   _project_scene_prompt   = _viz_scene._project_scene_prompt
#   _viz_artifact_path_for  = _viz_scene._viz_artifact_path_for
#   api_visualize_scene     = _viz_scene.api_visualize_scene
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _project_scene_prompt(self, p: dict, task_list: list[dict]) -> str:
    """Compose a Mermaid Gantt brief from a project record + task list.

    Pulls dates from frontmatter (`created`, `deadline`) and from any
    tasks carrying a due-date suffix. Mermaid Gantt expects YYYY-MM-DD
    dates; falls back to a synthetic 90-day window when a project has
    no deadline (mirrors `extended.py::api_timeline` defaults).
    """
    from datetime import date, datetime, timedelta

    today = date.today().isoformat()
    start = (p.get("created") or "").strip() or today
    deadline = (p.get("deadline") or "").strip()
    if not deadline:
        try:
            s = datetime.strptime(start, "%Y-%m-%d").date()
            deadline = (s + timedelta(days=90)).isoformat()
        except Exception:
            deadline = today

    dated = []
    for t in task_list or []:
        due = (t.get("due") or t.get("deadline") or "").strip()
        if due:
            dated.append({
                "text": (t.get("text") or "").strip()[:80],
                "due": due,
                "done": bool(t.get("done")),
            })

    lines = [
        f"Render a Mermaid Gantt chart for the project '{p.get('name') or p.get('id')}'.",
        "",
        f"- Project starts: {start}",
        f"- Project deadline: {deadline}",
        f"- Status: {p.get('status', 'unknown')}",
        f"- Progress: {p.get('progress', 0)}% ({p.get('done_tasks', 0)} of {p.get('total_tasks', 0)} tasks done)",
    ]
    if p.get("stage"):
        lines.append(f"- Current stage: {p['stage']} ({p.get('stage_index', 0) + 1} of {p.get('stage_total', 1)})")
    if p.get("next_action"):
        lines.append(f"- Next action: {p['next_action']}")

    if dated:
        lines.append("")
        lines.append("Tasks with due dates (use these as Gantt rows; mark `done` for completed):")
        for t in dated[:20]:
            state = " (done)" if t["done"] else ""
            lines.append(f"  - {t['due']}: {t['text']}{state}")
    else:
        lines.append("")
        lines.append(
            "No per-task due dates available — render the project as a single span "
            "from start → deadline with a 'done' segment proportional to progress."
        )

    lines.append("")
    lines.append(
        "Use a single `gantt` block with `dateFormat YYYY-MM-DD`, a sensible "
        "`title`, a `section` per logical grouping (or one default section if "
        "tasks are flat), and `done`/`active`/`crit` modifiers where the data "
        "implies them. Keep the chart readable on one screen."
    )
    return "\n".join(lines)


def _viz_artifact_path_for(self, record_id: str) -> Path | None:
    """Resolve a viz record id to its scene.html on disk. Returns None
    if the artifact has been deleted (i.e. cached pointer is stale)."""
    if not record_id:
        return None
    # Mirrors apps/viz/app.py::_record_dir — keep in sync if viz moves.
    base = "30_Resources/EmptyOS/viz"  # viz's vault_config default
    p = self.vault_root / base / "outputs" / record_id / "scene.html"
    return p if p.exists() else None


@web_route("POST", "/api/projects/{project_id}/visualize-scene")
async def api_visualize_scene(self, request) -> dict:
    """Get-or-generate a Mermaid Gantt scene for the project.

    Default: returns the cached scene from `viz_scene_record` if it
    still exists on disk (no LLM call, instant). Pass `{"force": true}`
    in the body to regenerate. Either way the response shape is:
        {ok, record_id, html_path, embed_url, generated_at, cached, prompt?}

    Persists `viz_scene_record` + `viz_scene_at` on the project
    frontmatter on every fresh generation.
    """
    from datetime import UTC, datetime

    pid = request.path_params.get("project_id", "")
    target = self._find_project_file(pid)
    if not target or not target.exists():
        return {"ok": False, "error": f"project {pid} not found"}

    try:
        body = await request.json()
    except Exception:
        body = {}
    force = bool(body.get("force"))

    vault_rel = self.vault_rel(target)

    fm = self.vault_get_properties(vault_rel) if vault_rel else {}
    cached_id = (fm or {}).get("viz_scene_record", "")
    cached_at = (fm or {}).get("viz_scene_at", "")
    if not force and cached_id:
        cached_file = self._viz_artifact_path_for(cached_id)
        if cached_file is not None:
            return {
                "ok": True,
                "cached": True,
                "record_id": cached_id,
                "generated_at": cached_at,
                "embed_url": f"/viz/api/html/{cached_id}",
            }

    p = self._read_project(target)
    if not p:
        return {"ok": False, "error": f"failed to read project {pid}"}

    try:
        content = target.read_text(encoding="utf-8")
        _, _, task_list = self._parse_tasks(content)
    except Exception:
        task_list = []

    prompt = self._project_scene_prompt(p, task_list)
    try:
        html_path = await self.artifact(prompt, shape="mermaid")
    except Exception as exc:
        return {"ok": False, "error": f"artifact capability call failed: {exc}"}
    if not html_path:
        return {
            "ok": False,
            "error": "viz returned no artifact — provider may be unavailable",
        }

    record_id = html_path.rstrip("/").rsplit("/", 2)[-2]
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if vault_rel:
        try:
            self.vault_update(vault_rel, {"viz_scene_record": record_id, "viz_scene_at": now})
        except Exception:
            pass

    await self.emit("projects:scene_visualized", {"project": pid, "record_id": record_id})
    return {
        "ok": True,
        "cached": False,
        "record_id": record_id,
        "html_path": html_path,
        "embed_url": f"/viz/api/html/{record_id}",
        "generated_at": now,
        "prompt": prompt,
    }
