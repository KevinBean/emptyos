"""Desktop shell — the GUI-free half: decisions, URLs, health, the offline splash.

The EmptyOS Desktop shell is a native window (pywebview → WebView2) around the
daemon's web UI. This module holds every piece of it that has real logic and
does not need a window, so it is unit-testable under the daemon's interpreter,
which does not have ``webview`` installed. :mod:`shell` is the only module that
imports ``webview``.

**Import discipline — this module is stdlib only.** The shell runs from its own
user-home venv (``%LOCALAPPDATA%/eos/envs/desktop-3.13``: pywebview + pythonnet
+ pystray + Pillow), which does not carry the daemon's dependencies. Importing ``emptyos.sdk`` runs that
package's ``__init__`` — Starlette, BaseApp, ~370 modules in all — and fails
there. So the health probe below is a small deliberate copy of
``emptyos/sdk/daemon_launcher.py``'s, which accepts exactly this trade for
standalone entrypoints.

**Attach mode never owns the daemon.** The daily-driver shell attaches to the
user's running ``:9000``. It does not restart, stop or kill it — ``restart.bat``
and the watchdog own that (``.claude/rules/daemon-handling.md``). The one daemon
action it can take is an explicit Start — the offline splash's button or the
tray's item — and :func:`start_allowed` refuses both whenever *any* process
holds the port: booting, slow, or wedged. Only a port nobody is listening on can
be started, which is the same rule the watchdog uses before a respawn.
"""

from __future__ import annotations

import errno
import html
import json
import os
import re
import socket
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 9000
DEFAULT_START_PATH = "/portal/"

#: Probe cadence for the attach-mode watcher. The timeout and the three-miss
#: rule follow the measurement in ``.claude/rules/debugging.md`` ("A watchdog
#: recovery is not evidence of a wedge"): 96% of 5 s probe misses on this box
#: were a *live* daemon under memory pressure. Here a false "down" costs less
#: than there — it swaps the page for the splash, it never kills anything — but
#: it still discards whatever the user was typing, so it needs three misses in
#: a row: ~24 s when the daemon hangs, ~15 s when the port is closed outright
#: (a refusal returns in ~2 s) — never one slow answer.
PROBE_TIMEOUT_S = 5.0
PROBE_INTERVAL_S = 3.0
MISSES_TO_DOWN = 3

#: Tray notice shown the first time closing the window hides it instead of quitting.
HIDDEN_NOTICE = "EmptyOS is still running in the tray. Quit from the tray icon."

#: A bound to refuse absurd input, not a spec: no EmptyOS route is anywhere
#: near it, and it sits just under the 2083-char URL ceiling Edge/IE enforced.
_MAX_PATH_LEN = 2048
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


# ── paths & URLs ─────────────────────────────────────────────────────────────
def safe_local_path(path: object) -> str | None:
    """Return ``path`` if it is a safe same-origin path, else ``None``.

    Accepts only an absolute path on the daemon's own origin: it must start with
    a single ``/``, carry no scheme, no backslash, no C0/C1 control character,
    and be at most 2048 chars. ``//host`` (protocol-relative) and ``/\\host``
    both reach another origin in a browser, so both are refused. This is the one
    gate every externally supplied path passes through — a second launch's
    ``--path`` today, ``eos://`` deep links later.
    """
    if not isinstance(path, str) or not path or len(path) > _MAX_PATH_LEN:
        return None
    if not path.startswith("/") or path.startswith("//"):
        return None
    if "\\" in path or _CONTROL.search(path):
        return None
    if ":" in path.split("?", 1)[0].split("#", 1)[0]:
        return None  # a colon in the path part smells like a smuggled scheme
    return path


def base_url(port: int) -> str:
    """The daemon origin. IPv4 literal on purpose: Python resolves ``localhost``
    to ``::1`` first while a local-mode daemon binds IPv4 only."""
    return f"http://127.0.0.1:{int(port)}"


def start_url(port: int, path: str = DEFAULT_START_PATH) -> str:
    """Window URL. Never carries a token — see ``scripts/eos_desktop.py``'s
    SECURITY note; auth is a login persisted in the webview's cookie store."""
    return base_url(port) + (safe_local_path(path) or DEFAULT_START_PATH)


