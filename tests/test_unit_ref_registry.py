"""Pytest wrapper for the node behavior tests of the Browser Session ref registry.

The ref lifecycle (churn-surviving refs guarded by a per-element identity
signature) shipped a blocker that only a live CAD walk caught (2026-07-15); the
logic was extracted to tools/chrome-extension/ref-registry.js so it is testable
here without a browser.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).parent / "js" / "ref-registry.test.js"


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_ref_registry_node_suite():
    assert _SCRIPT.exists(), f"missing {_SCRIPT}"
    result = subprocess.run([_NODE, str(_SCRIPT)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"node suite failed:\n{result.stdout}\n{result.stderr}"
    assert "passed" in result.stdout, result.stdout
