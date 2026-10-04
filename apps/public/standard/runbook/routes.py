"""Runbook — web API + CLI routes (bound onto RunbookApp).

Module-level handlers attached to RunbookApp in app.py. Imports the leaf
utility layer + engine helpers via ``self`` (bound). Never imports ``.app``.

# ─── Bind to RunbookApp class as ─────────────────────────────────────
#   api_list           = _routes.api_list
#   api_get            = _routes.api_get
#   api_templates      = _routes.api_templates
#   api_create         = _routes.api_create
#   api_run            = _routes.api_run
#   api_run_block      = _routes.api_run_block
#   api_block_output   = _routes.api_block_output
#   api_publish        = _routes.api_publish
#   api_schedule       = _routes.api_schedule
#   api_from_session   = _routes.api_from_session
#   api_from_confirm   = _routes.api_from_confirm
#   cli_list           = _routes.cli_list
#   cli_run            = _routes.cli_run
#   _draft_path        = _routes._draft_path
#   _register_schedules = _routes._register_schedules
# Adding a method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json as _json
import re as _re
import uuid as _uuid
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import cli_command, web_route
from emptyos.sdk import now_iso as _now_iso

from . import shared as _shared
from .shared import Block, parse_blocks, serialize_blocks

if TYPE_CHECKING:
    from .app import RunbookApp  # noqa: F401


def _blocks_from_dicts(items: list) -> list:
    out = []
    for i, d in enumerate(items or []):
        if not isinstance(d, dict):
            continue
        bid = str(d.get("id") or f"block{i}")
        btype = str(d.get("type") or "")
        output = str(d.get("output") or bid)
        header = dict(d.get("header") or {})
        header.update({"id": bid, "type": btype, "output": output})
        inputs = d.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}
        out.append(Block(id=bid, type=btype, output=output, header=header,
                         body=str(d.get("body") or ""), inputs=inputs))
    return out


def _write_runbook(self, runbook_id: str, title: str, body: str, *, schedule: str = "") -> str:
    # Chokepoint guard: runbook_id flows into a vault path, so it must never
    # contain path separators or `..`. The allowlist regex makes traversal
    # impossible regardless of which caller built the id.
    if not _RID_RE.match(runbook_id or ""):
        raise ValueError(f"invalid runbook id: {runbook_id!r}")
    rel = f"{_shared.RUNBOOK_DIR}/{runbook_id}.md"
    fm = {
        "tags": [_shared.RUNBOOK_TAG],
        "id": runbook_id,
        "title": title or runbook_id,
        "author": "user",
        "created": _now_iso()[:10],
        "updated": _now_iso()[:10],
    }
    if schedule:
        fm["schedule"] = schedule
    self.vault_create_note(rel, fm, body)
    return rel


# ── list / get ─────────────────────────────────────────────────────────────
@web_route("GET", "/api/runbooks")
async def api_list(self, request):
    try:
        notes = self.vault_query(tags=[_shared.RUNBOOK_TAG])
    except Exception:  # noqa: BLE001
        notes = []
    out = []
    for n in notes:
        rid = Path(n.get("path", "")).stem
        if not rid:
            continue
        fm = n.get("frontmatter", {}) or {}
        body = self.vault_read_body(f"{_shared.RUNBOOK_DIR}/{rid}.md") or ""
        out.append({
            "id": rid,
            "title": fm.get("title") or rid,
            "schedule": fm.get("schedule") or "",
            "block_count": len(parse_blocks(body)),
        })
    out.sort(key=lambda r: r["title"].lower())
    return {"runbooks": out, "count": len(out)}


@web_route("GET", "/api/runbooks/{rid}")
async def api_get(self, request):
    rid = _norm_rid(request.path_params["rid"])
    rel = f"{_shared.RUNBOOK_DIR}/{rid}.md"
    fm = self.vault_get_properties(rel)
    if not fm:
        return {"error": f"runbook '{rid}' not found"}
    blocks = parse_blocks(self.vault_read_body(rel) or "")
    try:
        vault_rel = str(
            (self.vault_root / rel).resolve().relative_to(self.vault_root.resolve())
        ).replace("\\", "/")
    except Exception:  # noqa: BLE001
        vault_rel = rel
    return {
        "id": rid,
        "title": fm.get("title") or rid,
        "schedule": fm.get("schedule") or "",
        "blocks": self.block_statuses(rid, blocks),
        "_vault_path": vault_rel,
    }


# ── starter templates (gallery of KB `pattern` notes, fail-soft) ───────────
_TEMPLATE_SLUGS = (
    "runbook-pattern-calculate",
    "runbook-pattern-think",
    "runbook-pattern-call-app",
)
_DEFAULT_STARTER = (
    "```eos-block\n"
    'id = "items"\n'
    'type = "vault_query"\n'
    'output = "items"\n'
    "---\n"
    "# header fields above the --- ; body below\n"
    "```"
)


@web_route("GET", "/api/templates")
async def api_templates(self, request):
    """Starting-point options for openNew()'s modal. Always includes the
    baseline vault_query starter first; each KB `pattern` note beyond that is
    best-effort — a missing note or an unavailable kb app just means fewer
    options, never an error (openNew() must still work with zero KB notes)."""
    out = [{"slug": "default", "title": "Query the vault (default)", "body": _DEFAULT_STARTER}]
    for slug in _TEMPLATE_SLUGS:
        try:
            note = await self.call_app("kb", "get_note", slug=slug)
        except Exception:  # noqa: BLE001 — kb app may not be installed
            continue
        if not isinstance(note, dict) or note.get("error"):
            continue
        note_blocks = [b for b in parse_blocks(note.get("body") or "") if b.type != "invalid"]
        if not note_blocks:
            continue
        out.append({
            "slug": slug,
            "title": (note.get("properties") or {}).get("title") or slug,
            "body": serialize_blocks(note_blocks[:1]).rstrip("\n"),
        })
    return {"templates": out}


# ── create ───────────────────────────────────────────────────────────────
@web_route("POST", "/api/runbooks")
async def api_create(self, request):
    body = await request.json()
    rid = _norm_rid(body.get("id"))
    if not rid:
        return {"error": "id required"}
    if not _RID_RE.match(rid):
        return {"error": "invalid runbook id"}
    rel = f"{_shared.RUNBOOK_DIR}/{rid}.md"
    if self.vault_get_properties(rel):
        return {"error": f"runbook '{rid}' already exists"}
    title = body.get("title") or rid
    md = body.get("markdown")
    if md is None:
        blocks = _blocks_from_dicts(body.get("blocks") or [])
        md = serialize_blocks(blocks) if blocks else _shared.serialize_blocks([])
    _write_runbook(self, rid, title, md)
    await self.emit("runbook:created", {"id": rid})
    return {"ok": True, "id": rid}


# ── run ──────────────────────────────────────────────────────────────────
@web_route("POST", "/api/runbooks/{rid}/run")
async def api_run(self, request):
    rid = _norm_rid(request.path_params["rid"])
    if not self.vault_get_properties(f"{_shared.RUNBOOK_DIR}/{rid}.md"):
        return {"error": f"runbook '{rid}' not found"}
    return await self.run_all(rid, mode="interactive")


@web_route("POST", "/api/runbooks/{rid}/run-block/{bid}")
async def api_run_block(self, request):
    rid = _norm_rid(request.path_params["rid"])
    bid = request.path_params["bid"]
    return await self.run_from(rid, bid, mode="interactive")


# ── rich output (runbook-no-rich-output) ────────────────────────────────────
@web_route("GET", "/api/runbooks/{rid}/block/{bid}/output")
async def api_block_output(self, request):
    """Full raw cached value for one block. block_statuses' output_preview is
    a truncated STRING (None for a table/artifact-ref value), so a `table`-
    typed vault_query result or a `artifact-ref`-typed chart needs its real
    value fetched separately — the frontend does this lazily, only for those
    two kinds. `bid` is matched against real parsed block ids before it ever
    reaches the cache store, mirroring run_from's own safety pattern — never
    pass a raw path param straight into a filename-building call."""
    rid = _norm_rid(request.path_params["rid"])
    bid = request.path_params["bid"]
    rel = f"{_shared.RUNBOOK_DIR}/{rid}.md"
    if not self.vault_get_properties(rel):
        return {"error": f"runbook '{rid}' not found"}
    blocks = parse_blocks(self.vault_read_body(rel) or "")
    if not any(b.id == bid for b in blocks):
        return {"error": f"block '{bid}' not found"}
    value = self._run_store().cached_output(rid, bid)
    return {"value": value}


# ── publish (runbook-publish-not-built) ─────────────────────────────────────
def _publish_content(title: str, statuses: list[dict]) -> tuple[str, bool]:
    """Render the last-run terminal artifact as a markdown summary — one
    section per block that has actually produced a result. A block with no
    string preview (a `number`/`json` output — see runbook-no-rich-output,
    a separate gap) gets an honest placeholder rather than a raw Python repr.
    Returns (markdown, any_block_ran)."""
    lines = [f"# {title}", ""]
    any_run = False
    for b in statuses:
        if b.get("status") not in ("ok", "stale") or not b.get("ran_at"):
            continue
        any_run = True
        lines.append(f"## {b['id']} ({b['type']})")
        preview = b.get("output_preview")
        lines.append(preview if preview else "*(no text preview for this block's output)*")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", any_run


@web_route("POST", "/api/runbooks/{rid}/publish")
async def api_publish(self, request):
    """Hand the runbook's last-run terminal artifact to the `publish` app as a
    new draft. Requires at least one block to have actually run — publishing
    an empty pipeline would just create a blank post."""
    rid = _norm_rid(request.path_params["rid"])
    rel = f"{_shared.RUNBOOK_DIR}/{rid}.md"
    fm = self.vault_get_properties(rel)
    if not fm:
        return {"error": f"runbook '{rid}' not found"}
    blocks = parse_blocks(self.vault_read_body(rel) or "")
    statuses = self.block_statuses(rid, blocks)
    content, any_run = _publish_content(fm.get("title") or rid, statuses)
    if not any_run:
        return {"error": "run this runbook at least once before publishing"}
    try:
        res = await self.call_app("publish", "save_draft", title=fm.get("title") or rid, content=content)
    except Exception as e:  # noqa: BLE001 — publish is an optional_apps integration
        return {"error": f"publish app unavailable: {type(e).__name__}: {e}"}
    if res.get("ok"):
        await self.emit("runbook:published", {"id": rid, "path": res.get("path")})
    return res


# ── schedule ─────────────────────────────────────────────────────────────
@web_route("POST", "/api/runbooks/{rid}/schedule")
async def api_schedule(self, request):
    rid = _norm_rid(request.path_params["rid"])
    rel = f"{_shared.RUNBOOK_DIR}/{rid}.md"
    if not self.vault_get_properties(rel):
        return {"error": f"runbook '{rid}' not found"}
    body = await request.json()
    cron = (body.get("cron") or "").strip()
    self.vault_update(rel, {"schedule": cron, "updated": _now_iso()[:10]})
    if cron:
        ok = self.add_cron_job_logged(
            f"runbook:{rid}",
            lambda r=rid: self.run_all(r, mode="scheduled"),
            cron=cron, crash_event="runbook_tick_crash",
        )
        await self.emit("runbook:scheduled", {"id": rid, "cron": cron})
        return {"ok": bool(ok), "cron": cron,
                "next_run": self.get_cron_job_next_fire(f"runbook:{rid}")}
    return {"ok": True, "cron": ""}


# ── from-session (propose → preview → confirm) ───────────────────────────
# Draft tokens are uuid4().hex[:12] — exactly 12 lowercase hex chars. Anything
# else is rejected before it can reach the filesystem (path-traversal guard).
_TOKEN_RE = _re.compile(r"\A[a-f0-9]{12}\Z")

# Runbook ids flow into vault file paths. Strict allowlist (no `.`, `/`, `\`,
# spaces) so a user-supplied id can never escape RUNBOOK_DIR (path-traversal
# guard). Matches the slugify shape created by api_create/api_from_session.
_RID_RE = _re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")


def _norm_rid(raw: str) -> str:
    """Normalize a caller-supplied runbook id the same way api_create stores it,
    so ids round-trip through every {rid} route (GET/run/schedule) regardless of
    the case/spacing the client used."""
    return (raw or "").strip().lower().replace(" ", "-")


def _draft_path(self, token: str) -> Path:
    if not _TOKEN_RE.match(token or ""):
        raise ValueError("invalid draft token")
    d = (Path(self.data_dir) / "drafts").resolve()
    d.mkdir(parents=True, exist_ok=True)
    p = (d / f"{token}.json").resolve()
    # Defence-in-depth: the resolved path must stay inside drafts/.
    if not str(p).startswith(str(d)):
        raise ValueError("draft path escapes drafts dir")
    return p


@web_route("POST", "/api/from-session")
async def api_from_session(self, request):
    """Draft a runbook from an exploration. Writes NOTHING — returns the drafted
    markdown + a confirm token. Accepts a raw ``transcript`` or a
    ``session_id`` (read via the assistant app)."""
    body = await request.json()
    rid = _norm_rid(body.get("id"))
    transcript = (body.get("transcript") or "").strip()
    if not transcript and body.get("session_id"):
        try:
            sess = await self.call_app("assistant", "get_session",
                                       sid=body["session_id"])
            msgs = (sess or {}).get("messages", []) if isinstance(sess, dict) else []
            transcript = "\n\n".join(
                f"{m.get('role', '?')}: {m.get('text', '')}" for m in msgs)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"could not read session: {e}"}
    if not transcript:
        return {"ok": False, "error": "transcript or session_id required"}
    if not rid:
        return {"ok": False, "error": "id required"}
    if not _RID_RE.match(rid):
        return {"ok": False, "error": "invalid runbook id"}

    raw = await self.think(transcript, domain="code", system=_shared.FROM_SESSION_SYSTEM)
    from emptyos.sdk.utils import parse_llm_json
    items = parse_llm_json(raw)
    if not isinstance(items, list):
        return {"ok": False, "error": "model did not return a block list",
                "raw": str(raw)[:400]}
    md = serialize_blocks(_blocks_from_dicts(items))
    token = _uuid.uuid4().hex[:12]
    _draft_path(self, token).write_text(_json.dumps(
        {"id": rid, "title": body.get("title") or rid, "markdown": md,
         "created": _now_iso()}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "token": token, "id": rid, "markdown": md}


@web_route("POST", "/api/from-session/confirm")
async def api_from_confirm(self, request):
    body = await request.json()
    token = (body.get("token") or "").strip()
    if not _TOKEN_RE.match(token):
        return {"ok": False, "error": "invalid draft token"}
    try:
        p = _draft_path(self, token)
        draft = _json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"ok": False, "error": "draft token not found or expired"}
    rid = draft["id"]
    if not _RID_RE.match(rid or ""):
        return {"ok": False, "error": "invalid runbook id"}
    rel = f"{_shared.RUNBOOK_DIR}/{rid}.md"
    if self.vault_get_properties(rel):
        return {"ok": False, "error": f"runbook '{rid}' already exists"}
    _write_runbook(self, rid, draft.get("title") or rid, draft.get("markdown") or "")
    try:
        p.unlink()
    except OSError:
        pass
    await self.emit("runbook:created", {"id": rid, "from": "session"})
    return {"ok": True, "id": rid}


# ── CLI ──────────────────────────────────────────────────────────────────
@cli_command("list")
async def cli_list(self):
    data = await api_list(self, None)
    for r in data["runbooks"]:
        sched = f"  [{r['schedule']}]" if r["schedule"] else ""
        print(f"{r['id']}  ({r['block_count']} blocks){sched}")


@cli_command("run")
async def cli_run(self, runbook_id: str):
    res = await self.run_all(runbook_id, mode="interactive")
    if res.get("error"):
        print(f"error: {res['error']}")
        return
    for bid, st in res.get("results", {}).items():
        print(f"  {bid}: {st.get('status')}")


# ── schedule registration (called from setup) ────────────────────────────
def _register_schedules(self) -> int:
    """Re-register cron jobs for every scheduled runbook. Called on setup."""
    n = 0
    try:
        notes = self.vault_query(tags=[_shared.RUNBOOK_TAG])
    except Exception:  # noqa: BLE001
        return 0
    for note in notes:
        rid = Path(note.get("path", "")).stem
        cron = (note.get("frontmatter", {}) or {}).get("schedule")
        if rid and cron:
            try:
                self.add_cron_job_logged(
                    f"runbook:{rid}",
                    lambda r=rid: self.run_all(r, mode="scheduled"),
                    cron=cron, crash_event="runbook_tick_crash",
                )
                n += 1
            except Exception:  # noqa: BLE001
                continue
    return n
