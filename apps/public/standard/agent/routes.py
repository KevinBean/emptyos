"""Web API routes for AgentApp.

Extracted from app.py to keep the core agent atomic. Each handler is a
module-level async function decorated with @web_route; they're attached
to AgentApp as class attributes in app.py (`api_foo = _routes.api_foo`),
which lets inspect.getmembers discover them as bound methods — same
pattern as projects/extended.py.
"""

from __future__ import annotations

import json as _json
from datetime import datetime as _dt

from emptyos.sdk import web_route
from emptyos.sdk import autopilot as _autopilot
from emptyos.sdk.agent_loop import DEFAULT_MAX_ITERS
from emptyos.sdk.trace import current_trace_id

# ── Session listing / CRUD ─────────────────────────────────────────────


@web_route("GET", "/api/sessions")
async def api_list_sessions(self, request):
    return self._sessions.list_sessions()


@web_route("GET", "/api/sessions/{sid}")
async def api_get_session(self, request):
    sid = request.path_params["sid"]
    sess = self._get_session(sid)
    if not sess:
        return {"error": "not found"}
    return sess


@web_route("POST", "/api/sessions")
async def api_create_session(self, request):
    data = await self.safe_json(request)
    return self._create_session(
        name=data.get("name", ""),
        provider=data.get("provider", ""),
        initial_user_message=data.get("initial_user_message", ""),
    )


@web_route("DELETE", "/api/sessions/{sid}")
async def api_delete_session(self, request):
    sid = request.path_params["sid"]
    self._sessions.delete_session(sid)
    return {"ok": True}


@web_route("PATCH", "/api/sessions/{sid}")
async def api_update_session(self, request):
    """Update session metadata — currently supports `name` and `provider`."""
    sid = request.path_params["sid"]
    if not self._get_session(sid):
        return {"error": "not found"}
    data = await self.safe_json(request)
    fields = {}
    if "name" in data:
        fields["name"] = str(data["name"])[:200]
    if "provider" in data:
        fields["provider"] = str(data["provider"])[:50]
    if not fields:
        return {"error": "no updatable fields provided"}
    self._sessions.update_session(sid, **fields)
    return {"ok": True, **fields}


@web_route("POST", "/api/sessions/{sid}/archive")
async def api_archive_session(self, request):
    """POST /api/sessions/{sid}/archive
    Summarise the session via think() and save it to the vault.
    Returns {ok, path, url} on success, {ok, error} on failure."""
    sid = request.path_params["sid"]
    return await self._archive_session(sid)


# ── Session-scoped tasks / fork / revert / edit-stack ──────────────────


@web_route("GET", "/api/sessions/{sid}/tasks")
async def api_session_tasks(self, request):
    """GET /api/sessions/{sid}/tasks — last persisted TaskList for this session.
    Returns {tasks: [...]} or {tasks: []} if never set or daemon restarted."""
    sid = request.path_params["sid"]
    path = self.data_dir / f"tasks_{sid}.json"
    try:
        if path.exists():
            return {"tasks": _json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        pass
    return {"tasks": []}


@web_route("POST", "/api/sessions/{sid}/fork")
async def api_fork_session(self, request):
    """POST /api/sessions/{sid}/fork  body: {at?: int, name?: str}

    Clone a session into a new one. `at` is the 0-based message index to
    truncate at — omit to clone the full history. `name` overrides the
    auto-generated "Fork of <name>" title.
    Returns the new session dict.
    """
    sid = request.path_params["sid"]
    body = await self.safe_json(request)
    at = body.get("at")  # None = full clone
    name = body.get("name", "")
    forked = self._sessions.fork_session(sid, at_message=at, name=name)
    if not forked:
        return {"error": "session not found"}
    return forked


@web_route("POST", "/api/sessions/{sid}/revert")
async def api_revert(self, request):
    """POST /api/sessions/{sid}/revert  body: {n?: int} (default 1).
    Pops up to N entries off the session's edit stack and restores each.
    Returns the structured summary from `_revert_last_edits`."""
    sid = request.path_params["sid"]
    body = await self.safe_json(request)
    n_raw = body.get("n", 1) if isinstance(body, dict) else 1
    return self._revert_last_edits(sid, n_raw)


@web_route("GET", "/api/sessions/{sid}/edit-stack")
async def api_edit_stack(self, request):
    """GET /api/sessions/{sid}/edit-stack → recent Write/Edit entries
    for the active session (newest last). Used by the web UI to show
    "N edits revertable" next to the /revert button, and to enable/disable
    the button when the stack is empty."""
    sid = request.path_params["sid"]
    stack = self._edit_stacks.get(sid) or []
    # Return a light preview — strip `previous_content` (can be large).
    return [
        {
            "path": e.get("path"),
            "action": e.get("action"),
            "bytes": len(e.get("previous_content") or ""),
        }
        for e in stack
    ]


# ── MCP bridge / tool audit / tools listing ────────────────────────────


@web_route("POST", "/api/mcp/tools/call")
async def api_mcp_tool_call(self, request):
    """MCP bridge — dispatch a single tool call from the claude-cli MCP server.

    Called by emptyos/mcp_server.py (stdio MCP proxy) when claude-cli
    invokes an EmptyOS MCP tool. No consent gate — the MCP path runs inside
    claude-cli's own loop, which already handles --dangerously-skip-permissions.
    """
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "content": "error: invalid JSON body"}
    name = (data.get("name") or "").strip()
    arguments = data.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    tool = self._tools.get(name)
    if not tool:
        available = ", ".join(sorted(self._tools.keys()))
        return {
            "ok": False,
            "content": f"error: tool {name!r} not registered. Available: {available}",
        }
    try:
        result = await tool.run(self, **arguments)
        return {"ok": result.ok, "content": result.content, "display": result.display}
    except Exception as e:
        return {"ok": False, "content": f"error: {type(e).__name__}: {e}"}


