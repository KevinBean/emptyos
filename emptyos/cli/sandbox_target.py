"""Resolve a ready sandbox-pool member for CLI targeting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from emptyos.cli._common import health_snapshot


def _project_root(main_config: str) -> Path:
    return Path(main_config).resolve().parent


def _data_dir(main_config: str) -> Path:
    from emptyos.kernel.config import Config

    cfg = Config(main_config)
    data_dir = cfg.data_dir
    if not data_dir.is_absolute():
        data_dir = _project_root(main_config) / data_dir
    return data_dir


def _member_health(port: int) -> dict[str, Any]:
    return health_snapshot(f"http://127.0.0.1:{port}")


def _pool_members(main_config: str) -> list[dict[str, Any]]:
    state = _data_dir(main_config) / "sandbox" / "pool.json"
    if state.exists():
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            members = data.get("members") or []
            if isinstance(members, list):
                return [m for m in members if isinstance(m, dict) and m.get("port")]
        except Exception:
            pass

    root = _project_root(main_config)
    members = []
    for cfg in sorted(root.glob("sandbox-*/emptyos.toml")):
        try:
            port = int(cfg.parent.name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            continue
        members.append({"port": port, "state": "unknown", "lease_id": None})
    return members


def resolve_sandbox_config(main_config: str, port: int | None = None) -> Path:
    """Return the config path for a ready sandbox member.

    Selection order without an explicit port:
    1. leased + ready member
    2. ready member
    """
    root = _project_root(main_config)
    members = _pool_members(main_config)
    if port is not None:
        members = [m for m in members if int(m.get("port") or 0) == int(port)]
        if not members:
            cfg = root / f"sandbox-{port}" / "emptyos.toml"
            if cfg.exists():
                members = [{"port": port, "state": "unknown", "lease_id": None}]
            else:
                raise RuntimeError(
                    f"No sandbox config found for port {port}. Run `eos sandbox status` first."
                )

    scored = []
    for m in members:
        p = int(m.get("port") or 0)
        cfg = root / f"sandbox-{p}" / "emptyos.toml"
        if not cfg.exists():
            continue
        health = _member_health(p)
        ready = bool(health.get("ready"))
        leased = bool(m.get("lease_id") or m.get("lease"))
        if ready:
            score = 2 if leased else 1
            scored.append((score, p, cfg))

    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[0][2]

    hint = "Run `eos sandbox status`, then `eos sandbox lease purpose=cli ttl_s=1800`."
    if port is not None:
        hint = f"Sandbox {port} is not ready. " + hint
    else:
        hint = "No ready sandbox member found. " + hint
    raise RuntimeError(hint)


def activate_sandbox_config(main_config: str, port: int | None = None) -> str:
    """Resolve and return a sandbox config path as a string."""
    return str(resolve_sandbox_config(main_config, port))
