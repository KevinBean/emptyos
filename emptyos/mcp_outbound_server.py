"""EmptyOS OUTBOUND MCP Connector Foundry — stdio JSON-RPC bridge that exposes
a *curated, grant-gated* subset of EmptyOS app verbs to an external MCP client.

This is the OUTBOUND direction and a SIBLING of ``emptyos/mcp_server.py`` (the
INBOUND stdio proxy that claude-cli spawns to expose dev tools). The inbound
server is untouched; this module is launched independently by an external MCP
client via its own dotfile ``mcpServers`` entry::

    {
      "mcpServers": {
        "emptyos-foundry": {
          "command": "python",
          "args": ["-m", "emptyos.mcp_outbound_server"],
          "env": {"EMPTYOS_PORT": "9000", "EMPTYOS_MCP_CLIENT_ID": "<client-id>"}
        }
      }
    }

The server connects to the already-running daemon over HTTP (no daemon spawn,
no restart). It reuses the exact MCP JSON-RPC 2.0 framing from
``emptyos/mcp_server.py`` (line-delimited JSON, ``_log`` to stderr so stdout
stays a clean JSON stream, initialize / notifications/initialized / tools/list
/ tools/call dispatch).

Differences from the inbound server — this module OWNS the gate, because the
daemon's ``POST /agent/api/mcp/tools/call`` endpoint deliberately has no gate:

  1. FLAG          — ``feature.mcp-foundry.enabled`` (dark default false). When
                     off, ``tools/list`` advertises ZERO tools and every
                     ``tools/call`` is refused.
  2. ALLOWLIST     — only verbs the daemon aggregates from per-app
                     ``[provides.mcp_foundry]`` manifests (via
                     ``GET /agent/api/mcp/foundry/verbs``) are ever advertised /
                     dispatched. Non-allowlisted names are refused. Fetched
                     once + cached; fail-closed to an empty set.
  3. ELIGIBILITY   — every advertised + dispatched verb is re-checked against
                     ``autopilot.is_eligible`` (the HARD FLOOR). A
                     ``policy.json`` edit that removes a verb instantly stops
                     advertising and dispatching it. Non-eligible verbs
                     (rooms.write_note, publish.deploy, notifications.send)
                     can NEVER be exposed regardless of any grant.
  4. GRANT MATCH   — every ``tools/call`` must be covered by an
                     ``actor_type="mcp-client"`` autopilot grant. With no
                     grant-issuing surface shipping this session, every call is
                     denied at this step by default (intended dark posture).
  5. DISPATCH      — only with a covering grant; forwarded to the daemon's
                     in-process CallApp via ``POST /agent/api/mcp/tools/call``.

The autopilot store is read directly from ``data/autopilot/`` via
``emptyos.sdk.autopilot`` (a pure module — importing it does NOT boot the
kernel or open a syslog handle, so it's daemon-safe). The audit chain records
both the no-grant denial (step 4) and the dispatch (step 5).
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from emptyos.sdk import autopilot

# ── Connection + identity ────────────────────────────────────────────────
EMPTYOS_PORT = os.environ.get("EMPTYOS_PORT", "9000")
BASE_URL = f"http://127.0.0.1:{EMPTYOS_PORT}"

# Names the external client connecting to us, for grant matching. Defaults to
# "default" so a future "any mcp-client" grant (empty actor.id) still applies.
MCP_CLIENT_ID = os.environ.get("EMPTYOS_MCP_CLIENT_ID", "default") or "default"

# When the daemon runs in private/public mode it requires an Authorization:
# Bearer <auth_token> header on every request. Env override wins; otherwise we
# read network.auth_token from emptyos.toml. Empty when auth isn't required.
_auth_token_cache: str | None = None

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "emptyos-foundry", "version": "1.0.0"}

# ── Flag (dark default) ──────────────────────────────────────────────────
# Canonical key: emptyos.toml [apps.agent] feature.mcp-foundry.enabled = false.
# agent owns the inbound MCP surface, so the foundry flag living next to it is
# coherent. Read directly from emptyos.toml (matches BaseApp.app_config's
# `apps.<id>.<key>` semantics) — fail closed on any error.
FLAG_HOST = "agent"
FLAG_KEY = "feature.mcp-foundry.enabled"
FLAG_DOTTED = f"apps.{FLAG_HOST}.{FLAG_KEY}"

# ── Outbound allowlist (per-app manifest opt-in, daemon-aggregated) ─────────
# The advertised verb set is sourced from the daemon's
# ``GET /agent/api/mcp/foundry/verbs`` endpoint, which aggregates each app's
# ``[provides.mcp_foundry] verbs`` and floors them by autopilot eligibility.
# We additionally re-check ``autopilot.is_eligible()`` at tools/list build time
# AND tools/call time (defence in depth — the daemon already floored, but the
# stdio server enforces it again so a stale daemon can never widen the set).
# Fetched once, cached; fail-closed to an empty list (exposes nothing).
_foundry_verbs_cache: list[str] | None = None

_data_dir_cache: Path | None = None
_verb_schema_cache: dict[str, dict] | None = None


def _foundry_verbs() -> list[str]:
    """Foundry-exposed verbs, aggregated by the daemon from per-app manifests
    and floored by autopilot eligibility. Fail-closed to ``[]`` so a fetch
    failure (or an old daemon without the endpoint) exposes nothing."""
    global _foundry_verbs_cache
    if _foundry_verbs_cache is not None:
        return _foundry_verbs_cache
    verbs: list[str] = []
    try:
        with _daemon_get("/agent/api/mcp/foundry/verbs", timeout=5) as r:
            data = json.loads(r.read())
        raw = data.get("verbs") if isinstance(data, dict) else None
        if isinstance(raw, list):
            verbs = [s for s in raw if isinstance(s, str) and "." in s]
    except Exception as e:
        _log(f"[mcp_outbound] could not fetch foundry verbs ({e}); exposing none")
        verbs = []
    _foundry_verbs_cache = verbs
    return verbs


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ── Project / data-dir resolution (no kernel boot) ────────────────────────
def _project_root() -> Path:
    # This module lives at <root>/emptyos/mcp_outbound_server.py.
    return Path(__file__).resolve().parent.parent


def _toml_path() -> Path:
    return _project_root() / "emptyos.toml"


def _resolve_data_dir() -> Path:
    """Resolve the daemon's data dir the same way the kernel does, WITHOUT
    booting the kernel: read os.data_dir from emptyos.toml (default ./data),
    rooted at the project root so relative paths point at the daemon's store."""
    global _data_dir_cache
    if _data_dir_cache is not None:
        return _data_dir_cache
    root = _project_root()
    raw = "./data"
    try:
        with open(_toml_path(), "rb") as f:
            cfg = tomllib.load(f)
        node = cfg.get("os", {})
        if isinstance(node, dict) and node.get("data_dir"):
            raw = str(node["data_dir"])
    except Exception as e:
        _log(f"[mcp_outbound] could not read emptyos.toml for data_dir ({e}); "
             f"falling back to {raw}")
    p = Path(raw)
    if not p.is_absolute():
        p = (root / p).resolve()
    _data_dir_cache = p
    return p