@web_route("GET", "/api/tool-audit")
async def api_tool_audit(self, request):
    """GET /api/tool-audit?limit=100&tool=Bash&session=abc
    Returns recent entries from tool-audit.jsonl, newest first."""
    params = request.query_params
    limit = min(int(params.get("limit", 100)), 1000)
    filter_tool = params.get("tool", "")
    filter_session = params.get("session", "")
    entries = []
    try:
        if self._audit_path.exists():
            lines = self._audit_path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    e = _json.loads(line)
                except Exception:
                    continue
                if filter_tool and e.get("tool") != filter_tool:
                    continue
                if filter_session and e.get("session") != filter_session:
                    continue
                entries.append(e)
                if len(entries) >= limit:
                    break
    except Exception:
        pass
    return {"entries": entries, "total": len(entries)}


@web_route("GET", "/api/mcp/tools")
async def api_mcp_tools(self, request):
    """List tools available via the MCP bridge."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
        }
        for t in self._tools.values()
    ]


@web_route("GET", "/api/mcp/foundry/verbs")
async def api_mcp_foundry_verbs(self, request):
    """Aggregate the OUTBOUND-foundry-exposed verbs across all loaded apps.

    The outbound MCP foundry (``emptyos/mcp_outbound_server.py``) reads this at
    tools/list time to decide which verbs to advertise to an external client.
    A verb is returned only if ALL hold:

      (a) the declaring app's manifest lists it under ``[provides.mcp_foundry]``
          ``verbs``;
      (b) the verb's app-half resolves (through aliases) to the declaring app —
          so a manifest can never expose ANOTHER app's verb; and
      (c) it passes the autopilot eligibility floor (``policy.json``).

    The eligibility floor is the ultimate gate: a manifest can never self-grant
    a non-eligible verb (rooms.write_note, publish.deploy, notifications.send).
    This endpoint only chooses what's *advertised*; every ``tools/call`` still
    requires a matching ``mcp-client`` autopilot grant.
    """
    data_dir = self.autopilot_root()
    manifests = self.kernel.apps.manifests
    instances = self.kernel.apps.instances
    eligible = self.kernel.autopilot_eligible_set()  # injected floor (None = legacy)
    registry = self.kernel.apps.get_verbs()
    out: list[str] = []
    seen: set[str] = set()

    # (1) Registry-sourced: verbs whose [[provides.verbs]] declares surfaces ∋
    #     "mcp". These supersede the app's legacy [provides.mcp_foundry]
    #     (all-or-nothing per (app, surface) — see .claude/rules/verb-registry.md).
    migrated_mcp_apps = registry.apps_for_surface("mcp")
    for e in registry.for_surface("mcp"):
        if e.app_id not in instances or e.verb in seen:
            continue
        if not _autopilot.is_eligible(data_dir, e.verb, eligible=eligible):
            continue
        seen.add(e.verb)
        out.append(e.verb)

    # (2) Legacy-sourced: [provides.mcp_foundry] for apps NOT migrated to the
    #     registry's mcp surface. Skipped for migrated apps so a verb is never
    #     advertised twice.
    for app_id, m in manifests.items():
        if app_id not in instances or app_id in migrated_mcp_apps:
            continue
        section = m.provides.get("mcp_foundry") or {}
        for v in section.get("verbs") or []:
            if not isinstance(v, str) or "." not in v or v in seen:
                continue
            resolved = manifests.get(v.split(".", 1)[0])
            if resolved is None or resolved.id != app_id:
                continue
            if not _autopilot.is_eligible(data_dir, v, eligible=eligible):
                continue
            seen.add(v)
            out.append(v)
    return {"verbs": sorted(out)}


@web_route("GET", "/api/verbs/sweep")
async def api_verbs_sweep(self, request):
    """Drift sweep over the unified [[provides.verbs]] registry.

    For every declared verb, resolve its ``method`` (and ``voice.narrate``)
    against the live app instance and report whether it exists. This is the
    on-demand companion to the instance-creation warning in app_loader — the
    "is anything drifted right now" check a release / /preflight gate calls.

    By default it only resolves apps that are already loaded (cheap). Pass
    ``?load=1`` to force-load every verb-declaring app first, for a complete
    answer regardless of lazy-load state.

    Returns ``{verbs: [...], drift: [...], unloaded: [...], eligible: [...]}``
    where ``drift`` lists verbs whose method is missing from the instance.
    """
    force_load = request.query_params.get("load") in ("1", "true", "yes")
    registry = self.kernel.apps.get_verbs()
    instances = self.kernel.apps.instances

    if force_load:
        for app_id in sorted(registry.apps()):
            if app_id not in instances:
                try:
                    await self.kernel.apps.load(app_id)
                except Exception:
                    pass  # a broken app shows up as "unloaded"; never abort the sweep

    rows: list[dict] = []
    drift: list[dict] = []
    unloaded: set[str] = set()
    for entry in registry:
        inst = instances.get(entry.app_id)
        row = {
            "verb": entry.verb,
            "app": entry.app_id,
            "method": entry.method,
            "eligibility": entry.eligibility,
            "surfaces": list(entry.surfaces),
            "loaded": inst is not None,
        }
        if inst is None:
            unloaded.add(entry.app_id)
            row["resolved"] = None
        else:
            missing = [
                m for m in entry.dispatch_methods()
                if not callable(getattr(inst, m, None))
            ]
            row["resolved"] = not missing
            if missing:
                row["missing"] = missing
                drift.append({"verb": entry.verb, "app": entry.app_id, "missing": missing})
        rows.append(row)

    return {
        "count": len(rows),
        "verbs": rows,
        "drift": drift,
        "unloaded": sorted(unloaded),
        "eligible": sorted(registry.eligible_verbs()),
        "ok": len(drift) == 0,
    }


# ── Outbound MCP foundry — flag state + mcp-client grant management ─────────
# Read-only flag display (enabling stays a deliberate emptyos.toml edit, to
# keep the dark default honest) + grant issue/revoke for actor_type=mcp-client.


def _foundry_flag_on(self) -> bool:
    """Mirror the stdio server's flag read: emptyos.toml
    [apps.agent] feature.mcp-foundry.enabled. Read via kernel config so the
    panel shows the same state the foundry process sees."""
    val = self.kernel.config.get("apps.agent.feature.mcp-foundry.enabled", False)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return bool(val)


@web_route("GET", "/api/foundry")
async def api_foundry_status(self, request):
    """Outbound-foundry status for the agent-app panel: flag state (read-only),
    the verbs that would be advertised, and active mcp-client grants."""
    root = self.autopilot_root()
    grants = [
        g for g in _autopilot.load_grants(root)
        if (g.get("actor") or {}).get("type") == "mcp-client"
    ]
    verbs = await api_mcp_foundry_verbs(self, request)
    return {
        "flag_on": _foundry_flag_on(self),
        "flag_key": "apps.agent.feature.mcp-foundry.enabled",
        "advertised_verbs": (verbs or {}).get("verbs", []),
        "grants": grants,
    }


@web_route("POST", "/api/foundry/grant")
async def api_foundry_grant(self, request):
    """Issue an mcp-client grant. Body:
    `{"actor_id": "codex", "verb_pattern": "task.add", "ttl_s": null,
      "scope": null, "rationale": ""}`. Default scope mcp:<actor_id> matches
    the foundry's scope candidates. Refused if the verb isn't eligible."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    actor_id = (body.get("actor_id") or "").strip()
    verb_pattern = (body.get("verb_pattern") or "").strip()
    if not actor_id or not verb_pattern:
        return {"ok": False, "error": "actor_id and verb_pattern required"}
    scope = (body.get("scope") or "").strip() or f"mcp:{actor_id}"
    ttl = body.get("ttl_s")
    try:
        ttl = int(ttl) if ttl else None
    except (TypeError, ValueError):
        ttl = None
    try:
        g = _autopilot.save_grant(
            self.autopilot_root(),
            actor_type="mcp-client",
            actor_id=actor_id,
            verb_pattern=verb_pattern,
            scope=scope,
            ttl_seconds=ttl,
            rationale=(body.get("rationale") or "").strip(),
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "grant": g}


