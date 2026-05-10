"""Fetch tool — HTTP requests so the agent can verify its own work.

Dedicated tool (rather than `curl` via Bash) because:
  1. Works identically on Windows / Linux / macOS without shelling out.
  2. Permission defaults can be scoped by URL (localhost = auto, public = ask).
  3. Returns a structured result the agent can reason about without parsing
     curl's text output.

Auto-approves GET requests to localhost / loopback / private-IP hosts (the
common case: pinging the running EmptyOS daemon to check it after an edit).
Everything else asks permission.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from emptyos.sdk.agent_tools.base import Tool, ToolResult

DEFAULT_TIMEOUT = 30
MAX_BODY_CHARS = 30_000


def _is_local_host(host: str) -> bool:
    """True if `host` is loopback or a private-network IP. Conservative:
    unknown hostnames (anything not an IP or 'localhost') are treated as
    non-local so they go through the permission gate."""
    if not host:
        return False
    h = host.lower().strip("[]")  # strip IPv6 brackets
    if h in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


class FetchTool(Tool):
    name = "Fetch"
    description = (
        "Make an HTTP request so you can verify your own work — load a page, hit an API, "
        "check a response code. Use this (not Bash+curl) to test the running EmptyOS daemon "
        "at http://localhost:9000/... after a code change and restart. "
        "GET to localhost/loopback/private-IP auto-approves; other methods or public URLs "
        "ask permission. Response body is truncated at 30K chars. "
        "Returns `status`, `headers` (subset), and `body`."
    )
    permission = "ask"  # overridden per-call in permission_for
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL, e.g. http://localhost:9000/calculator/",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                "description": "HTTP method (default GET).",
            },
            "body": {
                "type": "string",
                "description": "Request body (string). JSON? Send the JSON string.",
            },
            "headers": {
                "type": "object",
                "description": "Extra headers as {name: value}. Content-Type defaults to application/json for POST/PUT/PATCH if a body is set.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default {DEFAULT_TIMEOUT}).",
            },
        },
        "required": ["url"],
    }

    def is_readonly(self, input: dict) -> bool:
        """Only GET is plan-mode safe. POST/PUT/PATCH/DELETE have potential side
        effects on whatever service is at the other end."""
        return (input.get("method") or "GET").upper() == "GET"

    def permission_for(self, input: dict) -> str:
        method = (input.get("method") or "GET").upper()
        url = input.get("url", "")
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            return "ask"
        if method == "GET" and _is_local_host(host):
            return "auto"
        return "ask"

    def permission_summary(self, input: dict) -> str:
        method = (input.get("method") or "GET").upper()
        url = input.get("url", "")
        body_preview = input.get("body", "")
        if body_preview and len(body_preview) > 100:
            body_preview = body_preview[:100] + "…"
        bits = [f"Fetch: {method} {url}"]
        if body_preview:
            bits.append(f"body: {body_preview}")
        return "  ".join(bits)

    async def run(self, app, **kwargs) -> ToolResult:
        url = (kwargs.get("url") or "").strip()
        if not url:
            return ToolResult(ok=False, content="error: url is required")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult(
                ok=False,
                content=f"error: unsupported scheme {parsed.scheme!r} (use http:// or https://)",
            )

        method = (kwargs.get("method") or "GET").upper()
        body_raw = kwargs.get("body")
        headers = dict(kwargs.get("headers") or {})
        t_raw = kwargs.get("timeout")
        try:
            timeout = int(t_raw) if t_raw is not None else DEFAULT_TIMEOUT
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT

        # Default Content-Type for body-bearing methods when the caller didn't specify.
        if (
            body_raw
            and method in ("POST", "PUT", "PATCH")
            and not any(k.lower() == "content-type" for k in headers)
        ):
            headers["Content-Type"] = "application/json"

        try:
            import aiohttp
        except ImportError:
            return ToolResult(ok=False, content="error: aiohttp is not installed")

        data = body_raw.encode("utf-8") if isinstance(body_raw, str) else body_raw

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method,
                    url,
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=True,
                ) as resp:
                    raw = await resp.read()
                    ct = resp.headers.get("Content-Type", "")
                    try:
                        body_text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        body_text = f"<{len(raw)} bytes binary, content-type={ct!r}>"
                    truncated = False
                    if len(body_text) > MAX_BODY_CHARS:
                        body_text = body_text[:MAX_BODY_CHARS]
                        truncated = True
                    status = resp.status
                    # Return a small, useful header subset — full headers blow up context.
                    keep_headers = {}
                    for k in ("Content-Type", "Content-Length", "Location", "Server"):
                        v = resp.headers.get(k)
                        if v:
                            keep_headers[k] = v
        except TimeoutError:
            return ToolResult(
                ok=False,
                content=f"error: {method} {url} timed out after {timeout}s",
            )
        except aiohttp.ClientError as e:
            return ToolResult(ok=False, content=f"error: {method} {url}: {e}")
        except Exception as e:
            return ToolResult(ok=False, content=f"error: {e}")

        summary = f"{method} {url} → {status}"
        body_label = f"\n\n{body_text}"
        if truncated:
            body_label += f"\n\n... (truncated at {MAX_BODY_CHARS} chars)"
        headers_block = "\n".join(f"{k}: {v}" for k, v in keep_headers.items())
        content = f"{summary}\n{headers_block}{body_label}"

        return ToolResult(
            ok=200 <= status < 400,
            content=content,
            display={
                "name": "Fetch",
                "method": method,
                "url": url,
                "status": status,
                "content_type": keep_headers.get("Content-Type", ""),
                "bytes": len(raw),
            },
        )
