"""Product identity — parse ``products/<id>/product.toml``.

One product = one config file. The launcher, the PyInstaller spec and (later)
the update manifest all read the same declaration, so a second product is a
new ``product.toml``, not a second pipeline.

Pure module: no kernel import, no filesystem side effects beyond reading the
file it is handed. Safe to import from a frozen entrypoint and from tests.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProductConfig:
    """A product's declared identity.

    ``tier`` names a ``release.toml`` tier — that is what makes the packaging
    pipeline tier-agnostic: the product says which slice of EmptyOS it bundles,
    and ``package-release.py`` resolves it.
    """

    id: str
    display_name: str
    tier: str
    #: Basename of the built executable + its one-dir folder.
    exe_name: str = ""
    start_url: str = "/hub/"
    appdata_name: str = "EmptyOS"
    brand_dir: str = ""
    # Empty until the first-run wizard ships (Phase 2). When set, a run that
    # *created* the config opens this instead of start_url.
    welcome_url: str = ""
    # Empty disables the update check (Phase 3).
    update_feed: str = ""
    window_width: int = 1440
    window_height: int = 900


def parse_product(data: dict) -> ProductConfig:
    """Build a :class:`ProductConfig` from parsed TOML. Pure — unit-testable."""
    product = data.get("product") or {}
    update = data.get("update") or {}

    pid = str(product.get("id") or "").strip()
    if not pid:
        raise ValueError("product.toml: [product] id is required")
    tier = str(product.get("tier") or "").strip()
    if not tier:
        raise ValueError(f"product.toml: [product] tier is required (product '{pid}')")

    window = product.get("window") or {}
    display_name = str(product.get("display_name") or pid)
    return ProductConfig(
        id=pid,
        display_name=display_name,
        tier=tier,
        exe_name=str(product.get("exe_name") or display_name),
        start_url=str(product.get("start_url") or "/hub/"),
        appdata_name=str(product.get("appdata_name") or product.get("display_name") or pid),
        brand_dir=str(product.get("brand_dir") or ""),
        welcome_url=str(product.get("welcome_url") or ""),
        update_feed=str(update.get("feed") or ""),
        window_width=int(window.get("width") or 1440),
        window_height=int(window.get("height") or 900),
    )


def load_product(path: str | Path) -> ProductConfig:
    """Read and parse a ``product.toml``."""
    p = Path(path)
    with open(p, "rb") as f:
        return parse_product(tomllib.load(f))


def product_toml_path(launcher_file: str | Path) -> Path:
    """Where ``product.toml`` lives at runtime.

    Frozen: it is bundled at the root of the PyInstaller data tree (``_MEIPASS``).
    From source: next to the product's ``launcher.py``.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "product.toml"
    return Path(launcher_file).resolve().parent / "product.toml"
