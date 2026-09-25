"""Macro Studio — shared Windows product launcher entrypoint.

Build:
    python scripts/package-release.py macro-studio --platform=laptop
    EOS_PRODUCT=macro-studio pyinstaller products/_shared/product.spec --noconfirm
"""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.launcher_core import main  # noqa: E402
from _shared.product_config import load_product, product_toml_path  # noqa: E402


def run() -> int:
    product = load_product(product_toml_path(__file__))
    return main(product, entry_script=__file__)


if __name__ == "__main__":
    raise SystemExit(run())
