"""Providers — manage the live `think` capability chain.

Owns the user-editable list of LLM provider instantiations (provider type +
host + model + mode). On startup, registers enabled providers into the
`think` capability alongside whatever `emptyos.toml` already loaded. On
every CRUD mutation it emits `providers:changed`, then rebuilds the chain
in-place so apps see the new ordering without a restart.

Storage: data/apps/providers/providers.json (a list of provider rows).
On first boot, migrates from the legacy data/apps/model-bench/variants.json.

APIs:
- GET    /api/providers                 list all rows
- POST   /api/providers                 add or update a row by id
- DELETE /api/providers/{id}            remove a row
- POST   /api/providers/{id}/toggle     flip enabled flag
- GET    /api/providers/discover/ollama query a local Ollama for installed models
- GET    /api/providers/live            current live chain (post-merge view)
"""

from __future__ import annotations

import json
from pathlib import Path

import aiohttp

from emptyos.sdk import BaseApp, cli_command, on_event, web_route


VALID_PROVIDER_TYPES = ("openai_compat", "claude_cli")


def _provider_view(p) -> dict:
    """Compact JSON view of a live Provider for read-only UIs."""
    return {
        "name": p.name,
        "variant_id": p.variant_id,
        "model": getattr(p, "model", "") or "",
        "mode": getattr(p, "mode", "") or getattr(p, "effort", "") or "",
        "host": getattr(p, "host", "") or "",
        "is_cloud": getattr(p, "is_cloud", False),
    }


def _instantiate(row: dict):
    """Build a live Provider from a stored row. Returns None if invalid."""
    ptype = (row.get("provider_type") or "").lower()
    if ptype == "openai_compat":
        from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider
        return OpenAICompatThinkProvider(
            host=row.get("host") or "http://localhost:11434",
            model=row.get("model") or "",
            api_key_env=row.get("api_key_env") or "",
            provider_name=row.get("provider_name") or row.get("id") or "openai_compat",
            timeout=int(row.get("timeout", 0) or 0),
        )
    if ptype == "claude_cli":
        from emptyos.capabilities.providers.claude_cli import ClaudeCLIThinkProvider
        return ClaudeCLIThinkProvider(
            model=row.get("model") or "",
            max_tokens=int(row.get("max_tokens", 4096) or 4096),
            timeout=int(row.get("timeout", 0) or 0),
            effort=row.get("mode") or "low",
        )
    return None


