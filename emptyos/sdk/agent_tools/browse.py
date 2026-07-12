"""Browse tool — drive a real (headless) browser step by step.

`Screenshot` renders a URL once and reports what it looked like. `Browse` is the
interactive sibling: the agent navigates, clicks, fills, reads, and re-observes
in a loop over a *persistent* browser session — the way a user verifies a
multi-step UI flow (open a form → fill it → submit → check the result), not just
pinging endpoints with `Fetch`.

It delegates to the `browse` capability (the shared Playwright plugin,
`plugins/playwright/`), so it reuses the daemon's single Chromium process and its
per-`context_id` sessions — no second browser runtime is spawned (the older
`Screenshot` tool launches its own; this one doesn't).

Actions (mirror the plugin's verb set):
  navigate(url)               — load a URL; returns url + title
  click(selector)             — click an element
  fill(selector, value)       — type into an input
  screenshot([selector])      — PNG to disk; returns the path
  snapshot([selector])        — read the page/element innerText (+ html length)
  eval(expression)            — run a JS expression, return its value
  wait_for(selector, [state]) — wait for an element to reach a state
  close                       — end the session (drop cookies + the open page)

Sessions: pass `context_id` to keep cookies + the same page across calls
(defaults to "agent"). Reuse it to stay logged in; `close` to reset.

Safety posture (mirrors Screenshot/Fetch):
  • Pure observation auto-approves — screenshot / snapshot / wait_for / close,
    and navigate to a localhost/loopback/private host. navigate to a public URL
    and every act verb (click / fill / eval) ask permission; eval runs arbitrary
    JS so it always asks.
  • readonly (plan-mode safe): navigate / screenshot / snapshot / wait_for.
    click / fill / eval / close mutate and are blocked in plan mode.
  • Dark-flagged: only added to the agent registry when
    `[apps.agent] feature.browser.enabled` is true (see AgentApp.setup).
"""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlparse

from emptyos.sdk.agent_tools.base import Tool, ToolResult

MAX_BODY_CHARS = 4_000
MAX_VALUE_CHARS = 2_000
DEFAULT_CONTEXT = "agent"

# Every invokable verb, and the two safety subsets.
_ACTIONS = ("navigate", "click", "fill", "screenshot", "snapshot", "eval", "wait_for", "close")
# Side-effect-free for the world → plan-mode safe.
_READONLY_ACTIONS = {"navigate", "screenshot", "snapshot", "wait_for"}
# Pure observation → auto-approve regardless of host (they can't act).
_AUTO_ACTIONS = {"screenshot", "snapshot", "wait_for", "close"}


def _is_local_host(host: str) -> bool:
    """True if `host` is loopback or a private-network IP. Conservative: unknown
    hostnames (anything not an IP or 'localhost') are treated as non-local so
    they go through the permission gate.

    NOTE: copied from screenshot.py / fetch.py — third occurrence. If a fourth
    appears, extract to agent_tools/base.py (CLAUDE.md rule 9)."""
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


