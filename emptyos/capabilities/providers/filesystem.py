"""Filesystem providers — plain file I/O. Always available where files exist."""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.basepath import resolve_under_base
from emptyos.capabilities import Provider
from emptyos.runtime.atomic_io import atomic_write_text


class FilesystemReadProvider(Provider):
    """Read files directly from the filesystem."""

    name = "filesystem"

    def __init__(self, base_path: str = ""):
        self.base_path = Path(base_path) if base_path else None

    async def available(self) -> bool:
        return True

    async def execute(self, *, path: str, **kwargs) -> str:
        target = self._resolve(path)
        return target.read_text(encoding="utf-8")

    def _resolve(self, path: str) -> Path:
        return resolve_under_base(path, self.base_path)


class FilesystemWriteProvider(Provider):
    """Write files directly to the filesystem."""

    name = "filesystem"

    def __init__(self, base_path: str = ""):
        self.base_path = Path(base_path) if base_path else None

    async def available(self) -> bool:
        return True

    async def execute(
        self, *, path: str, content: str, atomic: bool = False, **kwargs
    ) -> str:
        target = self._resolve(path)
        if atomic:
            await asyncio.to_thread(atomic_write_text, target, content)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return str(target)

    def _resolve(self, path: str) -> Path:
        return resolve_under_base(path, self.base_path)