class ProvidersApp(BaseApp):

    async def setup(self):
        await super().setup()
        self._migrate_from_model_bench()
        self._sync_chain()

    # --- storage ----------------------------------------------------------

    def _path(self) -> Path:
        return self.data_dir / "providers.json"

    def _load(self) -> list[dict]:
        p = self._path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self, rows: list[dict]):
        self._path().write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")

    def _defaults(self, row: dict) -> dict:
        """Fill in unspecified fields + compute a stable variant_id."""
        rid = (row.get("id") or "").strip()
        ptype = (row.get("provider_type") or "openai_compat").lower()
        model = (row.get("model") or "").strip()
        mode = (row.get("mode") or "").strip()
        host = (row.get("host") or "").strip()
        api_key_env = (row.get("api_key_env") or "").strip()
        provider_name = (row.get("provider_name") or rid or ptype).strip()
        parts = [provider_name]
        if model:
            parts.append(model)
        if mode:
            parts.append(mode)
        return {
            "id": rid,
            "provider_type": ptype,
            "provider_name": provider_name,
            "display_name": (row.get("display_name") or rid or ":".join(parts)).strip(),
            "host": host,
            "model": model,
            "mode": mode,
            "api_key_env": api_key_env,
            "enabled": bool(row.get("enabled", True)),
            "variant_id": ":".join(parts),
        }

    def _migrate_from_model_bench(self):
        """One-shot copy of legacy variants.json from model-bench."""
        if self._path().exists():
            return
        legacy = self.kernel.config.data_dir / "apps" / "model-bench" / "variants.json"
        if not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                self._save(data)
        except Exception:
            return

    # --- chain sync -------------------------------------------------------

    def _sync_chain(self):
        """Rebuild the think capability's provider chain.

        Drops every provider we previously injected (matched by variant_id
        of the current rows), then appends our enabled rows ahead of the
        human fallback. emptyos.toml-seeded providers are preserved.
        """
        try:
            think = self.kernel.capabilities.get("think")
        except Exception:
            return

        all_rows = self._load()
        our_variants = {r.get("variant_id") for r in all_rows if r.get("variant_id")}
        kept = []
        human_idx = None
        for p in think.providers:
            if p.variant_id in our_variants:
                continue
            if p.name == "human" and human_idx is None:
                human_idx = len(kept)
            kept.append(p)

        new = [p for p in (_instantiate(r) for r in all_rows if r.get("enabled")) if p is not None]
        if human_idx is not None:
            think.providers = kept[:human_idx] + new + kept[human_idx:]
        else:
            think.providers = kept + new

    @on_event("providers:changed")
    async def _on_changed(self, event):
        self._sync_chain()

    async def _emit_changed(self):
        await self.emit("providers:changed", {"count": len(self._load())})

    # --- CRUD endpoints ---------------------------------------------------

    @web_route("GET", "/api/providers")
    async def api_list(self, request):
        return {"providers": self._load()}

    @web_route("POST", "/api/providers")
    async def api_save(self, request):
        data = await request.json()
        row = self._defaults(data or {})
        if not row["id"]:
            return {"error": "id is required"}
        if row["provider_type"] not in VALID_PROVIDER_TYPES:
            return {"error": f"provider_type must be one of {VALID_PROVIDER_TYPES}"}
        if not row["model"] and row["provider_type"] == "openai_compat":
            return {"error": "model is required for openai_compat providers"}
        rows = self._load()
        updated = False
        for i, existing in enumerate(rows):
            if existing.get("id") == row["id"]:
                rows[i] = row
                updated = True
                break
        if not updated:
            rows.append(row)
        self._save(rows)
        await self._emit_changed()
        return {"ok": True, "provider": row, "created": not updated}

    @web_route("DELETE", "/api/providers/{rid}")
    async def api_delete(self, request):
        rid = request.path_params.get("rid", "")
        rows = [r for r in self._load() if r.get("id") != rid]
        self._save(rows)
        await self._emit_changed()
        return {"ok": True}

    @web_route("POST", "/api/providers/{rid}/toggle")
    async def api_toggle(self, request):
        rid = request.path_params.get("rid", "")
        rows = self._load()
        for r in rows:
            if r.get("id") == rid:
                r["enabled"] = not bool(r.get("enabled", True))
                self._save(rows)
                await self._emit_changed()
                return {"ok": True, "enabled": r["enabled"]}
        return {"error": f"no provider with id '{rid}'"}

    @web_route("GET", "/api/providers/discover/ollama")
    async def api_discover_ollama(self, request):
        host = request.query_params.get("host") or "http://localhost:11434"
        host = host.rstrip("/")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{host}/api/tags", timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return {"error": f"ollama returned HTTP {resp.status}"}
                    data = await resp.json()
        except Exception as e:
            return {"error": f"could not reach ollama at {host}: {e}"}
        models = []
        for m in (data.get("models") or []):
            name = m.get("name") or ""
            if not name:
                continue
            size = m.get("size") or 0
            models.append({
                "name": name,
                "size_bytes": size,
                "size_gb": round(size / (1024 ** 3), 2) if size else 0,
                "modified_at": m.get("modified_at", ""),
                "suggested_id": f"ollama-{name.replace(':', '-').replace('/', '-')}",
            })
        return {"host": host, "models": models}

    @web_route("GET", "/api/providers/seeded")
    async def api_seeded(self, request):
        """Read-only view of providers seeded by emptyos.toml at boot.

        These appear in the live think chain but are not stored in
        providers.json. Edit them by editing emptyos.toml + restarting.
        """
        cfg = self.kernel.config
        think_cfg = cfg.get_section("capabilities.think") or {}
        names = list(think_cfg.get("providers") or [])
        if not names:
            legacy = cfg.get_section("llm.providers") or {}
            names = list(legacy.keys())

        rows = []
        for name in names:
            section = cfg.get_section(f"capabilities.think.{name}") or cfg.get_section(f"llm.providers.{name}") or {}
            host = section.get("host", "")
            model = section.get("model", "")
            # Match the same inference rules as _build_think_provider
            if name == "claude" and section.get("method", "") != "api":
                provider_type = "claude_cli"
                provider_name = "claude"
                host = ""  # CLI, no host
            elif name == "ollama" or (host and "11434" in host):
                provider_type = "openai_compat"
                provider_name = "ollama"
                host = host or "http://localhost:11434"
                model = model or "llama3.1"
            elif name == "openai" or "openai.com" in host:
                provider_type = "openai_compat"
                provider_name = "openai"
                host = host or "https://api.openai.com"
                model = model or "gpt-5-mini"
            elif name == "claude" and section.get("method") == "api":
                provider_type = "openai_compat"
                provider_name = "claude"
                host = host or "https://api.anthropic.com"
                model = model or "claude-sonnet-4-20250514"
            else:
                provider_type = "openai_compat"
                provider_name = name

            parts = [provider_name]
            if model:
                parts.append(model)
            rows.append({
                "id": name,
                "source": "emptyos.toml",
                "provider_type": provider_type,
                "provider_name": provider_name,
                "display_name": name,
                "host": host,
                "model": model,
                "mode": section.get("effort", "") if name == "claude" else "",
                "api_key_env": section.get("api_key_env", ""),
                "enabled": True,
                "variant_id": ":".join(parts),
            })
        return {"providers": rows}

    @web_route("GET", "/api/providers/live")
    async def api_live(self, request):
        """Current live chain — what `think` will actually try, in order."""
        try:
            think = self.kernel.capabilities.get("think")
        except Exception:
            return {"providers": []}
        return {"providers": [_provider_view(p) for p in think.providers]}

    @web_route("GET", "/api/providers/capabilities")
    async def api_capabilities(self, request):
        """Read-only overview of every capability and its provider chain.

        Reflects the entire kernel — useful to see what the OS can actually
        do, including providers injected by plugins (voice-api, comfyui,
        obsidian-cli) that this app doesn't manage.
        """
        registry = self.kernel.capabilities
        out = []
        for name, cap in registry.list().items():
            chains = [{"label": "default", "providers": [_provider_view(p) for p in cap.providers]}]
            for domain_name, domain_providers in getattr(cap, "_domains", {}).items():
                chains.append({
                    "label": f"domain: {domain_name}",
                    "providers": [_provider_view(p) for p in domain_providers],
                })
            out.append({
                "capability": name,
                "chains": chains,
                "managed_here": name == "think",
            })
        return {"capabilities": out}

    # --- CLI --------------------------------------------------------------

    @cli_command("providers", help="List configured think providers")
    async def cmd_providers(self, action: str = "list"):
        if action == "list":
            rows = self._load()
            if not rows:
                print("No providers configured. Add one at http://localhost:9000/providers/")
                return
            for r in rows:
                flag = "ON " if r.get("enabled") else "off"
                print(f"  [{flag}] {r['id']:<24} {r.get('variant_id', '')}")
        elif action == "live":
            try:
                think = self.kernel.capabilities.get("think")
            except Exception:
                print("think capability not available")
                return
            for i, p in enumerate(think.providers):
                tag = " (cloud)" if getattr(p, "is_cloud", False) else ""
                print(f"  {i+1}. {p.variant_id}{tag}")
        else:
            print("Usage: eos providers [list|live]")
