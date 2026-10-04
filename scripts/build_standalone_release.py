"""build_standalone_release.py — build an app's standalone distribution artifacts.

The two standard no-daemon distribution targets (see
.claude/rules/standalone-distribution.md) for locked-down IT environments
(no Python, no exe):

1. ``single-html`` — one self-contained HTML file. Opens from disk / network
   share / email. Chrome blocks IndexedDB on file:// pages, so the export
   shim falls back to its memory store + explicit "Save/Load data to file"
   (amber pill, Ctrl+S).
2. ``extension`` — Manifest V3 Chrome extension zip (generated shell + the
   csp-safe export as app/). chrome-extension:// pages get real IndexedDB,
   so data persists without manual saves. Zero host permissions, no network.

Which targets an app ships is declared in its manifest:

    [provides.export]
    enabled = true
    targets = ["single-html", "extension"]   # default: ["single-html"]

The ``extension`` target requires the app's export surface to stay inside
the eos-csp-bridge handler grammar — verified here via
scripts/check-csp-inline.py --app <id> before building.

Usage:
    python scripts/build_standalone_release.py <app_id> [--out dist]
        [--targets single-html,extension] [--skip-verify]
    python scripts/build_standalone_release.py --group <group_id> [--out dist]

Artifacts land in ``dist/<id>-standalone-<ver>/`` plus a bundle zip.
Requires the ``eos`` CLI (same Python env); never touches running daemons.
Per-app README extras: drop an ``EXPORT-README.md`` next to the app's
manifest — appended verbatim to the generated README.

``--group`` packages an export-groups.toml multi-app bundle as an MV3
extension (extension target only — groups have no single-html mode): the
chooser shell becomes the extension's entry page and every member must
pass the CSP grammar scan.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS = ["single-html"]
VALID_TARGETS = {"single-html", "extension"}

README_TEMPLATE = """\
# {name} — standalone release v{version}

{description}

Pure HTML+JS — no Python, no installer, no server, no network access
(the only optional network call is bring-your-own-key AI, and only after
you paste a key into the offline panel yourself).

## What's in this folder

| File | Use when |
|---|---|
{rows}

## Where your data lives

- **Extension**: in the browser's own storage (IndexedDB) — persists
  automatically across sessions.
- **Single .html file**: browsers block persistent storage on local files,
  so use the amber pill's **Save data to file… / Load data from file…**
  (or Ctrl+S) to keep your data between sessions — e.g. on a shared drive.
  The same Save/Load buttons work in the extension too, as backup/transfer.

## Installing the extension

1. Unzip `{ext_zip}` to a folder (or use the pre-unzipped one here).
2. Open `chrome://extensions` (or `edge://extensions`).
3. Enable **Developer mode** (top right).
4. **Load unpacked** → pick the folder.
5. Click the extension's toolbar icon.
"""

ROW_HTML = "| `{html}` | You just want a file. Open it in Chrome/Edge (double-click). |\n"
ROW_EXT = "| `{zip}` | You're allowed to install extensions. Data then persists automatically. |\n"


def _find_manifest(app_id: str) -> tuple[dict, Path]:
    from emptyos.sdk.app_layout import iter_app_dirs

    for aid, app_dir in iter_app_dirs(ROOT / "apps", include_personal=True):
        if aid == app_id:
            with open(app_dir / "manifest.toml", "rb") as f:
                return tomllib.load(f), app_dir
    sys.exit(f"unknown app id: {app_id!r}")


def _run_eos(args: list[str], fail_msg: str) -> None:
    """Run an `eos` CLI command (console script when on PATH, module fallback)."""
    eos = shutil.which("eos")
    base = [eos] if eos else [sys.executable, "-c", "from emptyos.cli.main import app; app()"]
    cmd = base + args
    print("  $", " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"{fail_msg} (exit {r.returncode})")


def _run_export(app_id: str, args: list[str]) -> None:
    _run_eos(["app", "export", app_id] + args, "export failed")


def build_single_html(app_id: str, out_dir: Path, version: str, verify: bool) -> Path:
    print("• single-html …")
    with tempfile.TemporaryDirectory() as td:
        tmp_out = Path(td) / f"{app_id}-export"
        args = ["--format", "single-html", "--out", str(tmp_out)]
        if verify:
            args.append("--verify")
        _run_export(app_id, args)
        built = tmp_out.parent / (tmp_out.name + ".html")
        target = out_dir / f"{app_id}-{version}.html"
        shutil.copy(built, target)
    print(f"  → {target.name} ({target.stat().st_size // 1024} KB)")
    return target


def _csp_gate(app_id: str) -> None:
    """The extension target requires the export surface to stay inside the
    eos-csp-bridge handler grammar."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-csp-inline.py"), "--app", app_id],
        cwd=ROOT,
    )
    if r.returncode != 0:
        sys.exit(
            f"'{app_id}' has inline handlers outside the eos-csp-bridge grammar "
            "— fix them (see scanner output) before shipping an extension target"
        )


