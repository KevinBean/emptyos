"""cable-bonding's report route must refuse a partial result, not 500 on it.

`_run_all` legitimately returns a PARTIAL result when `svl_u_r_kv` is absent —
it refuses to fabricate an arrester rating, records why in `slots_skipped`, and
returns only the `standing` and `tov` slots. `_report_markdown` unpacks all five
slots unconditionally, so asking for a sheet on that result raised
`KeyError('transient')` and the route answered **500**.

The compute path degraded gracefully and the report path did not. Found by
probing the live daemon on 2026-09-01, not by any test — the payload that
triggers it is well-formed and accepted, so nothing in the suite reached it.

Daemon-free: the module is loaded standalone and only pure module-level
functions are exercised.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SRC = ROOT / "apps/extension/engineering/cable-bonding/app.py"


def _mod():
    """app.py uses relative imports, so register its parent package first —
    the pattern in `.claude/rules/multi-module-apps.md`."""
    base = _SRC.parent
    pkg = types.ModuleType("_cb_pkg")
    pkg.__path__ = [str(base)]
    sys.modules["_cb_pkg"] = pkg
    for sub in sorted(p.stem for p in base.glob("*.py") if p.name != "app.py"):
        sp = importlib.util.spec_from_file_location(f"_cb_pkg.{sub}", base / f"{sub}.py")
        if not (sp and sp.loader):
            continue
        mm = importlib.util.module_from_spec(sp)
        sys.modules[f"_cb_pkg.{sub}"] = mm
        sp.loader.exec_module(mm)
    sp = importlib.util.spec_from_file_location("_cb_pkg.app", _SRC)
    m = importlib.util.module_from_spec(sp)
    sys.modules["_cb_pkg.app"] = m
    sp.loader.exec_module(m)
    return m


# Every field `_coerce` requires EXCEPT svl_u_r_kv — that omission is the whole
# point: it is the one input the app refuses to default, because U_R is a term
# of F1 and an assumed arrester would contribute a third of the transient answer
# invisibly. So this payload is well-formed and accepted, and produces a partial
# result.
FULL = {
    "route_length_m": 500, "spacing_m": 0.2, "formation": "trefoil",
    "sheath_mean_radius_m": 0.03, "load_current_a": 400, "frequency_hz": 50,
    "standing_limit_v": 65.0, "coordination_margin_min": 1.2,
}


def test_run_all_returns_a_partial_result_without_an_arrester_rating():
    m = _mod()
    r = m._run_all(m._coerce(dict(FULL)))
    assert "slots_skipped" in r, "the partial shape this test guards is gone"
    assert "transient" not in r
    assert r["slots"] == ["standing", "tov"]


def test_the_partial_result_still_carries_its_reason():
    m = _mod()
    r = m._run_all(m._coerce(dict(FULL)))
    reason = next(iter(r["slots_skipped"].values()))
    assert "svl_u_r_kv" in reason
    assert "no safe default" in reason


def test_report_markdown_raises_on_a_partial_result():
    """This is the underlying sharp edge. The route must not reach it — see the
    next test — but the renderer's own contract is all-five-slots, and pinning
    that keeps the route's guard honest rather than incidental."""
    m = _mod()
    r = m._run_all(m._coerce(dict(FULL)))
    with pytest.raises(KeyError):
        m._report_markdown(m._coerce(dict(FULL)), r, {"rows": 1, "worst_pct": 0.1,
                                                      "tolerance_pct": 1.0})


def test_the_route_refuses_in_band_instead_of_raising():
    """Drive the real route, not the source.

    A source-grep version of this test was written first and **survived** the
    mutation that disables the guard — because the explanatory comment above it
    also contains the string `slots_skipped`. That is the third instance in this
    session of an assertion scoped wider than the thing it names
    (`.claude/rules/audits.md` § failure mode 3); here the fix is to stop
    reading the file and execute the handler.
    """
    import asyncio

    m = _mod()
    App = m.CableBondingApp

    class _Req:
        pass

    app = object.__new__(App)

    async def safe_json(_request):
        return dict(FULL)
    app.safe_json = safe_json

    async def report_response(*_a, **_k):        # must never be reached
        raise AssertionError("the partial result reached the renderer")
    app.report_response = report_response

    res = asyncio.run(App.api_report(app, _Req()))
    assert res.status_code == 400, f"expected an in-band refusal, got {res!r}"
    body = res.body.decode("utf-8")
    assert body.startswith("refused:")
    assert "svl_u_r_kv" in body, "the refusal must carry the app's own reason"


def test_a_complete_payload_still_renders():
    m = _mod()
    body = dict(FULL)
    body["svl_u_r_kv"] = 6.0
    p = m._coerce(body)
    r = m._run_all(p)
    assert "transient" in r, "a complete payload must produce every slot"
    md = m._report_markdown(p, r, {"rows": 8, "worst_pct": 0.4,
                                   "tolerance_pct": 2.0})
    assert md.startswith("# Calculation report")