# ── shell login exchange (daemon side: emptyos/web/auth_exchange.py) ─────────
#
# The shell already holds the machine token (it reads emptyos.toml); the webview
# does not, so in private mode the user is asked to log in by hand to a daemon
# running as them on their own machine. The exchange trades the token the shell
# already has for the session cookie the webview needs, ONCE, without the token
# ever appearing in a URL.

EXCHANGE_MINT_PATH = "/api/auth/shell-exchange"
EXCHANGE_REDEEM_PATH = "/auth/shell-exchange"


def exchange_url(port: int, code: str, path: str = DEFAULT_START_PATH) -> str:
    """The URL that spends a code and lands on ``path``.

    A CODE, never the token: it is worth 60 seconds and one use, so the copy
    that lingers in the webview's history is worthless by the time anyone reads
    it. ``path`` goes through the same ``safe_local_path`` gate every other
    externally supplied path does — the daemon clamps it again, and neither
    side relies on the other having done so.
    """
    nxt = safe_local_path(path) or DEFAULT_START_PATH
    return (
        f"{base_url(port)}{EXCHANGE_REDEEM_PATH}"
        f"?code={urllib.parse.quote(str(code), safe='')}"
        f"&next={urllib.parse.quote(nxt, safe='/')}"
    )


def mint_exchange_code(port: int, token: str, timeout: float = PROBE_TIMEOUT_S) -> str | None:
    """Trade the held token for a one-shot code, or ``None``.

    ``None`` on every failure and it is never an error: the daemon may be older
    than this feature (404), the flag may be off, there may be no token at all
    in local mode. Each of those means "just open the window" — the user either
    needs no login or gets the ordinary one. A shell that refused to start
    because an optional convenience was unavailable would be worse than the
    manual login it exists to avoid.
    """
    if not token:
        return None
    try:
        req = urllib.request.Request(
            base_url(port) + EXCHANGE_MINT_PATH,
            data=b"",
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            body = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) and code else None


def first_url(port: int, token: str = "", path: str = DEFAULT_START_PATH) -> str:
    """Where the window should open: the exchange when it is available, else
    the plain start URL. One call so the window has one answer."""
    code = mint_exchange_code(port, token)
    return exchange_url(port, code, path) if code else start_url(port, path)


def is_daemon_url(url: object, port: int) -> bool:
    """True when ``url`` is on the daemon's origin (scheme + host + port)."""
    if not isinstance(url, str):
        return False
    base = base_url(port)
    return url == base or url.startswith(base + "/") or url.startswith(base + "?")


def is_splash_url(url: object) -> bool:
    """The offline splash is loaded with ``NavigateToString``, which WebView2
    reports as ``about:blank``. Nothing else the shell loads is an ``about:`` URL."""
    return isinstance(url, str) and url.startswith("about:")


def is_splash_source(uri: object) -> bool:
    """A document WebView2 reports as HTML the shell loaded itself:
    ``NavigateToString`` documents read back as ``about:blank`` (measured live).

    ``about:blank`` is *not* proof on its own — any page can navigate there —
    so the shell only lets a navigation reach it when it is loading the splash
    itself (a one-shot token; see ``AttachShell.on_navigation``), and the Start
    button additionally requires the shell's own view state.
    """
    return uri == "about:blank"


_DATA_HTML = "data:text/html;charset=utf-8;base64,"


def is_data_url_for(uri: object, html: str | None) -> bool:
    """True when ``uri`` is WebView2's rendering of ``NavigateToString(html)``.

    WebView2 reports that navigation as ``data:text/html;charset=utf-8;base64,``
    plus the page (measured live, 2026-09-12 — the shell's own splash was being
    blocked by its own guard until this was recognised). Decoding and comparing
    against the exact HTML the shell just rendered makes it unforgeable: a page
    cannot produce the shell's splash byte-for-byte, and Chromium refuses
    renderer-initiated top-level ``data:`` navigations anyway.
    """
    if not (isinstance(uri, str) and html and uri.startswith(_DATA_HTML)):
        return False
    import base64
    import binascii

    try:
        return base64.b64decode(uri[len(_DATA_HTML):], validate=True) == html.encode("utf-8")
    except (binascii.Error, ValueError):
        return False


