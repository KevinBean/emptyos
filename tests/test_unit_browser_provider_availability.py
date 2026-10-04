"""The chrome-extension browse provider must be UNAVAILABLE unless armed.

`Provider.available()` is contractually a bool, and the capability chain gates on
`if not await provider.available()`. An earlier version returned the structured
`{"available": False, "reason": ...}` dict — which is truthy — so the provider
advertised itself as usable with the feature flag dark and no session armed. On a
machine without Playwright the chain would then select it and fail at execute()
instead of honestly reporting that no browse provider is wired.

These tests assert on the value the chain actually reads, not on the shape the
implementer intended.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities.providers.browser import ChromeExtensionBrowseProvider


class _RT:
    def __init__(self, enabled: bool, armed: bool):
        self._enabled, self._armed = enabled, armed

    def browser_session_enabled(self) -> bool:
        return self._enabled

    def active_browser_session(self):
        return object() if self._armed else None


class _Kernel:
    def __init__(self, realtime):
        self.realtime = realtime


def _provider(enabled: bool = True, armed: bool = True) -> ChromeExtensionBrowseProvider:
    return ChromeExtensionBrowseProvider(_Kernel(_RT(enabled, armed)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,armed,expected",
    [
        (False, False, False),  # flag dark, nothing armed
        (False, True, False),   # flag dark — an armed session must not override it
        (True, False, False),   # flag on, but disarmed
        (True, True, True),     # the only usable state
    ],
)
async def test_available_is_a_bool_the_chain_can_gate_on(enabled, armed, expected):
    got = await _provider(enabled, armed).available()
    assert got is expected, f"available() returned {got!r}; a truthy dict would pass a `not` gate"
    assert isinstance(got, bool)


@pytest.mark.asyncio
async def test_no_realtime_service_is_unavailable():
    class _Bare:
        pass

    assert await ChromeExtensionBrowseProvider(_Bare()).available() is False


@pytest.mark.asyncio
async def test_health_carries_the_reason_the_inspector_shows():
    dark = await _provider(enabled=False, armed=False).health()
    assert dark["available"] is False
    assert dark["reason"] == "browser session disabled"
    assert dark["recovery"]

    disarmed = await _provider(enabled=True, armed=False).health()
    assert disarmed["reason"] == "browser session is not armed"

    armed = await _provider(enabled=True, armed=True).health()
    assert armed["available"] is True
    assert armed["reason"] is None
