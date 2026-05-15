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


# Always-on plugins — loaded regardless of the store's installed set.
# Without `health`, capability probes return unknown and `/system` shows
# everything as offline. Keep this list minimal — every essential is one
# the user can't recover from disabling without editing JSON by hand.
ESSENTIAL_PLUGINS: frozenset[str] = frozenset({"health"})


class PluginLoader:
    """Discovers and manages plugins."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self.manifests: dict[str, PluginManifest] = {}
        self.instances: dict[str, Any] = {}

    def essential_ids(self) -> frozenset[str]:
        """Plugin ids the store cannot disable/uninstall. Public surface."""
        return ESSENTIAL_PLUGINS

    def installed_ids(self) -> set[str]:
        """Plugin ids marked installed in `data/store/installed-plugins.json`.

        Pure read — see `AppLoader.installed_ids` for the rationale. Does
        not subtract disabled or union essentials. Demo mode bypasses.
        """
        from emptyos.runtime import store_state

        if self.kernel.config.demo_enabled:
            return set(self.manifests.keys())

        data_dir = self.kernel.config.data_dir
        store_state.seed_if_missing(
            data_dir,
            "plugins",
            ((m.id, m.version) for m in self.manifests.values()),
        )
        return store_state.installed_ids(data_dir, "plugins")

    def disabled_ids(self) -> set[str]:
        from emptyos.runtime import store_state

        if self.kernel.config.demo_enabled:
            return set()
        return store_state.disabled_ids(self.kernel.config.data_dir, "plugins") - ESSENTIAL_PLUGINS

    def enabled_ids(self) -> set[str]:
        """What the kernel should load. `installed - disabled ∪ essentials`."""
        if self.kernel.config.demo_enabled:
            return set(self.manifests.keys()) | ESSENTIAL_PLUGINS
        return (self.installed_ids() - self.disabled_ids()) | ESSENTIAL_PLUGINS

    def enabled_manifests(self) -> dict[str, PluginManifest]:
        enabled = self.enabled_ids()
        return {pid: m for pid, m in self.manifests.items() if pid in enabled}

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
        """Load enabled plugins, connect them, register as services.

        "Enabled" = installed AND not disabled (∪ essentials). Disabled and
        uninstalled plugins both stay in `self.manifests` for the catalog
        API but never load — no service registration, no provider injection.
        Restart-required toggling.
        """
        for plugin_id, manifest in self.enabled_manifests().items():
            try:
                module_file = manifest.path / f"{manifest.entry_module}.py"
                instance = load_module(
                    module_file,
                    f"eos_plugins.{plugin_id}",
                    manifest.path,
                    manifest.entry_class,
                    self.kernel,
                    manifest,
                )

                instance._config = self.kernel.config.get_section(f"plugins.{plugin_id}")
                await instance.connect()

                tags = manifest.provides.get("tags", [])
                for service_name in manifest.provides.get("services", []):
                    self.kernel.services.register(service_name, instance, tags=tags)

                self.instances[plugin_id] = instance
                self.kernel.syslog.info(
                    "plugin_loader",
                    f"Loaded plugin '{plugin_id}' -> services: {manifest.provides.get('services', [])}",
                )

            except Exception as e:
                self.kernel.syslog.error(
                    "plugin_loader", f"Failed to load plugin '{plugin_id}': {e}"
                )

    async def stop_all(self):
        """Disconnect all plugins."""
        for plugin_id, instance in self.instances.items():
            if hasattr(instance, "disconnect"):
                try:
                    await instance.disconnect()
                except Exception as e:
                    self.kernel.syslog.error(
                        "plugin_loader", f"Error disconnecting '{plugin_id}': {e}"
                    )
        self.instances.clear()
