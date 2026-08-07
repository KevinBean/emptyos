"""Record which process IS this daemon, so a restart can kill it by name.

`restart.bat` and the Settings restart button both reached for
`taskkill /F /IM python.exe` — every Python process on the machine. That takes
down ComfyUI mid-render, voice-api, the pronounce service, sandbox-pool
members, and whatever script the user happened to be running, to stop one
daemon whose PID was sitting in `os.getpid()` the whole time. The daemon simply
never wrote it down.

Identity, not just a number. A bare PID is not enough: PIDs are recycled, so a
stale record can point at an unrelated process, and force-killing *that* is
worse than the blanket kill it was meant to replace. The record carries the
process creation time, so a recycled PID fails verification and the caller is
told "unknown" rather than handed the wrong tree. Same shape as the ownership
record in `plugins/external-lab-host` — PID + create_time + port + identity, and
ownership is accepted only when they all agree.

`verify_owner` is the only function a killer should call. It returns a PID
solely when that process really is this vault's daemon.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

IDENTITY = "emptyos-daemon"
PIDFILE_NAME = "daemon.pid.json"


def _pidfile(data_dir: Path | str) -> Path:
    return Path(data_dir) / PIDFILE_NAME


def process_create_time(pid: int) -> float | None:
    """Creation time of `pid`, or None if it is gone or unreadable.

    This is the PID-recycling guard. Without it a stale record is a loaded gun
    pointed at whatever process inherited the number.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except Exception:
        return None


def write_pidfile(data_dir: Path | str, *, port: int, pid: int | None = None) -> dict[str, Any] | None:
    """Record this process as the daemon. Returns the record, or None if it
    could not be verified well enough to be safe to act on later.

    Never raises: failing to write a convenience record must not stop a boot.
    """
    pid = os.getpid() if pid is None else pid
    created = process_create_time(pid)
    if created is None:
        # Without a creation time the record cannot be distinguished from a
        # recycled PID later, so writing it would invite exactly the wrong kill.
        return None
    record = {
        "identity": IDENTITY,
        "pid": pid,
        "create_time": created,
        "port": int(port),
    }
    try:
        path = _pidfile(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return None
    return record


def read_pidfile(data_dir: Path | str) -> dict[str, Any] | None:
    """Read the record verbatim. No verification — use `verify_owner` to act."""
    try:
        raw = json.loads(_pidfile(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def verify_owner(data_dir: Path | str, *, port: int | None = None) -> int | None:
    """Return the daemon's PID only if the recorded process really is it.

    Returns None when the record is missing, malformed, names a dead process,
    names a *different* process that reused the PID, or was written for another
    port. A caller that gets None must fall back to something safe — asking the
    user — and must NOT widen its aim to every Python process.
    """
    record = read_pidfile(data_dir)
    if not record or record.get("identity") != IDENTITY:
        return None
    try:
        pid = int(record["pid"])
        recorded_created = float(record["create_time"])
    except (KeyError, TypeError, ValueError):
        return None
    if port is not None and int(record.get("port", -1)) != int(port):
        return None
    actual_created = process_create_time(pid)
    if actual_created is None:
        return None  # process is gone, or psutil is unavailable — fail closed
    # Filesystem timestamps and psutil can disagree in the last decimals.
    if abs(actual_created - recorded_created) > 1.0:
        return None  # PID was recycled — this is somebody else
    return pid


def clear_pidfile(data_dir: Path | str) -> None:
    """Remove the record on a clean shutdown. Never raises."""
    try:
        _pidfile(data_dir).unlink(missing_ok=True)
    except OSError:
        pass
