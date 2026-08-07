"""Pytest wrapper for the node behavior tests of eos-cad-schema-form.js — the pure
dotted-path scalar-schema-to-form-markup layer shared by object-inspector.js (the
generic typed-object editor) and corridor-inspector.js (the cable-run ampacity
spec editor, its second consumer — this module is what CLAUDE.md rule 9's
extraction looks like for frontend view logic, not just Python). Runs
tests/js/cad_schema_form.test.mjs under node; skips cleanly when node isn't
installed (the repo's CI is pytest-first). No daemon needed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).parent / "js" / "cad_schema_form.test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_cad_schema_form_node_suite():
    assert _SCRIPT.exists(), f"missing {_SCRIPT}"
    res = subprocess.run([_NODE, str(_SCRIPT)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"node suite failed:\n{res.stdout}\n{res.stderr}"
    assert "PASS" in res.stdout, res.stdout