# ── what reaches the page ↔ shell bridge ─────────────────────────────────────
#: The whole API a page may call. pywebview also dispatches built-in names
#: (``pywebviewStateUpdate``, ``pywebviewMoveWindow``, ``_pywebviewAlert``,
#: ``console``, ``FilesDropped`` …) *before* it consults the exposed functions —
#: and ``pywebviewStateUpdate`` writes into a script pywebview injects, unescaped,
#: into every later page load. None of them is needed here, so none gets through.
BRIDGE_NAMES = frozenset({"shell_info", "start_daemon",
                          "hide", "open_main", "set_quick_height"})
#: Only the offline splash may ask for a daemon start.
_SPLASH_ONLY = frozenset({"start_daemon"})
#: Only the quick-entry window may drive the quick-entry window. The two windows
#: are the same origin, so a page cannot be told apart by its URL — the caller is
#: identified by which webview posted the message (``window_uid``), which is
#: WebView2's own bookkeeping and not something a page can set.
_QUICK_ONLY = frozenset({"hide", "open_main", "set_quick_height"})
#: pywebview's own call ids are ``Math.random()`` digits or 11 base-36 chars. The
#: id is interpolated into a script pywebview evaluates, so anything else is refused.
_CALL_ID = re.compile(r"[A-Za-z0-9]{1,32}")


def bridge_args(name: object, params: object) -> tuple | None:
    """The positional arguments a bridge call carries, or ``None`` if refused.

    pywebview sends ``Array.prototype.slice.call(arguments)`` as a JSON string,
    so ``params`` is a JSON list — one entry per positional argument. Each name
    declares exactly what it accepts; anything else is refused rather than
    coerced, because a bridge call that half-works is harder to notice than one
    that does not arrive.

    This is the guard's copy of the contract. The exposed functions validate
    their own arguments again when pywebview hands them over — they are the ones
    that must not trust their input, and the guard only decides whether the call
    is dispatched at all.
    """
    if not isinstance(params, str):
        return None
    try:
        args = json.loads(params)
    except ValueError:
        return None
    if args == {}:            # some runtimes spell "no arguments" this way
        args = []
    if not isinstance(args, list):
        return None
    if name in ("shell_info", "start_daemon", "hide"):
        return () if not args else None
    if name == "open_main":
        # No path is legitimate: "just bring the main window forward".
        if not args:
            return ()
        if len(args) != 1:
            return None
        path = safe_local_path(args[0])
        return (path,) if path else None
    if name == "set_quick_height":
        if len(args) != 1 or isinstance(args[0], bool) or not isinstance(args[0], (int, float)):
            return None
        return (clamp_quick_height(args[0]),)
    return None


def message_allowed(source: object, raw: object, port: int,
                    splash_html_now: str | None = None,
                    window_uid: object = None, quick_uid: object = None) -> bool:
    """Whether a WebView2 web message may be dispatched by pywebview at all.

    ``source`` is WebView2's own record of which document posted it (not a
    field the page controls); ``raw`` is the message JSON. A message passes only
    as ``[<bridge name>, <args JSON>, <plain id>]`` from the daemon origin or the
    splash, with arguments matching that name's shape (:func:`bridge_args`).

    Two names are narrowed further, and both narrowings are about *which window*
    rather than which page. ``start_daemon`` is accepted only from the splash
    document itself, so no page that happens to be showing (an XSS on a daemon
    page included) can press the splash's button for it. The quick-entry verbs
    are accepted only from the quick window, so a page in the main window cannot
    hide or resize it — same origin, different window, and the window is the
    thing that can be verified.

    ``splash_html_now`` is the splash the shell last rendered, for the runtimes
    that report its source as a ``data:`` URL rather than ``about:blank``.
    """
    try:
        msg = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return False
    if not (isinstance(msg, list) and len(msg) == 3):
        return False
    name, params, call_id = msg
    if name not in BRIDGE_NAMES or not isinstance(call_id, str) or not _CALL_ID.fullmatch(call_id):
        return False
    if bridge_args(name, params) is None:
        return False
    from_splash = is_splash_source(source) or is_data_url_for(source, splash_html_now)
    if name in _SPLASH_ONLY:
        return from_splash
    if name in _QUICK_ONLY:
        # An unknown uid on either side is not a match: a quick window that does
        # not exist cannot be the caller.
        return bool(quick_uid) and window_uid == quick_uid and is_daemon_url(source, port)
    return from_splash or is_daemon_url(source, port)


