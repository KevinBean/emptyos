"""The reading layer's decisions, tested without a browser.

Four of the five reading-layer bugs fixed on 2026-07-14 were pure decisions —
inputs in, verdict out, no DOM — living inline in a content script, where the only
coverage was a ~50s Chromium walk. Every one of them shipped, and every one was
caught by the reader rather than by a test.

They now live in `tools/chrome-extension/reading-policy.js`, and this runs their
tests in milliseconds. No JS test runner, no package.json: node is already a
dependency of the build checks (`scripts/check-csp-inline.py`).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SUITE = Path(__file__).parent / "js" / "reading-policy.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_reading_policy_rules_hold():
    result = subprocess.run(
        [shutil.which("node"), str(SUITE)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # The JS suite prints one line per rule; surface all of it on failure, since the
    # assertion message IS the explanation of which rule broke.
    assert result.returncode == 0, (
        f"reading-policy rules failed\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}"
    )
    assert "passed" in result.stdout
