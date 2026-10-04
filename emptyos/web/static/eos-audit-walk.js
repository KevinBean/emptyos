/* eos-audit-walk.js — shared harness for rendered-DOM audits.
 *
 * Extracted at the second consumer (CLAUDE.md rule 9). eos-readability.js
 * (contrast/font/opacity) and eos-ui-affordance.js (overflow/tab-semantics)
 * had independently grown the same 90-line skeleton: settle animations, walk
 * the DOM, group raw hits into findings, stamp mark attributes, and boot a
 * `?debug=<name>` overlay with an outline + badge + re-audit button.
 *
 * This module owns that skeleton. A concrete audit supplies ONLY its detector
 * and thresholds, then calls:
 *
 *   var A = window.__eosAudit;
 *   A.settleAnimations();                       // before ANY computed-style/geometry read
 *   ... walk, push {el, type, severity, ...} into `raw` ...
 *   var out = A.groupFindings(raw, { keyOf, baseOf, maxFindings, mark, markAttrs });
 *   A.mountOverlay({ debug: 'affordance', audit: audit, ... });
 *
 * Deliberately NOT shared: the detectors, thresholds, severity meanings, and
 * each audit's `window.__eosX` export — those are what make the two audits
 * different, and folding them together would produce a "config-driven" blob.
 *
 * Self-contained, vanilla JS, no dependencies. Safe to load on any page.
 */
