"""App Generator — the system grows itself.

Generates apps from descriptions AND plugins from external service APIs.
Uses the system's self-knowledge (capabilities, plugins, apps) to produce
correct, working code.

Two generators in one app:
  - Apps: describe an idea → manifest.toml + app.py under apps/
  - Plugins: probe a service URL → manifest.toml + plugin.py under plugins/
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, slugify, web_route

from . import plugin_gen


APP_GEN_SYSTEM = """You generate EmptyOS apps — self-contained modules under apps/<id>/.

Shape of an app:
- Two files: manifest.toml + app.py
- App class inherits BaseApp from emptyos.sdk
- Access the OS via self.*:
  - await self.think(prompt, domain="text|code|reason") -> str
  - await self.read(path) -> str  (reads from vault)
  - await self.write(path, content) -> str
  - await self.search(query) -> list
  - await self.speak(text) -> str                (TTS)
  - await self.listen(audio) -> str              (STT)
  - await self.draw(prompt) -> str               (image gen)
  - self.think_stream(prompt) -> async generator
  - await self.call_app(app_id, method, **kw) -> any
  - await self.emit(event_type, data) -> None
  - self.data_dir -> Path                        (app-local storage)
  - self.load_state(default) / self.save_state(data)
  - self.require(service) / self.service(service)
- Decorators: @cli_command(name, help=...), @web_route(method, path), @on_event(type)
- Vault path: self.kernel.config.notes_path

What NOT to do:
- Do NOT use open(), Path.read_text(), requests, httpx, or subprocess — route through capabilities
- Do NOT hardcode vault paths; use self.vault_config(key) or self.vault_write(...)
- Do NOT invent capabilities or decorators that were not listed above
- Do NOT wrap output in markdown fences
- Do NOT include example usage, __main__ blocks, TODO comments, or narrative docstrings
- Do NOT add comments explaining WHAT the code does — only non-obvious WHY comments"""


APP_GEN_MANIFEST_TEMPLATE = """Current system state:
  Capabilities: {caps}
  Plugins: {plugins}
  Existing apps: {apps}

Generate manifest.toml for a new app.
  App ID: {app_id}
  Description: {description}

Use this skeleton, filling in realistic values:
```toml
[app]
id = "{app_id}"
name = "..."
version = "1.0.0"
description = "..."

[app.entry]
module = "app"
class = "..."

[requires]
capabilities = [...]
apps = []

[provides.cli]
commands = ["..."]

[provides.web]
prefix = "/{app_id}"

[provides.events]
emits = [...]
```

Return ONLY the TOML content."""


APP_GEN_CODE_TEMPLATE = """Current system state:
  Capabilities: {caps}
  Plugins: {plugins}
  Existing apps: {apps}

Generate app.py for this app.
  App ID: {app_id}
  Description: {description}

Manifest (already generated, match it):
{manifest}

Include at least one @cli_command and one @web_route that realise the description.
Use only the capabilities the manifest declares.

