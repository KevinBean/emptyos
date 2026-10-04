"""EmptyOS Desktop — PyInstaller entrypoint.

The whole product is `product.toml` plus this file. Everything real lives in
`products/_shared/launcher_core.py`, which owns the daemon subprocess, the
window, the tray, and (Phase 3) the updater.

Run from source:
    python products/desktop-windows/launcher.py            # boot + window + tray
    python products/desktop-windows/launcher.py --no-window --no-tray
    python products/desktop-windows/launcher.py --daemon   # the daemon child itself

Build:
    python scripts/package-release.py standard --platform=laptop
    EOS_PRODUCT=desktop-windows pyinstaller products/_shared/product.spec --noconfirm
"""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    # From source, `_shared` is a sibling package under products/. Frozen, it is
    # already in the bundle (the spec puts products/ on pathex).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.launcher_core import main  # noqa: E402
from _shared.product_config import load_product, product_toml_path  # noqa: E402


def run() -> int:
    product = load_product(product_toml_path(__file__))
    return main(product, entry_script=__file__)


if __name__ == "__main__":
    raise SystemExit(run())
