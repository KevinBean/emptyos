"""The desktop shell's login exchange (A3).

The shell holds the machine token already; the webview it opens does not, so in
private mode the user is asked to log in by hand to a daemon running as them on
their own machine. The exchange closes that without putting the credential in a
URL — and every property that makes it safe rather than a back door is pinned
here, because the redeem leg is auth-exempt by necessity.
"""

from __future__ import annotations

import pytest

from emptyos.web.auth_exchange import (
    DEFAULT_TTL_S,
    MAX_LIVE_CODES,
    CodeStore,
    new_code,
    request_is_local,
    safe_next,
)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ── next-URL clamping ────────────────────────────────────────────────

def test_a_local_path_passes_through():
    assert safe_next("/portal/") == "/portal/"
    assert safe_next("/portal/?x=1#y") == "/portal/?x=1#y"
    assert safe_next("/a:b") == "/a:b", "a path-absolute URL has no scheme to introduce"


@pytest.mark.parametrize("hostile", [
    "//evil.example/x",          # protocol-relative: starts with "/" and leaves the origin
    "///evil.example",
    "/\\evil.example",           # browsers normalise the backslash to "//"
    "/%2fevil.example",          # the encoded spellings of both
    "/%5Cevil.example",
    "http://evil.example/",
    "https://evil.example/",
    "javascript:alert(1)",
    "data:text/html,x",
    "evil.example",
    "\\\\evil.example\\share",
])
def test_anything_that_could_leave_this_origin_is_refused(hostile):
    """The cookie is set on the same response as the redirect, so a `next` that
    leaves the origin hands the session to someone else."""
    assert safe_next(hostile) == "/"


def test_a_raw_control_character_cannot_split_the_location_header():
    assert safe_next("/a\r\nSet-Cookie: x=1") == "/"
    assert safe_next("/a\nb") == "/"
    assert safe_next("/a\x00b") == "/"


def test_a_percent_encoded_newline_is_path_text_and_is_kept():
    """It is not a header boundary, and refusing it would refuse legitimate
    encoded paths. Pinned so nobody 'hardens' it into a false positive."""
    assert safe_next("/a%0d%0ab") == "/a%0d%0ab"


def test_an_empty_or_missing_next_is_the_root_not_an_error():
    for v in ("", "   ", None):
        assert safe_next(v) == "/"
    assert safe_next("", default="/portal/") == "/portal/"


# ── who may redeem ───────────────────────────────────────────────────

def test_only_this_machine_counts_as_local():
    assert request_is_local("127.0.0.1")
    assert request_is_local("::1")
    assert request_is_local("localhost")


def test_the_private_lan_is_NOT_local_here():
    """Deliberately narrower than host_is_loopback_or_private: the shell runs
    on the same box as the daemon, so a private-LAN peer redeeming a code is
    someone else's machine on the same network."""
    for host in ("192.168.1.5", "10.0.0.2", "172.16.0.9", "100.64.1.1"):
        assert not request_is_local(host), host


def test_an_unknown_or_absent_host_is_not_local():
    for host in ("", None, "evil.example", "not-an-ip"):
        assert not request_is_local(host)


def test_an_obfuscated_loopback_spelling_still_reads_as_loopback():
    """0x7f.0.0.1 and 127.1 both reach loopback in the client that opens the
    connection, so they must not read as remote here either."""
    assert request_is_local("127.1")
    assert request_is_local("0x7f.0.0.1")


# ── the code itself ──────────────────────────────────────────────────

def test_a_code_works_exactly_once():
    s = CodeStore()
    code = s.mint()
    assert s.redeem(code) is True
    assert s.redeem(code) is False, "a replayed code must not sign anyone in twice"


def test_a_code_expires():
    clock = Clock()
    s = CodeStore(now=clock)
    code = s.mint()
    clock.t += DEFAULT_TTL_S + 1
    assert s.redeem(code) is False
    assert s.live() == 0


