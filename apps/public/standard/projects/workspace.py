"""Projects — standalone workspace page + its capability endpoints.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the full-page project workspace at /projects/workspace/<id>
(``page_workspace``) and the per-project endpoints that power its multi-pane
work surface — command-center overview, project-grounded AI ask, inline doc
read/edit, 4D timeline, and related links. These compose existing helpers
(reading.py task/parse/context, dependencies.py, BaseApp.timeline/think) — no
new domain logic.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: reading.py helpers (_find_project_file,
_find_project_dir, _read_project, _parse_tasks, _days_until,
load_project_context, project_path), dependencies via self._resolve_dependencies,
dev_features._parse_sprints. Do not import from ``.app`` (it imports us).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import parse_frontmatter, web_route

from .shared import _parse_sprints

if TYPE_CHECKING:
    from .app import ProjectsApp  # noqa: F401 — for type hints only


# ─── Bind to ProjectsApp class as ────────────────────────────────────
#   page_workspace     = _workspace.page_workspace        # @web_route
#   api_overview       = _workspace.api_overview          # @web_route
#   api_ask            = _workspace.api_ask               # @web_route
#   api_ask_stream     = _workspace.api_ask_stream        # @web_route
#   api_get_doc        = _workspace.api_get_doc           # @web_route
#   api_save_doc       = _workspace.api_save_doc          # @web_route
#   api_timeline4d     = _workspace.api_timeline4d        # @web_route
#   api_related        = _workspace.api_related           # @web_route
#   _project_doc_path  = _workspace._project_doc_path
# (_split_frontmatter is a module-level pure helper used only within this
#  module — not bound to the class.)
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


ASK_SYSTEM = """You are a focused project assistant. The user is working on ONE
project; its notes and documents are provided as context. Answer the user's
question about THIS project — its tasks, status, blockers, decisions, docs, and
what to do next.

Rules:
- Ground every claim in the provided project context. If the context doesn't
  contain the answer, say so plainly — never invent tasks, dates, or facts.
- Be concise and concrete. Prefer specific next actions over generic advice.
- When asked "what next", propose 1-3 concrete moves drawn from the open tasks
  and blockers in the context, in priority order.
