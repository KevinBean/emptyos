# PyInstaller spec for the stub — the exe the user's shortcut points at.
#
#   EOS_PRODUCT=desktop-windows pyinstaller products/_shared/stub.spec --noconfirm
#
# Onefile, and deliberately tiny: it imports nothing from EmptyOS and its only job
# is to pick the newest app-<version>/ directory and spawn it. It is the one thing
# that must start even when everything else in the install is broken — if the stub
# fails, the user has an icon that does nothing.
#
# It is also what makes the stub itself updatable: because it exits the moment it
# has spawned the app, its own file is never locked while EmptyOS is running.

import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(ROOT / "products"))

from _shared.product_config import load_product  # noqa: E402

PRODUCT_ID = os.environ.get("EOS_PRODUCT", "desktop-windows")
PRODUCT_DIR = ROOT / "products" / PRODUCT_ID
if not (PRODUCT_DIR / "product.toml").exists():
    raise SystemExit(f"No products/{PRODUCT_ID}/product.toml")
PRODUCT = load_product(PRODUCT_DIR / "product.toml")

with open(ROOT / "release.toml", "rb") as f:
    VERSION = tomllib.load(f)["release"]["version"]

ICON = ROOT / PRODUCT.brand_dir / "icon.ico" if PRODUCT.brand_dir else None

a = Analysis(
    [str(ROOT / "products" / "_shared" / "stub.py")],
    pathex=[str(ROOT / "products")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    excludes=[
        # The stub must stay small and dependency-free. If any of these ever show
        # up in it, something has been imported that has no business being here.
        "numpy", "scipy", "pandas", "fastapi", "uvicorn", "aiohttp",
        "tkinter", "PIL", "pystray", "test", "tests",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=PRODUCT.exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console: this process lives for a few milliseconds and a flashing black
    # window on every launch is the most visible thing about the whole product.
    # Errors go to a MessageBox instead (stub.fail).
    console=False,
    icon=str(ICON) if ICON and ICON.exists() else None,
)