# ── quick entry ──────────────────────────────────────────────────────────────
#: The quick window opens as a single composer row and grows as an answer
#: streams in. The floor is one row of chrome; the ceiling keeps it a *panel* —
#: past this the main window is the right surface, and ``open_main`` is how the
#: page gets there.
QUICK_MIN_HEIGHT = 120
QUICK_MAX_HEIGHT = 720
#: The narrowest panel :func:`quick_geometry` will produce. Named rather than
#: left a literal because QUICK_START_HEIGHT's measurement below is stated
#: against it ("the NARROWEST panel quick_geometry produces — which is 360"),
#: so moving one silently invalidates the other.
QUICK_MIN_WIDTH = 360
#: What the composer actually measures before anything has been typed, at the
#: NARROWEST panel :func:`quick_geometry` produces — which is 360, its floor.
#: Measured in a browser against the real page (2026-09-12, ``.portal-main``
#: scrollHeight): 187px at 720 wide, 194 at 640, 207 at 560, and 249 at each of
#: 480, 420 and 360, where the verb chips wrap to a second row and stay there.
#:
#: Sized for the tallest of those on purpose. The panel corrects itself ~200ms
#: after it loads, so the only question is which way to be wrong in the meantime,
#: and the two are not symmetric: too tall is a strip of empty panel, while too
#: short cuts off the bottom of the composer — which is where the Send button is.
QUICK_START_HEIGHT = 250
QUICK_MAX_WIDTH = 720
QUICK_DEFAULT_PATH = "/portal/?quick=1"
#: Breathing room below a fully-grown panel, so it does not sit flush against
#: the bottom of the usable area. NOT the taskbar — the taskbar is already
#: excluded, because :func:`shell._primary_screen` reports the WORK AREA
#: (``SPI_GETWORKAREA``) rather than the whole screen, which also covers a
#: taskbar docked left, right or top. A round 24px of margin; nothing measures
#: it, and nothing depends on the exact figure.
QUICK_BOTTOM_MARGIN = 24


def clamp_quick_height(value: object, ceiling: object = QUICK_MAX_HEIGHT) -> int:
    """A height the page asked for, clamped to the panel band.

    Clamped rather than refused: the page measures its own content, and a
    measurement that lands outside the band is a long answer or an empty one,
    not an attack. Non-finite input (``NaN``/``inf`` survive a JSON round-trip
    in every browser) falls back to the opening height, because every comparison
    against NaN is False and an unguarded clamp would pass it straight through.

    ``ceiling`` is how a *small screen* narrows the band — see
    :func:`quick_geometry`, which computes it. It can only ever lower the top of
    the band, never raise it above :data:`QUICK_MAX_HEIGHT`, and never below the
    floor: a caller passing nonsense gets the ordinary band back rather than a
    window it cannot see.
    """
    # OverflowError, not just TypeError/ValueError: JSON has no integer bound, so
    # `json.loads` happily produces a 400-digit Python int, and `int.__float__`
    # raises OverflowError — an ArithmeticError, which neither of the other two
    # catches. This function runs inside the web-message guard, on input any
    # daemon page can post, so an escape here is an exception thrown out of a
    # .NET event handler on the WebView2 message pump rather than a refusal.
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return QUICK_START_HEIGHT
    if n != n or n in (float("inf"), float("-inf")):
        return QUICK_START_HEIGHT
    try:
        top = int(ceiling)
    except (TypeError, ValueError, OverflowError):
        top = 0
    # A ceiling of zero or less is UNSET, not "a screen zero pixels tall".
    # Reading it as a real bound pins the window to its floor — a 120px panel
    # that can never grow, and silently so.
    top = QUICK_MAX_HEIGHT if top <= 0 else max(QUICK_MIN_HEIGHT, min(QUICK_MAX_HEIGHT, top))
    return int(max(QUICK_MIN_HEIGHT, min(top, n)))


