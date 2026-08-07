"""Unit tests for declared provider trust (emptyos.capabilities.Provider.trust).

Pure — no kernel, no network.

The gap these pin: `is_cloud` inferred local-vs-cloud from the host address, and
`host_is_local()` counts Tailscale CGNAT (100.64/10) and *.ts.net as local. That
is right for your own machines and wrong for a *rented* GPU you happen to have
tunnelled to — the convenient way to reach rented compute made a stranger's box
read as local and skip the consent gate entirely.

Worse, a provider that never sets `host` at all inferred `bool("") -> False`,
i.e. local unconditionally. That was ComfyUI's case.

See `.claude/rules/rented-compute.md`.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities import Provider


def _p(**attrs):
    """A throwaway Provider subclass with the given class attributes."""
    return type("P", (Provider,), attrs)()


# --- declared trust wins over address inference ---------------------------

@pytest.mark.parametrize("host", [
    "http://localhost:8188",
    "http://127.0.0.1:8188",
    "http://192.168.1.50:8188",
    "http://100.64.0.10:8188",      # Tailscale CGNAT
    "http://gpu-rental.ts.net:8188",  # Tailscale MagicDNS
])
def test_rented_is_cloud_however_local_the_address_looks(host):
    """The whole point: a rented box on your tailnet is NOT your machine."""
    assert _p(host=host, trust="rented").is_cloud is True


def test_service_is_cloud():
    assert _p(host="https://api.openai.com", trust="service").is_cloud is True


def test_owned_is_not_cloud_even_on_a_public_address():
    """Your own VPS reached over the open internet is still your hardware."""
    assert _p(host="https://my-own-box.example.com", trust="owned").is_cloud is False


@pytest.mark.parametrize("declared", ["OWNED", "  owned  ", "Owned"])
def test_trust_is_case_and_whitespace_insensitive(declared):
    assert _p(host="https://example.com", trust=declared).is_cloud is False


def test_unrecognised_trust_fails_closed():
    """A typo must tighten the gate, never open it."""
    for bad in ("ownd", "local", "mine", "yes", "true"):
        assert _p(host="http://127.0.0.1", trust=bad).is_cloud is True, bad


# --- undeclared falls back to the old behaviour, unchanged ---------------

@pytest.mark.parametrize("host,expected", [
    ("", False),                              # no host -> local (legacy)
    ("http://localhost:8188", False),
    ("http://127.0.0.1:11434", False),
    ("http://192.168.1.10:8188", False),
    ("http://100.64.0.10:9000", False),     # own tailnet machine
    ("https://api.openai.com", True),
    ("https://api.pexels.com", True),
])
def test_undeclared_still_infers_from_host(host, expected):
    assert _p(host=host).is_cloud is expected


def test_empty_trust_is_undeclared_not_owned():
    """trust='' must NOT be read as 'owned' — it means 'nobody said'."""
    assert _p(host="https://api.openai.com", trust="").is_cloud is True


# --- the ComfyUI shape: a provider that reports host via a property ------

def test_property_host_and_trust_are_honoured():
    """ComfyUI exposes host/trust as properties so they track config changes."""

    class Cfg:
        host = "http://gpu-rental.ts.net:8188"
        trust = "rented"

    class ComfyLike(Provider):
        name = "comfyui"

        @property
        def host(self) -> str:
            return Cfg.host

        @property
        def trust(self) -> str:
            return Cfg.trust

    p = ComfyLike()
    assert p.is_cloud is True

    Cfg.host, Cfg.trust = "http://localhost:8188", ""
    assert p.is_cloud is False, "must re-read, not freeze at construction"


def test_hostless_provider_is_still_local_when_undeclared():
    """Legacy behaviour preserved — but this is exactly the shape that hid
    ComfyUI's remote host, which is why the plugin now populates `host`."""
    assert _p().is_cloud is False


# --- the key-name check the auth model depends on ------------------------

def test_auth_mode_follows_is_cloud():
    assert _p(host="https://api.openai.com", trust="service").auth_mode == "api-key"
    assert _p(host="http://localhost:8188", trust="owned").auth_mode == "local"
    # A rented box needs a credential like any other non-owned endpoint.
    assert _p(host="http://gpu.ts.net:8188", trust="rented").auth_mode == "api-key"
