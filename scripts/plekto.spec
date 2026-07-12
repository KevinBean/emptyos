# PyInstaller spec for Plekto.
# Build: pyinstaller scripts/plekto.spec --noconfirm
# Output: dist/Plekto/ (one-dir mode — debuggable, smaller compressed)
#
# Bundles a Plekto-tier subset of EmptyOS: kernel + sdk + capabilities +
# web + the 10 Plekto-flagship apps + 12 core apps + 5 plugins + 4 skills.
# Builds either tier set via package-release.py first; this spec then
# packages dist/emptyos-plekto-<ver>/ as the application root.

from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from pathlib import Path

# Resolve repo paths assuming this spec is run from the repo root.
ROOT = Path(SPECPATH).resolve().parent
PLEKTO_DIST = next(ROOT.glob("dist/emptyos-plekto-*"), None)
if PLEKTO_DIST is None:
    raise SystemExit(
        "No dist/emptyos-plekto-*/ directory found. Run first:\n"
        "  python scripts/package-release.py plekto --platform=laptop"
    )

LAUNCHER = ROOT / "scripts" / "plekto_launcher.py"

# Bundle the entire Plekto-tier release output as a data tree under the
# bundle root. Path inside the bundle mirrors the source layout — code
# that does `Path(__file__).parent / 'apps'` keeps working.
datas = []
for entry in PLEKTO_DIST.iterdir():
    src = str(entry)
    if entry.is_dir():
        datas.append((src, entry.name))
    else:
        datas.append((src, "."))

# Icon (macOS .icns / Windows .ico) — these need to be pre-generated
# from brand/plekto/icon.svg. Build step in the workflow creates them.
ICON_MAC = ROOT / "brand" / "plekto" / "icon.icns"
ICON_WIN = ROOT / "brand" / "plekto" / "icon.ico"

hidden = []
# Web framework + ASGI server need explicit submodule collection;
# uvicorn lazy-imports its protocols and lifespan handlers.
for pkg in ("uvicorn", "fastapi", "starlette", "anyio", "h11", "h2",
            "aiohttp", "anthropic", "watchfiles", "apscheduler"):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass

# Data files for packages that ship JSON / templates / static assets.
for pkg in ("fastapi", "starlette", "uvicorn"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass


a = Analysis(
    [str(LAUNCHER)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Local-dev cruft we never want in the bundle.
        "tkinter", "test", "tests", "unittest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Plekto",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,   # leave console on for v1 — surfaces errors during onboarding
    icon=str(ICON_WIN) if ICON_WIN.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Plekto",
)

# macOS .app bundle. PyInstaller produces the .app structure when BUNDLE()
# is invoked. The DMG-wrapping step happens in the workflow (hdiutil).
import sys
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Plekto.app",
        icon=str(ICON_MAC) if ICON_MAC.exists() else None,
        bundle_identifier="dev.plekto.desktop",
        info_plist={
            "CFBundleName": "Plekto",
            "CFBundleDisplayName": "Plekto",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
