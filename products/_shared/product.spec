# Generic PyInstaller spec — builds ANY product declared by a products/<id>/product.toml.
#
#   python scripts/package-release.py <tier> --platform=laptop
#   EOS_PRODUCT=desktop-windows pyinstaller products/_shared/product.spec --noconfirm
#
# Output: dist/<exe_name>/ (one-dir — debuggable, and the layout the Phase-3
# updater swaps wholesale).
#
# Tier-agnostic by construction: the product names a release.toml tier,
# package-release.py has already resolved it into dist/emptyos-<tier>-<ver>/,
# and this spec packages that tree as the application root. Replacing
# scripts/plekto.spec, which hardcoded one tier, one brand and one version.

import os
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]   # products/_shared/ -> repo root
sys.path.insert(0, str(ROOT / "products"))

from _shared.product_config import load_product  # noqa: E402

PRODUCT_ID = os.environ.get("EOS_PRODUCT", "desktop-windows")
PRODUCT_DIR = ROOT / "products" / PRODUCT_ID
if not (PRODUCT_DIR / "product.toml").exists():
    raise SystemExit(
        f"No products/{PRODUCT_ID}/product.toml. Set EOS_PRODUCT=<product dir name>."
    )
PRODUCT = load_product(PRODUCT_DIR / "product.toml")

with open(ROOT / "release.toml", "rb") as f:
    VERSION = tomllib.load(f)["release"]["version"]

# The tier tree package-release.py produced. Single source of truth for what
# ships — including the version, which is stamped into its MANIFEST.json.
TIER_DIST = ROOT / "dist" / f"emptyos-{PRODUCT.tier}-{VERSION}"
if not TIER_DIST.is_dir():
    raise SystemExit(
        f"Missing {TIER_DIST}. Build it first:\n"
        f"  python scripts/package-release.py {PRODUCT.tier} --platform=laptop"
    )

# Bundle the tier tree as data, mirroring the source layout, so runtime code
# doing `Path(__file__).parent / 'apps'` keeps working. launcher_core points the
# daemon's cwd + EOS_*_PATH at this root.
#
# The tier tree ships the test suite (a self-hosting release can run its own
# conformance checks). A product's user never will, and it is 26 MB of fixtures
# and screenshots, so it does not go in the exe.
SKIP_FROM_BUNDLE = {"tests", ".github", "restart.bat", "service.bat"}

datas = []
for entry in TIER_DIST.iterdir():
    if entry.name in SKIP_FROM_BUNDLE:
        continue
    datas.append((str(entry), entry.name if entry.is_dir() else "."))

# The product's own declaration, read back at runtime by product_toml_path().
datas.append((str(PRODUCT_DIR / "product.toml"), "."))

if PRODUCT.brand_dir and (ROOT / PRODUCT.brand_dir).is_dir():
    datas.append((str(ROOT / PRODUCT.brand_dir), PRODUCT.brand_dir))

ICON_WIN = ROOT / PRODUCT.brand_dir / "icon.ico" if PRODUCT.brand_dir else None
ICON_MAC = ROOT / PRODUCT.brand_dir / "icon.icns" if PRODUCT.brand_dir else None

hidden = []
# Third-party packages that lazy-import their own submodules (uvicorn's
# protocols, apscheduler's executors) vanish from the bundle unless collected.
#
# `emptyos` is deliberately NOT in this list. It ships as *source* in the tier
# tree above and is imported at runtime off sys.path (launcher_core.run_daemon).
# Collecting it would import every module in the package — including optional
# ones like sdk/embeddings.py — dragging their heavyweight deps in behind them.
# Doing exactly that produced a 5.73 GB bundle (torch 3.8 GB, onnxruntime 402 MB,
# bitsandbytes 176 MB) before this comment existed. The static graph from
# `emptyos.cli.main` is what the daemon actually needs to boot; anything past it
# is an optional capability that degrades to its next provider.
for pkg in ("uvicorn", "fastapi", "starlette", "anyio", "h11", "h2",
            "aiohttp", "anthropic", "openai", "watchfiles", "apscheduler",
            "pystray", "PIL"):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass

for pkg in ("fastapi", "starlette", "uvicorn"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# Belt-and-braces against the same blow-up returning by another route: a dev
# machine has the whole local-ML stack installed, and one new module-level import
# anywhere in the reachable graph would silently re-add gigabytes. These are the
# GPU / local-ML giants a desktop product never ships — the capabilities they back
# (local embeddings, OCR, local TTS, vision, headless browsing) degrade to their
# next provider, which is the point of the provider chain. An app whose top-level
# import needs one of these fails to load and is logged; the daemon still boots.
HEAVY_EXCLUDES = [
    "torch", "torchvision", "torchaudio",
    "transformers", "sentence_transformers",
    "onnxruntime", "onnxruntime_gpu", "bitsandbytes", "ctranslate2",
    "tensorflow", "numba", "llvmlite", "faiss", "cv2",
    "playwright", "av", "pyarrow",
]


a = Analysis(
    [str(PRODUCT_DIR / "launcher.py")],
    pathex=[str(ROOT), str(ROOT / "products")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # NOT "unittest": some engines import it at module level, and excluding it
    # made them fail to load ("No module named 'unittest'"). Inherited from the
    # plekto spec, where those engines weren't in the tier so it never showed.
    excludes=["tkinter", *HEAVY_EXCLUDES],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=PRODUCT.exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console stays on until the product has been through a pilot: a windowed
    # build that dies at boot shows the user nothing. The launcher also writes
    # %APPDATA%\<App>\launcher.log unconditionally, which is what survives the
    # flip to console=False.
    console=True,
    icon=str(ICON_WIN) if ICON_WIN and ICON_WIN.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=PRODUCT.exe_name,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{PRODUCT.display_name}.app",
        icon=str(ICON_MAC) if ICON_MAC and ICON_MAC.exists() else None,
        bundle_identifier=f"net.binbian.{PRODUCT.id}",
        info_plist={
            "CFBundleName": PRODUCT.display_name,
            "CFBundleDisplayName": PRODUCT.display_name,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
