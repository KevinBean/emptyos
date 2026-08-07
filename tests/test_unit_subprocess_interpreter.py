"""Every python subprocess must use sys.executable, not bare "python".

PATH `python` may resolve to a different interpreter than the one running the
daemon -- this machine carries 3.13 and 3.11, and CLAUDE.md § Testing already
warns about the identical trap for `pytest`. Two gates were exposed to it:

  - `py_compile_files` (emptyos/sdk/worktree.py) gates EVERY fix-agent merge. A
    syntax check under the wrong Python is worse than none: it reports green for
    source the daemon cannot import, or red for syntax the daemon accepts.
  - `release-public.py` spawns its scan/generator scripts, so a public release
    could be validated by an interpreter it does not ship against.

Both were latent rather than live (PATH resolved correctly at the time), which
is exactly why a test is worth more than a fix alone.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Files that spawn a Python interpreter as a gate. Add to this list rather than
# widening the scan -- a repo-wide sweep would fight docs, templates, and the
# products/ launchers, which resolve their own embedded interpreters on purpose.
GATE_FILES = [
    ROOT / "emptyos/sdk/worktree.py",
    ROOT / "scripts/release-public.py",
    ROOT / "apps/extension/dev/fix-agent/regression.py",
]


def _bare_python_argv0(path: Path) -> list[tuple[int, str]]:
    """Line numbers where a list literal starts with the string "python"."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:  # pragma: no cover - would fail the suite loudly
        pytest.fail(f"{path.name} does not parse: {exc}")
    bad: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            continue
        first = node.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if first.value.lower() in {"python", "python3", "python.exe"}:
                bad.append((getattr(node, "lineno", 0), first.value))
    return bad


@pytest.mark.parametrize("path", GATE_FILES, ids=lambda p: p.name)
def test_no_bare_python_interpreter(path):
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    bad = _bare_python_argv0(path)
    assert not bad, (
        f"{path.relative_to(ROOT)} spawns a bare interpreter at line(s) "
        f"{[ln for ln, _ in bad]} — use sys.executable so the subprocess is the "
        "same Python as the caller"
    )


def test_py_compile_files_uses_the_running_interpreter():
    """Behavioural, not just textual — the helper must actually shell to us."""
    from emptyos.sdk.worktree import py_compile_files

    good = ROOT / "tests" / "test_unit_subprocess_interpreter.py"
    ok, msg = py_compile_files([str(good)], cwd=ROOT)
    assert ok, f"expected this very file to compile cleanly: {msg}"


def test_py_compile_files_still_reports_a_syntax_error(tmp_path):
    from emptyos.sdk.worktree import py_compile_files

    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n", encoding="utf-8")
    ok, msg = py_compile_files([str(bad)], cwd=tmp_path)
    assert ok is False and msg, "a SyntaxError must still fail the gate"


def test_scan_would_catch_a_regression(tmp_path):
    """Pin the detector itself, so a no-op scan can't read as a pass."""
    f = tmp_path / "sample.py"
    f.write_text('import subprocess\nsubprocess.run(["python", "-m", "x"])\n', encoding="utf-8")
    assert _bare_python_argv0(f), "detector failed to flag a known-bad shape"
    f.write_text('import subprocess, sys\nsubprocess.run([sys.executable, "-m", "x"])\n', encoding="utf-8")
    assert not _bare_python_argv0(f), "detector false-positives on the fixed shape"


def test_sys_executable_is_a_real_interpreter():
    assert sys.executable and Path(sys.executable).exists()
