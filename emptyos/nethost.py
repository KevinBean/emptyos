"""Hostname canonicalisation — one spelling of "which machine is this?".

A top-level leaf on purpose. Both layers need it: ``emptyos/capabilities/
consent.py`` (kernel-side cloud classification) and ``emptyos/sdk/`` (the SSRF
gate + the agent tools' auto-approve check). The kernel deliberately does not
import ``emptyos.sdk`` at module level, and ``emptyos/__init__.py`` is three
lines, so living here costs neither layer a package-init and inverts nothing.

Pure stdlib, no EmptyOS imports — keep it that way, or it stops being safe for
the kernel to import.

Why this exists
===============
``urlparse`` hands back a host string; it does not canonicalise it. Five call
sites independently compared that raw string against loopback/private ranges,
and every one of them silently disagreed with the client that would actually
open the connection:

  - ``0x7f.0.0.1``, ``127.0.0.001``, ``127.1`` — ``ipaddress.ip_address()``
    rejects all three, so a range check never runs, while ``inet_aton`` (and
    therefore libcurl and Chromium) reads them as 127.0.0.1. Measured 2026-08-14
    returning HTTP 200 from the local daemon.
  - ``127.0.0.1.`` / ``foo.local.`` — RFC 1034 trailing dot. Chromium strips it;
    an ``endswith(".local")`` suffix check does not.

In the SSRF gate that disagreement was exploitable. In the other four it fails
safe (an unrecognised host gets *more* friction — a permission prompt or a cloud
consent gate — never less), which is why they were not a live bug. They are
still wrong, and one canonicaliser is cheaper than four that drift.

See ``docs/OPEN-SOURCE-BORROWING-PLAN.md`` § claude-seo for the measurements.
"""

from __future__ import annotations

import ipaddress
import re
import socket

__all__ = ["canonical_hostname", "host_is_loopback_or_private"]


# Every spelling a C resolver (``inet_aton``) reads as a packed IPv4 rather than
# a DNS name: dotted-quad, leading zeros, octal, hex, and the short two/three
# part forms. Anything matching goes through inet_aton; anything else is a name.
_OBFUSCATED_IPV4_RE = re.compile(
    r"^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+)){0,3}$", re.IGNORECASE
)


def canonical_hostname(host: str) -> str:
    """Normalise a hostname to the form the connecting client will resolve.

    Lowercases, strips IPv6 brackets, drops a single RFC 1034 trailing dot, and
    rewrites any numeric IPv4 spelling to dotted-quad. Returns ``""`` for empty
    input or a malformed numeric form (which no client can reach anyway), so
    callers should treat ``""`` as "unusable", not as "fine".

    Not URL-aware — pass a hostname, not a URL. ``urlparse(...).hostname``
    first if you have a URL.
    """
    if not host:
        return ""
    h = host.strip().lower().strip("[]")
    if h.endswith(".") and not h.endswith(".."):
        h = h[:-1]
    if _OBFUSCATED_IPV4_RE.match(h):
        try:
            return socket.inet_ntoa(socket.inet_aton(h))
        except OSError:
            return ""
    return h


def host_is_loopback_or_private(host: str) -> bool:
    """True for this machine or the private LAN; False for anything else.

    Conservative by design: an unrecognised host (any DNS name that is not a
    literal) is reported non-local so the caller keeps its permission gate.
    Narrower than ``capabilities.consent.host_is_local``, which also counts
    Tailscale CGNAT and ``*.ts.net`` — that breadth is right for cloud consent
    and wrong for "may I skip the prompt".
    """
    h = canonical_hostname(host)
    if not h:
        return False
    if h == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private
