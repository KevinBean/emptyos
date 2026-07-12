// EOS Conversation — shared helpers for chat-shape pages (rooms, portal).
//
// Both rooms-chat.js and portal index.html grew the same day + session
// segmentation machinery (CLAUDE.md rule 9 — extract on second consumer).
// This module is the consolidated home.
//
// Responsibilities:
//   - Time/text formatters (day labels, gap labels, time labels, previews)
//   - Jump-to-segment modal (renders a picker, fires a callback on choose)
//   - scroll + flash a segment marker (the divider is the scroll target)
//
// What stays caller-side (intentional, NOT shared):
//   - Divider HTML generation. rooms uses `.day-divider` / `.session-divider`,
//     portal uses `.portal-day-divider` / `.portal-session-divider`. The CSS
//     classes differ; each app owns its own rendering.
//   - Per-app segment cache (the variable name + lifecycle differ per app).
//
// Depends on globals: `esc`, `escAttr` (from eos.js), `EOS_UI.modal` +
// `EOS_UI.toast` (from eos-components.js). Load this script AFTER those.

(function() {
    'use strict';

    var SESSION_GAP_MS = 90 * 60 * 1000;

    function formatDay(yyyymmdd) {
        if (!yyyymmdd) return '';
        var today = new Date();
        var todayStr = today.toISOString().slice(0, 10);
        if (yyyymmdd === todayStr) return 'Today';
        var yest = new Date(today.getTime() - 86400000);
        if (yyyymmdd === yest.toISOString().slice(0, 10)) return 'Yesterday';
        var d = new Date(yyyymmdd + 'T00:00:00');
        if (isNaN(d.getTime())) return yyyymmdd;
        var sameYear = today.getFullYear() === d.getFullYear();
        try {
            return d.toLocaleDateString(undefined, sameYear
                ? { weekday: 'short', month: 'short', day: 'numeric' }
                : { month: 'short', day: 'numeric', year: 'numeric' });
        } catch (e) { return yyyymmdd; }
    }

    function formatGap(ms) {
        if (!ms || ms < 0) return '';
        if (ms < 3600000) return Math.max(1, Math.round(ms / 60000)) + ' min';
        if (ms < 86400000) return Math.max(1, Math.round(ms / 3600000)) + ' hr';
        return Math.max(1, Math.round(ms / 86400000)) + ' day' + (ms >= 86400000 * 2 ? 's' : '');
    }

    function segmentTimeLabel(ts) {
        if (!ts) return '';
        try {
            return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) { return ''; }
    }

    function segmentPreview(m) {
        var t = (m && m.text) || '';
        t = t.replace(/\s+/g, ' ').trim();
        return t.length > 70 ? t.slice(0, 70) + '…' : t;
    }

    var _modal = null;

    function openJumpModal(segments, onJump) {
        if (!segments || !segments.length) {
            if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) {
                EOS_UI.toast('Nothing to jump to yet');
            }
            return null;
        }
        var rows = segments.slice().reverse().map(function(s) {
            var icon = s.kind === 'day' ? '&#x1F4C5;' : '&#x23F1;';
            var sub = s.kind === 'day'
                ? (s.day + (s.preview ? (' &middot; ' + esc(s.preview)) : ''))
                : (segmentTimeLabel(s.ts) + (s.preview ? (' &middot; ' + esc(s.preview)) : ''));
            return '<button class="jump-day-item" data-seg-id="' + escAttr(s.id) + '" ' +
                'style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 12px;border-radius:6px;background:none;border:none;color:var(--text);font-family:inherit;font-size:13px;cursor:pointer;text-align:left;width:100%">' +
                '<span>' + icon + ' ' + esc(s.label) + '</span>' +
                '<span style="font-size:11px;color:var(--text-muted);opacity:0.8">' + sub + '</span>' +
                '</button>';
        }).join('');
        var footer = '<div style="padding:10px 12px;font-size:11px;color:var(--text-muted);border-top:1px solid var(--border);margin-top:6px">' +
            'Topic-level segmentation is coming &mdash; today this picker shows day boundaries + ~90 min activity gaps only.' +
            '</div>';
        if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
            _modal = EOS_UI.modal({
                title: 'Jump to',
                body: '<div style="display:flex;flex-direction:column;gap:2px;max-height:60vh;overflow-y:auto">' + rows + '</div>' + footer
            });
            // Wire row clicks via event delegation — the modal's body is
            // fresh DOM each call, so we attach inside this call.
            setTimeout(function() {
                document.querySelectorAll('.jump-day-item[data-seg-id]').forEach(function(btn) {
                    btn.addEventListener('click', function() {
                        var segId = btn.getAttribute('data-seg-id');
                        closeJumpModal();
                        if (onJump) onJump(segId);
                    });
                });
            }, 0);
        }
        return _modal;
    }

    function closeJumpModal() {
        if (_modal && _modal.close) { try { _modal.close(); } catch (e) {} }
        _modal = null;
    }

    // Scroll + brief accent-flash on a divider element. `containerSelector`
    // is optional — when omitted, searches the whole document.
    function scrollToSegment(segId, container) {
        var root = container || document;
        var el = root.querySelector('[data-segment-id="' + segId + '"]');
        if (!el) return;
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        var prev = el.style.boxShadow;
        el.style.transition = 'box-shadow 0.6s ease';
        el.style.boxShadow = '0 0 0 3px color-mix(in srgb,var(--accent) 50%,transparent)';
        setTimeout(function() { el.style.boxShadow = prev; }, 1200);
    }

    window.EOS_CONVERSATION = {
        SESSION_GAP_MS: SESSION_GAP_MS,
        formatDay: formatDay,
        formatGap: formatGap,
        segmentTimeLabel: segmentTimeLabel,
        segmentPreview: segmentPreview,
        openJumpModal: openJumpModal,
        closeJumpModal: closeJumpModal,
        scrollToSegment: scrollToSegment
    };
})();
