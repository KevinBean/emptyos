"""Plugin Generator — DNA intake mechanism.

Probe an external service, discover its API, and generate a plugin wrapper.
The system absorbs new capabilities by creating plugins that connect to
external services.

Usage:
    eos new-plugin --url http://localhost:7700 --name fiction-engine
    eos new-plugin --url http://localhost:8600 --name talkbuddy
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import aiohttp

from emptyos.sdk import BaseApp, cli_command, slugify, web_route


class PluginGenApp(BaseApp):

    def _plugins_dir(self) -> Path:
        return Path(self.kernel.config.get("plugins.path", "./plugins"))

    async def probe(self, url: str) -> dict:
        """Probe an external service to discover its API."""
        url = url.rstrip("/")
        result = {
            "url": url,
            "reachable": False,
            "endpoints": [],
            "health": None,
            "openapi": None,
            "docs": None,
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            # 1. Health check
            for health_path in ["/health", "/api/health", "/v1/health", "/status", "/"]:
                try:
                    async with session.get(url + health_path) as resp:
                        if resp.status == 200:
                            result["reachable"] = True
                            result["health"] = health_path
                            try:
                                result["health_data"] = await resp.json()
                            except Exception:
                                result["health_data"] = {"status": "ok"}
                            break
                except Exception:
                    continue

            if not result["reachable"]:
                return result

            # 2. OpenAPI/Swagger discovery
            for docs_path in ["/openapi.json", "/docs", "/api/docs", "/swagger.json"]:
                try:
                    async with session.get(url + docs_path) as resp:
                        if resp.status == 200:
                            ct = resp.headers.get("content-type", "")
                            if "json" in ct:
                                data = await resp.json()
                                if "paths" in data or "openapi" in data:
                                    result["openapi"] = data
                                    # Extract endpoints from OpenAPI
                                    for path, methods in data.get("paths", {}).items():
                                        for method, details in methods.items():
                                            if method.upper() in ("GET", "POST", "PUT", "DELETE"):
                                                result["endpoints"].append({
                                                    "method": method.upper(),
                                                    "path": path,
                                                    "summary": details.get("summary", ""),
                                                })
                            result["docs"] = docs_path
                            break
                except Exception:
                    continue

            # 3. Common endpoint probing (if no OpenAPI found)
            if not result["endpoints"]:
                for probe_path in [
                    "/api/status", "/api/models", "/api/voices", "/api/health",
                    "/v1/models", "/v1/voices", "/v1/status",
                    "/api/list", "/api/generate", "/api/chat",
                ]:
                    try:
                        async with session.get(url + probe_path) as resp:
                            if resp.status == 200:
                                result["endpoints"].append({
                                    "method": "GET",
                                    "path": probe_path,
                                    "summary": "discovered",
                                })
                    except Exception:
                        continue

        return result

    async def generate(self, url: str, name: str, probe_result: dict | None = None) -> dict:
        """Generate a plugin from probe results."""
        if not probe_result:
            probe_result = await self.probe(url)

        if not probe_result["reachable"]:
            return {"error": f"Service not reachable at {url}"}

        plugin_id = slugify(name)

        # Use LLM to generate plugin code from probe results
        endpoints_desc = "\n".join(
            f"  {e['method']} {e['path']} — {e.get('summary', '')}"
            for e in probe_result["endpoints"]
        ) or "  (no endpoints discovered — generate basic connect/disconnect/available)"

        prompt = f"""Generate an EmptyOS plugin for an external service.

Service: {name} at {url}
Health endpoint: {probe_result.get('health', '/health')}
Discovered endpoints:
{endpoints_desc}

EmptyOS plugin rules:
- File: plugins/{plugin_id}/plugin.py
- Class inherits BasePlugin from emptyos.sdk
- Must implement: connect(), disconnect(), available()
- Use aiohttp for HTTP calls
- Register as service via self.config("host", "{url}")
- Each discovered endpoint becomes a method on the plugin

Generate TWO files:

FILE 1: manifest.toml
```toml
[plugin]
id = "{plugin_id}"
name = "{name}"
version = "1.0.0"
description = "Connects to {name} at {url}"

[plugin.entry]
module = "plugin"
class = "{name.replace(' ', '').replace('-', '')}Plugin"

[provides]
services = ["{plugin_id}"]
tags = []
```

