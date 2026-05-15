"""Settings — system + app configuration.

Uses the kernel SettingsService for persistent key-value storage.
Shows system config (read-only from emptyos.toml) + editable settings.
"""

from __future__ import annotations

import re
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route


def _write_network_config(toml_path: Path, updates: dict[str, str]) -> tuple[bool, str]:
    """Surgically update `[network]` keys in emptyos.toml, preserving comments.

    `updates` maps key → value. All keys are written as quoted strings (caller's
    responsibility to reject quotes in values). Returns (ok, error_message).
    """
    if not updates:
        return True, ""
    try:
        text = toml_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"read error: {e}"

    def _set_key_in(section_text: str, key: str, value: str) -> str:
        pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=.*$")
        replacement = f'{key} = "{value}"'
        if pattern.search(section_text):
            return pattern.sub(replacement, section_text, count=1)
        if section_text and not section_text.endswith("\n"):
            section_text += "\n"
        return section_text + replacement + "\n"

    header_match = re.search(r"(?m)^\[network\]\s*$", text)
    if header_match:
        section_start = header_match.end()
        next_header = re.search(r"(?m)^\[", text[section_start:])
        section_end = section_start + next_header.start() if next_header else len(text)
        section = text[section_start:section_end]
        for key, value in updates.items():
            section = _set_key_in(section, key, value)
        new_text = text[:section_start] + section + text[section_end:]
    else:
        trailer = "\n\n[network]\n"
        for key, value in updates.items():
            trailer += f'{key} = "{value}"\n'
        new_text = text.rstrip() + trailer

    try:
        tmp = toml_path.with_suffix(toml_path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(toml_path)
    except Exception as e:
        return False, f"write error: {e}"
    return True, ""


# Mode → default host. Mirrors emptyos/kernel/config.py _MODE_DEFAULTS.
_MODE_HOSTS = {"local": "127.0.0.1", "private": "0.0.0.0", "public": "0.0.0.0"}


class SettingsApp(BaseApp):
    def _settings(self):
        return self.require("settings")

    async def get_system_info(self) -> dict:
        k = self.kernel
        caps = {}
        for name, cap in k.capabilities.list().items():
            providers = [p.name for p in cap.providers if p.name != "human"]
            domains = list(cap._domains.keys()) if hasattr(cap, "_domains") else []
            caps[name] = {"providers": providers, "domains": domains}

        from emptyos.kernel.app_loader import AppState

        apps_loaded = sum(
            1 for s in k.apps.states.values() if s in (AppState.LOADED, AppState.STARTED)
        )

        return {
            "os_name": k.config.get("os.name", "EmptyOS"),
            "vault_path": str(k.config.notes_path or "not configured"),
            "host": k.config.host,
            "port": k.config.port,
            "capabilities": caps,
            "plugins": [m.id for m in k.plugins.manifests.values()],
            "apps": {"total": len(k.apps.manifests), "loaded": apps_loaded},
        }

    @cli_command("settings", help="View and edit settings")
    async def cmd_settings(self, action: str = "show", key: str = "", value: str = ""):
        if action == "show":
            info = await self.get_system_info()
            print(f"\n  {info['os_name']}")
            print(f"  Vault: {info['vault_path']}")
            print(f"  Apps: {info['apps']['loaded']}/{info['apps']['total']}")
            all_settings = self._settings().all()
            if all_settings:
                print("\n  Settings:")
                for k, v in all_settings.items():
                    if isinstance(v, dict):
                        for sk, sv in v.items():
                            print(f"    {k}.{sk} = {sv}")
                    else:
                        print(f"    {k} = {v}")
            else:
                print("\n  No custom settings yet")
            print()
        elif action == "get" and key:
            print(f"  {key} = {self._settings().get(key)}")
        elif action == "set" and key and value:
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"
            elif value.isdigit():
                value = int(value)
            self._settings().set(key, value)
            print(f"  {key} = {value}")
        else:
            print("Usage: eos settings [show|get|set] [key] [value]")

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        return {"system": await self.get_system_info(), "settings": self._settings().all()}

    @web_route("GET", "/api/get")
    async def api_get(self, request):
        key = request.query_params.get("key", "")
        if not key:
            return self._settings().all()
        return {"key": key, "value": self._settings().get(key)}

    @web_route("POST", "/api/set")
    async def api_set(self, request):
        data = await request.json()
        key = data.get("key", "")
        value = data.get("value")
        if not key:
            return {"error": "key required"}
        self._settings().set(key, value)
        await self.emit("settings:changed", {"key": key, "value": value})
        return {"key": key, "value": value}

    @web_route("POST", "/api/set-bulk")
    async def api_set_bulk(self, request):
        """Set multiple settings at once."""
        data = await request.json()
        updated = []
        for key, value in data.items():
            self._settings().set(key, value)
            updated.append(key)
        await self.emit("settings:changed", {"keys": updated})
        return {"updated": updated}

    @web_route("POST", "/api/reset")
    async def api_reset(self, request):
        """Reset a setting to default (delete it)."""
        data = await request.json()
        key = data.get("key", "")
        if key:
            self._settings().set(key, None)
        return {"reset": key}

    # --- Network (writes to emptyos.toml, requires restart) ---
    @web_route("GET", "/api/network")
    async def api_network_get(self, request):
        c = self.kernel.config
        mode_default_host = _MODE_HOSTS.get(c.network_mode, "127.0.0.1")
        host_override = c.host != mode_default_host
        return {
            "mode": c.network_mode,
            "host": c.host,
            "mode_default_host": mode_default_host,
            "host_override": host_override,
            "port": c.port,
            "auth_required": c.auth_required,
            "auth_token_set": bool(c.auth_token),
            "password_set": bool(c.login_password),
            "is_remote_bind": c.is_remote_bind,
        }

    @web_route("POST", "/api/network")
    async def api_network_set(self, request):
        data = await request.json()
        mode = str(data.get("mode", "")).strip().lower()
        raw_token = data.get("auth_token")
        auth_token = str(raw_token).strip() if raw_token is not None else None

        if mode not in ("local", "private", "public"):
            return {"error": "mode must be local, private, or public"}
        if auth_token is not None and ('"' in auth_token or "\n" in auth_token):
            return {"error": "auth_token cannot contain quotes or newlines"}

        current_token = self.kernel.config.auth_token
        if mode == "public" and not auth_token and not current_token:
            return {"error": "public mode requires auth_token"}

        # Always write the host that matches the mode so mode + host stay in sync.
        # This clears any stale `host = "..."` override from prior configs.
        updates = {"mode": mode, "host": _MODE_HOSTS[mode]}
        if mode == "public" and auth_token:
            updates["auth_token"] = auth_token

        ok, err = _write_network_config(self.kernel.config.path, updates)
        if not ok:
            return {"error": err}

        await self.emit("settings:network_changed", {"mode": mode})
        return {"ok": True, "restart_required": True, "mode": mode, "host": _MODE_HOSTS[mode]}

    # --- Daemon restart (Windows: spawns restart.bat detached) ----------------
    # Lives here, not in any single app, because restart is system-wide. The
    # command palette has a "Restart Daemon" entry that POSTs here from any page.
    @web_route("POST", "/api/restart-daemon")
    async def api_restart_daemon(self, request):
        """Trigger restart.bat as a detached process. Confirm-gated.

        restart.bat does `taskkill /F /IM python.exe` — including this daemon. The
        spawned cmd.exe must outlive the kill: detached, breakaway-from-job, no
        inherited handles. The browser will lose its connection mid-response.
        """
        import os
        import subprocess

        data = await self.safe_json(request)
        if not data.get("confirm"):
            return {"error": "missing confirm: true — this kills the running daemon"}

        if os.name != "nt":
            return {"error": "restart-daemon is Windows-only (uses restart.bat)"}

        bat = self.repo_root / "restart.bat"
        if not bat.exists():
            return {"error": f"restart.bat not found at {bat}"}

        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_BREAKAWAY_FROM_JOB"):
            flags |= getattr(subprocess, name, 0)

        try:
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", "/min", str(bat)],
                cwd=str(self.repo_root),
                creationflags=flags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as e:
            return {"error": f"spawn failed: {e}"}

        await self.emit("system:restart_requested", {"source": "settings"})
        return {
            "ok": True,
            "message": "restart.bat spawned detached. Daemon will die in ~2s and reboot. "
            "Refresh the page in 15-20s.",
        }

    # User-facing preference sections (rendered on the "Settings" tab).
    # The "System" tab holds infrastructure (network mode, capabilities, plugins, vault).
    # Order here is the display order: identity → appearance → behavior → integrations.
    SYSTEM_SETTINGS = [
        {
            "title": "Profile",
            "icon": "user",
            "settings": [
                {"key": "user.name", "label": "Your Name", "type": "text", "default": ""},
                {
                    "key": "user.cv_path",
                    "label": "CV Path (in vault)",
                    "type": "text",
                    "default": "",
                },
            ],
        },
        {
            "title": "Appearance",
            "icon": "gear",
            "settings": [
                {
                    "key": "system.theme",
                    "label": "Theme",
                    "type": "select",
                    "options": ["eos", "void-dark", "warm-dark", "nord", "soft-light"],
                    "default": "eos",
                },
                {
                    "key": "system.language",
                    "label": "Language",
                    "type": "select",
                    "options": ["en", "zh-en", "zh"],
                    "default": "zh-en",
                },
            ],
        },
        {
            "title": "Location",
            "icon": "pin",
            "settings": [
                {"key": "location.latitude", "label": "Latitude", "type": "number", "default": 0},
                {"key": "location.longitude", "label": "Longitude", "type": "number", "default": 0},
                {"key": "location.timezone", "label": "Timezone", "type": "text", "default": "UTC"},
            ],
        },
        {
            "title": "LLM Routing",
            "icon": "brain",
            "settings": [
                {
                    "key": "think.default",
                    "label": "Default Provider",
                    "type": "select",
                    "options": ["claude-cli", "openai", "openai-mini", "openai-nano", "ollama"],
                    "default": "claude-cli",
                },
                {
                    "key": "think.openai.model",
                    "label": "OpenAI Full Model",
                    "type": "select",
                    "options": ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"],
                    "default": "gpt-5.4",
                    "hint": "Which model the `openai` provider uses (full tier). `openai-mini` and `openai-nano` are independent providers with their own models. Restart daemon to apply.",
                },
                {
                    "key": "think.domain.code",
                    "label": "Code Domain",
                    "type": "select",
                    "options": ["claude-cli", "openai", "openai-mini", "ollama"],
                    "default": "openai",
                },
                {
                    "key": "think.domain.text",
                    "label": "Text Domain",
                    "type": "select",
                    "options": ["claude-cli", "openai", "openai-mini", "openai-nano", "ollama"],
                    "default": "claude-cli",
                },
                {
                    "key": "think.domain.reason",
                    "label": "Reason Domain",
                    "type": "select",
                    "options": ["claude-cli", "openai", "openai-mini", "ollama"],
                    "default": "claude-cli",
                },
                {
                    "key": "think.global_timeout",
                    "label": "Provider Timeout (seconds)",
                    "type": "number",
                    "default": 30,
                },
                {
                    "key": "capability.simulate_offline",
                    "label": "Simulate Capability Offline",
                    "type": "select",
                    "options": ["", "think", "all"],
                    "default": "",
                    "hint": "Pretend this capability has no provider (raises immediately, bypassing real providers). Used to test graceful degradation — pages show the AI-offline banner and AI buttons degrade. Leave empty for normal operation.",
                },
            ],
        },
        {
            "title": "Notifications",
            "icon": "bell",
            "settings": [
                {
                    "key": "notify.enabled",
                    "label": "Notifications Enabled",
                    "type": "toggle",
                    "default": True,
                },
            ],
        },
        {
            "title": "Navigation",
            "icon": "compass",
            "settings": [
                {
                    "key": "layout.nav_apps",
                    "label": "Nav Bar Apps (JSON array)",
                    "type": "text",
                    "default": '[{"id":"task","prefix":"/task","name":"Tasks"},{"id":"expense","prefix":"/expense","name":"Expense"},{"id":"english","prefix":"/english","name":"English"},{"id":"journal","prefix":"/journal","name":"Journal"},{"id":"healing","prefix":"/healing","name":"Healing"},{"id":"search","prefix":"/search","name":"Search"}]',
                },
            ],
        },
        {
            "title": "Countdowns",
            "icon": "calendar",
            "settings": [
                {
                    "key": "countdown.items",
                    "label": "Countdown Items (JSON array)",
                    "type": "text",
                    "default": "[]",
                },
            ],
        },
        {
            "title": "Keyboard Shortcuts",
            "icon": "keyboard",
            "settings": [
                {
                    "key": "shortcuts.enabled",
                    "label": "Shortcuts Enabled",
                    "type": "toggle",
                    "default": True,
                },
            ],
        },
    ]

    @web_route("GET", "/api/shortcuts")
    async def api_shortcuts_page(self, request):
        """Shortcuts data for the settings page — includes current go-map and all shortcuts."""

        settings = self._settings()
        custom_map = settings.get("shortcuts.go_map")

        # Default go map
        default_map = {
            "h": "Home",
            "t": "Tasks",
            "j": "Journal",
            "e": "Expense",
            "s": "Search",
            "a": "Assistant",
            "b": "Briefing",
            "d": "Dashboard",
            "n": "Nutrition",
            "p": "Projects",
            "c": "Contacts",
            "i": "Items",
            "l": "Healing",
            "m": "Media",
            "r": "Reader",
            "k": "Tracker",
            "v": "Vault Analytics",
            "x": "English",
            "w": "Review",
            "q": "Quotes",
        }

        # Merge custom
        if isinstance(custom_map, dict):
            for key, val in custom_map.items():
                if isinstance(val, dict):
                    default_map[key] = val.get("label", key)
                else:
                    default_map[key] = str(val)

        global_shortcuts = [
            {"key": "Ctrl+K", "action": "Command Palette"},
            {"key": "Ctrl+/", "action": "Show Shortcuts Help"},
            {"key": "?", "action": "Show Shortcuts Help"},
            {"key": "/", "action": "Focus Search Input"},
            {"key": "Esc", "action": "Close Overlay"},
        ]

        return {
            "go_map": default_map,
            "global": global_shortcuts,
            "custom_overrides": custom_map or {},
        }

    @web_route("GET", "/api/schema")
    async def api_schema(self, request):
        """Collect settings schema from all apps + system defaults.

        Each app can declare settings in its manifest:
        [provides.settings]
        schema = [{key, label, type, default, options?}]

        The settings page auto-discovers all app settings.
        """
        sections = list(self.SYSTEM_SETTINGS)

        # Collect from all app manifests
        for app_id, manifest in self.kernel.apps.manifests.items():
            app_settings = manifest.provides.get("settings", {})
            schema = app_settings.get("schema", [])
            if schema:
                sections.append(
                    {
                        "title": manifest.name,
                        "icon": "app",
                        "app_id": app_id,
                        "settings": schema,
                    }
                )

        return {"sections": sections}
