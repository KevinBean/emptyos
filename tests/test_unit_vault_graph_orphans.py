"""vault-graph — the orphan panel's wiring to `link` (offline, no daemon).

The panel's whole value is that it does NOT re-derive orphans: `link` owns the
vault-wide link index and this app renders it. That makes the wiring itself the
thing worth pinning, because `try_call_app` swallows failures by contract — a
renamed method on the other side would degrade the panel to "unavailable"
silently, which reads as "link isn't installed" rather than "we broke it".

`web_route` only stamps metadata on the function, so the handler is callable
with a stub self.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from helpers import load_app_module  # noqa: E402

VG = load_app_module("vault-graph", "app")
api_orphans = VG.VaultGraphApp.api_orphans

REPORT = {
    "orphans": ["a.md"],
    "unreferenced": ["b.md"],
    "broken_only": [],
    "counts": {"orphans": 9204, "unreferenced": 14000, "broken_only": 31},
    "truncated": True,
    "limit": 100,
    "total_notes": 27592,
}


class _Req:
    def __init__(self, **params):
        self.query_params = params


class _Stub:
    """Records the cross-app call and replays a canned answer."""

    def __init__(self, result=REPORT, err=""):
        self._result, self._err = result, err
        self.calls: list[tuple] = []

    async def try_call_app(self, app_id, method, /, **kwargs):
        self.calls.append((app_id, method, kwargs))
        return (None, self._err) if self._err else (self._result, "")


@pytest.mark.asyncio
async def test_calls_link_orphan_report_and_passes_the_report_through():
    stub = _Stub()
    out = await api_orphans(stub, _Req())
    assert stub.calls == [("link", "orphan_report", {"limit": 100})]
    assert out["available"] is True
    assert out["counts"]["orphans"] == 9204
    assert out["total_notes"] == 27592


@pytest.mark.asyncio
async def test_degrades_instead_of_raising_when_link_is_absent():
    """`link` is an optional_apps integration — the graph is fine without it."""
    stub = _Stub(err="App 'link' has no method 'orphan_report'")
    out = await api_orphans(stub, _Req())
    assert out["available"] is False
    assert "orphan_report" in out["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "given,expected",
    [
        ({}, 100),               # absent → default
        ({"limit": ""}, 100),    # blank → default, not 0-means-unbounded
        ({"limit": "abc"}, 100),  # unparseable → default
        ({"limit": "0"}, 1),     # `link` reads 0 as unbounded; never ask for that
        ({"limit": "5000"}, 500),
        ({"limit": "25"}, 25),
    ],
)
async def test_limit_is_clamped_before_it_reaches_link(given, expected):
    """A panel render is 100 rows; `limit=0` on `link` means the whole list —
    ~9,200 paths here — so this endpoint must never forward it."""
    stub = _Stub()
    await api_orphans(stub, _Req(**given))
    assert stub.calls[0][2] == {"limit": expected}
