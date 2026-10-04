"""Pytest wrapper for the node behavior tests of eos-cad-part-ops.js — the pure
store-mutating core of the layout-model part-editor (stage 5 of legacy-workspace
retirement). Runs tests/js/cad_part_ops.test.mjs under node; skips cleanly when
node isn't installed (the repo's CI is pytest-first). No daemon needed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).parent / "js" / "cad_part_ops.test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_cad_part_ops_node_suite():
    assert _SCRIPT.exists(), f"missing {_SCRIPT}"
    res = subprocess.run([_NODE, str(_SCRIPT)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"node suite failed:\n{res.stdout}\n{res.stderr}"
    assert "PASS" in res.stdout, res.stdout
