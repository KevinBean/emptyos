"""Shared, kernel-free plumbing for EmptyOS CLI clients.

Configuration discovery, daemon probes, auth headers, and the agent-facing
JSON envelope all need to work before (or without) booting the kernel. Keep
them here so interactive clients and command modules share one implementation
without importing :mod:`emptyos.cli.main` and creating cycles.
"""

from __future__ import annotations

import json
import os
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console

console = Console()


def config_candidates() -> list[Path]:
    """Return config candidates in CLI precedence order."""
    candidates: list[Path] = []
    env_path = os.environ.get("EOS_CONFIG", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            Path("emptyos.toml"),
            Path.home() / ".config" / "emptyos" / "emptyos.toml",
        ]
    )

    pointer = Path.home() / ".config" / "emptyos" / "config-path.txt"
    if pointer.exists():
        try:
            target = pointer.read_text(encoding="utf-8").strip()
            if target:
                candidates.append(Path(target))
        except OSError:
            pass

    # Preserve precedence while avoiding repeated filesystem probes.
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def find_config() -> str | None:
    """Return the first existing config path, or ``None``."""
    for candidate in config_candidates():
        if candidate.exists():
            return str(candidate)
    return None


def require_config() -> str:
    """Return the active config path or emit the standard CLI error."""
    config_path = find_config()
    if config_path:
        return config_path
    console.print("[red]No emptyos.toml found.[/red]")
    console.print("  Set EOS_CONFIG=/path/to/emptyos.toml or place it in the current directory.")
    console.print("  Run [bold]eos init[/bold] to create one interactively.")
    raise typer.Exit(1)


def _load_config(config_path: str | Path) -> dict[str, Any]:
    try:
        with Path(config_path).open("rb") as handle:
            data = tomllib.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def env_key_for(key: str) -> str:
    """Map a dotted config key to its environment-variable override name."""
    return "EOS_" + key.replace(".", "_").upper()


def cfg_get(config: dict[str, Any], key: str, default: Any = None) -> Any:
    """Read a dotted config key, honouring the ``EOS_<KEY>`` env override.

    Mirrors :meth:`emptyos.kernel.config.Config.get` so kernel-free CLI clients
    resolve `network.auth_token`, `network.port`, and friends the same way the
    daemon does. The demo container, for one, injects `EOS_NETWORK_AUTH_TOKEN`
    by env and leaves it out of the TOML entirely.
    """
    env_val = os.environ.get(env_key_for(key))
    if env_val is not None:
        return env_val

    node: Any = config
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def resolve_data_dir(
    explicit: str | Path | None = None,
    config_path: str | Path | None = None,
) -> Path:
    """Resolve the daemon data directory without importing the kernel.

    ``explicit`` wins, then ``EOS_OS_DATA_DIR``; both are cwd-relative like
    :class:`Config`. Otherwise use canonical ``[os] data_dir`` and retain the
    older ``[data] path`` shape as a compatibility fallback — TOML values are
    anchored to the config file so the CLI finds the same directory the daemon
    writes to no matter which directory it runs from.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()

    env_dir = os.environ.get(env_key_for("os.data_dir"))
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    chosen = str(config_path) if config_path else find_config()
    if not chosen:
        return Path("data").resolve()
    config = _load_config(chosen)
    raw = cfg_get(config, "os.data_dir") or cfg_get(config, "data.path")
    data_dir = Path(raw or "data").expanduser()
    if not data_dir.is_absolute():
        data_dir = Path(chosen).resolve().parent / data_dir
    return data_dir.resolve()


def client_base_url(config_path: str | Path) -> str:
    """Return a connectable daemon URL from a TOML config."""
    config = _load_config(config_path)
    mode = str(cfg_get(config, "network.mode") or "local").lower().strip()
    default_host = "127.0.0.1" if mode == "local" else "0.0.0.0"
    host = str(cfg_get(config, "network.host") or default_host).strip()
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = int(cfg_get(config, "network.port") or 9000)
    return f"http://{host}:{port}"


def probe_health(base_url: str, timeout: float = 2.0) -> bool:
    """Return whether the daemon health endpoint responds successfully."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def health_snapshot(base_url: str, timeout: float = 2.0) -> dict[str, Any]:
    """Return reachability plus sandbox-ready health semantics."""
    fallback = {
        "reachable": False,
        "ready": False,
        "status": "unreachable",
        "apps": 0,
    }
    try:
        with urllib.request.urlopen(f"{base_url}/api/health", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # urlopen raises for any >=400, so this — not a status check on a
        # returned response — is where a 5xx or a 401 actually surfaces.
        return {**fallback, "status": f"http_{exc.code}"}
    except Exception:
        return fallback
    if not isinstance(data, dict):
        return fallback
    status = str(data.get("status") or "unknown")
    apps = int(data.get("apps") or 0)
    return {
        "reachable": True,
        "ready": status == "ok" and apps > 0,
        "status": status,
        "apps": apps,
    }


def daemon_url() -> str | None:
    """Return the active daemon URL when its health probe succeeds."""
    config_path = find_config()
    if not config_path:
        return None
    base_url = client_base_url(config_path)
    return base_url if probe_health(base_url, timeout=1.0) else None


def bearer_headers(
    config_path: str | Path | None = None,
    *,
    json_content: bool = False,
) -> dict[str, str]:
    """Build request headers, including configured bearer auth."""
    headers = {"Content-Type": "application/json"} if json_content else {}
    chosen = str(config_path) if config_path else find_config()
    # An env-only token still applies when no TOML is reachable.
    config = _load_config(chosen) if chosen else {}
    token = str(cfg_get(config, "network.auth_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def emit(
    ok: bool,
    code: str,
    message: str,
    data: Any = None,
    *,
    as_json: bool,
    human: Callable[[], None] | None = None,
) -> NoReturn:
    """Emit the shared agent-CLI envelope or human output, then exit."""
    if as_json:
        typer.echo(json.dumps({"ok": ok, "code": code, "message": message, "data": data}))
    elif human is not None:
        human()
    elif ok:
        console.print(message)
    else:
        console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=0 if ok else 1)
