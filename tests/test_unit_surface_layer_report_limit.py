"""surface-layer-screen — the sheet's threshold-limit cell has two absent shapes.

`screen_surface_layer` reports `delta_l_limit_m` as the equivalent depth at
which the stack would reach the exclusion threshold. It has no value in two
different ways:

  * **None** — the stack IMPROVES the rating (`delta_l < 0`), so there is no
    depth at which it crosses the threshold. Deliberate engine behaviour.
  * **NaN** — the solve did not converge.

The report guarded only the second (`x == x`). `None == None` is True, so the
sheet fell through to `None * 1000` and `POST /api/report` returned HTTP 500 on
every `improves` verdict — the asphalt-over-soil case the app exists to screen.
It was invisible because RELEASE-VERIFICATION step 5 ("a conductive layer flips
the verdict, and the sheet follows") asserted only the API half.

Daemon-free: `_limit_mm` is a module-level pure function.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_BASE = ROOT / "apps/extension/engineering/surface-layer-screen"

needs_app = pytest.mark.skipif(not (_BASE / "app.py").exists(), reason="app absent")
pytestmark = needs_app


def _limit_mm():
    """Load the app module by path — it uses relative imports, so register the
    parent package first (the pattern in .claude/rules/multi-module-apps.md)."""
    pkg = types.ModuleType("_sls_pkg")
    pkg.__path__ = [str(_BASE)]
    sys.modules["_sls_pkg"] = pkg
    for sub in ("spec",):
        sp = importlib.util.spec_from_file_location(f"_sls_pkg.{sub}", _BASE / f"{sub}.py")
        m = importlib.util.module_from_spec(sp)
        sys.modules[f"_sls_pkg.{sub}"] = m
        sp.loader.exec_module(m)
    sp = importlib.util.spec_from_file_location("_sls_pkg.app", _BASE / "app.py")
    m = importlib.util.module_from_spec(sp)
    sys.modules["_sls_pkg.app"] = m
    sp.loader.exec_module(m)
    return m._limit_mm


def test_none_is_not_a_number_and_must_not_be_multiplied():
    """The regression: an improving stack has no limit, and None passes `x == x`."""
    assert _limit_mm()(None) == "n/a"


def test_nan_still_reads_as_absent():
    assert _limit_mm()(float("nan")) == "n/a"


def test_a_real_limit_is_rendered_in_millimetres():
    assert _limit_mm()(0.0331) == "33.1 mm"


def test_zero_is_a_value_not_an_absence():
    """0.0 is falsy — a truthiness guard would wrongly report it as absent, and
    a stack exactly at the threshold is the case a reader most needs to see."""
    assert _limit_mm()(0.0) == "0.0 mm"


def test_a_negative_limit_still_renders():
    """Not a physical case — a guard against the next "tidy-up".

    `if limit and limit > 0` looks like a reasonable hardening and would
    silently swallow both this and 0.0. Pinning the sign keeps the function a
    formatter: absence is the only thing it is allowed to interpret.
    """
    assert _limit_mm()(-0.02) == "-20.0 mm"
