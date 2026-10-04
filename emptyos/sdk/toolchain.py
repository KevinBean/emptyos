"""External binary detection + version probing — pure subprocess wrappers.

Three ``forge`` targets (``cli.py``, ``native_android.py``, ``tauri.py``)
each pasted the same "resolve a binary on PATH, run it with ``--version``,
suppress the console window Windows pops for a subprocess, and fall back to
``"?"`` on any failure" logic verbatim. Collapsed here so a 4th Target (or
any other app doing external-tool preflight — see CLAUDE.md "External
Service Launch Pattern") reuses it instead of copying it a 4th time.

Pure functions, no ``self``: callers pass a binary name and get back
whether it resolved + a one-line version string. Anything app-specific
(install hints, hint URLs, the shape of the result object) stays in the
calling app.

When NOT to use:
    - You need a long-running or interactive subprocess. This is a
      point-in-time, timeout-bounded probe (default 5s) — no streaming.
    - You want the *path* to the binary, not its version. Use
      ``resolve_binary`` alone (or bare ``shutil.which``) in that case.
"""

from __future__ import annotations

import shutil
import subprocess

from emptyos.headless import no_window_flags


def resolve_binary(name: str) -> str | None:
    """``shutil.which(name)`` — named so a call site reads as intent
    ("resolve this binary") rather than a bare stdlib call."""
    return shutil.which(name)


def probe_binary_version(
    name: str,
    *,
    version_args: tuple[str, ...] | list[str] = ("--version",),
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """Resolve ``name`` on PATH and run it with ``version_args`` to read a
    one-line version string.

    Returns ``(found, version)``. ``found`` is ``False`` only when the
    binary isn't on PATH at all — a version-probe subprocess failure (bad
    args, non-zero exit, timeout) still reports ``(True, "?")`` since the
    caller already knows the tool exists, it just couldn't be versioned.

    Suppresses the console window ``subprocess`` would otherwise flash on
    Windows (``CREATE_NO_WINDOW``); a no-op elsewhere.
    """
    path = resolve_binary(name)
    if not path:
        return False, ""
    try:
        out = subprocess.run(
            [path, *version_args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=no_window_flags(),
        )
        ver = (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else "?"
    except Exception:
        ver = "?"
    return True, ver