def _auth_token() -> str:
    """Daemon bearer token (env EMPTYOS_AUTH_TOKEN > emptyos.toml network.auth_token).
    Empty string when auth isn't configured. Read once, cached."""
    global _auth_token_cache
    if _auth_token_cache is not None:
        return _auth_token_cache
    tok = (os.environ.get("EMPTYOS_AUTH_TOKEN") or "").strip()
    if not tok:
        try:
            with open(_toml_path(), "rb") as f:
                cfg = tomllib.load(f)
            net = cfg.get("network", {})
            if isinstance(net, dict):
                tok = str(net.get("auth_token") or "").strip()
        except Exception as e:
            _log(f"[mcp_outbound] could not read auth_token ({e})")
            tok = ""
    _auth_token_cache = tok
    return tok


def _daemon_headers() -> dict:
    h = {"Content-Type": "application/json"}
    tok = _auth_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _daemon_get(path: str, timeout: int = 5):
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=_daemon_headers(), method="GET")
    return urllib.request.urlopen(req, timeout=timeout)


def _toml_node(dotted: str, label: str):
    """Walk a dotted key through emptyos.toml. None when the key is missing
    or the file is unreadable (logged) — callers coerce None to their own
    fail-closed default."""
    try:
        with open(_toml_path(), "rb") as f:
            cfg = tomllib.load(f)
    except Exception as e:
        _log(f"[mcp_outbound] {label} read failed ({e}); failing closed")
        return None
    node = cfg
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _read_flag() -> bool:
    """Read feature.mcp-foundry.enabled from emptyos.toml. Fail closed.

    Canonical location matches BaseApp.app_config semantics
    (``apps.<host>.feature.mcp-foundry.enabled``). Any failure → False.
    """
    node = _toml_node(FLAG_DOTTED, "flag")
    if isinstance(node, bool):
        return node
    if isinstance(node, str):
        return node.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(node, (int, float)):
        return bool(node)
    return False


