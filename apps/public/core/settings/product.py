"""Settings — product surfaces: first-run wizard, About, restart, license seam.

Extracted from app.py to keep the core spine atomic (CLAUDE.md rule 4). Owns
everything that only exists when EmptyOS is running as a **packaged product**
(`products/desktop-windows/`) rather than from source: the welcome wizard's
backing APIs, the About panel's identity/version data, the restart handshake with
the launcher, and the license-key field.

Dark by construction. Every route here returns ``{"enabled": false}`` unless the
launcher set ``EOS_PRODUCT`` — a dev daemon on :9000 never sets it, so it never
grows an updater or a wizard. There is no config flag to keep in step; the
environment *is* the flag, and only the product launcher can set it.

Cross-module callers reach these via ``self.X`` after re-binding in app.py.
Reaches into other modules: ``app._write_toml_section`` (the surgical TOML writer
shared with the network settings). Do not import from ``.app`` (it imports us).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:  # pragma: no cover
    from .app import SettingsApp  # noqa: F401


# ─── Bind to SettingsApp class as ────────────────────────────────────────────
#   product_info              = _product.product_info
#   api_product               = _product.api_product
#   api_product_vault         = _product.api_product_vault
#   api_product_restart       = _product.api_product_restart
#   api_product_update_status = _product.api_product_update_status
#   api_product_update_apply  = _product.api_product_update_apply
#   _product_enabled          = _product._product_enabled
#   _bundle_root              = _product._bundle_root
#   _updater                  = _product._updater
#   _staged_version           = _product._staged_version
#   _current_version          = _product._current_version
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────────


#: The launcher respawns a daemon that exits with this. Must match
#: products/_shared/launcher_core.RESTART_EXIT_CODE — a product change that
#: forgets this leaves the user with an app that quits when they meant restart.
RESTART_EXIT_CODE = 42

#: Exit with this instead, and the launcher hands off to the stub, which starts
#: the newest *installed* version rather than this one. Restarting into an update
#: has to go through the stub: the launcher is the old version and cannot start a
#: newer one.
UPDATE_EXIT_CODE = 43

#: Where the wizard's license key lives. No validation, no server, no expiry:
#: the seam only has to exist so a secret-tier verb can send it to a Lane-1
#: service (.claude/rules/product-packaging.md). Building the licence server
#: before there is anything to license would be building the wrong half.
LICENSE_KEY = "product.license_key"


def _product_enabled(self) -> bool:
    return bool(os.environ.get("EOS_PRODUCT"))


def _bundle_root(self) -> Path:
    """The frozen bundle's root — where MANIFEST.json (and so the version) lives."""
    env = os.environ.get("EOS_BUNDLE_ROOT")
    if env:
        return Path(env)
    return Path.cwd()


async def product_info(self) -> dict:
    """Identity, version and locations. The About panel and the wizard both read this."""
    if not self._product_enabled():
        return {"enabled": False}

    version, tier = "", ""
    manifest = self._bundle_root() / "MANIFEST.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        version, tier = str(data.get("version", "")), str(data.get("tier", ""))
    except Exception:
        pass  # a bundle without a manifest still runs; it just can't name itself

    cfg = self.kernel.config
    return {
        "enabled": True,
        "product": os.environ.get("EOS_PRODUCT", ""),
        "version": version,
        "tier": tier,
        "config_path": str(cfg.path),
        "data_dir": str(cfg.data_dir),
        "vault_path": str(cfg.notes_path or ""),
        "install_root": os.environ.get("EOS_INSTALL_ROOT", ""),
        "license_key": self._settings().get(LICENSE_KEY) or "",
        "restart_exit_code": RESTART_EXIT_CODE,
    }


@web_route("GET", "/api/product")
async def api_product(self, request):
    return await self.product_info()