def test_a_code_still_works_just_inside_its_window():
    clock = Clock()
    s = CodeStore(now=clock)
    code = s.mint()
    clock.t += DEFAULT_TTL_S - 1
    assert s.redeem(code) is True


def test_an_unknown_or_empty_code_is_refused():
    s = CodeStore()
    s.mint()
    assert s.redeem("made-up") is False
    assert s.redeem("") is False
    assert s.redeem(None) is False


def test_redeeming_one_code_does_not_spend_another():
    s = CodeStore()
    a, b = s.mint(), s.mint()
    assert s.redeem(a) is True
    assert s.redeem(b) is True


def test_codes_are_unguessable_and_distinct():
    codes = {new_code() for _ in range(200)}
    assert len(codes) == 200
    assert all(len(c) >= 40 for c in codes), "a code is a bearer credential for its lifetime"


def test_the_store_cannot_grow_without_bound():
    s = CodeStore()
    kept = [s.mint() for _ in range(MAX_LIVE_CODES + 5)]
    assert s.live() <= MAX_LIVE_CODES
    # The newest still work — a full store must never lock the user out.
    assert s.redeem(kept[-1]) is True


# ── The routes (the gate lives here, not in the helpers above) ────────
#
# Every test above pins a pure function; none of them can see whether a route
# CALLS it. These drive the real handlers through a FastAPI app with a fake
# kernel — no daemon — because the mint leg's cookie refusal and the redeem
# leg's exemption are both properties of the wiring.

from types import SimpleNamespace  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from emptyos.web.routes_auth import register_auth  # noqa: E402

TOKEN = "tok-abcdefghijklmnop"


def _client(*, flag=True, token=TOKEN, password="", peer="127.0.0.1"):
    cfg = SimpleNamespace(
        auth_token=token,
        login_password=password,
        demo_enabled=False,
        get=lambda k, d=None: (flag if k == "network.feature.shell-exchange.enabled" else d),
    )
    kernel = SimpleNamespace(
        config=cfg,
        apps=SimpleNamespace(manifests={}),
        events=SimpleNamespace(emit=lambda *a, **k: None),
        services=SimpleNamespace(get_optional=lambda _n: None),
    )
    app = FastAPI()
    register_auth(app, kernel)

    @app.get("/portal/")
    async def _portal():
        return {"ok": True}

    # A real peer address: the default "testclient" is not an IP, so the
    # loopback check would refuse it — correctly, which is its own test below.
    return TestClient(app, follow_redirects=False, client=(peer, 51234))


def _peer(client, host):
    """A SECOND client onto the SAME app, from a different address.

    Building a fresh app instead would give the remote caller its own empty
    code store, so a locality test would pass with the locality check deleted —
    the code simply would not be there. Same app, same store, different peer is
    the only shape that actually tests the check.
    """
    return TestClient(client.app, follow_redirects=False, client=(host, 51235))


def _exchange_routes(client):
    """The exchange paths this app actually registered."""
    return sorted(
        r.path for r in client.app.routes
        if getattr(r, "path", "").endswith("shell-exchange")
    )


def _mint(c, token=TOKEN):
    return c.post("/api/auth/shell-exchange", headers={"Authorization": f"Bearer {token}"})


def test_the_routes_do_not_exist_with_the_flag_off():
    c = _client(flag=False)
    assert not _exchange_routes(c)
    assert _mint(c).status_code == 404
    # 302 EXACTLY — the login redirect, i.e. still behind the auth gate. An
    # `in (302, 404)` here would pass with the exemption moved out of its `if`
    # while the routes stayed gated: the path would be permanently
    # auth-exempt-but-unrouted (404), which is a hole in the auth boundary that
    # the test named for it would call fine.
    assert c.get("/auth/shell-exchange?code=x").status_code == 302


@pytest.mark.parametrize("flagged", ["false", "0", "no", "", None])
def test_a_string_flag_does_not_open_the_route(flagged):
    """Config.get returns an env override as a STRING, so bool("false") is
    True. A dark flag any string switches on is not a dark flag — and this one
    opens an auth-exempt route."""
    c = _client(flag=flagged)
    assert not _exchange_routes(c)
    assert _mint(c).status_code == 404
    assert c.get("/auth/shell-exchange?code=x").status_code == 302


