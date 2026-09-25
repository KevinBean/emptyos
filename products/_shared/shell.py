"""Desktop shell — the native window (pywebview → WebView2), attach mode.

The daily-driver EmptyOS window: a real native window around the running
``:9000`` daemon, with one instance per port, a login that persists across
restarts, and an offline splash instead of a browser error page. Launched by
``scripts/eos_desktop.py --webview``.

This is the **only** shell module that imports ``webview`` (lazily, inside
:meth:`AttachShell.run`); the decisions it acts on live in :mod:`shell_core` so
they can be tested without a GUI. It runs from the shell's own venv, so it
imports nothing from ``emptyos.sdk``.

**Three layers keep pages away from the shell** — each found by hostile review,
each necessary on its own:

1. **Foreign documents never load here.** :func:`install_bridge_guard` cancels
   every top-level navigation and popup off the daemon origin *before* it
   happens: web links go to the system browser, a short allowlist of other
   schemes (``[desktop] external_schemes``) to the OS, and ``file:`` — a file
   dropped on the window — nowhere. A foreign page's scripts would otherwise
   run next to the bridge long before any ``loaded`` event could react. The
   guard only exists on WebView2 and a tested pywebview, so the shell refuses
   to open on anything else.
2. **Web messages are filtered before pywebview sees them.** pywebview dispatches
   built-in names ahead of the API — ``pywebviewStateUpdate`` is written,
   unescaped, into a script injected into every later page, and the page's call
   id is pasted into ``evaluate_js``. :func:`shell_core.message_allowed` passes
   only the bridge names, with a plain id and arguments of the declared shape,
   from the daemon origin or the splash — judged by the *sending document*
   WebView2 reports, and for two of them by the *sending window* as well
   (``start_daemon`` from the splash alone; the quick-entry verbs from the quick
   window alone, since both windows share the daemon's origin).
3. **Nothing is reachable through ``js_api``.** pywebview resolves a call by a
   plain ``getattr`` walk from ``js_api`` (dunders included), so the shell passes
   an :class:`_InertRoot` whose every lookup fails, and publishes its functions
   with ``window.expose``, which dispatches by exact name.

Lifetime is ``webview.start()``, which returns once the window is destroyed.
With the tray up, closing the window only hides it (``close_action``); the app
ends from the tray's Quit, or on close when there is no tray to come back to.
The daemon is never touched on the way out — attach mode does not own it —
and while this shell's tray icon is up, the daemon's own tray icon stands down
(``emptyos/desktop_presence.py``). The close is never cancelled while Windows is
ending the session, or the shell would veto every sign-out and restart.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from . import single_instance
from .shell_core import (
    first_url,
    DEFAULT_EXTERNAL_SCHEMES,
    DEFAULT_START_PATH,
    HIDDEN_NOTICE,
    PROBE_INTERVAL_S,
    QUICK_DEFAULT_PATH,
    QUICK_MIN_HEIGHT,
    QUICK_MIN_WIDTH,
    QUICK_START_HEIGHT,
    HealthWatch,
    clamp_quick_height,
    close_action,
    instance_name,
    is_daemon_url,
    is_data_url_for,
    is_splash_source,
    is_splash_url,
    is_up,
    listen_hosts,
    message_allowed,
    navigation_decision,
    pipe_address,
    port_listening,
    probe_health,
    quick_geometry,
    quick_url,
    safe_local_path,
    scheme_of,
    splash_html,
    start_allowed,
    start_url,
    state_dir,
    tray_menu,
)

SHELL_VERSION = "0.1.0"

#: The bridge guard replaces pywebview internals (EdgeChrome's event handlers)
#: and is only proven against these releases — a new one could add a
#: subscription the guard doesn't see. Refuse anything else rather than run
#: unguarded; bump this after re-checking webview/platforms/edgechromium.py
#: (tests/test_unit_desktop_shell.py reads the installed source for exactly that).
TESTED_PYWEBVIEW = frozenset({"6.2.1"})

#: How long a second launch keeps trying to reach a primary that holds the lock
#: but may not be listening yet (it takes the mutex before it opens the pipe).
FORWARD_RETRY_S = 4.0

#: How long "starting" may last before the splash offers Start again.
#: ``restart.bat``'s own watchdog comment puts a boot at 70-80 s, and it first
#: waits on external services; a declined UAC prompt means no boot at all.
START_TIMEOUT_S = 180.0

#: How long a fresh tray may take to put its icon on screen before the shell
#: gives up on it. pystray is ready in well under a second; this only matters
#: if Explorer is still starting (logon), when pystray re-adds the icon on
#: ``TaskbarCreated`` anyway.
TRAY_GRACE_S = 10.0

#: How long run() waits for the tray icon before opening, so presence is claimed
#: at once in the normal case instead of a watcher tick (3 s) later.
TRAY_READY_WAIT_S = 1.5


class _Log:
    """Append-only ``shell.log``. The shell runs under ``pythonw``, which has no
    console, so this file is the only place a failure is ever written down."""

    def __init__(self, path: Path):
        self.path = path

    def __call__(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


class _InertRoot:
    """The ``js_api`` object: resolves no attribute at all.

    See the module docstring. ``__getattribute__`` makes every ``getattr`` walk
    from a page stop here (pywebview treats the failure as "no such function");
    ``__dir__`` keeps pywebview's own listing from finding anything to publish.
    """

    __slots__ = ()

    def __getattribute__(self, name):
        raise AttributeError(name)

    def __dir__(self):
        return []


class AttachShell:
    """One window attached to a daemon it does not own."""

    def __init__(self, *, port: int, start_path: str, title: str,
                 width: int, height: int, log: Callable[[str], None],
                 start_daemon: Callable[[], bool] | None = None, starting: bool = False,
                 probe: Callable[[int], dict | None] = probe_health,
                 listening: Callable[[int], bool | None] = port_listening,
                 external_schemes: tuple[str, ...] = DEFAULT_EXTERNAL_SCHEMES,
                 auth_token: str = "", quick_shortcut: str = ""):
        self.port = port
        self.external_schemes = external_schemes
        # first_url, not start_url: when the daemon offers the login exchange
        # (A3) this spends a one-shot code so the webview arrives signed in,
        # instead of asking the user to log in by hand to a daemon running as
        # them. The TOKEN never reaches the URL — only a code worth 60 seconds
        # and one use — and every failure to mint falls back to start_url, so
        # an older daemon, the flag off, or local mode all just open the window.
        self.start = first_url(port, auth_token, start_path)
        self.title = title
        self.width = width
        self.height = height
        self.log = log
        self._start_daemon_fn = start_daemon
        self._probe = probe
        self._listening = listening
        self.watch = HealthWatch()
        self.window = None
        self.last_url: str | None = None  # last daemon page, restored after an outage
        # True while a start the user asked for is in flight (--start, or the
        # splash button) — the splash then says "starting" and offers no second
        # button, since a second restart.bat would kill the first one's daemon.
        self._starting = starting
        self._starting_since = time.monotonic() if starting else 0.0
        self._start_lock = threading.Lock()
        self._splash_key: tuple | None = None
        # Which view the SHELL put in the window. Tracked here, never read back
        # from the window: pywebview reports get_current_url() == None for HTML
        # it loaded itself (edgechromium on_navigation_completed), so the URL
        # cannot identify the splash — and only the shell ever loads the splash
        # or navigates away from it, so a page cannot fake this state.
        self._on_splash = False
        # One-shot: set just before the shell loads the splash, consumed by the
        # navigation it causes. Any other navigation to about:blank — a page
        # sending the window there, a popup — finds no token and is refused.
        self._expect_splash = False
        self._splash_html: str | None = None
        self._drops = 0
        self._last_notice: dict[str, float] = {}
        self.tray = None             # TrayHandle once the tray is up; None → close quits
        self._presence = None        # the tray-presence mutex the daemon's tray defers to
        self._presence_lock = threading.Lock()
        self._tray_started_at = 0.0
        self._tray_seen_alive = False
        self._quitting = False
        self._stopping = False       # run() is tearing down: tray death is expected
        self._reason_hooked = False  # our FormClosing hook decides hide/close by CloseReason
        self._told_hidden = False
        self._notice_lock = threading.Lock()
        # -- quick entry (empty shortcut = the whole feature is off) -----------
        self.quick_shortcut = quick_shortcut
        self.quick = None            # the frameless quick window, created hidden
        self._quick_uid = None       # which webview may call the quick-only verbs
        self._quick_claim_settled = False  # a late presence claim is attempted once
        self._quick_w = 0            # its width, needed to resize by height alone
        self._quick_max_h = None     # the screen's own ceiling (quick_geometry)
        self._quick_visible = False  # pywebview cannot be asked, so we remember
        self._quick_lock = threading.Lock()
        # Serialises a WHOLE press. `_quick_lock` guards each state transition,
        # but a toggle is read-then-act across two separate acquisitions of it,
        # so two presses can interleave between them. Deliberately a second
        # lock rather than making `_quick_lock` reentrant: the UI-thread work
        # (`_activate`, `evaluate_js`) must NOT run under `_quick_lock`, and the
        # page's `eos:quick-shown` handler calls back into bridge verbs that
        # take `_quick_lock` — nothing on that path takes this one, so holding
        # it across the slow half cannot deadlock.
        self._toggle_lock = threading.Lock()
        self._quick_reason_hooked = False
        self._hotkey = None
        self._quick_presence = None  # mutex that makes command-launcher stand down

    # -- the page-facing API (see module docstring) ----------------------------
    def exposed(self, role: str = "main") -> tuple[Callable, ...]:
        """The functions published to one window via ``window.expose``.

        **Per window, not one set for both.** The guard's uid narrowing keeps
        the quick verbs off the main window and Start off the quick one, but
        publishing every name to every window means that narrowing is the *only*
        thing standing between them — so each window is handed just the verbs it
        has a use for, and the guard becomes the second layer it is described as.

        What makes them safe beyond that is pywebview's exact-name dict lookup
        for exposed functions, not their shape — a page cannot walk *from* them,
        because a dotted name never matches a key and falls through to the inert
        root.

        Each still validates its own arguments. The guard has already refused
        anything malformed (:func:`shell_core.bridge_args`), but these functions
        are what pywebview actually calls, so they are what must not trust their
        input — a guard that stops being reached must not silently widen them.
        """
        shell = self

        def shell_info() -> dict:
            return {"shell": "eos-desktop", "version": SHELL_VERSION,
                    "mode": "attach", "port": shell.port,
                    "quick": bool(shell.quick_shortcut)}

        def start_daemon() -> dict:
            return shell.start_daemon()

        def hide() -> dict:
            # Reports what actually happened, like its neighbours: there is no
            # window when quick entry is off, and `hide` can fail.
            return {"ok": shell.hide_quick()}

        def open_main(path: object = None) -> dict:
            return shell.open_main(path)

        def set_quick_height(px: object = None) -> dict:
            return shell.set_quick_height(px)

        if role == "quick":
            return shell_info, hide, open_main, set_quick_height
        return shell_info, start_daemon

    # -- views -----------------------------------------------------------------
    def _load(self, url: str) -> None:
        """Every navigation the shell makes goes through here, so the view
        state can never say "splash" while a page is showing."""
        self._on_splash = False
        self.window.load_url(url)

    def _build_splash(self, listening: bool | None) -> str:
        """Record the splash about to be loaded. The guard recognises its
        navigation (and its messages) only by matching this exact HTML."""
        self._splash_key = (listening, self._starting)
        self._on_splash = True
        self._expect_splash = True
        self._splash_html = splash_html(
            self.port, listening=listening,
            can_start=self._start_daemon_fn is not None, starting=self._starting)
        return self._splash_html

    def _render_splash(self, listening: bool | None) -> None:
        self.window.load_html(self._build_splash(listening))

    def _show_splash(self) -> None:
        # ``last_url`` is already current — _on_loaded records every daemon page.
        # (Asking the window instead blocks up to 20 s on a page mid-load.)
        self._render_splash(self._listening(self.port))

    def _on_loaded(self) -> None:
        """Record the daemon page that loaded, and bounce anything else.

        The navigation guard already refuses foreign documents before they load,
        so the bounce branch is a backstop, not the boundary.
        """
        try:
            url = self.window.get_current_url() or ""
        except Exception:
            return
        if is_daemon_url(url, self.port):
            self.last_url = url
            self._on_splash = False  # a daemon page is showing, however it got there
            return
        if is_splash_url(url) or not url:
            return  # the splash (HTML the shell loaded reports no URL) and blank loads
        self._on_splash = False
        if navigation_decision(url, "", self.port) == "browser":
            self.log(f"foreign page reached the window → system browser: {url[:200]}")
            threading.Thread(target=_open_quietly, args=(webbrowser.open, url), daemon=True).start()
        else:
            self.log(f"foreign document reached the window, not handed on: {url[:120]}")
        self._load(self.last_url or self.start)

    # -- the guard's decisions (see install_bridge_guard) -----------------------
    def on_navigation(self, uri: str, current: str) -> bool:
        """A top-level navigation is about to start. ``True`` lets it proceed."""
        if self._expect_splash and (is_splash_source(uri) or is_data_url_for(uri, self._splash_html)):
            self._expect_splash = False  # one-shot: the shell's own splash load
            return True
        decision = navigation_decision(uri, current, self.port, self.external_schemes)
        if decision == "allow":
            self._on_splash = False  # before the page's scripts can run
            return True
        if decision == "splash":
            self._note_drop("navigation to about:blank refused (the shell didn't load the splash)")
            return False
        self._hand_off(decision, uri)
        return False

    def on_new_window(self, uri: str, current: str) -> None:
        """A page asked for a new window (``target=_blank``, ``window.open``).

        Never a second window, and never pywebview's default — which hands any
        URI to ``webbrowser.open`` (``os.startfile`` on Windows) with no user
        gesture. A daemon page opens in this window; everything else goes
        through the same hand-off rules as a navigation.
        """
        decision = navigation_decision(uri, current, self.port, self.external_schemes)
        if decision == "allow":
            self._load(uri)
        elif decision == "splash":
            self._note_drop("popup to about:blank refused")
        else:
            self._hand_off(decision, uri)

    def _hand_off(self, decision: str, uri: str) -> None:
        if decision == "browser":
            self.log(f"link to another site → system browser: {uri[:200]}")
            threading.Thread(target=_open_quietly, args=(webbrowser.open, uri), daemon=True).start()
        elif decision == "os" and hasattr(os, "startfile"):
            self.log(f"external link handed to the OS: {uri[:80]}")
            threading.Thread(target=_open_quietly, args=(os.startfile, uri), daemon=True).start()
        elif decision == "unlisted":
            self._notify_unlisted(uri)
        else:
            self._note_drop(f"navigation blocked: {uri[:120]}")

    def _notify_unlisted(self, uri: str) -> None:
        """Explain a refused link instead of doing nothing — at most once per
        scheme per minute, so a page looping over links can't stack dialogs."""
        scheme = scheme_of(uri)
        now = time.monotonic()
        if now - self._last_notice.get(scheme, -1e9) < 60:
            return
        self._last_notice[scheme] = now
        self.log(f"'{scheme}:' link refused — not in [desktop] external_schemes")
        threading.Thread(target=_message_box, daemon=True, args=(
            f"EmptyOS Desktop didn't open a '{scheme}:' link.\n\n"
            f"To allow links of this kind, add \"{scheme}\" to external_schemes "
            f"under [desktop] in emptyos.toml, then reopen the window.",)).start()

    def on_message(self, source: str, raw: str, window_uid: object = None) -> bool:
        """A web message arrived. ``True`` lets pywebview dispatch it.

        ``window_uid`` is which webview posted it — pywebview's own per-window
        id, read off the renderer instance rather than from the message, so a
        page cannot claim to be the quick window.
        """
        if message_allowed(source, raw, self.port, splash_html_now=self._splash_html,
                           window_uid=window_uid, quick_uid=self._quick_uid):
            return True
        self._note_drop(f"bridge message dropped from {source[:80]}: {raw[:120]}")
        return False

    def _note_drop(self, line: str) -> None:
        # A page can post in a loop; keep the log readable and bounded.
        self._drops += 1
        if self._drops <= 20 or self._drops % 1000 == 0:
            self.log(f"{line} (#{self._drops})")

    # -- daemon ----------------------------------------------------------------
    def start_daemon(self) -> dict:
        """The splash's Start button. Locked, and gated on a fresh check."""
        return self._start(origin="splash")

    def start_daemon_from_tray(self) -> dict:
        """The tray's Start item. The tray is the shell's own native UI — no page
        can reach it — so it skips the splash check but not the port-free check."""
        result = self._start(origin="tray")
        if result.get("ok"):
            self.show_window()   # the splash says "starting" and reconnects on its own
        return result

    def _start(self, *, origin: str) -> dict:
        with self._start_lock:  # every page call runs on its own thread
            if self._start_daemon_fn is None:
                return {"ok": False, "reason": "unavailable"}
            if self._starting:
                return {"ok": True, "reason": "already_starting"}
            on_splash = self._on_splash if origin == "splash" else True
            listening = self._listening(self.port)
            if not start_allowed(on_splash=on_splash, listening=listening):
                self.log(f"start_daemon refused ({origin}: on_splash={on_splash}, listening={listening})")
                return {"ok": False, "reason": "not_allowed"}
            self._starting = True
            self._starting_since = time.monotonic()
            self.log(f"start_daemon: user clicked Start ({origin})")
            try:
                ok = bool(self._start_daemon_fn())
            except Exception as e:
                self.log(f"start_daemon failed: {e!r}")
                ok = False
            if not ok:
                self._starting = False
            self._render_splash(listening)
            self._refresh_tray()  # "starting" is on the tray's status line too
            return {"ok": ok}

    def tick(self) -> None:
        """One watcher step: probe, then swap views on an edge."""
        self._check_tray()
        self._check_quick()
        edge = self.watch.observe(is_up(self._probe(self.port)))
        if edge:
            self._refresh_tray()  # the menu's status line and Start item follow the daemon
        if edge == "went_down":
            self.log("daemon not answering — showing the splash")
            self._show_splash()
        elif edge == "came_up":
            self._starting = False
            self.log("daemon is ready")
            self._load(self.last_url or self.start)
        elif self.watch.state == "down":
            if (self._starting
                    and time.monotonic() - self._starting_since > START_TIMEOUT_S):
                # A start that never came up (UAC declined, restart.bat failed):
                # give the button back rather than saying "starting" forever.
                self._starting = False
                self.log("daemon start timed out — offering Start again")
                self._refresh_tray()
            listening = self._listening(self.port)
            if (listening, self._starting) != self._splash_key:
                self._render_splash(listening)  # e.g. booting → gone: button appears

    def _watch_loop(self) -> None:
        while True:
            time.sleep(PROBE_INTERVAL_S)
            try:
                self.tick()
            except Exception as e:
                # One failed window call (pywebview times out a call made while
                # the window is still initialising) must not end the watcher.
                self.log(f"watcher step failed: {e!r}")

    # -- window + tray ---------------------------------------------------------
    def show_window(self) -> None:
        """Bring the window back: from the tray, a second launch, or a Start."""
        w = self.window
        if w is None:
            return
        try:
            w.show()
            w.restore()
        except Exception:
            pass
        _activate(w)

    def open_in_browser(self) -> None:
        threading.Thread(target=_open_quietly, args=(webbrowser.open, self.last_url or self.start),
                         daemon=True).start()

    # -- quick entry -----------------------------------------------------------
    def toggle_quick(self) -> None:
        """What the global hotkey does: show the quick window, or hide it if it
        is already up. Runs on the hotkey's message thread, so it hands the
        window work off rather than blocking the next press behind it."""
        threading.Thread(target=self._toggle_quick, daemon=True).start()

    def _toggle_quick(self) -> None:
        # One press at a time. `show_quick(toggle=True)` decides inside
        # `_quick_lock` but RETURNS from inside it, so the read and the
        # following `hide_quick()` are two separate acquisitions — a
        # check-then-act race MOD_NOREPEAT does not cover (it suppresses
        # auto-repeat, not two real presses). Both hazards are real and neither
        # was closed by the comment that used to claim it: two presses while
        # hidden could both read "hidden" and one hide the window while the
        # other was still activating it, and two presses while VISIBLE could
        # both read "was_visible" and both hide, losing a press.
        with self._toggle_lock:
            if self.show_quick(toggle=True) == "was_visible":
                self.hide_quick()

    def show_quick(self, toggle: bool = False) -> str:
        """Bring the composer up and give the page the keyboard.

        The window is **reset to its opening height first**: it was left as tall
        as the last answer made it, and a composer that opens 700px tall because
        of something the user asked yesterday is a different window each time.

        ``toggle`` makes the already-visible case report ``"was_visible"``
        instead of re-showing, so a press can decide show-or-hide **inside** the
        lock. Deciding outside it is a check-then-act race: MOD_NOREPEAT
        suppresses auto-repeat but not two real presses, so both could read
        "hidden" and both show — or one could hide the window while the other
        was still activating it and telling the page to focus.
        """
        w = self.quick
        if w is None:
            return "no_window"
        # Only the state transition and the window calls are under the lock.
        # `_activate` and `evaluate_js` marshal to (and wait on) the UI thread,
        # and the page's `eos:quick-shown` handler calls straight back into the
        # bridge — so holding the lock across them would let the UI thread block
        # on a lock this thread holds while waiting for that same UI thread.
        with self._quick_lock:
            if toggle and self._quick_visible:
                return "was_visible"
            try:
                if self._quick_w:
                    w.resize(self._quick_w, QUICK_START_HEIGHT)
                w.show()
            except Exception as e:
                self.log(f"quick show failed: {e!r}")
                return "failed"
            self._quick_visible = True
        _activate(w)
        # The page focuses its own input on `eos:quick-shown`; a window that was
        # already loaded gets no second `loaded` event to hang that on.
        try:
            w.evaluate_js("window.dispatchEvent(new Event('eos:quick-shown'))")
        except Exception as e:
            # Logged like its three siblings (`quick show/hide/resize failed`).
            # This is the only path that gives the page its focus signal, so a
            # silent failure looks exactly like a working press — the panel is
            # up, the composer is not focused, and the user's keystrokes go
            # nowhere. Common when the daemon was down at preload and the page
            # is a WebView2 error document.
            self.log(f"quick focus signal failed: {e!r}")
        return "shown"

    def hide_quick(self) -> bool:
        w = self.quick
        if w is None:
            return False
        with self._quick_lock:
            try:
                w.hide()
            except Exception as e:
                # The flag is written only AFTER a successful hide, mirroring
                # show. `Window.hide` can raise (it waits on `shown`, up to
                # 20 s), and recording "hidden" for a window still on screen
                # inverts the toggle: the next press would show an
                # already-visible window and the hotkey could never dismiss it.
                self.log(f"quick hide failed: {e!r}")
                return False
            self._quick_visible = False
        return True

    def open_main(self, path: object = None) -> dict:
        """Hand the conversation to the main window — the quick panel's exit.

        ``path`` goes through :func:`safe_local_path`; a refused one opens the
        main window anyway, on whatever it is already showing. Losing the
        destination is a smaller failure than losing the answer the user just
        asked to keep.
        """
        target = safe_local_path(path) if path is not None else None
        self.hide_quick()
        self.show_window()
        if target and self.watch.state == "up":
            self._load(start_url(self.port, target))
        return {"ok": True, "path": target or ""}

    def set_quick_height(self, px: object = None) -> dict:
        """Grow (or shrink) the panel to fit what the page is showing.

        The ceiling is this screen's, not the constant: on a 720p display a
        full-height panel does not fit below the opening position, and on an
        800x600 one it does not fit at all.
        """
        w = self.quick
        height = clamp_quick_height(px, self._quick_max_h)
        if w is None or not self._quick_w:
            return {"ok": False, "height": height}
        try:
            w.resize(self._quick_w, height)
        except Exception as e:
            self.log(f"quick resize failed: {e!r}")
            return {"ok": False, "height": height}
        return {"ok": True, "height": height}

    def _claim_quick_presence(self) -> None:
        """Make the daemon's ``command-launcher`` stand down — but only once the
        hotkey is really registered, so a shortcut another program already holds
        never leaves the user with no launcher at all."""
        # Under `_presence_lock` with an idempotence guard, matching
        # `_claim_tray_presence` exactly. The watcher can now call this too
        # (a late registration), so claim and release genuinely race; the
        # asymmetry with the tray twin was unexplained even while unreachable.
        with self._presence_lock:
            if self._quick_presence is not None:
                return
            try:
                from emptyos.desktop_presence import quick_presence_name

                self._quick_presence = single_instance.InstanceLock.acquire(
                    quick_presence_name(self.port))
            except Exception as e:
                self.log(f"quick presence not claimed ({e!r}) — the daemon's launcher stays")
                return
            if self._quick_presence is None:
                self.log("quick presence already held elsewhere — the daemon's launcher may stay")

    def _check_quick(self) -> None:
        """Keep the presence claim in step with whether the hotkey is registered.

        The mutex means "a shell holds this shortcut", and `command-launcher`
        stands down on it. Both directions matter, and the release half alone is
        not enough:

        RELEASE — if the message loop ends (`GetMessageW` erroring, the thread
        dying) the claim would outlive the hotkey and the user would get ZERO
        windows per press.

        CLAIM — `HotkeyThread.start()` gives up after a timeout but leaves its
        thread running, so under load `RegisterHotKey` can succeed a moment
        after `_start_quick` has already taken the failure branch. The hotkey is
        then live with no claim behind it, and the user gets TWO windows per
        press — the shell's panel and the launcher's — which is the exact
        outcome `quick_presence_name` exists to prevent. This gate used to
        return early on `_quick_presence is None`, so it could only ever
        release; that made it a half twin of `_check_tray`, which both claims
        and releases.
        """
        hk = self._hotkey
        if hk is None or self._stopping:
            return
        if hk.is_registered():
            if self._quick_presence is None and not self._quick_claim_settled:
                # One attempt, not one per tick: a mutex genuinely held by
                # another shell would otherwise log on every health cycle.
                self._quick_claim_settled = True
                self.log("the global hotkey registered late — claiming the launcher stand-down")
                self._claim_quick_presence()
            return
        if self._quick_presence is not None:
            self.log("the global hotkey is gone — the daemon's launcher takes it back")
            self._release_quick_presence()

    def _release_quick_presence(self) -> None:
        with self._presence_lock:
            lock, self._quick_presence = self._quick_presence, None
        if lock is not None:
            lock.release()

    def _start_quick(self) -> None:
        """Register the hotkey and claim presence. Never fatal: without a hotkey
        the quick window is simply unreachable, and the main window is unaffected."""
        if not self.quick_shortcut or self.quick is None:
            return
        from .hotkey_win import HotkeyThread

        hk = HotkeyThread(self.quick_shortcut, self.toggle_quick, self.log)
        # The handle is kept even on failure. A start() that TIMED OUT may still
        # have a thread that registers a moment later; dropping the handle there
        # leaves a live hotkey `_stop_quick` cannot release.
        self._hotkey = hk
        if not hk.start():
            self.log(f"quick entry off: {hk.error}")
            return
        self._claim_quick_presence()

    def _stop_quick(self) -> None:
        hk, self._hotkey = self._hotkey, None
        if hk is not None:
            hk.stop()
        self._release_quick_presence()

    def quit(self) -> None:
        """End the app. The daemon keeps running — attach mode doesn't own it."""
        self._quitting = True
        self.log("quit from the tray")
        self._destroy_quick()
        if self.window is not None:
            self.window.destroy()

    def _destroy_quick(self) -> None:
        """Really destroy the quick window, so the message loop can end.

        Sets ``_quitting`` first: without it the window's own closing handler
        cancels the close and hides instead, which is right for Alt+F4 and
        exactly wrong here — the window would survive and the process with it.
        """
        w = self.quick
        if w is None:
            return
        self._quitting = True
        try:
            w.destroy()
        except Exception as e:
            self.log(f"quick window would not close: {e!r}")

    def _tray_alive(self) -> bool:
        tray = self.tray  # one read: the watcher may clear it between two
        return tray is not None and tray.alive()

    def _on_closing(self):
        """pywebview's ``closing`` event: return False to cancel the close.

        pywebview cannot see *why* the window is closing. When our FormClosing
        hook is in place it decides instead (``_on_form_closing``), so this
        never cancels — otherwise it would veto sign-out and installers. The
        fallback, without the hook, treats every close as the user's own.
        """
        if self._reason_hooked:
            return None
        return self._decide_close("UserClosing")

    def _on_form_closing(self, sender, args) -> None:
        """WinForms ``FormClosing``, subscribed after pywebview's own handler
        (which only sets Cancel from ``_on_closing`` — None here — and cleans up
        later, in FormClosed). The one place that knows the close reason."""
        try:
            raw = args.CloseReason
            reason = str(raw)
        except Exception as e:
            raw, reason = e, "UserClosing"
        decision = self._decide_close(reason)
        self.log(f"close requested: reason={reason!r} raw={raw!r} → "
                 f"{'hide' if decision is False else 'close'}")
        if decision is False:
            args.Cancel = True

    def _decide_close(self, reason: str):
        action = close_action(tray_alive=self._tray_alive(), quitting=self._quitting, reason=reason)
        if action == "close":
            # pywebview exits its message loop only when the LAST window is
            # destroyed (winforms: `if len(BrowserView.instances) == 0`). The
            # hidden quick window is an instance, so closing the main one alone
            # leaves a pythonw.exe with nothing on screen, still holding the
            # single-instance mutex, the presence mutex and the hotkey — and a
            # relaunch hands off to it and shows a window that no longer exists.
            self._destroy_quick()
            return None
        # Hide off the event thread; the handler must return before the window
        # can process anything else.
        threading.Thread(target=self._hide, daemon=True).start()
        return False

    def _hook_close_reason(self) -> None:
        """Runs on pywebview's ``shown`` event, once ``window.native`` exists."""
        try:
            from System.Windows.Forms import FormClosingEventHandler  # pythonnet

            self.window.native.FormClosing += FormClosingEventHandler(self._on_form_closing)
            self._reason_hooked = True
        except Exception as e:
            self.log(f"close-reason hook unavailable ({e!r}) — every close is treated as the user's")

    def _hide(self) -> None:
        try:
            self.window.hide()
        except Exception:
            return
        with self._notice_lock:
            first = not self._told_hidden
            self._told_hidden = True
        tray = self.tray
        if first and tray is not None:
            tray.notify(HIDDEN_NOTICE, "EmptyOS")

    def _refresh_tray(self) -> None:
        tray = self.tray
        if tray is not None:
            tray.refresh()

    def _claim_tray_presence(self) -> None:
        """Tell the daemon's tray to stand down — only once ours is actually on
        screen, so the user is never left with zero tray icons."""
        with self._presence_lock:
            if self._presence is not None:
                return
            try:
                from emptyos.desktop_presence import tray_presence_name

                self._presence = single_instance.InstanceLock.acquire(tray_presence_name(self.port))
            except Exception as e:
                self.log(f"tray presence not claimed ({e!r}) — the daemon's tray stays visible")
                return
            if self._presence is None:
                self.log("tray presence already held elsewhere — the daemon's tray may stay visible")

    def _release_tray_presence(self) -> None:
        with self._presence_lock:
            lock, self._presence = self._presence, None
        if lock is not None:
            lock.release()

    def _check_tray(self) -> None:
        """Keep the tray, the close behaviour and the daemon's icon in step.

        - The icon came up → claim presence (the daemon's icon stands down).
        - The icon died after being seen, or never appeared within
          ``TRAY_GRACE_S`` → fall back to close-quits, give the daemon its icon
          back, and bring a hidden window forward — a window hidden into a tray
          that no longer exists would otherwise be unreachable.
        """
        tray = self.tray
        if tray is None or self._stopping:
            return
        if tray.alive():
            self._tray_seen_alive = True
            self._claim_tray_presence()
            return
        if not self._tray_seen_alive and time.monotonic() - self._tray_started_at < TRAY_GRACE_S:
            return
        self.log("tray icon is gone — closing the window now quits")
        self.tray = None
        self._release_tray_presence()
        self.show_window()

    # -- second launch ---------------------------------------------------------
    def on_second_launch(self, msg: dict) -> None:
        if self.window is None:
            return
        self.show_window()
        path = safe_local_path(msg.get("path"))
        if path and self.watch.state == "up":
            self._load(start_url(self.port, path))

    # -- run -------------------------------------------------------------------
    def run(self, *, storage_path: Path, icon: str | None, ipc_address: str | None,
            brand_dir: Path | None = None) -> int:
        import webview

        # Before any window exists: the guard patches the handlers each window
        # binds as it is created. Fails closed — an untested pywebview, or a
        # renderer other than WebView2 (whose handlers the guard never sees),
        # raises here, and run_shell reports it instead of opening a window.
        require_supported_webview()
        install_bridge_guard(self)
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        first_up = is_up(self._probe(self.port))
        self.watch.observe(first_up)
        kwargs = dict(js_api=_InertRoot(), width=self.width, height=self.height,
                      min_size=(720, 480))
        if first_up:
            self.window = webview.create_window(self.title, self.start, **kwargs)
        else:
            html = self._build_splash(self._listening(self.port))
            self.log("daemon not ready at launch — opening on the splash")
            self.window = webview.create_window(self.title, html=html, **kwargs)
        self.window.expose(*self.exposed())
        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing
        self.window.events.shown += self._hook_close_reason
        self.log(f"window open → {self.start if first_up else 'splash'}")

        # The quick window is created hidden and PRELOADED — the point of the
        # feature is that a press shows an already-rendered composer instead of
        # paying a page load (the Chrome `--app` launcher it replaces spent ~1 s
        # per press respawning a window). It is created even when the daemon is
        # down: it would then load nothing useful, but the hotkey still has a
        # window to show, and the page reconnects when the daemon returns.
        if self.quick_shortcut:
            try:
                self._create_quick(webview)
            except Exception as e:
                self.quick = None
                self.log(f"quick window unavailable ({e!r}) — the hotkey stays off")

        # The tray is what lets closing the window hide it instead of quitting.
        # A convenience, never a blocker: without pystray, close simply quits.
        try:
            self.tray = start_shell_tray(self, brand_dir)
            self._tray_started_at = time.monotonic()
            deadline = self._tray_started_at + TRAY_READY_WAIT_S
            while not self.tray.alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            self._check_tray()  # claims presence once the icon is on screen
            self.log("tray up — closing the window now hides it")
        except Exception as e:
            self.tray = None
            self.log(f"no tray ({e!r}) — closing the window quits; the daemon's tray stays")

        # Hand-off starts only once the window exists, so a second launch never
        # arrives to find nothing to focus.
        stop_ipc = lambda: None  # noqa: E731
        if ipc_address:
            try:
                stop_ipc = single_instance.serve(ipc_address, self.on_second_launch)
            except Exception as e:
                self.log(f"second-launch hand-off unavailable: {e!r}")

        # Our own daemon thread, not webview.start(func): pywebview runs `func` on
        # a NON-daemon thread, so this never-ending loop would keep the process
        # alive after the window closed.
        threading.Thread(target=self._watch_loop, name="shell-health", daemon=True).start()
        # The hotkey is registered only once the windows exist, so a press can
        # never arrive before there is something to show.
        self._start_quick()
        try:
            webview.start(gui="edgechromium", private_mode=False,
                          storage_path=str(storage_path), icon=icon)
        finally:
            self._stopping = True  # the watcher must not read this as a tray death
            stop_ipc()
            self._stop_quick()
            tray = self.tray
            if tray is not None:
                tray.stop()
            self._release_tray_presence()
        self.log("window closed")
        return 0

    def _create_quick(self, webview) -> None:
        """The frameless, always-on-top composer panel — hidden until the hotkey.

        ``frameless`` with ``easy_drag=False``: a draggable frameless window
        moves when the user swipes to select text in the composer, which is the
        one gesture this window exists for.
        """
        geo = quick_geometry(*_primary_screen())
        self._quick_w = geo["width"]
        self._quick_max_h = geo["max_height"]
        self.quick = webview.create_window(
            "EmptyOS Quick", quick_url(self.port, QUICK_DEFAULT_PATH),
            js_api=_InertRoot(), hidden=True, frameless=True, easy_drag=False,
            on_top=True, resizable=False, shadow=True,
            width=geo["width"], height=geo["height"], x=geo["x"], y=geo["y"],
            # The band this module already defends, not a second declaration of
            # it: `quick_geometry`'s width floor (which QUICK_START_HEIGHT's
            # measurement comment cites) and QUICK_MIN_HEIGHT. The previous
            # (320, 100) was uncited and sat below both — dead while
            # `resizable=False`, but a quieter contradiction of the real floors.
            min_size=(QUICK_MIN_WIDTH, QUICK_MIN_HEIGHT))
        self.quick.expose(*self.exposed("quick"))
        self._quick_uid = getattr(self.quick, "uid", None)
        # Closing the quick window hides it — it is preloaded state, and a
        # destroyed one would leave the hotkey pointing at nothing. (Frameless
        # means there is no X button; this catches Alt+F4.) The `shown` hook is
        # what keeps that from also cancelling a Windows sign-out.
        self.quick.events.closing += self._on_quick_closing
        self.quick.events.shown += self._hook_quick_close_reason
        self.log(f"quick window preloaded → {QUICK_DEFAULT_PATH}")

    def _on_quick_closing(self):
        """The quick window's close: hide it, so the preloaded page survives.

        Guarded by ``_quick_reason_hooked`` for the same reason the main window
        is (:meth:`_on_form_closing`): pywebview's ``closing`` event cannot see
        WHY the window is closing, and WinForms delivers ``FormClosing`` with
        ``WindowsShutDown`` to **every** form during a sign-out or restart.
        Cancelling that vetoes the whole session end — so with the reason hook
        in place this never cancels, and the hook decides instead. Without it,
        the fallback treats every close as the user's own, exactly as the main
        window does.
        """
        if self._quick_reason_hooked:
            return None
        return self._decide_quick_close("UserClosing")

    def _on_quick_form_closing(self, sender, args) -> None:
        try:
            raw = args.CloseReason
            reason = str(raw)
        except Exception as e:
            raw, reason = e, "UserClosing"
        if self._decide_quick_close(reason) is False:
            args.Cancel = True

    def _decide_quick_close(self, reason: str):
        """``False`` to cancel (and hide instead), ``None`` to let it close."""
        if self._quitting or self._stopping or reason != "UserClosing":
            return None
        threading.Thread(target=self.hide_quick, daemon=True).start()
        return False

    def _hook_quick_close_reason(self) -> None:
        try:
            from System.Windows.Forms import FormClosingEventHandler  # pythonnet

            self.quick.native.FormClosing += FormClosingEventHandler(self._on_quick_form_closing)
            self._quick_reason_hooked = True
        except Exception as e:
            self.log(f"quick close-reason hook unavailable ({e!r}) — every close is the user's")