@web_route("POST", "/api/product/vault")
async def api_product_vault(self, request):
    """Point the vault somewhere else. Takes effect on the next daemon start.

    The vault path lives in emptyos.toml (not settings.json) because the kernel
    reads it at boot, before any app exists — so this is the same surgical,
    comment-preserving TOML edit the network settings use, just on ``[notes]``.
    """
    if not self._product_enabled():
        return {"error": "not running as a product"}

    data = await self.safe_json(request)
    raw = str(data.get("path", "")).strip().strip('"')
    if not raw:
        return {"error": "path required"}
    # The value is written into a TOML string. Reject what would break out of it.
    if '"' in raw or "\n" in raw or "\\" in raw.replace("\\\\", ""):
        return {"error": "path cannot contain quotes, newlines or backslashes — use forward slashes"}

    path = Path(raw).expanduser()
    if not path.is_absolute():
        return {"error": "path must be absolute"}
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"error": f"cannot create {path}: {e}"}
    if not os.access(path, os.W_OK):
        return {"error": f"{path} is not writable"}

    existing = any(path.glob("*.md"))
    ok, err = self._write_toml_section(
        self.kernel.config.path, "notes", {"path": path.as_posix()}
    )
    if not ok:
        return {"error": err}

    await self.emit("settings:vault_changed", {"path": path.as_posix()})
    return {
        "ok": True,
        "path": path.as_posix(),
        "adopted_existing": existing,   # tell the user we found notes already there
        "restart_required": True,
    }


def _updater(self):
    """Import the updater out of the bundle. It lives with the launcher, not the
    daemon: the daemon is a *version*, the updater manages versions."""
    root = self._bundle_root()
    for candidate in (root / "products", root.parent / "products"):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    from _shared import updater  # noqa: PLC0415

    return updater


def _staged_version(self) -> str:
    """A version sitting on disk, newer than the running one, waiting for a restart."""
    install_root = os.environ.get("EOS_INSTALL_ROOT", "")
    if not install_root:
        return ""
    try:
        updater = self._updater()
        newest = updater.newest_version(Path(install_root))
        if newest and updater.is_newer(newest[0], self._current_version()):
            return newest[0]
    except Exception:
        pass
    return ""


def _current_version(self) -> str:
    try:
        data = json.loads((self._bundle_root() / "MANIFEST.json").read_text(encoding="utf-8"))
        return str(data.get("version") or "")
    except Exception:
        return ""


@web_route("GET", "/api/product/update")
async def api_product_update_status(self, request):
    """What's installed, and what's waiting.

    Read-only and cheap — no network. The launcher already checks the feed in the
    background and stages anything newer, so by the time the user opens Settings
    the answer is a directory listing, not a download.
    """
    if not self._product_enabled():
        return {"enabled": False}
    staged = self._staged_version()
    return {
        "enabled": True,
        "current": self._current_version(),
        "staged": staged,
        "update_ready": bool(staged),
    }


@web_route("POST", "/api/product/update/apply")
async def api_product_update_apply(self, request):
    """Restart into the staged version.

    Exits 43, which the launcher reads as "hand off to the stub" — the stub then
    starts the newest installed version. It cannot be a plain restart: this
    process *is* the old version.
    """
    if not self._product_enabled():
        return {"error": "not running as a product"}
    staged = self._staged_version()
    if not staged:
        return {"error": "no update is staged"}

    async def _apply() -> None:
        await asyncio.sleep(0.4)          # let the response reach the browser
        try:
            await self.kernel.stop()      # flush SQLite/WAL before we go
        except Exception:
            pass
        os._exit(UPDATE_EXIT_CODE)

    self.spawn_background(_apply())
    await self.emit("system:restart_requested", {"source": "product-update", "version": staged})
    return {"ok": True, "applying": staged}


@web_route("POST", "/api/product/restart")
async def api_product_restart(self, request):
    """Restart the daemon by exiting 42 — the launcher respawns us.

    Nothing like the `/api/restart-daemon` route next door, which shells out to
    restart.bat: that is a dev-machine script that kills every python.exe and
    boots GPU services. A packaged product has a supervisor that owns this
    process and is already waiting on its exit code.
    """
    if not self._product_enabled():
        return {"error": "not running as a product"}

    data = await self.safe_json(request)
    if not data.get("confirm"):
        return {"error": "missing confirm: true — this restarts the daemon"}

    async def _restart() -> None:
        # Let the HTTP response flush before the socket dies under it.
        await asyncio.sleep(0.4)
        try:
            await self.kernel.stop()   # flush SQLite/WAL — never _exit on a live kernel
        except Exception:
            pass
        os._exit(RESTART_EXIT_CODE)

    self.spawn_background(_restart())
    await self.emit("system:restart_requested", {"source": "product"})
    return {"ok": True, "restarting": True}