def _daemon_reachable() -> bool:
    """Fail closed if the daemon is unreachable — we proxy dispatch to it."""
    try:
        with _daemon_get("/settings/api/config", timeout=5) as r:
            r.read(1)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            _log(f"[mcp_outbound] daemon at {BASE_URL} requires auth (HTTP {e.code}); "
                 f"set EMPTYOS_AUTH_TOKEN or network.auth_token in emptyos.toml")
        else:
            _log(f"[mcp_outbound] daemon probe failed HTTP {e.code}")
        return False
    except Exception as e:
        _log(f"[mcp_outbound] daemon unreachable at {BASE_URL} ({e})")
        return False


def _flag_on() -> bool:
    """Read the foundry flag fresh on every check (no caching) so flipping
    feature.mcp-foundry.enabled OFF in emptyos.toml disables a running foundry
    on the next tools/call or resources/read — without a subprocess relaunch."""
    return _read_flag()


# ── KB exposure allowlist (fail-closed, sibling of vault_tags_allow) ────────
# Canonical key: emptyos.toml [apps.agent]
# feature.mcp-foundry.kb_kinds_allow = ["pattern", ...]. KB note BODIES are
# vault content the foundry otherwise never exposes (/api/vault/read is
# unmapped; eos://vault/query is frontmatter-only), so a note is readable only
# when its `kind` frontmatter is in this list. Empty / absent / unreadable →
# expose nothing — the same regression posture the vault_tags_allow gate pins.
# Read fresh per call (no caching) so a config edit takes effect live.
KB_KINDS_DOTTED = f"apps.{FLAG_HOST}.feature.mcp-foundry.kb_kinds_allow"


def _kb_kinds_allow() -> list[str]:
    node = _toml_node(KB_KINDS_DOTTED, "kb_kinds_allow")
    if isinstance(node, list):
        return [s.strip() for s in node if isinstance(s, str) and s.strip()]
    return []


# ── Verb → tool schema (B) ────────────────────────────────────────────────
def _fetch_verb_schemas() -> dict[str, dict]:
    """Map each allowlisted verb to an MCP {name, description, inputSchema}
    entry, sourced from the daemon's /agent/api/mcp/tools registry where a
    matching tool name exists. Verbs without a registry hit still get a
    minimal passthrough schema (free-form object) so they remain callable.

    The registry's CallApp tool is what actually dispatches — the per-verb
    schema is advisory metadata for the client. We key by the verb string
    (e.g. "task.add"), which is the advertised MCP tool name.
    """
    global _verb_schema_cache
    if _verb_schema_cache is not None:
        return _verb_schema_cache
    registry: dict[str, dict] = {}
    try:
        with _daemon_get("/agent/api/mcp/tools", timeout=5) as r:
            for t in json.loads(r.read()):
                if isinstance(t, dict) and t.get("name"):
                    registry[t["name"]] = t
    except Exception as e:
        _log(f"[mcp_outbound] could not fetch tool registry ({e}); "
             f"using passthrough schemas")
    schemas: dict[str, dict] = {}
    for verb in _foundry_verbs():
        app_id, _, method = verb.partition(".")
        # Prefer a registry tool whose name matches the verb (rare); otherwise
        # advertise a passthrough object schema that forwards verbatim to
        # CallApp(**arguments). Description names the underlying app + method.
        reg = registry.get(verb)
        if reg:
            schemas[verb] = {
                "name": verb,
                "description": reg.get("description") or f"{app_id}.{method}",
                "inputSchema": reg.get("inputSchema")
                or {"type": "object", "properties": {}, "additionalProperties": True},
            }
        else:
            schemas[verb] = {
                "name": verb,
                "description": f"EmptyOS verb {app_id}.{method} (foundry, grant-gated)",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            }
    _verb_schema_cache = schemas
    return schemas


