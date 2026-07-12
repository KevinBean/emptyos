#!/usr/bin/env python3
"""Diagnostic hook — dump a hook event's real stdin payload to disk.

The Claude Code docs leave several events' payload FIELD NAMES undocumented or
inconsistent (`PostToolBatch` is rendered as both `tool_calls[]` and `tools`).
Rather than build a gate against a guessed schema, register this against the
event, trigger it once, and read the ground truth.

Writes to `data/hook-probe/<event>.json` (gitignored — `data/` is machine
state). Prints nothing, blocks nothing, always exits 0.

TEMPORARY — unregister from .claude/settings.json once the shape is captured.
"""

from __future__ import annotations

import json
import sys

from hook_common import project_root


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        payload = {"_probe_error": str(exc)}

    out_dir = project_root(payload) / "data" / "hook-probe"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        event = str(payload.get("hook_event_name") or "unknown")
        (out_dir / f"{event}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass  # a probe must never break a session
    return 0


if __name__ == "__main__":
    sys.exit(main())
