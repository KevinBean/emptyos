"""Settings — system + app configuration.

Uses the kernel SettingsService for persistent key-value storage.
Shows system config (read-only from emptyos.toml) + editable settings.
"""

from __future__ import annotations

import re
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

from . import autopilot_panel as _autopilot_panel
from . import product as _product


def _write_toml_section(
    toml_path: Path, section: str, updates: dict[str, str]
) -> tuple[bool, str]:
    """Surgically update keys in one `[section]` of emptyos.toml, preserving comments.

    Every value is written as a quoted string; the caller must reject quotes and
    newlines in values. Atomic (tmp + replace). Returns (ok, error_message).

    Kept as a hand-rolled edit rather than a parse-and-dump because emptyos.toml is
    a file the *user* also hand-edits — round-tripping it through a TOML writer
    would silently eat their comments and reflow their layout.

    Generalized from `[network]` when the product wizard needed the same treatment
    for `[notes] path` (CLAUDE.md rule 9: extract on the second consumer).
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

    header_match = re.search(rf"(?m)^\[{re.escape(section)}\]\s*$", text)
    if header_match:
        section_start = header_match.end()
        next_header = re.search(r"(?m)^\[", text[section_start:])
        section_end = section_start + next_header.start() if next_header else len(text)
        body = text[section_start:section_end]
        for key, value in updates.items():
            body = _set_key_in(body, key, value)
        new_text = text[:section_start] + body + text[section_end:]
    else:
        trailer = f"\n\n[{section}]\n"
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


def _write_network_config(toml_path: Path, updates: dict[str, str]) -> tuple[bool, str]:
    """Back-compat shim — `[network]` is one section like any other now."""
    return _write_toml_section(toml_path, "network", updates)


# Mode → default host. Mirrors emptyos/kernel/config.py _MODE_DEFAULTS.
_MODE_HOSTS = {"local": "127.0.0.1", "private": "0.0.0.0", "public": "0.0.0.0"}


class SettingsApp(BaseApp):
    # ── Product surfaces (extracted to product.py) ──
    # Dark unless the launcher set EOS_PRODUCT, so a dev daemon never grows them.
    _product_enabled          = _product._product_enabled
    _bundle_root              = _product._bundle_root
    _updater                  = _product._updater
    _staged_version           = _product._staged_version
    _current_version          = _product._current_version
    product_info              = _product.product_info
    api_product               = _product.api_product
    api_product_vault         = _product.api_product_vault
    api_product_restart       = _product.api_product_restart
    api_product_update_status = _product.api_product_update_status
    api_product_update_apply  = _product.api_product_update_apply

    # Shared with product.py: the surgical, comment-preserving emptyos.toml editor.
    _write_toml_section = staticmethod(_write_toml_section)

    # ── Autopilot console (extracted to autopilot_panel.py) ──
    # The director's view of every standing AI-actor delegation in the system.
    api_autopilot_console     = _autopilot_panel.api_autopilot_console
    api_autopilot_grant       = _autopilot_panel.api_autopilot_grant
    api_autopilot_revoke      = _autopilot_panel.api_autopilot_revoke
    api_autopilot_hold_revoke = _autopilot_panel.api_autopilot_hold_revoke
    api_autopilot_budget      = _autopilot_panel.api_autopilot_budget

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

        # state_of() reconciles against the live instance registry, so an app
        # whose state label drifted from `instances` still counts as loaded.
        apps_loaded = sum(
            1
            for aid in k.apps.manifests
            if k.apps.state_of(aid) in (AppState.LOADED, AppState.STARTED)
        )

        info = {
            "os_name": k.config.get("os.name", "EmptyOS"),
            "vault_path": str(k.config.notes_path or "not configured"),
            "host": k.config.host,
            "port": k.config.port,
            "capabilities": caps,
            "plugins": [m.id for m in k.plugins.manifests.values()],
            "apps": {"total": len(k.apps.manifests), "loaded": apps_loaded},
        }
        # In user posture the browser user is not the operator: don't hand them
        # the host filesystem layout, bind address, or the plugin roster (the
        # same class of leak closed on /api/vault/read and gated on /api/plugins).
        # See docs/AUTH.md § Operator vs user.
        if not getattr(k.config, "web_is_operator", True):
            info["vault_path"] = "vault"
            info.pop("host", None)
            info.pop("port", None)
            info.pop("plugins", None)
        return info

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
        elif action == "set" and key and value and self._operator_key(key):
            print(f"  {key} is set by the operator in this build ([cloud] locked)")
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

    def _operator_key(self, key) -> bool:
        """A `think.*` key in a locked build ([cloud] locked): provider,
        model and routing choices belong to the operator there. Reads ignore
        them too (`routing_settings`); refusing the write says so plainly."""
        # The bare "think" key too: set() stores a dict under it as nested keys.
        return (isinstance(key, str) and (key == "think" or key.startswith("think."))
                and bool(getattr(getattr(self.kernel, "config", None), "cloud_locked", False)))

    @web_route("POST", "/api/set")
    async def api_set(self, request):
        data = await request.json()
        key = data.get("key", "")
        value = data.get("value")
        refusal = self._refuse_key(key)
        if refusal:
            return {"error": refusal}
        self._settings().set(key, value)
        await self.emit("settings:changed", {"key": key, "value": value})
        return {"key": key, "value": value}

    @web_route("POST", "/api/set-bulk")
    async def api_set_bulk(self, request):
        """Set multiple settings at once."""
        data = await request.json()
        refused = sorted(k for k in data if self._refuse_key(k))
        if refused:
            # All or nothing, so a form is never left half saved.
            return {"error": f"not settable in this edition: {', '.join(refused)}"}
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
        refusal = self._refuse_key(key)
        if refusal:
            return {"error": refusal}
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

    @web_route("POST", "/api/network", operator=True)
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
    @web_route("POST", "/api/restart-daemon", operator=True)
    async def api_restart_daemon(self, request):
        """Trigger restart.bat as a detached process. Confirm-gated.

        restart.bat does `taskkill /F /IM python.exe`, which is a whole-stack
        restart on purpose: ComfyUI, voice-api, pronounce and sandbox members
        come back with the daemon instead of being left in whatever state they
        were in. Reviewed and kept 2026-07-28.

        The reply still reports `owned_pid` — the PID this daemon actually is,
        recorded at boot by emptyos.sdk.daemon_pidfile — because "which process
        am I" is worth answering regardless of what this button chooses to kill.

        The spawned cmd.exe must outlive the kill: detached, breakaway-from-job,
        no inherited handles. The browser will lose its connection mid-response.
        """
        import os
        import subprocess

        from emptyos.sdk.daemon_pidfile import verify_owner

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

        owned_pid = verify_owner(self.kernel.config.data_dir, port=self.kernel.config.port)
        await self.emit(
            "system:restart_requested", {"source": "settings", "owned_pid": owned_pid}
        )
        return {
            "ok": True,
            "owned_pid": owned_pid,
            "message": "restart.bat spawned detached. Daemon will die in ~2s and reboot. "
            "Refresh the page in 15-20s.",
            "scope": (
                "Restarts the whole local stack, by design: restart.bat kills every "
                "python.exe, so ComfyUI, voice-api, pronounce and sandbox members "
                "come back with the daemon rather than being left in whatever state "
                "they were in. A targeted kill of this daemon alone is possible "
                f"({'PID ' + str(owned_pid) if owned_pid else 'PID unverified'}) but "
                "is deliberately not what this button does."
            ),
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
                    "options": [
                        "eos", "digital-garden", "soft-light", "tatami",
                        "void-dark", "warm-dark", "nord",
                        "forest", "deep-sea", "vino",
                    ],
                    "default": "eos",
                },
                {
                    "key": "system.language",
                    "label": "Language",
                    "type": "select",
                    "options": ["en", "zh-en", "zh"],
                    "default": "zh-en",
                },
                {
                    # Read by emptyos/web/server.py::home_target (settings → TOML
                    # [os] home → /portal/ when installed → /hub/).
                    "key": "os.home",
                    "label": "Home page — the route ⌂ Home opens (empty: Portal, or Hub when Portal is not installed)",
                    "type": "text",
                    "default": "",
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
                {
                    "key": "feature.projects-offline-writes.enabled",
                    "label": "Queue core writes while offline",
                    "type": "toggle",
                    "default": False,
                    "hint": "Dark by default. Queues JSON POSTs for task, journal, projects, and people in the browser, then replays them when connectivity returns.",
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

    def _has_cloud_voice(self) -> bool:
        """A cloud speak provider is registered and the speech guard, which
        the switch drives, sits on the speak chain."""
        from emptyos.capabilities.speech_guard import guard_of

        try:
            cap = self.kernel.capabilities.get("speak")
        except Exception:
            return False
        return guard_of(cap) is not None and any(
            getattr(p, "is_cloud", False) for p in cap.all_providers())

    @web_route("GET", "/api/schema")
    async def api_schema(self, request):
        """Collect settings schema from all apps + system defaults.

        Each app can declare settings in its manifest:
        [provides.settings]
        schema = [{key, label, type, default, options?}]

        The settings page auto-discovers all app settings.
        """
        return {"sections": self._settings_sections()}

    def _settings_sections(self) -> list[dict]:
        """The settings schema for THIS build — the single source of truth for
        both the schema endpoint and the user-posture write allowlist
        (`_settable_keys`). Drops LLM Routing when locked OR in user posture;
        app-declared and privacy keys are included.
        """
        _cfg = getattr(self.kernel, "config", None)
        sections = list(self.SYSTEM_SETTINGS)
        # Drop LLM Routing when the build is locked ([cloud] locked ignores saved
        # model/routing choices and refuses think.* writes) OR when the browser
        # user is not the operator. That section holds provider/model routing AND
        # operator test switches (`think.global_timeout`, `capability.simulate_offline`)
        # that on a SHARED user-posture daemon (the public demo) let one visitor
        # break AI for everyone — so they must not be user-writable even when the
        # build is not locked. Keying the drop on cloud_locked alone left that
        # DoS open on the demo (which is user posture, unlocked). See
        # docs/AUTH.md § Operator vs user.
        _drop_routing = (
            getattr(_cfg, "cloud_locked", False)
            or not getattr(_cfg, "web_is_operator", True)
        )
        if _drop_routing:
            sections = [s for s in sections if s["title"] != "LLM Routing"]
        # `os.home` decides where "/" lands for EVERY visitor of this daemon, so
        # on a shared user-posture daemon it is the operator's, like the routing
        # section above. server.py::home_target ignores the stored value there
        # too, so an earlier write cannot outlive the posture change.
        if not getattr(_cfg, "web_is_operator", True):
            sections = [
                {**s, "settings": [i for i in s["settings"] if i.get("key") != "os.home"]}
                for s in sections
            ]
        privacy: list[dict] = []

        # Only when this build keeps notes local by default ([cloud] note_apps):
        # the learner's switch for sending note text to the cloud AI service.
        if getattr(self.kernel, "note_scope", None) is not None:
            from emptyos.capabilities.note_scope import NOTES_SETTING, NOTES_SETTING_LABEL

            privacy.append({
                "key": NOTES_SETTING,
                "label": f"{NOTES_SETTING_LABEL} (note text is sent to an outside AI service)",
                "type": "toggle",
                "default": False,
            })
        # The kill switch for every cloud voice, shown when one is registered.
        if self._has_cloud_voice():
            from emptyos.capabilities.speech_guard import CLOUD_SETTING, CLOUD_SETTING_LABEL

            guard = getattr(self.kernel, "speech_guard", None)
            note = ("switched off for this device by the operator, whatever this is set to"
                    if guard is not None and guard.operator_off() else
                    "words to be read aloud are sent to an outside speech service; "
                    "off keeps speech on this device")
            privacy.append({
                "key": CLOUD_SETTING,
                "label": f"{CLOUD_SETTING_LABEL} ({note})",
                "type": "toggle",
                "default": True,
            })
        if privacy:
            sections.append({"title": "Privacy", "icon": "gear", "settings": privacy})

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

        return sections

    def _settable_keys(self) -> set[str]:
        """Keys a web user may write when the browser user is NOT the operator.

        The schema IS the allowlist: a key is user-settable only if some section
        of THIS build declares it. Config a user never sees a control for
        (`cloud.consent`, `cloud.llm_scan.*`, `trust.web`, `network.*`,
        `demo.*`, and — on a locked build — `think.*` / `capability.simulate_offline`)
        is therefore refused, because none of those are declared schema keys.
        """
        keys: set[str] = set()
        for section in self._settings_sections():
            for item in section.get("settings", []) or []:
                k = item.get("key") if isinstance(item, dict) else None
                if isinstance(k, str) and k:
                    keys.add(k)
        return keys

    def _refuse_key(self, key) -> str | None:
        """Why writing `key` is refused, or None when allowed.

        Two layers: the operator's think-lock (any build) and — in user posture
        — the schema allowlist above. Operator posture keeps today's behaviour
        (everything but a think.* write on a locked build).
        """
        if not isinstance(key, str) or not key:
            return "key required"
        if self._operator_key(key):
            return f"{key} is set by the operator in this build"
        if not getattr(getattr(self.kernel, "config", None), "web_is_operator", True):
            if key not in self._settable_keys():
                return f"{key} is not user-settable in this edition"
        return None
