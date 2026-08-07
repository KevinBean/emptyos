"""Every ``syslog.<level>()`` call in the repo must name a method that exists.

``SystemLog`` shipped ``warn`` and no ``warning``. Stdlib ``logging`` is the
other way round -- ``warning`` is the real one and ``warn`` the deprecated
alias -- so trained fingers write ``.warning`` and Python raises
``AttributeError`` at the call. Six sites in ``plugins/comfyui/plugin.py`` had
done exactly that, and every one of them sat on an error path: a missing
workflow file, a failed GPU pre-flight, an unreachable server. The diagnostic
line meant to explain a failure was itself a second failure, and it could only
fire once something else had already gone wrong, which is why nothing caught it.

``warning`` is now an alias, so both spellings work. These tests pin the alias
and, more importantly, sweep the repo -- a seventh site spelled some third way
fails here instead of in production.
"""

from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path

import pytest

from emptyos.kernel.syslog import SystemLog

REPO = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("apps", "plugins", "emptyos", "scripts")

# Names reached through a syslog handle that are not severity levels.
NON_LEVEL = {"log", "query", "trim", "flush"}


@pytest.fixture
def slog(tmp_path):
    return SystemLog(tmp_path / "syslog.db")


def test_warning_is_accepted(slog):
    """The spelling six real call sites used must not raise."""
    slog.warning("test", "hello")
    assert [r["message"] for r in slog.query(limit=1)] == ["hello"]


def test_warning_stores_the_same_level_as_warn(slog):
    """An alias, not a new level -- existing queries and rows stay valid."""
    slog.warn("test", "a")
    slog.warning("test", "b")
    levels = {r["level"] for r in slog.query(limit=2)}
    assert levels == {"warn"}, f"alias introduced a second level: {levels}"


def test_warning_forwards_kwargs(slog):
    """The alias must be the method itself, not a lossy wrapper."""
    assert SystemLog.warning is SystemLog.warn


def _syslog_level_calls() -> list[tuple[Path, int, str]]:
    """Every ``....syslog.<name>(`` in the tree, as (file, line, name).

    A regex rather than an AST walk on purpose: the handle is reached by many
    shapes (``self.kernel.syslog``, ``plugin.kernel.syslog``, ``kernel.syslog``,
    a local alias), and the attribute *name* is the whole question. AST would
    have to resolve the receiver to do better, and cannot -- these are runtime
    objects.
    """
    pat = re.compile(r"\bsyslog\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    out = []
    for d in SCAN_DIRS:
        for f in (REPO / d).rglob("*.py"):
            if "_retired" in f.parts or "node_modules" in f.parts:
                continue
            try:
                src = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(src.splitlines(), 1):
                for m in pat.finditer(line):
                    out.append((f, i, m.group(1)))
    return out


def test_the_sweep_actually_finds_calls():
    """Guard the guard: a regex that matches nothing would pass silently."""
    calls = _syslog_level_calls()
    assert len(calls) > 50, (
        f"only {len(calls)} syslog calls found across {SCAN_DIRS} — the pattern "
        f"has probably stopped matching, which would make the sweep below vacuous"
    )


def test_every_syslog_call_names_a_real_method():
    bad = [
        (f.relative_to(REPO).as_posix(), ln, name)
        for f, ln, name in _syslog_level_calls()
        if not hasattr(SystemLog, name)
    ]
    assert not bad, "syslog calls that would raise AttributeError:\n" + "\n".join(
        f"  {p}:{ln}  syslog.{name}(...)" for p, ln, name in bad
    )


def test_known_level_names_are_all_present():
    """The set a caller may reasonably reach for, pinned so a rename is loud."""
    for name in ("debug", "info", "warn", "warning", "error"):
        assert callable(getattr(SystemLog, name, None)), f"SystemLog.{name} missing"


def test_log_survives_a_locked_database(slog):
    """A transient 'database is locked' inside log() must not propagate.

    Regression for 2026-08-01: app_loader's harmless "slow load" diagnostic
    hit a stale WAL lock right after a watchdog respawn and took down an
    otherwise-fine app load with it (RuntimeError: Failed to load app
    'journal': database is locked) — the exception came from the logging
    call itself, not from the app. See data/wedge-evidence/20260801T080139Z.

    A closed connection raises sqlite3.ProgrammingError on execute() — a
    real sqlite3.Error subclass, standing in for the OperationalError a
    live lock would raise, without needing to fake WAL contention.
    """
    slog._conn.close()
    slog.log("warn", "app_loader", "slow load 'journal': setup=4792ms")  # must not raise


def test_setter_free_module_import_is_cheap():
    """A bare script (the depth guard, a hook) imports this without the web stack."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c", "from emptyos.kernel.syslog import SystemLog; print('ok')"],
        capture_output=True, text=True, cwd=REPO, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout
