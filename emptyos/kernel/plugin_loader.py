"""Plugin loader — discovers, loads, and manages plugin lifecycle.

Plugins are loaded BEFORE apps so that apps can require() plugin services.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from emptyos.kernel.module_import import load_module

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


@dataclass
class PluginManifest:
    """Parsed manifest.toml for a plugin."""

    id: str
    name: str
    version: str
    description: str
    path: Path
    entry_module: str = "plugin"
    entry_class: str | None = None
    provides: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_toml(cls, manifest_path: Path) -> PluginManifest:
        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)
        plugin_section = data.get("plugin", {})
        entry = plugin_section.get("entry", {})
        return cls(
            id=plugin_section["id"],
            name=plugin_section.get("name", plugin_section["id"]),
            version=plugin_section.get("version", "0.0.0"),
            description=plugin_section.get("description", ""),
            path=manifest_path.parent,
            entry_module=entry.get("module", "plugin"),
            entry_class=entry.get("class"),
            provides=data.get("provides", {}),
            raw=data,
        )


class PluginLoader:
    """Discovers and manages plugins."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self.manifests: dict[str, PluginManifest] = {}
        self.instances: dict[str, Any] = {}

    def discover(self) -> list[PluginManifest]:
        """Scan plugins directory for manifest.toml files."""
        plugins_path = Path(self.kernel.config.get("plugins.path", "./plugins"))
        if not plugins_path.is_absolute():
            plugins_path = Path(self.kernel.config.path).parent / plugins_path
        plugins_path = plugins_path.resolve()

        self.manifests.clear()
        if not plugins_path.exists():
            return []

        for manifest_file in sorted(plugins_path.glob("*/manifest.toml")):
            try:
                manifest = PluginManifest.from_toml(manifest_file)
                self.manifests[manifest.id] = manifest
            except Exception as e:
                self.kernel.syslog.error("plugin_loader", f"Failed to parse {manifest_file}: {e}")

        return list(self.manifests.values())

    async def load_all(self):
        """Load all discovered plugins, connect them, and register as services."""
        for plugin_id, manifest in self.manifests.items():
            try:
                module_file = manifest.path / f"{manifest.entry_module}.py"
                instance = load_module(
                    module_file, f"eos_plugins.{plugin_id}",
                    manifest.path, manifest.entry_class, self.kernel, manifest,
                )

                instance._config = self.kernel.config.get_section(f"plugins.{plugin_id}")
                await instance.connect()

                tags = manifest.provides.get("tags", [])
                for service_name in manifest.provides.get("services", []):
                    self.kernel.services.register(service_name, instance, tags=tags)

                self.instances[plugin_id] = instance
                self.kernel.syslog.info("plugin_loader", f"Loaded plugin '{plugin_id}' -> services: {manifest.provides.get('services', [])}")

            except Exception as e:
                self.kernel.syslog.error("plugin_loader", f"Failed to load plugin '{plugin_id}': {e}")

    async def stop_all(self):
        """Disconnect all plugins."""
        for plugin_id, instance in self.instances.items():
            if hasattr(instance, "disconnect"):
                try:
                    await instance.disconnect()
                except Exception as e:
                    self.kernel.syslog.error("plugin_loader", f"Error disconnecting '{plugin_id}': {e}")
        self.instances.clear()
