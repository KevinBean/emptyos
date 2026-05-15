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
    extract_wikilinks,
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

    @web_route("GET", "/api/graph")
    async def api_graph(self, request):
        """Return a vault-wide knowledge graph: notes as nodes, wikilinks as edges.

        Query params:
            kinds — comma-separated tags to restrict node pool (default: all)
            limit — node cap (default 400, hard cap 1500) — keeps the
                    frontend responsive on huge vaults
            shared_tags — "1" to add dashed `tag-shared` edges between notes
                          sharing ≥1 non-trivial tag (default off; can explode)
        """
        kinds_raw = (request.query_params.get("kinds") or "").strip()
        kinds = [k.strip() for k in kinds_raw.split(",") if k.strip()] if kinds_raw else []
        try:
            limit = min(1500, max(10, int(request.query_params.get("limit") or 400)))
        except ValueError:
            limit = 400
        include_tag_edges = (request.query_params.get("shared_tags") or "").strip() == "1"

        vault_index = self.kernel.services.get("vault_index")
        if not vault_index:
            return {"nodes": [], "edges": [], "error": "vault_index unavailable"}

        if kinds:
            pool: dict[str, dict] = {}
            for k in kinds:
                for e in vault_index.find(tags=[k]):
                    pool[e["path"]] = e
            entries = list(pool.values())
        else:
            entries = list(vault_index._files.values())

        # Sort by recency, cap to limit
        entries.sort(key=lambda e: e.get("modified", 0), reverse=True)
        entries = entries[:limit]

        name_to_path: dict[str, str] = {}
        for e in entries:
            n = (e.get("name") or "").lower()
            if n and n not in name_to_path:
                name_to_path[n] = e["path"]

        nodes = []
        for e in entries:
            tags = e.get("tags") or []
            primary_kind = next((t for t in tags if "/" not in t), tags[0] if tags else "note")
            nodes.append({
                "id": e["path"],
                "label": (e.get("name") or "").replace("-", " "),
                "kind": primary_kind,
                "folder": e.get("folder", ""),
                "modified": e.get("modified", 0),
            })

        # Wikilink edges — walk each note's body once, resolve targets
        # against the in-pool name index (skip unresolved links to avoid
        # phantom nodes that aren't part of the requested slice).
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()
        notes_dir = self.vault_root
        for e in entries:
            if not notes_dir:
                break
            try:
                content = await self.read(str(notes_dir / e["path"]))
            except Exception:
                continue
            for target in extract_wikilinks(content):
                key = target.lower()
                target_path = name_to_path.get(key)
                if not target_path or target_path == e["path"]:
                    continue
                edge_key = (e["path"], target_path, "wikilink")
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append({"from": e["path"], "to": target_path, "kind": "wikilink"})

        # Optional: dashed shared-tag edges. Skip the rapidly-shared "kb",
        # "note", folder-y tags that would saturate the graph.
        if include_tag_edges:
            from collections import defaultdict
            ignore = {"note", "kb", "guideline", "draft", "verified", "tag", "explore"}
            tag_to_paths: dict[str, list[str]] = defaultdict(list)
            for e in entries:
                for t in e.get("tags") or []:
                    if t in ignore or "/" in t:
                        continue
                    tag_to_paths[t].append(e["path"])
            for t, paths in tag_to_paths.items():
                if len(paths) > 8 or len(paths) < 2:
                    continue  # too generic or trivial
                for i in range(len(paths)):
                    for j in range(i + 1, len(paths)):
                        a, b = paths[i], paths[j]
                        edge_key = (a, b, "tag")
                        rev_key = (b, a, "tag")
                        if edge_key in seen_edges or rev_key in seen_edges:
                            continue
                        seen_edges.add(edge_key)
                        edges.append({"from": a, "to": b, "kind": "tag", "tag": t})

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_indexed": vault_index.file_count(),
                "in_slice": len(nodes),
                "wikilink_edges": sum(1 for e in edges if e.get("kind") == "wikilink"),
                "tag_edges": sum(1 for e in edges if e.get("kind") == "tag"),
            },
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
