"""Runbook — run-state sidecar store (machine telemetry → data/, never vault).

Per-runbook block status + provenance + run history live here, NOT inline in
the vault note, so running a runbook never rewrites the human-editable source
or races a user editing it (CLAUDE.md storage rule + vault read-modify-write
race note).

Pure storage class — takes a data dir, does atomic JSON I/O. No kernel access.
"""

from __future__ import annotations

import json
from pathlib import Path

from emptyos.sdk import now_iso as _now_iso


class RunStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_root = Path(data_dir) / "cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def _path(self, runbook_id: str) -> Path:
        safe = runbook_id.replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}.json"

    def load(self, runbook_id: str) -> dict:
        p = self._path(runbook_id)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {"blocks": {}, "runs": []}

    def _save(self, runbook_id: str, data: dict) -> None:
        self._path(runbook_id).write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def block_state(self, runbook_id: str, block_id: str) -> dict:
        return self.load(runbook_id).get("blocks", {}).get(block_id, {})

    def set_block(self, runbook_id: str, block_id: str, **fields) -> None:
        data = self.load(runbook_id)
        blocks = data.setdefault("blocks", {})
        cur = blocks.setdefault(block_id, {})
        cur.update(fields)
        cur["ran_at"] = _now_iso()
        self._save(runbook_id, data)

    def append_run(self, runbook_id: str, record: dict) -> None:
        data = self.load(runbook_id)
        runs = data.setdefault("runs", [])
        runs.append({"ts": _now_iso(), **record})
        data["runs"] = runs[-50:]  # keep the last 50 runs
        self._save(runbook_id, data)

    # ── upstream output cache (for run_from rehydration) ──
    def _cache_path(self, runbook_id: str, block_id: str) -> Path:
        safe = runbook_id.replace("/", "_").replace("\\", "_")
        d = self.cache_root / safe
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{block_id}.json"

    def cache_output(self, runbook_id: str, block_id: str, value) -> None:
        try:
            self._cache_path(runbook_id, block_id).write_text(
                json.dumps({"value": value}, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 — caching is best-effort
            pass

    def cached_output(self, runbook_id: str, block_id: str):
        try:
            d = json.loads(self._cache_path(runbook_id, block_id).read_text(encoding="utf-8"))
            return d.get("value")
        except (FileNotFoundError, ValueError):
            return None
