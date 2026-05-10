"""Explore — visual exploration browser.

Click on a topic, get a Flipbook-style page (illustration + labeled callouts).
Click on any callout, dive deeper. Pages persist as vault notes so the
exploration tree is a knowledge map, not an evaporating session.

Lifecycle: AI generates a **draft** → user reviews/edits in-place → user
saves as **verified**. Drafts are cached so re-clicking a topic doesn't burn
another LLM call. Verified notes are the trustworthy layer.
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.responses import FileResponse

from emptyos.sdk import (
    BaseApp,
    parse_llm_json,
    web_route,
)

from ._helpers import (
    DEFAULT_FOLDER,
    PEEK_PROMPT_TEMPLATE,
    PEEK_SYSTEM_PROMPT,
)
from .generation import GenerationMixin
from .vault_io import VaultIOMixin


class ExploreApp(GenerationMixin, VaultIOMixin, BaseApp):
    async def setup(self):
        await super().setup()
        self._seed_demo_symbols()

    # ── Routes ──────────────────────────────────────────────

    @web_route("POST", "/api/start")
    async def api_start(self, request):
        body = await request.json()
        topic = (body.get("topic") or "").strip()
        force = bool(body.get("force"))
        mode = (body.get("mode") or "svg").strip()
        provider = (body.get("provider") or "local").strip()
        fast = bool(body.get("fast"))
        if not topic:
            return {"error": "topic required"}
        page = await self._generate_page(
            topic, parents=[], force=force, mode=mode,
            provider=provider, fast=fast,
        )
        await self.emit("explore:visited", {"topic": topic, "parents": []})
        return page

    @web_route("POST", "/api/expand")
    async def api_expand(self, request):
        body = await request.json()
        label = (body.get("label") or "").strip()
        parents = body.get("parents") or []
        force = bool(body.get("force"))
        mode = (body.get("mode") or "svg").strip()
        provider = (body.get("provider") or "local").strip()
        fast = bool(body.get("fast"))
        if not label:
            return {"error": "label required"}
        page = await self._generate_page(
            label, parents=parents, force=force, mode=mode,
            provider=provider, fast=fast,
        )
        await self.emit("explore:visited", {"topic": label, "parents": parents})
        return page

    @web_route("GET", "/api/symbols")
    async def api_symbols(self, request):
        """List available reusable symbols."""
        return {"symbols": self._list_symbols()}

    @web_route("POST", "/api/symbols")
    async def api_save_symbol(self, request):
        """Save an SVG as a named symbol in the library.

        Accepts either:
        - `topic` (saves the page's current SVG as a symbol), OR
        - `svg` (raw SVG content to save directly).
        Plus `name` (the symbol id; sanitised) and optional `description`.
        """
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return {"error": "name required"}
        slug = self._symbol_slug(name)

        svg = (body.get("svg") or "").strip()
        if not svg:
            topic = (body.get("topic") or "").strip()
            if not topic:
                return {"error": "either svg or topic required"}
            page = await self._load_page(topic)
            if not page:
                return {"error": "topic not found"}
            svg = (page.get("svg") or "").strip()
            if not svg:
                return {"error": "page has no svg"}
            # Strip any previously injected symbol defs so we don't nest libraries
            svg = re.sub(r"<defs>.*?</defs>", "", svg, count=1, flags=re.DOTALL)

        description = (body.get("description") or "").strip()
        if description:
            # Embed description as a <desc> just inside the <svg>
            m = re.search(r"<svg\b[^>]*>", svg)
            if m and "<desc>" not in svg[:m.end() + 200]:
                svg = (svg[:m.end()]
                       + f"<desc>{description}</desc>"
                       + svg[m.end():])

        sd = self._symbols_dir()
        if sd is None:
            return {"error": "vault not configured"}
        sd.mkdir(parents=True, exist_ok=True)
        target = sd / f"{slug}.svg"
        target.write_text(svg, encoding="utf-8")
        return {"ok": True, "id": slug, "path": str(target)}

    @web_route("GET", "/api/page/{slug:path}")
    async def api_page_get(self, request):
        """Return a saved exploration page by slug.

        `slug` may be hierarchical (e.g. `guitar/nut`) — the last segment
        is the topic, earlier segments are its parent breadcrumb. Returns
        404 if no matching note exists.
        """
        from starlette.responses import JSONResponse
        raw = (request.path_params.get("slug") or "").strip().strip("/")
        if not raw:
            return JSONResponse({"error": "slug required"}, status_code=400)
        segments = [s for s in raw.split("/") if s]
        topic = segments[-1]
        parents = segments[:-1]
        page = await self._load_page(topic, parents)
        if not page:
            return JSONResponse({"error": "not found", "slug": raw}, status_code=404)
        page["_topic"] = topic
        if parents:
            page["breadcrumb"] = parents + [page.get("title") or topic]
        return page

    @web_route("GET", "/api/symbols/{slug}")
    async def api_symbol_get(self, request):
        """Return the raw SVG content for a saved symbol (preview)."""
        from starlette.responses import JSONResponse, Response
        slug = self._symbol_slug(request.path_params.get("slug", ""))
        sd = self._symbols_dir()
        if sd is None:
            return JSONResponse({"error": "vault not configured"}, status_code=404)
        path = sd / f"{slug}.svg"
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            svg = path.read_text(encoding="utf-8")
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return Response(content=svg, media_type="image/svg+xml")

    @web_route("DELETE", "/api/symbols/{slug}")
    async def api_delete_symbol(self, request):
        slug = self._symbol_slug(request.path_params.get("slug", ""))
        sd = self._symbols_dir()
        if sd is None:
            return {"error": "vault not configured"}
        target = sd / f"{slug}.svg"
        if not target.exists():
            return {"error": "not found"}
        target.unlink()
        return {"ok": True}

    @web_route("GET", "/api/asset/{slug}.png")
    async def api_asset(self, request):
        """Serve an explore-app PNG asset directly from the vault."""
        slug = request.path_params.get("slug", "")
        folder = self.vault_config_path("explore_dir", DEFAULT_FOLDER)
        if not folder:
            return {"error": "no vault"}
        path = folder / "_assets" / f"{slug}.png"
        if not path.exists():
            return {"error": "not found"}
        return FileResponse(str(path), media_type="image/png")

    @web_route("POST", "/api/save")
    async def api_save(self, request):
        body = await request.json()
        page = body.get("page") or {}
        verify = bool(body.get("verify"))
        if not page.get("title"):
            return {"error": "page.title required"}
        # Cache key is the topic-as-typed (last breadcrumb entry at gen-time),
        # not the editable title — otherwise re-typing the topic misses cache.
        topic = page.get("_topic") or (page.get("breadcrumb") or [page["title"]])[-1]
        await self._save_page(page, topic=topic, verified=verify)
        return {"ok": True, "verified": verify, "path": self._path_for(topic)}

    @web_route("POST", "/api/detail")
    async def api_detail(self, request):
        """Generate (or read from cache) a popover-sized detail card for a
        callout. Cached on the callout itself in the parent vault note so
        re-peeking is free. Cheaper + faster than a full page generation."""
        body = await request.json()
        label = (body.get("label") or "").strip()
        page_topic = (body.get("page_topic") or "").strip()
        page_title = (body.get("page_title") or "").strip()
        idx = body.get("idx")
        force = bool(body.get("force"))
        if not label:
            return {"error": "label required"}

        # Cache lookup: load parent page, return existing peek if present
        parent = None
        if page_topic and not force:
            parent = await self._load_page(page_topic)
            if parent and isinstance(idx, int):
                callouts = parent.get("callouts") or []
                if 0 <= idx < len(callouts):
                    cached = (callouts[idx] or {}).get("peek")
                    if cached and cached.get("summary"):
                        return {
                            "label": label,
                            "summary": cached.get("summary", ""),
                            "facts": cached.get("facts", []),
                            "from_cache": True,
                        }

        prompt = PEEK_PROMPT_TEMPLATE.format(
            label=label, parent=page_title or page_topic,
        )
        raw = await self.think(
            prompt, system=PEEK_SYSTEM_PROMPT, domain="reason", temperature=0.4,
        )
        data = parse_llm_json(raw) or {}
        peek = {
            "summary": data.get("summary") or "",
            "facts": data.get("facts") or [],
        }

        # Persist back onto the parent note's callout if we know which one
        if page_topic and isinstance(idx, int):
            try:
                if parent is None:
                    parent = await self._load_page(page_topic)
                if parent:
                    callouts = parent.get("callouts") or []
                    if 0 <= idx < len(callouts):
                        callouts[idx]["peek"] = peek
                        parent["callouts"] = callouts
                        await self._save_page(
                            parent, topic=page_topic,
                            verified=parent.get("verified", False),
                        )
            except Exception:
                pass

        return {
            "label": label,
            "summary": peek["summary"],
            "facts": peek["facts"],
            "from_cache": False,
        }

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        """List saved exploration notes for browsing on the start screen.
        Uses VaultIndex (tag-based query) so notes the user moves around in
        the vault still show up — the index is the source of truth, not the
        folder path."""
        notes = self.vault_query(tags=["explore"]) or []
        items = []
        for n in notes:
            props = n.get("properties", {}) or {}
            slug_val = Path(n.get("path", "")).stem
            title = self._unescape_legacy(props.get("title") or slug_val)
            topic = self._unescape_legacy(
                props.get("topic") or props.get("title") or slug_val
            )
            items.append({
                "slug": slug_val,
                "title": title,
                "topic": topic,
                "mode": props.get("mode", "svg"),
                "verified": str(props.get("verified", "")).lower() == "true",
                "updated": props.get("updated", ""),
            })
        return {"items": items}