def _zip_dir(src: Path, target: Path, *, arc_root: str = "") -> Path:
    """Zip ``src``; ``arc_root`` prefixes every entry (release bundle zips
    unpack into a named folder, extension zips unpack flat)."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                arc = p.relative_to(src)
                z.write(p, Path(arc_root) / arc if arc_root else arc)
    print(f"  → {target.name} ({target.stat().st_size // 1024} KB)")
    return target


def _verify_extension(shell: Path, member: str = "") -> None:
    """Load-extension MV3 verification (scripts/verify_extension.py)."""
    cmd = [sys.executable, str(ROOT / "scripts" / "verify_extension.py"), str(shell)]
    if member:
        cmd += ["--member", member]
    print("  $", " ".join(cmd[1:]))
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        sys.exit("extension verification failed")


def build_extension(
    app_id: str, out_dir: Path, manifest: dict, version: str, *, verify: bool = True
) -> Path:
    from emptyos.sdk.exporter import build_extension_shell

    print("• chrome extension …")
    _csp_gate(app_id)
    shell = out_dir / f"{app_id}-extension-{version}"
    if shell.exists():
        shutil.rmtree(shell)
    shell.mkdir(parents=True)
    _run_export(app_id, ["--format", "dir", "--csp-safe", "--out", str(shell / "app")])
    build_extension_shell(
        shell,
        name=manifest["app"].get("name", app_id),
        version=version,
        description=manifest["app"].get("description", ""),
    )
    if verify:
        _verify_extension(shell)
    return _zip_dir(shell, out_dir / f"{app_id}-extension-{version}.zip")


def build_group_extension(group_id: str, out_root: Path, *, verify: bool = True) -> int:
    """--group mode: package an export-groups.toml bundle as an MV3 extension."""
    from emptyos.sdk.exporter import build_extension_shell, load_groups

    groups = load_groups(ROOT / "export-groups.toml")
    group = next((g for g in groups if g.get("id") == group_id), None)
    if not group:
        sys.exit(f"unknown group id: {group_id!r} — available: {[g.get('id') for g in groups]}")
    version = str(group.get("version", "1.0.0"))
    name = group.get("name", group_id)
    for member in group.get("apps", []):
        _csp_gate(member)

    out_dir = (out_root / f"{group_id}-standalone-{version}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Building group '{group_id}' extension v{version} → {out_dir}")

    shell = out_dir / f"{group_id}-extension-{version}"
    if shell.exists():
        shutil.rmtree(shell)
    shell.mkdir(parents=True)
    _run_eos(
        ["export-group", "build", group_id, "--format", "dir", "--csp-safe",
         "--out", str(shell / "app")],
        "group export failed",
    )
    build_extension_shell(
        shell,
        name=name,
        version=version,
        description=group.get("description", ""),
        entry="app/index.html",   # the chooser shell
    )
    if verify:
        _verify_extension(shell, member=(group.get("apps") or [""])[0])
    zip_path = _zip_dir(shell, out_dir / f"{group_id}-extension-{version}.zip")
    readme = README_TEMPLATE.format(
        name=name,
        version=version,
        description=group.get("description", ""),
        rows=ROW_EXT.format(zip=zip_path.name),
        ext_zip=zip_path.name,
    )
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    print("• bundle", end="")
    _zip_dir(out_dir, out_dir.parent / f"{group_id}-standalone-{version}.zip",
             arc_root=out_dir.name)
    print("Done.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app_id", nargs="?", help="export-enabled app id")
    ap.add_argument("--group", default="", help="export-groups.toml group id (extension target only)")
    ap.add_argument("--out", default="dist", help="output root (default: dist/)")
    ap.add_argument("--targets", default="", help="comma list overriding the manifest's [provides.export].targets")
    ap.add_argument("--skip-verify", action="store_true",
                    help="skip the Playwright checks (single-html file:// + extension MV3 load)")
    ns = ap.parse_args()

    if ns.group:
        return build_group_extension(ns.group, ROOT / ns.out, verify=not ns.skip_verify)
    if not ns.app_id:
        ap.error("app_id required (or use --group <group_id>)")

    manifest, app_dir = _find_manifest(ns.app_id)
    export_cfg = (manifest.get("provides") or {}).get("export") or {}
    if not export_cfg.get("enabled"):
        sys.exit(f"'{ns.app_id}' has not declared [provides.export].enabled = true")
    targets = (
        [t.strip() for t in ns.targets.split(",") if t.strip()]
        if ns.targets
        else list(export_cfg.get("targets") or DEFAULT_TARGETS)
    )
    bad = set(targets) - VALID_TARGETS
    if bad:
        sys.exit(f"unknown target(s): {sorted(bad)} — valid: {sorted(VALID_TARGETS)}")

    version = manifest["app"].get("version", "0.0.0")
    name = manifest["app"].get("name", ns.app_id)
    out_dir = (ROOT / ns.out / f"{ns.app_id}-standalone-{version}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Building {ns.app_id} standalone release v{version} ({', '.join(targets)}) → {out_dir}")

    rows = ""
    ext_zip = ""
    if "single-html" in targets:
        html = build_single_html(ns.app_id, out_dir, version, verify=not ns.skip_verify)
        rows += ROW_HTML.format(html=html.name)
    if "extension" in targets:
        zip_path = build_extension(
            ns.app_id, out_dir, manifest, version, verify=not ns.skip_verify
        )
        ext_zip = zip_path.name
        rows += ROW_EXT.format(zip=ext_zip)

    readme = README_TEMPLATE.format(
        name=name,
        version=version,
        description=manifest["app"].get("description", ""),
        rows=rows,
        ext_zip=ext_zip or "(no extension target)",
    )
    extra = app_dir / "EXPORT-README.md"
    if extra.exists():
        readme += "\n" + extra.read_text(encoding="utf-8")
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    print("• bundle", end="")
    _zip_dir(out_dir, out_dir.parent / f"{ns.app_id}-standalone-{version}.zip",
             arc_root=out_dir.name)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
