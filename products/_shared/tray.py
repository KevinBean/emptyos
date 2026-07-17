"""System-tray icon owned by the *launcher* process.

``plugins/system-tray/`` puts a tray on a daemon that is already running, which
is the wrong place for a product: the tray disappears whenever the daemon
restarts, and its Restart item shells out to ``restart.bat`` (a dev-machine
script that boots GPU services and assumes a repo). Here the tray belongs to the
supervisor, so it survives every daemon respawn and drives the real lifecycle.

pystray + Pillow are optional. Without them the product still works — it just
has no tray, and closing the window leaves the daemon running until the user
quits it. Callers must treat tray failure as non-fatal.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .launcher_core import Supervisor


class TrayHandle:
    """Handle to a running tray, so the supervisor can stop it on shutdown."""

    def __init__(self, icon, thread: threading.Thread):
        self._icon = icon
        self._thread = thread

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass


def _load_icon_image(sup: Supervisor):
    """The product's brand icon, or a plain generated mark as a fallback."""
    from PIL import Image, ImageDraw

    from .launcher_core import app_root

    if sup.product.brand_dir:
        for name in ("icon.png", "icon-256.png", "icon.ico"):
            candidate = app_root() / sup.product.brand_dir / name
            if candidate.exists():
                try:
                    return Image.open(candidate)
                except Exception:
                    continue

    image = Image.new("RGB", (64, 64), color="black")
    draw = ImageDraw.Draw(image)
    draw.ellipse([16, 16, 48, 48], fill="white")
    return image


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
    menu = pystray.Menu(*items)
    icon = pystray.Icon(
        sup.product.id, _load_icon_image(sup), sup.product.display_name, menu
    )
    thread = threading.Thread(target=icon.run, daemon=True)
    thread.start()
    return TrayHandle(icon, thread)