@web_route("POST", "/api/foundry/revoke")
async def api_foundry_revoke(self, request):
    """Revoke an mcp-client grant by id."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    gid = (body.get("grant_id") or "").strip()
    if not gid:
        return {"ok": False, "error": "grant_id required"}
    return {"ok": _autopilot.revoke_grant(self.autopilot_root(), gid)}


@web_route("GET", "/api/autopilot/review")
async def api_autopilot_review(self, request):
    """Periodic grant re-review — stale/expiring classification + weekly
    usage roll-up from the audit chain. Read-only; revocation goes through
    the existing revoke paths."""
    return _autopilot.review_grants(self.autopilot_root())


async def panel_autopilot_review(self):
    """Hub panel: autopilot grants needing a look + this week's activity.

    Returns None (panel invisible) until autopilot is in real use — at
    least one grant or one audit entry must exist.
    """
    rep = _autopilot.review_grants(self.autopilot_root())
    if not rep["grants"] and not rep["audit_lines"]:
        return None
    rows = []
    by_id = {g.get("id"): g for g in rep["grants"]}
    for gid in rep["stale_ids"]:
        g = by_id.get(gid) or {}
        actor = g.get("actor") or {}
        rows.append({
            "title": f"⚠ {g.get('verb_pattern', '?')} · {actor.get('id', '?')}",
            "subtitle": "no fires in 14d — consider revoking",
            "href": "/agent/pages/foundry.html",
        })
    for gid in rep["expiring_ids"]:
        g = by_id.get(gid) or {}
        exp = (g.get("expires_at") or "")[:16]
        rows.append({
            "title": f"⏳ {g.get('verb_pattern', '?')}",
            "subtitle": f"expires {exp}",
            "href": "/agent/pages/foundry.html",
        })
    for actor_id in rep["budgets_over"]:
        rows.append({
            "title": f"💸 {actor_id} over budget",
            "subtitle": "auto-applies gate until the window rolls",
            "href": "/agent/pages/foundry.html",
        })
    week = rep["week"]
    rows.append({
        "title": f"fired {week['fired']}× this week",
        "subtitle": (f"{len(rep['grants'])} grant(s) · "
                     f"{week['ok']} ok / {week['failed']} failed"),
        "href": "/agent/pages/foundry.html",
    })
    return rows


@web_route("GET", "/api/tools")
async def api_list_tools(self, request):
    return [
        {
            "name": t.name,
            "description": t.description,
            "permission": t.permission,
            "input_schema": t.input_schema,
        }
        for t in self._tools.values()
    ]


# ── Skills / slash-commands catalog ────────────────────────────────────


@web_route("GET", "/api/skills")
async def api_skills(self, request):
    """Catalog of Claude-Code-compatible skills the agent can invoke via
    `/<skill-name>`. Returns {name, description, source} for each — the
    full SKILL.md body is loaded server-side on invocation, never
    shipped to the browser (it can be large)."""
    catalog = self._load_skill_catalog()
    return [
        {"name": s.name, "description": s.description, "source": s.source, "params": s.params}
        for s in sorted(catalog.values(), key=lambda s: s.name)
    ]


@web_route("GET", "/api/slash-commands")
async def api_slash_commands(self, request):
    """Shared slash-command list — same set for CLI and web."""
    from .repl import SLASH_COMMANDS

    return SLASH_COMMANDS


# ── Permissions ────────────────────────────────────────────────────────


@web_route("POST", "/api/permission/{req_id}/approve")
async def api_approve_permission(self, request):
    req_id = request.path_params["req_id"]
    data = await self.safe_json(request)
    scope = data.get("scope", "once")
    if scope not in ("once", "session"):
        scope = "once"
    tool_consent = self.service("tool_consent")
    if not tool_consent:
        return {"error": "tool_consent service not available"}
    ok = tool_consent.approve(req_id, scope=scope)
    return {"ok": ok}


@web_route("POST", "/api/permission/{req_id}/deny")
async def api_deny_permission(self, request):
    req_id = request.path_params["req_id"]
    tool_consent = self.service("tool_consent")
    if not tool_consent:
        return {"error": "tool_consent service not available"}
    ok = tool_consent.deny(req_id)
    return {"ok": ok}


@web_route("GET", "/api/permissions")
async def api_list_permissions(self, request):
    tool_consent = self.service("tool_consent")
    if not tool_consent:
        return {"pending": []}
    sid = request.query_params.get("session_id")
    return {"pending": tool_consent.pending_list(session_id=sid)}


# ── Agent-wide status summary ─────────────────────────────────────────


@web_route("GET", "/api/status")
async def api_status(self, request):
    """Summarize the agent's runtime state.

    Query ?session_id=<sid> scopes provider resolution to that session
    (honoring its `provider` field); otherwise reports the default.
    """
    sid = request.query_params.get("session_id", "")
    session_record = self._get_session(sid) if sid else None
    provider_name = (session_record or {}).get("provider") or self._default_provider_name()
    provider = self._resolve_provider(provider_name)

    provider_info: dict = {"requested": provider_name, "available": False}
    if provider is not None:
        provider_info.update(
            {
                "available": True,
                "name": provider.name,
                "kind": "native"
                if self._is_native_provider(provider)
                else getattr(provider, "kind", ""),
                "model": getattr(provider, "model", "") or "",
                "native_tool_summary": (
                    getattr(provider, "native_tool_summary", "")
                    if self._is_native_provider(provider)
                    else ""
                ),
            }
        )

    tool_consent = self.service("tool_consent")
    settings = self.service("settings")
    max_iters = DEFAULT_MAX_ITERS
    policy = "ask"
    if settings:
        try:
            max_iters = int(settings.get("agent.max_iters") or DEFAULT_MAX_ITERS)
        except (TypeError, ValueError):
            pass
        policy = settings.get("agent.tool_policy") or (
            getattr(tool_consent, "policy", "ask") if tool_consent else "ask"
        )

    return {
        "session_id": sid or None,
        "session": {
            "name": (session_record or {}).get("name") if session_record else None,
            "message_count": len((session_record or {}).get("messages", []) or [])
            if session_record
            else 0,
        }
        if session_record
        else None,
        "provider": provider_info,
        "tools": {
            "count": len(self._tools),
            "names": list(self._tools.keys()),
        },
        "policy": policy,
        "max_iters": max_iters,
        "pending_permissions": (
            len(tool_consent.pending_list(session_id=sid or None)) if tool_consent else 0
        ),
    }


# ── Tool-hook helpers (registered in AgentApp.setup) ───────────────────


def task_persist_hook(self, session_id: str, tool_name: str, input: dict, result) -> None:
    """After-hook: persist TaskList state to disk so it survives daemon restarts."""
    if tool_name != "TaskList" or result is None or not result.ok:
        return
    tasks = (result.display or {}).get("tasks")
    if not tasks:
        return
    try:
        path = self.data_dir / f"tasks_{session_id}.json"
        path.write_text(_json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def audit_log_hook(self, session_id: str, tool_name: str, input: dict, result) -> None:
    """After-hook: append one JSON line to tool-audit.jsonl."""
    # Summarise large inputs so the log stays readable
    input_summary: dict = {}
    for k, v in (input or {}).items():
        sv = str(v)
        input_summary[k] = sv[:120] + "…" if len(sv) > 120 else sv
    entry = {
        "ts": _dt.utcnow().isoformat() + "Z",
        "session": session_id,
        "tool": tool_name,
        "input": input_summary,
        "ok": result.ok if result is not None else False,
    }
    # Correlation with the turn's agent:* + think:executed events.
    trace_id = current_trace_id()
    if trace_id:
        entry["trace_id"] = trace_id
    display = getattr(result, "display", None)
    if isinstance(display, dict) and "duration_ms" in display:
        entry["duration_ms"] = display["duration_ms"]
    try:
        with self._audit_path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
