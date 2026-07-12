/* eos-ui-affordance.js — rendered-page interaction-affordance audit.
 *
 * The perceptual-audit gap that hid the CAD workspace issues (a list clipped
 * with no way to reach its content; a row of buttons switching panels without
 * tab semantics) had ZERO automated coverage — every existing rendered check
 * targets a different axis (readability=contrast, clickable=occlusion,
 * ui_structure=EOS_UI adoption). This is the deterministic half of that gap.
 * The perceptual/judgment half stays in the eos-ui-walk skill.
 *
 * Two detectors, both on the REAL rendered DOM:
 *   1. overflow-clip — content taller than its box, clipped by overflow:hidden/
 *      clip with no scroll affordance (unreachable). `fail` only for the
 *      high-confidence "a declared scroller inside was clamped off" signature
 *      (the exact broken height-chain bug); otherwise `warn` (a collapsed
 *      accordion legitimately clips, so bare clipping is advisory).
 *   2. buttons-as-tabs — a horizontal row of >=2 buttons with exactly one marked
 *      active/selected (mutually-exclusive = tab-like) that lacks role="tablist"
 *      / role="tab". Semantic gap → always `warn` (never auto-gates; a11y-role
 *      advice, per audits.md "ambiguous signal must never gate").
 *
 * Two consumers, one walk (single source of truth):
 *   1. Library mode  — window.__eosAffordance.audit(opts) -> {findings, stats}.
 *      scripts/check_ui_affordance.py injects this file headlessly.
 *   2. Overlay mode  — eos.js boot loads it on `?debug=affordance`: outlines
 *      offenders (red=fail, amber=warn) with a count badge + re-audit button.
 *
 * REQUIRES eos-audit-walk.js (window.__eosAudit) — the shared rendered-audit
 * harness (settle/group/mark/overlay). Load it FIRST.
 *
 * Opt-out: `data-affordance-ignore` on any element exempts its whole subtree
 * (inline opt-out beats a central allowlist — audits.md).
 *
 * Self-contained beyond that, vanilla JS. Safe to load on any page.
 */
