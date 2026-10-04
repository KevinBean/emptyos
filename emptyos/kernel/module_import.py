"""Shared module import logic for app/plugin/engine loaders."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


def ensure_package(namespace: str, path: Path | None = None):
    """Register a virtual package in sys.modules if not already present."""
    if namespace not in sys.modules:
        pkg = types.ModuleType(namespace)
        pkg.__path__ = [str(path)] if path else []
        pkg.__package__ = namespace
        sys.modules[namespace] = pkg


def load_module(
    module_path: Path,
    namespace: str,
    manifest_path: Path,
    entry_class: str | None,
    kernel: Kernel,
    manifest: Any,
) -> Any:
    """Import a Python module from disk and optionally instantiate a class.

    Handles parent package registration so relative imports work.
    Used by app_loader, plugin_loader, and engine_loader.
    """
    module_name = f"{namespace}.{module_path.stem}"
    parent_pkg = namespace

    ensure_package(namespace.split(".")[0])
    ensure_package(parent_pkg, manifest_path)

    spec = importlib.util.spec_from_file_location(
        module_name, module_path, submodule_search_locations=[]
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = parent_pkg
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if entry_class:
        cls = getattr(module, entry_class)
        return cls(kernel=kernel, manifest=manifest)

    module._kernel = kernel
    module._manifest = manifest
    return module
