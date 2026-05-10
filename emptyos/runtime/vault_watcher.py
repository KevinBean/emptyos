"""Vault watcher — file changes in the vault emit events on the EventBus.

Apps subscribe via @on_event("vault:changed") to react to file modifications.
Debounces rapid changes (1s) to prevent event spam.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class VaultWatcher:
    """Watch vault directory for file changes, emit events."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self._task: asyncio.Task | None = None
        self._running = False
        self._debounce_seconds = 1.0

    @property
    def vault_path(self) -> Path | None:
        p = self.kernel.config.notes_path
        return p if p and p.exists() else None

    async def start(self):
        """Start watching the vault directory."""
        if not self.kernel.config.get("notes.watch", False):
            return
        vault = self.vault_path
        if not vault:
            print("[VaultWatcher] No vault path configured or path does not exist")
            return

        self._running = True
        self._task = asyncio.create_task(self._watch_loop(vault))
        print(f"[VaultWatcher] Watching {vault}")

    async def stop(self):
        """Stop watching."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _watch_loop(self, vault_path: Path):
        """Main watch loop using watchfiles."""
        try:
            from watchfiles import Change, awatch
        except ImportError:
            print("[VaultWatcher] watchfiles not installed, falling back to polling")
            await self._poll_loop(vault_path)
            return

        try:
            async for changes in awatch(
                vault_path,
                watch_filter=self._filter,
                debounce=int(self._debounce_seconds * 1000),
                stop_event=asyncio.Event() if not self._running else None,
            ):
                if not self._running:
                    break
                for change_type, path_str in changes:
                    rel_path = str(Path(path_str).relative_to(vault_path))
                    # Normalize to forward slashes
                    rel_path = rel_path.replace("\\", "/")

                    change_name = {
                        Change.added: "added",
                        Change.modified: "modified",
                        Change.deleted: "deleted",
                    }.get(change_type, "modified")

                    await self.kernel.events.emit(
                        "vault:changed",
                        {"path": rel_path, "change": change_name},
                        source="vault_watcher",
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[VaultWatcher] Error: {e}")

    @staticmethod
    def _filter(change, path: str) -> bool:
        """Filter which files to watch."""
        p = Path(path)
        # Skip hidden directories and files
        for part in p.parts:
            if part.startswith("."):
                return False
        # Only watch markdown and common text files
        if p.is_file() or not p.exists():  # deleted files won't exist
            suffix = p.suffix.lower()
            return suffix in (".md", ".txt", ".toml", ".json", ".yaml", ".yml", "")
        return False

    async def _poll_loop(self, vault_path: Path):
        """Fallback polling watcher if watchfiles is not available."""
        known: dict[str, float] = {}

        # Initial scan
        for f in vault_path.rglob("*.md"):
            try:
                known[str(f)] = f.stat().st_mtime
            except OSError:
                pass

        while self._running:
            await asyncio.sleep(2.0)
            current: dict[str, float] = {}
            for f in vault_path.rglob("*.md"):
                try:
                    current[str(f)] = f.stat().st_mtime
                except OSError:
                    pass

            # Detect changes
            for path_str, mtime in current.items():
                rel = str(Path(path_str).relative_to(vault_path)).replace("\\", "/")
                if path_str not in known:
                    await self.kernel.events.emit(
                        "vault:changed", {"path": rel, "change": "added"}, source="vault_watcher"
                    )
                elif mtime > known[path_str]:
                    await self.kernel.events.emit(
                        "vault:changed", {"path": rel, "change": "modified"}, source="vault_watcher"
                    )

            for path_str in known:
                if path_str not in current:
                    rel = str(Path(path_str).relative_to(vault_path)).replace("\\", "/")
                    await self.kernel.events.emit(
                        "vault:changed", {"path": rel, "change": "deleted"}, source="vault_watcher"
                    )

            known = current