(function() {
    'use strict';

    var A = window.__eosAudit;
    if (!A) { console.error('[eos-affordance] eos-audit-walk.js must load first'); return; }

    var MARK_ATTR = 'data-eos-affordance';
    var TYPE_ATTR = 'data-eos-aff-type';

    // ── Thresholds ─────────────────────────────────────────────────────
    // Calibrated 2026-07-11 against 21 known-healthy apps (hub/task/journal +
    // 18 more): 0 `fail` anywhere — the broken-height-chain signal is FP-clean,
    // so it is the one that gates. `buttons-as-tabs` hit 3/21 (~14%, under the
    // 30% noise bar of audits.md) and every hit was genuine (dogfood-agent's
    // container is literally class="df-tabs"), so it stays as real advisory
    // signal rather than being tuned away.
    var OVERFLOW_SLOP = 8;      // px of scrollHeight-clientHeight before it counts (rounding)
    var MIN_CLIP_H = 40;        // clip regions shorter than this are 1-line ellipsis clips, not panels
    var ROW_TOP_SLOP = 6;       // px: children within this offsetTop delta count as one horizontal row
    var MAX_FINDINGS = 40;      // per page, post-dedupe

    var ACTIVE_RE = /(^|[\s_-])(active|selected|current|is-active)([\s_-]|$)/;

    function isActive(el) {
        return ACTIVE_RE.test(el.className || '') || el.getAttribute('aria-selected') === 'true';
    }

    // A descendant that DECLARED scrolling (overflow:auto/scroll) but is being
    // clipped off the bottom of `el` — the "height chain broke, the scroller
    // never got a bounded height so it grew to content and the ancestor clips
    // it off" signature. High confidence this is a bug, not intentional truncation.
    function hasClampedScroller(el, elBottom) {
        var kids = el.querySelectorAll('*');
        for (var i = 0; i < kids.length; i++) {
            var k = kids[i];
            var oy = getComputedStyle(k).overflowY;
            if (oy !== 'auto' && oy !== 'scroll') continue;
            if (k.getBoundingClientRect().bottom > elBottom + OVERFLOW_SLOP) return true;
        }
        return false;
    }

    // ── The walk ────────────────────────────────────────────────────────
    function audit(opts) {
        opts = opts || {};
        // Mid-flight entry animations distort the very geometry this audit reads
        // (heights, offsets), so settle them first — same rule readability learned.
        A.settleAnimations();

        var raw = [];
        var scanned = 0;
        var all = document.querySelectorAll('body *');

        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (A.SKIP_TAGS[el.tagName]) continue;
            if (el.closest('[data-affordance-ignore]')) continue;
            if (!A.isRendered(el)) continue;
            scanned++;

            // Detector 1: overflow-clip
            var cs = getComputedStyle(el);
            var oy = cs.overflowY;
            if ((oy === 'hidden' || oy === 'clip') && el.clientHeight >= MIN_CLIP_H) {
                var over = el.scrollHeight - el.clientHeight;
                var nowrapEllipsis = cs.whiteSpace === 'nowrap' && cs.textOverflow === 'ellipsis';
                if (over > OVERFLOW_SLOP && !nowrapEllipsis) {
                    var clamped = hasClampedScroller(el, el.getBoundingClientRect().bottom);
                    raw.push({
                        el: el, type: 'overflow-clip',
                        severity: clamped ? 'fail' : 'warn',
                        detail: over + 'px clipped by overflow:' + oy +
                                (clamped ? ' (a scroll region inside is clamped off — broken height chain)' : ''),
                    });
                }
            }

            // Detector 2: buttons-as-tabs (missing role="tablist"/"tab")
            var kids = [];
            for (var c = 0; c < el.children.length; c++) {
                var ch = el.children[c];
                if (ch.tagName === 'BUTTON' || ch.getAttribute('role') === 'button') kids.push(ch);
            }
            if (kids.length >= 2) {
                var top0 = kids[0].offsetTop;
                var horizontal = kids.every(function(k) { return Math.abs(k.offsetTop - top0) <= ROW_TOP_SLOP; });
                var selected = kids.filter(isActive);
                // aria-pressed => toolbar-toggle semantics (independent toggles),
                // NOT a one-of-N tab set. Exclude to avoid flagging real toolbars.
                var anyPressed = kids.some(function(k) { return k.hasAttribute('aria-pressed'); });
                var role = el.getAttribute('role') || '';
                var kidRoled = kids.some(function(k) {
                    var r = k.getAttribute('role'); return r === 'tab' || r === 'radio';
                });
                var properlyRoled = role === 'tablist' || role === 'radiogroup' || kidRoled;
                if (horizontal && selected.length === 1 && !anyPressed && !properlyRoled) {
                    raw.push({
                        el: el, type: 'buttons-as-tabs', severity: 'warn',
                        detail: kids.length + ' buttons, exactly one active (one-of-N) but no roles — ' +
                                'add role="tablist"/"tab" if they switch panels, or ' +
                                'role="radiogroup"/"radio" if they pick a value',
                    });
                }
            }
        }

        var out = A.groupFindings(raw, {
            maxFindings: opts.maxFindings || MAX_FINDINGS,
            mark: !!opts.mark,
            severityRank: { fail: 0, warn: 1 },
            keyOf: function(f) { return [f.type, f.severity, A.selPath(f.el)].join('|'); },
            baseOf: function(f) {
                return {
                    type: f.type, severity: f.severity, detail: f.detail,
                    sample: A.deepText(f.el), sel: A.selPath(f.el), src: A.srcOf(f.el),
                };
            },
            markAttrs: function(g) {
                var a = {};
                a[MARK_ATTR] = g.severity;
                a[TYPE_ATTR] = g.type;
                return a;
            },
        });

        return {
            findings: out.findings,
            stats: {
                scanned: scanned, groups: out.groupCount,
                fails: out.findings.filter(function(f) { return f.severity === 'fail'; }).length,
                warns: out.findings.filter(function(f) { return f.severity === 'warn'; }).length,
            },
        };
    }

    function clearMarks() { A.clearMarks([MARK_ATTR, TYPE_ATTR]); }

    window.__eosAffordance = { audit: audit, clearMarks: clearMarks, version: 1 };

    A.mountOverlay({
        debug: 'affordance',
        styleId: 'eos-debug-affordance-style',
        badgeId: 'eos-debug-affordance-badge',
        markAttr: MARK_ATTR,
        labelAttr: TYPE_ATTR,
        side: 'right',
        tag: '[eos-affordance]',
        audit: audit,
        badgeText: function(s) {
            return '🔴 ' + s.fails + ' fail · 🟠 ' + s.warns + ' warn affordance (?debug=affordance)';
        },
        row: function(f) {
            return { type: f.type, sev: f.severity, n: f.count, sample: f.sample,
                     sel: f.sel, detail: f.detail };
        },
    });
})();