def quick_geometry(screen_width: object, screen_height: object) -> dict:
    """Where the quick window opens: centred, high on the screen.

    High rather than centred vertically — the window grows *downward* as an
    answer arrives, and a vertically-centred panel would either walk up the
    screen or run off the bottom. Around a quarter down is the aesthetic
    position; it is then raised as far as it must be for a **fully-grown**
    panel to fit, and where even the top of the screen is not enough the
    returned ``max_height`` lowers the ceiling instead.

    Both clamps are needed and neither is hypothetical: at 1280x720 a panel
    opened at the aesthetic position runs 201px off the bottom, and at 800x600
    no opening position fits :data:`QUICK_MAX_HEIGHT` at all. Without them the
    window looks right when it appears and silently cuts off long answers —
    on exactly the small displays where it is hardest to move it.

    An unreadable screen size needs no branch of its own, which is worth stating
    because a guard clause for it looks obviously necessary and is not: the
    floors below already turn 0 (or a negative, or a non-number) into a
    360-wide window at the top-left corner — small and in a corner, but on
    screen, which is the only property that matters. A mutation removing an
    earlier explicit fallback survived every test, because there was nothing
    left for it to change.
    """
    try:
        sw, sh = int(screen_width), int(screen_height)
    except (TypeError, ValueError):
        sw = sh = 0
    width = max(QUICK_MIN_WIDTH, min(QUICK_MAX_WIDTH, int(sw * 0.5)))
    y = max(0, min(int(sh * 0.28), sh - QUICK_MAX_HEIGHT - QUICK_BOTTOM_MARGIN))
    if sh <= 0:
        # Nothing is known about the screen, so nothing is concluded about it:
        # the ordinary ceiling, rather than one derived from a zero.
        max_height = QUICK_MAX_HEIGHT
    else:
        max_height = max(QUICK_MIN_HEIGHT,
                         min(QUICK_MAX_HEIGHT, sh - y - QUICK_BOTTOM_MARGIN))
    return {
        "width": width,
        "height": QUICK_START_HEIGHT,
        "x": max(0, (sw - width) // 2),
        "y": y,
        "max_height": max_height,
    }


def quick_url(port: int, path: str = QUICK_DEFAULT_PATH) -> str:
    """The quick window's page. Never goes through the login exchange: the quick
    window is preloaded at startup and the main window has already spent the
    one-shot code, and both windows share one WebView2 profile — so the cookie
    the main window received is already this window's cookie."""
    return base_url(port) + (safe_local_path(path) or QUICK_DEFAULT_PATH)


#: Schemes the OS may open by default for a link on an EmptyOS page. Anything
#: else must be listed in ``[desktop] external_schemes`` (e.g. the scheme of the
#: vault viewer's "open external" link). An allowlist on purpose: Windows
#: registers dozens of protocol handlers (``search-ms:``, ``ms-msdt:``,
#: ``ms-officecmd:``…) that reach ShellExecute, and a browser would at least
#: prompt before handing one on.
DEFAULT_EXTERNAL_SCHEMES = ("mailto",)
#: Never handed to the OS, even when configured: they load local files or run script.
NEVER_EXTERNAL = frozenset({"file", "javascript", "vbscript", "data", "blob",
                            "filesystem", "about", "view-source", "shell"})
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]{0,31}")


def scheme_of(uri: object) -> str:
    """Lower-case URI scheme, or ``""``."""
    if not isinstance(uri, str) or ":" not in uri:
        return ""
    s = uri.split(":", 1)[0].lower()
    return s if _SCHEME.fullmatch(s) else ""


def clean_external_schemes(values: object) -> tuple[str, ...]:
    """Validate ``[desktop] external_schemes``: well-formed, never a NEVER_EXTERNAL."""
    out = list(DEFAULT_EXTERNAL_SCHEMES)
    if isinstance(values, (list, tuple)):
        for v in values:
            s = str(v).strip().lower().rstrip(":")
            if _SCHEME.fullmatch(s) and s not in NEVER_EXTERNAL and s not in out:
                out.append(s)
    return tuple(out)


def navigation_decision(uri: object, current: object, port: int,
                        external_schemes: tuple[str, ...] = DEFAULT_EXTERNAL_SCHEMES) -> str:
    """What to do with a top-level navigation (or popup) before it happens.

    - ``"allow"`` — the daemon origin.
    - ``"splash"`` — ``about:blank``; the caller allows it only while the shell
      is loading the splash itself.
    - ``"browser"`` — a web page on another origin: opened in the system
      browser, never in this window, so its scripts never run next to the shell.
    - ``"os"`` — an allowed non-web scheme clicked on a daemon page.
    - ``"unlisted"`` — a non-web scheme clicked on a daemon page that isn't in
      the allowlist: refused, and the user is told how to allow it.
    - ``"block"`` — everything else, silently: ``file:`` (a file dropped on the
      window), script-bearing schemes, and any non-web scheme from anything
      but a daemon page.
    """
    if not isinstance(uri, str) or not uri:
        return "block"
    if is_daemon_url(uri, port):
        return "allow"
    if is_splash_source(uri):
        return "splash"
    scheme = scheme_of(uri)
    if scheme in ("http", "https"):
        return "browser"
    if not scheme or scheme in NEVER_EXTERNAL or not is_daemon_url(current, port):
        return "block"
    return "os" if scheme in external_schemes else "unlisted"


