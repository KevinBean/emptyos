"""Smoke-test a built product bundle — boot it, hit it, shut it down.

    python products/_shared/smoke.py                    # desktop-windows
    python products/_shared/smoke.py --product foo      # any products/<id>/
    python products/_shared/smoke.py --exe path/to.exe

This is the only test that runs the artifact a user actually receives, and it is
worth its weight: every bug below was invisible to the unit tests and to a
`node --check`-grade "does it build" pass, because each one only exists once the
code is frozen.

  - the daemon deadlocked forever on `import numpy` (OpenBLAS builds a thread
    pool inside its DLL entry point; under a frozen bundle's loader lock, with
    the daemon's other threads alive, that hangs). No error, no output, no exit.
  - the daemon killed itself at boot: its shutdown watcher treated stdin EOF as
    "stop", and EOF is the normal state of a process with no stdin.
  - two engines failed to load behind an over-broad PyInstaller exclude.
  - a live API key shipped inside the bundle (see tests/test_unit_package_release.py).

Run it after every product build. It exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "products"))

from _shared.product_config import load_product  # noqa: E402

#: Credentials a pilot user's machine will not have. Passing the dev box's
#: environment through made the first smoke run connect to a real Telegram bot —
#: the test was quietly exercising a machine no user has.
CREDENTIAL_MARKERS = ("TELEGRAM", "OPENAI", "ANTHROPIC", "TOKEN", "API_KEY",
                      "SECRET", "PEXELS", "EOS_")

BOOT_DEADLINE_S = 300.0


def json_request(base: str, path: str, *, method: str = "GET", body: dict | None = None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base + path, data=payload, method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def clean_env(sandbox: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not any(m in k.upper() for m in CREDENTIAL_MARKERS)}
    env["APPDATA"] = str(sandbox / "AppData")       # -> config, data, logs
    env["USERPROFILE"] = str(sandbox)               # -> Path.home(), so the vault
    env["HOMEPATH"] = str(sandbox)                  #    lands in the sandbox too
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default="desktop-windows")
    ap.add_argument("--exe", default="")
    args = ap.parse_args()

    product = load_product(ROOT / "products" / args.product / "product.toml")
    exe = Path(args.exe) if args.exe else (
        ROOT / "dist" / "product" / product.exe_name / f"{product.exe_name}.exe"
    )
    if not exe.exists():
        print(f"FAIL: no bundle at {exe}\n"
              f"  python scripts/package-release.py {product.tier} --platform=laptop\n"
              f"  EOS_PRODUCT={args.product} pyinstaller products/_shared/product.spec "
              f"--noconfirm --distpath dist/product")
        return 1

    sandbox = Path(tempfile.mkdtemp(prefix=f"{product.id}-smoke-"))
    print(f"exe:     {exe}")
    print(f"sandbox: {sandbox}")

    proc = subprocess.Popen(
        [str(exe), "--no-window", "--no-tray"],
        env=clean_env(sandbox), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    app_dir = sandbox / "AppData" / product.appdata_name
    cfg = app_dir / "emptyos.toml"
    failures = 0

    # 1. first run lays down config + vault
    deadline = time.time() + 60
    while time.time() < deadline and not cfg.exists():
        if proc.poll() is not None:
            print("FAIL: exited before writing its config")
            print((proc.stdout.read() or b"").decode("utf-8", "replace")[-3000:])
            return 1
        time.sleep(0.5)
    if not cfg.exists():
        print("FAIL: no first-run config after 60s")
        proc.kill()
        return 1
    port = tomllib.loads(cfg.read_text(encoding="utf-8"))["network"]["port"]
    vault = sandbox / "Documents" / f"{product.appdata_name}Vault"
    print(f"PASS: first-run config written (port={port}, vault exists={vault.is_dir()})")

    # 2. it serves
    base = f"http://127.0.0.1:{port}"
    t0 = time.time()
    health = None
    while time.time() - t0 < BOOT_DEADLINE_S:
        if proc.poll() is not None:
            print(f"FAIL: daemon exited (code {proc.returncode}) during boot")
            break
        try:
            # Plain /api/health — NOT ?full=true, which probes every external
            # service and outlasts any sane request timeout. Polling that looked
            # exactly like a dead daemon while the daemon was serving fine.
            with urllib.request.urlopen(base + "/api/health", timeout=5) as r:
                health = json.loads(r.read().decode("utf-8"))
            break
        except Exception:
            time.sleep(1)

    if health:
        print(f"PASS: /api/health in {time.time()-t0:.0f}s "
              f"(status={health.get('status')}, apps={health.get('apps')})")
        if not health.get("apps"):
            print("FAIL: daemon is up but loaded no apps")
            failures += 1
        pages = [product.start_url, "/api/apps"]
        if product.welcome_url:
            # The first thing a real user sees. A 404 here is the whole product
            # failing at hello.
            pages.append(product.welcome_url)
        for path in pages:
            try:
                with urllib.request.urlopen(base + path, timeout=10) as r:
                    print(f"PASS: GET {path} -> {r.status}")
            except Exception as e:
                print(f"FAIL: GET {path} -> {e}")
                failures += 1

        # The product API the wizard and the About panel are built on. It must be
        # live here (the launcher sets EOS_PRODUCT) and dark from source.
        try:
            with urllib.request.urlopen(base + "/settings/api/product", timeout=10) as r:
                info = json.loads(r.read().decode("utf-8"))
            if not info.get("enabled"):
                print("FAIL: /settings/api/product is dark inside the product")
                failures += 1
            elif not info.get("version"):
                print("FAIL: product API reports no version (MANIFEST.json not found?)")
                failures += 1
            else:
                print(f"PASS: product API — v{info['version']} "
                      f"({info.get('tier')}), vault={info.get('vault_path')}")
        except Exception as e:
            print(f"FAIL: GET /settings/api/product -> {e}")
            failures += 1

        if product.id == "macro-studio":
            # Product-specific acceptance: the dynamically loaded Windows
            # libraries survived freezing, and the product can persist the
            # same versioned note format used by full EmptyOS. No input is
            # injected during this smoke.
            try:
                _, config = json_request(base, "/operate/api/macro/config")
                services = config.get("services") or {}
                required = ("desktop_control", "actuation", "point_picker", "global_hotkey")
                missing = [name for name in required if not services.get(name)]
                if missing:
                    print("FAIL: Macro Studio desktop dependencies unavailable: "
                          + ", ".join(missing))
                    failures += 1
                else:
                    print("PASS: Macro Studio desktop dependencies loaded")

                macro = {
                    "version": 1,
                    "name": "Packaged smoke macro",
                    "target": {"process": "notepad.exe", "title_contains": "Untitled"},
                    "repeat": {"mode": "count", "count": 1, "interval_seconds": 3},
                    "steps": [{"id": "s1", "action": "press", "key": "right"}],
                }
                status, created = json_request(
                    base, "/operate/api/macros", method="POST", body=macro,
                )
                macro_id = (created.get("macro") or {}).get("id", "")
                if status != 201 or not created.get("ok") or not macro_id:
                    print(f"FAIL: packaged macro persistence returned {created}")
                    failures += 1
                else:
                    _, detail = json_request(base, f"/operate/api/macros/{macro_id}")
                    if (detail.get("macro") or {}).get("name") != macro["name"]:
                        print("FAIL: packaged macro did not round-trip")
                        failures += 1
                    else:
                        print(f"PASS: packaged macro persisted ({macro_id})")
            except Exception as e:
                print(f"FAIL: Macro Studio product smoke -> {e}")
                failures += 1
    else:
        print(f"FAIL: never served in {BOOT_DEADLINE_S:.0f}s — see {app_dir/'daemon.log'}")
        failures += 1

    # 3. it stops when asked — an explicit message, never EOF
    t0 = time.time()
    try:
        proc.stdin.write(b"stop\n")
        proc.stdin.flush()
        proc.wait(timeout=60)
        code = proc.returncode
        print(f"PASS: graceful shutdown in {time.time()-t0:.1f}s (exit {code})"
              if code == 0 else f"FAIL: shut down with exit {code}")
        failures += 0 if code == 0 else 1
    except subprocess.TimeoutExpired:
        print("FAIL: ignored the stop request; killing the tree")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        failures += 1

    if failures:
        log = app_dir / "daemon.log"
        if log.exists():
            print(f"\n--- {log} (tail) ---")
            print(log.read_text(encoding="utf-8", errors="replace")[-2500:])
    print("\nSMOKE " + ("FAILED" if failures else "PASSED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
