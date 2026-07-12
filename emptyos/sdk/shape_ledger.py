"""Prediction ledger for the Tier-1 shape-validation gate.

One JSONL row per generate→validate→verdict, appended under
``data/shape_validation/ledger.jsonl``. This is the first concrete consumer of
the "grade your own prediction" eval loop — the gap the world-model / EverOS
borrow pointed at: the 3D pipeline acts on a model of plausible geometry, and
this records whether each generation's prediction (valid) held, so model
fidelity can be measured over time and `model-ability.md`'s strong-model gate
can later be relaxed empirically.

Telemetry → ``data/`` (CLAUDE.md storage split), never the vault. Best-effort:
a logging failure must never affect the caller's response. Pure stdlib + file
I/O — no kernel import.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def log_shape_validation(
    repo_root,
    source_app: str,
    entity: str,
    *,
    ok: bool,
    hard_codes: list[str] | None = None,
    soft_codes: list[str] | None = None,
) -> None:
    """Append one normalized verdict row. Callers adapt their own report shape
    (ValidationReport, CompileReport, …) into ok + code lists."""
    try:
        hard = list(hard_codes or [])
        soft = list(soft_codes or [])
        led_dir = Path(repo_root) / "data" / "shape_validation"
        led_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source_app": source_app,
            "entity": entity,
            "actual_valid": bool(ok),
            "hard": len(hard),
            "soft": len(soft),
            "codes": hard + soft,
        }
        with (led_dir / "ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass
