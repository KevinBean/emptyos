"""Block regressions where an app page ships without the global nav include.

Pairs with scripts/check-app-nav.py — runs the same scan in CI so a missing
`<script src="/static/eos.js">` in any `apps/<id>/pages/*.html` (or
`apps/personal/<id>/pages/*.html`) trips the build.

The scanner honors `<!-- eos-nav: skip — <rationale> -->` opt-outs (full-bleed
canvases, presenter views, first-run onboarding). When this test fails:
either add the script include to the page in question, or — if the page
genuinely has no global chrome — add the opt-out comment with a one-line
rationale describing why.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-app-nav.py"


@pytest.mark.api
def test_check_app_nav_clean():
    """Every app page must include /static/eos.js or carry an explicit opt-out."""
    assert SCRIPT.exists(), f"scanner missing at {SCRIPT}"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "check-app-nav found app pages missing the global nav include.\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}\n"
        "Fix either by adding `<script src=\"/static/eos.js\">` after `<body>`, "
        "or — if the page is intentionally chromeless — by adding "
        "`<!-- eos-nav: skip — <reason> -->` to the page head."
    )
