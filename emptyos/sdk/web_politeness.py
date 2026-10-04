"""Web politeness — robots.txt and per-host rate limiting for outbound fetches.

EmptyOS had **neither** before 2026-07-20: every ``read_web_source`` navigated
immediately, with no robots check and no per-host pacing. That is a courtesy
(and liability) gap independent of any external borrow, which is why this is a
first-party module rather than a port.

Two deliberate scope decisions, both load-bearing:

**robots.txt is honoured on automated paths only.** A background sweep
(kb-gap-miner, daily-brief) is crawling and must obey; a user asking a live
question and getting one page read on their behalf is not. Many news sites
blanket-``Disallow: /`` for non-Google agents, so honouring robots on
interactive reads would silently degrade ``explore``/``assistant`` for no
ethical gain. The caller passes ``automated=`` **explicitly** — it is never
inferred from context, so the choice is visible at every call site and a new
consumer has to think about it.

**Rate limiting applies to every path**, automated or not. Pacing is about not
hammering someone's origin, which a user-initiated read can do just as easily
when five of six results share a domain.

Both caches are per-process and in-memory: this is best-effort courtesy, not an
audited control, and a daemon restart legitimately re-fetches robots.txt.

Not built here (deliberate): a fetch/content cache. That is a separate concern
with a persistent store and an eviction policy — see
``docs/WEB-RESEARCH-SCOPING-2026-07-20.md`` item 2.
"""

from __future__ import annotations

import asyncio
import time
import urllib.request
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse

__all__ = [
    "robots_allows",
    "host_gate",
    "note_retry_after",
    "parse_retry_after",
    "reset_state",
    "ROBOTS_UA",
]

ROBOTS_UA = "EmptyOS-Research"
_ROBOTS_TTL_S = 3600.0
_ROBOTS_TIMEOUT_S = 5.0
_ROBOTS_MAX_BYTES = 512 * 1024

#: Minimum gap between requests to the same host.
MIN_HOST_INTERVAL_S = 1.0
#: Ceiling on an honoured ``Retry-After``. A hostile or typo'd header
#: (``Retry-After: 99999``) must not wedge a research run for a day.
MAX_RETRY_AFTER_S = 300.0

# host -> (fetched_at, allows, disallows, crawl_delay) for the `User-agent: *` group
_robots_cache: dict[str, tuple[float, list[str], list[str], float]] = {}
_robots_locks: dict[str, asyncio.Lock] = {}
# host -> earliest next-request timestamp (time.monotonic)
_next_ok: dict[str, float] = {}
_gate_lock = asyncio.Lock()


def reset_state() -> None:
    """Clear both caches. Tests only."""
    _robots_cache.clear()
    _robots_locks.clear()
    _next_ok.clear()


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def parse_retry_after(value: str) -> float:
    """Parse a ``Retry-After`` header → seconds, clamped to MAX_RETRY_AFTER_S.

    Accepts both forms in RFC 9110: delta-seconds and an HTTP-date. Returns
    ``0.0`` for anything unparseable or negative, so a malformed header is
    simply ignored rather than becoming an unbounded sleep.
    """
    raw = (value or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, min(float(int(raw)), MAX_RETRY_AFTER_S))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return 0.0
    if when is None:
        return 0.0
    try:
        delta = when.timestamp() - time.time()
    except (OSError, OverflowError, ValueError):
        return 0.0
    return max(0.0, min(delta, MAX_RETRY_AFTER_S))


def _parse_robots(body: str) -> tuple[list[str], list[str], float]:
    """Extract the ``User-agent: *`` group → (allows, disallows, crawl_delay).

    Only the wildcard group is honoured, matching the common-case behaviour of
    small crawlers. A named ``User-agent: EmptyOS-Research`` group would be more
    correct, but nothing publishes one for us, and silently preferring a named
    group we never match would be worse than the honest wildcard read.
    """
    allows: list[str] = []
    disallows: list[str] = []
    crawl_delay = 0.0
    in_star = False
    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            in_star = value == "*"
            continue
        if not in_star:
            continue
        if field == "disallow":
            # "Disallow:" with an empty value means allow-all — not a rule.
            if value:
                disallows.append(value)
        elif field == "allow":
            if value:
                allows.append(value)
        elif field == "crawl-delay":
            try:
                crawl_delay = max(0.0, min(float(value), MAX_RETRY_AFTER_S))
            except (TypeError, ValueError):
                pass
    return allows, disallows, crawl_delay


