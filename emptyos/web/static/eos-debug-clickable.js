/* eos-debug-clickable.js — dev overlay that outlines intercepted CTAs.
 *
 * Loaded by eos.js boot when `?debug=clickable` is in the URL.
 * For every visible button-shaped element, runs `elementFromPoint(center)`
 * and outlines the button in red if the top element at that point is
 * NOT the button or one of its descendants/ancestors. Catches z-order
 * collisions (e.g. FAB dock covering in-app buttons) at design time —
 * the same heuristic that scripts/ui_walk_round2.py click pass uses.
 *
 * Self-contained, vanilla JS, no dependencies. Safe to load on any page.
 */
(function() {
    'use strict';

    var STYLE_ID = 'eos-debug-clickable-style';
    var BADGE_ID = 'eos-debug-clickable-badge';
    var REFRESH_INTERVAL_MS = 800;

    function injectStyle() {
        if (document.getElementById(STYLE_ID)) return;
        var s = document.createElement('style');
        s.id = STYLE_ID;
        s.textContent =
            '[data-eos-intercepted="1"] { outline: 2px solid #ff3b30 !important; outline-offset: 1px; position: relative; }' +
            '[data-eos-intercepted="1"]::after { content: "intercepted"; position: absolute; top: -18px; right: 0; background: #ff3b30; color: #fff; font: 600 10px/14px system-ui; padding: 1px 5px; border-radius: 3px; pointer-events: none; z-index: 99999; }' +
            '#' + BADGE_ID + ' { position: fixed; bottom: 12px; left: 12px; background: #ff3b30; color: #fff; font: 600 11px/16px system-ui; padding: 4px 10px; border-radius: 999px; z-index: 99998; pointer-events: none; box-shadow: 0 2px 6px rgba(0,0,0,0.3); }';
        document.head.appendChild(s);
    }

    function injectBadge(count) {
        var b = document.getElementById(BADGE_ID);
        if (!b) {
            b = document.createElement('div');
            b.id = BADGE_ID;
            document.body.appendChild(b);
        }
        b.textContent = '🔴 ' + count + ' intercepted CTA' + (count === 1 ? '' : 's') + ' (?debug=clickable)';
    }

    function candidates() {
        // Cast a wide net for clickable things; the intercept check filters noise.
        var sel = 'button, [role="button"], .btn, .eos-btn, a.btn, a[onclick], a[href]:not([href^="#"]):not([href=""])';
        return Array.from(document.querySelectorAll(sel)).filter(function(el) {
            if (el.offsetParent === null) return false; // not visible
            if (el.disabled) return false;
            // Skip our own badge
            if (el.id === BADGE_ID) return false;
            // Skip dock-internal pills — they're allowed to be hidden behind dock state
            if (el.closest('#eos-fab-dock')) return false;
            var r = el.getBoundingClientRect();
            if (r.width < 6 || r.height < 6) return false;
            return true;
        });
    }

    function audit() {
        var count = 0;
        candidates().forEach(function(el) {
            var r = el.getBoundingClientRect();
            var cx = r.x + r.width / 2;
            var cy = r.y + r.height / 2;
            if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) {
                // off-screen — clear marker if previously set
                el.removeAttribute('data-eos-intercepted');
                return;
            }
            var top = document.elementFromPoint(cx, cy);
            var intercepted = top && top !== el && !el.contains(top) && !top.contains(el);
            if (intercepted) {
                el.setAttribute('data-eos-intercepted', '1');
                count++;
            } else {
                el.removeAttribute('data-eos-intercepted');
            }
        });
        injectBadge(count);
    }

    function start() {
        injectStyle();
        injectBadge(0);
        audit();
        // Re-audit periodically — layout shifts (modals opening, dock expanding)
        // can introduce or resolve intercepts.
        setInterval(audit, REFRESH_INTERVAL_MS);
        // Also re-audit on hover events that show/hide the FAB dial
        document.addEventListener('mouseover', function(e) {
            if (e.target.closest && e.target.closest('#eos-fab-dock')) {
                setTimeout(audit, 320); // after dock transition
            }
        }, { passive: true });
        console.info('[eos-debug-clickable] Click-intercept audit live. Re-running every', REFRESH_INTERVAL_MS, 'ms.');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
