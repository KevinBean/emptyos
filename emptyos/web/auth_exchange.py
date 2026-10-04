"""Shell login exchange — turn a held bearer token into a browser cookie once.

The desktop shell (``products/_shared/``) already holds the machine credential:
it reads ``auth_token`` from ``emptyos.toml`` to talk to the daemon. The webview
it opens does not — so in ``network.mode = "private"`` the user is asked to log
in again, by hand, per webview profile, to a daemon running as them on their own
machine. This closes that, without ever putting the credential in a URL.

Two steps, and the split IS the security property:

1. **Mint** — ``POST /api/auth/shell-exchange`` with ``Authorization: Bearer
   <auth_token>`` returns a short-lived single-use code. Nothing else can mint:
   a session **cookie is refused here**, because a cookie is what a page in the
   browser has, and an XSS anywhere in the daemon could otherwise mint a code
   and carry a working login off the machine. Holding the bearer means holding
   the token already — the exchange grants nothing new.
2. **Redeem** — ``GET /auth/shell-exchange?code=…&next=/portal/`` sets the
   existing ``eos_session`` cookie and redirects. This route is auth-exempt by
   necessity (it is how you become authenticated), so everything it accepts is
   checked here: the code must be unexpired and unused, the caller must be on
   loopback, and ``next`` must be a path on this daemon.

Why a code rather than ``?token=`` (which the middleware already supports): the
token is the permanent credential and would land in history, the address bar,
and any log that records a URL. A code is worth 60 seconds, once — but be
precise about what it *buys*: redeeming it sets the same ``eos_session`` cookie
``/login`` would, whose value IS the token, for 30 days. The code's short life
bounds the window to steal it, not the damage if it is stolen. That asymmetry
is why redemption is restricted to the loopback **socket** rather than to
anything the caller can assert.

No kernel imports — this module is pure logic plus a small in-memory store, so
every invariant above is testable without a daemon
(``tests/test_unit_auth_shell_exchange.py``).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from emptyos.nethost import canonical_hostname

#: Long enough for a webview to make one navigation, short enough that a code
#: caught in a log or a crash dump is worthless by the time anyone reads it.
DEFAULT_TTL_S = 60

#: A code is a bearer credential for its lifetime — size it like one.
CODE_BYTES = 32

#: Cap the store so a caller that mints in a loop cannot grow it without bound.
#: Minting requires the token, so this is a seatbelt, not a boundary.
MAX_LIVE_CODES = 32


def new_code() -> str:
    return secrets.token_urlsafe(CODE_BYTES)


def safe_next(value: str, *, default: str = "/") -> str:
    """A ``next`` target that can only ever be a path on THIS daemon.

    Refuses, in order of how easily each is missed:

    - ``//evil.example/x`` — a protocol-relative URL. It starts with ``/``, so
      a naive "must start with /" check passes it, and the browser goes to
      another host carrying the cookie we just set.
    - ``/\\evil.example`` — the same trick with a backslash, which several
      browsers normalise to ``//``.
    - ``javascript:``, ``data:``, ``http://…`` and any other scheme — none of
      them start with ``/``, so the rule that admits them is the same one.
    - A RAW control character or newline, which could split the ``Location``
      header. A percent-ENCODED one (``%0d``) is deliberately left alone: it is
      path text, not a header boundary, and rejecting it would refuse
      legitimate encoded paths for no gain.

    A colon is not checked for once the value starts with a single ``/``: a
    path-absolute URL has no scheme to introduce, so ``/a:b`` is a path.

    Anything refused becomes ``default`` rather than an error: the user asked to
    sign in, and a strange ``next`` is not a reason to refuse them the daemon.
    """
    v = str(value or "").strip()
    if not v:
        return default
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        return default
    if not v.startswith("/"):
        return default
    # "//host" and "/\host" are other-origin. The percent-encoded spellings are
    # refused as well — belt and braces rather than a live path: Starlette has
    # already decoded a query param by the time this sees it, so "?next=%2f%2fx"
    # arrives as "//x" and the plain branch catches it.
    lowered = v.lower()
    if lowered.startswith(("//", "/\\", "/%2f", "/%5c")):
        return default
    return v


def request_is_local(client_host: str) -> bool:
    """Strictly this machine. Not ``host_is_loopback_or_private``: the shell
    runs on the same box as the daemon, so a private-LAN peer redeeming a code
    is someone else's machine on the same network, which is exactly the case
    this must refuse."""
    h = canonical_hostname(client_host)
    if not h:
        return False
    if h == "localhost":
        return True
    import ipaddress

    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


@dataclass
class CodeStore:
    """Live exchange codes. In memory on purpose — a code outliving a restart
    is a credential on disk for no benefit, since the shell can simply mint
    another."""

    ttl_s: int = DEFAULT_TTL_S
    max_live: int = MAX_LIVE_CODES
    # repr=False: the dataclass repr would otherwise dump every live code
    # into any exception chain, log line or debugger frame that touches it.
    _codes: dict[str, float] = field(default_factory=dict, repr=False)
    #: Injectable so expiry is testable without sleeping.
    now: object = time.time

    def mint(self) -> str:
        self._sweep()
        if len(self._codes) >= self.max_live:
            # Drop the oldest rather than refuse: the shell asking again is the
            # normal case, and a full store must not lock the user out.
            oldest = min(self._codes, key=self._codes.get)
            self._codes.pop(oldest, None)
        code = new_code()
        self._codes[code] = float(self.now())
        return code

    def redeem(self, code: str) -> bool:
        """True once per code, and only while it is fresh.

        A plain dict lookup, deliberately. An earlier version compared each
        live code with ``hmac.compare_digest`` "so a lookup cannot leak, by
        timing, how much of a guess is right" — which is not how dict lookup
        works (it hashes the whole key and compares within a bucket; it does
        not prefix-scan), was not itself constant-time, and raised TypeError on
        any non-ASCII string, turning ``?code=café`` into a 500 on the one
        route that answers without a credential. A hardening step with no
        security value that created a remotely-triggerable crash.
        """
        self._sweep()
        code = str(code or "")
        if not code:
            return False
        return self._codes.pop(code, None) is not None   # single use

    def live(self) -> int:
        self._sweep()
        return len(self._codes)

    def _sweep(self) -> None:
        cutoff = float(self.now()) - self.ttl_s
        for code in [c for c, ts in self._codes.items() if ts < cutoff]:
            self._codes.pop(code, None)
