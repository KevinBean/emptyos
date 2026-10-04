"""Vault-index scan resilience — a single unreadable-without-blocking file
must not wedge the boot-time scan.

Root cause it pins (2026-07-13): a 13k-note vault under iCloud had 1,164
dataless placeholders (regular files, SF_DATALESS set, no `.icloud` name).
`read_text()` on one triggered a synchronous cloud materialize that blocked
`read()` forever — and the scan is a boot gate, so the daemon never bound :9000.
No exception, so the reader's try/except couldn't catch it.

The fix: skip files whose read would BLOCK rather than error — non-regular
files (FIFO/socket/device) and macOS dataless placeholders — counting them so
the boot logs a loud warning instead of hanging.
"""

from __future__ import annotations

import os
import stat as _stat
import types

import pytest

from emptyos.runtime.vault_index import _SF_DATALESS, _read_may_block


def _fake_stat(mode, flags=0):
    return types.SimpleNamespace(st_mode=mode, st_flags=flags)


# ── the pure predicate (cross-platform, no filesystem) ───────────────
def test_regular_file_is_readable():
    assert _read_may_block(_fake_stat(_stat.S_IFREG | 0o644)) is False


def test_dataless_placeholder_skipped():
    st = _fake_stat(_stat.S_IFREG | 0o644, flags=_SF_DATALESS)
    assert _read_may_block(st) is True


def test_dataless_bit_among_other_flags():
    # Other st_flags bits set alongside SF_DATALESS must still trip the guard.
    st = _fake_stat(_stat.S_IFREG | 0o644, flags=_SF_DATALESS | 0x1)
    assert _read_may_block(st) is True


@pytest.mark.parametrize("mode", [_stat.S_IFIFO, _stat.S_IFSOCK, _stat.S_IFCHR, _stat.S_IFBLK])
def test_non_regular_files_skipped(mode):
    assert _read_may_block(_fake_stat(mode | 0o644)) is True


def test_missing_st_flags_is_noop_on_windows_linux():
    # os.stat has no st_flags on Windows/Linux → getattr(...,0) → not skipped.
    st = types.SimpleNamespace(st_mode=_stat.S_IFREG | 0o644)  # no st_flags attr
    assert _read_may_block(st) is False


# ── integration: a real FIFO in a scanned vault is skipped, not hung ──
def test_scan_skips_fifo_and_indexes_regular(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("mkfifo unavailable on this platform")

    from emptyos.runtime.vault_index import VaultIndex

    # A normal note...
    (tmp_path / "good.md").write_text("---\ntags:\n  - kb\n---\n# Good\n", encoding="utf-8")
    # ...and a FIFO named like a note. read_text() on it would block forever;
    # the scan must skip it (S_ISREG false) instead of hanging.
    os.mkfifo(tmp_path / "trap.md")

    idx = VaultIndex.__new__(VaultIndex)   # bypass __init__ (needs a kernel)
    idx._vault = tmp_path
    idx._skipped_blocking = 0

    files, by_tag = idx._scan_all()

    assert "good.md" in files
    assert "trap.md" not in files          # the FIFO was skipped, not read
    assert idx._skipped_blocking == 1
    assert "kb" in by_tag and "good.md" in by_tag["kb"]
