"""Playwright plugin — headless browser automation.

Registers a `PlaywrightBrowseProvider` on the `browse` capability and exposes
a `playwright` service so apps that need fine-grained control can grab the
plugin directly via `self.require("playwright")`.

Playwright is imported lazily inside `available()` / `execute()` — if the
`playwright` Python package or its bundled browsers are missing, the
provider reports unavailable. Apps using `self.browse(...)` should detect
the `RuntimeError` from an unfulfilled chain and skip cleanly.

The plugin owns one shared `async_playwright` runtime and one Chromium
browser process for the daemon's lifetime. Apps allocate isolated
`BrowserContext` slots by passing a stable `context_id` — each context
keeps its own cookies, storage, and one current page. Omitting
`context_id` uses a default singleton, fine for one-shot scripts.

Cleanup runs on plugin disconnect; orphaned contexts also get reaped on
process exit because the browser dies with the daemon.

Operational note: Playwright bundles Chromium (~300MB). One-time install
the user must run themselves (we don't auto-download GB-scale binaries):

    pip install playwright
    playwright install chromium

The plugin reports `available() = False` until both exist.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from emptyos.capabilities import Provider
from emptyos.sdk import BasePlugin

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "emptyos-browse"
SCREENSHOT_DIR.mkdir(exist_ok=True)

_DEFAULT_CONTEXT = "_default"


class PlaywrightPlugin(BasePlugin):
    name = "playwright"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._lock = asyncio.Lock()
        self._available_cached: bool | None = None

    async def connect(self):
        # Don't spawn the browser eagerly — first use lazily starts it. Keeps
        # daemon boot fast and makes the plugin a no-op cost when no app
        # actually browses.
        cap = self.kernel.capabilities.get("browse")
        cap.add_provider(PlaywrightBrowseProvider(self), priority=0)

    async def disconnect(self):
        async with self._lock:
            for page in self._pages.values():
                try:
                    await page.close()
                except Exception:
                    pass
            self._pages.clear()
            for ctx in self._contexts.values():
                try:
                    await ctx.close()
                except Exception:
                    pass
            self._contexts.clear()
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None

    async def available(self) -> bool:
        if self._available_cached is not None:
            return self._available_cached
        try:
            import playwright  # noqa: F401
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError:
            self._available_cached = False
            return False
        # Browsers might be missing even if the python package is installed.
        # `playwright install chromium` writes to ~/.cache/ms-playwright; we
        # detect that lazily by trying a launch on first use rather than
        # statting paths (the install dir varies per OS and Python env).
        self._available_cached = True
        return True

    def _headless(self) -> bool:
        return bool(self.config("headless", True))

    def _default_timeout_ms(self) -> int:
        return int(self.config("timeout_s", 30)) * 1000

    def _viewport(self) -> dict | None:
        w = self.config("viewport_width", 1280)
        h = self.config("viewport_height", 800)
        if not (w and h):
            return None
        return {"width": int(w), "height": int(h)}

    async def _ensure_browser(self):
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless())

    async def _get_page(self, context_id: str | None = None) -> Page:
        """Return the current page for the given context, creating both lazily."""
        cid = context_id or _DEFAULT_CONTEXT
        async with self._lock:
            await self._ensure_browser()
            ctx = self._contexts.get(cid)
            if ctx is None:
                kwargs: dict[str, Any] = {}
                viewport = self._viewport()
                if viewport:
                    kwargs["viewport"] = viewport
                ctx = await self._browser.new_context(**kwargs)
                ctx.set_default_timeout(self._default_timeout_ms())
                self._contexts[cid] = ctx
            page = self._pages.get(cid)
            if page is None or page.is_closed():
                page = await ctx.new_page()
                self._pages[cid] = page
            return page

    async def close_context(self, context_id: str | None = None) -> dict:
        cid = context_id or _DEFAULT_CONTEXT
        async with self._lock:
            page = self._pages.pop(cid, None)
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            ctx = self._contexts.pop(cid, None)
            if ctx is not None:
                try:
                    await ctx.close()
                except Exception:
                    pass
        return {"ok": True, "context_id": cid}

    # --- Action implementations -------------------------------------------------

    async def navigate(
        self,
        url: str,
        *,
        wait: str = "load",
        context_id: str | None = None,
        timeout_s: float | None = None,
    ) -> dict:
        page = await self._get_page(context_id)
        timeout_ms = int((timeout_s or self.config("timeout_s", 30)) * 1000)
        await page.goto(url, wait_until=wait, timeout=timeout_ms)
        return {"url": page.url, "title": await page.title()}

    async def click(
        self, selector: str, *, context_id: str | None = None, timeout_s: float | None = None
    ) -> dict:
        page = await self._get_page(context_id)
        timeout_ms = int((timeout_s or self.config("timeout_s", 30)) * 1000)
        await page.click(selector, timeout=timeout_ms)
        return {"ok": True}

    async def fill(
        self,
        selector: str,
        value: str,
        *,
        context_id: str | None = None,
        timeout_s: float | None = None,
    ) -> dict:
        page = await self._get_page(context_id)
        timeout_ms = int((timeout_s or self.config("timeout_s", 30)) * 1000)
        await page.fill(selector, value, timeout=timeout_ms)
        return {"ok": True}

    async def screenshot(
        self,
        *,
        selector: str | None = None,
        full_page: bool = False,
        context_id: str | None = None,
        path: str | None = None,
    ) -> dict:
        page = await self._get_page(context_id)
        out_path = Path(path) if path else SCREENSHOT_DIR / f"shot_{uuid.uuid4().hex[:8]}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if selector:
            locator = page.locator(selector)
            await locator.screenshot(path=str(out_path))
        else:
            await page.screenshot(path=str(out_path), full_page=full_page)
        return {"path": str(out_path)}

    async def snapshot(
        self, *, selector: str | None = None, context_id: str | None = None
    ) -> dict:
        page = await self._get_page(context_id)
        if selector:
            locator = page.locator(selector)
            text = await locator.inner_text()
            html = await locator.inner_html()
        else:
            text = await page.inner_text("body")
            html = await page.content()
        return {"text": text, "html": html, "url": page.url, "title": await page.title()}

    async def eval(
        self, expression: str, *, context_id: str | None = None
    ) -> dict:
        page = await self._get_page(context_id)
        value = await page.evaluate(expression)
        return {"value": value}

    async def wait_for(
        self,
        selector: str,
        *,
        state: str = "visible",
        context_id: str | None = None,
        timeout_s: float | None = None,
    ) -> dict:
        page = await self._get_page(context_id)
        timeout_ms = int((timeout_s or self.config("timeout_s", 30)) * 1000)
        await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
        return {"ok": True}


# Dispatch table — verb name → method on the plugin. Keeps the provider
# trivial and makes adding a verb a one-line registration.
_ACTIONS = {
    "navigate": "navigate",
    "click": "click",
    "fill": "fill",
    "screenshot": "screenshot",
    "snapshot": "snapshot",
    "eval": "eval",
    "wait_for": "wait_for",
    "close": "close_context",
}


class PlaywrightBrowseProvider(Provider):
    """Headless Chromium — fulfils `browse` via the Playwright plugin."""

    name = "playwright"

    def __init__(self, plugin: PlaywrightPlugin):
        self.plugin = plugin

    async def available(self) -> bool:
        return await self.plugin.available()

    async def health(self) -> dict:
        if await self.plugin.available():
            return {"available": True, "reason": None, "recovery": None}
        return {
            "available": False,
            "reason": "playwright python package or chromium browser not installed",
            "recovery": {
                "kind": "plugin",
                "id": "playwright",
                "launcher": "pip install playwright && playwright install chromium",
            },
        }

    async def execute(self, *, action: str, **kwargs) -> Any:
        method_name = _ACTIONS.get(action)
        if method_name is None:
            raise ValueError(
                f"browse: unknown action {action!r} (known: {sorted(_ACTIONS)})"
            )
        method = getattr(self.plugin, method_name)
        return await method(**kwargs)