def start_shell_tray(shell: AttachShell, brand_dir: Path | None):
    """The shell's tray icon, its menu regenerated from :func:`shell_core.tray_menu`
    on every refresh so the status line and Start item follow the daemon.

    Menu actions run on their own thread: pystray calls them on its message
    thread, and a Start (a ~2 s port check, then restart.bat) or a Quit (which
    tears the window down) must not stall the tray.
    """
    import pystray

    from .tray import load_icon_image, run_tray

    actions = {
        "open": shell.show_window,
        "browser": shell.open_in_browser,
        "start": shell.start_daemon_from_tray,
        "quit": shell.quit,
    }

    def run_action(fn):
        return lambda icon, item: threading.Thread(target=fn, daemon=True).start()

    def items():
        rows = tray_menu(port=shell.port, daemon_state=shell.watch.state,
                         can_start=shell._start_daemon_fn is not None, starting=shell._starting)
        for row in rows:
            if row == "-":
                yield pystray.Menu.SEPARATOR
                continue
            fn = actions.get(row["id"])
            yield pystray.MenuItem(row["label"], run_action(fn) if fn else (lambda icon, item: None),
                                   enabled=row["enabled"], default=row.get("default", False))

    return run_tray("emptyos-desktop", load_icon_image(brand_dir), "EmptyOS", pystray.Menu(items))


