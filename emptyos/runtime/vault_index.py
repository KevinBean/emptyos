"""Vault Index — in-memory metadata cache for vault markdown files.

In-memory metadata cache: full scan on startup, incremental updates on
vault:changed events. Apps query the index instead of scanning vault files.

Storage: plain Python dicts in memory. No SQLite, no files. Rescan on restart.

Usage:
    vault_index = kernel.services.get("vault_index")
    results = vault_index.find(tags=["job-application"], status="interview")
    vault_index.update_properties("path/to/note.md", {"status": "offer"})
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.kernel import Kernel

logger = logging.getLogger(__name__)


def _parse_fm(content: str) -> dict:
    """Parse YAML frontmatter from markdown. Handles simple lists."""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end < 0:
        return {}
    fm: dict = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for line in content[3:end].strip().split("\n"):
        stripped = line.strip()
        if stripped.startswith("- ") and current_key is not None and current_list is not None:
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue
        if current_key is not None and current_list is not None:
            fm[current_key] = current_list
            current_key = None
            current_list = None
        if ":" in line and not stripped.startswith("-"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val:
                # Inline YAML arrays "[a, b, c]" → real list, so downstream
                # tag queries like vault_query(tags=[t]) still match. Without
                # this, "[a, b, c]" stays a single malformed string and the
                # note silently disappears from tag-based lookups.
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1].strip()
                    fm[key] = (
                        [t.strip().strip('"').strip("'") for t in inner.split(",") if t.strip()]
                        if inner
                        else []
                    )
                else:
                    fm[key] = val
            else:
                current_key = key
                current_list = []
    if current_key is not None and current_list is not None:
        fm[current_key] = current_list if current_list else ""
    return fm


def _serialize_fm(fm: dict) -> str:
    """Convert dict to YAML frontmatter block."""
    lines = ["---"]
    for k, v in fm.items():
        if v is None:
            continue
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        elif isinstance(v, (int, float, bool)):
            lines.append(f"{k}: {v}")
        else:
            sv = str(v)
            if sv == "":
                continue
            if any(c in sv for c in ':#{}[]|>&*?!,"'):
                sv = f'"{sv}"'
            lines.append(f"{k}: {sv}")
    lines.append("---")
    return "\n".join(lines)


class VaultIndex:
    """In-memory vault metadata index."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self._vault: Path | None = None
        # path → {name, folder, ext, size, modified, properties: dict, tags: list}
        self._files: dict[str, dict] = {}

    # ── Lifecycle ──

    def start(self):
        self._vault = self.kernel.config.notes_path
        if not self._vault or not self._vault.exists():
            logger.warning("[VaultIndex] No vault path — index disabled")
            return

        t0 = time.time()
        count = self._full_scan()
        elapsed = round((time.time() - t0) * 1000)
        logger.info("[VaultIndex] Indexed %d files in %dms", count, elapsed)

        self.kernel.events.on("vault:changed", self._on_vault_changed)

        # Periodic rescan to catch missed filesystem events (Windows quirk)
        import asyncio

        self._rescan_task = asyncio.ensure_future(self._periodic_rescan())

    def stop(self):
        self._files.clear()
        if hasattr(self, "_rescan_task") and self._rescan_task:
            self._rescan_task.cancel()

    # ── Scanning ──

    def _full_scan(self) -> int:
        if not self._vault:
            return 0
        self._files.clear()
        count = 0
        for f in self._vault.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(self._vault).parts):
                continue
            rel = str(f.relative_to(self._vault)).replace("\\", "/")
            self._index_one(rel, f)
            count += 1
        return count

    def _index_one(self, rel_path: str, abs_path: Path):
        try:
            stat = abs_path.stat()
            content = abs_path.read_text(encoding="utf-8")
        except Exception:
            return

        fm = _parse_fm(content)
        folder = str(Path(rel_path).parent).replace("\\", "/")
        if folder == ".":
            folder = ""

        tags = fm.pop("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Extract section names from body (## headers)
        sections = []
        body_start = content.find("---", 3)
        body = content[body_start + 3 :] if body_start > 0 else content
        for line in body.split("\n"):
            if line.startswith("## ") and not line.startswith("### "):
                sections.append(line[3:].strip())

        self._files[rel_path] = {
            "path": rel_path,
            "name": Path(rel_path).stem,
            "folder": folder,
            "ext": Path(rel_path).suffix,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "properties": fm,
            "tags": tags,
            "sections": sections,
        }

    def index_file(self, rel_path: str):
        if not self._vault:
            return
        abs_path = self._vault / rel_path
        if abs_path.exists():
            self._index_one(rel_path, abs_path)
        else:
            self._files.pop(rel_path, None)

    def _incremental_scan(self) -> int:
        """Re-index files whose mtime has changed since last index. Returns count of updated files."""
        if not self._vault:
            return 0
        updated = 0
        current_paths = set()
        for f in self._vault.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(self._vault).parts):
                continue
            rel = str(f.relative_to(self._vault)).replace("\\", "/")
            current_paths.add(rel)
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            existing = self._files.get(rel)
            if not existing or existing["modified"] < mtime:
                self._index_one(rel, f)
                updated += 1
        # Remove deleted files
        for rel in list(self._files.keys()):
            if rel not in current_paths:
                del self._files[rel]
                updated += 1
        return updated

    async def _periodic_rescan(self):
        """Rescan vault every 30s to catch missed filesystem events."""
        import asyncio

        while True:
            await asyncio.sleep(30)
            try:
                updated = self._incremental_scan()
                if updated:
                    logger.info("[VaultIndex] Periodic rescan: %d files updated", updated)
            except Exception as e:
                logger.warning("[VaultIndex] Rescan error: %s", e)

    async def _on_vault_changed(self, event):
        path = event.data.get("path", "")
        change = event.data.get("change", "")
        if not path or not path.endswith(".md"):
            return
        if change == "deleted":
            self._files.pop(path, None)
        else:
            self.index_file(path)

    # ── Query ──

    @staticmethod
    def _tag_matches(query: str, entry_tags: list[str]) -> bool:
        """Hierarchical tag match using the prefix-with-slash convention.

        Query "person" matches entry tags "person", "people/friend" won't.
        Query "people" matches "people", "people/friend", "people/family".
        Exact match or prefix-with-slash — never substring.
        """
        prefix = query + "/"
        return any(t == query or t.startswith(prefix) for t in entry_tags)

    def find(
        self, tags: list[str] | None = None, folder: str | None = None, **properties
    ) -> list[dict]:
        """Find files matching tags and/or frontmatter properties.

        Tag matching is hierarchical: querying "place" also matches notes tagged
        "place/restaurant", "place/sydney", etc. — a note tagged
        `place/restaurant` IS a place.
        """
        results = []
        for entry in self._files.values():
            if tags:
                entry_tags = entry.get("tags", [])
                if not all(self._tag_matches(t, entry_tags) for t in tags):
                    continue
            if folder is not None and entry["folder"] != folder:
                continue
            if properties:
                props = entry.get("properties", {})
                if not all(props.get(k) == str(v) for k, v in properties.items()):
                    continue
            results.append(entry)
        return results

    def get_properties(self, path: str) -> dict:
        entry = self._files.get(path)
        return dict(entry["properties"]) if entry else {}

    def get_tags(self, path: str) -> list[str]:
        entry = self._files.get(path)
        return list(entry["tags"]) if entry else []

    def files_in_folder(self, folder: str) -> list[dict]:
        return [e for e in self._files.values() if e["folder"] == folder]

    def tag_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._files.values():
            for tag in entry.get("tags", []):
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def file_count(self) -> int:
        return len(self._files)

    # ── Write ──

    def update_properties(self, rel_path: str, updates: dict):
        """Update frontmatter properties in a vault note and re-index."""
        if not self._vault:
            return
        abs_path = self._vault / rel_path
        if not abs_path.exists():
            return

        content = abs_path.read_text(encoding="utf-8")
        fm = _parse_fm(content)
        fm.update(updates)

        if content.startswith("---"):
            end = content.find("---", 3)
            body = content[end + 3 :] if end > 0 else "\n" + content
        else:
            body = "\n" + content

        abs_path.write_text(_serialize_fm(fm) + body, encoding="utf-8")
        self._index_one(rel_path, abs_path)

    def append_to_section(self, rel_path: str, section: str, text: str):
        """Append text to a ## section in a vault note and re-index."""
        if not self._vault:
            return
        abs_path = self._vault / rel_path
        if not abs_path.exists():
            return

        content = abs_path.read_text(encoding="utf-8")
        header = f"## {section}"
        if header in content:
            lines = content.split("\n")
            insert_idx = None
            in_section = False
            for i, line in enumerate(lines):
                if line.strip() == header:
                    in_section = True
                    continue
                if in_section:
                    if line.strip().startswith("##"):
                        insert_idx = i
                        break
                    if line.strip():
                        insert_idx = i + 1
            if insert_idx is None:
                insert_idx = len(lines)
            lines.insert(insert_idx, text)
            content = "\n".join(lines)
        else:
            content = content.rstrip() + f"\n\n{header}\n{text}\n"

        abs_path.write_text(content, encoding="utf-8")
        self._index_one(rel_path, abs_path)

    def create_note(self, rel_path: str, frontmatter: dict, body: str):
        """Create a new vault note with frontmatter and body, then index it."""
        if not self._vault:
            return
        abs_path = self._vault / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(_serialize_fm(frontmatter) + "\n\n" + body, encoding="utf-8")
        self._index_one(rel_path, abs_path)

    def abs_path(self, rel_path: str) -> Path | None:
        if not self._vault:
            return None
        return self._vault / rel_path

    # ── Data Contracts ──

    def reconcile(
        self,
        folder: str,
        expected_tags: list[str] | None = None,
        expected_fields: list[str] | None = None,
    ) -> dict:
        """Check notes in a folder against expected frontmatter structure.

        Returns a report of what's missing — does NOT modify any files.
        Use enrich() to actually add missing tags/fields.

        Args:
            folder: vault folder to check (e.g. "30_Resources/Books")
            expected_tags: tags every note should have (e.g. ["book"])
            expected_fields: frontmatter keys every note should have (e.g. ["title", "type"])

        Returns:
            {total, compliant, gaps: [{path, missing_tags, missing_fields}]}
        """
        files = [
            e
            for e in self._files.values()
            if e["folder"] == folder or e["folder"].startswith(folder + "/")
        ]
        if not files:
            return {"total": 0, "compliant": 0, "gaps": [], "folder": folder}

        gaps = []
        for entry in files:
            missing_tags = []
            missing_fields = []
            if expected_tags:
                for tag in expected_tags:
                    if tag not in entry.get("tags", []):
                        missing_tags.append(tag)
            if expected_fields:
                props = entry.get("properties", {})
                for field in expected_fields:
                    if field not in props:
                        missing_fields.append(field)
            if missing_tags or missing_fields:
                gaps.append(
                    {
                        "path": entry["path"],
                        "name": entry["name"],
                        "missing_tags": missing_tags,
                        "missing_fields": missing_fields,
                    }
                )

        return {
            "total": len(files),
            "compliant": len(files) - len(gaps),
            "pct": round((len(files) - len(gaps)) / len(files) * 100) if files else 0,
            "gaps": gaps,
            "folder": folder,
        }

    def enrich(
        self, rel_path: str, add_tags: list[str] | None = None, defaults: dict | None = None
    ) -> bool:
        """Add missing tags and default field values to a vault note.

        Only adds — never overwrites existing values. Safe to run repeatedly.

        Args:
            rel_path: path to the note
            add_tags: tags to add if not already present
            defaults: {field: default_value} — only set if field is missing

        Returns:
            True if the note was modified, False if already compliant.
        """
        if not self._vault:
            return False
        abs_path = self._vault / rel_path
        if not abs_path.exists():
            return False

        content = abs_path.read_text(encoding="utf-8")
        fm = _parse_fm(content)
        changed = False

        # Add missing tags
        if add_tags:
            existing_tags = fm.get("tags", [])
            if isinstance(existing_tags, str):
                existing_tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
            for tag in add_tags:
                if tag not in existing_tags:
                    existing_tags.append(tag)
                    changed = True
            if changed:
                fm["tags"] = existing_tags

        # Add default field values (only if missing)
        if defaults:
            for key, default_val in defaults.items():
                if key not in fm:
                    fm[key] = default_val
                    changed = True

        if not changed:
            return False

        # Rewrite frontmatter, preserve body
        if content.startswith("---"):
            end = content.find("---", 3)
            body = content[end + 3 :] if end > 0 else "\n" + content
        else:
            body = "\n" + content

        abs_path.write_text(_serialize_fm(fm) + body, encoding="utf-8")
        self._index_one(rel_path, abs_path)
        return True