class BrowseTool(Tool):
    name = "Browse"
    description = (
        "Drive a real headless browser step by step over a persistent session — "
        "navigate, click, fill, snapshot (read innerText), screenshot, eval (JS), "
        "wait_for, close. Use this when Fetch/Screenshot aren't enough: a multi-step "
        "UI flow you need to click through and verify (open a form → fill → submit → "
        "check the result), the way a user would. Pass `context_id` to keep cookies + "
        "the same page across calls (default 'agent'); `close` resets. After a click or "
        "fill, call `snapshot` or `screenshot` to see what the page looks like now. "
        "Observation verbs + navigate-to-localhost auto-approve; navigate to a public "
        "URL and every act verb (click/fill/eval) ask permission."
    )
    permission = "ask"  # overridden per-call in permission_for
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_ACTIONS),
                "description": "The browser verb to run.",
            },
            "url": {"type": "string", "description": "For navigate — full http(s) URL."},
            "selector": {
                "type": "string",
                "description": "CSS selector — required for click/fill/wait_for, optional for "
                "screenshot/snapshot (defaults to the whole page).",
            },
            "value": {"type": "string", "description": "For fill — the text to type."},
            "expression": {
                "type": "string",
                "description": "For eval — a JS expression, e.g. 'document.title' or "
                "'document.querySelectorAll(\"button\").length'.",
            },
            "state": {
                "type": "string",
                "enum": ["visible", "attached", "hidden", "detached"],
                "description": "For wait_for — the state to wait for (default 'visible').",
            },
            "full_page": {
                "type": "boolean",
                "description": "For screenshot — capture the whole scrollable page (default false).",
            },
            "context_id": {
                "type": "string",
                "description": "Browser session id; reuse to keep cookies + page across calls "
                "(default 'agent').",
            },
        },
        "required": ["action"],
    }

    def is_readonly(self, input: dict) -> bool:
        return (input.get("action") or "").strip() in _READONLY_ACTIONS

    def permission_for(self, input: dict) -> str:
        action = (input.get("action") or "").strip()
        if action in _AUTO_ACTIONS:
            return "auto"
        if action == "navigate":
            try:
                host = urlparse(input.get("url") or "").hostname or ""
            except Exception:
                return "ask"
            return "auto" if _is_local_host(host) else "ask"
        # click / fill / eval mutate page state (or run arbitrary JS) → always ask.
        return "ask"

    def permission_summary(self, input: dict) -> str:
        action = input.get("action") or "?"
        detail = (
            input.get("url")
            or input.get("selector")
            or input.get("expression")
            or ""
        )
        return f"Browse: {action} {detail}".strip()

    async def run(self, app, **kwargs) -> ToolResult:
        action = (kwargs.get("action") or "").strip()
        if action not in _ACTIONS:
            return ToolResult(
                ok=False,
                content=f"error: unknown action {action!r} (known: {list(_ACTIONS)})",
            )

        ctx = (kwargs.get("context_id") or DEFAULT_CONTEXT).strip() or DEFAULT_CONTEXT
        call: dict = {"context_id": ctx}

        if action == "navigate":
            url = (kwargs.get("url") or "").strip()
            if not url:
                return ToolResult(ok=False, content="error: navigate requires 'url'")
            if urlparse(url).scheme not in ("http", "https"):
                return ToolResult(
                    ok=False,
                    content=f"error: unsupported scheme (use http:// or https://): {url!r}",
                )
            call["url"] = url
        elif action in ("click", "fill", "wait_for"):
            selector = (kwargs.get("selector") or "").strip()
            if not selector:
                return ToolResult(ok=False, content=f"error: {action} requires 'selector'")
            call["selector"] = selector
            if action == "fill":
                call["value"] = kwargs.get("value") or ""
            if action == "wait_for" and kwargs.get("state"):
                call["state"] = kwargs["state"]
        elif action == "eval":
            expr = (kwargs.get("expression") or "").strip()
            if not expr:
                return ToolResult(ok=False, content="error: eval requires 'expression'")
            call["expression"] = expr
        elif action == "screenshot":
            if kwargs.get("selector"):
                call["selector"] = kwargs["selector"]
            if kwargs.get("full_page"):
                call["full_page"] = True
        elif action == "snapshot":
            if kwargs.get("selector"):
                call["selector"] = kwargs["selector"]
        # close takes only context_id

        ok, result = await app.try_browse(action, **call)
        if not ok:
            err = str(result)
            if "provider" in err.lower():
                err += (
                    " — install with: pip install playwright && playwright install chromium"
                )
            return ToolResult(ok=False, content=f"error: {err}")

        return self._format(action, ctx, result if isinstance(result, dict) else {})

    def _format(self, action: str, ctx: str, result: dict) -> ToolResult:
        """Turn a provider verb result into a compact model-facing content block."""
        display = {"name": "Browse", "action": action, "context_id": ctx}

        if action == "navigate":
            url, title = result.get("url", ""), result.get("title", "")
            display.update(url=url, title=title)
            return ToolResult(ok=True, content=f"navigated → {url}\ntitle: {title}", display=display)

        if action in ("click", "fill", "wait_for"):
            verb = {"click": "clicked", "fill": "filled", "wait_for": "waited for"}[action]
            return ToolResult(ok=True, content=f"{verb} (ok)", display=display)

        if action == "screenshot":
            path = result.get("path", "")
            display["path"] = path
            return ToolResult(ok=True, content=f"screenshot: {path}", display=display)

        if action == "snapshot":
            text = result.get("text") or ""
            html = result.get("html") or ""
            url, title = result.get("url", ""), result.get("title", "")
            preview = text[:MAX_BODY_CHARS]
            if len(text) > MAX_BODY_CHARS:
                preview += f"\n... (truncated from {len(text)} chars)"
            display.update(url=url, title=title, text_chars=len(text), html_chars=len(html))
            content = (
                f"{url} — {title}\n"
                f"(text {len(text)} chars, html {len(html)} chars)\n\n"
                f"--- innerText ---\n{preview or '(empty)'}"
            )
            return ToolResult(ok=True, content=content, display=display)

        if action == "eval":
            value = result.get("value")
            try:
                rendered = json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                rendered = str(value)
            if len(rendered) > MAX_VALUE_CHARS:
                rendered = rendered[:MAX_VALUE_CHARS] + f"\n... (truncated from {len(rendered)} chars)"
            return ToolResult(ok=True, content=f"value: {rendered}", display=display)

        if action == "close":
            return ToolResult(ok=True, content=f"closed session '{ctx}'", display=display)

        # Shouldn't reach here — action was validated against _ACTIONS.
        return ToolResult(ok=True, content=json.dumps(result, default=str)[:MAX_VALUE_CHARS], display=display)
