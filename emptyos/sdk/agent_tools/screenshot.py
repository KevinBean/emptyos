"""Screenshot tool — render a URL in a real browser and report what came back.

Fetch gives us HTML bytes; it can't tell us whether the page actually renders,
whether JS threw, or whether CSS broke the layout. For UI work (debugging
blank pages, validating newly-written apps, checking that `pages/index.html`
auto-mount actually served the right markup) the agent needs to see it like
a user would.

Returns:
  • PNG screenshot path (under `data/state/agent_screenshots/`)
  • `document.body.innerText` so the model can read rendered text directly
  • Console errors + page errors (JS exceptions, failed resource loads)
  • HTTP status + final URL (after redirects)

Auto-approves for localhost / loopback / private-IP hosts.
"""

from __future__ import annotations

import ipaddress
import time
from urllib.parse import urlparse

from emptyos.sdk.agent_tools.base import Tool, ToolResult, repo_root

DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_SETTLE_MS = 500
MAX_BODY_CHARS = 5_000
MAX_ERROR_LINES = 30


def _is_local_host(host: str) -> bool:
    if not host:
        return False
    h = host.lower().strip("[]")
    if h in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


class ScreenshotTool(Tool):
    name = "Screenshot"
    description = (
        "Load a URL in a headless browser and return what rendered: PNG path, "
        "body.innerText, and any console/page errors. Use this for UI verification — "
        "Fetch gives you raw HTML, but only Screenshot tells you whether the page "
        "actually rendered and whether JS broke. Auto-approves for localhost/loopback; "
        "other URLs ask permission. Screenshots are written under `data/state/agent_screenshots/`."
    )
    permission = "ask"
    readonly = True  # plan-mode safe — just renders, doesn't mutate
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to load."},
            "wait_ms": {
                "type": "integer",
                "description": f"Additional settle time after networkidle (default {DEFAULT_SETTLE_MS}). "
                f"Bump this if the page does deferred work after load.",
            },
            "viewport": {
                "type": "string",
                "description": "Viewport size as 'WxH' (default '1280x800'). Useful for mobile simulation.",
            },
        },
        "required": ["url"],
    }

    def permission_for(self, input: dict) -> str:
        url = input.get("url", "")
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            return "ask"
        return "auto" if _is_local_host(host) else "ask"

    def permission_summary(self, input: dict) -> str:
        return f"Screenshot: {input.get('url', '')}"

    def _parse_viewport(self, raw: str | None) -> dict:
        default = {"width": 1280, "height": 800}
        if not raw:
            return default
        try:
            w, h = raw.lower().split("x")
            return {"width": int(w), "height": int(h)}
        except Exception:
            return default

    async def run(self, app, **kwargs) -> ToolResult:
        url = (kwargs.get("url") or "").strip()
        if not url:
            return ToolResult(ok=False, content="error: url is required")
        if urlparse(url).scheme not in ("http", "https"):
            return ToolResult(
                ok=False,
                content=f"error: unsupported scheme (use http:// or https://). Got: {url!r}",
            )

        try:
            wait_ms = int(kwargs.get("wait_ms") or DEFAULT_SETTLE_MS)
        except (TypeError, ValueError):
            wait_ms = DEFAULT_SETTLE_MS
        viewport = self._parse_viewport(kwargs.get("viewport"))

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return ToolResult(
                ok=False,
                content=(
                    "error: playwright not installed. Install with:\n"
                    "  pip install playwright && playwright install chromium"
                ),
            )

        console_errors: list[str] = []
        page_errors: list[str] = []
        shots_dir = repo_root(app) / "data" / "state" / "agent_screenshots"
        try:
            shots_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return ToolResult(ok=False, content=f"error: could not create screenshot dir: {e}")
        path = shots_dir / f"{int(time.time() * 1000)}.png"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(viewport=viewport)
                    page = await context.new_page()

                    # Collect JS errors + console errors/warnings. Bind as lambdas
                    # on the page events — playwright fires these async.
                    page.on("pageerror", lambda e: page_errors.append(str(e)))

                    def _console(msg):
                        if msg.type in ("error", "warning"):
                            console_errors.append(f"[{msg.type}] {msg.text}")

                    page.on("console", _console)

                    response = await page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=DEFAULT_TIMEOUT_MS,
                    )
                    if wait_ms > 0:
                        await page.wait_for_timeout(wait_ms)

                    status = response.status if response else 0
                    final_url = page.url
                    body_text = await page.evaluate(
                        "() => document.body ? document.body.innerText : ''"
                    )
                    await page.screenshot(path=str(path), full_page=False)
                finally:
                    await browser.close()
        except Exception as e:
            # Clean up partial screenshot if it got created
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            return ToolResult(ok=False, content=f"error: {type(e).__name__}: {e}")

        # Build content block for the model
        body_preview = body_text[:MAX_BODY_CHARS]
        if len(body_text) > MAX_BODY_CHARS:
            body_preview += f"\n... (truncated from {len(body_text)} chars)"
        err_lines = (page_errors + console_errors)[:MAX_ERROR_LINES]
        err_block = "\n".join(err_lines) if err_lines else "(none)"

        summary = f"{url} → {status}"
        if final_url != url:
            summary += f"  (redirected to {final_url})"

        content = (
            f"{summary}\n"
            f"screenshot: {path}\n"
            f"page errors: {len(page_errors)}  ·  console errors/warnings: {len(console_errors)}\n"
            f"{err_block}\n\n"
            f"--- body.innerText ---\n{body_preview or '(empty body)'}"
        )

        ok = (
            200 <= status < 400 and not page_errors  # any JS exception is a red flag
        )
        return ToolResult(
            ok=ok,
            content=content,
            display={
                "name": "Screenshot",
                "url": url,
                "final_url": final_url,
                "status": status,
                "path": str(path),
                "page_errors": len(page_errors),
                "console_errors": len(console_errors),
                "body_chars": len(body_text),
            },
        )
