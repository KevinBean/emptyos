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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from emptyos.runtime.atomic_io import atomic_write_text
from emptyos.sdk.utils import is_past_ttl


def _registry_entries(kernel, app_id: str, method: str) -> list:
    """Verb-registry entries of ``app_id`` whose canonical or per-surface
    dispatch method is ``method``."""
    try:
        registry = kernel.apps.get_verbs()
    except Exception:
        return []
    if registry is None:
        return []
    out = []
    for entry in registry:
        if entry.app_id != app_id:
            continue
        if method in {entry.method, entry.method_for("voice"), entry.method_for("assistant")}:
            out.append(entry)
    return out


def verb_for_method(kernel, app_id: str, method: str) -> str:
    """The registry verb name (``task.add``) that ``app_id.method`` dispatches,
    or ``""`` — so a surface wrapper (``task.voice_add_task``) can be judged by
    the verb it implements rather than by its method name."""
    entries = _registry_entries(kernel, app_id, method)
    return entries[0].verb if entries else ""


_NO_RESULT = object()


def lookup_inverse(kernel, app_id: str, method: str, result: Any = _NO_RESULT) -> str:
    """Return the declared inverse method for ``app_id.method``, or ``""``.

    Registry first. A voice wrapper (a voice ``method`` distinct from the
    canonical one) answers only with its voice sub-table's ``inverse``: it
    returns a different shape from the canonical method, so the canonical
    inverse's arguments would not fit it. Then ``raw['inverse']``, then
    ``assistant['inverse']``; finally the legacy
    ``provides.assistant.commands`` fallback.

    A wrapper inverse reverses what the call reported doing, so it takes the
    ``undo_args`` the result carries. Pass the call's ``result`` after it ran
    and a result without them — a duplicate skipped, nothing added — answers
    ``""``: there is nothing to undo, and recording an inverse that could only
    fail would also block ``undo_last`` for every older action. Without
    ``result`` this answers the declaration (whether the verb *can* be undone).
    """
    # ── Registry-aware ──────────────────────────────────────────────────
    for entry in _registry_entries(kernel, app_id, method):
        voice_method = entry.method_for("voice")
        if method == voice_method and method != entry.method:
            inv = (entry.voice or {}).get("inverse") or ""
            if inv and result is not _NO_RESULT and not (
                    isinstance(result, dict) and isinstance(result.get("undo_args"), dict)):
                return ""
            return str(inv)
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

    A dict result may carry ``undo_args``: the exact arguments its inverse
    needs (the line that was written, the id that was created). They are kept
    verbatim, because ``result`` itself is stored truncated and stringified.
    ``id`` addresses this entry for :func:`undo_entry`.
    """
    entry = {
        "id": uuid.uuid4().hex[:16],
        "ts": datetime.now(timezone.utc).isoformat(),
        "app": app,
        "method": method,
        "args": args,
        "result": str(result)[:500],
        "inverse": inverse,
        "reversed": False,
    }
    undo_args = result.get("undo_args") if isinstance(result, dict) else None
    if isinstance(undo_args, dict):
        entry["undo_args"] = undo_args
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return entry


def _load(log_path: Path) -> list[str]:
    return log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []


def _same_entry(a: dict, b: dict) -> bool:
    # Entries written before ids existed are matched by what identified them.
    if a.get("id") or b.get("id"):
        return a.get("id") == b.get("id")
    return (a.get("ts"), a.get("app"), a.get("method")) == (b.get("ts"), b.get("app"), b.get("method"))


def _update_entry(log_path: Path, target: dict, changes: dict, drop: tuple = ()) -> None:
    """Re-read the log and merge ``changes`` into ``target``'s line.

    Synchronous on purpose: no ``await`` between the read and the write, so an
    action appended while an inverse was awaited is kept rather than
    overwritten by a stale copy of the file.
    """
    lines = _load(log_path)
    for i, raw in enumerate(lines):
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        if _same_entry(entry, target):
            entry.update(changes)
            for key in drop:
                entry.pop(key, None)
            lines[i] = json.dumps(entry, ensure_ascii=False)
            atomic_write_text(log_path, "\n".join(lines) + "\n")
            return


async def _reverse(log_path: Path, call_app, target: dict) -> dict:
    """Claim, run the inverse, then mark ``target`` reversed — or release the
    claim when the inverse fails, so the action stays undoable.

    The claim is released on any in-process failure, cancellation included.
    Two cases leave ``reversing`` set, so ``undo_entry`` answers
    ``in_progress`` from then on: the process dying mid-inverse (the outcome
    really is unknown — never auto-replayed), and a final-mark write that
    failed after the inverse ran, which is reported as ``ok`` with a
    ``warning`` because the undo did happen.
    """
    try:
        _update_entry(log_path, target, {"reversing": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        return {"ok": False, "error": f"couldn't record the undo: {e}"}
    args = target.get("undo_args") if isinstance(target.get("undo_args"), dict) else target.get("args", {})
    try:
        result = await call_app(target["app"], target["inverse"], **args)
    except BaseException as e:
        _release(log_path, target)
        if not isinstance(e, Exception):
            raise
        return {"ok": False, "error": f"inverse failed: {e}"}
    if isinstance(result, dict) and result.get("error"):
        _release(log_path, target)
        return {"ok": False, "error": str(result["error"])}
    warning = ""
    try:
        _update_entry(log_path, target,
                      {"reversed": True, "reversed_ts": datetime.now(timezone.utc).isoformat()},
                      drop=("reversing",))
    except Exception as e:
        warning = f"undone, but the log could not be updated: {e}"
    out = {
        "ok": True,
        "undid": {
            "id": target.get("id", ""),
            "app": target["app"],
            "method": target["method"],
            "args": target.get("args", {}),
            "inverse": target["inverse"],
        },
        "result": str(result)[:300],
    }
    if warning:
        out["warning"] = warning
    return out


def _release(log_path: Path, target: dict) -> None:
    try:
        _update_entry(log_path, target, {}, drop=("reversing",))
    except Exception:
        pass


def _entries(log_path: Path) -> list[dict]:
    out = []
    for raw in _load(log_path):
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


async def undo_last(
    log_path: Path,
    call_app: Callable[..., Awaitable[Any]],
) -> dict:
    """Undo the most recent reversible, not-yet-reversed action.

    Finds the last log entry with an ``inverse`` that is neither reversed nor
    mid-reversal, calls that inverse (with the entry's ``undo_args`` when it has
    them, else its original ``args``), and marks it reversed. Returns
    ``{ok, undid|message|error}``; the caller emits its own domain event with
    the returned ``undid`` payload.
    """
    try:
        entries = _entries(log_path)
    except Exception as e:
        return {"ok": False, "error": f"read log failed: {e}"}
    target = next((e for e in reversed(entries)
                   if e.get("inverse") and not e.get("reversed") and not e.get("reversing")), None)
    if not target:
        return {"ok": False, "message": "Nothing to undo."}
    return await _reverse(log_path, call_app, target)


async def undo_entry(
    log_path: Path,
    call_app: Callable[..., Awaitable[Any]],
    entry_id: str,
    *,
    max_age_s: float = 0,
) -> dict:
    """Undo one specific action by the ``id`` :func:`record_action` gave it.

    For an Undo button attached to one action: undoing "the last one" would
    reverse whatever happened most recently, which after a second action is
    the wrong one. Refuses, without calling anything, when the entry is gone,
    has no inverse, is already reversed (``already``), is being reversed right
    now (``in_progress`` — a double tap; the first tap reports the outcome),
    or is older than ``max_age_s`` (``expired``; 0 = no limit).
    """
    if not entry_id:
        return {"ok": False, "message": "Nothing to undo."}
    try:
        entries = _entries(log_path)
    except Exception as e:
        return {"ok": False, "error": f"read log failed: {e}"}
    target = next((e for e in entries if e.get("id") == entry_id), None)
    if not target:
        return {"ok": False, "message": "Nothing to undo."}
    if not target.get("inverse"):
        return {"ok": False, "message": "This action can't be undone."}
    if target.get("reversed"):
        return {"ok": False, "already": True, "message": "Already undone."}
    if target.get("reversing"):
        return {"ok": False, "in_progress": True, "message": "Undo already in progress."}
    if max_age_s and is_past_ttl(str(target.get("ts", "")), max_age_s):
        return {"ok": False, "expired": True, "message": "Too old to undo."}
    return await _reverse(log_path, call_app, target)
