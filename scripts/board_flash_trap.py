"""board_flash_trap — flash/monitor helper for deep-sleeping e-ink boards.

The M5PaperColor (firmware/paper_dashboard/) deep-sleeps with USB dead and
re-enumerates only on its own timer wake, a button wake, or a download-mode
power-hold. Iterating on its firmware therefore needs three recurring moves,
extracted here after being hand-typed ~8x in one session (rule-9 for
workflows):

  trap    wait for the board's COM port to appear, flash the sketch, then
          watchdog-kick it out of the ROM bootloader into the app (esptool's
          RTS reset leaves this board parked in the bootloader — the latch).
  watch   serial logger that does NOT assert DTR/RTS (asserting them holds
          the S3 in reset — the silent-serial trap), resilient to the port
          vanishing on deep sleep.
  status  one-shot: is the port present, and is the chip sitting in the
          ROM bootloader?

Usage (from the repo root):
  python scripts/board_flash_trap.py trap   [--port COM4] [--minutes 75] [--sketch firmware/paper_dashboard]
  python scripts/board_flash_trap.py watch  [--port COM4] [--minutes 10] [--out watch.log]
  python scripts/board_flash_trap.py status [--port COM4]

Full hardware playbook: firmware/paper_dashboard/README.md § Hardware reference.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time

FQBN = "m5stack:esp32:m5stack_papercolor:PSRAM=opi,FlashSize=16M,CDCOnBoot=cdc,PartitionScheme=huge_app"


def find_arduino_cli() -> str:
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"),
        "arduino-cli",
    ]
    for c in candidates:
        if c == "arduino-cli" or os.path.exists(c):
            return c
    raise SystemExit("arduino-cli not found — install Arduino IDE 2.x or put arduino-cli on PATH")


def find_esptool() -> str:
    hits = glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Arduino15\packages\**\esptool.exe"), recursive=True)
    if not hits:
        raise SystemExit("esptool.exe not found under Arduino15 packages")
    return hits[0]


def port_present(port: str) -> bool:
    try:
        import serial.tools.list_ports
        return any(p.device == port for p in serial.tools.list_ports.comports())
    except Exception:
        return False


def kick(port: str) -> None:
    """Watchdog-reset the chip out of the ROM bootloader into the app."""
    subprocess.run([find_esptool(), "--port", port, "--before", "no-reset",
                    "--after", "watchdog-reset", "chip-id"],
                   capture_output=True, text=True, timeout=60)


def cmd_trap(args) -> int:
    cli = find_arduino_cli()
    print(f"[trap] armed @ {time.strftime('%H:%M:%S')} — waiting for {args.port} "
          f"(power-hold the board ~3s, or wait for its timer wake)", flush=True)
    t_end = time.time() + args.minutes * 60
    while time.time() < t_end:
        try:
            if port_present(args.port):
                print(f"[trap] {args.port} @ {time.strftime('%H:%M:%S')} — flashing in 3s", flush=True)
                time.sleep(3)
                r = subprocess.run([cli, "upload", "-p", args.port, "--fqbn", args.fqbn, args.sketch],
                                   capture_output=True, text=True, timeout=240)
                if "Hard resetting" in (r.stdout + r.stderr):
                    print("[trap] FLASHED OK — kicking into the app", flush=True)
                    kick(args.port)
                    print("[trap] done — new build live", flush=True)
                    return 0
                print("[trap] flash failed:", (r.stdout + r.stderr)[-200:], flush=True)
                time.sleep(20)
        except Exception as e:  # noqa: BLE001 — the trap must outlive USB churn
            print(f"[trap] loop error (continuing): {e}", flush=True)
            time.sleep(5)
        time.sleep(2)
    print("[trap] window expired without a flash", flush=True)
    return 1


def cmd_watch(args) -> int:
    import serial
    out = open(args.out, "a", encoding="utf-8") if args.out else sys.stdout

    def emit(text: str) -> None:
        out.write(text)
        out.flush()

    t_end = time.time() + args.minutes * 60
    s = None
    emit(f"\n[watch] start @ {time.strftime('%H:%M:%S')} on {args.port}\n")
    while time.time() < t_end:
        if s is None:
            try:
                s = serial.Serial()
                s.port, s.baudrate, s.timeout = args.port, 115200, 0.3
                # NEVER assert DTR/RTS — they hold the S3 in reset (hard-won).
                s.dtr = False
                s.rts = False
                s.open()
                emit(f"\n[watch] port open @ {time.strftime('%H:%M:%S')}\n")
            except Exception:
                s = None
                time.sleep(0.4)
                continue
        try:
            data = s.read(4096)
            if data:
                emit(data.decode("utf-8", "replace"))
        except Exception:
            emit(f"\n[watch] port dropped @ {time.strftime('%H:%M:%S')} (board sleeping?)\n")
            try:
                s.close()
            except Exception:
                pass
            s = None
    emit("\n[watch] done\n")
    return 0


def cmd_status(args) -> int:
    present = port_present(args.port)
    print(f"{args.port} present: {present}")
    if present:
        r = subprocess.run([find_esptool(), "--port", args.port, "--before", "no-reset",
                            "--after", "no-reset", "chip-id"],
                           capture_output=True, text=True, timeout=60)
        in_bootloader = "Chip type" in (r.stdout + r.stderr)
        print(f"in ROM bootloader: {in_bootloader}"
              + ("  (use `trap` to flash, or kick with esptool --after watchdog-reset)" if in_bootloader
                 else "  (app is running — serial via `watch`)"))
    else:
        print("board is deep-sleeping — wake it (button / power-hold) or wait for its timer poll")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("trap", cmd_trap), ("watch", cmd_watch), ("status", cmd_status)):
        p = sub.add_parser(name)
        p.add_argument("--port", default="COM4")
        p.set_defaults(fn=fn)
        if name == "trap":
            p.add_argument("--minutes", type=int, default=75)
            p.add_argument("--sketch", default="firmware/paper_dashboard")
            p.add_argument("--fqbn", default=FQBN)
        elif name == "watch":
            p.add_argument("--minutes", type=int, default=10)
            p.add_argument("--out", default="")
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
