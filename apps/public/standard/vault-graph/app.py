"""VaultGraphApp — vault content topology.

Force-directed graph of how the user's vault notes link to each other.
Companion to ``/topology`` (which shows the *system* graph of apps,
capabilities, events, services). This app shows the *content* graph the
user actually lives in: notes, wikilinks, frontmatter references, KB
clause→reference chains, and (opt-in) shared-tag + folder edges.

Clicking a node opens the EOS_UI.timeline4D drawer for that note —
see ``.claude/rules/app-ui-patterns.md`` § "4D Timeline Panel".

Edges:
- ``wikilink``        — ``[[note]]`` references parsed from body via
                         ``emptyos.sdk.utils.extract_wikilinks``
- ``frontmatter_ref`` — known link-shape fields (configurable below)
- ``kb_clause``       — citation strings parsed via the kb app's
                         ``resolve_reference`` route
- ``tag_shared``      — opt-in (``?shared_tags=1``); pairwise per tag,
                         capped so the graph doesn't hairball
- ``folder``          — opt-in (``?folder_edges=1``); parent folder →
                         child note

Performance: in-memory cache invalidated on ``vault:changed``. Full rebuild
is bounded by note-count × avg body size; for vaults under 5k notes,
sub-second.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any

from emptyos.sdk import BaseApp, on_event, web_route
from emptyos.sdk.utils import extract_wikilinks, normalize_link_target


# Frontmatter fields whose value(s) are wikilink-shaped (a note title or
# slug). Each match becomes a ``frontmatter_ref`` edge. New consumers
# add their fields here; keep the list short — every field forces a
# resolve attempt per note per scan.
FRONTMATTER_REF_FIELDS = [
    "verified_against",
    "implemented_in",
    "references",
    "related",
    "parent",
    "project",
    "org",
    "source",
    "supersedes",
    "superseded_by",
    # Where a generated artifact ended up being used ("kb/<slug>",
    # "publish/<post>-<concept>"). This is the only reliably-resolvable
    # direction for an artifact record: its own filename is a fixed
    # `record.md` under a per-id folder, so nothing can point *at* it by slug.
    "used_in",
]


class VaultGraphApp(BaseApp):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # cache: keyed by the request's filter dict; value is the assembled
        # {nodes, edges, stats} response. Invalidated on vault:changed.
        self._cache: dict[str, dict] = {}
        self._cache_lock = asyncio.Lock()

    async def setup(self):
        await super().setup()
        # Pre-warm a default cache entry (best-effort, non-blocking)
        self.spawn_background(self._build_graph())

    @on_event("vault:changed")
    async def _on_vault_change(self, event):
        """Drop the cache on any vault mutation; rebuild lazily on next read."""
        self._cache.clear()

    # ── HTTP API ──────────────────────────────────────────────────────────

    @web_route("GET", "/api/graph")
    async def api_graph(self, request):
        """Return the vault topology graph.

        Query params:
            kinds       — csv tag filter ("kb,note,job-application")
            folders     — csv folder prefix filter ("10_Projects,20_Areas")
            limit       — node cap (default 800, hard cap 2000); an explicit
                          smaller value is honored down to 1.
            shared_tags — "1" to include shared-tag edges
            folder_edges — "1" to include folder containment edges
        """
        kinds_raw = (request.query_params.get("kinds") or "").strip()
        kinds = [k.strip() for k in kinds_raw.split(",") if k.strip()] if kinds_raw else []
        folders_raw = (request.query_params.get("folders") or "").strip()
        folders = (
            [f.strip() for f in folders_raw.split(",") if f.strip()] if folders_raw else []
        )
        try:
            # Floor at 1 (not 10) so an explicit ?limit=5 is respected — the
            # node cap is a contract, not just a default. 0/blank → default 800.
            limit = min(2000, max(1, int(request.query_params.get("limit") or 800)))
        except ValueError:
            limit = 800
        include_tag_edges = (request.query_params.get("shared_tags") or "").strip() == "1"
        include_folder_edges = (request.query_params.get("folder_edges") or "").strip() == "1"

        cache_key = f"k={','.join(kinds)}|f={','.join(folders)}|lim={limit}|tag={int(include_tag_edges)}|fold={int(include_folder_edges)}"
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            built = await self._build_graph(
                kinds=kinds,
                folders=folders,
                limit=limit,
                include_tag_edges=include_tag_edges,
                include_folder_edges=include_folder_edges,
            )
            self._cache[cache_key] = built
            return built

    @web_route("GET", "/api/node/{path:path}")
    async def api_node(self, request):
        """Single-node detail: outgoing + incoming + frontmatter summary."""
        path = request.path_params.get("path") or ""
        if not path:
            return {"error": "path required"}
        vi = self.kernel.services.get_optional("vault_index")
        if not vi:
            return {"error": "vault_index unavailable"}
        entry = vi._files.get(path)
        if not entry:
            return {"error": "not found", "path": path}

        # Build outgoing on the fly (small, single-note scan)
        outgoing = await self._outgoing_for(path, vi)
        incoming = await self._incoming_for(path, vi)
        return {
            "path": path,
            # `_vault_path` is the key the 4D-timeline auto-mount contract
            # (.claude/rules/app-ui-patterns.md) reads off detail responses;
            # expose it alongside `path` so a future consumer wiring the drawer
            # naively still works.
            "_vault_path": path,
            "name": entry.get("name", ""),
            "folder": entry.get("folder", ""),
            "tags": entry.get("tags", []),
            "frontmatter": entry.get("properties", {}),
            "outgoing": outgoing,
            "incoming": incoming,
        }

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        vi = self.kernel.services.get_optional("vault_index")
        if not vi:
            return {"total": 0, "cached_views": 0}
        return {"total": vi.file_count(), "cached_views": len(self._cache)}

    @web_route("GET", "/api/orphans")
    async def api_orphans(self, request):
        """Disconnected notes — fetched from `link`, never re-derived here.

        `link` owns the vault-wide inverted link index, so this is one call
        rather than a second scan of the same 27.8k notes carrying a second
        definition of "orphan" to drift from the first.

        Not cached here on purpose — `link` already caches, and a second cache
        would have to guess when the first went stale. Its cost profile is
        bimodal and worth knowing before wiring this anywhere hot: 0.1s warm,
        but 36-57s whenever a vault write has invalidated the index (measured
        2026-08-17 on :9000). The panel is lazy for that reason.

        Deliberately *not* computed from ``_build_graph``'s slice. The graph
        draws at most 2000 most-recent notes, so a degree-0 count taken from it
        answers "isolated among the notes I happened to draw" while reading as
        "isolated in your vault" — a different, smaller, and misleading number
        (the graph's own cap-honesty gap, made worse by dressing it up as an
        orphan list). Vault-wide counts ride along so the panel can say which
        one it is.

        Degrades rather than 500s when `link` is absent: it is an
        ``optional_apps`` integration, and the graph is fine without it.
        """
        try:
            limit = min(500, max(1, int(request.query_params.get("limit") or 100)))
        except ValueError:
            limit = 100
        rep, err = await self.try_call_app("link", "orphan_report", limit=limit)
        if err:
            return {"available": False, "error": err}
        return {"available": True, **(rep or {})}

    # ── Graph build ───────────────────────────────────────────────────────

    async def _build_graph(
        self,
        *,
        kinds: list[str] | None = None,
        folders: list[str] | None = None,
        limit: int = 800,
        include_tag_edges: bool = False,
        include_folder_edges: bool = False,
    ) -> dict:
        kinds = kinds or []
        folders = folders or []

        vi = self.kernel.services.get_optional("vault_index")
        if not vi:
            return {"nodes": [], "edges": [], "stats": {}, "error": "vault_index unavailable"}

        # Resolve entry pool
        if kinds:
            pool: dict[str, dict] = {}
            for k in kinds:
                for e in vi.find(tags=[k]):
                    pool[e["path"]] = e
            entries = list(pool.values())
        else:
            entries = list(vi._files.values())

        if folders:
            entries = [
                e
                for e in entries
                if any((e.get("folder") or "").startswith(f) for f in folders)
            ]

        # Sort by recency; cap. `matched` is the pool the filters selected,
        # BEFORE the cap — the only honest denominator for "showing N of M".
        # `total_indexed` is the wrong one whenever a filter is active: with
        # `kinds=kb` the graph draws 800 of the KB notes, not 800 of the vault,
        # and quoting the vault total there is a different wrong number rather
        # than a fix. The UI needs both, so both ship.
        matched = len(entries)
        entries.sort(key=lambda e: e.get("modified", 0), reverse=True)
        entries = entries[:limit]

        # Build name→path index for wikilink resolution
        name_to_path: dict[str, str] = {}
        slug_to_path: dict[str, str] = {}
        path_to_path: dict[str, str] = {}
        for e in entries:
            n = (e.get("name") or "").lower()
            if n and n not in name_to_path:
                name_to_path[n] = e["path"]
            stem = Path(e["path"]).stem.lower()
            if stem and stem not in slug_to_path:
                slug_to_path[stem] = e["path"]
            path_to_path[self._path_key(e["path"])] = e["path"]

        # Nodes
        nodes: list[dict] = []
        for e in entries:
            tags = e.get("tags") or []
            primary_kind = next((t for t in tags if "/" not in t), tags[0] if tags else "note")
            nodes.append(
                {
                    "id": e["path"],
                    "label": self._node_label(e),
                    "kind": primary_kind,
                    "folder": e.get("folder", ""),
                    "modified": e.get("modified", 0),
                    "tags": tags,
                }
            )

        edges: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        notes_dir = self.vault_root

        # Wikilink + frontmatter-ref + kb-clause edges (one body read per note)
        for e in entries:
            if not notes_dir:
                break
            path = e["path"]
            # Body read for wikilinks — via read capability per Rule #1
            try:
                body = await self.read(str(notes_dir / path))
            except Exception:
                body = ""

            # 1. Wikilinks
            for target in extract_wikilinks(body):
                key = self._link_key(target)
                # Path form first: it is the more specific of the two.
                target_path = (path_to_path.get(key) or name_to_path.get(key)
                               or slug_to_path.get(key))
                if not target_path or target_path == path:
                    continue
                ek = (path, target_path, "wikilink")
                if ek in seen:
                    continue
                seen.add(ek)
                edges.append({"from": path, "to": target_path, "kind": "wikilink"})

            # 2. Frontmatter refs
            fm = e.get("properties") or {}
            for field in FRONTMATTER_REF_FIELDS:
                v = fm.get(field)
                if not v:
                    continue
                candidates = v if isinstance(v, list) else [v]
                for c in candidates:
                    if not isinstance(c, str):
                        continue
                    ref_target = self._resolve_ref(c, name_to_path, slug_to_path)
                    if not ref_target or ref_target == path:
                        continue
                    ek = (path, ref_target, "frontmatter_ref")
                    if ek in seen:
                        continue
                    seen.add(ek)
                    edges.append(
                        {
                            "from": path,
                            "to": ref_target,
                            "kind": "frontmatter_ref",
                            "field": field,
                        }
                    )

            # 3. KB clause edges via kb.resolve_reference (feature-detect)
            kb_refs = fm.get("references")
            if kb_refs and isinstance(kb_refs, list):
                for ref_str in kb_refs:
                    if not isinstance(ref_str, str):
                        continue
                    try:
                        resolved = await self.call_app(
                            "kb", "resolve_reference", reference=ref_str
                        )
                    except Exception:
                        resolved = None
                    if not resolved or not isinstance(resolved, dict):
                        continue
                    slug = resolved.get("slug")
                    if not slug:
                        continue
                    target_path = slug_to_path.get(slug.lower())
                    if not target_path or target_path == path:
                        continue
                    ek = (path, target_path, "kb_clause")
                    if ek in seen:
                        continue
                    seen.add(ek)
                    edges.append(
                        {"from": path, "to": target_path, "kind": "kb_clause"}
                    )

        # 4. Shared-tag edges (opt-in)
        if include_tag_edges:
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
                        if (a, b, "tag_shared") in seen or (b, a, "tag_shared") in seen:
                            continue
                        seen.add((a, b, "tag_shared"))
                        edges.append(
                            {"from": a, "to": b, "kind": "tag_shared", "tag": t}
                        )

        # 5. Folder containment edges (opt-in) — adds folder pseudo-nodes
        folder_node_ids: set[str] = set()
        if include_folder_edges:
            for e in entries:
                folder = e.get("folder") or ""
                if not folder:
                    continue
                folder_node_id = "folder::" + folder
                if folder_node_id not in folder_node_ids:
                    folder_node_ids.add(folder_node_id)
                    nodes.append(
                        {
                            "id": folder_node_id,
                            "label": folder.split("/")[-1] or folder,
                            "kind": "folder",
                            "folder": folder,
                            "modified": 0,
                            "tags": [],
                        }
                    )
                ek = (folder_node_id, e["path"], "folder")
                if ek in seen:
                    continue
                seen.add(ek)
                edges.append({"from": folder_node_id, "to": e["path"], "kind": "folder"})

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_indexed": vi.file_count(),
                "in_slice": len(nodes),
                # The cap-honesty triple. `in_slice` counts *nodes*, which
                # includes folder pseudo-nodes when `folder_edges=1` — so it
                # overstates how much of the vault is drawn and can't be the
                # numerator. These three can:
                "notes_drawn": len(entries),
                "matched": matched,
                "capped": matched > len(entries),
                "limit": limit,
                "filtered": bool(kinds or folders),
                "wikilink_edges": sum(1 for e in edges if e["kind"] == "wikilink"),
                "frontmatter_edges": sum(
                    1 for e in edges if e["kind"] == "frontmatter_ref"
                ),
                "kb_clause_edges": sum(1 for e in edges if e["kind"] == "kb_clause"),
                "tag_edges": sum(1 for e in edges if e["kind"] == "tag_shared"),
                "folder_edges": sum(1 for e in edges if e["kind"] == "folder"),
            },
        }

    # ── Single-note read paths for /api/node/{path} ──────────────────────

    async def _outgoing_for(self, path: str, vi: Any) -> list[dict]:
        notes_dir = self.vault_root
        if not notes_dir:
            return []
        try:
            body = await self.read(str(notes_dir / path))
        except Exception:
            return []
        # Build minimal indexes from whole vault (single-shot — small data)
        name_to_path: dict[str, str] = {}
        slug_to_path: dict[str, str] = {}
        path_to_path: dict[str, str] = {}
        for p, e in vi._files.items():
            n = (e.get("name") or "").lower()
            if n and n not in name_to_path:
                name_to_path[n] = p
            stem = Path(p).stem.lower()
            if stem and stem not in slug_to_path:
                slug_to_path[stem] = p
            path_to_path[self._path_key(p)] = p
        out: list[dict] = []
        seen_targets: set[tuple[str, str]] = set()
        for target in extract_wikilinks(body):
            key = self._link_key(target)
            tp = (path_to_path.get(key) or name_to_path.get(key)
                  or slug_to_path.get(key))
            if not tp or tp == path:
                continue
            if (tp, "wikilink") in seen_targets:
                continue
            seen_targets.add((tp, "wikilink"))
            out.append({"path": tp, "kind": "wikilink"})
        fm = (vi._files.get(path) or {}).get("properties") or {}
        for field in FRONTMATTER_REF_FIELDS:
            v = fm.get(field)
            if not v:
                continue
            candidates = v if isinstance(v, list) else [v]
            for c in candidates:
                if not isinstance(c, str):
                    continue
                tp = self._resolve_ref(c, name_to_path, slug_to_path)
                if not tp or tp == path:
                    continue
                if (tp, "frontmatter_ref") in seen_targets:
                    continue
                seen_targets.add((tp, "frontmatter_ref"))
                out.append({"path": tp, "kind": "frontmatter_ref", "field": field})
        return out


    # Path form is this vault's most common shape — 855 of 1362 links in a
    # 400-note sample — and resolving stems only made ~63% of real links
    # invisible to the graph. `link` had the identical defect and was fixed
    # 2026-08-17; both now share one normaliser so they cannot drift apart
    # again (the local copies had already diverged over `#anchor` handling).
    _link_key = staticmethod(normalize_link_target)
    _path_key = staticmethod(normalize_link_target)

    async def _incoming_for(self, path: str, vi: Any) -> list[dict]:
        target_stem = Path(path).stem.lower()
        target_name = ((vi._files.get(path) or {}).get("name") or "").lower()
        target_path = self._path_key(path)
        notes_dir = self.vault_root
        if not notes_dir:
            return []
        out: list[dict] = []
        seen_sources: set[str] = set()
        for src_path, e in vi._files.items():
            if src_path == path:
                continue
            try:
                body = await self.read(str(notes_dir / src_path))
            except Exception:
                continue
            wl = {self._link_key(t) for t in extract_wikilinks(body)}
            if (target_path in wl or target_stem in wl
                    or (target_name and target_name in wl)):
                if src_path not in seen_sources:
                    seen_sources.add(src_path)
                    out.append({"path": src_path, "kind": "wikilink"})
        return out

    @staticmethod
    def _node_label(entry: dict) -> str:
        """Display label for a graph node — declared `title`, else the stem.

        The stem is a poor label whenever a note lives in a per-entity folder
        under a fixed filename: every `<app>/outputs/<id>/record.md` renders as
        a node labelled "record", indistinguishable across an app's entire
        output history. The label is display + search-filter only
        (pages/index.html), so preferring the note's own human name is safe.
        """
        title = str((entry.get("properties") or {}).get("title") or "").strip()
        return title or (entry.get("name") or "").replace("-", " ")

    @staticmethod
    def _resolve_ref(value: str, name_to_path: dict[str, str], slug_to_path: dict[str, str]) -> str | None:
        """Resolve a free-text frontmatter value to a vault path, if it
        looks like a wikilink-shape reference. Tolerates [[brackets]] and
        slug-or-name forms; returns None when nothing matches.
        """
        v = (value or "").strip().lower()
        if not v:
            return None
        # Strip wikilink brackets if present
        if v.startswith("[[") and v.endswith("]]"):
            v = v[2:-2]
        # Strip section anchor (#) and alias (|)
        v = v.split("#", 1)[0].split("|", 1)[0].strip()
        # Strip ".md" suffix
        if v.endswith(".md"):
            v = v[:-3]
        hit = name_to_path.get(v) or slug_to_path.get(v)
        if hit or "/" not in v:
            return hit
        # Path-ish value ("kb/cse-thrust", "30_Resources/.../note"): both index
        # maps are keyed by bare name/stem, so a qualified reference misses on
        # the exact lookup even though the note is right there. Retry on the
        # last segment. Tried only after the exact form so a note literally
        # named "kb/cse-thrust" still wins.
        return slug_to_path.get(v.rsplit("/", 1)[-1])

    # ── Timeline contract (also wired) ──
    # vault-graph itself doesn't carry per-entity detail views, so it
    # doesn't declare [provides.timeline]. The node-click in
    # pages/index.html opens EOS_UI.timeline4D(path) directly.
