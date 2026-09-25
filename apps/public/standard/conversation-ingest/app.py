"""Conversation Ingest - visual control plane for the canonical ingest skill."""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, scheduled, web_route

from .store import (
    ImportPathError,
    aggregate_items,
    discover_imports,
    item_detail,
    list_items,
    parse_mechanism,
    resume_target,
)

DEFAULT_BACKLOG_THRESHOLD = 20

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
        # Thin alias; the BaseApp property is the canonical accessor.
        return self.repo_root

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
            # conversation-ingest-single-import-scope: no `import` -> the
            # merged "everything pending across all sources" view.
            return await asyncio.to_thread(
                aggregate_items,
                self._imports_root(),
                bucket=query.get("bucket") or "pending",
                query=query.get("q") or "",
                offset=query.get("offset") or 0,
                limit=query.get("limit") or 50,
            )
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

    # ── Proactive backlog nudge (conversation-ingest-no-backlog-nudge) ──────
    # Dark until `[apps.conversation-ingest] feature.backlog-nudge.enabled` —
    # a fresh install's daily sweep does nothing until opted in. Even once
    # enabled, `proactive_notify` itself ships dark (master policy toggle
    # defaults off) and applies its own quiet-hours/daily-cap/dedup gate, so
    # this is never more than a candidate nudge, never a guaranteed one.
    @scheduled("0 9 * * *", id="conversation-ingest-backlog-nudge")
    async def backlog_nudge_sweep(self):
        if not self.app_config("feature.backlog-nudge.enabled", False):
            return
        threshold = int(
            self.setting_or_config(
                "conversation-ingest.backlog_threshold", DEFAULT_BACKLOG_THRESHOLD
            )
            or DEFAULT_BACKLOG_THRESHOLD
        )
        result = await asyncio.to_thread(discover_imports, self._imports_root())
        if not result.get("root_ok"):
            return
        for row in result.get("imports") or []:
            pending = int(row.get("pending") or 0)
            if pending < threshold:
                continue
            key = str(row.get("key") or "")
            await self.proactive_notify(
                "conversation-ingest-backlog",
                f"{pending} conversations pending ingestion in '{key}'.",
                dedup_key=f"conversation-ingest-backlog:{key}",
                link={"text": "Open Conversation Ingest",
                      "href": f"/conversation-ingest/?import={key}"},
            )

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
