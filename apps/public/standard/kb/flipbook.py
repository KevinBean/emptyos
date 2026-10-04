"""kb — flipbook interactive note builder (start/expand/save/list/detail).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: All `api_flipbook_*` endpoints — page state machine, symbol palette, asset serving, save & detail/list views.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._note_path / self.get_note (notes), self.kb_explain (BaseApp infra).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from emptyos.sdk import flipbook as fb
from emptyos.sdk import web_route
from typing import TYPE_CHECKING

from .shared import LEGACY_FLIPBOOK_DIRS, _note_has_visual, _slug_of

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   api_flipbook_start          = _flipbook.api_flipbook_start
#   api_flipbook_expand         = _flipbook.api_flipbook_expand
#   api_flipbook_symbols        = _flipbook.api_flipbook_symbols
#   api_flipbook_save_symbol    = _flipbook.api_flipbook_save_symbol
#   api_flipbook_symbol_get     = _flipbook.api_flipbook_symbol_get
#   api_flipbook_delete_symbol  = _flipbook.api_flipbook_delete_symbol
#   api_flipbook_page_get       = _flipbook.api_flipbook_page_get
#   api_flipbook_asset          = _flipbook.api_flipbook_asset
#   api_flipbook_save           = _flipbook.api_flipbook_save
#   api_flipbook_detail         = _flipbook.api_flipbook_detail
#   api_flipbook_list           = _flipbook.api_flipbook_list
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("POST", "/api/flipbook/start")
async def api_flipbook_start(self, request):
    body = await request.json()
    topic = (body.get("topic") or "").strip()
    force = bool(body.get("force"))
    mode = (body.get("mode") or "svg").strip()
    provider = (body.get("provider") or "local").strip()
    fast = bool(body.get("fast"))
    # Wave 3: optional list of kb slugs to use as the callout subjects.
    # When present, the SVG prompt enforces exactly these as callouts.
    callout_slugs = body.get("callout_slugs") or []
    if not isinstance(callout_slugs, list):
        callout_slugs = []
    # Wave 3.6: when the call originates from a kb-detail "Generate
    # visual" button, the frontend passes the existing kb slug so the
    # save lands on THAT note's path — not on a new file derived from
    # the (possibly different) topic string. Without this, a kb note
    # with slug `snr-social-perception` and title "Signal-to-Noise
    # Theory of Social Perception" would silently fork into a new
    # `signal-to-noise-theory-of-social-perception.md` note.
    target_slug = (body.get("slug") or "").strip() or None
    if not topic:
        return {"error": "topic required"}
    page = await self._generate_page(
        topic, parents=[], force=force, mode=mode,
        provider=provider, fast=fast,
        callout_slugs=callout_slugs,
        target_slug=target_slug,
    )
    await self.emit(
        "kb:flipbook_visited",
        {"topic": topic, "parents": [], "slug": target_slug or ""},
    )
    return page


@web_route("POST", "/api/flipbook/expand")
async def api_flipbook_expand(self, request):
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
    await self.emit("kb:flipbook_visited", {"topic": label, "parents": parents})
    return page


@web_route("GET", "/api/flipbook/symbols")
async def api_flipbook_symbols(self, request):
    """List available reusable symbols for the SVG generator."""
    return {"symbols": self._list_symbols()}


@web_route("POST", "/api/flipbook/symbols")
async def api_flipbook_save_symbol(self, request):
    """Save an SVG as a named symbol in the library.

    Accepts either `topic` (saves the page's current SVG as a symbol) or
    raw `svg` content. Plus `name` (sanitised) and optional `description`.
    """
    import re as _re
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
        svg = _re.sub(r"<defs>.*?</defs>", "", svg, count=1, flags=_re.DOTALL)
    description = (body.get("description") or "").strip()
    if description:
        m = _re.search(r"<svg\b[^>]*>", svg)
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


@web_route("GET", "/api/flipbook/symbols/{slug}")
async def api_flipbook_symbol_get(self, request):
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


@web_route("DELETE", "/api/flipbook/symbols/{slug}")
async def api_flipbook_delete_symbol(self, request):
    slug = self._symbol_slug(request.path_params.get("slug", ""))
    sd = self._symbols_dir()
    if sd is None:
        return {"error": "vault not configured"}
    target = sd / f"{slug}.svg"
    if not target.exists():
        return {"error": "not found"}
    target.unlink()
    return {"ok": True}


@web_route("GET", "/api/flipbook/page/{slug:path}")
async def api_flipbook_page_get(self, request):
    """Return a flipbook view for a kb note by slug.

    After Wave 3 the lookup walks kb's unified corpus first — any kb
    concept can be opened in flipbook mode, not just notes that were
    originally generated as flipbooks. Three response shapes:

    - Note exists + has visual frontmatter → full flipbook payload
      (title, svg, callouts, image_url, …). Hits the legacy
      ``_load_page`` path-shaped reader.
    - Note exists + no visual data → ``{slug, title, topic, body,
      needs_generation: true}`` so the frontend can render the
      "Generate visual" affordance pre-filled with the kb body as
      context.
    - Note doesn't exist → 404.

    ``slug`` may be hierarchical (``guitar/nut``); the last segment
    is the slug, earlier segments are the parent breadcrumb that
    ``_load_page`` uses for nested storage paths.
    """
    from starlette.responses import JSONResponse
    raw = (request.path_params.get("slug") or "").strip().strip("/")
    if not raw:
        return JSONResponse({"error": "slug required"}, status_code=400)
    segments = [s for s in raw.split("/") if s]
    topic = segments[-1]
    parents = segments[:-1]

    # 1. Direct kb lookup by slug — covers concepts that haven't been
    #    visualized yet, and the common case post-migration where the
    #    note already lives in kb/notes/ rather than the legacy
    #    folders the path-shaped reader walks.
    kb_match = next(
        (n for n in self._all_notes()
         if _slug_of(n.get("path", "")) == topic),
        None,
    )
    if kb_match:
        props = self.vault_get_properties(kb_match.get("path", "")) or {}
        if _note_has_visual(props):
            # Existing visual — fall through to the path-shaped reader
            # so the SVG asset embed + callouts get hydrated.
            page = await self._load_page(topic, parents)
            if page:
                page["_topic"] = topic
                if parents:
                    page["breadcrumb"] = parents + [page.get("title") or topic]
                return page
            # _load_page miss despite has_visual — fall through to
            # the no-visual shape rather than 404.
        body = self.vault_read_body(kb_match.get("path", ""))
        return {
            "slug": topic,
            "title": props.get("title") or topic.replace("-", " "),
            "topic": props.get("topic") or props.get("title") or topic.replace("-", " "),
            "body": body or "",
            "kind": props.get("kind", ""),
            "domain": props.get("domain", ""),
            "breadcrumb": (parents + [props.get("title") or topic]) if parents else [props.get("title") or topic],
            "_topic": topic,
            "needs_generation": True,
        }

    # 2. Fall back to the legacy path-shaped reader (covers any
    #    notes still at the explore-era or Wave-2 locations).
    page = await self._load_page(topic, parents)
    if not page:
        return JSONResponse({"error": "not found", "slug": raw}, status_code=404)
    page["_topic"] = topic
    if parents:
        page["breadcrumb"] = parents + [page.get("title") or topic]
    return page


@web_route("GET", "/api/flipbook/asset/{slug}.png")
async def api_flipbook_asset(self, request):
    """Serve a flipbook PNG asset directly from the vault.

    After Wave 3 the canonical location is ``kb/notes/_assets/``. For
    transitional convenience (notes the user hasn't migrated yet)
    this also checks the legacy ``kb/flipbook/_assets/`` and the
    explore-era ``30_Resources/Explore/_assets/`` paths.
    """
    from starlette.responses import FileResponse, JSONResponse
    slug = request.path_params.get("slug", "")
    candidates: list[Path] = []
    root = self._flipbook_root()
    if root:
        candidates.append(root / "_assets" / f"{slug}.png")
    vault = self.kernel.config.notes_path
    if vault:
        for legacy in LEGACY_FLIPBOOK_DIRS:
            candidates.append(vault / legacy / "_assets" / f"{slug}.png")
    for c in candidates:
        if c.exists():
            return FileResponse(str(c), media_type="image/png")
    return JSONResponse({"error": "not found"}, status_code=404)


@web_route("POST", "/api/flipbook/save")
async def api_flipbook_save(self, request):
    body = await request.json()
    page = body.get("page") or {}
    verify = bool(body.get("verify"))
    if not page.get("title"):
        return {"error": "page.title required"}
    topic = page.get("_topic") or (page.get("breadcrumb") or [page["title"]])[-1]
    # Wave 3.6: honor the kb-anchored slug stamped by api_flipbook_start
    # when the page originated from a "Generate visual" click. Without
    # this the verify/refine save would fork into a topic-derived path.
    target_slug = (body.get("slug") or page.get("_target_slug") or "").strip() or None
    # The resolver may have pinned a non-default-folder path on first
    # generate; round-trip it so verify/refine land on the same file.
    target_path = (page.get("_target_path") or "").strip() or None
    await self._save_page(
        page, topic=topic, verified=verify,
        target_slug=target_slug, target_path=target_path,
    )
    if target_path:
        saved_path = target_path
    elif target_slug:
        saved_path = f"{self._notes_dir()}/{target_slug}.md"
    else:
        saved_path = self._path_for(topic)
    return {"ok": True, "verified": verify, "path": saved_path}


@web_route("POST", "/api/flipbook/detail")
async def api_flipbook_detail(self, request):
    """Generate (or read from cache) a popover-sized detail card for a
    callout. Cached on the callout itself so re-peeking is free."""
    body = await request.json()
    label = (body.get("label") or "").strip()
    page_topic = (body.get("page_topic") or "").strip()
    page_title = (body.get("page_title") or "").strip()
    idx = body.get("idx")
    force = bool(body.get("force"))
    if not label:
        return {"error": "label required"}
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
    async def _think_fn(system: str, user: str) -> str:
        raw = await self.think(
            user, system=system, domain="reason", temperature=0.4,
        )
        return raw if isinstance(raw, str) else str(raw)

    peek = await fb.generate_peek(
        label, parent=(page_title or page_topic), think_fn=_think_fn,
    )
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


@web_route("GET", "/api/flipbook/list")
async def api_flipbook_list(self, request):
    """List kb concept notes for the flipbook saved-list.

    After Wave 3, this returns the unified kb corpus (filtered to
    kind=concept by default) — not just notes that carry the legacy
    ``flipbook`` tag. Each row carries ``has_visual`` so the UI can
    group "with visuals" above "without visuals (click to generate)".

    Query params:
        kind  — restrict to one kb kind. Default ``concept``; pass
                ``kind=`` (empty) to include every kb kind.
        with_visuals_only  — ``1`` for the legacy list shape (drop
                             rows where has_visual is false). Default off.
    """
    kind_filter = request.query_params.get("kind")
    if kind_filter is None:
        kind_filter = "concept"
    only_with_visuals = (request.query_params.get("with_visuals_only") or "").strip() == "1"
    notes = self._all_notes()
    items = []
    for n in notes:
        props = n.get("properties", {}) or {}
        note_kind = props.get("kind", "")
        if kind_filter and note_kind and note_kind != kind_filter:
            continue
        # Legacy notes without a kind set still surface under the
        # default concept filter — they're concepts in spirit; the
        # user can refine later via the typed kb endpoints.
        if kind_filter and not note_kind and kind_filter != "concept":
            continue
        has_visual = _note_has_visual(props)
        if only_with_visuals and not has_visual:
            continue
        slug_val = _slug_of(n.get("path", ""))
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
            "has_visual": has_visual,
            "kind": note_kind,
            "domain": props.get("domain", ""),
        })
    # has_visual=True first, newest-updated within each bucket.
    # Two stable sorts: secondary (updated desc) then primary
    # (has_visual desc) so the secondary order is preserved within
    # each primary group. Cheaper to read than a composite key here
    # since `updated` is an ISO date string and we want descending.
    items.sort(key=lambda r: r["updated"] or "", reverse=True)
    items.sort(key=lambda r: not r["has_visual"])
    return {"items": items}
