"""Hello EmptyOS — the smallest installable example app.

Copy this folder as a starting point for your own app. The marketplace
validates the manifest, runs a py_compile gate over every *.py, shows you a
review card, and only on your confirm moves it into apps/<category>/<id>/.
No code runs until the next restart.

Conventions worth keeping:
- Use capabilities (self.read/think/write/emit), never raw tools.
- The web prefix in manifest.toml must equal the app id + folder name.
- Every POST implies a form; every list GET implies a UI (pages/index.html).
"""

from __future__ import annotations

from emptyos.sdk import BaseApp, web_route


class HelloEosApp(BaseApp):
    """One route + one event — proof that a marketplace install works."""

    @web_route("GET", "/api/hello")
    async def api_hello(self, request) -> dict:
        await self.emit("hello-eos:greeted", {"via": "marketplace"})
        return {"message": "Hello from a marketplace-installed app!"}
