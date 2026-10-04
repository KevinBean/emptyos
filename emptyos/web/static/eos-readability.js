/* eos-readability.js — perceptual readability audit (contrast / font-size / opacity).
 *
 * Catches "renders fine but reads badly" issues on the REAL rendered page:
 * text whose effective color is too close to its effective background,
 * illegibly tiny fonts, and opacity-faded text. The rendered-DOM sibling of
 * scripts/check-contrast.py (which only checks static theme-token pairs).
 *
 * Two consumers, one walk (single source of truth):
 *   1. Library mode  — window.__eosReadability.audit(opts) -> {findings, stats}.
 *      scripts/check_readability.py injects this file headlessly and calls it.
 *   2. Overlay mode  — loaded by eos.js boot when `?debug=readability` is in
 *      the URL: outlines offenders (red = fail, amber = warn) with the
 *      contrast ratio as a label, plus a count badge with a re-audit button.
 *
 * Findings are WCAG 2.1 relative-luminance contrast ratios computed against
 * the element's EFFECTIVE colors: ancestor backgroundColor layers are
 * alpha-composited back-to-front, and the text color is composited at its
 * cumulative ancestor opacity. Elements whose background chain hits a
 * background-image/gradient are unverifiable statically — reported as
 * severity "info", never a fail.
 *
 * Opt-out: put `data-readability-ignore` on any element to exempt its
 * whole subtree (inline opt-out beats a central allowlist — audits.md).
 *
 * REQUIRES eos-audit-walk.js (window.__eosAudit) — the shared rendered-audit
 * harness (settle / group+dedupe / mark / overlay), extracted once
 * eos-ui-affordance.js became a second consumer of the same skeleton. Load it
 * FIRST. Beyond that: vanilla JS, no dependencies. Safe to load on any page.
 */