def state_dir(repo_root: Path) -> Path:
    """Attach-mode shell state: webview storage + ``shell.log``.

    Under the repo's gitignored ``data/``, deliberately separate from a product
    install's ``%APPDATA%\\EmptyOS`` so the daily driver and a packaged product
    never share one WebView2 profile.
    """
    return repo_root / "data" / "desktop-shell"


def instance_name(port: int) -> str:
    """Single-instance mutex name for an attach shell on ``port``.

    Per-port, so a shell on :9000 never blocks one on a sandbox member, and
    ``Local\\`` so it is scoped to the interactive session. Distinct from the
    *tray* presence mutex the daemon probes (``emptyos/desktop_presence.py``):
    a running shell without a tray must not make the daemon's tray stand down.
    """
    return f"Local\\EmptyOS.Desktop.attach-{int(port)}"


def pipe_address(port: int) -> str:
    """Named pipe a second launch uses to hand its request to the running shell.

    Carries the user name so two users on one machine don't collide on the
    (machine-wide) pipe namespace. It is not an access boundary — see
    :mod:`single_instance` for what the pipe accepts and why that is safe.
    """
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", os.environ.get("USERNAME") or "user")
    return f"\\\\.\\pipe\\EmptyOS.Desktop.{user}.attach-{int(port)}"


# ── second-launch IPC ────────────────────────────────────────────────────────
def validate_ipc(msg: object) -> dict | None:
    """Normalise a message from a second launch, or ``None`` if it is not one.

    The whole vocabulary is ``{"cmd": "show", "path"?: <safe local path>}``.
    Anything else is dropped rather than half-honoured.
    """
    if not isinstance(msg, dict) or msg.get("cmd") != "show":
        return None
    out: dict = {"cmd": "show"}
    if "path" in msg and msg["path"] is not None:
        path = safe_local_path(msg["path"])
        if path is None:
            return None
        out["path"] = path
    return out


# ── health ───────────────────────────────────────────────────────────────────
def probe_health(port: int, timeout: float = PROBE_TIMEOUT_S) -> dict | None:
    """GET ``/api/health`` (auth-exempt). Parsed body, or ``None`` on no answer."""
    try:
        with urllib.request.urlopen(base_url(port) + "/api/health", timeout=timeout) as r:  # noqa: S310
            body = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def is_up(payload: dict | None) -> bool:
    """A daemon is *up* only when it says ``status: ok``.

    ``{"status": "starting", "apps": 0}`` answers but has loaded nothing — a page
    opened then would 404 on every app route, so the window keeps the splash
    until it is really ready. (That is a *view* decision. Whether the daemon can
    be started is :func:`start_allowed`'s, and a booting daemon is never startable.)
    """
    return isinstance(payload, dict) and payload.get("status") == "ok"


_WILDCARD_HOSTS = {"", "0.0.0.0", "::", "localhost", "127.0.0.1", "::1"}


def listen_hosts(bind_host: object = None) -> tuple[str, ...]:
    """Every address a daemon configured with ``[network] host`` could answer on.

    IPv4 and IPv6 loopback always (``0.0.0.0`` answers on the first; ``::`` —
    IPv6-only by default on Windows — on the second), plus the configured host
    when it names one specific address (a LAN or Tailscale IP binds nothing on
    loopback). ``restart.bat`` kills whatever holds the port on *any* address,
    so "nothing is listening" has to mean all of them.
    """
    hosts = ["127.0.0.1", "::1"]
    if isinstance(bind_host, str) and bind_host.strip() not in _WILDCARD_HOSTS:
        hosts.append(bind_host.strip())
    return tuple(hosts)