def test_there_is_nothing_to_exchange_without_a_machine_token():
    """The mint leg is bearer-only, so a password-only daemon would register a
    route nobody could ever use. Unregistered, the path falls through to the
    ordinary auth gate — 401, not a code."""
    c = _client(token="", password="hunter2")
    # Assert on the ROUTE TABLE, not a status code: an unregistered /api/ path
    # is answered 401 by the auth middleware before it can 404, so a status
    # check cannot tell "no route" from "route, no credential" — and with
    # `and bool(_auth_token)` dropped the handler would answer 403, which a
    # loose `in (401, 403, 404)` would happily accept.
    assert not _exchange_routes(c), "a password-only daemon has nothing to exchange"
    assert c.get("/auth/shell-exchange?code=x").status_code == 302, "still gated"


def test_the_happy_path_mints_once_and_sets_the_session():
    c = _client()
    assert _exchange_routes(c) == ["/api/auth/shell-exchange", "/auth/shell-exchange"]
    r = _mint(c)
    assert r.status_code == 200
    code = r.json()["code"]
    assert r.json()["expires_in"] == DEFAULT_TTL_S

    red = c.get(f"/auth/shell-exchange?code={code}&next=/portal/")
    assert red.status_code == 302
    assert red.headers["location"] == "/portal/"
    assert "eos_session" in red.cookies or "eos_session" in red.headers.get("set-cookie", "")

    # And the cookie actually works on a gated route.
    assert c.get("/portal/").status_code == 200


def test_a_session_cookie_cannot_mint_a_code():
    """THE invariant. The auth middleware would admit a valid cookie to any
    /api/ route, so the handler re-checks the header itself: a cookie is what a
    PAGE holds, and an XSS anywhere in the daemon could otherwise mint a code
    and carry a working login off the machine."""
    c = _client()
    c.cookies.set("eos_session", TOKEN)
    r = c.post("/api/auth/shell-exchange")
    assert r.status_code == 403
    assert "session" in r.json()["error"]


def test_a_wrong_bearer_cannot_mint():
    c = _client()
    assert _mint(c, token="not-the-token").status_code in (401, 403)


def test_a_code_cannot_be_redeemed_twice_over_http():
    c = _client()
    code = _mint(c).json()["code"]
    assert c.get(f"/auth/shell-exchange?code={code}").status_code == 302
    assert c.get(f"/auth/shell-exchange?code={code}").status_code == 403


def test_an_unknown_code_is_refused_and_sets_no_cookie():
    c = _client()
    r = c.get("/auth/shell-exchange?code=made-up")
    assert r.status_code == 403
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_the_redirect_target_is_clamped_at_the_route():
    """safe_next is pure; this proves the ROUTE calls it. Without the call the
    Location would carry the cookie to another origin."""
    c = _client()
    code = _mint(c).json()["code"]
    r = c.get(f"/auth/shell-exchange?code={code}&next=//evil.example/x")
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_the_redeem_route_is_reachable_without_a_session_and_the_rest_is_not():
    """It has to be exempt — it is how you become authenticated — so the
    exemption is worth pinning in both directions."""
    c = _client()
    assert c.get("/portal/").status_code in (302, 401), "gated before signing in"
    r = c.get("/auth/shell-exchange?code=nope")
    assert r.status_code == 403, "reached the handler rather than the login redirect"


def test_a_peer_that_is_not_this_machine_is_refused_on_both_legs():
    """The private LAN counts as remote here: the shell runs on the same box,
    so a LAN peer redeeming a code is someone else's machine."""
    c = _client(peer="192.168.1.50")
    assert _mint(c).status_code == 403
    assert c.get("/auth/shell-exchange?code=whatever").status_code == 403


