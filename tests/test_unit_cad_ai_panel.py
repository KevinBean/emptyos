"""Pytest wrapper for the node behavior tests of eos-cad-ai-panel.js."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).parent / "js" / "cad_ai_panel.test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_cad_ai_panel_node_suite():
    assert _SCRIPT.exists(), f"missing {_SCRIPT}"
    result = subprocess.run([_NODE, str(_SCRIPT)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"node suite failed:\n{result.stdout}\n{result.stderr}"
    assert "PASS" in result.stdout, result.stdout
