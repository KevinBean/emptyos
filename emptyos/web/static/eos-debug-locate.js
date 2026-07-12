/* eos-debug-locate.js — read-only pixel->source locator overlay.
 *
 * Injected by the server into an app page when `?debug=locate` is in the URL
 * (see emptyos/web/server.py _inject_locate). Every editable element carries a
 * data-eos-src="<rel_path>:<line>" stamp (from emptyos.sdk.html_anchors, the
 * same parser the designer element-edit loop uses). Hover outlines the nearest
 * stamped element; click copies its `file:line` to the clipboard + toasts +
 * logs a clickable reference.
 *
 * READ-ONLY by design — it locates source, it does NOT edit. The dev/growth
 * loop (screenshot -> describe -> grep -> find line) collapses to a click.
 * Self-contained, vanilla JS, no dependencies. The Cursor "Design Mode" idea,
 * EmptyOS-shaped: visual targeting for the locate step.
 */
(function () {
    'use strict';
    var STYLE_ID = 'eos-debug-locate-style';
    var BADGE_ID = 'eos-debug-locate-badge';
    var HL_ID = 'eos-debug-locate-hl';
    var hl = null;

    function injectStyle() {
        if (document.getElementById(STYLE_ID)) return;
        var s = document.createElement('style');
        s.id = STYLE_ID;
        s.textContent =
            '#' + HL_ID + ' { position: fixed; pointer-events: none; z-index: 2147483646; border: 2px solid #8b5cf6; background: rgba(139,92,246,.10); border-radius: 4px; display: none; transition: all .04s linear; }' +
            '#' + HL_ID + '::after { content: attr(data-src); position: absolute; left: 0; top: -18px; background: #8b5cf6; color: #fff; font: 600 10px/14px ui-monospace, monospace; padding: 1px 5px; border-radius: 3px; white-space: nowrap; }' +
            '#' + BADGE_ID + ' { position: fixed; bottom: 12px; left: 12px; background: #8b5cf6; color: #fff; font: 600 11px/16px system-ui; padding: 4px 10px; border-radius: 999px; z-index: 2147483647; pointer-events: none; box-shadow: 0 2px 6px rgba(0,0,0,0.3); }';
        document.head.appendChild(s);
    }

    function badge(text) {
        var b = document.getElementById(BADGE_ID);
        if (!b) { b = document.createElement('div'); b.id = BADGE_ID; document.body.appendChild(b); }
        b.textContent = text;
    }

    function ensureHl() {
        if (hl) return hl;
        hl = document.createElement('div');
        hl.id = HL_ID;
        document.body.appendChild(hl);
        return hl;
    }

    function nearest(node) {
        while (node && node.nodeType === 1) {
            if (node.hasAttribute && node.hasAttribute('data-eos-src')) return node;
            node = node.parentElement;
        }
        return null;
    }

    function paint(el) {
        var box = ensureHl();
        if (!el) { box.style.display = 'none'; return; }
        var r = el.getBoundingClientRect();
        box.style.left = r.left + 'px';
        box.style.top = r.top + 'px';
        box.style.width = r.width + 'px';
        box.style.height = r.height + 'px';
        box.setAttribute('data-src', el.getAttribute('data-eos-src') || '');
        box.style.display = 'block';
    }

    function copy(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).catch(function () {});
        }
    }

    function start() {
        injectStyle();
        badge('📍 locate mode (?debug=locate) — click an element');

        document.addEventListener('mousemove', function (e) { paint(nearest(e.target)); }, true);
        document.addEventListener('mouseleave', function () { paint(null); }, true);

        document.addEventListener('click', function (e) {
            var el = nearest(e.target);
            if (!el) return;
            e.preventDefault();
            e.stopPropagation();
            var src = el.getAttribute('data-eos-src') || '';
            copy(src);
            if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast('Copied ' + src, 'ok');
            badge('📍 copied: ' + src);
            console.info('[eos-locate] %s', src);
        }, true);

        console.info('[eos-debug-locate] Locator live. Click an element to copy its source location.');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
