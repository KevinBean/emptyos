"""Config loader — reads emptyos.toml with env var overrides."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


class Config:
    """TOML-based config with dot-path access and env override."""

    def __init__(self, path: str = "emptyos.toml"):
        self.path = Path(path)
        try:
            with open(self.path, "rb") as f:
                self._data = tomllib.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Config not found: {self.path}\n"
                f"Run 'eos init' or copy emptyos.example.toml to emptyos.toml."
            ) from None

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dot-path. e.g. config.get('llm.default_provider')

        Checks EOS_SECTION_KEY env var first (e.g. EOS_LLM_DEFAULT_PROVIDER).
        """
        # Check env override
        env_key = "EOS_" + key.replace(".", "_").upper()
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val

        # Walk the nested dict
        parts = key.split(".")
        node = self._data
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def get_section(self, key: str) -> dict:
        """Get a config section as a dict."""
        val = self.get(key, {})
        return val if isinstance(val, dict) else {}

    @property
    def data_dir(self) -> Path:
        return Path(self.get("os.data_dir", "./data"))

    @property
    def notes_path(self) -> Path | None:
        """Path to the notes directory (markdown vault)."""
        p = self.get("notes.path")
        return Path(p) if p else None

    # --- Network / deployment mode -------------------------------------------
    # Modes describe the trust level of the network EmptyOS is accessible on.
    # They are independent of demo mode (see demo_enabled).
    #
    #   "local"   — 127.0.0.1 only, no auth. Single machine.
    #   "private" — 0.0.0.0, no auth. Tailscale / LAN / WireGuard — you trust
    #               the network layer to gate access.
    #   "public"  — 0.0.0.0, auth_token REQUIRED. Internet-exposed / VPS.
    #
    # Raw network.host / network.auth_token still work as overrides for power users.

    _MODE_DEFAULTS = {
        "local":   {"host": "127.0.0.1", "auth_required": False},
        "private": {"host": "0.0.0.0",   "auth_required": False},
        "public":  {"host": "0.0.0.0",   "auth_required": True},
    }

    @property
    def network_mode(self) -> str:
        mode = (self.get("network.mode", "local") or "local").lower().strip()
        return mode if mode in self._MODE_DEFAULTS else "local"

    @property
    def host(self) -> str:
        explicit = self.get("network.host", None)
        if explicit:
            return str(explicit)
        return self._MODE_DEFAULTS[self.network_mode]["host"]

    @property
    def port(self) -> int:
        return int(self.get("network.port", 9000))

    @property
    def auth_token(self) -> str:
        return (self.get("network.auth_token", "") or "").strip()

    @property
    def auth_required(self) -> bool:
        """True when the current mode requires an auth token."""
        return bool(self._MODE_DEFAULTS[self.network_mode]["auth_required"])

    @property
    def is_remote_bind(self) -> bool:
        """True when host is not loopback — i.e. accessible beyond the machine."""
        h = self.host.strip()
        return h not in ("127.0.0.1", "localhost", "::1", "")

    # --- Demo mode (orthogonal to network mode) -------------------------------
    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1", "yes", "on"):
                return True
            if v in ("false", "0", "no", "off", ""):
                return False
        return default

    @property
    def demo_enabled(self) -> bool:
        return self._as_bool(self.get("demo.enabled", False))

    @property
    def demo_reset_on_restart(self) -> bool:
        return self._as_bool(self.get("demo.reset_on_restart", False))

    # --- Cloud consent --------------------------------------------------------
    @property
    def cloud_consent(self) -> str:
        val = (self.get("cloud.consent", "ask") or "ask").lower().strip()
        return val if val in ("ask", "always", "never") else "ask"

    def __repr__(self) -> str:
        return f"Config({self.path})"
