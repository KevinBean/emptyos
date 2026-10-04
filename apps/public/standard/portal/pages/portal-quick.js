// portal-quick.js — quick entry (A4 + B6): Portal stripped to its composer,
// for a small always-on-top window opened by a global hotkey.
//
// Two hosts reach this mode and it must work in both:
//
//   * The DESKTOP SHELL's quick window (products/_shared/shell.py) — frameless,
//     preloaded once and shown on the hotkey, so this page is NOT reloaded per
//     press. That is the whole point of the feature (the Chrome --app launcher
//     it replaces respawned a window, ~1 s per press), and it is why focus is
//     driven by the `eos:quick-shown` event rather than by page load.
//   * plugins/command-launcher's borderless Chrome window, which still spawns
//     per press and has no shell bridge. Everything here degrades to that:
//     Esc closes the window instead of hiding it, and the panel does not resize.
//
// `?launcher=1` is an ALIAS of `?quick=1`, not a second mode — command-launcher
// has shipped that spelling since before the shell existed and users have it in
// their config. Which spellings count, and how to reach either host, is
// EOS.quickHost's (eos.js): eos-keys.js needs the same answers, so it lives in
// the shared bundle rather than twice here.
//
// One namespaced global — PortalQuick — per .claude/rules/multi-module-apps.md
// § frontend counterpart. Loads before portal.js, whose boot calls
// PortalQuick.init(). The pure helpers are exported for tests/js/portal_quick.test.mjs.
var PortalQuick = (function () {
    'use strict';

    // Kept in step with shell_core's QUICK_MIN/MAX/START_HEIGHT. Both sides
    // clamp on purpose: the page asks for a height it measured, and the shell
    // refuses to be told anything outside the band by any page at all.
    // tests/js/portal_quick.test.mjs and tests/test_unit_desktop_shell.py pin
    // the same three numbers so a change on one side fails on the other.
    var MIN_HEIGHT = 120;
    var MAX_HEIGHT = 720;
    var START_HEIGHT = 250;

    // A rounding buffer, NOT padding: `body` is the measured element and its
    // own padding is already inside its scrollHeight. Enough to absorb
    // sub-pixel layout and a border, so the content never sits one pixel past
    // the edge of a window whose overflow is hidden.
    var SIZE_BUFFER = 8;

    // Below this, a resize is not worth a bridge call and a window move. It is
    // also what stops the resize loop: the input's own max-height is a fraction
    // of the window, so a resize changes the content that asked for it, and the
    // series only settles because small steps are ignored.
    var RESIZE_STEP = 8;

    // Overlays that own Esc while they are open. Dismissing the window from
    // under one discards the thing the user opened instead of closing it, and
    // the user never gets back to the composer they were returning to.
    var ESC_OWNERS = '#eos-modal-overlay, #eos-palette-overlay.show, ' +
                     '#eos-search-overlay.open, .app-drawer-overlay.open';

    // Chrome that has no place in a one-line composer is hidden by the
    // `.portal-quick-mode` rules in index.html, NOT from here. This module used
    // to walk a selector list setting inline styles, with a MutationObserver and
    // a 3s window because eos.js injects its nav through an async <script> — all
    // of which a stylesheet does for free and cannot lose the race on.

    var S = { on: false, observer: null, lastHeight: 0 };

    // ── Pure helpers (node-tested) ─────────────────────────────────────

    // The fallback for when eos.js is absent; EOS.quickHost owns this question
    // otherwise (see `quickOn`). Takes the raw search string so it is testable
    // without a document.
    function isQuickSearch(search) {
        // Deliberately no try/catch: URLSearchParams exists in every browser
        // that can load this page, and a catch here could only ever turn a
        // missing global into a silent "not quick mode" — which is the full
        // portal opening in a frameless window with no way to close it.
        var p = new URLSearchParams(String(search || ''));
        return p.get('quick') === '1' || p.get('launcher') === '1';
    }

    // The height to ask the shell for, given what the content measures.
    // Clamped, never refused: a measurement outside the band is a long answer
    // or an empty one, not an error worth dropping the resize over.
    function heightFor(contentHeight) {
        // `== null` catches null AND undefined, and it has to come first:
        // `Number(null)` is 0, which would read a MISSING measurement as a
        // measured zero and clamp it to the floor. The two are different
        // states — one is "nothing was laid out", the other is "there is
        // nothing to show" — and only the second should shrink the window.
        if (contentHeight == null) return START_HEIGHT;
        var n = Number(contentHeight);
        // START, not MIN, for the same reason: an unmeasurable height means the
        // DOM query failed, and answering that with the floor collapses the
        // window to a sliver. The shell's clamp_quick_height agrees.
        if (!isFinite(n)) return START_HEIGHT;
        return Math.round(Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, n)));
    }

    // Where "open this in the main window" should land. The thread lives in the
    // hash, so it has to travel; the query does not — carrying `quick=1` over
    // would open the MAIN window stripped to a composer, which is the one
    // outcome this button exists to avoid.
    //
    // Refuses the same two shapes shell_core.safe_local_path does, in the same
    // order — `//host` and `/\host` both reach another origin in a browser.
    // Chromium normalises the backslash out of `location.pathname`, so today
    // only the first is reachable; carrying half of a documented pair is how
    // the other half stops being true later.
    function mainPath(pathname, hash) {
        var path = String(pathname || '/portal/');
        if (path.charAt(0) !== '/' || path.indexOf('//') === 0 ||
            path.indexOf('/\\') === 0 || /[\x00-\x1f\x7f]/.test(path)) {
            path = '/portal/';
        }
        var h = String(hash || '');
        return h && h.charAt(0) === '#' ? path + h : path;
    }

    function worthResizing(next, last) {
        return Math.abs(Number(next) - Number(last || 0)) >= RESIZE_STEP;
    }

    // ── The host (EOS.quickHost, eos.js) ───────────────────────────────

    function host() {
        return (window.EOS && EOS.quickHost) || null;
    }

    // Whether this page is in a quick window. Asked of the host so a third
    // spelling added there reaches BOTH consumers — eos-keys.js would otherwise
    // treat the window as a launcher while this module left the full Portal
    // rendering in a frameless, always-on-top box with no title bar.
    function quickOn() {
        var h = host();
        return h ? h.active() : isQuickSearch(window.location.search);
    }

    // The shell's own API, for the one verb only this page uses. NEVER cached:
    // pywebview 6.2.1 injects `window.pywebview` from `on_navigation_completed`
    // — after the document has loaded — so a snapshot taken when this module
    // runs (bottom of <body>) is null forever, and with it every resize.
    function shellBridge() {
        var h = host();
        return h ? h.bridge() : null;
    }

    function dismiss() {
        var h = host();
        if (h) return h.dismiss();
        try { window.close(); } catch (_) {}   // eos.js absent: still dismissable
    }

    function openInMain() {
        var path = mainPath(window.location.pathname, window.location.hash);
        var h = host();
        if (h) return h.openMain(path);
        try { window.open(path, '_blank', 'noopener'); } catch (_) {}
        try { window.close(); } catch (_) {}
    }

    // ── Sizing ─────────────────────────────────────────────────────────

    // What the window is sized to. `body`, not `.portal-main`: `body` carries
    // theme.css's unconditional `padding-top: calc(46px + safe-area)` — space
    // for the fixed nav, which quick mode hides but cannot remove (the rule is
    // `!important`). That padding is painted, so the window really is 46px
    // taller than `.portal-main`, and measuring the inner element clipped the
    // bottom of the composer — where the Send button is.
    function measured() {
        return document.body;
    }

    function syncHeight() {
        var api = shellBridge();
        if (!api || typeof api.set_quick_height !== 'function') return;
        var raw = measured().scrollHeight;
        // Zero is not a measurement — it is "nothing is laid out right now"
        // (mid-swap between panes, or a hidden pane). Keep the size we have.
        if (!(raw > 0)) return;
        var next = heightFor(raw + SIZE_BUFFER);
        if (!worthResizing(next, S.lastHeight)) return;
        S.lastHeight = next;
        try { api.set_quick_height(next); } catch (_) {}
    }

    // ── Mode ───────────────────────────────────────────────────────────

    // The composer the user can actually type into. Portal has two — the hero's
    // `#hero-input` and the thread's `#chat-input` — and after the first send
    // the hero is display:none, where `.focus()` is a silent no-op. Focusing the
    // wrong one is the feature's worst failure: the window appears, the user
    // types, and nothing receives the keystrokes.
    function visibleInput() {
        var inputs = document.querySelectorAll('#hero-input, #chat-input');
        for (var i = 0; i < inputs.length; i++) {
            if (inputs[i].offsetParent !== null) return inputs[i];
        }
        return null;
    }

    function focusInput() {
        var el = visibleInput();
        if (el) { try { el.focus(); } catch (_) {} }
    }

    function onKeydown(e) {
        if (e.key === 'Escape') {
            // An overlay owns Esc while it is open.
            if (document.querySelector(ESC_OWNERS)) return;
            e.preventDefault();
            dismiss();
            return;
        }
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            openInMain();
        }
    }

    // The shell shows an already-loaded window, so there is no second `load`
    // event to hang focus on — it dispatches this instead.
    function onShown() {
        S.lastHeight = 0;          // the shell reset the window; re-measure
        focusInput();
        syncHeight();
    }

    function init() {
        if (S.on || !quickOn()) return false;
        S.on = true;
        // Both elements: one CSS rule then reaches <html> and <body>, which is
        // what undoes the full-height chain the hero normally sits in.
        document.documentElement.classList.add('portal-quick-mode');
        document.body.classList.add('portal-quick-mode');

        document.addEventListener('keydown', onKeydown);
        // Registered unconditionally, NOT behind a bridge probe: the bridge does
        // not exist yet at this point (see shellBridge), and syncHeight already
        // no-ops without one. Blur-dismiss is deliberately absent — eos-keys.js
        // registers it for every quick window, and a second copy here would fire
        // twice for one blur.
        window.addEventListener('eos:quick-shown', onShown);
        if (window.ResizeObserver) {
            // The reference is kept, not discarded: an observer nothing holds
            // can be collected, and the panel would then stop following its
            // content with no error anywhere.
            S.observer = new ResizeObserver(syncHeight);
            S.observer.observe(measured());
        }
        setTimeout(focusInput, 100);
        setTimeout(syncHeight, 200);
        return true;
    }

    return {
        init: init,
        isOn: function () { return S.on; },
        dismiss: dismiss,
        openInMain: openInMain,
        syncHeight: syncHeight,
        focusInput: focusInput,
        // pure — exported for tests/js/portal_quick.test.mjs
        isQuickSearch: isQuickSearch,
        heightFor: heightFor,
        mainPath: mainPath,
        worthResizing: worthResizing,
        MIN_HEIGHT: MIN_HEIGHT,
        MAX_HEIGHT: MAX_HEIGHT,
        START_HEIGHT: START_HEIGHT
    };
})();