(function() {
    'use strict';

    // Tags that never carry rendered content worth auditing.
    var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, OPTION: 1, TITLE: 1, META: 1, LINK: 1, TEMPLATE: 1, HEAD: 1 };

    // Entry animations (fade-in cards, slide-in rows, expanding panels) leave
    // elements at a partial opacity/height for a few hundred ms. Measuring
    // mid-flight reports a composited colour — or a geometry — that never
    // actually persists: one element becomes a family of near-identical phantom
    // findings (timeline's `cardIn` produced 44 of them for readability).
    // Jump every finite animation to its end state so we measure the settled
    // page. Infinite ones (spinners) throw on finish() — decorative, skipped.
    //
    // ANY audit that reads computed style OR geometry must call this first.
    function settleAnimations() {
        if (typeof document.getAnimations !== 'function') return;
        var anims = document.getAnimations();
        for (var i = 0; i < anims.length; i++) {
            try { anims[i].finish(); } catch (e) { /* infinite / unresolved — leave it */ }
        }
    }

    // First direct text-node sample (not descendants' text) — identifies the
    // element in a report without dragging a whole subtree's copy along.
    function sampleText(el, max) {
        max = max || 40;
        for (var i = 0; i < el.childNodes.length; i++) {
            var n = el.childNodes[i];
            if (n.nodeType === 3 && n.textContent.trim()) return n.textContent.trim().slice(0, max);
        }
        return '';
    }

    // Whole-subtree text — for container-shaped findings (a tab row, a clipped
    // panel) whose own text nodes are empty because the content is in children.
    function deepText(el, max) {
        max = max || 48;
        return (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, max);
    }

    // file:line of the source that rendered this element (needs ?debug=locate).
    function srcOf(el) {
        var host = el.closest && el.closest('[data-eos-src]');
        return host ? host.getAttribute('data-eos-src') : '';
    }

    function selPath(el) {
        var part = function(e) {
            var s = e.tagName.toLowerCase();
            if (e.id) s += '#' + e.id;
            else if (e.classList && e.classList.length) s += '.' + Array.prototype.slice.call(e.classList, 0, 2).join('.');
            return s;
        };
        var p = el.parentElement;
        return (p && p !== document.body ? part(p) + ' > ' : '') + part(el);
    }

    function isRendered(el) {
        var r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return false;
        var cs = getComputedStyle(el);
        return cs.visibility !== 'hidden' && cs.display !== 'none';
    }

    // Dedupe: one bad rule = one finding with count=N, not N findings. The two
    // audits group by different signatures, so the KEY is theirs; the grouping,
    // severity-sort, cap and mark-stamping are shared.
    //
    // cfg: { keyOf(f)->str, baseOf(f)->obj, maxFindings, severityRank,
    //        tieBreak(a,b), mark, markable(g)->bool, markAttrs(g)->{attr:val} }
    function groupFindings(raw, cfg) {
        cfg = cfg || {};
        var maxFindings = cfg.maxFindings || 30;
        var rank = cfg.severityRank || { fail: 0, warn: 1, info: 2 };
        var groups = {};
        var order = [];

        for (var i = 0; i < raw.length; i++) {
            var f = raw[i];
            var key = cfg.keyOf(f);
            if (!groups[key]) {
                var g0 = cfg.baseOf(f);
                g0.count = 0;
                g0._els = [];
                groups[key] = g0;
                order.push(key);
            }
            groups[key].count++;
            groups[key]._els.push(f.el);
        }

        order.sort(function(a, b) {
            var ga = groups[a], gb = groups[b];
            var ra = rank[ga.severity], rb = rank[gb.severity];
            if (ra !== rb) return ra - rb;
            return cfg.tieBreak ? cfg.tieBreak(ga, gb) : 0;
        });

        var findings = [];
        for (var k = 0; k < order.length && findings.length < maxFindings; k++) {
            var g = groups[order[k]];
            if (cfg.mark && (!cfg.markable || cfg.markable(g))) {
                var attrs = cfg.markAttrs ? cfg.markAttrs(g) : {};
                for (var e = 0; e < g._els.length; e++) {
                    for (var a in attrs) {
                        if (Object.prototype.hasOwnProperty.call(attrs, a)) g._els[e].setAttribute(a, attrs[a]);
                    }
                }
            }
            delete g._els;
            findings.push(g);
        }
        return { findings: findings, groupCount: order.length };
    }

    function clearMarks(attrs) {
        var marked = document.querySelectorAll('[' + attrs[0] + ']');
        for (var i = 0; i < marked.length; i++) {
            for (var a = 0; a < attrs.length; a++) marked[i].removeAttribute(attrs[a]);
        }
    }

    // ── Overlay mode (?debug=<name>) ───────────────────────────────────
    // cfg: { debug, styleId, badgeId, markAttr, labelAttr, side, tag,
    //        audit(opts)->{findings,stats}, badgeText(stats)->str, row(f)->obj }
    function mountOverlay(cfg) {
        var markAttrs = [cfg.markAttr, cfg.labelAttr];

        function injectStyle() {
            if (document.getElementById(cfg.styleId)) return;
            var s = document.createElement('style');
            s.id = cfg.styleId;
            var m = '[' + cfg.markAttr, b = '#' + cfg.badgeId;
            s.textContent =
                m + '="fail"] { outline: 2px solid #ff3b30 !important; outline-offset: 1px; position: relative; }' +
                m + '="warn"] { outline: 2px dashed #ff9500 !important; outline-offset: 1px; position: relative; }' +
                m + ']::after { content: attr(' + cfg.labelAttr + '); position: absolute; top: -16px; left: 0; background: #ff3b30; color: #fff; font: 600 10px/14px system-ui; padding: 1px 5px; border-radius: 3px; pointer-events: none; z-index: 99999; white-space: nowrap; }' +
                m + '="warn"]::after { background: #ff9500; }' +
                b + ' { position: fixed; bottom: 12px; ' + (cfg.side === 'right' ? 'right' : 'left') + ': 12px; background: #1c1c1e; color: #fff; font: 600 11px/16px system-ui; padding: 4px 10px; border-radius: 999px; z-index: 99998; box-shadow: 0 2px 6px rgba(0,0,0,0.3); display: flex; gap: 8px; align-items: center; }' +
                b + ' button { background: #3a3a3c; color: #fff; border: 0; border-radius: 999px; font: 600 10px/14px system-ui; padding: 2px 8px; cursor: pointer; }';
            document.head.appendChild(s);
        }

        function updateBadge(stats) {
            var el = document.getElementById(cfg.badgeId);
            if (!el) {
                el = document.createElement('div');
                el.id = cfg.badgeId;
                var label = document.createElement('span');
                var btn = document.createElement('button');
                btn.textContent = '↻ re-audit';
                btn.addEventListener('click', run);
                el.appendChild(label);
                el.appendChild(btn);
                document.body.appendChild(el);
            }
            el.firstChild.textContent = cfg.badgeText(stats);
        }

        function run() {
            clearMarks(markAttrs);
            var result = cfg.audit({ mark: true });
            updateBadge(result.stats);
            if (result.findings.length) {
                console.info(cfg.tag + ' findings:');
                console.table(result.findings.map(cfg.row));
            } else {
                console.info(cfg.tag + ' clean — no findings.');
            }
        }

        function start() {
            injectStyle();
            // One pass after the page settles — async data paints after
            // DOMContentLoaded, and auditing too early flags transitional content.
            setTimeout(run, 1500);
        }

        if (location.search.indexOf('debug=' + cfg.debug) >= 0) {
            if (document.readyState === 'complete') start();
            else window.addEventListener('load', start);
        }
    }

    window.__eosAudit = {
        SKIP_TAGS: SKIP_TAGS,
        settleAnimations: settleAnimations,
        sampleText: sampleText,
        deepText: deepText,
        srcOf: srcOf,
        selPath: selPath,
        isRendered: isRendered,
        groupFindings: groupFindings,
        clearMarks: clearMarks,
        mountOverlay: mountOverlay,
        version: 1,
    };
})();
