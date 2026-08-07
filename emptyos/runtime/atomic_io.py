"""Crash-safe replacement writes shared by platform and app code.

Atomic replacement prevents readers from observing a partially written file.
It does not serialize read-modify-write sequences; callers still need the
resource lock that owns their logical update.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


def _temporary_path(target: Path) -> tuple[int, Path]:
    """Create a unique sibling temp, owner-only until proven otherwise.

    0o600 is the starting point, not the umask default (0o666), so a
    brand-new file this helper writes never has a wider-than-owner mode
    unless ``atomic_write_bytes`` explicitly widens it to match an existing
    target. This mirrors the one other explicit permission policy in the
    codebase (``emptyos/sdk/autopilot.py``'s secrets key file) rather than
    inheriting the process umask, which on a shared or containerized POSIX
    host would otherwise make new vault content world-readable by default.
    """
    for _ in range(20):
        candidate = target.parent / (
            f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            return fd, candidate
        except FileExistsError:
            continue
    raise FileExistsError(f"could not allocate a temporary sibling for {target}")


def _sync_parent_best_effort(parent: Path) -> None:
    """Persist the rename where directory fsync is supported."""
    if os.name == "nt":
        return
    fd: int | None = None
    try:
        fd = os.open(parent, os.O_RDONLY)
        os.fsync(fd)
    except OSError:
        # Once replace succeeds, a sync error must not invite a duplicate retry.
        pass
    finally:
        if fd is not None:
            os.close(fd)


def atomic_write_bytes(path: str | Path, content: bytes) -> Path:
    """Replace ``path`` through an fsynced sibling temp file.

    Existing permission bits survive; a brand-new file defaults to owner-only
    (0o600) rather than the process umask — see :func:`_temporary_path`.
    Directory fsync is best-effort because Windows and some mounts reject it.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        existing_mode = None

    fd, temporary = _temporary_path(target)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        os.replace(temporary, target)
        temporary = None
        _sync_parent_best_effort(target.parent)
        return target
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(
    path: str | Path, content: str, *, encoding: str = "utf-8"
) -> Path:
    """Encode ``content`` and commit it with :func:`atomic_write_bytes`."""
    return atomic_write_bytes(path, content.encode(encoding))
