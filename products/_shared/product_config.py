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
    # Third-party packages imported only after a plugin is loaded at runtime.
    # PyInstaller cannot discover those from the launcher's static import graph,
    # so products declare the packages they need collected into their bundle.
    collect_packages: tuple[str, ...] = ()
    # Optional capabilities present on a developer machine but intentionally
    # absent from this product. Their source paths may still exist in the tier;
    # imports degrade through EmptyOS's normal provider chain at runtime.
    exclude_packages: tuple[str, ...] = ()
    # First-run runtime policy. "human" is the non-AI fallback that keeps
    # manual EmptyOS features usable without a model or external service.
    think_providers: tuple[str, ...] = ("ollama", "human")
    enable_plugins: tuple[str, ...] = ()


def parse_product(data: dict) -> ProductConfig:
    """Build a :class:`ProductConfig` from parsed TOML. Pure — unit-testable."""
    product = data.get("product") or {}
    update = data.get("update") or {}
    build = data.get("build") or {}
    runtime = data.get("runtime") or {}

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
        collect_packages=tuple(
            str(name).strip()
            for name in (build.get("collect_packages") or [])
            if str(name).strip()
        ),
        exclude_packages=tuple(
            str(name).strip()
            for name in (build.get("exclude_packages") or [])
            if str(name).strip()
        ),
        think_providers=tuple(
            str(name).strip()
            for name in (runtime.get("think_providers") or ["ollama", "human"])
            if str(name).strip()
        ),
        enable_plugins=tuple(
            str(name).strip()
            for name in (runtime.get("enable_plugins") or [])
            if str(name).strip()
        ),
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
