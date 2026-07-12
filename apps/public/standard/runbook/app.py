"""Runbook — vault-canonical executable documents (typed block pipelines).

A runbook is ONE human-editable markdown note whose ordered ``eos-block`` fences
form a pipeline (vault_query → calculate → think → chart → write_draft → notify).
The note stays canonical in the vault; this app executes it using existing
capabilities + the review gate, and keeps run-state telemetry in ``data/``.

Spine only — parse/serialize + templating live in ``shared.py``, the safe
expression evaluator in ``calc.py``, run-state in ``runs.py``, per-type
executors in ``blocks.py``, orchestration in ``engine.py``, the HTTP/CLI surface
in ``routes.py``. See ``.claude/rules/multi-module-apps.md``.
"""

from __future__ import annotations

import logging

from emptyos.sdk import BaseApp

from . import engine as _engine
from . import routes as _routes

log = logging.getLogger("emptyos.runbook")


class RunbookApp(BaseApp):
    async def setup(self):
        await super().setup()
        try:
            n = self._register_schedules()
            if n:
                log.info("runbook: registered %d scheduled runbook(s)", n)
        except Exception as e:  # noqa: BLE001 — never block boot on schedule wiring
            log.warning("runbook: schedule registration failed: %s", e)

    # ── engine (engine.py) ──
    _run_store = _engine._run_store
    _runbook_rel = _engine._runbook_rel
    _load_blocks = _engine._load_blocks
    _producer_map = _engine._producer_map
    _build_context = _engine._build_context
    run_block = _engine.run_block
    run_all = _engine.run_all
    run_from = _engine.run_from
    block_statuses = _engine.block_statuses
    send_notification = _engine.send_notification
    runbook_timeline = _engine.runbook_timeline
    panel_runbook_status = _engine.panel_runbook_status

    # ── routes (routes.py) ──
    api_list = _routes.api_list
    api_get = _routes.api_get
    api_create = _routes.api_create
    api_run = _routes.api_run
    api_run_block = _routes.api_run_block
    api_schedule = _routes.api_schedule
    api_from_session = _routes.api_from_session
    api_from_confirm = _routes.api_from_confirm
    cli_list = _routes.cli_list
    cli_run = _routes.cli_run
    _draft_path = _routes._draft_path
    _register_schedules = _routes._register_schedules
