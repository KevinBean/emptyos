"""AgentApp — chat projects and conversation search (desktop GUI plan, B2).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the project routes (a project = a name + standing instructions
that every chat in it receives), the conversation search route, the two plain
verbs other apps call (portal's folder migration), and the system-prompt block
a session's project contributes to a turn. Storage is ChatSessionStore's
opt-in projects table + FTS5 search index (``emptyos/sdk/chat_session.py``).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._sessions`` / ``self._get_session`` (SessionMixin).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import AgentApp  # noqa: F401 — for type hints only


# ─── Bind to AgentApp class as ──────────────────────────────────────────
#   api_list_projects    = _projects.api_list_projects
#   api_create_project   = _projects.api_create_project
#   api_update_project   = _projects.api_update_project
#   api_delete_project   = _projects.api_delete_project
#   api_search           = _projects.api_search
#   create_chat_project  = _projects.create_chat_project
#   assign_chat_project  = _projects.assign_chat_project
#   _project_block       = _projects._project_block
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

#: Caps on what a project stores. Instructions ride in the system prompt of
#: every turn in the project, so an unbounded paste would tax every call.
NAME_MAX = 120
INSTRUCTIONS_MAX = 8000
#: Sessions per search answer — a sidebar list, read top-down; past a screenful
#: the query needs narrowing, not more rows.
SEARCH_LIMIT = 30

PROJECT_BLOCK = """\
This conversation belongs to the project "{name}". The user's standing instructions for every chat in it:
{instructions}"""


def _clean(data: dict) -> tuple[dict, str]:
    """Validated name/instructions from a request body, or an error message."""
    out = {}
    if "name" in data:
        name = " ".join(str(data.get("name") or "").split())
        if not name:
            return {}, "a project needs a name"
        if len(name) > NAME_MAX:
            return {}, f"name is longer than {NAME_MAX} characters"
        out["name"] = name
    if "instructions" in data:
        text = str(data.get("instructions") or "").strip()
        if len(text) > INSTRUCTIONS_MAX:
            return {}, f"instructions are longer than {INSTRUCTIONS_MAX} characters"
        out["instructions"] = text
    return out, ""


@web_route("GET", "/api/projects")
async def api_list_projects(self, request):
    return {"projects": self._sessions.list_projects()}


@web_route("POST", "/api/projects")
async def api_create_project(self, request):
    """Body: {name, instructions?}."""
    data = await self.safe_json(request)
    return await self.create_chat_project(data.get("name", ""), data.get("instructions", ""))


@web_route("PATCH", "/api/projects/{pid}")
async def api_update_project(self, request):
    """Body: {name?, instructions?}."""
    pid = request.path_params["pid"]
    if not self._sessions.get_project(pid):
        return {"error": "no such project"}
    fields, err = _clean(await self.safe_json(request))
    if err:
        return {"error": err}
    if not fields:
        return {"error": "no updatable fields provided"}
    return self._sessions.update_project(pid, **fields)


@web_route("DELETE", "/api/projects/{pid}")
async def api_delete_project(self, request):
    """Delete a project. Its chats stay, back in Recents."""
    pid = request.path_params["pid"]
    if not self._sessions.get_project(pid):
        return {"error": "no such project"}
    self._sessions.delete_project(pid)
    return {"ok": True}


@web_route("GET", "/api/search")
async def api_search(self, request):
    """?q=<text>[&project=<pid>][&profile=chat] → {results: [...]} (newest first).

    Substring search over what was said — the user's typed text and the
    assistant's replies, never tool output or injected context — plus session
    names. See ChatSessionStore.search.
    """
    q = (request.query_params.get("q") or "").strip()
    if not q:
        return {"results": []}
    where = {}
    if request.query_params.get("project"):
        where["project_id"] = request.query_params["project"]
    if request.query_params.get("profile"):
        where["profile"] = request.query_params["profile"]
    return {"results": self._sessions.search(q[:200], limit=SEARCH_LIMIT, where=where)}


# ── Verbs for other apps (call_app passes plain kwargs, not a request) ──


async def create_chat_project(self, name: str = "", instructions: str = "") -> dict:
    """Create a chat project. Portal's folder migration calls this."""
    fields, err = _clean({"name": name, "instructions": instructions})
    if err:
        return {"error": err}
    return self._sessions.create_project(fields["name"], fields.get("instructions", ""))


async def assign_chat_project(self, session_id: str = "", project_id: str = "", keep_existing: bool = False) -> dict:
    """Move a session into a project ("" = out of any project).

    ``keep_existing`` leaves a session that is already in a project where it
    is (portal's folder migration: the user's own placement wins)."""
    record = self._get_session(session_id)
    if not record:
        return {"error": "no such session"}
    if project_id and not self._sessions.get_project(project_id):
        return {"error": "no such project"}
    if keep_existing and record.get("project_id"):
        return {"ok": True, "session_id": session_id, "project_id": record["project_id"], "kept": True}
    self._sessions.update_session(session_id, project_id=project_id)
    return {"ok": True, "session_id": session_id, "project_id": project_id}


def _project_block(self, record: dict) -> str:
    """The system-prompt block for a session's project, or "" when it has none
    (or the project was deleted, or its instructions are empty)."""
    pid = (record or {}).get("project_id") or ""
    if not pid:
        return ""
    project = self._sessions.get_project(pid)
    if not project or not (project.get("instructions") or "").strip():
        return ""
    return PROJECT_BLOCK.format(name=project["name"], instructions=project["instructions"].strip())
