"""Conversation Ingest - visual control plane for the canonical ingest skill."""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

from .store import (
    ImportPathError,
    discover_imports,
    item_detail,
    list_items,
    parse_mechanism,
    resume_target,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


class ConversationIngestApp(BaseApp):
    """Read-only dashboard over conversation ingestion telemetry and evidence."""

    def _imports_root(self) -> Path:
        override = self.setting_or_config(
            "conversation-ingest.import_root", "", config_key="import_root"
        )
        if override:
            return Path(str(override))
        return Path(self.kernel.config.data_dir) / "imports"

    def _repo_root(self) -> Path:
        return REPO_ROOT

    def _skill_root(self) -> Path:
        return self._repo_root() / ".agents" / "skills" / "eos-ai-conversation-ingest"

    @web_route("GET", "/api/overview")
    async def api_overview(self, request):
        return await asyncio.to_thread(discover_imports, self._imports_root())

    @web_route("GET", "/api/items")
    async def api_items(self, request):
        query = request.query_params
        import_key = (query.get("import") or "").strip()
        if not import_key:
            return {"error": "import is required"}
        try:
            return await asyncio.to_thread(
                list_items,
                self._imports_root(),
                import_key,
                bucket=query.get("bucket") or "pending",
                query=query.get("q") or "",
                offset=query.get("offset") or 0,
                limit=query.get("limit") or 50,
            )
        except (ImportPathError, ValueError) as exc:
            return {"error": str(exc)}

    @web_route("GET", "/api/items/{provider_id}")
    async def api_item(self, request):
        import_key = (request.query_params.get("import") or "").strip()
        provider_id = request.path_params["provider_id"]
        if not import_key:
            return {"error": "import is required"}
        try:
            detail = await asyncio.to_thread(
                item_detail, self._imports_root(), import_key, provider_id
            )
        except (ImportPathError, ValueError) as exc:
            return {"error": str(exc)}
        return detail or {"error": "conversation not found"}

    @web_route("GET", "/api/resume")
    async def api_resume(self, request):
        import_key = (request.query_params.get("import") or "").strip()
        if not import_key:
            return {"error": "import is required"}
        try:
            return await asyncio.to_thread(resume_target, self._imports_root(), import_key)
        except (ImportPathError, ValueError) as exc:
            return {"error": str(exc)}

    @web_route("GET", "/api/mechanism")
    async def api_mechanism(self, request):
        root = self._skill_root()
        skill_path = root / "SKILL.md"
        archive_path = root / "references" / "archive-contract.md"
        routing_path = root / "references" / "routing-and-audit.md"
        try:
            skill_text, routing_text = await asyncio.gather(
                self.read(str(skill_path)),
                self.read(str(routing_path)),
            )
        except Exception as exc:
            return {
                "error": f"canonical skill unavailable: {type(exc).__name__}: {exc}",
                "skill_path": str(skill_path).replace("\\", "/"),
            }
        parsed = parse_mechanism(skill_text, routing_text)
        return {
            **parsed,
            "skill_path": str(skill_path).replace("\\", "/"),
            "archive_contract_path": str(archive_path).replace("\\", "/"),
            "routing_contract_path": str(routing_path).replace("\\", "/"),
            "canonical_owner": "eos-ai-conversation-ingest",
        }

    @cli_command("status", help="Show conversation ingestion coverage")
    async def cli_status(self):
        result = await asyncio.to_thread(discover_imports, self._imports_root())
        if not result["root_ok"]:
            print(f"Conversation Ingest: {result['error']}")
            return
        active_key = result.get("active")
        active = next(
            (row for row in result["imports"] if row["key"] == active_key),
            None,
        )
        if not active:
            print("Conversation Ingest: no supported imports")
            return
        print(
            "Conversation Ingest: "
            f"{active['processed']}/{active['total']} processed, "
            f"{active['pending']} pending, "
            f"{active['needs_backfill']} backfill"
        )