Return ONLY the Python code."""


class AppGenApp(BaseApp):

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._plugin_gen = plugin_gen.PluginGenMixin(self)

    def _apps_dir(self) -> Path:
        return Path(self.kernel.config.get("apps.path", "./apps"))

    def _system_snapshot(self) -> dict:
        return {
            "caps": list(self.kernel.capabilities.list().keys()),
            "plugins": [m.id for m in self.kernel.plugins.manifests.values()],
            "apps": [m.id for m in self.kernel.apps.manifests.values()],
        }

    async def generate(self, description: str, app_id: str = "") -> dict:
        """Generate a new app from a description."""
        if not app_id:
            app_id = slugify(description, max_len=30)

        app_dir = self._apps_dir() / app_id
        if app_dir.exists():
            return {"error": f"App '{app_id}' already exists at {app_dir}"}

        snap = self._system_snapshot()

        manifest_user = APP_GEN_MANIFEST_TEMPLATE.format(
            app_id=app_id, description=description, **snap,
        )
        manifest_content = await self.think(
            manifest_user, domain="code",
            system=APP_GEN_SYSTEM, temperature=0.2,
        )
        manifest_content = self._clean_code(manifest_content)

        code_user = APP_GEN_CODE_TEMPLATE.format(
            app_id=app_id, description=description, manifest=manifest_content, **snap,
        )
        code_content = await self.think(
            code_user, domain="code",
            system=APP_GEN_SYSTEM, temperature=0.2,
        )
        code_content = self._clean_code(code_content)

        # Write files
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "manifest.toml").write_text(manifest_content, encoding="utf-8")
        (app_dir / "app.py").write_text(code_content, encoding="utf-8")

        await self.emit("app-gen:created", {"id": app_id, "description": description})

        return {
            "id": app_id,
            "path": str(app_dir),
            "description": description,
            "manifest": manifest_content,
            "code_lines": len(code_content.split("\n")),
        }

    def _clean_code(self, text: str) -> str:
        """Remove markdown fences if LLM included them."""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        return text.strip()

    @cli_command("new-app", help="Generate a new app from description")
    async def cmd_new_app(self, description: str = "", id: str = ""):
        if not description:
            print("  Usage: eos new-app 'describe your app' [--id app-id]")
            return
        print(f"  Generating app: {description}")
        result = await self.generate(description, id)
        if result.get("error"):
            print(f"  Error: {result['error']}")
        else:
            print(f"  Created: {result['path']}")
            print(f"  Code: {result['code_lines']} lines")
            print(f"  Restart to load: eos start")

    @web_route("POST", "/api/generate")
    async def api_generate(self, request):
        data = await request.json()
        description = data.get("description", "")
        app_id = data.get("id", "")
        if not description:
            return {"error": "description required"}
        return await self.generate(description, app_id)

    # ── Plugin generator ──

    async def probe_service(self, url: str) -> dict:
        return await self._plugin_gen.probe(url)

    async def generate_plugin(self, url: str, name: str, probe_result: dict | None = None) -> dict:
        return await self._plugin_gen.generate(url, name, probe_result)

    @cli_command("new-plugin", help="Generate plugin from external service")
    async def cmd_new_plugin(self, url: str = "", name: str = ""):
        if not url:
            print("  Usage: eos new-plugin --url http://localhost:7700 --name fiction-engine")
            return

        if not name:
            name = url.split("//")[-1].split(":")[0].replace("localhost", "service")

        print(f"  Probing {url}...")
        probe = await self._plugin_gen.probe(url)
        print(f"  Reachable: {probe['reachable']}")
        print(f"  Endpoints: {len(probe['endpoints'])}")

        if not probe["reachable"]:
            print(f"  Error: service not reachable at {url}")
            return

        for ep in probe["endpoints"][:10]:
            print(f"    {ep['method']:6} {ep['path']}")

        print(f"\n  Generating plugin '{name}'...")
        result = await self._plugin_gen.generate(url, name, probe)

        if result.get("error"):
            print(f"  Error: {result['error']}")
        else:
            print(f"  Created: {result['path']}")
            print(f"  Files: {result['files']}")
            print(f"  Restart EmptyOS to load the new plugin.")

    @web_route("POST", "/api/plugin/probe")
    async def api_plugin_probe(self, request):
        data = await request.json()
        url = data.get("url", "")
        if not url:
            return {"error": "url required"}
        return await self._plugin_gen.probe(url)

    @web_route("POST", "/api/plugin/generate")
    async def api_plugin_generate(self, request):
        data = await request.json()
        url = data.get("url", "")
        name = data.get("name", "")
        if not url or not name:
            return {"error": "url and name required"}
        return await self._plugin_gen.generate(url, name)

    @web_route("GET", "/api/plugins")
    async def api_plugins(self, request):
        return self._plugin_gen.list_plugins()
