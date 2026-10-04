"""Tests for emptyos.sdk.web_politeness — robots.txt + per-host pacing.

Pins BOTH directions of the automated/interactive split, because that split is
a policy decision (see the module docstring) and a silent flip either way is a
real defect: honouring robots on interactive reads would degrade live research
on blanket-Disallow sites, and skipping it on sweeps is the discourtesy the
module exists to prevent.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from emptyos.sdk import web_politeness as wp

ROBOTS_BLOCK_ALL = "User-agent: *\nDisallow: /\n"
ROBOTS_MIXED = (
    "User-agent: *\n"
    "Disallow: /private\n"
    "Allow: /private/ok\n"
    "Crawl-delay: 2\n"
    "\n"
    "User-agent: Googlebot\n"
    "Disallow: /\n"
)


@pytest.fixture(autouse=True)
def _clean_state():
    wp.reset_state()
    yield
    wp.reset_state()


def _serve(body: str):
    """Patch the blocking robots fetch to return a fixed body."""
    return patch.object(wp, "_fetch_robots", lambda host, scheme: wp._parse_robots(body))


# ── the automated/interactive split ───────────────────────────────────────


def test_interactive_reads_ignore_robots():
    with _serve(ROBOTS_BLOCK_ALL):
        allowed = asyncio.run(wp.robots_allows("https://x.com/a", automated=False))
    assert allowed is True


def test_automated_reads_honour_disallow():
    with _serve(ROBOTS_BLOCK_ALL):
        allowed = asyncio.run(wp.robots_allows("https://x.com/a", automated=True))
    assert allowed is False


# ── rule semantics ────────────────────────────────────────────────────────


def test_allow_overrides_longer_disallow_prefix():
    with _serve(ROBOTS_MIXED):
        blocked = asyncio.run(wp.robots_allows("https://x.com/private/secret", automated=True))
        allowed = asyncio.run(wp.robots_allows("https://x.com/private/ok", automated=True))
        unrelated = asyncio.run(wp.robots_allows("https://x.com/public", automated=True))
    assert blocked is False
    assert allowed is True
    assert unrelated is True


def test_named_agent_group_is_not_applied_to_us():
    """The Googlebot ``Disallow: /`` group must not block EmptyOS."""
    with _serve(ROBOTS_MIXED):
        allowed = asyncio.run(wp.robots_allows("https://x.com/anything", automated=True))
    assert allowed is True


def test_network_failure_fails_open():
    """An unreachable robots.txt must never block a legitimate read."""

    def boom(*a, **k):
        raise OSError("network down")

    with patch.object(wp.urllib.request, "urlopen", boom):
        assert wp._fetch_robots("x.com", "https") == ([], [], 0.0)
        assert asyncio.run(wp.robots_allows("https://x.com/a", automated=True)) is True


def test_missing_robots_is_unrestricted():
    with _serve(""):  # empty/404-equivalent robots → unrestricted
        assert asyncio.run(wp.robots_allows("https://x.com/a", automated=True)) is True


def test_empty_disallow_value_means_allow_all():
    with _serve("User-agent: *\nDisallow:\n"):
        assert asyncio.run(wp.robots_allows("https://x.com/a", automated=True)) is True


def test_wildcard_and_anchor_rules():
    assert wp._rule_matches("/a/b.php", "/a/*.php") is True
    assert wp._rule_matches("/a/b.html", "/a/*.php") is False
    assert wp._rule_matches("/exact", "/exact$") is True
    assert wp._rule_matches("/exact/more", "/exact$") is False


# ── Retry-After parsing ───────────────────────────────────────────────────


def test_retry_after_clamped_and_junk_ignored():
    assert wp.parse_retry_after("30") == 30.0
    assert wp.parse_retry_after("99999") == wp.MAX_RETRY_AFTER_S  # hostile value capped
    assert wp.parse_retry_after("soon") == 0.0
    assert wp.parse_retry_after("") == 0.0
    assert wp.parse_retry_after("-5") == 0.0


def test_retry_after_accepts_http_date():
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    when = datetime.now(timezone.utc) + timedelta(seconds=45)
    secs = wp.parse_retry_after(format_datetime(when))
    assert 30 <= secs <= 60


def test_note_retry_after_pushes_the_host_gate():
    applied = wp.note_retry_after("https://x.com/a", "10")
    assert applied == 10.0
    assert wp._next_ok["x.com"] > time.monotonic()


# ── pacing ────────────────────────────────────────────────────────────────


def test_host_gate_paces_same_host_but_not_different_hosts():
    async def run():
        slept: list[float] = []

        async def fake_sleep(s):
            slept.append(s)

        with patch.object(wp.asyncio, "sleep", fake_sleep):
            await wp.host_gate("https://x.com/1")
            await wp.host_gate("https://x.com/2")  # same host → paced
            await wp.host_gate("https://y.com/1")  # different host → free
        return slept

    slept = asyncio.run(run())
    assert len(slept) == 1
    assert slept[0] > 0


def test_host_gate_ignores_unparseable_url():
    asyncio.run(wp.host_gate("not a url"))  # must not raise