def _prop(obj, name: str):
    """A .NET property via pythonnet: the attribute, or its ``get_`` accessor."""
    try:
        return getattr(obj, name)
    except Exception:
        return getattr(obj, f"get_{name}")()


def require_supported_webview() -> None:
    """Raise unless pywebview is a tested release rendering with WebView2.

    Without WebView2, pywebview silently falls back to MSHTML, which dispatches
    page calls straight to ``js_bridge_call`` — none of the guard's patched
    handlers would ever run.
    """
    from importlib.metadata import version

    v = version("pywebview")
    if v not in TESTED_PYWEBVIEW:
        raise RuntimeError(
            f"pywebview {v} is not a tested release ({', '.join(sorted(TESTED_PYWEBVIEW))}); "
            "the bridge guard depends on its internals. Install a tested version, or "
            "re-check webview/platforms/edgechromium.py and add it to TESTED_PYWEBVIEW.")
    from webview.platforms import winforms

    renderer = getattr(winforms, "renderer", None)
    if renderer != "edgechromium":
        raise RuntimeError(
            f"pywebview chose the {renderer!r} renderer, not WebView2 — install the "
            "Microsoft Edge WebView2 Runtime. Refusing to open without the bridge guard.")


def install_bridge_guard(shell: AttachShell) -> None:
    """Wrap pywebview's WebView2 handlers so every navigation, popup and web
    message is judged by the shell before pywebview acts on it.

    ``EdgeChrome.__init__`` binds ``self.on_navigation_start`` and
    ``self.on_script_notify`` to the WebView2 events, and ``on_webview_ready``
    binds ``self.on_new_window_request`` — so replacing the three on the class
    before a window is created intercepts every one. Anything the guard cannot
    read is refused, never passed through.
    """
    from webview.platforms import edgechromium as ec

    cls = ec.EdgeChrome
    orig_notify = getattr(cls, "_eos_orig_notify", cls.on_script_notify)
    orig_nav = getattr(cls, "_eos_orig_nav", cls.on_navigation_start)

    def on_new_window_request(self_, sender, args):
        # Always handled here: pywebview's own handler would webbrowser.open()
        # whatever URI the page asked for, and WebView2's default is a new window.
        try:
            args.set_Handled(True)
        except Exception:
            try:
                args.Handled = True
            except Exception:
                pass
        try:
            uri = str(args.get_Uri())
            src = _prop(sender, "Source")
            current = str(src) if src is not None else ""
            shell.on_new_window(uri, current)
        except Exception as e:
            shell.log(f"popup guard failed, popup dropped: {e!r}")

    def on_script_notify(self_, sender, args):
        # The DECISION is wrapped, not only the extraction. Everything reachable
        # from here parses page-supplied JSON, and a decision that raises is not
        # a refusal — it is an exception thrown out of a .NET event handler on
        # the WebView2 message pump. (Measured: a 400-digit JSON integer reached
        # `float()` and raised OverflowError.) The guard's own docstring
        # promises input it cannot read is *refused*; this is what makes that true.
        try:
            source = str(_prop(args, "Source"))
            raw = str(args.get_WebMessageAsJson())
            # Which window this renderer belongs to. pywebview sets it in
            # EdgeChrome.__init__ and never from page content, so it identifies
            # the caller even though both windows share the daemon's origin.
            uid = getattr(getattr(self_, "pywebview_window", None), "uid", None)
            allowed = shell.on_message(source, raw, uid)
        except Exception as e:
            shell.log(f"bridge guard failed, message dropped: {e!r}")
            return
        if allowed:
            orig_notify(self_, sender, args)

    def on_navigation_start(self_, sender, args):
        try:
            uri = str(_prop(args, "Uri"))
            src = _prop(sender, "Source")
            current = str(src) if src is not None else ""
            allowed = shell.on_navigation(uri, current)
        except Exception as e:
            shell.log(f"navigation guard failed, navigation cancelled: {e!r}")
            args.Cancel = True
            return
        if not allowed:
            args.Cancel = True
            return
        orig_nav(self_, sender, args)

    cls._eos_orig_notify, cls._eos_orig_nav = orig_notify, orig_nav
    cls.on_script_notify = on_script_notify
    cls.on_navigation_start = on_navigation_start
    cls.on_new_window_request = on_new_window_request


