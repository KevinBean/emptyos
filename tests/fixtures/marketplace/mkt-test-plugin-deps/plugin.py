"""Plugin fixture with an unsatisfiable plugin dependency. py_compile-clean."""

from emptyos.sdk import BasePlugin


class MktTestPluginDeps(BasePlugin):
    async def connect(self):
        pass