def _probe_one(host: str, port: int, timeout: float) -> bool | None:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except ConnectionRefusedError:
        return False
    except OSError as e:
        if getattr(e, "winerror", None) == 10061 or e.errno == errno.ECONNREFUSED:
            return False
        if e.errno in (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT) or getattr(e, "winerror", None) in (10049, 10047):
            return False  # that address family / address doesn't exist here: nothing can listen on it
        return None


def port_listening(port: int, timeout: float = 3.0,
                   hosts: tuple[str, ...] = ("127.0.0.1", "::1")) -> bool | None:
    """``False`` only when every host refuses, ``True`` when any accepts,
    ``None`` otherwise (a timeout is unknown, and unknown is not a "no").

    The timeout must outlast Windows' refusal: a connect to a closed loopback
    port is not refused at once — the stack retries the SYN after the RST and
    reports ``WSAECONNREFUSED`` only after ~2 s (measured 2.04 s, 2026-09-11).
    Below that, a free port reads as *unknown* and the Start button never shows.
    The hosts are probed in parallel so the answer still takes ~2 s, not 2 s each.
    """
    import threading

    results: dict[str, bool | None] = {}

    def run(h: str) -> None:
        results[h] = _probe_one(h, port, timeout)

    threads = [threading.Thread(target=run, args=(h,), daemon=True) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 1.0)
    answers = [results.get(h) for h in hosts]
    if any(a is True for a in answers):
        return True
    if answers and all(a is False for a in answers):
        return False
    return None


class HealthWatch:
    """Turn a stream of probe results into ``"went_down"`` / ``"came_up"`` edges.

    The first observation decides the opening view immediately — a daemon that
    is down at launch shows the splash at once, not after several probes. After
    that, an *up* daemon needs :data:`MISSES_TO_DOWN` consecutive misses before
    it is declared down, and one good probe brings it back.
    """

    def __init__(self, misses_to_down: int = MISSES_TO_DOWN):
        self.misses_to_down = max(1, int(misses_to_down))
        self.state = "unknown"
        self._misses = 0

    def observe(self, up: bool) -> str | None:
        if up:
            self._misses = 0
            if self.state != "up":
                self.state = "up"
                return "came_up"
            return None
        self._misses += 1
        if self.state == "up" and self._misses < self.misses_to_down:
            return None
        if self.state != "down":
            self.state = "down"
            return "went_down"
        return None


def start_allowed(*, on_splash: bool, listening: bool | None) -> bool:
    """Gate for the splash's "Start daemon" button.

    Running ``restart.bat`` kills every ``python.exe`` on the machine, so the
    button may only fire when (1) the call comes while the window is showing the
    splash — a daemon page or a foreign site that reaches the shell's API is not
    on the splash — and (2) a fresh check finds **nothing** listening on the
    port. A booting, slow or wedged daemon still holds its port, and an unknown
    answer (``None``) is not a "no".
    """
    return bool(on_splash) and listening is False


def close_action(*, tray_alive: bool, quitting: bool, reason: str = "UserClosing") -> str:
    """What closing the main window does: ``"hide"`` (to the tray) or ``"close"``.

    ``reason`` is the WinForms ``CloseReason`` name. Only the user's own close
    (``UserClosing`` — the X button, Alt+F4) hides. Everything else closes:
    ``WindowsShutDown`` is sign-out, restart *and* Restart Manager asking an
    installer's open files to close — cancelling it vetoes all three
    ("EmptyOS is preventing restart"); ``TaskManagerClosing`` is "End task".
    Measured live: cancelling on any reason made ``WM_QUERYENDSESSION`` answer
    0 (veto). And hide only with a tray icon actually running to come back to,
    never while quitting.
    """
    return "hide" if tray_alive and not quitting and reason == "UserClosing" else "close"


def tray_menu(*, port: int, daemon_state: str, can_start: bool, starting: bool) -> list[dict]:
    """The shell's tray menu as data: ``{id, label, enabled, default?}`` rows,
    ``"-"`` for a separator. Pure so the menu's rules are testable without pystray.

    Attach mode never offers Restart or Stop — the daemon isn't the shell's to
    manage. Start appears only when the daemon is down and a starter exists; it
    still has to pass the port-free gate when clicked.
    """
    up = daemon_state == "up"
    if starting:
        status = "Daemon: starting…"
    elif up:
        status = f"Daemon: running on :{int(port)}"
    else:
        status = f"Daemon: not answering on :{int(port)}"
    rows: list[dict] = [
        {"id": "open", "label": "Open EmptyOS", "enabled": True, "default": True},
        {"id": "browser", "label": "Open in browser", "enabled": up},
        "-",
        {"id": "status", "label": status, "enabled": False},
    ]
    if can_start and not up and not starting:
        rows.append({"id": "start", "label": "Start daemon (restart.bat)…", "enabled": True})
    rows += ["-", {"id": "quit", "label": "Quit (daemon keeps running)", "enabled": True}]
    return rows