FILE 2: plugin.py
```python
from __future__ import annotations
import aiohttp
from emptyos.sdk import BasePlugin

class {name.replace(' ', '').replace('-', '')}Plugin(BasePlugin):
    name = "{plugin_id}"
    # ... implement connect, disconnect, available, and methods for each endpoint
```

Return ONLY the two file contents, clearly separated by "--- FILE 2 ---"."""

        raw = await self.think(prompt, domain="code")

        # Parse output into two files
        manifest_content = ""
        plugin_content = ""

        if "--- FILE 2 ---" in raw:
            parts = raw.split("--- FILE 2 ---")
            manifest_content = parts[0]
            plugin_content = parts[1]
        elif "FILE 2:" in raw:
            parts = raw.split("FILE 2:")
            manifest_content = parts[0]
            plugin_content = parts[1]
        else:
            # Try to split on ```toml and ```python blocks
            toml_match = re.search(r"```toml\s*\n(.*?)```", raw, re.DOTALL)
            py_match = re.search(r"```python\s*\n(.*?)```", raw, re.DOTALL)
            if toml_match:
                manifest_content = toml_match.group(1)
            if py_match:
                plugin_content = py_match.group(1)

        # Clean up
        for marker in ["```toml", "```python", "```", "FILE 1:", "FILE 2:", "manifest.toml", "plugin.py"]:
            manifest_content = manifest_content.replace(marker, "")
            plugin_content = plugin_content.replace(marker, "")
        manifest_content = manifest_content.strip()
        plugin_content = plugin_content.strip()

        if not manifest_content or not plugin_content:
            return {"error": "Failed to generate plugin code", "raw": raw[:500]}

        # Write files
        plugin_dir = self._plugins_dir() / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "manifest.toml").write_text(manifest_content, encoding="utf-8")
        (plugin_dir / "plugin.py").write_text(plugin_content, encoding="utf-8")

        await self.emit("plugin-gen:created", {"id": plugin_id, "url": url})

        return {
            "plugin_id": plugin_id,
            "path": str(plugin_dir),
            "url": url,
            "endpoints_discovered": len(probe_result["endpoints"]),
            "files": ["manifest.toml", "plugin.py"],
        }

    # --- CLI ---

    @cli_command("new-plugin", help="Generate plugin from external service")
    async def cmd_new_plugin(self, url: str = "", name: str = ""):
        if not url:
            print("  Usage: eos new-plugin --url http://localhost:7700 --name fiction-engine")
            return

        if not name:
            # Derive name from URL
            name = url.split("//")[-1].split(":")[0].replace("localhost", "service")

        print(f"  Probing {url}...")
        probe = await self.probe(url)
        print(f"  Reachable: {probe['reachable']}")
        print(f"  Endpoints: {len(probe['endpoints'])}")

        if not probe["reachable"]:
            print(f"  Error: service not reachable at {url}")
            return

        for ep in probe["endpoints"][:10]:
            print(f"    {ep['method']:6} {ep['path']}")

        print(f"\n  Generating plugin '{name}'...")
        result = await self.generate(url, name, probe)

        if result.get("error"):
            print(f"  Error: {result['error']}")
        else:
            print(f"  Created: {result['path']}")
            print(f"  Files: {result['files']}")
            print(f"  Restart EmptyOS to load the new plugin.")

    # --- Web API ---

    @web_route("POST", "/api/probe")
    async def api_probe(self, request):
        data = await request.json()
        url = data.get("url", "")
        if not url:
            return {"error": "url required"}
        return await self.probe(url)

    @web_route("POST", "/api/generate")
    async def api_generate(self, request):
        data = await request.json()
        url = data.get("url", "")
        name = data.get("name", "")
        if not url or not name:
            return {"error": "url and name required"}
        return await self.generate(url, name)

    @web_route("GET", "/api/plugins")
    async def api_plugins(self, request):
        """List existing plugins."""
        return [
            {"id": m.id, "name": m.name, "services": m.provides.get("services", [])}
            for m in self.kernel.plugins.manifests.values()
        ]