def _primary_screen() -> tuple[int, int]:
    """The primary monitor's usable area in **logical** pixels, or ``(0, 0)``.

    Three things about this are easy to get backwards, so they are stated:

    **Logical, not physical.** pywebview calls ``SetProcessDPIAware()`` inside
    ``webview.start()``, not at import — measured here, the process is still
    ``DPI_UNAWARE`` when this runs (the quick window's geometry is decided
    *before* ``start``). So Windows virtualises these numbers, and pywebview's
    own geometry API takes logical pixels too (``winforms`` multiplies by the
    scale on the way in). The two agree — but only by both being logical. Making
    the process DPI-aware earlier without changing this would inflate every
    number by the scale factor and push a grown panel off the bottom of the
    screen at 150%, defeating both clamps :func:`quick_geometry` exists for.

    **Work area, not the whole screen.** ``SPI_GETWORKAREA`` excludes the
    taskbar wherever it is docked; ``GetSystemMetrics(SM_CXSCREEN)`` does not,
    and a left- or top-docked taskbar would then sit over the panel.

    **Primary monitor.** The panel always opens on the primary display, even
    when the user is working on a secondary one. That is a real limitation, not
    an oversight — the window is frameless with ``easy_drag=False``, so a
    position derived from a monitor that later disappears would be unreachable,
    and the primary display is the one guaranteed to exist and to start at (0,0).
    """
    if sys.platform != "win32":
        return 0, 0
    try:
        import ctypes
        from ctypes import wintypes  # Windows-only; reached only past the guard

        rect = wintypes.RECT()
        SPI_GETWORKAREA = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return int(rect.right - rect.left), int(rect.bottom - rect.top)
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 0, 0


