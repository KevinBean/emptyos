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

import asyncio
import logging
import os
import stat as _stat
import time
from pathlib import Path
from typing import TYPE_CHECKING

# The frontmatter parser used to live here too, as a near-verbatim copy of the
# SDK's. They drifted three separate ways — empty scalars by position, quoted
# empties, and where the block ends — each found only after the previous fix.
# One implementation now, top-level so this module can reach it without
# importing `emptyos.sdk` (which would run its package __init__ and pull in
# base_app). The private aliases are kept because this module's four internal
# call sites and the tests use them.
from emptyos.frontmatter import fm_end as _fm_end  # noqa: F401
from emptyos.frontmatter import parse_frontmatter as _parse_fm  # noqa: F401

if TYPE_CHECKING:
    from emptyos.kernel import Kernel

logger = logging.getLogger(__name__)

# macOS marks an iCloud/cloud "dataless" placeholder — a real file whose content
# has been evicted to the cloud — with this st_flags bit. Reading such a file
# triggers a SYNCHRONOUS materialize (download); when iCloud is slow/offline that
# read() blocks indefinitely with no exception. The flag is macOS/BSD-only; on
# Windows/Linux os.stat has no st_flags, so `getattr(..., 0)` makes this a no-op.
_SF_DATALESS = 0x40000000


def _read_may_block(stat_res) -> bool:
    """True if reading this file could BLOCK indefinitely instead of erroring.

    The vault scan is a boot gate: a single file whose ``read()`` hangs wedges
    the whole daemon before it binds :9000. Two classes hang rather than raise —
    and neither is caught by a try/except around the read:

      • non-regular files (FIFO/socket/device) — read() waits on a writer/device
      • macOS dataless iCloud placeholders — read() triggers a cloud materialize

    Both are skipped by the scan; the periodic rescan re-picks a placeholder once
    it has been downloaded. Ordinary read *errors* (permissions, bad encoding)
    still fall through to the reader's own try/except — those raise, so they're
    already safe.
    """
    if not _stat.S_ISREG(stat_res.st_mode):
        return True
    return bool(getattr(stat_res, "st_flags", 0) & _SF_DATALESS)


def _has_newline(sv: str) -> bool:
    return "\n" in sv or "\r" in sv


def _yaml_quote_oneline(sv: str) -> str:
    """Escape a value into a double-quoted YAML scalar on ONE physical line.

    Escapes backslash and `"` (so the quoting is well-formed) and collapses
    `\\n`/`\\r` to their escape sequences. `_parse_fm` is line-based, so a
    multi-line scalar lets any writer of user-controlled text inject or truncate
    frontmatter keys (a `\\n` opens a new `key: value` line; a bare `---` closes
    the block early). Values holding a newline are already unparseable today, so
    escaping them is strictly a fix. Escape order matters: backslash first, or
    the escapes we add get re-escaped.
    """
    sv = sv.replace("\\", "\\\\").replace('"', '\\"')
    sv = sv.replace("\r\n", "\\n").replace("\r", "\\r").replace("\n", "\\n")
    return f'"{sv}"'


