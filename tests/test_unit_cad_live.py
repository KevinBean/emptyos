"""Pytest wrapper for the node behavior tests of eos-cad-live.js — the
live-consequence tick (phase 1: recompute engineering checks on edit instead of on a
button) and the derived-analysis channel it drives on createCadStore. Runs
tests/js/cad_live.test.mjs under node; skips cleanly when node isn't installed
(the repo's CI is pytest-first). No daemon needed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).parent / "js" / "cad_live.test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_cad_live_node_suite():
    assert _SCRIPT.exists(), f"missing {_SCRIPT}"
    res = subprocess.run([_NODE, str(_SCRIPT)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"node suite failed:\n{res.stdout}\n{res.stderr}"
    assert "PASS" in res.stdout, res.stdout
