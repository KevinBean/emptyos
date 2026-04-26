"""Note Manager — CRUD on notes with fuzzy matching and frontmatter."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import json

from emptyos.sdk import BaseApp, cli_command, parse_llm_json, web_route


class NoteApp(BaseApp):

    def _notes_dir(self) -> Path:
        p = self.kernel.config.get("notes.path", "")
        return Path(p) if p else self.kernel.config.data_dir / "notes"

    def _resolve(self, title: str, folder: str = "") -> Path:
        """Resolve a title to a file path. Kebab-case, .md extension."""
        filename = title.replace(" ", "-") + ".md"
        base = self._notes_dir()
        if folder:
            base = base / folder
        return base / filename

    async def find(self, title: str, folder: str = "") -> str | None:
        """Find a note by fuzzy title match. Returns path or None."""
        base = self._notes_dir()
        search_dir = str(base / folder) if folder else str(base)
        results = await self.search(title, path=search_dir)
        if not results:
            return None
        # Best match: shortest path containing the title
        title_lower = title.lower()
        for r in results:
            path = r.get("path", r) if isinstance(r, dict) else r
            if title_lower in Path(path).stem.lower().replace("-", " "):
                return path
        return results[0].get("path", results[0]) if isinstance(results[0], dict) else results[0]

    async def create(self, title: str, folder: str = "", tags: list[str] | None = None, content: str = "") -> str:
        """Create a new note with frontmatter."""
        path = self._resolve(title, folder)
        tag_str = "\n".join(f"  - {t}" for t in (tags or []))
        frontmatter = f"---\ncreated: {date.today()}\ntags:\n{tag_str}\n---\n\n" if tag_str else f"---\ncreated: {date.today()}\n---\n\n"
        body = f"# {title}\n\n{content}"
        await self.write(str(path), frontmatter + body)
        await self.emit("note:created", {"path": str(path), "title": title})
        return str(path)

    async def get(self, title: str, folder: str = "") -> str:
        """Read a note by title (fuzzy match)."""
        path = await self.find(title, folder)
        if not path:
            # Try direct path
            direct = self._resolve(title, folder)
            path = str(direct)
        return await self.read(path)

    async def append(self, title: str, text: str, folder: str = "") -> str:
        """Append text to an existing note."""
        path = await self.find(title, folder) or str(self._resolve(title, folder))
        existing = await self.read(path)
        await self.write(path, existing.rstrip("\n") + "\n\n" + text + "\n")
        await self.emit("note:updated", {"path": path, "title": title})
        return path

    async def list_notes(self, folder: str = "") -> list[str]:
        """List note files in a folder."""
        base = self._notes_dir()
        search_dir = base / folder if folder else base
        if not search_dir.exists():
            return []
        return sorted(
            str(p.relative_to(base))
            for p in search_dir.rglob("*.md")
            if not p.name.startswith(".")
        )

    async def voice_create_note(self, title: str, body: str) -> dict:
        """Voice intent — create a titled note with body content in the default folder."""
        path = await self.create(title.strip(), folder="", tags=None, content=body or "")
        return {"say": f"Note saved: {title.strip()}."}

    @cli_command("note", help="Manage notes")
    async def cmd_note(self, action: str = "list", title: str = "", folder: str = "", text: str = ""):
        if action == "create" and title:
            path = await self.create(title, folder)
            self.print_rich(f"[green]Created:[/green] {path}")
        elif action == "read" and title:
            content = await self.get(title, folder)
            print(content)
        elif action == "append" and title and text:
            path = await self.append(title, text, folder)
            self.print_rich(f"[green]Appended to:[/green] {path}")
        elif action == "list":
            notes = await self.list_notes(folder)
            for n in notes[:30]:
                print(f"  {n}")
            if len(notes) > 30:
                print(f"  ... and {len(notes) - 30} more")
        else:
            self.print_rich("[dim]Usage: eos note {create|read|append|list} [title] [--folder F] [--text T][/dim]")

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        folder = request.query_params.get("folder", "")
        rels = await self.list_notes(folder)
        base = self._notes_dir()
        out = []
        for rel in rels:
            rel_norm = rel.replace("\\", "/")
            parts = rel_norm.rsplit("/", 1)
            sub = parts[0] if len(parts) == 2 else ""
            name = parts[-1]
            title = name[:-3] if name.endswith(".md") else name
            out.append({
                "title": title,
                "path": str(base / rel),
                "folder": sub,
            })
        return out

    @web_route("GET", "/api/get")
    async def api_get(self, request):
        title = request.query_params.get("title", "")
        folder = request.query_params.get("folder", "")
        if not title:
            return {"error": "title is required"}
        try:
            content = await self.get(title, folder)
            return {"title": title, "content": content}
        except Exception as e:
            return {"error": str(e)}

    @web_route("POST", "/api/create")
    async def api_create(self, request):
        data = await request.json()
        title = data.get("title", "").strip()
        if not title:
            return {"error": "title is required"}
        path = await self.create(title, data.get("folder", ""), data.get("tags"), data.get("content", ""))
        return {"ok": True, "path": path}

    @web_route("POST", "/api/append")
    async def api_append(self, request):
        data = await request.json()
        title = data.get("title", "").strip()
        text = data.get("text", "").strip()
        if not title or not text:
            return {"error": "title and text are required"}
        path = await self.append(title, text, data.get("folder", ""))
        return {"ok": True, "path": path}

    @web_route("POST", "/api/suggest-tags")
    async def api_suggest_tags(self, request):
        """AI suggests tags for a note based on title and content."""
        data = await request.json()
        title = data.get("title", "")
        content = data.get("content", "")[:500]
        if not title and not content:
            return {"error": "title or content required"}
        result = await self.think(
            f"Suggest 3-5 short tags for this note. Return JSON array of strings only.\n\n"
            f"Title: {title}\nContent: {content[:300]}",
            domain="text", temperature=0.3,
        )
        tags = parse_llm_json(result, fallback=[])
        if not isinstance(tags, list):
            tags = []
        return {"tags": tags}

    @web_route("GET", "/api/search")
    async def api_search(self, request):
        q = request.query_params.get("q", "")
        if not q:
            return []
        path = await self.find(q)
        if path:
            return [{"path": path, "title": q}]
        return []