def _fetch_robots(host: str, scheme: str) -> tuple[list[str], list[str], float]:
    """Fetch + parse one host's robots.txt. Fails **open** on any error.

    A network failure, timeout, 404, or garbage body all yield "no rules". That
    is the right default for courtesy plumbing: an unreachable robots.txt must
    never block a legitimate read, and 404 genuinely means unrestricted.
    """
    url = urlunparse((scheme or "https", host, "/robots.txt", "", "", ""))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ROBOTS_UA})
        with urllib.request.urlopen(req, timeout=_ROBOTS_TIMEOUT_S) as resp:
            if getattr(resp, "status", 200) != 200:
                return [], [], 0.0
            body = resp.read(_ROBOTS_MAX_BYTES).decode("utf-8", errors="replace")
    except Exception:
        # Deliberately broad: a 404, timeout, DNS failure, TLS error, or garbage
        # body must all mean "no rules" rather than blocking a legitimate read.
        return [], [], 0.0
    return _parse_robots(body)


def _path_of(url: str) -> str:
    try:
        p = urlparse(url)
        return (p.path or "/") + (f"?{p.query}" if p.query else "")
    except Exception:
        return "/"


def _rule_matches(path: str, rule: str) -> bool:
    """Longest-prefix match with ``*`` wildcard and ``$`` end-anchor support."""
    if not rule:
        return False
    anchored = rule.endswith("$")
    pattern = rule[:-1] if anchored else rule
    if "*" not in pattern:
        return path == pattern if anchored else path.startswith(pattern)
    parts = pattern.split("*")
    if not path.startswith(parts[0]):
        return False
    pos = len(parts[0])
    for part in parts[1:]:
        if not part:
            continue
        idx = path.find(part, pos)
        if idx < 0:
            return False
        pos = idx + len(part)
    return path.endswith(parts[-1]) if anchored and parts[-1] else True


async def robots_allows(url: str, *, automated: bool) -> bool:
    """Is ``url`` fetchable under its host's robots.txt?

    ``automated=False`` returns ``True`` immediately — see the module docstring
    for why interactive reads are exempt. Always ``True`` on any failure to
    fetch or parse (fail-open).
    """
    if not automated:
        return True
    host = _host_of(url)
    if not host:
        return True
    scheme = (urlparse(url).scheme or "https").lower()
    now = time.time()
    cached = _robots_cache.get(host)
    if not cached or (now - cached[0]) > _ROBOTS_TTL_S:
        lock = _robots_locks.setdefault(host, asyncio.Lock())
        async with lock:
            cached = _robots_cache.get(host)
            if not cached or (time.time() - cached[0]) > _ROBOTS_TTL_S:
                allows, disallows, delay = await asyncio.to_thread(_fetch_robots, host, scheme)
                cached = (time.time(), allows, disallows, delay)
                _robots_cache[host] = cached
    _, allows, disallows, _delay = cached
    if not disallows:
        return True
    path = _path_of(url)
    best_dis = max((r for r in disallows if _rule_matches(path, r)), key=len, default="")
    if not best_dis:
        return True
    best_allow = max((r for r in allows if _rule_matches(path, r)), key=len, default="")
    # Longest match wins; Allow wins ties (the de-facto standard).
    return len(best_allow) >= len(best_dis)


async def host_gate(url: str) -> None:
    """Sleep until this host may be hit again. Applies to every path.

    Serialises the reservation under one lock so concurrent readers of the same
    host queue rather than all reading the same ``_next_ok`` and racing through
    together — the failure mode that makes a "rate limiter" pace nothing when
    five results share a domain.
    """
    host = _host_of(url)
    if not host:
        return
    # crawl-delay is only known on hosts whose robots.txt we fetched — i.e.
    # automated callers. Interactive reads pace at MIN_HOST_INTERVAL_S, which
    # is the intended asymmetry: they're courteous, not crawling.
    cached = _robots_cache.get(host)
    interval = max(MIN_HOST_INTERVAL_S, cached[3] if cached else 0.0)
    async with _gate_lock:
        now = time.monotonic()
        earliest = _next_ok.get(host, 0.0)
        wait = max(0.0, earliest - now)
        _next_ok[host] = max(now, earliest) + interval
    if wait > 0:
        await asyncio.sleep(min(wait, MAX_RETRY_AFTER_S))


def note_retry_after(url: str, retry_after: str | float) -> float:
    """Record a 429/503 back-off for this host. Returns the seconds applied.

    Callers that can see a response status should call this so the next
    ``host_gate`` for the same host waits it out.
    """
    host = _host_of(url)
    if not host:
        return 0.0
    secs = (
        parse_retry_after(retry_after)
        if isinstance(retry_after, str)
        else max(0.0, min(float(retry_after or 0.0), MAX_RETRY_AFTER_S))
    )
    if secs <= 0:
        return 0.0
    _next_ok[host] = max(_next_ok.get(host, 0.0), time.monotonic() + secs)
    return secs
