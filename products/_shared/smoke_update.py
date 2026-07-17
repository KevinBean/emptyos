"""End-to-end test of the update path — the one that can brick a user's install.

    python products/_shared/smoke_update.py

Everything else about a product is recoverable: a bad build doesn't ship, a bad
setting is one edit away. A bad *update* replaces a working app with a broken one
on a machine you cannot reach. So this test drives the whole thing for real —
stub, versioned directories, exit codes, handoff — with no mocks:

  1. lay out an install:  <root>/EmptyOS.exe (stub) + app-<v>/ (the app)
  2. launch the STUB, not the app — the way a user's shortcut does
  3. assert it picked the version and the daemon came up
  4. stage a newer version beside it, exactly as the updater would
  5. assert the running daemon *sees* it as staged
  6. POST apply -> daemon exits 43 -> launcher hands off to the stub
  7. assert the NEW version is now running

Step 7 is the one that matters. Everything before it can pass while the product
still can't update itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "products"))

from _shared import updater as up  # noqa: E402
from _shared.product_config import load_product  # noqa: E402
from _shared.smoke import clean_env  # noqa: E402

APP_BUILD = ROOT / "dist" / "product" / "EmptyOS"
STUB_BUILD = ROOT / "dist" / "stub" / "EmptyOS.exe"


def current_version() -> str:
    with open(ROOT / "release.toml", "rb") as f:
        return str(tomllib.load(f)["release"]["version"])


def bump(version: str) -> str:
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def wait_health(get_port, timeout: float = 300.0) -> tuple[int, dict] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = get_port()
        if port:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=5
                ) as r:
                    return port, json.loads(r.read().decode("utf-8"))
            except Exception:
                pass
        time.sleep(2)
    return None


def api(port: int, path: str, body: dict | None = None) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def stage_version(root: Path, version: str, source: Path) -> Path:
    """Put a version on disk exactly as updater.install() leaves one."""
    target = root / up.app_dir_name(version)
    shutil.copytree(source, target)
    manifest = target / "_internal" / "MANIFEST.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["version"] = version
    manifest.write_text(json.dumps(data), encoding="utf-8")
    (target / up.OK_MARKER).write_text(version, encoding="utf-8")
    return target


def main() -> int:
    if not APP_BUILD.is_dir() or not STUB_BUILD.is_file():
        print("FAIL: build both first:\n"
              "  EOS_PRODUCT=desktop-windows pyinstaller products/_shared/product.spec "
              "--noconfirm --distpath dist/product\n"
              "  EOS_PRODUCT=desktop-windows pyinstaller products/_shared/stub.spec "
              "--noconfirm --distpath dist/stub")
        return 1

    product = load_product(ROOT / "products" / "desktop-windows" / "product.toml")
    v1 = current_version()
    v2 = bump(v1)
    sandbox = Path(tempfile.mkdtemp(prefix="eos-update-smoke-"))
    root = sandbox / "install"
    root.mkdir(parents=True)
    failures = 0

    print(f"sandbox: {sandbox}\n{v1} -> {v2}")

    # 1. an install as a user would have it after unzipping
    print("laying out the install (copying the bundle — this takes a moment)…")
    stage_version(root, v1, APP_BUILD)
    shutil.copy2(STUB_BUILD, root / "EmptyOS.exe")

    installed = up.installed_versions(root)
    if [v for v, _ in installed] != [v1]:
        print(f"FAIL: expected only {v1} installed, got {installed}")
        return 1
    print(f"PASS: install laid out — stub + app-{v1}")

    # 2. launch the STUB (what the shortcut points at), not the app
    env = clean_env(sandbox)
    stub_proc = subprocess.Popen(
        [str(root / "EmptyOS.exe"), "--no-window", "--no-tray"],
        env=env, cwd=str(root),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    stub_proc.wait(timeout=60)   # the stub spawns the app and exits immediately
    print(f"PASS: stub exited ({stub_proc.returncode}) after handing off")

    cfg = sandbox / "AppData" / product.appdata_name / "emptyos.toml"

    def port_from_config() -> int | None:
        if not cfg.exists():
            return None
        try:
            return int(tomllib.loads(cfg.read_text(encoding="utf-8"))["network"]["port"])
        except Exception:
            return None

    got = wait_health(port_from_config)
    if not got:
        print("FAIL: the app the stub launched never came up")
        return 1
    port, health = got
    print(f"PASS: stub launched the app — daemon up on :{port} ({health.get('apps')} apps)")

    info = api(port, "/settings/api/product")
    if info.get("version") != v1:
        print(f"FAIL: running version is {info.get('version')}, expected {v1}")
        failures += 1
    else:
        print(f"PASS: running v{v1}")

    # 3. an update lands on disk — exactly what updater.install() produces
    print(f"staging {v2}…")
    stage_version(root, v2, APP_BUILD)

    status = api(port, "/settings/api/product/update")
    if not status.get("update_ready") or status.get("staged") != v2:
        print(f"FAIL: running daemon does not see the staged update: {status}")
        failures += 1
    else:
        print(f"PASS: daemon sees {v2} staged and ready")

    # 4. apply — daemon exits 43, launcher hands off to the stub, stub picks v2
    if not failures:
        try:
            api(port, "/settings/api/product/update/apply", {"confirm": True})
        except Exception:
            pass  # the connection dies under us by design
        print("applying (daemon exits 43 -> launcher -> stub -> newest version)…")
        time.sleep(8)

        got = wait_health(port_from_config, timeout=300)
        if not got:
            print("FAIL: nothing came back up after applying the update")
            failures += 1
        else:
            port, _ = got
            info = api(port, "/settings/api/product")
            if info.get("version") == v2:
                print(f"PASS: now running v{v2} — the update applied itself")
            else:
                print(f"FAIL: still on v{info.get('version')} after applying {v2}")
                failures += 1

    # Cleanup: kill only what is running *out of this sandbox*. A blanket
    # `taskkill /IM EmptyOS.exe` would also kill a real install on this machine.
    q = subprocess.run(
        ["wmic", "process", "where", "name='EmptyOS.exe'", "get", "ProcessId,ExecutablePath"],
        capture_output=True, text=True,
    )
    for line in q.stdout.splitlines()[1:]:
        parts = line.rsplit(None, 1)
        if len(parts) == 2 and str(sandbox) in parts[0]:
            subprocess.run(["taskkill", "/F", "/T", "/PID", parts[1].strip()],
                           capture_output=True)

    print("\nUPDATE SMOKE " + ("FAILED" if failures else "PASSED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
