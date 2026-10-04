"""Throwaway plugin fixture for marketplace install tests. py_compile-clean,
never actually loaded by a daemon (tests assert staging only)."""

from emptyos.sdk import BasePlugin


class MktTestPlugin(BasePlugin):
    async def connect(self):
        pass

    async def available(self) -> bool:
        return False
