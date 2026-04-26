"""Filesystem providers — plain file I/O. Always available where files exist."""

from __future__ import annotations

from pathlib import Path

from emptyos.capabilities import Provider


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
        p = Path(path)
        if p.is_absolute():
            return p
        if self.base_path:
            return self.base_path / p
        return p


class FilesystemWriteProvider(Provider):
    """Write files directly to the filesystem."""

    name = "filesystem"

    def __init__(self, base_path: str = ""):
        self.base_path = Path(base_path) if base_path else None

    async def available(self) -> bool:
        return True

    async def execute(self, *, path: str, content: str, **kwargs) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target)

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        if self.base_path:
            return self.base_path / p
        return p
