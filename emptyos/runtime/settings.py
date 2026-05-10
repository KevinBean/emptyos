"""Settings service — unified key-value store for system and app settings.

Stored in data/settings.json. No restart needed — changes take effect immediately.
Apps access via self.require("settings").get("app.key", default).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class SettingsService:
    """Persistent key-value settings store."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self._path = kernel.config.data_dir / "settings.json"
        self._data: dict[str, Any] = {}
        self._load()
        self._seed_personal_defaults()

    def _load(self):
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        else:
            self._data = {}

    def _set_in_memory(self, key: str, value: Any):
        """Set a value in memory without saving to disk."""
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def _seed_personal_defaults(self):
        """Seed settings from data/personal-defaults.json on first boot.

        This file is git-ignored (lives inside data/) and contains personal
        values like location, name, countdowns. Keys already set in settings
        are not overwritten, so this only fills in blanks.
        """
        defaults_path = self._path.parent / "personal-defaults.json"
        if not defaults_path.exists():
            return
        try:
            defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        except Exception:
            return
        seeded = False
        for key, value in defaults.items():
            if self.get(key) is None:
                self._set_in_memory(key, value)
                seeded = True
        if seeded:
            self._save()

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting by dot-path. e.g., 'system.theme' or 'expense.default_category'."""
        parts = key.split(".")
        node = self._data
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, key: str, value: Any):
        """Set a setting by dot-path. Saves immediately."""
        self._set_in_memory(key, value)
        self._save()

    def delete(self, key: str):
        """Delete a setting by dot-path."""
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return
        if isinstance(node, dict) and parts[-1] in node:
            del node[parts[-1]]
            self._save()

    def all(self) -> dict:
        """Return all settings."""
        return dict(self._data)

    def section(self, prefix: str) -> dict:
        """Get all settings under a prefix. e.g., section('expense') returns {'default_category': 'Other'}."""
        return self.get(prefix, {})
