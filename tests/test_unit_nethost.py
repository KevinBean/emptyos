"""Unit tests for emptyos.nethost — daemon-free, pure stdlib.

The obfuscated forms below are not hypothetical: on 2026-08-14 `0x7f.0.0.1`,
`127.0.0.001` and `127.1` each returned HTTP 200 from the local daemon via curl
while EmptyOS's own SSRF guard classified them as public web. Both directions
are pinned — the must-stay-public set is what stops a future tightening from
turning a real hostname into a false local.
"""

from __future__ import annotations

import pytest

from emptyos.nethost import canonical_hostname, host_is_loopback_or_private


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Numeric spellings a C resolver reads as packed IPv4.
        ("0x7f.0.0.1", "127.0.0.1"),  # dotted hex
        ("127.0.0.001", "127.0.0.1"),  # leading zeros
        ("127.1", "127.0.0.1"),  # two-part short form
        ("127.0.1", "127.0.0.1"),  # three-part short form
        ("2130706433", "127.0.0.1"),  # bare decimal
        ("0x7f000001", "127.0.0.1"),  # bare hex
        ("017700000001", "127.0.0.1"),  # bare octal
        # RFC 1034 trailing dot — Chromium strips it, so we must too.
        ("127.0.0.1.", "127.0.0.1"),
        ("foo.local.", "foo.local"),
        ("example.com.", "example.com"),
        # Case + IPv6 brackets.
        ("FOO.Local", "foo.local"),
        ("[::1]", "::1"),
        # Left alone: real names, punycode IDN, already-canonical literals.
        ("example.com", "example.com"),
        ("xn--fsq.com", "xn--fsq.com"),
        ("127.0.0.1", "127.0.0.1"),
        ("8.8.8.8", "8.8.8.8"),
        # Unusable input reports "" rather than something reachable-looking.
        ("", ""),
        ("999.999.999.999", ""),
    ],
)
def test_canonical_hostname(raw, expected):
    assert canonical_hostname(raw) == expected


def test_canonical_hostname_takes_low_32_bits_like_inet_aton():
    """A huge numeric wraps the way a C resolver wraps it, not to something safe.

    Worth pinning explicitly: the wrap must land on the *same* address the
    client would reach, or canonicalising would hand the range check a
    different host than the one that gets connected to.
    """
    assert canonical_hostname("0x999999999999") == "153.153.153.153"


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "::1",
        "10.0.0.5",
        "192.168.1.9",
        "172.16.0.1",
        # The forms that used to slip past every copy of this check.
        "0x7f.0.0.1",
        "127.0.0.001",
        "127.1",
        "127.0.0.1.",
        "10.0.0.5.",
    ],
)
def test_host_is_loopback_or_private_true(host):
    assert host_is_loopback_or_private(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "8.8.8.8",
        "1.1.1.1",
        "api.openai.com",
        "xn--fsq.com",
        "",  # conservative: unusable → keep the permission gate
        "999.999.999.999",
        # Deliberately NOT local here: this check is the auto-approve gate, and
        # a tailnet peer may be someone else's rented box. consent.host_is_local
        # is the broader one that counts these — see rented-compute.md.
        "100.64.0.1",
        "foo.ts.net",
    ],
)
def test_host_is_loopback_or_private_false(host):
    assert host_is_loopback_or_private(host) is False
