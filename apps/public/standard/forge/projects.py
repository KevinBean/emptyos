"""forge — Forge project CRUD + path helpers + creation pipeline.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Path resolvers, async lock factory, list/get/create/update/delete project surface, and the multi-step `_create_project_impl` orchestrator that runs the chosen target's scaffold.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from emptyos.sdk import web_route
from typing import Any, TYPE_CHECKING

from .targets import COMING_SOON, TARGETS

if TYPE_CHECKING:
    from .app import ForgeApp  # noqa: F401 — for type hints only

FORGE_TAG = "forge-project"


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (text or "").lower()).strip("-")
    return s or "project"


def _scaffold_ctx(project_id: str, name: str, root: Path):
    from .targets import ScaffoldCtx

    return ScaffoldCtx(project_id=project_id, name=name, root=root)


def _replace_section(full_md: str, section: str, new_body: str) -> str:
    """Replace the body of a `## <section>` block in a markdown note.

    Naive but adequate for our shape — section bodies in forge notes are
    short hand-edited prose, not nested headings. If a future use case needs
    headings-within-section we promote this to BaseApp.
    """
    pattern = re.compile(
        rf"(?m)^##\s+{re.escape(section)}\s*\n.*?(?=^##\s+|\Z)",
        re.DOTALL,
    )
    replacement = f"## {section}\n\n{new_body.rstrip()}\n\n"
    if pattern.search(full_md):
        return pattern.sub(replacement, full_md, count=1)
    # Section absent — append.
    sep = "" if full_md.endswith("\n") else "\n"
    return full_md + sep + "\n" + replacement


# ─── Bind to ForgeApp class as ────────────────────────────────
#   _default_root         = _projects._default_root
#   _vault_rel            = _projects._vault_rel
#   _data_dir             = _projects._data_dir
#   _lock_for             = _projects._lock_for
#   list_projects         = _projects.list_projects
#   list_all              = _projects.list_all
#   get_project           = _projects.get_project
#   set_field             = _projects.set_field
#   update_design         = _projects.update_design
#   delete_project        = _projects.delete_project
#   create_project        = _projects.create_project
#   _create_project_impl  = _projects._create_project_impl
#   api_targets           = _projects.api_targets
#   api_list              = _projects.api_list
#   api_create            = _projects.api_create
#   api_get               = _projects.api_get
#   api_delete            = _projects.api_delete
#   api_update_design     = _projects.api_update_design
#   api_set_field         = _projects.api_set_field
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _default_root(self) -> Path:
    raw = self.app_config("default_root", "~/dev/forge")
    return Path(raw).expanduser().resolve()


def _vault_rel(self, project_id: str) -> str:
    return f"{self.vault_config('vault_dir', '30_Resources/EmptyOS/forge')}/{project_id}.md"


def _data_dir(self, *parts: str) -> Path:
    # data/apps/forge/<parts>
    root = self.kernel.config.path.parent / "data" / "apps" / "forge"
    p = root.joinpath(*parts) if parts else root
    p.mkdir(parents=True, exist_ok=True)
    return p


def _lock_for(self, project_id: str) -> asyncio.Lock:
    if project_id not in self._locks:
        self._locks[project_id] = asyncio.Lock()
    return self._locks[project_id]


async def list_projects(self) -> list[dict]:
    """Return every forge-project note in the vault."""
    rows = self.vault_query(tags=[FORGE_TAG]) or []
    out = []
    for r in rows:
        props = r.get("properties") or {}
        pid = props.get("id") or Path(r.get("path", "")).stem
        out.append(
            {
                "id": pid,
                "name": props.get("name") or pid,
                "target": props.get("target") or "",
                "repo_path": props.get("repo_path") or "",
                "git_remote": props.get("git_remote") or "",
                "status": props.get("status") or "scaffolded",
                "version": props.get("version") or "0.0.0",
                "created": props.get("created") or "",
                "updated": props.get("updated") or "",
                "last_build": props.get("last_build") or "",
                "running": pid in self._running,
            }
        )
    out.sort(key=lambda d: d.get("updated", ""), reverse=True)
    return out


async def list_all(self) -> list[dict]:
    """Boards-as-view-layer contract — used by the boards app."""
    return await self.list_projects()


async def get_project(self, project_id: str) -> dict:
    """Detail view: vault frontmatter + adapter.status() + design notes."""
    rel = self._vault_rel(project_id)
    fm = self.vault_get_properties(rel)
    if not fm:
        return {"error": "Project not found"}
    target_id = fm.get("target") or ""
    target = TARGETS.get(target_id)
    repo_path = Path(fm.get("repo_path") or "").expanduser()
    status_info: dict[str, Any] = {}
    if target and repo_path:
        try:
            status_info = await target.status(repo_path)
        except Exception as e:
            status_info = {"error": str(e)}
    design = self.vault_read_section(rel, "Design")
    return {
        "id": project_id,
        "frontmatter": fm,
        "design": design,
        "running": project_id in self._running,
        "target_status": status_info,
    }


async def set_field(self, id: str, field: str, value) -> dict:
    """Boards inline-edit hook. Only fields in SETTABLE_FIELDS are allowed."""
    if field not in self.SETTABLE_FIELDS:
        return {"error": f"field '{field}' not settable"}
    rel = self._vault_rel(id)
    if not self.vault_get_properties(rel):
        return {"error": "Project not found"}
    self.vault_update(
        rel, {field: value, "updated": datetime.now().strftime("%Y-%m-%d")}
    )
    return {"ok": True}


async def update_design(self, project_id: str, text: str) -> dict:
    """Replace the body of the `## Design` section (UI-driven note edit).

    vault_append_section *appends* — for a replace we need to rewrite the
    note. Phase 1: read the file, swap the section, write it back. Future:
    promote to a real `vault_replace_section` on BaseApp once 2+ apps need
    it.
    """
    rel = self._vault_rel(project_id)
    if not self.vault_get_properties(rel):
        return {"error": "Project not found"}
    full = await self.read(rel)
    new_full = _replace_section(full, "Design", text)
    await self.write(rel, new_full)
    # Re-index by touching the frontmatter timestamp.
    self.vault_update(rel, {"updated": datetime.now().strftime("%Y-%m-%d")})
    return {"ok": True}


async def delete_project(self, project_id: str, *, hard: bool = False) -> dict:
    """Remove the forge tracking note. If hard, also rm -rf the external repo.

    Stops any running dev process first. Hard delete requires the project
    to not be currently running.
    """
    rel = self._vault_rel(project_id)
    fm = self.vault_get_properties(rel)
    if not fm:
        return {"error": "Project not found"}
    if project_id in self._running:
        await self.stop_dev(project_id)
    # Drop the vault note from disk AND evict the VaultIndex entry —
    # the index is in-memory; without the re-index call,
    # vault_get_properties keeps returning the cached frontmatter and
    # subsequent create_project rejects the id as "already exists".
    vault_root = self.kernel.config.notes_path
    if vault_root:
        note_path = vault_root / rel
        if note_path.exists():
            note_path.unlink()
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            vi.index_file(rel)
    if hard:
        repo_path = Path(fm.get("repo_path") or "").expanduser()
        if repo_path and repo_path.exists() and repo_path.is_dir():
            shutil.rmtree(repo_path, ignore_errors=True)
    await self.emit("forge:deleted", {"id": project_id, "hard": hard})
    return {"ok": True}


async def create_project(
    self,
    *,
    target: str,
    name: str,
    project_id: str = "",
    root: str = "",
) -> dict:
    """Create + scaffold a new project.

    Always writes the vault tracking note first. If the target adapter
    raises NotImplementedError (target not yet wired), the note survives
    so the user can point repo_path at a hand-bootstrapped tree later
    and forge still tracks it.
    """
    # Breadcrumb log — sidesteps any FastAPI 500 swallowing by writing
    # to a file directly. Each step appends a line so we can see exactly
    # where a crash happens even if exception handling fails.
    bc = self._data_dir() / "create-breadcrumbs.log"
    def _bc(step: str):
        try:
            bc.parent.mkdir(parents=True, exist_ok=True)
            with bc.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} {project_id or name} {step}\n")
        except Exception:
            pass

    _bc("ENTER")
    try:
        r = await self._create_project_impl(
            target=target, name=name, project_id=project_id, root=root,
        )
        _bc(f"RETURN {r.get('ok', False)} {r.get('error', '')}")
        return r
    except BaseException as e:
        import asyncio
        import traceback
        _bc(f"EXCEPT {type(e).__name__}: {e}")
        if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        return {
            "ok": False,
            "error": f"create_project crashed: {type(e).__name__}: {e}",
            "traceback": traceback.format_exc().splitlines()[-12:],
        }


async def _create_project_impl(
    self,
    *,
    target: str,
    name: str,
    project_id: str = "",
    root: str = "",
) -> dict:
    bc = self._data_dir() / "create-breadcrumbs.log"
    def _bc(step: str):
        try:
            with bc.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} {project_id or name} IMPL:{step}\n")
        except Exception:
            pass
    _bc("start")
    tgt = TARGETS.get(target)
    if not tgt:
        return {"error": f"unknown target '{target}'"}
    pid = _slugify(project_id or name)
    if not pid:
        return {"error": "project_id or name required"}
    rel = self._vault_rel(pid)
    if self.vault_get_properties(rel):
        return {"error": f"project '{pid}' already exists"}
    root_dir = Path(root).expanduser().resolve() if root else self._default_root()
    repo_path = root_dir / pid
    if repo_path.exists() and any(repo_path.iterdir()):
        return {"error": f"target dir not empty: {repo_path}"}
    root_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    fm = {
        "tags": [FORGE_TAG],
        "id": pid,
        "name": name or pid,
        "target": target,
        "repo_path": str(repo_path),
        "git_remote": "",
        "status": "scaffolding",
        "version": "0.0.1",
        "created": today,
        "updated": today,
        "last_build": "",
    }
    body = (
        "\n## Design\n\n"
        "_Design notes for this project. Editable from the forge UI._\n"
        "\n## Changelog\n\n"
        f"- {today} scaffolded\n"
    )
    _bc(f"pre-vault-create rel={rel}")
    self.vault_create_note(rel, fm, body)
    _bc("vault-note-written")

    # Run the adapter's scaffold inside the per-project lock so a
    # double-click doesn't spawn two npm-create runs against the same
    # directory.
    log_path = self._data_dir("runs", pid) / "scaffold.log"
    _bc(f"log_path={log_path}")

    def _on_log(line: bytes) -> None:
        # Drain callback — agent-runtime already writes to the file.
        pass

    try:
        runtime = self.require("agent-runtime")
        _bc("got-runtime")
    except Exception as e:
        runtime = None
        _bc(f"runtime-missing: {e}")

    async with self._lock_for(pid):
        _bc("lock-acquired")
        try:
            result = await tgt.scaffold(
                ctx=_scaffold_ctx(pid, name or pid, root_dir),
                runtime=runtime,
                on_log=_on_log,
            )
            _bc(f"scaffold-returned ok={result.ok} err={result.error}")
        except NotImplementedError:
            # Target adapter not yet wired. Vault note survives so the
            # user can point repo_path at a hand-bootstrapped tree.
            self.vault_update(rel, {"status": "stub", "updated": today})
            return {
                "ok": False,
                "id": pid,
                "repo_path": str(repo_path),
                "error": "scaffold not implemented yet for this target",
            }
        except Exception as e:
            # Any other failure during scaffold — log the cause on the
            # vault note and return JSON so the UI sees a clean error
            # instead of FastAPI's 500. The npm-create log under
            # `.eos-forge-logs/` is the place to debug from.
            self.vault_update(rel, {"status": "scaffold-failed", "updated": today})
            return {
                "ok": False,
                "id": pid,
                "repo_path": str(repo_path),
                "error": f"scaffold crashed: {type(e).__name__}: {e}",
            }
        status = "scaffolded" if result.ok else "scaffold-failed"
        self.vault_update(rel, {"status": status, "updated": today})
        self.spawn_background(self.emit(
            "forge:scaffolded",
            {"id": pid, "target": target, "repo_path": str(repo_path)},
        ))
        return {
            "ok": result.ok,
            "id": pid,
            "repo_path": str(result.repo_path),
            "log": str(result.log_path),
            "error": result.error,
        }


@web_route("GET", "/api/targets")
async def api_targets(self, request):
    out = []
    for tid, tgt in TARGETS.items():
        out.append(
            {
                "id": tid,
                "name": tgt.name,
                "description": tgt.description,
                "coming_soon": tgt.coming_soon,
                "preflight": [c.__dict__ for c in tgt.preflight()],
            }
        )
    for cs in COMING_SOON:
        out.append(
            {
                **cs,
                "coming_soon": True,
                "preflight": [],
            }
        )
    return {"targets": out, "default_root": str(self._default_root())}


@web_route("GET", "/api/projects")
async def api_list(self, request):
    return {"projects": await self.list_projects()}


@web_route("POST", "/api/projects")
async def api_create(self, request):
    # Route-level safety net — captures anything that happens before
    # create_project's own catch (body parse, kwarg expansion, etc.).
    # Writes a breadcrumb so we can tell whether the handler was even
    # reached.
    bc_path = self._data_dir() / "route-breadcrumbs.log"
    try:
        bc_path.parent.mkdir(parents=True, exist_ok=True)
        with bc_path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} api_create:ENTER\n")
    except Exception:
        pass
    try:
        body = await request.json()
        try:
            with bc_path.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} api_create:body={body}\n")
        except Exception:
            pass
        r = await self.create_project(
            target=body.get("target") or "",
            name=body.get("name") or "",
            project_id=body.get("id") or "",
            root=body.get("root") or "",
        )
        try:
            with bc_path.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} api_create:RETURN ok={r.get('ok')}\n")
        except Exception:
            pass
        return r
    except BaseException as e:
        import asyncio
        import traceback
        try:
            with bc_path.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} api_create:EXCEPT {type(e).__name__}: {e}\n")
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        return {
            "ok": False,
            "error": f"api_create crashed: {type(e).__name__}: {e}",
        }


@web_route("GET", "/api/projects/{id}")
async def api_get(self, request):
    return await self.get_project(request.path_params["id"])


@web_route("DELETE", "/api/projects/{id}")
async def api_delete(self, request):
    hard = request.query_params.get("hard", "") in ("1", "true", "yes")
    return await self.delete_project(request.path_params["id"], hard=hard)


@web_route("POST", "/api/projects/{id}/design")
async def api_update_design(self, request):
    body = await request.json()
    return await self.update_design(
        request.path_params["id"], body.get("text") or ""
    )


@web_route("POST", "/api/projects/{id}/field")
async def api_set_field(self, request):
    body = await request.json()
    return await self.set_field(
        request.path_params["id"], body.get("field") or "", body.get("value")
    )