(function() {
    'use strict';

    var A = window.__eosAudit;
    if (!A) { console.error('[eos-readability] eos-audit-walk.js must load first'); return; }

    var STYLE_ID = 'eos-debug-readability-style';
    var BADGE_ID = 'eos-debug-readability-badge';

    // ── Thresholds ─────────────────────────────────────────────────────
    // Confident/ambiguous split per .claude/skills/eos-graduate-audit:
    // "fail" = genuinely unreadable (high confidence, can eventually gate);
    // "warn" = below the weakest theme-token floor. Calibration (2026-07-11,
    // task/journal/hub × 6 themes) showed 3.0–4.5 is where legitimate
    // muted-by-design text lives (check-contrast.py pins --text-muted ≥ 3.0),
    // so warning below 4.5 was ~95% noise. Rendered text below 3.0 is below
    // EVERY token floor → off-token hex or double-dimmed → real signal.
    var NORMAL_FAIL = 2.5, NORMAL_WARN = 3.0;   // normal-size text
    var LARGE_FAIL  = 2.0, LARGE_WARN  = 3.0;   // >=24px, or >=18.66px bold
    var TINY_FONT_PX = 10;                       // visible text below this → warn
                                                 // (10px is the FDL's section-label size — healthy pages use it)
    var FADED_ALPHA  = 0.4;                      // effective text alpha below this → warn
    var MAX_FINDINGS = 30;                       // per page, post-dedupe


    // ── Color parsing ──────────────────────────────────────────────────
    // Computed values are usually rgb()/rgba(); color-mix()/oklch sources can
    // serialize differently, so fall back to canvas fillStyle normalization
    // (the canvas 2d context normalizes any CSS color to sRGB hex/rgba).
    var _RGB_RE = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/;
    var _SRGB_RE = /^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+%?))?\s*\)$/;
    var _HEX_RE = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
    var _canvasCtx = null;

    function _parseAlpha(a) {
        if (a === undefined || a === null || a === '') return 1;
        if (typeof a === 'string' && a.indexOf('%') >= 0) return parseFloat(a) / 100;
        return parseFloat(a);
    }

    function parseColor(str) {
        // -> [r, g, b, a] (0-255, a 0-1) or null when unparseable
        if (!str) return null;
        str = str.trim();
        if (str === 'transparent') return [0, 0, 0, 0];
        var m = _RGB_RE.exec(str);
        if (m) return [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3]), _parseAlpha(m[4])];
        // color-mix() computes to color(srgb r g b / a) in Chromium (0-1 channels)
        m = _SRGB_RE.exec(str);
        if (m) return [parseFloat(m[1]) * 255, parseFloat(m[2]) * 255, parseFloat(m[3]) * 255, _parseAlpha(m[4])];
        var h = _HEX_RE.exec(str);
        if (h) {
            var x = h[1];
            if (x.length === 3) x = x[0] + x[0] + x[1] + x[1] + x[2] + x[2];
            var a = x.length === 8 ? parseInt(x.slice(6, 8), 16) / 255 : 1;
            return [parseInt(x.slice(0, 2), 16), parseInt(x.slice(2, 4), 16), parseInt(x.slice(4, 6), 16), a];
        }
        // Canvas normalization fallback (handles oklch, color(srgb …), named colors)
        try {
            if (!_canvasCtx) _canvasCtx = document.createElement('canvas').getContext('2d');
            _canvasCtx.fillStyle = '#000';
            _canvasCtx.fillStyle = str;
            var norm = _canvasCtx.fillStyle;
            if (norm === str) return null; // didn't normalize → give up
            return parseColor(norm);
        } catch (e) {
            return null;
        }
    }

    function compositeOver(top, base3) {
        // top = [r,g,b,a], base3 = [r,g,b] opaque → [r,g,b]
        var a = top[3];
        return [
            top[0] * a + base3[0] * (1 - a),
            top[1] * a + base3[1] * (1 - a),
            top[2] * a + base3[2] * (1 - a),
        ];
    }

    function luminance(rgb) {
        var f = function(c) {
            c = c / 255;
            return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        };
        return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
    }

    function contrastRatio(rgb1, rgb2) {
        var a = luminance(rgb1), b = luminance(rgb2);
        var hi = Math.max(a, b), lo = Math.min(a, b);
        return (hi + 0.05) / (lo + 0.05);
    }

    function toHex(rgb) {
        var h = function(v) { v = Math.round(Math.max(0, Math.min(255, v))); return (v < 16 ? '0' : '') + v.toString(16); };
        return '#' + h(rgb[0]) + h(rgb[1]) + h(rgb[2]);
    }

    // ── Effective background / opacity ─────────────────────────────────
    function effectiveBackground(el) {
        // Walk ancestors collecting backgroundColor layers (inner-first) until
        // a fully-opaque layer; composite back-to-front. A background-image
        // anywhere before the first opaque layer makes contrast unverifiable.
        var layers = [];
        var node = el;
        while (node && node.nodeType === 1) {
            var cs = getComputedStyle(node);
            var bi = cs.backgroundImage;
            // A background-image blocks verification — EXCEPT on body/html,
            // where it's a page-level decorative texture over a solid --bg
            // (digital-garden's 4%-alpha paper grain blinded the whole theme).
            if (bi && bi !== 'none' && node !== document.body && node !== document.documentElement) {
                return { image: true };
            }
            var rawBg = cs.backgroundColor;
            var c = parseColor(rawBg);
            if (!c && rawBg && rawBg !== 'transparent') {
                // Unparseable paint (exotic color space) — a layer we can't
                // measure must make the result unverifiable, never be dropped.
                return { image: true };
            }
            if (c && c[3] > 0) {
                layers.push(c);
                if (c[3] >= 0.999) break;
            }
            node = node.parentElement; // … → body → html → null
        }
        // Base = white (browser default canvas) when nothing opaque was found.
        var out = [255, 255, 255];
        var last = layers[layers.length - 1];
        if (last && last[3] >= 0.999) {
            out = [last[0], last[1], last[2]];
            layers.pop();
        }
        for (var i = layers.length - 1; i >= 0; i--) out = compositeOver(layers[i], out);
        return { color: out };
    }

    function cumulativeOpacity(el, cache) {
        var product = 1;
        var node = el;
        while (node && node.nodeType === 1) {
            if (cache.has(node)) { product *= cache.get(node); break; }
            var o = parseFloat(getComputedStyle(node).opacity);
            if (!isNaN(o)) product *= o;
            node = node.parentElement;
        }
        cache.set(el, product);
        return product;
    }

    // ── Element metadata ───────────────────────────────────────────────
    // Contrast only means something for glyphs painted in the text color.
    // Emoji (😊 mood buttons) render as color bitmaps regardless of `color`,
    // and symbol-only affordances (✎ ✓ hover-reveal icons) are dim by design
    // — both were pure noise in calibration. Require a letter or digit.
    var _READABLE_RE = /[\p{L}\p{N}]/u;

    function hasDirectText(el) {
        for (var i = 0; i < el.childNodes.length; i++) {
            var n = el.childNodes[i];
            if (n.nodeType === 3 && n.textContent.trim()) return true;
        }
        return false;
    }

    // ── The audit walk ─────────────────────────────────────────────────
    function audit(opts) {
        opts = opts || {};
        var maxFindings = opts.maxFindings || MAX_FINDINGS;
        var mark = !!opts.mark;
        if (opts.settle !== false) A.settleAnimations();
        var opacityCache = new Map();
        var raw = [];          // one entry per offending element
        var scanned = 0, bgImageCount = 0, glyphOnly = 0;

        if (mark) clearMarks();

        var all = document.body ? document.body.querySelectorAll('*') : [];
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (A.SKIP_TAGS[el.tagName]) continue;
            if (el.id === BADGE_ID || (el.closest && el.closest('#' + BADGE_ID))) continue;
            if (!hasDirectText(el)) continue;
            if (el.closest && el.closest('[data-readability-ignore]')) continue;
            // WCAG exempts disabled controls from contrast requirements.
            if (el.closest && el.closest('[disabled],[aria-disabled="true"]')) continue;
            if (el.closest && el.closest('[aria-hidden="true"]')) continue;
            if (!_READABLE_RE.test(A.sampleText(el))) { glyphOnly++; continue; }

            var cs = getComputedStyle(el);
            if (cs.visibility === 'hidden' || cs.visibility === 'collapse') continue;
            var rects = el.getClientRects();
            if (!rects.length) continue;
            var r = el.getBoundingClientRect();
            if (r.width < 3 || r.height < 6) continue;
            // Off-screen a11y text (left:-9999 etc.) — not user-facing.
            if (r.right < 0 || r.bottom < 0) continue;

            scanned++;

            var fontSize = parseFloat(cs.fontSize) || 0;
            var fontWeight = parseInt(cs.fontWeight, 10) || 400;
            // -webkit-text-fill-color overrides color (gradient-text pages set
            // it transparent + background-clip; those surface as bg-image).
            var fgRaw = parseColor(cs.webkitTextFillColor || cs.color) || parseColor(cs.color);
            if (!fgRaw) continue;

            var opacity = cumulativeOpacity(el, opacityCache);
            if (opacity <= 0.05) continue; // effectively invisible — not "hard to read", just hidden

            var effAlpha = fgRaw[3] * opacity;
            var bg = effectiveBackground(el);

            if (bg.image) {
                bgImageCount++;
                raw.push({ el: el, type: 'bg-image', severity: 'info', ratio: null,
                           fg: toHex(fgRaw), bg: 'image', fontSize: fontSize });
                continue;
            }

            var finding = null;

            if (effAlpha < FADED_ALPHA) {
                finding = { type: 'faded', severity: 'warn', ratio: null,
                            fg: toHex(fgRaw), bg: toHex(bg.color), fontSize: fontSize,
                            detail: 'effective opacity ' + effAlpha.toFixed(2) };
            } else {
                var effFg = compositeOver([fgRaw[0], fgRaw[1], fgRaw[2], effAlpha], bg.color);
                var ratio = contrastRatio(effFg, bg.color);
                var isLarge = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
                var failBelow = isLarge ? LARGE_FAIL : NORMAL_FAIL;
                var warnBelow = isLarge ? LARGE_WARN : NORMAL_WARN;
                if (ratio < failBelow) {
                    finding = { type: 'contrast', severity: 'fail', ratio: Math.round(ratio * 100) / 100,
                                fg: toHex(effFg), bg: toHex(bg.color), fontSize: fontSize };
                } else if (ratio < warnBelow) {
                    finding = { type: 'contrast', severity: 'warn', ratio: Math.round(ratio * 100) / 100,
                                fg: toHex(effFg), bg: toHex(bg.color), fontSize: fontSize };
                }
            }

            if (!finding && fontSize > 0 && fontSize < TINY_FONT_PX) {
                finding = { type: 'tiny-font', severity: 'warn', ratio: null,
                            fg: toHex(fgRaw), bg: toHex(bg.color), fontSize: fontSize };
            }

            if (finding) {
                finding.el = el;
                raw.push(finding);
            }
        }

        // Dedupe: one bad CSS rule = 1 finding, not 200. Group by the visual
        // signature + source file so the report stays readable. Grouping/sort/
        // cap/mark are the shared harness; only the KEY is readability-specific.
        var out = A.groupFindings(raw, {
            maxFindings: maxFindings,
            mark: mark,
            keyOf: function(f) {
                var srcFile = (A.srcOf(f.el) || '').split(':')[0];
                return [f.type, f.severity, f.fg, f.bg, Math.round(f.fontSize),
                        srcFile || A.selPath(f.el)].join('|');
            },
            baseOf: function(f) {
                return {
                    type: f.type, severity: f.severity, ratio: f.ratio,
                    fg: f.fg, bg: f.bg, fontSize: f.fontSize,
                    sample: A.sampleText(f.el), src: A.srcOf(f.el), sel: A.selPath(f.el),
                    detail: f.detail || '',
                };
            },
            // worst ratio first within a severity band
            tieBreak: function(ga, gb) { return (ga.ratio || 99) - (gb.ratio || 99); },
            markable: function(g) { return g.severity !== 'info'; },
            markAttrs: function(g) {
                return {
                    'data-eos-readability': g.severity,
                    'data-eos-ratio': g.type === 'contrast' ? (g.ratio + ':1')
                        : g.type === 'tiny-font' ? (g.fontSize + 'px')
                        : g.type,
                };
            },
        });

        var findings = out.findings;
        return {
            findings: findings,
            stats: { scanned: scanned, groups: out.groupCount, bgImage: bgImageCount, glyphOnly: glyphOnly,
                     fails: findings.filter(function(f) { return f.severity === 'fail'; }).length,
                     warns: findings.filter(function(f) { return f.severity === 'warn'; }).length },
        };
    }

    function clearMarks() { A.clearMarks(['data-eos-readability', 'data-eos-ratio']); }

    // ── Overlay mode (?debug=readability) — style/badge/boot from the shared harness
    window.__eosReadability = { audit: audit, clearMarks: clearMarks, version: 1 };

    A.mountOverlay({
        debug: 'readability',
        styleId: STYLE_ID,
        badgeId: BADGE_ID,
        markAttr: 'data-eos-readability',
        labelAttr: 'data-eos-ratio',
        side: 'left',
        tag: '[eos-readability]',
        audit: audit,
        badgeText: function(s) {
            return '🔴 ' + s.fails + ' fail · 🟠 ' + s.warns + ' warn readability (?debug=readability)';
        },
        row: function(f) {
            return { type: f.type, sev: f.severity, ratio: f.ratio, fg: f.fg, bg: f.bg,
                     px: f.fontSize, n: f.count, sample: f.sample, src: f.src || f.sel };
        },
    });
})();
