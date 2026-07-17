"""Assistant — legacy REST chat + compare + provider/slash listings.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the non-streaming `/api/chat` endpoint (back-compat path
for callers that can't speak WS), `/api/compare` (fan-out to all
providers), and the read-only `/api/providers` + `/api/slash-commands`
discovery endpoints.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules:
  - slash.py:    self._handle_slash / self._slash_commands
  - context.py:  self._build_context / self._build_system / self._build_chat_messages
  - tools.py:    self._chat_with_tools / self._use_tools_default
  - sessions.py: self._sessions_lock / self._add_message / self._get_session
  - spine:       self._get_provider / self._think_with_provider (BaseApp)
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   api_chat            = _api_chat.api_chat
#   api_providers       = _api_chat.api_providers
#   api_slash_commands  = _api_chat.api_slash_commands
#   api_compare         = _api_chat.api_compare
#   api_dispatch        = _api_chat.api_dispatch
#   api_propose_kb_note = _api_chat.api_propose_kb_note
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


# Verbs the dispatch endpoint refuses to execute even when the user clicks
# Apply on an extension card. These are the same shapes the rooms gate
# routes through ALWAYS_GATE_VERBS — diff-preview / irreversible-publish
# verbs whose value IS the review step. Forcing them through /rooms/
# keeps the diff-render path the only way to land them.
_DISPATCH_DENY: set[tuple[str, str]] = {
    ("rooms", "write_note"),
    ("repo", "edit"),
    ("repo", "write"),
    ("repo", "exec"),
    ("publish", "deploy"),
}


@web_route("POST", "/api/chat")
async def api_chat(self, request):
    data = await request.json()
    message = data.get("message", "")
    session_id = data.get("session_id", "")
    if not message:
        return {"error": "message required"}

    # Slash command check
    if message.startswith("/"):
        result = await self._handle_slash(message)
        if result:
            if session_id:
                async with self._sessions_lock:
                    self._add_message(session_id, "user", message)
                    self._add_message(session_id, "assistant", result, agent="system")
            return {"response": result, "message": message, "provider": "system"}

    # Tool-use retrieval path — opt-in per request, or via setting default.
    use_tools = bool(data.get("use_tools", self._use_tools_default()))

    session = None
    if session_id:
        async with self._sessions_lock:
            self._add_message(session_id, "user", message)
        session = self._get_session(session_id)

    if use_tools:
        text, prov_name, tool_log = await self._chat_with_tools(message, session)
        if session_id and text:
            async with self._sessions_lock:
                self._add_message(session_id, "assistant", text, agent=f"tools:{prov_name}")
        return {
            "response": text,
            "message": message,
            "provider": f"tools:{prov_name}",
            "tool_calls": tool_log,
        }

    # Classic path — keyword-grep context + single LLM call.
    context = ""
    twophase = False
    scoped_paths: list = []
    if data.get("context", True):
        if self.app_config("feature.companion-twophase.enabled", False):
            try:
                scoped, scoped_paths = await self._scoped_context(message, session=session)
            except Exception:
                scoped, scoped_paths = None, []
            if scoped is not None:
                context, twophase = scoped, True
        if not twophase:
            context = await self._build_context(message, session=session)

    system = await self._build_system()

    if session:
        chat_messages = self._build_chat_messages(session, vault_context=context)
    else:
        user_content = f"[Vault context]\n{context}\n\n{message}" if context else message
        chat_messages = [{"role": "user", "content": user_content}]

    provider = self._get_provider(message, session) if session else "openai"
    # Cheap fast preliminary on the two-phase path (Phase 2 below stays on the
    # normal chain). Only when the user hasn't pinned a specific backend.
    if twophase and (session or {}).get("backend", "auto") == "auto":
        _pp = str(self.app_config("companion.preliminary_provider", "") or "")
        if _pp:
            provider = _pp
    try:
        result = await self._think_with_provider(
            provider,
            "",
            "text",
            {"system": system, "messages": chat_messages},
        )
        if not result:
            result = await self.think(
                messages=chat_messages,
                system=system,
                domain="text",
                temperature=0.4,
            )
            provider = "default"
    except RuntimeError as e:
        # AI-offline → let the server.py middleware turn it into a 503; a 200 OK
        # with "Error: ..." in the body looks like a real reply to the UI.
        if "No available provider for capability" in str(e):
            raise
        result = f"Error: {e}"
        provider = "error"
    except Exception as e:
        result = f"Error: {e}"
        provider = "error"

    # Phase 2 (two-phase companion): the answer above came from a scoped slice;
    # always verify against the whole vault, RESOLVED BY SOURCE-NOTE RECENCY. No
    # streaming channel here, so block on both. prefer_full supersedes; conflict
    # returns both sides (the caller surfaces the disagreement, picks neither).
    verdict = None
    correction = None
    conflict = None
    if twophase and not str(result).startswith("Error:"):
        try:
            full_v, _, full_paths = await self._full_answer(message, session=session)
            differ = (full_v or "").strip() and full_v.strip() != str(result).strip()
            verdict = await self._reconcile(
                message, result, full_v, scoped_paths, full_paths
            ) if differ else "consistent"
            if verdict == "prefer_full" and differ:
                correction, result = full_v, full_v
            elif verdict == "conflict" and differ:
                conflict = {
                    "scoped": result, "full": full_v,
                    "scoped_sources": self._provenance_items(scoped_paths),
                    "full_sources": self._provenance_items(full_paths),
                    "scoped_paths": scoped_paths, "full_paths": full_paths,
                }
        except Exception:
            verdict = None

    if session_id:
        async with self._sessions_lock:
            self._add_message(session_id, "assistant", result, agent=provider)

    out = {"response": result, "message": message, "provider": provider}
    if verdict is not None:
        out["verdict"] = verdict
    if correction:
        out["correction"] = correction
    if conflict:
        out["conflict"] = conflict
    return out


@web_route("GET", "/api/providers")
async def api_providers(self, request):
    """Available LLM providers."""
    cap = self.kernel.capability("think")
    providers = []
    seen = set()
    for p in cap.providers:
        if p.name not in seen:
            try:
                avail = await p.available()
            except Exception:
                avail = False
            providers.append({"name": p.name, "available": avail})
            seen.add(p.name)
    return providers


@web_route("GET", "/api/slash-commands")
async def api_slash_commands(self, request):
    """List available slash commands."""
    cmds = [
        {"command": c, "app": a, "method": m}
        for c, (a, m, _) in sorted(self._slash_commands.items())
    ]
    cmds.append({"command": "/help", "app": "assistant", "method": "help"})
    cmds.append({"command": "/new", "app": "assistant", "method": "new session"})
    cmds.append(
        {
            "command": "/research",
            "app": "assistant",
            "method": "browse top results + synthesize cited report",
        }
    )
    return cmds


@web_route("POST", "/api/compare")
async def api_compare(self, request):
    """Send same prompt to ALL providers. Returns [{provider, response, latency_ms}].

    Persists into the session if `session_id` is supplied — user message +
    one combined assistant message containing every provider's reply, so
    the conversation survives refresh.
    """
    data = await request.json()
    message = data.get("message", "")
    session_id = data.get("session_id", "")
    if not message:
        return {"error": "message required"}

    if session_id:
        async with self._sessions_lock:
            self._add_message(session_id, "user", message)

    session = self._get_session(session_id) if session_id else None
    context = await self._build_context(message, session=session)
    system = await self._build_system()
    prompt = f"Vault context:\n{context}\n\nQuestion: {message}" if context else message

    results = await self.think_compare(prompt, system=system, domain="text")
    # Normalize — extract text from CapabilityResult objects
    normalized = []
    for r in results:
        resp = r.get("response", "")
        if hasattr(resp, "value"):
            resp = resp.value
        normalized.append(
            {
                "provider": r.get("provider", "unknown"),
                "text": str(resp)[:4000],
                "latency_ms": r.get("latency_ms", 0),
                "error": r.get("error"),
            }
        )

    if session_id and normalized:
        # Combine into one assistant turn so the next chat turn sees one
        # coherent prior reply, not N parallel "assistant" rows.
        blocks = []
        for r in normalized:
            if r.get("error"):
                blocks.append(f"**{r['provider']}** — *error:* {r['error']}")
            else:
                blocks.append(f"**{r['provider']}** ({r['latency_ms']}ms):\n{r['text']}")
        combined = "\n\n---\n\n".join(blocks)
        async with self._sessions_lock:
            self._add_message(session_id, "assistant", combined, agent="compare")

    return {"question": message, "results": normalized}


@web_route("POST", "/api/dispatch")
async def api_dispatch(self, request):
    """Execute a single ``[DO:app.method({...})]``-shaped action on behalf of an
    external surface (the chrome-extension side panel today).

    Body: ``{app, method, args, source_actor?: {type, id}}``

    Refuses verbs whose review IS the gate (free-form vault writes, code edits,
    irreversible publishes) — those must go through ``/rooms/`` so the diff /
    impact preview renders. Everything else is dispatched via ``kernel.call_app``
    and the result returned to the caller. The user clicked Apply on a card
    surfaced in the panel, so we treat this as the explicit gate; the deny
    list is the eligibility floor.

    Returns ``{ok, result?, error?, app, method}``. Audit lands in syslog with
    ``actor: extension``.
    """
    data = await request.json()
    app_id = (data.get("app") or "").strip()
    method = (data.get("method") or "").strip()
    args = data.get("args") or {}
    actor = data.get("source_actor") or {"type": "extension", "id": "chrome-ext"}

    if not app_id or not method:
        return {"ok": False, "error": "app + method required"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object"}

    if (app_id, method) in _DISPATCH_DENY:
        return {
            "ok": False,
            "app": app_id,
            "method": method,
            "error": (
                f"{app_id}.{method} requires the rooms review gate — open "
                "/rooms/ to apply this action with its diff/impact preview."
            ),
        }

    if app_id not in self.kernel.apps.instances:
        return {"ok": False, "app": app_id, "method": method, "error": "unknown app"}

    try:
        result = await self.call_app(app_id, method, **args)
        ok = True
        err = None
    except Exception as e:
        result = None
        ok = False
        err = f"{type(e).__name__}: {e}"

    # Best-effort audit; never break the action path.
    try:
        self.log_info(
            f"dispatch actor={actor.get('type','?')}/{actor.get('id','?')} "
            f"verb={app_id}.{method} ok={ok}"
        )
    except Exception:
        pass

    payload = {"ok": ok, "app": app_id, "method": method}
    if ok:
        payload["result"] = str(result)[:1000] if result is not None else ""
    else:
        payload["error"] = err
    return payload


@web_route("POST", "/api/browser-session/snapshots")
async def api_browser_session_snapshots(self, request):
    """Read selected armed tabs via directed Chrome commands without logging bodies."""
    data = await request.json()
    raw_ids = data.get("tab_ids") or []
    if not isinstance(raw_ids, list) or len(raw_ids) > 10:
        return {"ok": False, "error": "tab_ids must contain at most 10 tabs"}
    try:
        tab_ids = [int(value) for value in raw_ids]
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid tab_id"}
    snapshots = []
    for tab_id in tab_ids:
        try:
            snap = await self.browse("snapshot", target="user-chrome", tab_id=tab_id, timeout_s=20)
            snapshots.append({"tab_id": tab_id, "ok": True, "snapshot": snap})
        except Exception as error:
            snapshots.append({"tab_id": tab_id, "ok": False, "error": str(error)[:200]})
    return {"ok": True, "snapshots": snapshots}


@web_route("POST", "/api/propose-kb-note")
async def api_propose_kb_note(self, request):
    """Propose a KB note for user-reviewed creation.

    Thin HTTP wrapper around ``BaseApp.propose_kb_note`` so non-Python
    surfaces (chrome-extension selection → KB clause) can file proposals
    against the rooms pending queue. The user reviews + applies in
    ``/rooms/`` exactly as for any other proposed KB note.

    Body: ``{kind?, title, body?, domain?, topic?, references?, related?,
    source?, room_id?}``. Defaults: ``kind="clause"``. ``source`` is
    typically the URL the selection came from.
    """
    data = await request.json()
    title = (data.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title required"}
    try:
        action = await self.propose_kb_note(
            kind=(data.get("kind") or "clause"),
            title=title,
            body=data.get("body", "") or "",
            domain=data.get("domain", "") or "",
            topic=data.get("topic", "") or "",
            references=data.get("references") or [],
            related=data.get("related") or [],
            source=data.get("source", "") or "",
            room_id=data.get("room_id", "") or "",
        )
        return {"ok": True, "action_id": action.get("id"), "action": action}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
