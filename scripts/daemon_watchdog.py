"""Daemon watchdog — captures evidence when :9000 (or any EmptyOS daemon) wedges,
and (with --restart) recovers it.

Polls /api/health on a short timeout. On 2 consecutive failures, snapshots the
moment-of-hang state into data/wedge-evidence/<iso-ts>/ so the cause is
diagnosable after the fact.

By default it does NOT restart the daemon — that stays the operator's call, and
manual `python scripts/daemon_watchdog.py` runs are evidence-only for diagnosis.
With --restart it becomes a crash/wedge supervisor: after evidence is captured
it restarts the daemon (targeted kill of the wedged PID's tree + detached
respawn), with a storm guard so a daemon that crashes on every boot can't loop
forever. restart.bat launches it with --restart so :9000 self-heals in normal
operation. This is safe because the watchdog is a user-owned process, not one
spawned by a Claude session (see .claude/rules/daemon-handling.md).

Usage:
    python scripts/daemon_watchdog.py                   # watches :9000, evidence only
    python scripts/daemon_watchdog.py --restart         # + auto-recover on wedge/crash
    python scripts/daemon_watchdog.py --port 9001       # watch the dogfood sidecar
    python scripts/daemon_watchdog.py --interval 15     # poll every 15s (default 30)
    python scripts/daemon_watchdog.py --probe-timeout 3 # 3s probe timeout (default 5)

Recovery tuning (only with --restart):
    --restart-grace 240     # wait this long for health after a respawn (default 240s)
    --boot-timeout 900      # ...but keep waiting past that while the respawned
                            #    PROCESS is still alive, up to this ceiling (default 15 min)
    --max-restarts 3        # respawns allowed within the storm window (default 3)
    --restart-window 1800   # storm window in seconds (default 30 min)
    --start-cmd <argv...>   # override the start command (default: <python> -m emptyos start)

Why the two-level wait: "nothing LISTENING on :9000" means either the daemon
crashed (respawn) or the daemon we just respawned is still booting (wait), and
the port cannot distinguish them. Guessing wrong compounds — respawning on top
of a live boot leaves both running, they starve each other, and boots get slower
with every attempt. On 2026-07-30 that took a steady 28s boot to 1006s across
four stacked daemons and kept :9000 down for ~50 minutes. So the watchdog keeps
the PID it spawned, asks whether that process is alive before declaring failure,
and kills any earlier boot of its own before starting another.

Run in a separate terminal — keeps running until Ctrl+C. Restart-survival:
re-run after restart.bat.

Evidence captured per wedge event (under data/wedge-evidence/<iso-ts>/):
    summary.json       — wedge metadata (first-seen, last-healthy, port, pid)
    netstat.txt        — netstat -ano snapshot (connection table at hang)
    tasklist.txt       — process list with memory
    pyspy_dump.txt     — py-spy dump --pid <daemon> (if py-spy installed)
    daemon_err.txt     — tail of data/daemon.err.log
    eos_stderr.txt     — tail of data/eos-stderr.log
    syslog_tail.txt    — last 200 syslog rows
    restart_log.txt    — tail of data/daemon-restart.log

py-spy is the load-bearing tool — it dumps each thread's Python stack of a
running process without restarting it. Install once:
    pip install py-spy
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
EVIDENCE_ROOT = DATA_DIR / "wedge-evidence"


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def probe_health(port: int, timeout: float) -> tuple[bool, str]:
    """Return (ok, detail). Treats any non-200 / timeout as not-ok."""
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        req = urllib.request.Request(url)
        # /api/health doesn't require auth in any deployment mode today
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return True, "200"
            return False, f"http_{resp.status}"
    except urllib.error.URLError as e:
        return False, f"urlerror:{e.reason!r}"
    except TimeoutError:
        return False, "timeout"
    except Exception as e:
        return False, f"exc:{type(e).__name__}:{e}"


def find_listening_pid(port: int) -> int | None:
    """Parse netstat -ano for the PID that owns the LISTENING socket on port."""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception as e:
        log(f"netstat failed: {e}")
        return None
    pattern = re.compile(rf"\s+TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)")
    for line in out.splitlines():
        m = pattern.search(line)
        if m:
            return int(m.group(1))
    return None


def run_cmd(cmd: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return f"$ {' '.join(cmd)}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}\n--- returncode={r.returncode} ---\n"
    except Exception as e:
        return f"$ {' '.join(cmd)}\n--- exception: {e!r} ---\n"


def tail_file(path: Path, n: int = 200) -> str:
    if not path.exists():
        return f"(missing: {path})\n"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]) or "(empty)\n"
    except Exception as e:
        return f"(read failed: {e!r})\n"


def syslog_tail(n: int = 200) -> str:
    db = DATA_DIR / "syslog.db"
    if not db.exists():
        return f"(missing: {db})\n"
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        rows = conn.execute(
            "SELECT ts, level, source, message FROM syslog ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
        conn.close()
    except Exception as e:
        return f"(sqlite failed: {e!r})\n"
    lines = []
    for ts, level, source, message in reversed(rows):
        dt = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "??:??:??"
        lines.append(f"{dt} {level:5} {source:20} {message}")
    return "\n".join(lines) or "(no rows)"


def parse_pid_list(text: str) -> list[int]:
    """Pull PIDs out of a pgrep / PowerShell listing, ignoring any noise lines."""
    out: list[int] = []
    for line in (text or "").splitlines():
        tok = line.strip()
        if tok.isdigit():
            n = int(tok)
            if n > 0 and n not in out:
                out.append(n)
    return out


def find_daemon_pids(limit: int = 4) -> list[int]:
    """Live EmptyOS daemon PIDs, found by command line rather than by port.

    find_listening_pid answers "who holds the port", which is None in the exact
    case most worth diagnosing: a daemon that is alive but no longer serving —
    stuck in shutdown, saturated, or with its socket already gone. Evidence
    capture keyed py-spy off the listening PID, so it skipped stacks precisely
    when they mattered; every one of the 218 snapshots taken on 2026-07-30 has
    no stacks and no usable process list, which is why that outage stayed
    unfalsifiable. Costs a subprocess, but only on a confirmed wedge.
    """
    if sys.platform == "win32":
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -like '*emptyos*start*' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        out = run_cmd(["powershell", "-NoProfile", "-Command", ps], timeout=25)
    else:
        out = run_cmd(["pgrep", "-f", "emptyos start"], timeout=15)
    return parse_pid_list(out)[:limit]


def capture_evidence(
    port: int,
    pid: int | None,
    first_seen: float,
    last_healthy: float | None,
) -> Path:
    ts = utc_iso()
    out = EVIDENCE_ROOT / ts
    out.mkdir(parents=True, exist_ok=True)

    # Alive-but-not-listening is the diagnosis that used to be invisible, so
    # record it as its own field: daemon_pid is who holds the port (often None),
    # daemon_pids is who is actually running.
    daemon_pids = find_daemon_pids()

    summary = {
        "captured_at_utc": ts,
        "port": port,
        "daemon_pid": pid,
        "daemon_pids": daemon_pids,
        "alive_but_not_listening": bool(daemon_pids) and pid is None,
        "first_wedge_seen": datetime.fromtimestamp(first_seen).isoformat(),
        "last_healthy": datetime.fromtimestamp(last_healthy).isoformat() if last_healthy else None,
        "seconds_wedged": round(time.time() - first_seen, 1),
        "watchdog_pid": os.getpid(),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    (out / "netstat.txt").write_text(run_cmd(["netstat", "-ano"]), encoding="utf-8")
    # No /V: verbose resolves a window title + user per process and reliably blew
    # the timeout on a loaded box, so every snapshot's process list read
    # "TimeoutExpired" instead of naming the processes.
    (out / "tasklist.txt").write_text(
        run_cmd(["tasklist", "/FO", "TABLE"], timeout=30), encoding="utf-8"
    )

    # Dump every candidate, not just the listener — see find_daemon_pids.
    targets: list[int] = ([pid] if pid else []) + [p for p in daemon_pids if p != pid]
    if targets and shutil.which("py-spy"):
        for target in targets[:3]:
            (out / f"pyspy_dump_{target}.txt").write_text(
                run_cmd(["py-spy", "dump", "--pid", str(target)], timeout=30),
                encoding="utf-8",
            )
    elif targets:
        (out / "pyspy_dump.txt").write_text(
            "py-spy not on PATH — install with `pip install py-spy` and re-run watchdog "
            f"for thread-stack evidence on PIDs {targets}.\n",
            encoding="utf-8",
        )

    (out / "daemon_err.txt").write_text(
        tail_file(DATA_DIR / "daemon.err.log"), encoding="utf-8"
    )
    (out / "eos_stderr.txt").write_text(
        tail_file(DATA_DIR / "eos-stderr.log"), encoding="utf-8"
    )
    (out / "restart_log.txt").write_text(
        tail_file(DATA_DIR / "daemon-restart.log"), encoding="utf-8"
    )
    # How the daemon died: kernel.stop() clears this record as its FIRST act, so
    # cleared => a graceful stop path ran (tray Quit, Settings restart) even if
    # it never finished; still naming a dead PID => killed from outside.
    pidfile = DATA_DIR / "daemon.pid.json"
    (out / "daemon_pidfile.txt").write_text(
        pidfile.read_text(encoding="utf-8") if pidfile.exists() else "(no daemon.pid.json)",
        encoding="utf-8",
    )
    (out / "syslog_tail.txt").write_text(syslog_tail(), encoding="utf-8")

    return out


def _read_telegram_config() -> tuple[str | None, str | None]:
    """Read [plugins.telegram] bot_token + chat_id from emptyos.toml.

    Returns (token, chat_id) or (None, None) if config missing. Best-effort —
    any parse failure returns None silently; notification will fall back to
    the flag file only.
    """
    cfg = REPO_ROOT / "emptyos.toml"
    if not cfg.exists():
        return None, None
    try:
        import tomllib
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    tg = (data.get("plugins") or {}).get("telegram") or {}
    return tg.get("bot_token"), tg.get("chat_id")


def _notify_wedge(args, evidence_dir, pid, first_wedge, last_healthy) -> None:
    """Push wedge alert via telegram + write data/wedge-alert.flag.

    Telegram is best-effort (skipped if unconfigured / network fails). The
    flag file always writes so a local watcher (system-tray, /loop) can
    notice without polling the watchdog directly.
    """
    wedged_for = round(time.time() - first_wedge, 1)
    last_h = datetime.fromtimestamp(last_healthy).strftime("%H:%M:%S") if last_healthy else "—"
    msg = (
        f"⚠ EmptyOS :{args.port} wedged\n"
        f"first failure: {datetime.fromtimestamp(first_wedge).strftime('%H:%M:%S')}\n"
        f"last healthy: {last_h}\n"
        f"wedged for: {wedged_for}s\n"
        f"daemon pid: {pid}\n"
        f"evidence: {evidence_dir.name}"
    )

    # Always write the flag file (local UI hint)
    try:
        flag = DATA_DIR / "wedge-alert.flag"
        flag.write_text(
            json.dumps(
                {
                    "at": datetime.now().isoformat(),
                    "port": args.port,
                    "evidence_dir": str(evidence_dir),
                    "pid": pid,
                    "wedged_for_s": wedged_for,
                    "message": msg,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log(f"wrote {flag}")
    except Exception as e:
        log(f"flag write failed: {e!r}")

    _send_telegram(args, msg)


def _send_telegram(args, msg: str) -> None:
    """Best-effort Telegram push; no-op when creds aren't configured."""
    if getattr(args, "no_notify", False):
        log("telegram skipped (--no-notify)")
        return
    token = args.telegram_token or None
    chat = args.telegram_chat or None
    if not token or not chat:
        t2, c2 = _read_telegram_config()
        token = token or t2
        chat = chat or c2
    if not token or not chat:
        log("telegram skipped (no token/chat configured)")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({"chat_id": chat, "text": msg}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                log("telegram alert sent")
            else:
                log(f"telegram returned {resp.status}")
    except Exception as e:
        log(f"telegram send failed: {e!r}")


# ─── Recovery (auto-restart) helpers — only exercised with --restart ──────────

def _append_restart_log(msg: str) -> None:
    """Append a timestamped line to data/daemon-restart.log.

    capture_evidence() tails this file, so every recovery action shows up in the
    next wedge snapshot — the forensic trail survives the restart.
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / "daemon-restart.log").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} [watchdog] {msg}\n")
    except Exception:
        pass


def _clear_wedge_flag() -> None:
    """Remove the wedge-alert flag once the daemon is confirmed healthy again."""
    try:
        flag = DATA_DIR / "wedge-alert.flag"
        if flag.exists():
            flag.unlink()
    except Exception:
        pass


def kill_pid_tree(pid: int) -> str:
    """Force-kill the daemon process and its child tree — and ONLY that tree.

    Targeted by PID, never a blanket `taskkill /IM python.exe`. The /T flag
    cascades to the daemon's plugin-spawned children (the :9001 dogfood sidecar,
    sandbox-pool members, any plugin auto_start child) but leaves the watchdog,
    Ollama/ComfyUI/voice-api (separate process trees), and agent-runtime
    DETACHED children untouched. See .claude/rules/daemon-handling.md.
    """
    if sys.platform == "win32":
        return run_cmd(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=20)
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        return f"pid {pid} already gone"
    except Exception as e:
        return f"kill failed: {e!r}"
    return f"sent SIGTERM/SIGKILL to {pid}"


def wait_for_port_free(port: int, timeout: float) -> bool:
    """Block until nothing LISTENs on `port`, re-killing any stubborn listener.

    Fixes the WinError 10048 race: after kill_pid_tree the old listener can
    linger for a second or two (slow-dying process, plugin children), and a
    respawn that binds during that window dies immediately on
    'only one usage of each socket address'. Returns True if the port freed,
    False if it was still held when the timeout elapsed.
    """
    deadline = time.time() + max(0.0, timeout)
    while True:
        pid = find_listening_pid(port)
        if pid is None:
            return True
        if time.time() >= deadline:
            return False
        # Still held — give the dying process another targeted nudge.
        log(f"  :{port} still held by pid {pid} — re-killing, waiting for release")
        kill_pid_tree(pid)
        time.sleep(1.5)


def start_daemon_detached(start_cmd: list[str]) -> int | None:
    """Respawn the daemon detached so it outlives the watchdog.

    DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (POSIX: start_new_session) keeps
    the new daemon out of the watchdog's console group, so neither a watchdog
    Ctrl+C nor the watchdog dying takes it down. It's still a plain python.exe,
    so the next restart.bat (`taskkill /IM python.exe`) remains the clean master
    reset. Output goes to data/watchdog-respawn.log (detached procs have no
    console to inherit).
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logf = (DATA_DIR / "watchdog-respawn.log").open("ab")
        logf.write(
            f"\n\n===== respawn {datetime.now().isoformat()} : "
            f"{' '.join(start_cmd)} =====\n".encode("utf-8")
        )
        logf.flush()
    except Exception as e:
        log(f"respawn log open failed: {e!r}")
        logf = None

    kwargs: dict = {}
    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS (0x8) | CREATE_NEW_PROCESS_GROUP (0x200)
        creationflags = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        p = subprocess.Popen(
            list(start_cmd),
            cwd=str(REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=logf or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if logf else subprocess.DEVNULL,
            env=os.environ.copy(),
            creationflags=creationflags,
            **kwargs,
        )
        return p.pid
    except Exception as e:
        log(f"respawn failed: {e!r}")
        return None


def _flag_give_up(args, restart_times: list[float]) -> None:
    """Latch a give-up alert — overwrite the wedge flag + push to Telegram."""
    tail = (
        f"backing off {args.giveup_cooldown:.0f}s then retrying automatically"
        if getattr(args, "giveup_cooldown", 0) > 0
        else "manual restart.bat needed"
    )
    msg = (
        f"🛑 EmptyOS :{args.port} watchdog hit the storm guard — {len(restart_times)} "
        f"restarts within {args.restart_window:.0f}s did not hold; {tail}."
    )
    try:
        (DATA_DIR / "wedge-alert.flag").write_text(
            json.dumps(
                {
                    "at": datetime.now().isoformat(),
                    "port": args.port,
                    "gave_up": True,
                    "restarts": len(restart_times),
                    "message": msg,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        log(f"give-up flag write failed: {e!r}")
    _send_telegram(args, msg)


# ─── Single-instance recovery lock ───────────────────────────────────────────
# At most ONE --restart watchdog per port may kill+respawn the daemon. Two would
# fight on the same wedge (double-kill, racing respawns → port conflict). A
# second --restart watchdog that finds a live owner downgrades to evidence-only.
# Stale locks (owner PID dead — e.g. killed by restart.bat's taskkill) are
# reclaimed. Evidence-only watchdogs never take the lock, so any number can run.

def _recovery_lock_path(port: int) -> Path:
    return DATA_DIR / f"watchdog-recovery-{port}.lock"


def _pid_alive(pid: int) -> bool:
    """True only if `pid` is a live python process (guards against PID reuse)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return str(pid) in out and "python" in out.lower()
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def _acquire_recovery_lock(port: int) -> bool:
    """Claim recovery ownership for `port`. False if a live owner already holds it.

    Fail-safe: any error returns False (don't become a second killer)."""
    path = _recovery_lock_path(port)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            holder = int(data.get("pid", 0))
            if holder and holder != os.getpid() and _pid_alive(holder):
                return False
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"pid": os.getpid(), "port": port, "started_at": datetime.now().isoformat()}
            ),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        log(f"recovery-lock check failed ({e!r}) — staying evidence-only to be safe")
        return False


def _release_recovery_lock(port: int) -> None:
    path = _recovery_lock_path(port)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("pid", 0)) == os.getpid():
                path.unlink()
    except Exception:
        pass


# ─── Boot-vs-crash discrimination ─────────────────────────────────────────────
# "Nothing is LISTENING on :9000" has two causes that demand opposite responses:
# the daemon crashed (respawn it) or the daemon we already respawned is still
# booting (wait). The port cannot tell them apart — only the child PID can, which
# is why start_daemon_detached's return value is now kept rather than just logged.

def boot_still_running(
    spawned_pid: int | None,
    elapsed: float,
    boot_timeout: float,
    *,
    alive: Callable[[int], bool] | None = None,
) -> bool:
    """True when our last respawn is alive but hasn't bound the port yet.

    The grace window is a *guess* at boot time; process liveness is the fact.
    Bounded by `boot_timeout` so a boot that is genuinely hung (not merely slow)
    is still eventually killed and retried rather than waited on forever.
    See the module docstring for what respawning on top of a live boot costs.
    """
    if spawned_pid is None or boot_timeout <= 0 or elapsed >= boot_timeout:
        return False
    return (alive or _pid_alive)(spawned_pid)


def pids_to_clear(
    listening_pid: int | None,
    spawned_pid: int | None,
    *,
    alive: Callable[[int], bool] | None = None,
) -> list[int]:
    """Every daemon PID that must die before a respawn — listener first.

    The listener is the wedged daemon. `spawned_pid` is our own previous
    respawn, which may be alive-but-not-listening (a boot that never finished).
    Killing only the listener leaves that one running, which is precisely how
    boots pile up. Deduped; empty when there is nothing to kill.
    """
    out: list[int] = []
    if listening_pid:
        out.append(listening_pid)
    if spawned_pid and spawned_pid not in out and (alive or _pid_alive)(spawned_pid):
        out.append(spawned_pid)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--interval", type=float, default=30.0, help="poll interval seconds")
    ap.add_argument("--probe-timeout", type=float, default=5.0)
    ap.add_argument(
        "--fail-threshold",
        type=int,
        default=2,
        help="consecutive failures before capturing evidence",
    )
    ap.add_argument(
        "--telegram-token",
        default=None,
        help="Override [plugins.telegram] bot_token for wedge alerts",
    )
    ap.add_argument(
        "--telegram-chat",
        default=None,
        help="Override [plugins.telegram] chat_id for wedge alerts",
    )
    ap.add_argument(
        "--restart",
        action="store_true",
        help="auto-restart the daemon on a confirmed wedge/crash "
        "(default off — evidence only). restart.bat passes this.",
    )
    ap.add_argument(
        "--restart-grace",
        type=float,
        default=240.0,
        help="seconds to wait for health after a respawn before reassessing. "
        "Only decides when to LOOK; if the respawned process is still alive the "
        "wait is extended (see --boot-timeout), so this need not cover a "
        "worst-case boot. Default 240.",
    )
    ap.add_argument(
        "--boot-timeout",
        type=float,
        default=900.0,
        help="ceiling on how long a respawned daemon may take to answer /api/health "
        "while its process is still alive. Past this it is treated as hung, killed "
        "and retried (default 900 = 15 min). Set 0 to disable the extension and "
        "reassess strictly at --restart-grace.",
    )
    ap.add_argument(
        "--max-restarts",
        type=int,
        default=3,
        help="respawns allowed within --restart-window before giving up "
        "(storm guard; default 3)",
    )
    ap.add_argument(
        "--restart-window",
        type=float,
        default=1800.0,
        help="storm window in seconds for --max-restarts (default 1800 = 30 min)",
    )
    ap.add_argument(
        "--giveup-cooldown",
        type=float,
        default=300.0,
        help="after exhausting the storm budget, wait this long then RESUME "
        "recovery attempts instead of staying silent forever (default 300 = 5 "
        "min). Set 0 to keep the old permanent-give-up behaviour.",
    )
    ap.add_argument(
        "--port-free-timeout",
        type=float,
        default=20.0,
        help="after killing the wedged daemon, wait up to this long for :PORT to "
        "stop LISTENING before respawning, so the new daemon doesn't hit "
        "WinError 10048 on bind (default 20s)",
    )
    ap.add_argument(
        "--no-notify",
        action="store_true",
        help="suppress Telegram wedge/give-up alerts (still writes the local "
        "flag file); useful for quiet runs and tests",
    )
    ap.add_argument(
        "--start-cmd",
        nargs=argparse.REMAINDER,
        default=None,
        help="command to (re)start the daemon, captured verbatim incl. its own "
        "flags — MUST be the last argument (default: <python> -m emptyos start)",
    )
    args = ap.parse_args()
    if not args.start_cmd:
        args.start_cmd = [sys.executable, "-m", "emptyos", "start"]

    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)

    # Single-instance guard: only one --restart watchdog may own recovery per
    # port. A second one downgrades to evidence-only so two killers can't fight.
    if args.restart:
        if _acquire_recovery_lock(args.port):
            import atexit
            atexit.register(_release_recovery_lock, args.port)
        else:
            log(
                f"another recovery watchdog already owns :{args.port} — "
                f"downgrading THIS one to evidence-only"
            )
            args.restart = False

    log(
        f"watchdog → :{args.port} every {args.interval}s "
        f"(probe timeout {args.probe_timeout}s, threshold {args.fail_threshold}, "
        f"restart={'on' if args.restart else 'off'})"
    )

    consecutive_fail = 0
    first_wedge_seen: float | None = None
    last_healthy: float | None = None
    captured_for_this_wedge = False

    # Recovery (auto-restart) state — only active with --restart.
    restart_times: list[float] = []   # epoch of each respawn within the window
    awaiting_recovery = False          # a respawn is in flight; allow boot grace
    recovery_deadline = 0.0            # respawn must be healthy by this time
    spawned_pid: int | None = None     # PID of our last respawn — the only way to
                                       # tell "still booting" from "crashed"
    gave_up = False                    # storm budget exhausted; in back-off cooldown
    gave_up_at = 0.0                   # epoch we entered back-off (for --giveup-cooldown)

    while True:
        ok, detail = probe_health(args.port, args.probe_timeout)
        now = time.time()

        if ok:
            if awaiting_recovery:
                booted_in = now - (restart_times[-1] if restart_times else now)
                log(f"RECOVERED via restart — healthy after {booted_in:.0f}s")
                _clear_wedge_flag()
            elif consecutive_fail >= args.fail_threshold:
                wedged_for = now - (first_wedge_seen or now)
                log(f"RECOVERED — was wedged for {wedged_for:.0f}s")
                _clear_wedge_flag()
            consecutive_fail = 0
            first_wedge_seen = None
            captured_for_this_wedge = False
            awaiting_recovery = False
            gave_up = False
            spawned_pid = None
            last_healthy = now
            time.sleep(args.interval)
            continue

        # --- not healthy ---
        # While a respawn is booting, hold fire until the grace deadline so the
        # new daemon's boot window isn't mistaken for a fresh wedge.
        if awaiting_recovery:
            if now < recovery_deadline:
                log(f"(restart booting — {recovery_deadline - now:.0f}s grace left, {detail})")
                time.sleep(args.interval)
                continue
            # Grace expired. Before calling it a failure, ask the process itself:
            # a live respawn that hasn't bound the port yet is booting, not dead,
            # and respawning on top of it is what turns one slow boot into a storm.
            booting_for = now - (restart_times[-1] if restart_times else now)
            if boot_still_running(spawned_pid, booting_for, args.boot_timeout):
                log(
                    f"(pid {spawned_pid} alive but not serving after {booting_for:.0f}s "
                    f"— still booting, extending grace {args.restart_grace:.0f}s)"
                )
                recovery_deadline = now + args.restart_grace
                time.sleep(args.interval)
                continue
            # Genuinely down (process gone, or hung past --boot-timeout).
            log(
                f"restart did not become healthy within grace "
                f"({booting_for:.0f}s elapsed) — reassessing"
            )
            awaiting_recovery = False
            # fall through to the recovery decision below (counts toward storm)

        consecutive_fail += 1
        if first_wedge_seen is None:
            first_wedge_seen = now
        log(
            f"FAIL #{consecutive_fail} ({detail}) — "
            f"wedged for {now - first_wedge_seen:.0f}s"
        )

        wedge_confirmed = (
            consecutive_fail >= args.fail_threshold and last_healthy is not None
        )

        if wedge_confirmed and not captured_for_this_wedge:
            pid = find_listening_pid(args.port)
            log(f"capturing evidence (daemon pid={pid})")
            try:
                out = capture_evidence(args.port, pid, first_wedge_seen, last_healthy)
                log(f"→ {out}")
                _notify_wedge(args, out, pid, first_wedge_seen, last_healthy)
            except Exception as e:
                log(f"capture failed: {e!r}")
            captured_for_this_wedge = True

        # Back-off resume: if we exhausted the storm budget but --giveup-cooldown
        # has elapsed, resume recovery instead of staying silent forever. A daemon
        # that's fully down never returns to a healthy state to clear gave_up, so
        # without this the old behaviour meant "one bad storm = guardian gives up
        # permanently". Set --giveup-cooldown 0 to keep the old behaviour.
        if gave_up and args.giveup_cooldown > 0 and (now - gave_up_at) >= args.giveup_cooldown:
            log(
                f"back-off elapsed ({args.giveup_cooldown:.0f}s) — RESUMING recovery attempts"
            )
            _append_restart_log(f"back-off elapsed; resuming recovery on :{args.port}")
            gave_up = False
            restart_times = []

        # Auto-recovery decision — only with --restart, always AFTER evidence.
        if args.restart and wedge_confirmed and not awaiting_recovery and not gave_up:
            restart_times = [t for t in restart_times if now - t < args.restart_window]
            if len(restart_times) >= args.max_restarts:
                gave_up = True
                gave_up_at = now
                cooldown_note = (
                    f"backing off {args.giveup_cooldown:.0f}s then retrying"
                    if args.giveup_cooldown > 0
                    else "manual restart.bat needed"
                )
                log(
                    f"GIVING UP for now — {len(restart_times)} restarts within "
                    f"{args.restart_window:.0f}s did not hold; {cooldown_note}."
                )
                _flag_give_up(args, restart_times)
            else:
                pid = find_listening_pid(args.port)
                state = "wedged" if pid else "dead"
                log(f"RECOVERY: daemon {state} (pid={pid}) — restarting")
                _append_restart_log(f"recovery: {state} pid={pid} port={args.port}")
                # Listener + any earlier respawn of ours still alive; see pids_to_clear.
                doomed = pids_to_clear(pid, spawned_pid)
                for victim in doomed:
                    res = kill_pid_tree(victim)
                    tail = res.splitlines()[-1] if res else "done"
                    label = "listener" if victim == pid else "orphaned boot"
                    _append_restart_log(f"kill {label} pid={victim}: {tail}")
                    log(f"killed pid tree {victim} ({label})")
                if doomed:
                    log(f"waiting for :{args.port} to release")
                    time.sleep(3.0)  # let SQLite WAL handles release (daemon-handling rule)
                # Block on the port actually freeing so the respawn can't hit
                # WinError 10048 binding a still-held :PORT.
                if not wait_for_port_free(args.port, args.port_free_timeout):
                    log(
                        f"  WARNING :{args.port} still held after "
                        f"{args.port_free_timeout:.0f}s — respawning anyway (may 10048)"
                    )
                    _append_restart_log(f"port {args.port} still held after wait; respawning anyway")
                new_pid = start_daemon_detached(args.start_cmd)
                _append_restart_log(f"respawned detached pid={new_pid}")
                log(f"respawned daemon (detached pid={new_pid}); grace {args.restart_grace:.0f}s")
                spawned_pid = new_pid
                restart_times.append(now)
                awaiting_recovery = True
                recovery_deadline = now + args.restart_grace
                # Reset wedge bookkeeping so a NEW post-grace wedge re-triggers.
                consecutive_fail = 0
                first_wedge_seen = None
                captured_for_this_wedge = False
        elif (
            consecutive_fail >= args.fail_threshold
            and last_healthy is None
            and not captured_for_this_wedge
        ):
            # Boot-grace: daemon hasn't been healthy since watchdog started.
            # Treat as "still booting" rather than wedged. Logged once.
            log("(holding fire — daemon never reached healthy state; treating as still booting)")
            captured_for_this_wedge = True  # don't keep logging this line

        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        print("\n(watchdog stopped)", flush=True)
        sys.exit(0)
