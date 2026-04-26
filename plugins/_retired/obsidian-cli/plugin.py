"""Obsidian CLI plugin — optional enhancer for search + vault graph queries.

Wraps the Obsidian desktop CLI (obs.sh) for instant vault queries:
backlinks, orphans, tags, tasks, search — all from Obsidian's live index.

Pattern: "Graceful Enhancement"
- If Obsidian is running: injects ObsidianSearchProvider at priority=0 (tried first)
- If not: silently skipped, search falls back to grep provider
- Apps can optionally access graph features via self.service("obsidian-cli")

Config (emptyos.toml):
    [plugins.obsidian-cli]
    obs_script = "/path/to/vault/scripts/obs.sh"
    bash_exe = "/usr/bin/bash"     # or Git Bash path on Windows
    timeout = 10
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

from emptyos.sdk import BasePlugin

logger = logging.getLogger("obsidian-cli")


class ObsidianCLIPlugin(BasePlugin):
    name = "obsidian-cli"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._cache: dict[str, tuple[float, object]] = {}
        self._available: bool | None = None
        self._available_ts: float = 0
        self._version: str | None = None
        self._semaphore = asyncio.Semaphore(3)

    def _obs_script(self) -> str:
        vault = self.kernel.config.notes_path or Path(".")
        default = str(vault / "99_Attachments" / "scripts" / "obs.sh")
        return self.config("obs_script", default)

    def _bash_exe(self) -> str:
        import sys
        if sys.platform == "win32":
            default = r"C:\Program Files\Git\usr\bin\bash.exe"
        else:
            default = "/bin/bash"
        return self.config("bash_exe", default)

    def _timeout(self) -> int:
        return int(self.config("timeout", 10))

    # ── Lifecycle ────────────────────────────────────────────

    async def connect(self):
        if await self.available():
            logger.info("Obsidian CLI connected (v%s) — enhancing search", self._version or "?")

            from emptyos.capabilities import Provider
            plugin = self

            class ObsidianSearchProvider(Provider):
                name = "obsidian-cli"

                async def available(self) -> bool:
                    return await plugin.available()

                async def execute(self, *, query: str, path: str = "", **kwargs) -> list[dict]:
                    """Search via Obsidian's index, return same format as grep provider."""
                    text = await plugin.search(query, limit=kwargs.get("limit", 20))
                    if text is None:
                        raise RuntimeError("Obsidian CLI search failed")
                    results = []
                    for line in text.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            results.append({"path": line})
                    return results

            search_cap = self.kernel.capabilities.get("search")
            if search_cap:
                search_cap.add_provider(ObsidianSearchProvider(), priority=0)
        else:
            logger.info("Obsidian CLI not available — search will use grep")

    async def disconnect(self):
        self._cache.clear()

    async def available(self) -> bool:
        """Check if Obsidian CLI is reachable. Cached for 60s."""
        now = time.time()
        if self._available is not None and now - self._available_ts < 60:
            return self._available
        code, text = await self._run("--status")
        self._available = code == 0 and "OK" in text
        self._available_ts = now
        self._version = text.replace("OK: Obsidian ", "").strip() if self._available else None
        return self._available

    # ── Core runners ────────────────────────────────────────

    async def _run(self, *args: str) -> tuple[int, str]:
        """Run an obs.sh command. Returns (exit_code, stdout)."""
        async with self._semaphore:
            try:
                proc = await asyncio.create_subprocess_exec(
                    self._bash_exe(), self._obs_script(), *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout()
                )
                text = stdout.decode("utf-8", errors="replace").strip()
                return proc.returncode or 0, text
            except asyncio.TimeoutError:
                logger.warning("CLI timeout: obs.sh %s", " ".join(args))
                try:
                    proc.kill()
                except Exception:
                    pass
                return -1, ""
            except Exception as e:
                logger.warning("CLI error: %s", e)
                return -1, ""

    async def _cached_run(self, cache_key: str, ttl: float, *args: str) -> str | None:
        """Run with caching. Returns stdout or None on failure."""
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if time.time() - ts < ttl:
                return data
        code, text = await self._run(*args)
        if code != 0:
            return None
        self._cache[cache_key] = (time.time(), text)
        return text

    def invalidate(self, pattern: str | None = None):
        """Clear cache. If pattern given, only clear matching keys."""
        if pattern is None:
            self._cache.clear()
        else:
            self._cache = {k: v for k, v in self._cache.items() if not k.startswith(pattern)}

    # ── Service methods (graph features) ────────────────────
    # Apps access these via: obs = self.service("obsidian-cli")

    async def _count(self, cache_key: str, ttl: float, *args: str) -> int | None:
        text = await self._cached_run(cache_key, ttl, *args)
        if text is None:
            return None
        try:
            return int(text.strip())
        except ValueError:
            return None

    # Vault stats
    async def vault_stats(self) -> dict | None:
        text = await self._cached_run("vault_stats", 60, "vault")
        if text is None:
            return None
        result = {}
        for line in text.split("\n"):
            parts = line.strip().split("\t")
            if len(parts) == 2:
                key, val = parts[0].strip(), parts[1].strip()
                if key == "files":
                    result["files"] = int(val)
                elif key == "folders":
                    result["folders"] = int(val)
                elif key == "size":
                    result["size_bytes"] = int(val)
                elif key == "name":
                    result["name"] = val
        return result if result else None

    # Counts
    async def orphan_count(self) -> int | None:
        return await self._count("orphan_count", 300, "orphans", "total")

    async def unresolved_count(self) -> int | None:
        return await self._count("unresolved_count", 300, "unresolved", "total")

    async def deadend_count(self) -> int | None:
        return await self._count("deadend_count", 300, "deadends", "total")

    async def task_count(self) -> int | None:
        return await self._count("task_count", 60, "tasks", "todo", "total")

    async def task_done_count(self) -> int | None:
        return await self._count("task_done_count", 60, "tasks", "done", "total")

    async def file_count(self) -> int | None:
        return await self._count("file_count", 60, "files", "total")

    # Tags
    async def tag_counts(self, limit: int = 50) -> list[dict] | None:
        text = await self._cached_run("tag_counts", 300, "tags", "counts", "sort=count")
        if text is None:
            return None
        tags = []
        for line in text.split("\n"):
            parts = line.strip().split("\t")
            if len(parts) == 2:
                name = parts[0].strip().lstrip("#")
                try:
                    count = int(parts[1].strip())
                    tags.append({"name": name, "count": count})
                except ValueError:
                    continue
        return tags[:limit]

    async def tag_files(self, tag: str) -> list[str] | None:
        code, text = await self._run("tag", f"name={tag}", "verbose")
        if code != 0 or not text:
            return None
        return [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]

    # Links
    async def backlinks(self, file: str) -> list[str] | None:
        code, text = await self._run("backlinks", f"file={file}")
        if code != 0:
            return None
        return [l.strip() for l in text.split("\n") if l.strip()] if text else []

    async def backlink_count(self, file: str) -> int | None:
        code, text = await self._run("backlinks", f"file={file}", "total")
        if code != 0 or not text:
            return None
        try:
            return int(text.strip())
        except ValueError:
            return None

    async def outgoing_links(self, file: str) -> list[str] | None:
        code, text = await self._run("links", f"file={file}")
        if code != 0:
            return None
        return [l.strip() for l in text.split("\n") if l.strip()] if text else []

    # Tasks
    async def tasks_for_file(self, file: str) -> list[str] | None:
        code, text = await self._run("tasks", "todo", f"file={file}")
        if code != 0:
            return None
        return [l.strip() for l in text.split("\n") if l.strip()] if text else []

    # Search
    async def search(self, query: str, context: bool = False, limit: int = 10) -> str | None:
        cmd = "search:context" if context else "search"
        code, text = await self._run(cmd, f"query={query}", f"limit={limit}")
        if code != 0:
            return None
        return text if text else None

    # Properties
    async def read_property(self, name: str, path: str) -> str | None:
        code, text = await self._run("property:read", f"name={name}", f"path={path}")
        if code != 0:
            return None
        return text.strip() if text else None

    # Misc
    async def recents(self) -> list[str] | None:
        text = await self._cached_run("recents", 30, "recents")
        if text is None:
            return None
        return [l.strip() for l in text.split("\n") if l.strip()]

    async def bookmarks(self) -> list[str] | None:
        text = await self._cached_run("bookmarks", 60, "bookmarks")
        if text is None:
            return None
        return [l.strip() for l in text.split("\n") if l.strip()]

    async def files_in_folder(self, folder: str) -> list[str] | None:
        code, text = await self._run("files", f"folder={folder}")
        if code != 0:
            return None
        return [l.strip() for l in text.split("\n") if l.strip()] if text else []