def _yaml_flow_scalar(v) -> str:
    """A single value as a YAML flow scalar — quoted only when it must be,
    so plain numbers/words stay unquoted inside a flow map/sequence."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    sv = str(v)
    if sv == "" or _has_newline(sv) or any(c in sv for c in ":#{}[]|>&*?!,\"'"):
        return _yaml_quote_oneline(sv)
    return sv


def _yaml_flow(v) -> str:
    """Recursively render a Python value as one-line YAML flow syntax
    (``{k: v, ...}`` / ``[a, b, ...]``) — the nested-value counterpart to
    the scalar/list handling below.

    Used for a dict-valued frontmatter field (e.g. the geo.md ``geo:``
    block: ``{type: MultiPoint, coordinates: [[lon, lat], ...]}``).
    Flow style keeps the whole value on ONE physical line, preserving
    `_serialize_fm`'s "one key = one physical line" invariant instead of
    needing a block-style multi-line mapping.
    """
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_yaml_flow(vv)}" for k, vv in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_yaml_flow(vv) for vv in v) + "]"
    return _yaml_flow_scalar(v)


def _serialize_fm(fm: dict) -> str:
    """Convert dict to YAML frontmatter block.

    Quoting rule for string values:
    - Contains a newline (`\\n` / `\\r`) → escaped onto one line, double-quoted.
      Checked FIRST: the one-line invariant outranks the other rules, because a
      value that spans lines is an injection vector, not just ugly.
    - No special chars → bare (no quoting needed)
    - Contains `"` (e.g. JSON-in-frontmatter for lessons_json,
      attribute_schema, paragraphs_json) → single-quoted YAML so embedded
      double quotes don't need escaping. Single quotes inside are escaped
      with `''` per YAML spec.
    - Other special chars (`: # { } [ ] | > & * ? ! ,`) → double-quoted.

    Invariant: one key emits exactly one physical line (plus one line per list
    item, or the single flow-style line for a dict value — see `_yaml_flow`).
    Pinned by tests/test_unit_vault_fm_injection.py.

    A dict value (e.g. the geo.md `geo:` block) is rendered as one-line YAML
    flow syntax via `_yaml_flow` — NOT `str(v)`. Before this, `str({...})`
    produced Python's dict repr (single-quoted keys, no real YAML structure),
    which the parser reads back as an opaque STRING rather than a mapping —
    silently breaking every app following the geo.md `geo:` block convention
    the moment it round-tripped through a read.
    """
    lines = ["---"]
    for k, v in fm.items():
        if v is None:
            continue
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                si = str(item)
                lines.append(f"  - {_yaml_quote_oneline(si) if _has_newline(si) else si}")
        elif isinstance(v, dict):
            lines.append(f"{k}: {_yaml_flow(v)}")
        elif isinstance(v, (int, float, bool)):
            lines.append(f"{k}: {v}")
        else:
            sv = str(v)
            if sv == "":
                continue
            if _has_newline(sv):
                sv = _yaml_quote_oneline(sv)
            elif '"' in sv:
                # Single-quoted YAML — embedded ' → '' per YAML spec.
                sv = "'" + sv.replace("'", "''") + "'"
            elif any(c in sv for c in ":#{}[]|>&*?!,"):
                sv = f'"{sv}"'
            lines.append(f"{k}: {sv}")
    lines.append("---")
    return "\n".join(lines)


def _current_task() -> object | None:
    """The running asyncio task, or None when called from a worker thread."""
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class _NoteLock(asyncio.Lock):
    """An asyncio.Lock that remembers which task holds it.

    Plain ``asyncio.Lock.locked()`` says *a* task holds the lock, not *which*.
    The sync-mutator race detector needs that distinction: a write issued from
    inside the holder's own ``async with`` block is correct usage, while a write
    arriving from a different task (or a worker thread) while the lock is held
    is the interleaving hazard we want to surface.
    """

    def __init__(self) -> None:
        super().__init__()
        self.owner: object | None = None

    async def acquire(self) -> bool:
        acquired = await super().acquire()
        self.owner = _current_task()
        return acquired

    def release(self) -> None:
        self.owner = None
        super().release()


class VaultIndex:
    """In-memory vault metadata index."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self._vault: Path | None = None
        # path → {name, folder, ext, size, modified, properties: dict, tags: list}
        self._files: dict[str, dict] = {}
        self._by_tag: dict[str, set[str]] = {}
        # Kernel-singleton locks stay separate from indexes so a rescan cannot
        # replace a lock currently held by a writer.
        self._note_locks: dict[str, _NoteLock] = {}
        # Count of files the last scan skipped because reading them would block
        # (non-regular or macOS dataless placeholders). Surfaced at boot so a
        # cloud-evicted vault isn't silently under-indexed.
        self._skipped_blocking = 0

    # ── Lifecycle ──

    async def start(self):
        self._vault = self.kernel.config.notes_path
        if not self._vault or not self._vault.exists():
            logger.warning("[VaultIndex] No vault path — index disabled")
            return

        t0 = time.time()
        count = await self._full_scan()
        elapsed = round((time.time() - t0) * 1000)
        logger.info("[VaultIndex] Indexed %d files in %dms", count, elapsed)
        if self._skipped_blocking:
            # Loud, not silent: these notes exist but weren't indexed because
            # reading them would have hung the boot (usually iCloud-evicted
            # placeholders). They'll be picked up by the periodic rescan once
            # their content is present locally.
            logger.warning(
                "[VaultIndex] skipped %d file(s) whose read would block "
                "(non-regular or cloud-dataless placeholders) — not indexed "
                "until materialized locally",
                self._skipped_blocking,
            )
            try:
                self.kernel.syslog.warn(
                    "vault_index",
                    f"skipped {self._skipped_blocking} unreadable-without-blocking "
                    "file(s) during scan (likely iCloud-evicted); not indexed yet",
                )
            except Exception:
                pass

        self.kernel.events.on("vault:changed", self._on_vault_changed)

        # Periodic rescan to catch missed filesystem events (Windows quirk)
        self._rescan_task = asyncio.ensure_future(self._periodic_rescan())

    def stop(self):
        self._files.clear()
        self._by_tag.clear()
        if hasattr(self, "_rescan_task") and self._rescan_task:
            self._rescan_task.cancel()

    # ── Scanning ──

    async def _full_scan(self) -> int:
        if not self._vault:
            return 0
        files, by_tag = await asyncio.to_thread(self._scan_all)
        # Atomic loop-thread swap: readers see a complete old or new index.
        self._files = files
        self._by_tag = by_tag
        return len(files)

    def _scan_all(self) -> tuple[dict[str, dict], dict[str, set[str]]]:
        """Build fresh indexes without mutating live state (thread-safe)."""
        files: dict[str, dict] = {}
        by_tag: dict[str, set[str]] = {}
        self._skipped_blocking = 0
        if not self._vault:
            return files, by_tag
        for f in self._vault.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(self._vault).parts):
                continue
            rel = str(f.relative_to(self._vault)).replace("\\", "/")
            entry = self._build_entry(rel, f)
            if entry is None:
                continue
            files[rel] = entry
            for tag in entry.get("tags", []):
                by_tag.setdefault(tag, set()).add(rel)
        return files, by_tag

    @staticmethod
    def _normalize_rel(rel_path: str | Path) -> str:
        return str(rel_path).replace("\\", "/").lstrip("/")

    def note_lock(self, rel_path: str | Path) -> _NoteLock:
        """Return the kernel-wide lock for one normalized vault note path."""
        key = self._normalize_rel(rel_path)
        lock = self._note_locks.get(key)
        if lock is None:
            lock = _NoteLock()
            self._note_locks[key] = lock
        return lock

    def _build_entry(self, rel_path: str, abs_path: Path) -> dict | None:
        try:
            stat_res = abs_path.stat()
        except Exception:
            return None
        # Skip files whose read() would BLOCK (not error) — they'd wedge the
        # scan (a boot gate). See _read_may_block. stat() itself never
        # materializes content, so probing the flag here is cheap + safe.
        if _read_may_block(stat_res):
            self._skipped_blocking += 1
            return None
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception:
            return None

        fm = _parse_fm(content)
        folder = str(Path(rel_path).parent).replace("\\", "/")
        if folder == ".":
            folder = ""

        tags = fm.pop("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Extract section names from body (## headers)
        sections = []
        body_start = _fm_end(content)
        body = content[body_start + 3 :] if body_start > 0 else content
        for line in body.split("\n"):
            if line.startswith("## ") and not line.startswith("### "):
                sections.append(line[3:].strip())

        return {
            "path": rel_path,
            "name": Path(rel_path).stem,
            "folder": folder,
            "ext": Path(rel_path).suffix,
            "size": stat_res.st_size,
            "modified": stat_res.st_mtime,
            "properties": fm,
            "tags": tags,
            "sections": sections,
        }

    def _remove(self, rel_path: str) -> dict | None:
        """Remove one path from both metadata indexes."""
        old = self._files.pop(rel_path, None)
        if old:
            for tag in old.get("tags", []):
                paths = self._by_tag.get(tag)
                if paths is None:
                    continue
                paths.discard(rel_path)
                if not paths:
                    self._by_tag.pop(tag, None)
        return old

    def _index_one(self, rel_path: str, abs_path: Path):
        entry = self._build_entry(rel_path, abs_path)
        if entry is None:
            return
        self._remove(rel_path)
        self._files[rel_path] = entry
        for tag in entry.get("tags", []):
            self._by_tag.setdefault(tag, set()).add(rel_path)

    def index_file(self, rel_path: str):
        if not self._vault:
            return
        abs_path = self._contained(rel_path)
        if abs_path is not None and abs_path.exists():
            self._index_one(rel_path, abs_path)
        else:
            self._remove(rel_path)

    def _walk_mtimes(self) -> dict[str, tuple[Path, float]]:
        """Walk the vault once: {rel_path: (abs_path, mtime)}. Pure IO — reads
        only self._vault (immutable after start), touches NO self._files, so it
        is safe to run on an asyncio.to_thread worker. The rglob+stat over
        thousands of files is the every-30s cost that must stay off the loop."""
        out: dict[str, tuple[Path, float]] = {}
        if not self._vault:
            return out
        for f in self._vault.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(self._vault).parts):
                continue
            rel = str(f.relative_to(self._vault)).replace("\\", "/")
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            out[rel] = (f, mtime)
        return out

    def _apply_scan(self, walk: dict[str, tuple[Path, float]]) -> int:
        """Reconcile a _walk_mtimes() snapshot against self._files. MUST run on
        the event-loop thread — it mutates self._files (via _index_one / del),
        which loop-thread readers like find() iterate, so it cannot run
        concurrently on a worker thread. Only changed files are re-read."""
        updated = 0
        for rel, (f, mtime) in walk.items():
            existing = self._files.get(rel)
            if not existing or existing["modified"] < mtime:
                self._index_one(rel, f)
                updated += 1
        for rel in list(self._files.keys()):
            if rel not in walk:
                self._remove(rel)
                updated += 1
        return updated

    def _incremental_scan(self) -> int:
        """Re-index files whose mtime has changed since last index. Returns count of updated files."""
        if not self._vault:
            return 0
        return self._apply_scan(self._walk_mtimes())

    async def _periodic_rescan(self):
        """Rescan vault every 30s to catch missed filesystem events."""
        while True:
            await asyncio.sleep(30)
            try:
                # rglob+stat off the loop; dict mutation back on the loop.
                walk = await asyncio.to_thread(self._walk_mtimes)
                updated = self._apply_scan(walk)
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
            self._remove(path)
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
        if tags:
            candidates: set[str] | None = None
            for query in tags:
                prefix = query + "/"
                matched: set[str] = set()
                for indexed_tag, paths in self._by_tag.items():
                    if indexed_tag == query or indexed_tag.startswith(prefix):
                        matched.update(paths)
                candidates = matched if candidates is None else candidates & matched
                if not candidates:
                    return []
            # sorted(): set iteration order varies with PYTHONHASHSEED, and the
            # pre-index implementation returned stable scan order. Callers that
            # take results[0] must not get a different note run-to-run. Only the
            # matched subset is sorted, not the whole index.
            entries = (self._files[path] for path in sorted(candidates) if path in self._files)
        else:
            entries = self._files.values()

        results = []
        for entry in entries:
            if folder is not None and entry["folder"] != folder:
                continue
            if properties:
                props = entry.get("properties", {})
                if not all(self._prop_eq(props.get(k), v) for k, v in properties.items()):
                    continue
            results.append(entry)
        return results

    @staticmethod
    def _prop_eq(stored, query) -> bool:
        """Compare a query value against a frontmatter property.

        `_parse_fm` stores every YAML scalar as a string ("true", "5", ...), so
        a plain `stored == str(query)` silently fails for booleans: a caller
        passing `published=True` produces "True" but frontmatter holds "true".
        Booleans are matched case-insensitively against true/false; everything
        else keeps the original exact-string comparison (no behaviour change for
        the existing string/int callers).
        """
        if isinstance(query, bool):
            return str(stored).strip().lower() == ("true" if query else "false")
        return stored == str(query)

    def get_properties(self, path: str) -> dict:
        entry = self._files.get(path)
        return dict(entry["properties"]) if entry else {}

    def get_tags(self, path: str) -> list[str]:
        entry = self._files.get(path)
        return list(entry["tags"]) if entry else []

    def files_in_folder(self, folder: str) -> list[dict]:
        return [e for e in self._files.values() if e["folder"] == folder]

    def tag_counts(self) -> dict[str, int]:
        counts = {tag: len(paths) for tag, paths in self._by_tag.items()}
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def file_count(self) -> int:
        return len(self._files)

    # ── Write ──

    def _warn_if_note_locked(self, rel_path: str) -> None:
        """Expose sync-mutator races until these APIs can become awaitable.

        Warns only when *another* task holds the note's lock. A write issued
        from inside the holder's own ``async with self.note_lock(path)`` block
        is the correct pattern — warning on it would flag every app we already
        fixed and drown the signal. Peeks at the registry rather than calling
        ``note_lock``, which would allocate (and permanently retain) a lock for
        every note ever written.
        """
        lock = self._note_locks.get(self._normalize_rel(rel_path))
        if lock is None or not lock.locked():
            return
        if lock.owner is not None and lock.owner is _current_task():
            return  # self-held: this write is inside its own critical section
        syslog = getattr(self.kernel, "syslog", None)
        if syslog:
            syslog.warn(
                "vault_index",
                "vault write while another task holds the note lock — race candidate",
                data={"path": self._normalize_rel(rel_path)},
            )

    def update_properties(self, rel_path: str, updates: dict):
        """Update frontmatter properties in a vault note and re-index."""
        if not self._vault:
            return
        abs_path = self._contained(rel_path)
        if abs_path is None or not abs_path.exists():
            return
        self._warn_if_note_locked(rel_path)

        content = abs_path.read_text(encoding="utf-8")
        fm = _parse_fm(content)
        fm.update(updates)

        if content.startswith("---"):
            end = _fm_end(content)
            body = content[end + 3 :] if end > 0 else "\n" + content
        else:
            body = "\n" + content

        abs_path.write_text(_serialize_fm(fm) + body, encoding="utf-8")
        self._index_one(rel_path, abs_path)

    def append_to_section(self, rel_path: str, section: str, text: str):
        """Append text to a ## section in a vault note and re-index."""
        if not self._vault:
            return
        abs_path = self._contained(rel_path)
        if abs_path is None or not abs_path.exists():
            return
        self._warn_if_note_locked(rel_path)

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
        abs_path = self._contained(rel_path)
        if abs_path is None:
            # Raise rather than no-op: this call CREATES directories, so a
            # silent skip would look like a successful write to the caller.
            # Checked BEFORE the lock warning — no point reporting on a lock
            # for a path we are about to refuse outright.
            raise ValueError(f"path escapes the vault root: {rel_path!r}")
        self._warn_if_note_locked(rel_path)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(_serialize_fm(frontmatter) + "\n\n" + body, encoding="utf-8")
        self._index_one(rel_path, abs_path)

    def abs_path(self, rel_path: str) -> Path | None:
        if not self._vault:
            return None
        return self._contained(rel_path)

    def _contained(self, rel_path: str) -> Path | None:
        """Join ``rel_path`` under the vault root, or None if it escapes.

        Every write here takes a vault-RELATIVE path that apps build from ids
        arriving on HTTP path params and request bodies. A raw ``..`` in one
        of those escaped the vault entirely: a confirmed traversal wrote and
        deleted files outside it. This is the platform choke-point — apps
        should still reject bad ids at their own boundary (that gives the
        user a decent error), but nothing gets to leave the vault by
        forgetting to.

        Uses normpath, NOT resolve(): the threat is a textual ``..``, and
        resolve() would additionally follow symlinks, breaking the legitimate
        case of a user symlinking a folder into their own vault.
        """
        if not self._vault:
            return None
        root = os.path.normpath(str(self._vault))
        joined = os.path.normpath(os.path.join(root, str(rel_path)))
        if joined != root and not joined.startswith(root + os.sep):
            return None
        return Path(joined)

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
        abs_path = self._contained(rel_path)
        if abs_path is None or not abs_path.exists():
            return False
        self._warn_if_note_locked(rel_path)

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
            end = _fm_end(content)
            body = content[end + 3 :] if end > 0 else "\n" + content
        else:
            body = "\n" + content

        abs_path.write_text(_serialize_fm(fm) + body, encoding="utf-8")
        self._index_one(rel_path, abs_path)
        return True
