"""Shared undo actions-log — record reversible actions + resolve their inverse.

Two consumers (CLAUDE.md rule 9 — extract on the second): the rooms agent
``[DO:]`` path (auto-apply + pending apply) and the voice-assistant intent path
(apply + confirm-intent). Both need the same two things: "which method reverses
this one" and a JSONL trail the Undo button reads.

The inverse resolution is **registry-aware**. It reads the verb registry's
``inverse`` field first (``AppLoader.get_verbs()`` → a top-level ``inverse`` on
the ``[[provides.verbs]]`` entry, or ``assistant.inverse``), then falls back to
the legacy ``provides.assistant.commands`` list. This fixes a real regression:
apps migrated all-or-nothing to ``[[provides.verbs]]`` delete their legacy
``[provides.assistant]`` block, and the old rooms-only lookup read *only* that
legacy list — so a migrated app (e.g. expense) silently lost its undo.

Pure module: functions take the kernel (for the registry/manifest lookup) and a
concrete ``log_path``; no ``self``, no I/O beyond the named log file. ``undo_last``
takes an ``async call_app`` so it never imports an app instance.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


def lookup_inverse(kernel, app_id: str, method: str) -> str:
    """Return the declared inverse method for ``app_id.method``, or ``""``.

    Registry first (a verb whose canonical or per-surface method matches
    ``method``, reading ``raw['inverse']`` then ``assistant['inverse']``), then
    the legacy ``provides.assistant.commands`` fallback.
    """
    # ── Registry-aware ──────────────────────────────────────────────────
    try:
        registry = kernel.apps.get_verbs()
    except Exception:
        registry = None
    if registry is not None:
        for entry in registry:
            if entry.app_id != app_id:
                continue
            methods = {entry.method, entry.method_for("voice"), entry.method_for("assistant")}
            if method not in methods:
                continue
            inv = entry.raw.get("inverse") or (entry.assistant or {}).get("inverse")
            if inv:
                return str(inv)

    # ── Legacy provides.assistant.commands ──────────────────────────────
    try:
        manifest = kernel.apps.manifests.get(app_id)
    except Exception:
        manifest = None
    if manifest:
        for cmd in manifest.provides.get("assistant", {}).get("commands", []):
            if cmd.get("method") == method:
                return cmd.get("inverse", "") or ""
    return ""


def record_action(
    log_path: Path,
    *,
    app: str,
    method: str,
    args: dict[str, Any],
    result: Any,
    inverse: str,
) -> dict:
    """Append one action to the JSONL undo log and return the written entry.

    Best-effort — a write failure never raises (the action already ran). The
    entry shape matches the rooms undo log so both consumers share one reader.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "app": app,
        "method": method,
        "args": args,
        "result": str(result)[:500],
        "inverse": inverse,
        "reversed": False,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return entry


async def undo_last(
    log_path: Path,
    call_app: Callable[..., Awaitable[Any]],
) -> dict:
    """Undo the most recent reversible, not-yet-reversed action.

    Finds the last log entry with an ``inverse`` and ``reversed != True``, calls
    that inverse via ``call_app(app, inverse, **args)``, marks it reversed, and
    rewrites the log. Returns ``{ok, undid|message|error}``; the caller emits its
    own domain event with the returned ``undid`` payload.
    """
    if not log_path.exists():
        return {"ok": False, "message": "Nothing to undo."}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return {"ok": False, "error": f"read log failed: {e}"}

    target_idx = None
    target = None
    for i in range(len(lines) - 1, -1, -1):
        raw = lines[i].strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        if entry.get("inverse") and not entry.get("reversed"):
            target_idx = i
            target = entry
            break

    if not target:
        return {"ok": False, "message": "Nothing to undo."}

    try:
        result = await call_app(target["app"], target["inverse"], **target.get("args", {}))
    except Exception as e:
        return {"ok": False, "error": f"inverse failed: {e}"}

    target["reversed"] = True
    target["reversed_ts"] = datetime.now(timezone.utc).isoformat()
    lines[target_idx] = json.dumps(target, ensure_ascii=False)
    try:
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass

    return {
        "ok": True,
        "undid": {
            "app": target["app"],
            "method": target["method"],
            "args": target.get("args", {}),
            "inverse": target["inverse"],
        },
        "result": str(result)[:300],
    }