def _advertised_tools() -> list[dict]:
    """Tools/list payload: allowlist ∩ flag-on ∩ daemon-reachable ∩ eligible.

    Fail closed → empty list when the flag is off or the daemon is unreachable.
    Eligibility is re-checked per verb so a policy.json edit instantly drops a
    verb from the advertised set.
    """
    if not _flag_on() or not _daemon_reachable():
        return []
    data_dir = _resolve_data_dir()
    schemas = _fetch_verb_schemas()
    out: list[dict] = []
    for verb in _foundry_verbs():
        if not autopilot.is_eligible(data_dir, verb):
            _log(f"[mcp_outbound] verb {verb!r} not eligible; not advertising")
            continue
        schema = schemas.get(verb)
        if schema is None:
            _log(f"[mcp_outbound] verb {verb!r} has no fetched schema; skipping")
            continue
        out.append(schema)
    return out


# ── tools/call gate flow (C) ──────────────────────────────────────────────
def _mcp_text(text: str) -> dict:
    """MCP-shaped result. Refusals ride in the text field so the client sees a
    readable refusal rather than a JSON-RPC transport error."""
    return {"content": [{"type": "text", "text": text}]}


def _dispatch_to_daemon(app_id: str, method: str, arguments: dict) -> tuple[bool, str]:
    """Forward to the daemon's in-process CallApp. call_app is kwargs-only —
    arguments is passed straight through as the inner arguments dict, splatted
    to **kwargs by CallAppTool.run. Returns (ok, content)."""
    payload = json.dumps({
        "name": "CallApp",
        "arguments": {"app_id": app_id, "method": method, "arguments": arguments},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/agent/api/mcp/tools/call",
        data=payload,
        headers=_daemon_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            result = json.loads(r.read())
        content = result.get("content", "")
        return bool(result.get("ok", False)), (str(content) if content is not None else "")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return False, f"error: HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return False, f"error: {type(e).__name__}: {e}"


def _handle_call(params: dict) -> dict:
    """Strict, fail-closed gate. Each step returns an MCP-shaped text result;
    the JSON-RPC envelope itself stays a successful result so the client sees
    the refusal rather than a transport error."""
    verb = (params.get("name") or "").strip()
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}

    data_dir = _resolve_data_dir()

    # 1. FLAG — re-checked here (boot cache), not just at tools/list.
    if not _flag_on():
        _log(f"[mcp_outbound] call refused: flag off ({verb!r})")
        return _mcp_text("error: mcp-foundry disabled")

    # 2. ALLOWLIST — guard against a client calling a name we never advertised.
    if verb not in _foundry_verbs():
        _log(f"[mcp_outbound] call refused: {verb!r} not in allowlist")
        return _mcp_text(f"error: tool '{verb}' not in foundry allowlist")

    # 3. IS_ELIGIBLE — HARD FLOOR. policy.json removal blocks dispatch.
    if not autopilot.is_eligible(data_dir, verb):
        _log(f"[mcp_outbound] call refused: {verb!r} not eligible")
        return _mcp_text(f"error: verb '{verb}' not autopilot-eligible")

    # 4. MATCH (grant) — per-server scope. No grant-issuing surface ships this
    #    session, so this is where every call stops by default.
    grant = autopilot.match(
        data_dir,
        actor_type="mcp-client",
        actor_id=MCP_CLIENT_ID,
        verb=verb,
        scope_candidates=[f"mcp:{MCP_CLIENT_ID}", "global"],
    )
    app_id, _, method = verb.partition(".")
    if grant is None:
        try:
            autopilot.append_audit(
                data_dir,
                actor={"type": "mcp-client", "id": MCP_CLIENT_ID},
                app=app_id,
                method=method,
                args=arguments,
                grant_id=None,
                ok=False,
                error="no_grant",
            )
        except Exception as e:
            _log(f"[mcp_outbound] audit (no_grant) failed: {e}")
        return _mcp_text(
            f"error: no autopilot grant for '{verb}' "
            f"(actor mcp-client:{MCP_CLIENT_ID})"
        )

    # 5. DISPATCH — only reached with a covering grant.
    ok, content = _dispatch_to_daemon(app_id, method, arguments)
    try:
        autopilot.append_audit(
            data_dir,
            actor={"type": "mcp-client", "id": MCP_CLIENT_ID},
            app=app_id,
            method=method,
            args=arguments,
            grant_id=grant.get("id"),
            ok=ok,
            error=None if ok else content,
        )
    except Exception as e:
        _log(f"[mcp_outbound] audit (dispatch) failed: {e}")
    return _mcp_text(content)


# ── MCP resources (read-only work-graph; flag-gated, no grant needed) ──────
# Each resource is a thin wrapper over an EXISTING daemon endpoint. The set is
# a CLOSED allowlist: ``_resolve_resource_path`` returns None for anything not
# explicitly mapped, so no write endpoint and no unmapped/traversal path is
# reachable. Resources are graph metadata, not state changes, so they're gated
# on flag-on + daemon-reachable only (no autopilot grant — that gates writes).
_STATIC_RESOURCES: list[tuple[str, str, str]] = [
    ("eos://apps", "/api/apps", "EmptyOS apps (id, name, capabilities, state)"),
    ("eos://topology", "/api/topology", "Dependency topology graph"),
    ("eos://capabilities", "/api/capabilities/full", "Capability providers + chains"),
    ("eos://plugins", "/api/plugins", "Loaded plugins + the services they register"),
    ("eos://services", "/api/services", "Registered services"),
    ("eos://scheduler/jobs", "/api/scheduler/jobs", "Scheduled jobs"),
    ("eos://projects", "/projects/api/projects", "Projects (summary — bodies stripped)"),
]

_RESOURCE_TEMPLATES: list[tuple[str, str]] = [
    ("eos://app/{id}", "One app's full detail (routes, provides, requires)"),
    (
        "eos://vault/query?tags={tags}&folder={folder}",
        "Query vault notes by tag/folder — frontmatter only, tag-allowlist gated",
    ),
]

# KB templates are advertised only when feature.mcp-foundry.kb_kinds_allow is
# non-empty (advertise only what's reachable — same spirit as _advertised_tools).
_KB_RESOURCE_TEMPLATES: list[tuple[str, str]] = [
    (
        "eos://kb/notes?kind={kind}&domain={domain}",
        "KB note metadata list — kind must be in the kb_kinds_allow allowlist",
    ),
    (
        "eos://kb/note/{slug}",
        "One KB note with full body — kinds-allowlist gated post-fetch",
    ),
]


def _resolve_resource_path(uri: str) -> str | None:
    """Map an ``eos://`` resource URI to a daemon GET path. Closed allowlist —
    returns None for unknown / traversal / cross-path URIs. Read-only by
    construction: no URI maps to a write endpoint, and ``/api/vault/read``
    (full bodies) is deliberately unmapped."""
    from urllib.parse import urlsplit, parse_qs, urlencode, quote

    if not isinstance(uri, str) or ".." in uri:
        return None
    for u, path, _desc in _STATIC_RESOURCES:
        if uri == u:
            return path
    if uri.startswith("eos://app/"):
        app_id = uri[len("eos://app/"):].strip("/")
        if app_id and "/" not in app_id and ".." not in app_id:
            return f"/api/apps/{quote(app_id, safe='')}"
    if uri.startswith("eos://vault/query"):
        q = parse_qs(urlsplit(uri).query)
        params = {
            "tags": (q.get("tags") or [""])[0],
            "folder": (q.get("folder") or [""])[0],
        }
        kept = {k: v for k, v in params.items() if v}
        return "/api/vault/query" + ("?" + urlencode(kept) if kept else "")
    if uri.startswith("eos://kb/note/"):
        slug = uri[len("eos://kb/note/"):].strip("/")
        # Resolvable only while the allowlist is non-empty; the per-kind check
        # runs post-fetch in _read_resource (kind isn't derivable from a slug).
        if slug and "/" not in slug and _kb_kinds_allow():
            return f"/kb/api/notes/{quote(slug, safe='')}"
        return None
    if uri.startswith("eos://kb/notes"):
        q = parse_qs(urlsplit(uri).query)
        kind = (q.get("kind") or [""])[0]
        # kind is REQUIRED and must be allowlisted — a kind-less list would
        # leak metadata for non-allowed kinds.
        if not kind or kind not in _kb_kinds_allow():
            return None
        params = {"kind": kind}
        domain = (q.get("domain") or [""])[0]
        if domain:
            params["domain"] = domain
        return "/kb/api/notes?" + urlencode(params)
    return None


def _list_resources() -> dict:
    """resources/list payload. Flag-off or daemon-unreachable → empty."""
    if not _flag_on() or not _daemon_reachable():
        return {"resources": [], "resourceTemplates": []}
    resources = [
        {"uri": u, "name": u, "description": d, "mimeType": "application/json"}
        for (u, _path, d) in _STATIC_RESOURCES
    ]
    template_rows = list(_RESOURCE_TEMPLATES)
    if _kb_kinds_allow():
        template_rows += _KB_RESOURCE_TEMPLATES
    templates = [
        {"uriTemplate": t, "name": t, "description": d, "mimeType": "application/json"}
        for (t, d) in template_rows
    ]
    return {"resources": resources, "resourceTemplates": templates}


def _shrink_resource(uri: str, data):
    """Drop long-text fields that would bloat the client's context or leak
    note bodies through a summary endpoint."""
    if uri == "eos://projects" and isinstance(data, list):
        drop = {"body", "notes", "content", "tasks_text"}
        return [
            {k: v for k, v in p.items() if k not in drop} if isinstance(p, dict) else p
            for p in data
        ]
    if uri.startswith("eos://kb/note/") and isinstance(data, dict):
        # The body IS the payload here; drop the heavy graph fields
        # (backlinks/outgoing/clauses/implemented_in_status) instead.
        keep = {"slug", "name", "path", "properties", "body"}
        return {k: v for k, v in data.items() if k in keep}
    return data


def _kb_note_refusal(uri: str, data) -> str:
    """Post-fetch kind gate for ``eos://kb/note/{slug}`` — a note's kind isn't
    derivable from its slug, so the kinds-allowlist check runs against the
    fetched note's frontmatter. Returns a refusal message, or "" to let the
    payload through. kb's own not-found error JSON rides the normal path."""
    if not uri.startswith("eos://kb/note/"):
        return ""
    if not isinstance(data, dict) or data.get("error"):
        return ""
    props = data.get("properties") or {}
    kind = str(props.get("kind") or "") if isinstance(props, dict) else ""
    if kind not in _kb_kinds_allow():
        return f"error: kb kind '{kind or 'unknown'}' not in kb_kinds_allow"
    return ""


def _read_resource(params: dict) -> dict:
    """resources/read — fetch the backing endpoint and return its JSON as text.
    Refusals ride in the content text so the client sees a readable message."""
    uri = (params.get("uri") or "").strip()
    if not _flag_on():
        return {"contents": [{"uri": uri, "mimeType": "text/plain",
                              "text": "error: mcp-foundry disabled"}]}
    path = _resolve_resource_path(uri)
    if path is None:
        return {"contents": [{"uri": uri, "mimeType": "text/plain",
                              "text": f"error: unknown resource '{uri}'"}]}
    try:
        with _daemon_get(path, timeout=10) as r:
            data = json.loads(r.read())
        refusal = _kb_note_refusal(uri, data)
        if refusal:
            return {"contents": [{"uri": uri, "mimeType": "text/plain",
                                  "text": refusal}]}
        text = json.dumps(_shrink_resource(uri, data), ensure_ascii=False, indent=2)
        mime = "application/json"
    except Exception as e:
        text, mime = f"error: {type(e).__name__}: {e}", "text/plain"
    return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}