def _open_quietly(opener: Callable[[str], object], target: str) -> None:
    try:
        opener(target)
    except Exception:
        pass


def _activate(window) -> None:
    """Bring the WinForms window to the front, on its own UI thread.

    The foreground right was passed on by the second launch
    (``AllowSetForegroundWindow``); ``Activate`` must still run on the thread
    that owns the form, so it is marshalled with ``Invoke`` where pythonnet is
    available.
    """
    try:
        from System import Action  # pythonnet, present in the desktop venv

        form = window.native
        form.Invoke(Action(form.Activate))
    except Exception:
        try:
            window.native.Activate()
        except Exception:
            pass


def _message_box(text: str, title: str = "EmptyOS") -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
        except Exception:
            pass


def run_shell(*, port: int, repo_root: Path, start_path: str = DEFAULT_START_PATH,
              forward_path: str | None = None, title: str = "EmptyOS",
              width: int = 1440, height: int = 900,
              start_daemon: Callable[[], bool] | None = None,
              start_on_launch: bool = False, bind_host: str | None = None,
              external_schemes: tuple[str, ...] = DEFAULT_EXTERNAL_SCHEMES,
              auth_token: str = "", quick_shortcut: str = "") -> int:
    """Open the attach-mode window, or hand off to the one already open.

    ``start_daemon`` is how the splash's button starts the daemon; the caller
    supplies it (the dev-machine ``restart.bat`` is the launcher script's
    knowledge, not the shell's). ``None`` hides the button.

    ``start_on_launch`` (``--start``) runs it once, only after this process has
    won the single-instance lock and only when nothing holds the port — so a
    second launch can never fire a second ``restart.bat`` over the first one's.
    ``bind_host`` is the daemon's ``[network] host``, so "nothing is listening"
    covers every address it could have bound.

    ``quick_shortcut`` turns on quick entry — a preloaded frameless composer on
    a global hotkey. Empty (the default) means no second window is created and
    no hotkey is registered, so the feature costs nothing when it is off.
    """
    hosts = listen_hosts(bind_host)

    def listening(p: int) -> bool | None:
        return port_listening(p, hosts=hosts)

    sdir = state_dir(repo_root)
    log = _Log(sdir / "shell.log")

    lock = single_instance.InstanceLock.acquire(instance_name(port))
    if lock is None:
        msg: dict = {"cmd": "show"}
        if forward_path:
            msg["path"] = forward_path
        deadline = time.monotonic() + FORWARD_RETRY_S
        while time.monotonic() < deadline:
            if single_instance.send(pipe_address(port), msg):
                log("second launch forwarded to the running shell")
                return 0
            time.sleep(0.4)
        log("another shell holds the lock but is not answering")
        _message_box("EmptyOS is already open but not responding.\n\n"
                     "Close it from Task Manager (pythonw.exe) and try again.")
        return 2

    try:
        try:  # a console-less shell must never let a child open a console window
            from emptyos.headless import install_headless_subprocess_guard

            install_headless_subprocess_guard()
        except Exception as e:
            log(f"headless guard unavailable: {e!r}")
        starting = False
        if start_on_launch and start_daemon is not None and listening(port) is False:
            log("--start: nothing is listening — starting the daemon")
            starting = bool(start_daemon())
        shell = AttachShell(port=port, start_path=start_path, title=title,
                            width=width, height=height, log=log,
                            start_daemon=start_daemon, starting=starting,
                            listening=listening, external_schemes=external_schemes,
                            auth_token=auth_token, quick_shortcut=quick_shortcut)
        brand = repo_root / "brand" / "emptyos"
        icon = brand / "icon.ico"
        return shell.run(storage_path=sdir / "webview",
                         icon=str(icon) if icon.is_file() else None,
                         ipc_address=pipe_address(port), brand_dir=brand)
    except Exception as e:
        log(f"shell crashed: {e!r}")
        _message_box(f"EmptyOS window failed to start:\n{e!r}\n\nSee {sdir / 'shell.log'}")
        return 1
    finally:
        lock.release()
