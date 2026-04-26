"""Hello World — the canonical EmptyOS scaffold.

Exists as a live reference for what a minimal app looks like:
- manifest.toml declaring a capability requirement and a web route
- class inheriting from BaseApp
- one CLI command, one web endpoint, one event emission
- a pages/index.html that talks to the endpoint and demonstrates EOS_UI

Useful when `/eos-new-app` spits out a stub — copy from here.
"""

from __future__ import annotations

from emptyos.sdk import BaseApp, cli_command, web_route


GREET_SYSTEM = (
    "You are a warm, concise greeter. Reply with ONE short sentence that welcomes "
    "the person by name to EmptyOS. No emoji. No preamble. Do not explain the system."
)


class HelloWorldApp(BaseApp):

    @cli_command("hello-world", help="Print a greeting")
    def _format_greeting(self, name: str) -> str:
        return f"Hello, {name}. Welcome to EmptyOS."

    async def cli_hello(self, name: str = "friend"):
        return self._format_greeting(name)

    @web_route("GET", "/api/ping")
    async def api_ping(self, request):
        return {"ok": True, "message": "pong"}

    @web_route("POST", "/api/greet")
    async def api_greet(self, request):
        import asyncio

        data = await request.json()
        name = (data.get("name") or "").strip() or "friend"
        use_think = bool(data.get("think", True))
        greeting = self._format_greeting(name)
        used_think = False
        if use_think:
            try:
                result = await asyncio.wait_for(
                    self.think(
                        f"{GREET_SYSTEM}\n\nGreet a user named {name}.",
                        domain="text",
                    ),
                    timeout=10,
                )
                result = (result or "").strip()
                if result:
                    greeting = result
                    used_think = True
            except Exception:
                pass
        await self.emit("hello-world:greeted", {"name": name, "used_think": used_think})
        return {"ok": True, "name": name, "greeting": greeting, "used_think": used_think}