# ── MCP prompts (KB `kind: pattern` notes; flag + kinds-allowlist gated) ───
# A pattern note IS a prompt: reusable scaffolding (engineering anatomy +
# fenced code) the client injects as few-shot context — the same role
# resolve_pattern_examples plays for viz/cad internally. Read-only like
# resources (no grant); gated on "pattern" being in kb_kinds_allow so one
# config key governs all KB exposure. No `arguments` — patterns aren't
# templated.
def _list_prompts() -> dict:
    """prompts/list payload. Flag-off / daemon-unreachable / "pattern" not
    allowlisted → empty list (fail-closed, never an error)."""
    if not _flag_on() or "pattern" not in _kb_kinds_allow() or not _daemon_reachable():
        return {"prompts": []}
    prompts = []
    try:
        with _daemon_get("/kb/api/notes?kind=pattern", timeout=10) as r:
            data = json.loads(r.read())
        notes = data.get("notes") if isinstance(data, dict) else None
        for n in notes or []:
            if not isinstance(n, dict):
                continue
            slug = str(n.get("slug") or "").strip()
            if not slug:
                continue
            prompts.append({
                "name": slug,
                "description": str(n.get("title") or n.get("name") or slug),
            })
    except Exception as e:
        _log(f"[mcp_outbound] prompts/list failed ({e}); exposing none")
        return {"prompts": []}
    return {"prompts": prompts}


