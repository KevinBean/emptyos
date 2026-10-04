"""agent — the session ↔ artifact link (desktop GUI B4).

The CreateArtifact tool (``emptyos/sdk/agent_tools/artifact.py``) hands its
page to viz, which owns the artifact: the file, the record note, the version
ring. What viz cannot know is **which conversation made it** — a tool only ever
sees the model's own arguments, and reading the live session off the app object
would be shared mutable state across concurrent chats.

The after-tool hook does know: ``agent_loop`` passes it the session id. So the
link is recorded here, in one row per (session, artifact). That row is the
whole reason the panel survives a reload — history replay carries no ``display``
payload, so a side panel driven only by the live tool event would vanish the
moment the user refreshed the page.

Extracted from app.py per .claude/rules/multi-module-apps.md. Owns: the
after-tool hook that records an artifact, and the read route the chat's panel
restores from. Reaches into other modules: none.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import AgentApp  # noqa: F401 — for type hints only


# ─── Bind to AgentApp class as ────────────────────────────────
#   _artifact_hook    = _artifacts._artifact_hook
#   _artifact_exists  = _artifacts._artifact_exists
#   api_artifacts     = _artifacts.api_artifacts
# Adding a new method here? Add a matching binding line in app.py.
# ──────────────────────────────────────────────────────────────

ARTIFACT_TOOL = "CreateArtifact"


def _artifact_hook(self, session_id: str, tool_name: str, input: dict, result) -> None:
    """After-hook: record the artifact a CreateArtifact call just saved.

    Reads the tool's own ``display.artifact`` rather than its input, so an id
    viz assigned (or refused) is what gets stored — the input carries the id
    the model *asked* for, which on a first save is empty.

    Best-effort like its siblings: losing an index row costs the panel its
    restore, and must never fail the turn the user actually asked for.
    """
    if tool_name != ARTIFACT_TOOL or result is None or not getattr(result, "ok", False):
        return
    art = (getattr(result, "display", None) or {}).get("artifact") or {}
    rid = str(art.get("id") or "").strip()
    if not rid or not session_id:
        return
    try:
        self._sessions.record_artifact(
            session_id, rid, title=str(art.get("title") or ""), shape=str(art.get("shape") or "")
        )
    except Exception as e:  # noqa: BLE001 — see docstring
        self.log(f"agent: could not record artifact {rid}: {e}", level="warning")


async def _artifact_exists(self, artifact_id: str) -> bool:
    """Is the artifact this row points at still there?

    The link lives in ``data/``; the artifact lives in the **vault**, where the
    user can delete it with a file manager and nothing tells us. Without this
    the panel would offer a row whose preview frame renders viz's 404 JSON
    where a chart used to be — a broken thing presented as a working one.

    viz absent (never installed, or disabled in the store) means no artifacts
    at all, which is the honest answer rather than a list of dead links.

    Reuses viz's own ``artifact_path`` — the existence-guarded entity_source
    its ``[provides.timeline]`` already declares, which answers "" when the
    record is gone. A second existence check here would be a second definition
    of the same question.
    """
    try:
        rel = await self.call_app("viz", "artifact_path", id=artifact_id)
    except Exception:  # noqa: BLE001 — viz absent or erroring
        return False
    return bool(rel)


@web_route("GET", "/api/sessions/{sid}/artifacts")
async def api_artifacts(self, request) -> dict:
    """The artifacts this chat produced, most recently touched first.

    Each row carries the URLs the panel needs, built here rather than stored,
    so a change to viz's routes never leaves stale links in the database.
    """
    sid = request.path_params.get("sid", "")
    if not self._get_session(sid):
        return {"error": "session not found"}
    rows = []
    for r in self._sessions.list_artifacts(sid):
        if await self._artifact_exists(r["artifact_id"]):
            rows.append(r)
    return {
        "artifacts": [
            {
                "id": r["artifact_id"],
                "title": r.get("title") or "Untitled artifact",
                "shape": r.get("shape") or "",
                "updated": r.get("updated") or "",
                "url": f"/viz/api/html/{r['artifact_id']}",
                "source_url": f"/viz/api/source/{r['artifact_id']}",
                "open_url": f"/viz/#{r['artifact_id']}",
            }
            for r in rows
        ]
    }
