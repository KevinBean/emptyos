"""Link Manager — find wikilinks, backlinks, and orphan notes."""

from __future__ import annotations

import re
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


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
    def _notes_dir(self) -> Path:
        p = self.kernel.config.get("notes.path", "")
        return Path(p) if p else self.kernel.config.data_dir / "notes"

    async def outgoing(self, path: str) -> list[str]:
        """Find all wikilinks in a note."""
        content = await self.read(path)
        return list(set(WIKILINK.findall(content)))

    async def backlinks(self, title: str) -> list[str]:
        """Find all notes that link TO a given title."""
        results = await self.search(f"[[{title}]]", path=str(self._notes_dir()))
        return [r.get("path", r) if isinstance(r, dict) else r for r in results]

    async def orphans(self) -> list[str]:
        """Find notes with no incoming links."""
        notes_dir = self._notes_dir()
        if not notes_dir.exists():
            return []

        all_notes = {p.stem: str(p) for p in notes_dir.rglob("*.md") if not p.name.startswith(".")}
        linked = set()

        for path in all_notes.values():
            try:
                content = await self.read(path)
                for link in WIKILINK.findall(content):
                    linked.add(link)
            except Exception:
                continue

        return sorted(path for stem, path in all_notes.items() if stem not in linked)

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
        return await self.orphans()

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        notes_dir = self._notes_dir()
        if not notes_dir.exists():
            return {"total_notes": 0, "total_links": 0, "orphan_count": 0}
        all_notes = list(notes_dir.rglob("*.md"))
        total_links = 0
        for p in all_notes:
            try:
                content = p.read_text(encoding="utf-8")
                total_links += len(WIKILINK.findall(content))
            except Exception:
                continue
        orphan_list = await self.orphans()
        result = {
            "total_notes": len(all_notes),
            "total_links": total_links,
            "orphan_count": len(orphan_list),
        }
        await self.emit("link:scan_completed", result)
        return result

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
