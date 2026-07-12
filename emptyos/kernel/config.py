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
        """Path to the notes directory (markdown vault). Always absolute so
        callers that build paths off it don't accidentally double-prefix when
        the value is later passed back through `FilesystemReadProvider` /
        `FilesystemWriteProvider` (whose `base_path` is also vault-rooted)."""
        p = self.get("notes.path")
        if not p:
            return None
        path = Path(p)
        return path if path.is_absolute() else path.resolve()

    # --- Network / deployment mode -------------------------------------------
    # Modes describe the trust level of the network EmptyOS is accessible on.
    # They are independent of demo mode (see demo_enabled).
    #
    #   "local"   — 127.0.0.1 only, no auth. Single machine.
    #   "private" — 0.0.0.0, auth_token REQUIRED by default. Tailscale / LAN /
    #               WireGuard — the network layer is the *outer* gate; the
    #               token is the *inner* gate. Override with
    #               `network.auth_required = false` if you genuinely want
    #               token-less LAN access (you're accepting full data exposure
    #               to anyone on the same subnet).
    #   "public"  — 0.0.0.0, auth_token REQUIRED. Internet-exposed / VPS.
    #
    # Raw network.host / network.auth_token still work as overrides for power users.

    _MODE_DEFAULTS = {
        "local": {"host": "127.0.0.1", "auth_required": False},
        # Private was historically auth-off; defaults flipped on 2026-04-27
        # following a security review — "private" implied trust we never
        # actually enforced. Set network.auth_required = false to opt back out.
        "private": {"host": "0.0.0.0", "auth_required": True},
        "public": {"host": "0.0.0.0", "auth_required": True},
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
    def login_password(self) -> str:
        """Human-typeable login password. Distinct from auth_token (the
        machine bearer credential). Either gates the daemon equally —
        password is for the browser login form, token is for CLI/API.
        See docs/AUTH.md for the design pin."""
        return (self.get("network.password", "") or "").strip()

    @property
    def auth_required(self) -> bool:
        """True when the current mode requires an auth token.

        Power-user override: `network.auth_required = false` in emptyos.toml
        forces auth off (e.g. you're behind your own reverse proxy that
        terminates auth). Default is mode-driven."""
        explicit = self.get("network.auth_required", None)
        if explicit is not None:
            return self._as_bool(explicit, default=True)
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

    @property
    def demo_seed_on_boot(self) -> bool:
        return self._as_bool(self.get("demo.seed_on_boot", False))

    # --- Cloud consent --------------------------------------------------------
    @property
    def cloud_consent(self) -> str:
        val = (self.get("cloud.consent", "ask") or "ask").lower().strip()
        return val if val in ("ask", "always", "never") else "ask"

    def __repr__(self) -> str:
        return f"Config({self.path})"
