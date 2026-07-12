"""kb — KB graph + backlinks + implementation lookup endpoints.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: `api_graph` (big graph builder), `api_note_by_path`, `api_implementations`, and `_kb_backlinks` helper.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._all_notes / self.get_note (notes), self._kind_index (indexes).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
from emptyos.sdk import extract_wikilinks, web_route
from typing import TYPE_CHECKING

from .shared import _note_has_visual, _related_targets, _slug_of

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401 — for type hints only


# ─── Bind to KBApp class as ────────────────────────────────
#   api_graph            = _graph.api_graph
#   api_note_by_path     = _graph.api_note_by_path
#   api_implementations  = _graph.api_implementations
#   _build_backlink_map  = _graph._build_backlink_map
#   _kb_backlinks        = _graph._kb_backlinks
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/graph")
async def api_graph(self, request):
    """Vault-wide knowledge graph: notes as nodes, wikilinks as edges.

    Powers the network-mode view in /kb/. Ported from the explore app's
    /api/graph so the same vis-network frontend works without changes.

    Query params:
        kinds — comma-separated tags to restrict node pool (default: "kb")
        limit — node cap (default 400, hard cap 1500)
        shared_tags — "1" to add dashed tag-shared edges (default off)
        center — optional vault path to highlight as the focused node

    Defaults to the kb-tagged subset (the natural view in the kb app);
    pass kinds= (empty) for the whole vault, kinds=note,kb,explore etc
    for a custom slice.
    """
    kinds_raw = request.query_params.get("kinds")
    # Distinguish "not provided" (None) from "explicitly empty" (= whole
    # vault). Default to kb-only when the param is missing entirely.
    if kinds_raw is None:
        kinds = ["kb"]
    else:
        kinds = [k.strip() for k in kinds_raw.split(",") if k.strip()]

    try:
        limit = min(1500, max(10, int(request.query_params.get("limit") or 400)))
    except ValueError:
        limit = 400
    include_tag_edges = (request.query_params.get("shared_tags") or "").strip() == "1"
    include_semantic = (request.query_params.get("semantic") or "").strip() == "1"
    center = (request.query_params.get("center") or "").strip()

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
    pool_n = len(entries)  # pre-cap size — the UI shows "250 of 1359" when capped
    entries = entries[:limit]

    name_to_path: dict[str, str] = {}
    for e in entries:
        n = (e.get("name") or "").lower()
        if n and n not in name_to_path:
            name_to_path[n] = e["path"]

    # For kb-tagged notes, surface the typed `kind` (concept/formula/...)
    # as the node's discriminator so colors match the rest of the kb UI.
    # Pull from cached vault-index entry to avoid re-reading files.
    nodes = []
    for e in entries:
        tags = e.get("tags") or []
        props = e.get("properties") or {}
        kb_kind = props.get("kind") if "kb" in tags else None
        primary = kb_kind or next(
            (t for t in tags if "/" not in t),
            tags[0] if tags else "note",
        )
        nodes.append({
            "id": e["path"],
            "label": (e.get("name") or "").replace("-", " "),
            "kind": primary,
            "domain": props.get("domain", "") if "kb" in tags else "",
            "folder": e.get("folder", ""),
            "modified": e.get("modified", 0),
            "is_center": e["path"] == center,
            "has_visual": _note_has_visual(props),
        })

    # Wikilink edges
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

    # Optional shared-tag edges
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
                continue
            for i in range(len(paths)):
                for j in range(i + 1, len(paths)):
                    a, b = paths[i], paths[j]
                    edge_key = (a, b, "tag")
                    rev_key = (b, a, "tag")
                    if edge_key in seen_edges or rev_key in seen_edges:
                        continue
                    seen_edges.add(edge_key)
                    edges.append({"from": a, "to": b, "kind": "tag", "tag": t})

    # Optional semantic edges — surface the KB's existing typed frontmatter
    # relationships (verified_against / references / superseded_by / related)
    # as distinct edge kinds so the reference→clause→formula→case ladder is
    # visible in the graph, not collapsed into wikilinks. Off by default →
    # response is byte-identical without `semantic=1`. Edges only connect
    # nodes already in the slice; deduped via the shared seen_edges set.
    if include_semantic:
        slug_to_path = {_slug_of(e["path"]): e["path"] for e in entries}

        def _add_semantic(src_path: str, dst_slug: str, kind: str) -> None:
            dst_path = slug_to_path.get(dst_slug)
            if not dst_path or dst_path == src_path:
                return
            edge_key = (src_path, dst_path, kind)
            if edge_key in seen_edges:
                return
            seen_edges.add(edge_key)
            edges.append({"from": src_path, "to": dst_path, "kind": kind})

        for e in entries:
            props = e.get("properties") or {}
            # formula --verified_against--> case  (Legal → Procedure)
            if props.get("kind") == "formula":
                for v in props.get("verified_against") or []:
                    s = str(v).strip().strip("[]").strip()
                    if s:
                        _add_semantic(e["path"], s, "verified_against")
            # note --related--> note  (generic, typed apart from wikilink)
            for s in _related_targets(props):
                _add_semantic(e["path"], s, "related")

        # citer --cites--> reference/clause, from the precomputed index.
        for ref_slug, citers in (getattr(self, "_inbound_citations", None) or {}).items():
            for c in citers:
                src_path = slug_to_path.get(c)
                if src_path:
                    _add_semantic(src_path, ref_slug, "cites")

        # older --superseded_by--> newer, from the precomputed forward map.
        for old_slug, new_slug in (getattr(self, "_supersession_forward", None) or {}).items():
            src_path = slug_to_path.get(old_slug)
            if src_path:
                _add_semantic(src_path, new_slug, "superseded_by")

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_indexed": vault_index.file_count(),
            "pool": pool_n,
            "in_slice": len(nodes),
            "wikilink_edges": sum(1 for e in edges if e.get("kind") == "wikilink"),
            "tag_edges": sum(1 for e in edges if e.get("kind") == "tag"),
            "verified_against_edges": sum(1 for e in edges if e.get("kind") == "verified_against"),
            "cites_edges": sum(1 for e in edges if e.get("kind") == "cites"),
            "superseded_by_edges": sum(1 for e in edges if e.get("kind") == "superseded_by"),
            "related_edges": sum(1 for e in edges if e.get("kind") == "related"),
        },
    }


@web_route("GET", "/api/notes-by-path/{path:path}")
async def api_note_by_path(self, request):
    """Lookup a note's slug + summary from its vault path.

    Inline-expand cards in the network view receive vault paths as node
    ids (see api_graph); this resolves a path → slug so the card can call
    the existing /api/notes/{slug} for the body excerpt + outgoing edges.
    Returns minimal fields to keep the card render cheap.
    """
    path = (request.path_params.get("path") or "").lstrip("/").strip()
    if not path:
        return {"error": "path required"}
    slug = _slug_of(path)
    # Confirm the note is in the kb-tagged set (don't leak arbitrary vault
    # paths through this route).
    match = next(
        (n for n in self._all_notes() if n.get("path", "") == path),
        None,
    )
    if not match:
        return {"error": "not in kb", "path": path}
    return {"slug": slug, "path": path}


@web_route("GET", "/api/implementations/{code_path:path}")
async def api_implementations(self, request):
    """Reverse-lookup: which KB notes declare `implemented_in:` matching the given code path?

    Path is normalized by stripping any leading `/` and the optional `::symbol` suffix.
    Returns `{code_path, slugs: [{slug, kind, title}]}`. Powers PR-review surfaces
    and runtime "explain this code" features.
    """
    raw = request.path_params.get("code_path", "")
    code_path = str(raw).lstrip("/").split("::", 1)[0].strip()
    slugs = self._implementation_index.get(code_path, [])
    # Enrich with kind/title so callers don't need a second round-trip.
    enriched: list[dict] = []
    notes_by_slug = {_slug_of(n.get("path", "")): n for n in self._all_notes()}
    for s in slugs:
        n = notes_by_slug.get(s)
        if not n:
            continue
        props = n.get("properties", {}) or {}
        enriched.append({
            "slug": s,
            "kind": props.get("kind", ""),
            "title": props.get("title") or s.replace("-", " "),
            "path": n.get("path", ""),
        })
    return {"code_path": code_path, "slugs": enriched, "count": len(enriched)}


def _build_backlink_map(self, all_notes: list[dict]) -> dict[str, list[dict]]:
    """Backlink adjacency for every note in one O(n) pass: {target_slug -> citers}.

    The inverse of `_kb_backlinks` built once, so callers that need backlinks for
    many notes (e.g. `health()`) don't rescan all notes + re-read every body per
    target. Each citer row is {slug, kind, domain}, deduped per target and sorted
    by (kind, slug) — byte-identical to what `_kb_backlinks` returns for a single
    slug. Citation-derived citers are restricted to real KB notes, matching the
    original (which only counted citers it saw while iterating `all_notes`).
    """
    notes_by_slug = {_slug_of(n.get("path", "")): n for n in all_notes}
    seen: dict[str, set[str]] = {}
    out: dict[str, list[dict]] = {}

    def _add(target: str, citer: str) -> None:
        if not target or not citer or target == citer:
            return
        citers = seen.setdefault(target, set())
        if citer in citers:
            return
        citers.add(citer)
        props = (notes_by_slug.get(citer, {}) or {}).get("properties", {}) or {}
        out.setdefault(target, []).append({
            "slug": citer,
            "kind": props.get("kind", ""),
            "domain": props.get("domain", ""),
        })

    for n in all_notes:
        citer = _slug_of(n.get("path", ""))
        if not citer:
            continue
        targets = set(_related_targets(n.get("properties", {}) or {}))
        targets |= extract_wikilinks(self.vault_read_body(n.get("path", "")))
        for t in targets:
            _add(t, citer)
    # Citation-derived citers (for reference targets) from the precomputed index.
    for target, citers in (self._inbound_citations or {}).items():
        for c in citers:
            if c in notes_by_slug:
                _add(target, c)
    for rows in out.values():
        rows.sort(key=lambda r: (r["kind"], r["slug"]))
    return out


def _kb_backlinks(self, slug: str, all_notes: list[dict]) -> list[dict]:
    """KB notes that cite `slug` via frontmatter `related`, body wikilinks, or —
    for `kind: reference` targets — parsed `references:` citations.

    Returns enriched rows {slug, kind, domain} so the UI can color/route. A
    single-slug view over `_build_backlink_map` (which owns the traversal).
    """
    return self._build_backlink_map(all_notes).get(slug, [])
