"""agent — MCP connectors a user can add, enable and disconnect (desktop GUI B5).

Inbound MCP already worked, from `[[apps.agent.mcp_servers]]` rows read once at
boot: to add a server you edited `emptyos.toml` and restarted the daemon, and
every connected server's tools were offered to every session. This adds the
half a chat home needs — a **store** the user can write at runtime, a
**per-chat** enabled set, and **connect / disconnect without a restart**.

Four things are deliberate:

- **Every add is propose → confirm, and the confirmation is a TOKEN.** The
  first call holds the row server-side and returns a one-shot token with a
  preview; the second call sends only that token, so what is written is the row
  that was shown. A self-asserted ``confirm: true`` flag would be no gate at
  all — a caller can set it on the first request, and in
  ``network.mode = "local"`` there is no credential and no Origin check, so
  "a caller" includes any page the user happens to open. HTTP rows are proposed
  too: a URL is not a process, but a ``${ENV}`` header means adding one can send
  a named secret to a host of the caller's choosing. See
  `.claude/rules/proposed-action.md` — the proposal is *captured*, and applying
  replays it.
- **Secrets are referenced, never stored.** A header or env value written as
  `${NAME}` is expanded from the daemon's environment at connect time, so the
  store keeps the *name*. It is not an allowlist: any variable the daemon has
  can be named, which is why the confirm card says which one and where it will
  go. Reading a row back masks a literal credential — including in ``args`` and
  a URL's query string, where MCP servers most often carry one — while leaving
  a ``${NAME}`` reference legible, because the name is what the user must check.
- **Refused in public mode, on every path.** The routes refuse, and so does the
  BOOT path (``_maybe_connect_inbound_mcp`` consults ``_connectors_enabled``) —
  gating only the routes would leave the claim true of the UI and false of the
  execution, which is the half that spawns.
- **There is no SSRF gate on an added URL** — ``mcp_client``'s HTTP transport
  posts wherever the row says, and aiohttp follows redirects. For a local
  single-user daemon that is the point (an MCP server is usually on loopback or
  the tailnet, which a public-web gate would refuse), so the containment is the
  confirm card naming the host, not a network rule. Do not read this module as
  sanitising the destination.

Extracted from app.py per .claude/rules/multi-module-apps.md. Owns: the server
store, the runtime connect/disconnect, and the routes over both.
Reaches into other modules: none (the app's ``_mcp_clients`` list and
``_tools`` registry are reached through ``self``).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.agent_tools.base import feature_enabled

if TYPE_CHECKING:
    from .app import AgentApp  # noqa: F401 — for type hints only


# ─── Bind to AgentApp class as ────────────────────────────────
#   _connectors_enabled   = _connectors._connectors_enabled
#   _connector_store_path = _connectors._connector_store_path
#   _load_connectors      = _connectors._load_connectors
#   _save_connectors      = _connectors._save_connectors
#   _connector_rows       = _connectors._connector_rows
#   _put_proposal         = _connectors._put_proposal
#   _take_proposal        = _connectors._take_proposal
#   _connect_connector    = _connectors._connect_connector
#   _disconnect_connector = _connectors._disconnect_connector
#   _refused_connectors   = _connectors._refused_connectors
#   api_connectors        = _connectors.api_connectors
#   api_connector_add     = _connectors.api_connector_add
#   api_connector_remove  = _connectors.api_connector_remove
#   api_connector_connect = _connectors.api_connector_connect
#   api_session_connectors = _connectors.api_session_connectors
# Adding a new method here? Add a matching binding line in app.py.
# ──────────────────────────────────────────────────────────────

#: `${NAME}` in a header or env value — a reference to the daemon's environment.
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: An id becomes part of every tool name (``mcp__<id>__<tool>``), which the
#: model matches literally and the per-chat set stores comma-separated.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

MAX_SERVERS = 24


# ── Pure helpers ─────────────────────────────────────────────────────

def expand_env_refs(value, environ=None) -> tuple[str, list[str]]:
    """``"Bearer ${TOK}"`` → the expanded string, plus the names that were MISSING.

    A missing reference expands to an empty string rather than being left as
    the literal ``${TOK}``: sending the placeholder to a server would put the
    text ``${TOK}`` in an Authorization header, which looks like a working
    credential in a log and is not one. The caller decides whether a missing
    name is fatal.
    """
    env = os.environ if environ is None else environ
    missing: list[str] = []

    def sub(m):
        name = m.group(1)
        if name in env:
            return str(env[name])
        missing.append(name)
        return ""

    return ENV_REF.sub(sub, str(value or "")), missing


def expand_mapping(mapping: dict | None, environ=None) -> tuple[dict, list[str]]:
    """``expand_env_refs`` over a header / env dict."""
    out, missing = {}, []
    for k, v in (mapping or {}).items():
        val, miss = expand_env_refs(v, environ)
        out[str(k)] = val
        missing.extend(miss)
    return out, missing


def _arg_list(value) -> list[str]:
    """``args`` as a list of strings, refusing a shape that would be mangled.

    A bare string is NOT split here: iterating it would turn "npx -y srv" into
    twelve single-character arguments, which is a valid list by the time
    ``valid_server_row`` sees it and nonsense by the time it is spawned. A dict
    would silently become its keys.
    """
    if isinstance(value, list):
        return [str(a) for a in value]
    return []


def command_line(row: dict) -> str:
    """The exact argv a stdio server will run, one token per line-part.

    Every argument is shown, including empty ones (``""`` — which the spawn
    path does pass and which a space-joined line would hide), and every one is
    quoted so a token containing a quote or a space cannot re-group into
    something that reads as two arguments. Quoting is for READING: argv goes to
    ``create_subprocess_exec`` directly, never through a shell, so nothing here
    implies a metacharacter would be interpreted.
    """
    parts = [str(row.get("command") or "")] + [str(a) for a in (row.get("args") or [])]
    return " ".join(json.dumps(p) for p in parts)


def preview_of(row: dict, *, default_cwd: str = "") -> dict:
    """What the confirm card must show before this row is written.

    ``env_refs`` is the load-bearing one for an HTTP row: adding a connector
    whose header is ``${OPENAI_API_KEY}`` sends that key to whatever host the
    row names, so the card names both the variable and the destination.
    """
    is_stdio = bool(row.get("command"))
    refs = sorted({
        m for v in list((row.get("env") or {}).values()) + list((row.get("headers") or {}).values())
        for m in ENV_REF.findall(str(v))
    })
    out = {
        "transport": "stdio" if is_stdio else "http",
        "env_refs": refs,
        "message": (
            "Adding this connector runs a program on this machine, every time the "
            "daemon starts. Read the command below before confirming."
            if is_stdio else
            "Adding this connector lets the daemon call this URL on your behalf."
        ),
    }
    if is_stdio:
        out["command_line"] = command_line(row)
        # The cwd the child will ACTUALLY get: an empty cwd means it inherits
        # the daemon's, which is not necessarily the config's directory.
        out["cwd"] = row.get("cwd") or default_cwd
        out["cwd_inherited"] = not row.get("cwd")
    else:
        out["url"] = row.get("url", "")
    if refs:
        out["message"] += (
            f" It will send the value of {', '.join(refs)} "
            f"to {'that program' if is_stdio else out.get('url') or 'that host'}."
        )
    return out


#: A proposal lives only until it is confirmed or goes stale. In memory on
#: purpose: it is a UI moment, and a file would be one more thing to tamper
#: with. Losing them on restart is correct — the card is gone too.
PROPOSAL_TTL_S = 300


def _put_proposal(self, row: dict) -> str:
    store = getattr(self, "_connector_proposals", None)
    if store is None:
        store = self._connector_proposals = {}
    now = time.time()
    for tok in [t for t, (ts, _) in store.items() if now - ts > PROPOSAL_TTL_S]:
        store.pop(tok, None)
    token = secrets.token_urlsafe(18)
    store[token] = (now, dict(row))
    return token


def _take_proposal(self, token: str) -> dict | None:
    """One shot: a token names a row once, then is gone."""
    store = getattr(self, "_connector_proposals", None) or {}
    entry = store.pop(token, None)
    if not entry:
        return None
    ts, row = entry
    return None if (time.time() - ts) > PROPOSAL_TTL_S else row


#: Shapes that are a credential wherever they appear. Deliberately narrow: a
#: mask that fires on ordinary arguments makes the panel unreadable, and a
#: connector's whole value is that a user can see what it will run.
SECRETISH = re.compile(
    r"^(sk-|pk-|ghp_|gho_|github_pat_|xox[abprs]-|AKIA|ya29\.|eyJ[A-Za-z0-9_-]{10,}\.)",
)


def mask_secret_value(value) -> str:
    """A header / env value safe to show.

    A value that REFERENCES the environment carries no secret — only a name —
    and the name is exactly what a user needs to read back to check they typed
    the right one. So ``Bearer ${OPENAI_API_KEY}`` is shown as written, while a
    value with a literal in it is masked. Masking the reference too (the first
    version did, because it required the *whole* value to be a reference) made
    the one field the user must verify unreadable.
    """
    s = str(value or "")
    if not s:
        return ""
    if ENV_REF.search(s) and not SECRETISH.search(s):
        return s
    return "••••"


def mask_value(value) -> str:
    """A value safe to show: an env reference as written, a credential-shaped
    string masked, anything else verbatim."""
    s = str(value or "")
    if ENV_REF.fullmatch(s.strip()) or not s:
        return s
    return "••••" if SECRETISH.match(s.strip()) else s


def mask_url(url: str) -> str:
    """The URL without its query string, which is where a token rides."""
    s = str(url or "")
    base, sep, _q = s.partition("?")
    return base + ("?••••" if sep else "")


def redact_row(row: dict) -> dict:
    """A server row safe to hand a page: `${NAME}` references are kept as
    written (they name an env var, they are not the secret), and any literal
    header or env value is masked — a user who pasted a raw token must not
    have it read back to them on a screen, or into a bug report."""
    def mask(mapping):
        out = {}
        for k, v in (mapping or {}).items():
            out[str(k)] = mask_secret_value(str(v))
        return out

    return {
        "id": row.get("id", ""),
        "transport": "http" if (row.get("url") and not row.get("command")) else "stdio",
        "command": row.get("command", ""),
        # argv and the URL carry secrets too — `--api-key sk-…` is the most
        # common MCP-server shape and a token in a query string the second.
        # Masking only env/headers would have left the two commonest places a
        # pasted credential lives readable on screen and in a bug report.
        "args": [mask_value(a) for a in (row.get("args") or [])],
        "url": mask_url(row.get("url", "")),
        "cwd": row.get("cwd") or "",
        "env": mask(row.get("env")),
        "headers": mask(row.get("headers")),
        "source": row.get("source", "store"),
    }


def valid_server_row(row: dict) -> str:
    """"" when the row is a usable server, else the reason it is not."""
    if not isinstance(row, dict):
        return "not a server"
    sid = str(row.get("id") or "").strip()
    if not ID_RE.match(sid):
        return "id must be lowercase letters, digits, - or _ (max 32)"
    cmd = str(row.get("command") or "").strip()
    url = str(row.get("url") or "").strip()
    if not cmd and not url:
        return "a server needs either a command (stdio) or a url (http)"
    if cmd and url:
        return "give a command or a url, not both"
    if url and not url.startswith(("http://", "https://")):
        return "url must be http(s)"
    if not isinstance(row.get("args") or [], list):
        return "args must be a list"
    return ""


# ── Store ────────────────────────────────────────────────────────────

def _connectors_enabled(self) -> bool:
    """The existing inbound-MCP flag governs this too: a UI for a feature whose
    tools are never registered would be a door onto a room with no floor."""
    if str(self.kernel.config.get("network.mode", "local")) == "public":
        return False
    return bool(feature_enabled(self, "mcp-inbound"))


def _connector_store_path(self):
    return self.data_dir / "mcp_servers.json"


def _load_connectors(self) -> list[dict]:
    """User-added servers. Machine state (`data/`), not vault content."""
    p = self._connector_store_path()
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt store must not break boot
        self.log("connectors: store unreadable, treating as empty", level="warning")
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def _save_connectors(self, rows: list[dict]) -> None:
    p = self._connector_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: a torn write leaves unreadable JSON, and _load_connectors then
    # reports an empty store — every connector the user added, silently gone
    # (.claude/rules/atomic-persistence.md).
    from emptyos.sdk import atomic_write_text

    atomic_write_text(p, json.dumps(rows, indent=2, ensure_ascii=False))


def _connector_rows(self) -> list[dict]:
    """Config rows and store rows as one list, config first.

    A `[[apps.agent.mcp_servers]]` row is the operator's declaration and cannot
    be edited or removed from the UI — it is in a file the user owns, and
    silently shadowing it from a page would make the toml a lie. A store row
    reusing a config id is dropped for the same reason.
    """
    out: list[dict] = []
    seen = set()
    for r in self.app_config("mcp_servers", []) or []:
        if isinstance(r, dict) and r.get("id"):
            out.append({**r, "source": "config"})
            seen.add(str(r["id"]))
    for r in self._load_connectors():
        if str(r.get("id")) not in seen:
            out.append({**r, "source": "store"})
    return out


# ── Runtime connect / disconnect ─────────────────────────────────────

def _connected_ids(self) -> set:
    return {c.spec.id for c in (getattr(self, "_mcp_clients", None) or [])}


async def _connect_connector(self, row: dict) -> dict:
    """Connect one server NOW and register its tools. No restart.

    Returns ``{ok, id, tools}`` or ``{ok: False, error}``. Every failure is an
    answer, never a raise: a server that is not running is the normal state for
    a desktop app, and the daemon must not care.
    """
    from emptyos.sdk.agent_tools.mcp_proxy import proxy_tools_for
    from emptyos.sdk.mcp_client import connect_mcp_servers, specs_from_config

    sid = str(row.get("id") or "")
    if sid in _connected_ids(self):
        return {"ok": False, "error": f"{sid} is already connected"}

    env, missing_env = expand_mapping(row.get("env"), None)
    headers, missing_hdr = expand_mapping(row.get("headers"), None)
    missing = sorted(set(missing_env + missing_hdr))
    if missing:
        # Fail loudly rather than connecting without the credential: a server
        # that answers "unauthorized" reads as a broken server, not as a
        # missing environment variable.
        return {"ok": False, "error": f"environment variable(s) not set: {', '.join(missing)}"}

    specs = specs_from_config([{**row, "env": env, "headers": headers}])
    if not specs:
        return {"ok": False, "error": "not a usable server row"}
    try:
        clients = await connect_mcp_servers(specs)
    except Exception as e:  # noqa: BLE001 — a bad server is an answer
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not clients:
        return {"ok": False, "error": "server did not answer"}

    added = []
    for client in clients:
        self._mcp_clients.append(client)
        for tool in proxy_tools_for(client):
            self._tools[tool.name] = tool
            added.append(tool.name)
    self.log(f"connectors: {sid} connected, {len(added)} tool(s)")
    return {"ok": True, "id": sid, "tools": added}


async def _disconnect_connector(self, sid: str) -> dict:
    """Stop a connected server and take its tools back out of the registry.

    Tools are unregistered before the transport closes, which is the right
    order but not a guarantee: ``select_session_tools`` hands a RESTRICTED
    profile (every chat) a snapshot of the registry, so a turn already running
    keeps the tool it was given and will get a transport error rather than an
    unknown-tool one. A coding session holds the registry object itself and
    does see the removal mid-turn. Neither case loses data — the failure is an
    error message either way — and closing that gap means versioning the tool
    set per turn, which is a change to the loop, not to this function.
    """
    sid = str(sid or "")
    keep, stopped = [], []
    for client in getattr(self, "_mcp_clients", None) or []:
        if client.spec.id == sid:
            stopped.append(client)
        else:
            keep.append(client)
    if not stopped:
        return {"ok": False, "error": f"{sid} is not connected"}
    prefix = f"mcp__{sid}__"
    removed = [n for n in list(self._tools) if n.startswith(prefix)]
    for n in removed:
        self._tools.pop(n, None)
    self._mcp_clients = keep
    for client in stopped:
        try:
            await client.stop()
        except Exception as e:  # noqa: BLE001 — already unregistered; log and move on
            self.log(f"connectors: {sid} did not stop cleanly: {e}", level="warning")
    return {"ok": True, "id": sid, "removed": removed}


# ── Routes ───────────────────────────────────────────────────────────

def _refused_connectors(self) -> dict | None:
    if self._connectors_enabled():
        return None
    return {"error": "connectors are off (feature.mcp-inbound.enabled), or this is a public deployment"}


@web_route("GET", "/api/connectors")
async def api_connectors(self, request) -> dict:
    """Every declared server, whether it is connected, and its tools."""
    refusal = self._refused_connectors()
    if refusal:
        return {**refusal, "connectors": [], "enabled": False}
    live = _connected_ids(self)
    rows = []
    for row in self._connector_rows():
        sid = str(row.get("id"))
        out = redact_row(row)
        out["connected"] = sid in live
        out["tools"] = sorted(n for n in self._tools if n.startswith(f"mcp__{sid}__"))
        rows.append(out)
    return {"enabled": True, "connectors": rows}


@web_route("POST", "/api/connectors")
async def api_connector_add(self, request) -> dict:
    """Add a server — always propose first, then confirm by TOKEN.

    A first call returns ``needs_confirmation`` with a preview and a one-shot
    token; the second call sends **only** that token, and what is written is
    the row the server held, not the row the second request carries.

    That indirection is the whole gate. A self-asserted ``confirm: true`` flag
    would let a caller skip the preview entirely by sending it on the FIRST
    request — and in ``network.mode = "local"`` there is no credential and no
    Origin check, so "a caller" includes any page the user happens to open. An
    MCP connector spawns a process or sends credentials outbound, so that is
    remote code execution reachable from a web page. Per
    ``.claude/rules/proposed-action.md``, the proposal is *captured*, and
    applying is a replay of exactly what was shown.

    HTTP rows are proposed too, not written directly: a URL is not a process,
    but ``${ENV}`` headers mean adding one can send a named secret to a host of
    the caller's choosing, which deserves the same look.
    """
    refusal = self._refused_connectors()
    if refusal:
        return refusal
    body = await self.read_json(request)

    token = str(body.get("token") or "").strip()
    if token:
        row = _take_proposal(self, token)
        if row is None:
            return {"error": "that confirmation expired — review the connector again"}
        # Re-check against the store as it is NOW: the id may have been taken
        # while the card was on screen.
        bad = valid_server_row(row)
        if bad:
            return {"error": bad}
        if str(row.get("id")) in {str(r.get("id")) for r in self._connector_rows()}:
            return {"error": f"a connector called {row['id']} already exists"}
        rows = self._load_connectors()
        if len(rows) >= MAX_SERVERS:
            return {"error": f"at most {MAX_SERVERS} connectors"}
        rows.append(row)
        self._save_connectors(rows)
        return {"ok": True, "connector": redact_row({**row, "source": "store"})}

    row = {
        "id": str(body.get("id") or "").strip().lower(),
        "command": str(body.get("command") or "").strip(),
        "args": _arg_list(body.get("args")),
        "env": {str(k): str(v) for k, v in (body.get("env") or {}).items()},
        "cwd": str(body.get("cwd") or "").strip(),
        "url": str(body.get("url") or "").strip(),
        "headers": {str(k): str(v) for k, v in (body.get("headers") or {}).items()},
    }
    bad = valid_server_row(row)
    if bad:
        return {"error": bad}
    existing = {str(r.get("id")) for r in self._connector_rows()}
    if row["id"] in existing:
        return {"error": f"a connector called {row['id']} already exists"}
    if len(self._load_connectors()) >= MAX_SERVERS:
        return {"error": f"at most {MAX_SERVERS} connectors"}

    # Propose: hold the row, show what it will do, write nothing.
    return {
        "ok": False,
        "needs_confirmation": True,
        "id": row["id"],
        "token": _put_proposal(self, row),
        **preview_of(row, default_cwd=str(self.kernel.config.path.parent)),
    }


@web_route("POST", "/api/connectors/{sid}/remove")
async def api_connector_remove(self, request) -> dict:
    """Forget a stored server, disconnecting it first if it is live."""
    refusal = self._refused_connectors()
    if refusal:
        return refusal
    sid = str(request.path_params.get("sid", ""))
    rows = self._load_connectors()
    kept = [r for r in rows if str(r.get("id")) != sid]
    if len(kept) == len(rows):
        # Either unknown, or declared in emptyos.toml — which a page must not
        # silently override; say which, so the user knows where to go.
        declared = {str(r.get("id")) for r in self._connector_rows() if r.get("source") == "config"}
        if sid in declared:
            return {"error": f"{sid} is declared in emptyos.toml — remove it there"}
        return {"error": f"no connector called {sid}"}
    if sid in _connected_ids(self):
        await self._disconnect_connector(sid)
    # Re-read AFTER the await. Stopping a stdio server waits on the child, and
    # a connector added in that window would otherwise be erased by the list
    # read before it (.claude/rules/dev-gotchas.md § Async atomicity: a
    # read-modify-write is atomic only while nothing awaits inside it).
    self._save_connectors([r for r in self._load_connectors() if str(r.get("id")) != sid])
    return {"ok": True, "id": sid}


@web_route("POST", "/api/connectors/{sid}/connect")
async def api_connector_connect(self, request) -> dict:
    """Connect or disconnect one server now. Body ``{"connect": false}`` stops it."""
    refusal = self._refused_connectors()
    if refusal:
        return refusal
    sid = str(request.path_params.get("sid", ""))
    body = await self.read_json(request)
    if body.get("connect") is False:
        return await self._disconnect_connector(sid)
    row = next((r for r in self._connector_rows() if str(r.get("id")) == sid), None)
    if not row:
        return {"error": f"no connector called {sid}"}
    return await self._connect_connector(row)


@web_route("POST", "/api/sessions/{sid}/connectors")
async def api_session_connectors(self, request) -> dict:
    """Which connectors THIS chat may use (profiles.select_session_tools).

    Per-chat for a RESTRICTED profile — which is every chat, and the only
    surface that shows the picker. The ``coding`` profile takes the whole
    registry and ignores this column entirely, so a connected server's tools
    are visible to /agent/ and ``eos chat`` regardless of what any chat chose:
    connecting is the decision there, not this.
    """
    refusal = self._refused_connectors()
    if refusal:
        return refusal
    sid = str(request.path_params.get("sid", ""))
    if not self._get_session(sid):
        return {"error": "session not found"}
    body = await self.read_json(request)
    known = {str(r.get("id")) for r in self._connector_rows()}
    wanted = [str(x).strip() for x in (body.get("connectors") or []) if str(x).strip()]
    unknown = sorted(set(wanted) - known)
    if unknown:
        return {"error": f"no such connector(s): {', '.join(unknown)}"}
    value = ",".join(dict.fromkeys(wanted))
    self._sessions.update_session(sid, connectors=value)
    return {"ok": True, "connectors": [x for x in value.split(",") if x]}
