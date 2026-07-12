"""Pytest wrapper for the node behavior tests of the CAD store undo/redo history."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).parent / "js" / "cad_store_undo.test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_cad_store_undo_node_suite():
    assert _SCRIPT.exists(), f"missing {_SCRIPT}"
    result = subprocess.run([_NODE, str(_SCRIPT)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"node suite failed:\n{result.stdout}\n{result.stderr}"
    assert "PASS" in result.stdout, result.stdout
