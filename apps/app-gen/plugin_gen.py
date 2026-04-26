"""Plugin generator — DNA intake mechanism.

Probes an external service, discovers its API, generates a plugin wrapper.
Migrated from standalone plugin-gen app.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from emptyos.sdk import slugify

if TYPE_CHECKING:
    from .app import AppGenApp


PLUGIN_GEN_SYSTEM = """You generate EmptyOS plugins — thin adapters that wire external services into the kernel.

An EmptyOS plugin is two files under plugins/<id>/:
  - manifest.toml: declares id, name, entry class, services it provides
  - plugin.py: a BasePlugin subclass implementing connect(), disconnect(), available()

Rules you must follow:
  - Use aiohttp for HTTP calls, never requests or urllib
  - Each discovered endpoint becomes a method on the plugin class
  - Register as service via self.config("host", <url>)
  - Class name is PascalCase + "Plugin" (e.g. FictionEnginePlugin)

What NOT to do:
  - Do NOT invent endpoints that were not discovered — only wrap what probe returned
  - Do NOT add authentication logic unless the probe surfaced it
  - Do NOT write markdown prose, comments explaining WHAT the code does, or TODOs
  - Do NOT wrap the output in markdown fences beyond the ones shown in the template
  - Do NOT add imports beyond aiohttp, emptyos.sdk, and stdlib
  - Do NOT include example usage, main blocks, or test scaffolding"""


PLUGIN_GEN_TEMPLATE = """Service: {name} at {url}
Health endpoint: {health}
Discovered endpoints:
{endpoints}

Generate TWO files for this plugin:

FILE 1: manifest.toml
```toml
[plugin]
id = "{plugin_id}"
name = "{name}"
version = "1.0.0"
description = "Connects to {name} at {url}"

[plugin.entry]
module = "plugin"
class = "{class_name}Plugin"

[provides]
services = ["{plugin_id}"]
tags = []
```

FILE 2: plugin.py
```python
from __future__ import annotations
import aiohttp
from emptyos.sdk import BasePlugin

class {class_name}Plugin(BasePlugin):
    name = "{plugin_id}"
    # implement connect, disconnect, available, and methods for each endpoint
```

Return ONLY the two file contents, clearly separated by "--- FILE 2 ---"."""


class PluginGenMixin:
    def __init__(self, app: "AppGenApp"):
        self.app = app

    def _plugins_dir(self) -> Path:
        return Path(self.app.kernel.config.get("plugins.path", "./plugins"))

    async def probe(self, url: str) -> dict:
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

            for docs_path in ["/openapi.json", "/docs", "/api/docs", "/swagger.json"]:
                try:
                    async with session.get(url + docs_path) as resp:
                        if resp.status == 200:
                            ct = resp.headers.get("content-type", "")
                            if "json" in ct:
                                data = await resp.json()
                                if "paths" in data or "openapi" in data:
                                    result["openapi"] = data
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
        if not probe_result:
            probe_result = await self.probe(url)

        if not probe_result["reachable"]:
            return {"error": f"Service not reachable at {url}"}

        plugin_id = slugify(name)
        class_name = name.replace(" ", "").replace("-", "")

        endpoints_desc = "\n".join(
            f"  {e['method']} {e['path']} — {e.get('summary', '')}"
            for e in probe_result["endpoints"]
        ) or "  (no endpoints discovered — generate basic connect/disconnect/available)"

        user_message = PLUGIN_GEN_TEMPLATE.format(
            name=name,
            url=url,
            health=probe_result.get("health", "/health"),
            endpoints=endpoints_desc,
            plugin_id=plugin_id,
            class_name=class_name,
        )

        raw = await self.app.think(
            user_message,
            domain="code",
            system=PLUGIN_GEN_SYSTEM,
            temperature=0.2,
        )

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
            toml_match = re.search(r"```toml\s*\n(.*?)```", raw, re.DOTALL)
            py_match = re.search(r"```python\s*\n(.*?)```", raw, re.DOTALL)
            if toml_match:
                manifest_content = toml_match.group(1)
            if py_match:
                plugin_content = py_match.group(1)

        for marker in ["```toml", "```python", "```", "FILE 1:", "FILE 2:", "manifest.toml", "plugin.py"]:
            manifest_content = manifest_content.replace(marker, "")
            plugin_content = plugin_content.replace(marker, "")
        manifest_content = manifest_content.strip()
        plugin_content = plugin_content.strip()

        if not manifest_content or not plugin_content:
            return {"error": "Failed to generate plugin code", "raw": raw[:500]}

        plugin_dir = self._plugins_dir() / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "manifest.toml").write_text(manifest_content, encoding="utf-8")
        (plugin_dir / "plugin.py").write_text(plugin_content, encoding="utf-8")

        await self.app.emit("plugin-gen:created", {"id": plugin_id, "url": url})

        return {
            "plugin_id": plugin_id,
            "path": str(plugin_dir),
            "url": url,
            "endpoints_discovered": len(probe_result["endpoints"]),
            "files": ["manifest.toml", "plugin.py"],
        }

    def list_plugins(self) -> list[dict]:
        return [
            {"id": m.id, "name": m.name, "services": m.provides.get("services", [])}
            for m in self.app.kernel.plugins.manifests.values()
        ]
