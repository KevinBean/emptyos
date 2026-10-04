"""A provider that RUNS AND FAILS must not be reported as a provider that is ABSENT.

Regression (CAD app-optimizer pass, 2026-07-15): the chain's fallback loop did
``except Exception: continue`` and then, having exhausted the providers, raised a
flat "No available provider" — discarding the real reason. Driving a live Chrome
Browser Session, a screenshot that Chrome refused for want of a permission was
reported as "No available provider for capability 'browse'", and the Browse tool
helpfully appended "install with: pip install playwright" — three layers of wrong
diagnosis over a one-line permission problem.

The distinction the chain must preserve:
  * nothing to call            -> "No available provider"  (absence)
  * something called and blew up -> the underlying error   (failure)
"""

from __future__ import annotations

import pytest

from emptyos.capabilities import Capability


class _Boom:
    """A provider that is present and available, but whose execute() fails."""

    name = "chrome-extension"
    is_cloud = False
    at_capacity = False
    ability = "standard"

    def __init__(self):
        self._current_load = 0

    async def available(self) -> bool:
        return True

    async def execute(self, **kwargs):
        raise RuntimeError("screenshot_permission_required")


class _Absent:
    """A provider that is registered but not usable at all."""

    name = "playwright"
    is_cloud = False
    at_capacity = False
    ability = "standard"

    def __init__(self):
        self._current_load = 0

    async def available(self) -> bool:
        return False

    async def execute(self, **kwargs):  # pragma: no cover - never reached
        raise AssertionError("unavailable provider must not be executed")


def _capability(providers) -> Capability:
    cap = Capability.__new__(Capability)
    cap.name = "browse"
    cap.providers = list(providers)
    return cap


@pytest.mark.asyncio
async def test_failing_provider_surfaces_its_own_error(monkeypatch):
    cap = _capability([_Boom()])
    monkeypatch.setattr(cap, "_get_providers", lambda *a, **k: list(cap.providers), raising=False)
    monkeypatch.setattr(cap, "_reorder_by_ability", lambda ps, _a: ps, raising=False)
    monkeypatch.setattr(cap, "_provider_ready", _always_ready, raising=False)
    monkeypatch.setattr(cap, "_preprocess_outbound_kwargs", _passthrough, raising=False)

    with pytest.raises(RuntimeError) as exc:
        await cap.execute(action="screenshot", only_provider="chrome-extension")

    msg = str(exc.value)
    # the REAL reason survives...
    assert "screenshot_permission_required" in msg
    # ...and we do not claim the provider was missing.
    assert "No available provider" not in msg


@pytest.mark.asyncio
async def test_genuinely_absent_provider_still_reports_absence(monkeypatch):
    cap = _capability([_Absent()])
    monkeypatch.setattr(cap, "_get_providers", lambda *a, **k: list(cap.providers), raising=False)
    monkeypatch.setattr(cap, "_reorder_by_ability", lambda ps, _a: ps, raising=False)
    monkeypatch.setattr(cap, "_preprocess_outbound_kwargs", _passthrough, raising=False)

    with pytest.raises(RuntimeError) as exc:
        await cap.execute(action="screenshot")

    assert "No available provider" in str(exc.value)


async def _always_ready(provider, **kwargs):
    return True


async def _passthrough(provider, kwargs):
    return dict(kwargs)
