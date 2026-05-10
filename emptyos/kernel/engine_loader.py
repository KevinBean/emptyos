"""Engine loader — discovers, loads, and manages engine lifecycle.

Engines are loaded AFTER plugins and BEFORE apps so that apps can
access engine services via self.engine("engine_id").
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
class EngineManifest:
    """Parsed manifest.toml for an engine."""

    id: str
    name: str
    version: str
    description: str
    path: Path
    entry_module: str = "engine"
    entry_class: str | None = None
    provides: dict[str, Any] = field(default_factory=dict)
    requires: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_toml(cls, manifest_path: Path) -> EngineManifest:
        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)
        engine_section = data.get("engine", {})
        entry = engine_section.get("entry", {})
        return cls(
            id=engine_section["id"],
            name=engine_section.get("name", engine_section["id"]),
            version=engine_section.get("version", "0.0.0"),
            description=engine_section.get("description", ""),
            path=manifest_path.parent,
            entry_module=entry.get("module", "engine"),
            entry_class=entry.get("class"),
            provides=data.get("provides", {}),
            requires=data.get("requires", {}),
            raw=data,
        )


class EngineLoader:
    """Discovers and manages engines."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self.manifests: dict[str, EngineManifest] = {}
        self.instances: dict[str, Any] = {}

    def discover(self) -> list[EngineManifest]:
        """Scan engines directory for manifest.toml files."""
        engines_path = Path(self.kernel.config.get("engines.path", "./engines"))
        if not engines_path.is_absolute():
            engines_path = Path(self.kernel.config.path).parent / engines_path
        engines_path = engines_path.resolve()

        self.manifests.clear()
        if not engines_path.exists():
            return []

        scan_dirs = [engines_path]
        personal_path = engines_path / "personal"
        if personal_path.exists():
            scan_dirs.append(personal_path)

        for scan_dir in scan_dirs:
            for manifest_file in sorted(scan_dir.glob("*/manifest.toml")):
                try:
                    manifest = EngineManifest.from_toml(manifest_file)
                    if manifest.id in self.manifests:
                        self.kernel.syslog.warn(
                            "engine_loader",
                            f"'{manifest.id}' in {scan_dir.name}/ overrides {self.manifests[manifest.id].path}",
                        )
                    self.manifests[manifest.id] = manifest
                except Exception as e:
                    self.kernel.syslog.error(
                        "engine_loader", f"Failed to parse {manifest_file}: {e}"
                    )

        return list(self.manifests.values())

    async def load_all(self):
        """Load all discovered engines, init them, and register as services."""
        for engine_id, manifest in self.manifests.items():
            try:
                module_file = manifest.path / f"{manifest.entry_module}.py"
                instance = load_module(
                    module_file,
                    f"eos_engines.{engine_id}",
                    manifest.path,
                    manifest.entry_class,
                    self.kernel,
                    manifest,
                )
                await instance.init()
                avail = await instance.available()

                tags = manifest.provides.get("tags", [])
                self.kernel.services.register(f"engine:{engine_id}", instance, tags=tags)

                self.instances[engine_id] = instance
                if avail:
                    self.kernel.syslog.info("engine_loader", f"Loaded engine '{engine_id}'")
                else:
                    self.kernel.syslog.warn(
                        "engine_loader",
                        f"Engine '{engine_id}' loaded but unavailable (missing deps)",
                    )

            except Exception as e:
                self.kernel.syslog.error(
                    "engine_loader", f"Failed to load engine '{engine_id}': {e}"
                )

    def get(self, engine_id: str) -> Any | None:
        """Get a loaded engine instance by ID."""
        return self.instances.get(engine_id)

    async def stop_all(self):
        """Shutdown all engines."""
        for engine_id, instance in self.instances.items():
            if hasattr(instance, "shutdown"):
                try:
                    await instance.shutdown()
                except Exception as e:
                    self.kernel.syslog.error(
                        "engine_loader", f"Error shutting down '{engine_id}': {e}"
                    )
        self.instances.clear()