def test_a_code_minted_here_cannot_be_redeemed_from_another_machine():
    """The interesting composite: minting is gated by the token, redeeming by
    locality, and a LEAKED code must not be spendable from the LAN. The remote
    caller shares this daemon's code store, so the only thing refusing it is
    the locality check."""
    local = _client()
    code = _mint(local).json()["code"]
    remote = _peer(local, "10.0.0.9")
    r = remote.get(f"/auth/shell-exchange?code={code}")
    assert r.status_code == 403
    assert "set-cookie" not in {k.lower() for k in r.headers}
    # And the code is still unspent — a refused attempt must not burn it.
    assert local.get(f"/auth/shell-exchange?code={code}").status_code == 302


def test_a_request_with_no_code_does_not_burn_a_rate_limit_slot():
    """A bare GET is not a failed attempt. Counting it let any page the user
    visits lock them out of /login with five <img src=...> — and the lockout
    covers the exchange too, so it is self-reinforcing."""
    c = _client()
    for _ in range(8):
        assert c.get("/auth/shell-exchange").status_code == 400
    code = _mint(c).json()["code"]
    assert c.get(f"/auth/shell-exchange?code={code}").status_code == 302, "still able to sign in"


def test_a_non_ascii_code_is_refused_rather_than_crashing():
    """hmac.compare_digest raises TypeError on a non-ASCII str, so an earlier
    constant-time loop turned ?code=cafe-with-an-accent into a 500 on the one
    route that answers without a credential."""
    c = _client()
    _mint(c)                       # a live code, so the lookup path runs
    r = c.get("/auth/shell-exchange?code=café")
    assert r.status_code == 403
    r2 = c.get("/auth/shell-exchange?code=你好")
    assert r2.status_code == 403


def test_a_spoofed_forwarded_header_does_not_make_a_remote_caller_local():
    """THE critical one. _client_ip prefers X-Forwarded-For, which the caller
    sets — so a locality gate reading it means "claims to be loopback". Both
    legs must read the socket instead."""
    local = _client()
    code = _mint(local).json()["code"]
    remote = _peer(local, "203.0.113.9")
    spoof = {"X-Forwarded-For": "127.0.0.1"}
    assert remote.get(f"/auth/shell-exchange?code={code}", headers=spoof).status_code == 403
    assert remote.post("/api/auth/shell-exchange",
                       headers={**spoof, "Authorization": f"Bearer {TOKEN}"}).status_code == 403
    # …and the code is still unspent, so the real shell can still use it.
    assert local.get(f"/auth/shell-exchange?code={code}").status_code == 302


def test_the_exemption_is_one_path_not_a_subtree():
    """A subtree exemption silently unauthenticates anything ever mounted
    beneath it — a StaticFiles mount, a sub-router — with no marker at the
    mount site. With the flag ON, a path under the route must still be gated."""
    c = _client()
    assert c.get("/auth/shell-exchange?code=x").status_code == 403, "the route itself is exempt"
    # Exempt-as-a-prefix would make this 404 (reached the router, no match);
    # exempt-as-a-path leaves it behind the gate, so it redirects to login.
    assert c.get("/auth/shell-exchange/anything").status_code == 302


def test_neither_leg_takes_its_locality_or_rate_limit_key_from_a_header():
    """The route-level twin of the spoofing test above. A remote caller is
    already refused by the socket check, so a header-derived RATE-LIMIT key is
    not reachable from outside — but a local caller could still use it to pin
    someone else's key into cooldown, and the next reader would have no way to
    tell which helper was the safe one."""
    import ast
    import inspect

    src = inspect.getsource(register_auth)
    tree = ast.parse(inspect.cleandoc(src))
    for name in ("shell_exchange_mint", "shell_exchange_redeem"):
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == name
        )
        called = {
            getattr(c.func, "id", "") or getattr(c.func, "attr", "")
            for c in ast.walk(fn) if isinstance(c, ast.Call)
        }
        assert "_peer_ip" in called, f"{name} does not read the socket address"
        assert "_client_ip" not in called, f"{name} reads a caller-supplied header"
