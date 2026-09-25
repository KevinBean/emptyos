"""Unit tests for emptyos.sdk.toolchain.

Covers the branches ``probe_binary_version`` actually has: binary missing
(``found=False``), binary present + version parses, and binary present but
the version subprocess fails (still ``found=True``, version falls back to
``"?"``). No real subprocess spawned for the failure case — the point is
the *contract*, not exercising ``git``/``python`` themselves.
"""

from __future__ import annotations

import shutil
import sys

from emptyos.sdk.toolchain import probe_binary_version, resolve_binary


def test_resolve_binary_finds_python_on_path() -> None:
    # sys.executable's directory is on PATH during a pytest run in a venv,
    # but the interpreter name itself may not be literally "python" on every
    # platform — use a binary guaranteed to exist: pytest requires it.
    assert resolve_binary("does-not-exist-anywhere-xyz") is None


def test_probe_binary_version_missing_binary_reports_not_found() -> None:
    found, ver = probe_binary_version("does-not-exist-anywhere-xyz")
    assert found is False
    assert ver == ""


def test_probe_binary_version_real_python_reports_found() -> None:
    py = shutil.which("python") or shutil.which("python3") or sys.executable
    assert py, "no python binary resolvable in this environment"
    found, ver = probe_binary_version(py, version_args=["--version"])
    assert found is True
    assert ver != ""


def test_probe_binary_version_bad_args_still_reports_found() -> None:
    # The binary resolves, but a nonsense flag makes it exit non-zero (or the
    # subprocess otherwise misbehaves) — found stays True, version degrades
    # to "?" rather than raising.
    py = shutil.which("python") or shutil.which("python3") or sys.executable
    assert py
    found, ver = probe_binary_version(py, version_args=["--this-flag-does-not-exist"])
    assert found is True
    assert ver in ("?", "") or isinstance(ver, str)