- Use short markdown (bullets, bold) — no headers, no preamble, no sign-off.
- Do not repeat the whole project back at the user."""

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


def _split_frontmatter(content: str) -> tuple[str, str]:
    """Split ``content`` into (frontmatter_block_including_fences, body).

    Returns ("", content) when there is no leading ``---`` frontmatter. The
    frontmatter block keeps its closing ``---`` + trailing newline so
    ``fm + body`` round-trips exactly.
    """
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            after = end + 3
            # keep one trailing newline with the frontmatter block
            if content[after:after + 1] == "\n":
                after += 1
            return content[:after], content[after:]
    return "", content


def _project_doc_path(self, project_id: str, rel: str) -> Path | None:
    """Resolve ``rel`` to an .md file INSIDE the project directory.

    Path-safety: the resolved path must stay within the project dir (no
    traversal) and be a markdown file. Returns None on any violation — the
    only client-supplied path that reaches a write, so the guard is mandatory.
    """
    rel_norm = (rel or "").replace("\\", "/")
    proj_dir = self._find_project_dir(project_id)
    if not proj_dir:
        # flat-file project: only its own main note is addressable. api_docs
        # gives it rel_path = the bare filename; also accept the vault-rel path.
        f = self._find_project_file(project_id)
        if f and rel_norm in (f.name, self.vault_rel(f)):
            return f
        return None
    rel_norm = rel_norm.lstrip("/")
    if not rel_norm or not rel_norm.endswith(".md"):
        return None
    try:
        target = (proj_dir / rel_norm).resolve()
        base = proj_dir.resolve()
    except Exception:
        return None
    if base != target and base not in target.parents:
        return None  # traversal attempt
    return target


@web_route("GET", "/workspace/{id}")
async def page_workspace(self, request):
    """Serve the standalone full-page project workspace (multi-pane work surface).

    Bookmarkable at /projects/workspace/<id>. The id is parsed client-side from
    the path; the HTML is static (hot-reloads). HTMLResponse bypasses JSON
    encoding (server.py route dispatch) — same pattern as boards/app.py. Asset
    refs inside workspace.html are absolute (this page is one level below
    /projects/).
    """
    from starlette.responses import HTMLResponse

    page = Path(__file__).parent / "pages" / "workspace.html"
    try:
        return HTMLResponse(page.read_text(encoding="utf-8"))
    except Exception:
        return HTMLResponse("<h1>Workspace unavailable</h1>", status_code=500)


@web_route("GET", "/api/projects/{id}/overview")
async def api_overview(self, request):
    """Command-center synthesis for the workspace rail — no LLM.

    Returns next unblocked moves, ready/blocked counts, deadline countdown,
    progress, open/done, the active sprint, and recent-activity count. Reuses
    the same task parse + dependency resolution as api_project_detail.
    """
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target or not target.exists():
        return {"error": "Project not found"}
    p = self._read_project(target)
    if not p:
        return {"error": "Failed to read project"}

    content = await self.read(str(target))
    _, _, task_list = self._parse_tasks(content)
    task_list = self._resolve_dependencies(task_list)

    open_tasks = [t for t in task_list if not t["done"]]
    ready = [t for t in open_tasks if t.get("ready")]
    blocked = [t for t in open_tasks if not t.get("ready")]

    # Next moves: ready tasks first (or all open if the project has no deps).
    pool = ready if ready else open_tasks
    next_actions = [{"text": t["text"], "line": t["line"]} for t in pool[:5]]

    # Active sprint (dev projects).
    active_sprint = None
    try:
        sprints = _parse_sprints(content)
        active = next((s for s in sprints if s.get("status") == "active"), None)
        if active:
            active_sprint = {"num": active.get("num"), "name": active.get("name", "")}
    except Exception:
        active_sprint = None

    # Recent activity: count of dated edits/completions in the last 30 days.
    recent_activity = 0
    cutoff = date.today()
    try:
        for line in content.split("\n"):
            m = re.search(r"✅\s*(\d{4}-\d{2}-\d{2})", line)
            if m:
                try:
                    d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                    if (cutoff - d).days <= 30:
                        recent_activity += 1
                except ValueError:
                    pass
    except Exception:
        pass

    return {
        "name": p["name"],
        "status": p["status"],
        "deadline": p.get("deadline", ""),
        "days_left": p.get("days_until_deadline"),
        "overdue": p.get("overdue", False),
        "progress": p.get("progress", 0),
        "open": len(open_tasks),
        "done": p.get("done_tasks", 0),
        "total": p.get("total_tasks", 0),
        "ready_count": len(ready),
        "blocked_count": len(blocked),
        "has_dependencies": any(t.get("depends_on") or t.get("blocks") for t in task_list),
        "next_actions": next_actions,
        "next_action_note": p.get("next_action", ""),
        "active_sprint": active_sprint,
        "recent_activity": recent_activity,
    }


@web_route("POST", "/api/projects/{id}/ask")
async def api_ask(self, request):
    """Project-grounded AI companion. Body: {question, history?}.

    Grounds self.think in load_project_context (main note + docs/). history is
    a client-sent list of {role, content} for multi-turn. Returns
    {answer, provenance}. User-initiated per call (Rule 19 'explicit per-request').
    """
    project_id = request.path_params.get("id", "")
    body = await request.json()
    question = (body.get("question") or "").strip()
    history = body.get("history") or []
    if not question:
        return {"error": "Empty question"}

    ctx = await self.load_project_context(project_id, max_chars=24_000, max_files=8)
    if not ctx:
        return {"error": "Project not found"}

    messages = _ask_messages(question, history, ctx)

    try:
        # Low temperature — grounded Q&A; honors the "never invent" rule in ASK_SYSTEM.
        answer = await self.think(messages=messages, domain="text", temperature=0.3)
    except Exception as e:
        return {"error": f"AI unavailable: {e}"}
    if isinstance(answer, dict):
        answer = answer.get("text") or answer.get("answer") or ""
    return {"answer": (answer or "").strip(), "provenance": self.last_provenance()}


def _ask_messages(question: str, history: list, ctx: str) -> list[dict]:
    """Build the grounded message list shared by api_ask and api_ask_stream."""
    messages = [{"role": "system", "content": ASK_SYSTEM + "\n\n# Project context\n\n" + ctx}]
    for turn in history[-6:]:  # bound the prompt to the last 6 turns
        role = turn.get("role")
        cont = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and cont:
            messages.append({"role": role, "content": cont})
    messages.append({"role": "user", "content": question})
    return messages


@web_route("POST", "/api/projects/{id}/ask-stream")
async def api_ask_stream(self, request):
    """Streaming twin of api_ask — yields ndjson chunks for progressive render.

    Lines: {"type":"chunk","text":...} repeated, then {"type":"done","provenance":...},
    or {"type":"error","error":...}. Same grounding + Rule-19 posture as api_ask
    (user-initiated per call). The non-streaming api_ask stays for export/offline.
    """
    from starlette.responses import StreamingResponse

    project_id = request.path_params.get("id", "")
    body = await request.json()
    question = (body.get("question") or "").strip()
    history = body.get("history") or []
    ctx = await self.load_project_context(project_id, max_chars=24_000, max_files=8)

    async def stream():
        if not question:
            yield json.dumps({"type": "error", "error": "Empty question"}) + "\n"
            return
        if not ctx:
            yield json.dumps({"type": "error", "error": "Project not found"}) + "\n"
            return
        messages = _ask_messages(question, history, ctx)
        try:
            async for chunk in self.think_stream(messages=messages, domain="text", temperature=0.3):
                # think_stream yields {"text": str, "done": bool} dicts — extract
                # the text, else the client renders the dict as "[object Object]".
                text = chunk.get("text", "") if isinstance(chunk, dict) else (chunk or "")
                if text:
                    yield json.dumps({"type": "chunk", "text": text}, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "error": f"AI unavailable: {e}"}, ensure_ascii=False) + "\n"
            return
        yield json.dumps(
            {"type": "done", "provenance": self.last_provenance()},
            ensure_ascii=False, default=str,
        ) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@web_route("GET", "/api/projects/{id}/doc")
async def api_get_doc(self, request):
    """Read a project doc's markdown body for the inline editor.

    Query: ?path=<vault-or-project-relative>. Returns {name, rel, body}. Body is
    the content after frontmatter (frontmatter is preserved on save, not edited).
    """
    project_id = request.path_params.get("id", "")
    rel = request.query_params.get("path", "")
    target = self._project_doc_path(project_id, rel)
    if not target or not target.exists():
        return {"error": "Document not found"}
    body = self.vault_read_body(self.vault_rel(target))
    return {"name": target.name, "rel": rel, "body": body}


@web_route("POST", "/api/projects/{id}/doc")
async def api_save_doc(self, request):
    """Save a project doc body (preserving its frontmatter). Body: {path, body}.

    Serialized per-file via write_lock to avoid the read-modify-write race
    (CLAUDE.md § Development Gotchas). Frontmatter is re-attached verbatim.
    """
    project_id = request.path_params.get("id", "")
    body_in = await request.json()
    rel = body_in.get("path", "")
    new_body = body_in.get("body", "")
    target = self._project_doc_path(project_id, rel)
    if not target or not target.exists():
        return {"error": "Document not found"}

    # Body replacement + frontmatter preservation + write-lock live in the SDK
    # (BaseApp.vault_set_body — write-side inverse of vault_read_body).
    result = await self.vault_set_body(self.vault_rel(target), new_body)
    if result.get("error"):
        return {"error": "Failed to save document"}

    import asyncio

    asyncio.create_task(self.emit("projects:doc_saved", {"id": project_id, "doc": target.name}))
    return {"ok": True, "name": target.name}


@web_route("GET", "/api/projects/{id}/timeline4d")
async def api_timeline4d(self, request):
    """Past / future / now for the project's main note (BaseApp.timeline)."""
    project_id = request.path_params.get("id", "")
    rel = await self.project_path(project_id)
    if not rel:
        return {"error": "Project not found", "past": [], "future": [], "now": {}}
    try:
        return await self.timeline(rel)
    except Exception:
        return {"past": [], "future": [], "now": {}}


