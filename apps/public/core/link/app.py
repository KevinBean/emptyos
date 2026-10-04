"""Link Manager — find wikilinks, backlinks, and orphan notes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, on_event, web_route

from . import linkindex

# Kept as a module-level alias: other code and tests referenced link.WIKILINK.
WIKILINK = linkindex.WIKILINK


ORPHAN_INSIGHT_SYSTEM = """You are a vault librarian helping a knowledge worker connect orphaned notes.

Group related orphans into 2-3 buckets, each one line in the form:
`<bucket label>: <note-a>, <note-b>, <note-c>`

A bucket label is 2-4 words (e.g. "Project planning", "Travel logistics"). Only group notes whose titles share a clear semantic thread; if a note doesn't fit, leave it out rather than force-fit it.

Do NOT:
- Output more than 3 buckets or more than the orphan list contains.
- Invent note titles that weren't in the input.
- Suggest where a note "should" live in the folder hierarchy — only grouping is asked.
- Add preamble, summary, or "let me know if you want…" hedges.
- Use markdown bullets, headers, or bold — plain `label: a, b, c` lines only.
"""

ORPHAN_INSIGHT_USER_TMPL = (
    "Orphan notes ({count}):\n{names}\n\n"
    "Group the related ones into 2-3 buckets."
)


class LinkApp(BaseApp):
    _index: dict | None = None
    # Bumped on every vault:changed. Lets the invalidation be lock-free (see
    # on_vault_changed) while still stopping a rebuild that started before the
    # change from publishing its pre-change result afterwards.
    _index_gen: int = 0

    def _notes_dir(self) -> Path:
        return self.kernel.config.notes_path or self.kernel.config.data_dir / "notes"

    # ── the link index ───────────────────────────────────────────────────
    # One inverted index answers every question this app asks. It used to walk
    # and re-read the whole vault per request (~20s over 23.5k notes), which is
    # also why `api_stats` carried a comment about not scanning twice — the real
    # fix was to stop scanning per request at all.

    def _read_all(self) -> dict[str, str]:
        """``{rel_path: text}`` for every vault note.

        BLOCKING BY DESIGN — an rglob plus a read_text per note. Callers hand
        this to ``asyncio.to_thread``: neither ``read_text`` nor ``self.read()``
        yields (the filesystem read provider is an ``async def`` wrapping a
        synchronous read), so running it inline pins the event loop and the
        daemon stops answering /api/health. Reading files directly rather than
        through the ``read`` capability is deliberate, as in the task indexer:
        this is one bulk scan, not N app-level reads.

        Any dot-**segment** excludes the note, not just a dot filename — the
        rule `VaultIndex` already applies (`vault_index.py`), so the two agree
        on what "the vault" is. Checking only `p.name` admitted 609 notes here
        (2.2%): `.claude/` rules, `.agent-bus/` ripple output, `.claude_backup/`,
        `.pytest_cache/`, and `.stversions/` — Syncthing's version store, i.e.
        stale copies of real notes that can carry phantom links to them. Those
        notes link to nothing by construction, so they sorted straight to the
        top of the orphan report: the first screenful of a list whose whole
        premise is "these are worth connecting" was machine config nobody can.
        """
        root = self._notes_dir()
        if not root.exists():
            return {}
        notes: dict[str, str] = {}
        for p in root.rglob("*.md"):
            rel_parts = p.relative_to(root).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            try:
                notes["/".join(rel_parts)] = p.read_text(encoding="utf-8")
            except Exception:
                continue
        return notes

    async def index(self, rebuild: bool = False) -> dict:
        """The cached link index, built on first use.

        Cached because the scan is expensive and the vault changes far less
        often than these endpoints are called. `vault:changed` invalidates it,
        so a stale answer is not possible — and the whole scan is no longer on
        the request path.
        """
        async with self.write_lock("link:index"):
            if rebuild or self._index is None:
                # Deliberately not retained: the index is derived from the
                # text and nothing reads the text again. Keeping it pinned
                # 834MB of markdown on this vault for no reader.
                gen = self._index_gen
                notes = await asyncio.to_thread(self._read_all)
                built = await asyncio.to_thread(linkindex.build_index, notes)
                # Publish only if the vault held still. A change during those
                # two awaits means `built` describes notes that no longer
                # exist, so caching it would swallow the invalidation and serve
                # a wrong orphan list until the next edit. Return it anyway —
                # that is the same answer the caller got before this guard,
                # when it held the lock across the whole build — but let the
                # next reader rebuild.
                if gen == self._index_gen:
                    self._index = built
                return built
            return self._index

    @on_event("vault:changed")
    async def on_vault_changed(self, data: dict):
        """Drop the cache; the next reader rebuilds.

        Deliberately not an incremental patch of one file: a single edit can
        resolve or break links *in both directions*, so a correct incremental
        update has to revisit every note that referenced the old target. Getting
        that subtly wrong yields a wrong orphan list, which is exactly the class
        of bug this rewrite exists to remove. Invalidate, rebuild on demand.

        NO LOCK, AND NO AWAIT — both load-bearing. This used to take
        `write_lock("link:index")`, which `index()` holds across the whole
        rebuild, so these two lines waited out a 100-345s scan (measured in
        syslog on a 9,746-orphan vault). `EventBus.emit()` awaits handlers
        serially in one task, so that pinned the entire bus, not just this app
        — the long-handler shape in `.claude/rules/debugging.md`.

        Dropping the lock is only safe because of `_index_gen`: with no await
        between the read and the write, the loop cannot interleave, so the bump
        and the clear are atomic, and a rebuild already in flight checks the
        generation before publishing. Do not add an `await` here.
        """
        self._index_gen += 1
        self._index = None

    async def outgoing(self, path: str) -> list[str]:
        """Raw wikilink targets in a note (unresolved, as authored)."""
        content = await self.read(path)
        return sorted(set(linkindex.WIKILINK.findall(content)))

    async def backlinks(self, title: str) -> list[str]:
        """Notes linking TO ``title`` — a stem, a path, or a rel_path.

        Exact. This used to delegate to the ``search`` capability with
        `[[title]]` as the query; search tokenizes, so on the live vault it
        returned 200 rows — the result cap — for a query whose real answer is 23.

        A bare stem can name several notes (3,081 stems collide in this vault),
        in which case this unions their backlinks. `ambiguous_stem_count` on
        /api/stats is how that stays visible; pass a path to disambiguate.
        """
        ix = await self.index()
        return linkindex.backlinks_for(ix, title)

    async def orphans(self) -> list[str]:
        """Genuinely disconnected notes — degree 0, nothing in **or** out.

        The old definition was "no incoming links", which reported a note
        linking out to fifty others as an orphan and flagged 82% of the vault.
        `orphan_report` keeps the other populations addressable.
        """
        ix = await self.index()
        return linkindex.orphan_report(ix)["orphans"]

    @cli_command("link", help="Manage note links")
    async def cmd_link(self, action: str = "show", title: str = ""):
        if action == "show" and title:
            links = await self.outgoing(title)
            if links:
                self.print_rich(f"[bold]Outgoing links from {title}:[/bold]")
                for l in links:
                    print(f"  → [[{l}]]")
            else:
                self.print_rich("[dim]No outgoing links.[/dim]")
        elif action == "backlinks" and title:
            files = await self.backlinks(title)
            if files:
                self.print_rich(f"[bold]Backlinks to {title}:[/bold]")
                for f in files:
                    print(f"  ← {f}")
            else:
                self.print_rich("[dim]No backlinks found.[/dim]")
        elif action == "orphans":
            orphan_list = await self.orphans()
            if orphan_list:
                self.print_rich(f"[bold]Orphan notes ({len(orphan_list)}):[/bold]")
                for o in orphan_list[:30]:
                    print(f"  {o}")
            else:
                self.print_rich("[green]No orphans.[/green]")
        else:
            self.print_rich("[dim]Usage: eos link {show|backlinks|orphans} [title][/dim]")

    @web_route("GET", "/api/backlinks")
    async def api_backlinks(self, request):
        title = request.query_params.get("title", "")
        return await self.backlinks(title) if title else []

    @web_route("GET", "/api/outgoing")
    async def api_outgoing(self, request):
        path = request.query_params.get("path", "")
        if not path:
            return {"error": "path is required"}
        return await self.outgoing(path)

    @web_route("GET", "/api/orphans")
    async def api_orphans(self, request):
        """Kept returning a bare list for existing readers, but capped.

        Pre-existing: this returned every orphan, which on the live vault was
        thousands of paths per request. `?limit=0` for the unbounded list.
        """
        try:
            limit = max(0, int(request.query_params.get("limit") or 500))
        except ValueError:
            limit = 500
        rows = await self.orphans()
        return rows[:limit] if limit else rows

    async def orphan_report(self, limit: int = 500) -> dict:
        """The three populations the single `orphans` number used to conflate.

        `orphans` (degree 0), `unreferenced` (links out, nothing links in), and
        `broken_only` (links out, but every target is missing — the action is
        fixing a link, not writing one).

        A plain method, not just the route body, because `vault-graph` calls
        this via `call_app` — a cross-app caller has no request object to hand
        a route handler, and fabricating one to reach the computation would be
        the wrong shape. This app owns the index; the other renders it.
        """
        ix = await self.index()
        rep = linkindex.orphan_report(ix)
        # Capped, with exact counts alongside. On this vault the orphan list is
        # ~9,200 paths — returning it whole was a 700KB response nobody reads,
        # and the counts are the actionable part. `limit=0` opts into the lot.
        limit = max(0, int(limit or 0))
        return {
            **{k: (v[:limit] if limit else v) for k, v in rep.items()},
            "counts": {k: len(v) for k, v in rep.items()},
            "truncated": bool(limit) and any(len(v) > limit for v in rep.values()),
            "limit": limit,
            "total_notes": len(ix["paths"]),
        }

    @web_route("GET", "/api/orphan-report")
    async def api_orphan_report(self, request):
        try:
            limit = max(0, int(request.query_params.get("limit") or 500))
        except ValueError:
            limit = 500
        return await self.orphan_report(limit=limit)

    @web_route("GET", "/api/broken-links")
    async def api_broken_links(self, request):
        """Link targets that resolve to nothing, most-referenced first.

        One typo repeated across twenty notes matters more than twenty one-off
        dead ends, so the ranking is by source count rather than alphabetical.
        """
        try:
            limit = max(0, int(request.query_params.get("limit") or 100))
        except ValueError:
            limit = 100
        ix = await self.index()
        rows = linkindex.broken_links(ix, limit=limit)
        return {"broken": rows, "total": len(ix["unresolved"])}

    @web_route("POST", "/api/reindex")
    async def api_reindex(self, request):
        """Force a rebuild. `vault:changed` handles this normally; this is for
        an external edit the watcher did not see."""
        ix = await self.index(rebuild=True)
        return {"ok": True, "notes": len(ix["paths"]), "links": ix["total_links"]}

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        ix = await self.index()
        rep = linkindex.orphan_report(ix)
        result = {
            "total_notes": len(ix["paths"]),
            "total_links": ix["total_links"],
            # Three populations, not one number that conflated them. The old
            # `orphan_count` was "notes with no incoming link" — 82% of this
            # vault — and is kept only so existing readers do not break.
            "orphan_count": len(rep["orphans"]),
            "unreferenced_count": len(rep["unreferenced"]),
            "broken_only_count": len(rep["broken_only"]),
            "broken_target_count": len(ix["unresolved"]),
            "ambiguous_stem_count": len(ix["ambiguous"]),
        }
        await self.emit("link:scan_completed", result)
        return result

    @web_route("GET", "/api/suggest")
    async def api_suggest(self, request):
        """Title-prefix suggestions for `[[` autocomplete in any app's textarea.

        Query params:
            q     — partial title (case-insensitive; matches stem or words)
            limit — max results (default 10, hard cap 50)
            kinds — comma-separated tag filter (e.g. "kb,note"); empty = all
        """
        q = (request.query_params.get("q") or "").strip().lower()
        try:
            limit = min(50, max(1, int(request.query_params.get("limit") or 10)))
        except ValueError:
            limit = 10
        kinds_raw = (request.query_params.get("kinds") or "").strip()
        kinds = [k.strip() for k in kinds_raw.split(",") if k.strip()] if kinds_raw else []

        vault_index = self.kernel.services.get("vault_index")
        if not vault_index:
            return {"suggestions": []}

        if kinds:
            # Union of per-tag finds (hierarchical match per VaultIndex._tag_matches).
            entries: dict[str, dict] = {}
            for k in kinds:
                for e in vault_index.find(tags=[k]):
                    entries[e["path"]] = e
            pool = list(entries.values())
        else:
            pool = list(vault_index._files.values())

        out = []
        for e in pool:
            name = e.get("name") or ""
            title = name.replace("-", " ")
            haystack = (name + " " + title).lower()
            if q and q not in haystack:
                continue
            tags = e.get("tags") or []
            primary_kind = next((t for t in tags if "/" not in t), tags[0] if tags else "note")
            out.append({
                "title": title,
                "name": name,
                "path": e.get("path", ""),
                "kind": primary_kind,
                "folder": e.get("folder", ""),
            })

        # Rank: prefix match on stem first, then contains, then alphabetical
        def _rank(row):
            n = row["name"].lower()
            if q and n.startswith(q):
                return (0, n)
            if q and q in n:
                return (1, n)
            return (2, n)

        out.sort(key=_rank)
        return {"suggestions": out[:limit], "count": len(out)}

    @web_route("GET", "/api/orphan-insights")
    async def api_orphan_insights(self, request):
        """AI suggests why notes are orphaned and how to connect them."""
        orphan_list = await self.orphans()
        if not orphan_list:
            return {"insights": "No orphan notes found.", "count": 0}
        names = [Path(o).stem.replace("-", " ") for o in orphan_list[:20]]
        insight = await self.think(
            ORPHAN_INSIGHT_USER_TMPL.format(count=len(names), names="\n".join(names)),
            system=ORPHAN_INSIGHT_SYSTEM,
            domain="text",
            temperature=0.4,
        )
        return {
            "insights": insight,
            "count": len(orphan_list),
            "sample": names,
            "provenance": self.last_provenance(),
        }
