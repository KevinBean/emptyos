"""Pytest wrapper for the node behavior tests of eos-draft-store.js."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).parent / "js" / "draft_store.test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_draft_store_node_suite():
    assert _SCRIPT.exists(), f"missing {_SCRIPT}"
    res = subprocess.run([_NODE, str(_SCRIPT)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"node suite failed:\n{res.stdout}\n{res.stderr}"
    assert "PASS" in res.stdout, res.stdout
