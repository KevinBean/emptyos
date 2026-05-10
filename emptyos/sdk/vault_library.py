"""VaultLibrary — reusable vault-backed collection for any note type.

A VaultLibrary queries vault notes by tag, extracts frontmatter into
typed dicts, and provides list/search/stats/detail/update operations.

Apps subclass VaultLibrary and declare their tag + fields:

    class SongLibrary(VaultLibrary):
        tag = "song"
        fields = {
            "title": str, "artist": str, "album": str,
            "status": str, "genre": str, "language": str,
            "bpm": str, "suno_url": str, "youtube_url": str,
        }
        sort_key = "created"
        sort_reverse = True

    # In app:
    self.songs = SongLibrary(self)
    items = self.songs.list()
    detail = self.songs.detail("song-name.md")
    results = self.songs.search("jazz")
    stats = self.songs.stats(group_by=["status", "genre"])

Uses VaultIndex when available, falls back to directory scan.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from emptyos.sdk.utils import parse_frontmatter, strip_frontmatter

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


class VaultLibrary:
    """Base class for vault-backed note collections."""

    # --- Subclass must set these ---
    tag: str = ""  # Primary tag to query (e.g. "song", "book")
    extra_tags: list[str] = []  # Additional tags to include (e.g. ["tv-series"])
    fields: dict[str, type] = {}  # Field name → type (str, int, float, list)
    aliases: dict[str, list[str]] = {}  # Field name → alternative frontmatter keys

    # --- Optional overrides ---
    sort_key: str = "title"  # Default sort field
    sort_reverse: bool = False  # Descending sort
    search_fields: list[str] = []  # Fields to search (empty = all str fields)
    fallback_folder: str = ""  # For directory scan fallback
    fallback_glob: str = "*.md"  # Glob pattern for fallback

    def __init__(self, app: BaseApp, extra_fields: list[str] | None = None):
        self.app = app
        if extra_fields:
            self.fields = {**self.__class__.fields, **{f: str for f in extra_fields}}

    # --- Core operations ---

    def list(self, **filters) -> list[dict]:
        """List all items. Optional filters: status="draft", language="zh"."""
        if self._has_vault_index():
            seen: set[str] = set()
            entries: list[dict] = []
            for t in [self.tag, *self.extra_tags]:
                for e in self.app.vault_query(tags=[t]):
                    if e["path"] not in seen:
                        seen.add(e["path"])
                        entries.append(e)
            items = [self._from_index(e) for e in entries]
        else:
            items = self._scan_fallback()

        # Apply filters
        for key, val in filters.items():
            if val:
                items = [i for i in items if i.get(key) == val]

        # Sort
        items.sort(
            key=lambda i: i.get(self.sort_key, "") or "",
            reverse=self.sort_reverse,
        )
        return items

    def detail(self, filename: str) -> dict | None:
        """Get full detail for a single item, including body content."""
        if not filename.endswith(".md"):
            filename += ".md"

        path = self._find_file(filename)
        if not path:
            return None

        content = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        body = strip_frontmatter(content)
        item = self._from_frontmatter(fm, filename=filename, path=str(path))
        item["body"] = body
        return item

    def search(self, q: str) -> list[dict]:
        """Search items by keyword across configured search fields."""
        if not q:
            return self.list()

        items = self.list()
        q_lower = q.lower()
        search_in = self.search_fields or [k for k, t in self.fields.items() if t is str]

        return [
            item
            for item in items
            if any(q_lower in str(item.get(f, "")).lower() for f in search_in)
        ]

    def stats(self, group_by: list[str] | None = None) -> dict:
        """Aggregate statistics. Groups by specified fields."""
        items = self.list()
        result: dict[str, Any] = {"total": len(items)}

        for field in group_by or ["status"]:
            counts: dict[str, int] = {}
            for item in items:
                val = str(item.get(field, "") or "unknown").strip()
                if val:
                    counts[val] = counts.get(val, 0) + 1
            result[f"by_{field}"] = counts

        return result

    def update(self, filename: str, data: dict) -> dict:
        """Update frontmatter fields in a vault note."""
        if not filename.endswith(".md"):
            filename += ".md"

        path = self._find_file(filename)
        if not path:
            return {"error": "not found"}

        content = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        body = strip_frontmatter(content)

        # Only update declared fields
        for key in self.fields:
            if key in data:
                fm[key] = data[key]

        # Rebuild file
        lines = ["---"]
        for k, v in fm.items():
            if isinstance(v, list):
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            elif isinstance(v, str) and ("[[" in v or '"' in v or ":" in v):
                lines.append(f'{k}: "{v}"')
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return {"ok": True, "file": filename}

    def find_by(self, **kwargs) -> list[dict]:
        """Find items matching all given field values."""
        items = self.list()
        for key, val in kwargs.items():
            items = [i for i in items if str(i.get(key, "")).lower() == str(val).lower()]
        return items

    def count(self, **filters) -> int:
        """Count items matching filters."""
        return len(self.list(**filters))

    # --- Internal ---

    def _has_vault_index(self) -> bool:
        vi = self.app.kernel.services.get_optional("vault_index")
        return vi is not None

    def _from_index(self, entry: dict) -> dict:
        """Convert VaultIndex entry to item dict."""
        props = entry.get("properties", {})
        item = {
            "file": Path(entry["path"]).name,
            "path": entry["path"],
        }
        for field, ftype in self.fields.items():
            # Check aliases
            alt_keys = self.aliases.get(field, [])
            val = props.get(field)
            if val is None:
                for alt in alt_keys:
                    val = props.get(alt)
                    if val is not None:
                        break
            item[field] = self._coerce(val, ftype)
        return item

    def _from_frontmatter(self, fm: dict, filename: str = "", path: str = "") -> dict:
        """Convert parsed frontmatter to item dict."""
        item = {"file": filename, "path": path}
        for field, ftype in self.fields.items():
            alt_keys = self.aliases.get(field, [])
            val = fm.get(field)
            if val is None:
                for alt in alt_keys:
                    val = fm.get(alt)
                    if val is not None:
                        break
            item[field] = self._coerce(val, ftype)
        return item

    def _coerce(self, val: Any, ftype: type) -> Any:
        """Coerce a value to the declared type."""
        if val is None:
            return "" if ftype is str else 0 if ftype in (int, float) else []
        if ftype is str:
            return str(val)
        if ftype is int:
            try:
                return int(val)
            except (ValueError, TypeError):
                return 0
        if ftype is float:
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0
        if ftype is list:
            if isinstance(val, list):
                return val
            return [v.strip() for v in str(val).split(",") if v.strip()]
        return val

    def _find_file(self, filename: str) -> Path | None:
        """Find a file by name — VaultIndex first, then directory scan."""
        # VaultIndex lookup
        if self._has_vault_index():
            entries: list[dict] = []
            for t in [self.tag, *self.extra_tags]:
                entries.extend(self.app.vault_query(tags=[t]))
            matches = [e for e in entries if Path(e["path"]).name == filename]
            if matches:
                vault_path = self.app.kernel.config.notes_path
                if vault_path:
                    full = vault_path / matches[0]["path"]
                    if full.exists():
                        return full

        # Fallback: scan configured directory
        fallback_dir = self._fallback_dir()
        if fallback_dir and fallback_dir.exists():
            direct = fallback_dir / filename
            if direct.exists():
                return direct
            # Recursive search
            found = list(fallback_dir.rglob(filename))
            if found:
                return found[0]

        return None

    def _fallback_dir(self) -> Path | None:
        """Get the fallback directory for scanning."""
        if self.fallback_folder:
            vault = self.app.kernel.config.notes_path
            if vault:
                return vault / self.fallback_folder
        return None

    def _scan_fallback(self) -> list[dict]:
        """Scan directory when VaultIndex is unavailable."""
        d = self._fallback_dir()
        if not d or not d.exists():
            return []
        items = []
        for f in sorted(d.rglob(self.fallback_glob)):
            if f.name.startswith("_"):
                continue
            try:
                content = f.read_text(encoding="utf-8")
                fm = parse_frontmatter(content)
                if self.tag:
                    accepted = {self.tag} | set(self.extra_tags)
                    note_type = fm.get("type", "")
                    note_tags = fm.get("tags", [])
                    if isinstance(note_tags, str):
                        note_tags = [t.strip() for t in note_tags.split(",")]
                    if note_type not in accepted and not (accepted & set(note_tags)):
                        continue
                vault = self.app.kernel.config.notes_path
                rel = str(f.relative_to(vault)).replace("\\", "/") if vault else f.name
                item = self._from_frontmatter(fm, filename=f.name, path=rel)
                items.append(item)
            except Exception:
                continue
        return items