def can_offer_start(executable: str) -> bool:
    """The Start button is only offered when the shell itself survives it.

    ``restart.bat`` runs ``taskkill /F /IM python.exe``; a shell running as
    ``python.exe`` would be killed by its own button (before the daemon it
    started could come up). ``pythonw.exe`` is not matched, so it survives.
    """
    return Path(executable).name.lower() != "python.exe"


# ── offline splash ───────────────────────────────────────────────────────────
def splash_html(port: int, *, listening: bool | None, can_start: bool,
                starting: bool = False) -> str:
    """The page shown while the daemon is not ready.

    Self-contained on purpose — no daemon, so no theme.css to load; the palette
    is inline. Two situations read differently: nothing is listening (the
    daemon is gone — the Start button may appear), or something holds the port
    but isn't ready (booting or unresponsive — wait, never start a second one).
    """
    addr = html.escape(base_url(port))
    if starting:
        heading = "Starting EmptyOS"
        status = "Starting the daemon — this window reconnects on its own."
    elif listening is False:
        heading = "EmptyOS is offline"
        status = f"Nothing is listening at {addr}."
    else:
        heading = "EmptyOS isn't ready"
        status = f"Something holds {addr} but it isn't answering yet — it may still be starting."
    button = (
        '<button id="start" onclick="startDaemon()">Start daemon (restart.bat)</button>'
        if can_start and listening is False and not starting else ""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>EmptyOS</title>
<style>
:root{{color-scheme:light dark}}
body{{margin:0;height:100vh;display:grid;place-items:center;font:15px system-ui,sans-serif;
background:#15161a;color:#e8e8ea}}
@media (prefers-color-scheme: light){{body{{background:#f6f6f4;color:#1b1b1f}}}}
main{{max-width:30rem;padding:1.5rem;text-align:center}}
h1{{font-size:1.25rem;margin:0 0 .5rem}}
p{{opacity:.8;line-height:1.5}}
button{{font:inherit;padding:.55rem 1rem;border-radius:.5rem;border:1px solid #888;
background:transparent;color:inherit;cursor:pointer;margin-top:.75rem}}
small{{display:block;margin-top:1.25rem;opacity:.6}}
</style></head><body><main>
<h1>{heading}</h1>
<p id="status">{status}</p>
{button}
<small>Checking every few seconds. The window returns to EmptyOS when it answers.</small>
</main><script>
function startDaemon(){{
  var b=document.getElementById('start'); if(b) b.disabled=true;
  document.getElementById('status').textContent='Starting the daemon — approve the UAC prompt if one appears.';
  if(window.pywebview&&window.pywebview.api) window.pywebview.api.start_daemon();
}}
</script></body></html>"""


# ── interpreter for the shell ────────────────────────────────────────────────
def default_desktop_python(localappdata: str | None = None) -> Path:
    """The shell's windowless interpreter in its user-home venv.

    ``pythonw.exe``, not ``python.exe`` — see :func:`can_offer_start`. The
    fallback when ``LOCALAPPDATA`` is unset matches
    ``emptyos/sdk/userhome_venv.py``'s canonical root (a deliberate copy; the
    shell cannot import the SDK).
    """
    root = localappdata or os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "eos" / "envs" / "desktop-3.13" / "Scripts" / "pythonw.exe"


def resolve_desktop_python(env: dict | None = None) -> Path | None:
    """``EOS_DESKTOP_PYTHON`` if set and present, else the default venv, else None.

    An override may name any interpreter; the Start button's safety does not
    depend on it, because :func:`can_offer_start` checks the running image.
    """
    env = os.environ if env is None else env
    override = env.get("EOS_DESKTOP_PYTHON")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    p = default_desktop_python(env.get("LOCALAPPDATA"))
    return p if p.is_file() else None
