"""Store state — per-user install list for apps + plugins.

State lives in `data/store/installed-{apps,plugins}.json`. Skills are tracked
by folder location (`.claude/skills/eos-<id>/` vs `.claude/skills/_archive/eos-<id>/`),
not by this module — see `apps/store/app.py` for the skill side.

The store is restart-required (community-plugin install model): install/uninstall
flips state, the running daemon doesn't reload code, the next `restart.bat`
picks up the new set.

First-boot seed: if the state file is missing, the kernel calls `seed_if_missing()`
during `start()` with the full set of discovered manifests. Existing users see
no behavior change; the file just appears with everyone marked installed.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Schema v2:
# {
#   "schema_version": 2,
#   "installed": {
#     "<id>": {"installed_at": "ISO8601", "version": "x.y.z"},
#     ...
#   },
#   "disabled": ["<id>", ...],         # subset of installed.keys() — kept on disk but not loaded
#   "last_change": "ISO8601 | null"    # set on any state-change; the UI uses this to flag restart-pending
# }
#
# v1 files (no `disabled` field) are auto-migrated on load() by backfilling
# `disabled: []`. The bump is forward-compat only — v2 readers handle v1
# files; v1 readers wouldn't recognise the new field.
_SCHEMA_VERSION = 2


def _path(data_dir: Path, kind: str) -> Path:
    return data_dir / "store" / f"installed-{kind}.json"


def _empty_state() -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "installed": {},
        "disabled": [],
        "last_change": None,
    }


def load(data_dir: Path, kind: str) -> dict:
    """Read the state file for *kind* ("apps" or "plugins").

    Returns an empty-shape dict if the file is missing. Tolerant of malformed
    JSON — returns empty rather than raising, so a corrupt state file can't
    block boot (boot then re-seeds). v1 files are upgraded in-memory to v2
    by adding an empty `disabled` list; the upgrade only persists on next
    write.
    """
    path = _path(data_dir, kind)
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "installed" not in data:
            raise ValueError("malformed")
        data.setdefault("schema_version", _SCHEMA_VERSION)
        data.setdefault("last_change", None)
        data.setdefault("disabled", [])  # v1 → v2 migration
        if not isinstance(data["disabled"], list):
            data["disabled"] = []
        return data
    except (json.JSONDecodeError, ValueError, OSError):
        return _empty_state()


def save(data_dir: Path, kind: str, state: dict) -> None:
    """Atomically write the state file (write to .tmp, then rename)."""
    path = _path(data_dir, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def installed_ids(data_dir: Path, kind: str) -> set[str]:
    """Set of ids currently marked installed for *kind* (regardless of enable state)."""
    return set(load(data_dir, kind).get("installed", {}).keys())


def disabled_ids(data_dir: Path, kind: str) -> set[str]:
    """Set of installed ids that are explicitly disabled.

    A disabled id stays in `installed` (config preserved, dep graph intact)
    but isn't loaded at boot. Empty when no user has flipped any disable
    toggle.
    """
    return set(load(data_dir, kind).get("disabled", []) or [])


def enabled_ids(data_dir: Path, kind: str) -> set[str]:
    """`installed - disabled` — what the kernel should load at boot.

    Essentials are not unioned here; that's the loader's job (the helper
    doesn't know which ids are essential, since that lives in
    `app_loader.ESSENTIAL_APPS` / `plugin_loader.ESSENTIAL_PLUGINS`).
    """
    return installed_ids(data_dir, kind) - disabled_ids(data_dir, kind)


def is_installed(data_dir: Path, kind: str, id_: str) -> bool:
    return id_ in load(data_dir, kind).get("installed", {})


def is_disabled(data_dir: Path, kind: str, id_: str) -> bool:
    return id_ in (load(data_dir, kind).get("disabled", []) or [])


def mark_installed(data_dir: Path, kind: str, id_: str, version: str = "0.0.0") -> None:
    state = load(data_dir, kind)
    state["installed"][id_] = {
        "installed_at": datetime.utcnow().isoformat(timespec="seconds"),
        "version": version,
    }
    state["last_change"] = datetime.utcnow().isoformat(timespec="seconds")
    save(data_dir, kind, state)


def mark_uninstalled(data_dir: Path, kind: str, id_: str) -> bool:
    """Remove *id_* from installed set. Returns True if anything changed.

    Also drops *id_* from the disabled list if present — a not-installed
    app can't be in a disabled state, that would be a contradiction.
    """
    state = load(data_dir, kind)
    changed = False
    if id_ in state["installed"]:
        del state["installed"][id_]
        changed = True
    if id_ in (state.get("disabled") or []):
        state["disabled"] = [x for x in state["disabled"] if x != id_]
        changed = True
    if changed:
        state["last_change"] = datetime.utcnow().isoformat(timespec="seconds")
        save(data_dir, kind, state)
    return changed


def mark_disabled(data_dir: Path, kind: str, id_: str) -> bool:
    """Move *id_* into the disabled list. No-op if not installed or already disabled.

    Returns True if anything changed. Disabling a not-installed app is a
    no-op rather than an error — the caller (store API) decides whether
    that's a 404 or a soft success.
    """
    state = load(data_dir, kind)
    if id_ not in state["installed"]:
        return False
    if id_ in (state.get("disabled") or []):
        return False
    state.setdefault("disabled", []).append(id_)
    state["last_change"] = datetime.utcnow().isoformat(timespec="seconds")
    save(data_dir, kind, state)
    return True


def mark_enabled(data_dir: Path, kind: str, id_: str) -> bool:
    """Remove *id_* from the disabled list. No-op if not disabled.

    Returns True if anything changed. Enabling a not-installed app is a
    no-op (you have to install it first to have anything to enable).
    """
    state = load(data_dir, kind)
    if id_ not in (state.get("disabled") or []):
        return False
    state["disabled"] = [x for x in state["disabled"] if x != id_]
    state["last_change"] = datetime.utcnow().isoformat(timespec="seconds")
    save(data_dir, kind, state)
    return True


def seed_if_missing(
    data_dir: Path, kind: str, items: Iterable[tuple[str, str]]
) -> bool:
    """First-boot seed. *items* yields (id, version) tuples.

    If the state file already exists, this is a no-op (returns False).
    Otherwise it writes every item as installed and returns True. Preserves
    today's behavior on existing daemons — nothing disappears after the store
    code lands.
    """
    path = _path(data_dir, kind)
    if path.exists():
        return False
    now = datetime.utcnow().isoformat(timespec="seconds")
    state = {
        "schema_version": _SCHEMA_VERSION,
        "installed": {
            id_: {"installed_at": now, "version": version} for id_, version in items
        },
        "disabled": [],
        "last_change": None,  # null on first seed — nothing's been changed by the user
    }
    save(data_dir, kind, state)
    return True


def last_change(data_dir: Path, kind: str) -> str | None:
    """ISO timestamp of the last install/uninstall, or None if untouched."""
    return load(data_dir, kind).get("last_change")
