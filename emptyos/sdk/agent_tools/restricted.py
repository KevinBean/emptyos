"""Narrowed tools for restricted agent profiles (the chat profile, B1).

A profile that hides a tool's schema does not bound what the offered tools can
reach. Two of the chat profile's tools were wider than its promise:

- **Fetch** could POST anywhere, including the daemon's own routes — and one of
  those (``/agent/api/mcp/tools/call``) runs any tool, Bash included. So chat
  gets ``WebFetchTool``: GET only, public web only (``is_public_web_url``, the
  SSRF guard web research already uses), every redirect hop re-checked.
- **CallApp** could call any public method on any app, down to ``BaseApp.write``
  and outbound ``send``. So chat gets ``VerbCallAppTool``: only methods an app
  declared as an invokable verb (``[[provides.verbs]]``) on the agent,
  assistant or mcp surface, never a verb whose eligibility is ``never``
  (irreversible / outbound / billing). The consent gate still asks per call.
- **Read** opened any absolute path with no prompt — ``emptyos.toml`` and its
  credentials included, one Fetch away from leaving the machine. So chat gets
  ``ScopedReadTool``: the vault and the configured file roots, never the repo.

Same names as the tools they narrow, so a chat transcript reads the same.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin

from emptyos.sdk.agent_tools.base import Tool, ToolResult
from emptyos.sdk.agent_tools.call_app import CallAppTool

#: Hops a redirect chain may take before WebFetch gives up — enough for the
#: common http→https→www→canonical chain, short enough to stop a loop.
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 20
MAX_BODY_CHARS = 30_000

#: Surfaces whose verbs a chat may call. ``voice`` is excluded: its handlers
#: return spoken ``{say, card}`` payloads, not data for a model.
CHAT_VERB_SURFACES = ("agent", "assistant", "mcp")


class WebFetchTool(Tool):
    name = "Fetch"
    description = (
        "Read a public web page or API over HTTP GET — for current or outside facts. "
        "Public internet only: localhost, private networks and this computer's own "
        "services are refused. Response body is truncated at 30K chars."
    )
    permission = "ask"
    readonly = True
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full http(s) URL on the public web."},
        },
        "required": ["url"],
    }

    def permission_summary(self, input: dict) -> str:
        return f"Fetch: GET {input.get('url', '')}"

    async def run(self, app, **kwargs) -> ToolResult:
        from emptyos.sdk.web_search import is_public_web_url

        url = (kwargs.get("url") or "").strip()
        method = (kwargs.get("method") or "GET").upper()
        if method != "GET":
            return ToolResult(ok=False, content=f"error: only GET is allowed here, not {method}")
        try:
            import aiohttp
        except ImportError:
            return ToolResult(ok=False, content="error: aiohttp is not installed")
        try:
            async with aiohttp.ClientSession() as session:
                for _hop in range(MAX_REDIRECTS + 1):
                    if not await asyncio.to_thread(is_public_web_url, url):
                        return ToolResult(ok=False, content=f"error: {url} is not a public web address")
                    async with session.get(
                        url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
                    ) as resp:
                        if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                            url = urljoin(url, resp.headers["Location"])
                            continue
                        raw = await resp.read()
                        status = resp.status
                        ctype = resp.headers.get("Content-Type", "")
                        break
                else:
                    return ToolResult(ok=False, content=f"error: more than {MAX_REDIRECTS} redirects")
        except TimeoutError:
            return ToolResult(ok=False, content=f"error: GET {url} timed out after {DEFAULT_TIMEOUT}s")
        except Exception as e:
            return ToolResult(ok=False, content=f"error: GET {url}: {e}")
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            body = f"<{len(raw)} bytes binary, content-type={ctype!r}>"
        note = ""
        if len(body) > MAX_BODY_CHARS:
            body, note = body[:MAX_BODY_CHARS], f"\n\n... (truncated at {MAX_BODY_CHARS} chars)"
        return ToolResult(
            ok=200 <= status < 400,
            content=f"GET {url} → {status}\nContent-Type: {ctype}\n\n{body}{note}",
            display={"name": "Fetch", "method": "GET", "url": url, "status": status, "bytes": len(raw)},
        )


def chat_read_roots(kernel) -> list:
    """Directories a chat may read: the vault, plus any configured
    ``[capabilities.search.files] allowed_roots`` — minus the EmptyOS repo,
    whose ``emptyos.toml`` holds the daemon's credentials. (Locate's default
    roots are ``[vault, repo]``; a chat must not Read the repo half of them.)
    """
    from pathlib import Path

    config = getattr(kernel, "config", None)
    notes = getattr(config, "notes_path", None) if config else None
    try:
        repo = Path(config.path).parent.resolve() if config and getattr(config, "path", None) else None
    except Exception:
        repo = None
    try:
        section = config.get_section("capabilities.search.files") or {} if config else {}
    except Exception:
        section = {}
    roots = []
    candidates = [notes] + [
        notes if r in ("notes", "vault") else (None if r == "repo" else r)
        for r in (section.get("allowed_roots") or [])
    ]
    for c in candidates:
        if not c:
            continue
        try:
            p = Path(c).resolve()
        except Exception:
            continue
        # A root that contains the repo (a whole drive, the parent folder)
        # would hand the repo back; a root inside the repo is fine only if it
        # is the vault itself (a sandbox keeps its vault under the repo).
        if repo and (repo == p or repo.is_relative_to(p)):
            continue
        if repo and p.is_relative_to(repo) and not (notes and p == Path(notes).resolve()):
            continue
        if p not in roots:
            roots.append(p)
    return roots


class ScopedReadTool(Tool):
    """Read, confined to ``chat_read_roots``. Relative paths are vault-relative."""

    name = "Read"
    description = (
        "Read a file from the user's vault, or a file Locate found in their allowed "
        "folders. Paths may be vault-relative (e.g. 10_Projects/x/x.md) or absolute. "
        "Returns contents with line numbers; pass offset/limit for long files."
    )
    permission = "auto"
    readonly = True

    def __init__(self):
        from emptyos.sdk.agent_tools.read import ReadTool

        self._inner = ReadTool()
        self.input_schema = self._inner.input_schema

    async def run(self, app, **kwargs) -> ToolResult:
        from pathlib import Path

        kernel = getattr(app, "kernel", None)
        roots = chat_read_roots(kernel) if kernel else []
        raw = str(kwargs.get("path") or "").strip()
        if not raw:
            return ToolResult(ok=False, content="error: path is required")
        if not roots:
            return ToolResult(ok=False, content="error: no readable folders are configured")
        p = Path(raw)
        target = (p if p.is_absolute() else roots[0] / p).resolve()
        if not any(target == r or target.is_relative_to(r) for r in roots):
            return ToolResult(ok=False, content=f"error: {raw} is outside the folders this chat may read")
        return await self._inner.run(app, **{**kwargs, "path": str(target)})


def chat_verbs(kernel) -> dict[tuple[str, str], object]:
    """``{(app_id, method): VerbEntry}`` a chat may call."""
    try:
        registry = kernel.apps.get_verbs()
    except Exception:
        return {}
    out = {}
    for e in registry:
        if e.eligibility == "never":
            continue
        for s in CHAT_VERB_SURFACES:
            if s in e.surfaces:
                out[(e.app_id, e.method_for(s))] = e
    return out


class VerbCallAppTool(CallAppTool):
    description = (
        "Call one of the EmptyOS apps' declared actions (add a task, read today's "
        "journal, list projects, …). Call with no arguments to list every action "
        "available, with its arguments; then call app_id + method + arguments."
    )

    def permission_summary(self, input: dict) -> str:
        app_id = (input.get("app_id") or "").strip()
        if not app_id or not (input.get("method") or "").strip():
            return "CallApp: list the available actions" + (f" of {app_id}" if app_id else "")
        return super().permission_summary(input)

    async def run(self, app, **kwargs) -> ToolResult:
        kernel = getattr(app, "kernel", None)
        if kernel is None:
            return ToolResult(ok=False, content="error: no kernel context")
        allowed = chat_verbs(kernel)
        app_id = (kwargs.get("app_id") or "").strip()
        method = (kwargs.get("method") or "").strip()
        if not app_id or not method:
            rows = sorted(
                (a, m, e) for (a, m), e in allowed.items() if not app_id or a == app_id
            )
            if not rows:
                return ToolResult(ok=False, content=f"error: no actions available{' on ' + app_id if app_id else ''}")
            lines = [
                f"  - {a}.{m}({', '.join(f'{k}: {v}' for k, v in (e.args or {}).items())})"
                + (f" — {e.summary}" if e.summary else "")
                for a, m, e in rows
            ]
            return ToolResult(ok=True, content="Available actions:\n" + "\n".join(lines),
                              display={"actions": [f"{a}.{m}" for a, m, _ in rows]})
        if (app_id, method) not in allowed:
            return ToolResult(
                ok=False,
                content=f"error: {app_id}.{method} is not an action available here. "
                "Call CallApp with no arguments to list the ones that are.",
            )
        return await super().run(app, **kwargs)