@web_route("GET", "/api/projects/{id}/related")
async def api_related(self, request):
    """Related entities for the workspace rail.

    Returns {related_projects, links}. related_projects from the note's
    ``related:`` frontmatter (resolved to known project ids); links from
    [[wikilinks]] in the note body. Inbound backlinks are deferred (no O(1)
    index today — see plan).
    """
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target or not target.exists():
        return {"error": "Project not found", "related_projects": [], "links": []}
    try:
        content = await self.read(str(target))
    except Exception:
        return {"related_projects": [], "links": []}

    fm = parse_frontmatter(content)
    raw_related = fm.get("related", "")
    if isinstance(raw_related, str):
        related_ids = [r.strip().strip("[]") for r in re.split(r"[,\n]", raw_related) if r.strip()]
    elif isinstance(raw_related, list):
        related_ids = [str(r).strip().strip("[]") for r in raw_related if str(r).strip()]
    else:
        related_ids = []

    related_projects = []
    for rid in related_ids:
        f = self._find_project_file(rid)
        if f:
            related_projects.append({"id": f.stem, "name": f.stem.replace("-", " ")})

    # Outbound wikilinks in the body (deduped, capped).
    _, body = _split_frontmatter(content)
    seen = set()
    links = []
    for m in _WIKILINK_RE.finditer(body):
        slug = m.group(1).strip()
        if slug and slug.lower() not in seen:
            seen.add(slug.lower())
            links.append({"target": slug})
        if len(links) >= 30:
            break

    return {"related_projects": related_projects, "links": links}
