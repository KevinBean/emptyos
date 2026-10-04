"""System-tray icons owned by a process outside the daemon.

``plugins/system-tray/`` puts a tray on a daemon that is already running, which
is the wrong place for a product: the tray disappears whenever the daemon
restarts, and its Restart item shells out to ``restart.bat`` (a dev-machine
script that boots GPU services and assumes a repo). Here the tray belongs to the
process that outlives the daemon — the product supervisor (:func:`start_tray`)
or the desktop shell (``shell.start_shell_tray``, via :func:`load_icon_image` and
:func:`run_tray`) — so it survives every daemon respawn.

pystray + Pillow are optional. Without them the product still works — it just
has no tray, and closing the window leaves the daemon running until the user
quits it. Callers must treat tray failure as non-fatal.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .launcher_core import Supervisor


class TrayHandle:
    """Handle to a running tray, so its owner can refresh, notify and stop it."""

    def __init__(self, icon, thread: threading.Thread):
        self._icon = icon
        self._thread = thread

    def alive(self) -> bool:
        """An icon is actually on screen: its thread runs and pystray marked it
        visible. A handle alone is not enough — ``run_tray`` returns before the
        icon is ready, and a tray thread can die later."""
        try:
            return self._thread.is_alive() and bool(self._icon.visible)
        except Exception:
            return False

    def refresh(self) -> None:
        """Re-read a menu built from callables (labels, visibility)."""
        try:
            self._icon.update_menu()
        except Exception:
            pass

    def notify(self, message: str, title: str = "") -> None:
        try:
            self._icon.notify(message, title)
        except Exception:
            pass

    def stop(self) -> None:
        """Remove the icon and wait (briefly) until it's gone — ``Icon.stop``
        only posts a message, and a process that exits first leaves a ghost
        icon in the tray until the user hovers over it."""
        try:
            self._icon.stop()
        except Exception:
            pass
        try:
            self._thread.join(2.0)
        except Exception:
            pass


def load_icon_image(brand_dir: Path | None):
    """A brand directory's icon, or a plain generated mark as a fallback."""
    from PIL import Image, ImageDraw

    if brand_dir:
        for name in ("icon.png", "icon-256.png", "icon.ico"):
            candidate = Path(brand_dir) / name
            if candidate.exists():
                try:
                    return Image.open(candidate)
                except Exception:
                    continue

    image = Image.new("RGB", (64, 64), color="black")
    draw = ImageDraw.Draw(image)
    draw.ellipse([16, 16, 48, 48], fill="white")
    return image


def run_tray(icon_id: str, image, title: str, menu) -> TrayHandle:
    """Run a pystray icon on a background thread. Raises if pystray is missing."""
    import pystray

    icon = pystray.Icon(icon_id, image, title, menu)
    thread = threading.Thread(target=icon.run, daemon=True)
    thread.start()
    return TrayHandle(icon, thread)


def _load_icon_image(sup: Supervisor):
    from .launcher_core import app_root

    brand = app_root() / sup.product.brand_dir if sup.product.brand_dir else None
    return load_icon_image(brand)


def _check_updates(sup: Supervisor) -> None:
    """Check on a thread (it does network I/O; pystray's callback must not block),
    then open Settings, where an available update has a Restart button."""
    def run() -> None:
        sup.check_for_update()
        sup.request_open_window_at("/settings/?tab=system")

    threading.Thread(target=run, daemon=True).start()


def start_tray(sup: Supervisor) -> TrayHandle:
    """Run a tray icon on a background thread. Raises if pystray is unavailable."""
    import pystray

    items = [
        pystray.MenuItem(f"Open {sup.product.display_name}",
                         lambda *_: sup.request_open_window(), default=True),
    ]
    if sup.product.update_feed:
        # Checking is the launcher's job (it owns the install tree); *showing* the
        # result is the daemon's (it has a UI). So the tray does the check and
        # sends the user to the panel that can act on it.
        items.append(pystray.MenuItem("Check for updates",
                                      lambda *_: _check_updates(sup)))
    items += [
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart", lambda *_: sup.request_restart()),
        pystray.MenuItem("Quit", lambda *_: sup.request_quit()),
    ]
    return run_tray(sup.product.id, _load_icon_image(sup), sup.product.display_name,
                    pystray.Menu(*items))
