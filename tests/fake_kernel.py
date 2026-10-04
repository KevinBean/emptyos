"""Fake kernel scaffolding for pure BaseApp unit tests.

Lets a test build a BaseApp without the full runtime: pass one `FakeCapability`
wired with `FakeProvider` instances, and call capability-level methods
directly. Supports both the sync `execute()` path (pinned_execute) and the
streaming `execute_stream()` path (think_stream).

Use this from tests that exercise SDK control flow — provider pinning,
fallback, domain subchains — without hitting a real daemon or LLM.
"""

from __future__ import annotations

from emptyos.sdk.base_app import BaseApp


class FakeProvider:
    """Capability provider stub. Records the kwargs it was called with in
    `called_with`. Specify `returns=` for `execute()` or `chunks=` for
    `execute_stream()` — one provider instance can serve either path."""

    def __init__(self, name, *, available=True, returns=None, chunks=None):
        self.name = name
        self._available = available
        self._returns = returns
        self._chunks = chunks
        self.called_with = None
        self._current_load = 0

    async def available(self):
        return self._available

    @property
    def at_capacity(self):
        return False

    async def execute(self, **kwargs):
        self.called_with = kwargs
        return self._returns

    async def execute_stream(self, **kwargs):
        self.called_with = kwargs
        for c in self._chunks or []:
            yield c


class FakeResult:
    """Mimics `CapabilityResult` — capabilities wrap their output in `.value`."""

    def __init__(self, value):
        self.value = value


class FakeCapability:
    """Capability stub. `chain_result` populates the default `execute()` path;
    `chain_chunks` populates the default `execute_stream()` path. Tracks
    whether either chain was reached via `.chain_called`."""

    def __init__(self, providers, domain_providers=None, *, chain_result="default-chain-result", chain_chunks=None):
        self.providers = providers
        self._domains = domain_providers or {}
        self._chain_result = chain_result
        self._chain_chunks = chain_chunks if chain_chunks is not None else [
            {"text": "from-chain", "done": False},
            {"text": "", "done": True},
        ]
        self.chain_called = False

    async def execute(self, **kwargs):
        self.chain_called = True
        return FakeResult(self._chain_result)

    async def execute_stream(self, **kwargs):
        self.chain_called = True
        for c in self._chain_chunks:
            yield c


class _FakeServices:
    def get_optional(self, name):
        return None


class _FakeEvents:
    """Records every emit() call so tests can assert on billing/journal events."""

    def __init__(self):
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, event_type, data, source=None):
        self.emitted.append((event_type, dict(data)))


class FakeKernel:
    def __init__(self, cap):
        self._cap = cap
        self.services = _FakeServices()
        self.events = _FakeEvents()

    def capability(self, name):
        return self._cap


class _FakeManifest:
    id = "test-app"


def make_bare_app(cap):
    """Build a `BaseApp` with minimal fake wiring. Bypasses `__init__` to
    avoid the heavy runtime dependencies real apps require."""
    app = BaseApp.__new__(BaseApp)
    app.kernel = FakeKernel(cap)
    app.manifest = _FakeManifest()
    return app