def _get_prompt(params: dict) -> dict:
    """prompts/get — full pattern-note body as one user message. Per MCP spec,
    refusals here are protocol errors (unlike resources, where refusal text
    rides in the contents)."""
    from urllib.parse import quote

    name = str(params.get("name") or "").strip()
    if not _flag_on():
        return {"error": {"code": -32602, "message": "mcp-foundry disabled"}}
    if "pattern" not in _kb_kinds_allow():
        return {"error": {"code": -32602,
                          "message": "kb patterns not exposed (kb_kinds_allow)"}}
    if not name or "/" in name or ".." in name:
        return {"error": {"code": -32602, "message": f"unknown prompt: {name!r}"}}
    try:
        with _daemon_get(f"/kb/api/notes/{quote(name, safe='')}", timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:
        return {"error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
    if not isinstance(data, dict) or data.get("error"):
        return {"error": {"code": -32602, "message": f"unknown prompt: {name!r}"}}
    props = data.get("properties") or {}
    kind = str(props.get("kind") or "") if isinstance(props, dict) else ""
    if kind != "pattern":
        return {"error": {"code": -32602,
                          "message": f"'{name}' is not a kb pattern note"}}
    title = ""
    if isinstance(props, dict):
        title = str(props.get("title") or "")
    return {
        "description": title or str(data.get("name") or name),
        "messages": [{
            "role": "user",
            "content": {"type": "text", "text": str(data.get("body") or "")},
        }],
    }


# ── JSON-RPC dispatch (framing reused verbatim from mcp_server.py) ─────────
def _handle(req: dict) -> dict:
    method = req.get("method", "")
    params = req.get("params") or {}

    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": SERVER_INFO,
        }

    if method == "notifications/initialized":
        return {}  # no-op notification

    if method == "tools/list":
        return {"tools": _advertised_tools()}

    if method == "tools/call":
        return _handle_call(params)

    if method == "resources/list":
        return _list_resources()

    if method == "resources/read":
        return _read_resource(params)

    if method == "prompts/list":
        return _list_prompts()

    if method == "prompts/get":
        return _get_prompt(params)

    return {"error": {"code": -32601, "message": f"Method not found: {method}"}}


def main():
    _log(f"[EmptyOS MCP Foundry] starting, daemon at {BASE_URL}, "
         f"client={MCP_CLIENT_ID!r}, flag={'on' if _flag_on() else 'off'}")
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError as e:
            _log(f"[mcp_outbound] JSON parse error: {e}")
            continue

        req_id = req.get("id")
        try:
            result = _handle(req)
        except Exception as e:
            _log(f"[mcp_outbound] handler error: {e}")
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": str(e)}}
            print(json.dumps(resp), flush=True)
            continue

        # Notifications (no id) don't get a response.
        if req_id is None and (req.get("method") or "").startswith("notifications/"):
            continue

        if "error" in result:
            resp = {"jsonrpc": "2.0", "id": req_id, "error": result["error"]}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
