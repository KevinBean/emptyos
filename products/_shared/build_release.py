"""Assemble the shippable artifact: a zip a user can unzip and double-click.

    python products/_shared/build_release.py --url https://.../EmptyOS-Desktop-...zip

Runs after the two PyInstaller builds and turns them into the layout the stub and
the updater both expect::

    EmptyOS-Desktop-windows-x64-0.5.6.zip
        EmptyOS.exe          the stub (what the shortcut points at)
        app-0.5.6/
            EmptyOS.exe      the app
            _internal/…
            .ok              so a *hand*-unzipped install is launchable too

That `.ok` is easy to forget and fatal: the updater writes one after it verifies a
download, but a user who just unzips the release has no updater involved — without
it the stub would find no complete version and the product would not start at all.

The same script produces `latest-<product>.json`, with the checksum computed from
the very zip being uploaded. CI runs this; so can you.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "products"))

from _shared.product_config import load_product  # noqa: E402
from _shared.updater import OK_MARKER, app_dir_name  # noqa: E402


def release_version() -> str:
    with open(ROOT / "release.toml", "rb") as f:
        return str(tomllib.load(f)["release"]["version"])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default="desktop-windows")
    ap.add_argument("--platform", default="windows-x64")
    ap.add_argument("--url", default="", help="where the zip will be downloadable")
    ap.add_argument("--notes-url", default="")
    ap.add_argument("--stub-version", type=int, default=1)
    ap.add_argument("--app-build", default="", help="default: dist/product/<exe_name>")
    ap.add_argument("--stub-build", default="", help="default: dist/stub/<exe_name>.exe")
    ap.add_argument("--out", default="", help="default: dist/release")
    args = ap.parse_args()

    product = load_product(ROOT / "products" / args.product / "product.toml")
    version = release_version()
    app_build = Path(args.app_build) if args.app_build else ROOT / "dist" / "product" / product.exe_name
    stub_build = Path(args.stub_build) if args.stub_build else ROOT / "dist" / "stub" / f"{product.exe_name}.exe"
    out = Path(args.out) if args.out else ROOT / "dist" / "release"

    if not app_build.is_dir():
        print(f"no app build at {app_build}", file=sys.stderr)
        return 1
    if not stub_build.is_file():
        print(f"no stub build at {stub_build}", file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    name = product.display_name.replace(" ", "-")
    zip_path = out / f"{name}-{args.platform}-{version}.zip"
    zip_path.unlink(missing_ok=True)

    app_prefix = app_dir_name(version)
    print(f"packing {zip_path.name}…")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(stub_build, f"{product.exe_name}.exe")
        for path in sorted(app_build.rglob("*")):
            if path.is_file():
                zf.write(path, f"{app_prefix}/{path.relative_to(app_build).as_posix()}")
        # Mark it complete. A hand-unzipped install has no updater to do this, and
        # without it the stub sees no version and the app never starts.
        zf.writestr(f"{app_prefix}/{OK_MARKER}", version)

    digest = sha256(zip_path)
    manifest = {
        "product": product.id,
        "version": version,
        "stub_version": args.stub_version,
        "notes_url": args.notes_url,
        "platforms": {
            args.platform: {
                "url": args.url,
                "sha256": digest,
                "size": zip_path.stat().st_size,
            }
        },
    }
    manifest_path = out / f"latest-{product.id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    checksum_path = out / f"{zip_path.name}.sha256"
    checksum_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")

    print(f"  {zip_path}  ({zip_path.stat().st_size/1e6:.0f} MB)")
    print(f"  {manifest_path}")
    print(f"  {checksum_path}")
    print(f"  sha256 {digest}")
    if not args.url:
        print("\nNOTE: --url was empty, so latest.json points nowhere. The update "
              "check will do nothing until it is set to the real download URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
