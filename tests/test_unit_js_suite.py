"""Bridge: run the `node --test` JS suite from inside pytest.

WHY A BRIDGE INSTEAD OF A SECOND CI JOB
---------------------------------------
The shared page bundle (`emptyos/web/static/*.js`) carries real logic —
escaping, ability comparison, status-variant mapping, the state store — and
none of it was testable. Browser tests cannot cover it: CI never runs
`playwright install`, and `.github/workflows/tests.yml` excludes interactive
tests outright, so a Playwright-based harness would gate nothing.

Node's built-in runner needs no npm packages and no browser, and GitHub's
ubuntu runners ship Node already. Routing it through pytest means the existing
"offline architecture guards" step picks it up with one added filename, and a
developer running the suite locally gets it for free — one test world, not two.

Skips (never fails) when Node is missing, so a Python-only checkout stays green.
The JS tests themselves skip per-file when a personal app is absent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "tests" / "js"


def _test_files() -> list[str]:
    """Explicit file list, not the directory.

    `node --test <dir>` is not a stable interface across Node versions — 22.17
    tries to *load the directory as a module* and dies with MODULE_NOT_FOUND,
    which reads like a broken harness rather than a bad argument. Naming files
    also means a new .test.mjs cannot be silently skipped by a discovery quirk.
    """
    return sorted(str(p) for p in JS_DIR.glob("*.test.mjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_suite_passes():
    files = _test_files()
    assert files, f"no *.test.mjs found in {JS_DIR} — the glob or the layout moved"

    proc = subprocess.run(
        [shutil.which("node"), "--test", *files],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        # Surface node's own report; a bare "exit 1" is useless for triage.
        pytest.fail(
            "node --test failed:\n"
            + (proc.stdout or "")[-8000:]
            + "\n--- stderr ---\n"
            + (proc.stderr or "")[-2000:]
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_suite_is_not_vacuously_empty():
    """A suite that collected nothing exits 0 and looks exactly like a pass.

    This is the same failure the repo's own audit rules warn about: green
    because it checked nothing. Assert the runner actually ran cases.
    """
    proc = subprocess.run(
        [shutil.which("node"), "--test", *_test_files()],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    passed = [
        line for line in (proc.stdout or "").splitlines()
        if line.startswith("# pass ")
    ]
    assert passed, f"no '# pass' line in node output:\n{(proc.stdout or '')[-2000:]}"
    count = int(passed[-1].split()[-1])
    # A floor against a vacuous run, deliberately well below the real count so
    # that adding or retiring a case is never a build break. The shared-bundle
    # file alone clears this and cannot skip — only personal-app tests do.
    assert count >= 5, f"suite ran almost nothing ({count} passed) — discovery is broken"
