/* EmptyOS Shared UI Components — JS
   Include via: <script src="/static/eos-components.js"></script>
   Requires: eos.js loaded first
*/

var EOS_UI = {
    // Toast notification — hover/touch pauses dismiss; click dismisses.
    // Errors stay 2x longer than success since users actually want to read them.
    // Last 30 toasts kept in EOS_UI._toastLog and reopenable via the 🔔 bell
    // (top-right) or EOS_UI.toastHistory().
    _toastLog: (function() {
        try {
            var raw = sessionStorage.getItem('eos.toastLog');
            var arr = raw ? JSON.parse(raw) : [];
            return Array.isArray(arr) ? arr : [];
        } catch (e) { return []; }
    })(),
    _toastMax: 30,
    _toastPersist: function() {
        try { sessionStorage.setItem('eos.toastLog', JSON.stringify(EOS_UI._toastLog)); } catch (e) {}
    },
    // toast(msg, ok?, opts?) — opts.action = {label, onClick} renders an inline
    // action button (e.g. "Undo"); opts.timeout overrides the auto-dismiss ms.
    // The action is transient (never persisted to the notification log).
    toast: function(msg, ok, opts) {
        var isErr = ok === false;
        EOS_UI._toastLog.unshift({ts: Date.now(), msg: String(msg == null ? '' : msg), ok: !isErr});
        if (EOS_UI._toastLog.length > EOS_UI._toastMax) EOS_UI._toastLog.length = EOS_UI._toastMax;
        EOS_UI._toastPersist();
        EOS_UI._updateToastBell();
        EOS_UI._toastShow(msg, ok, opts);
    },
    // Copy text to the clipboard. Never throws (clipboard is unavailable in
    // insecure contexts / some embeds). Pass {toast:true} for a "Copied" toast
    // or {toast:'Copied URL'} for custom text. Returns the clipboard promise.
    copy: function(text, opts) {
        opts = opts || {};
        var done = function() {
            if (opts.toast) EOS_UI.toast(opts.toast === true ? 'Copied' : opts.toast);
        };
        try {
            var p = navigator.clipboard && navigator.clipboard.writeText(String(text == null ? '' : text));
            if (p && p.then) { p.then(done, function() {}); return p; }
            done();
        } catch (e) { /* clipboard unavailable — non-fatal */ }
        return Promise.resolve();
    },
    _toastShow: function(msg, ok, opts) {
        opts = opts || {};
        var el = document.getElementById('eos-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'eos-toast';
            el.className = 'eos-toast eos-toast-ok';
            el.title = 'Hover to keep open · click to dismiss';
            el.addEventListener('mouseenter', function() { clearTimeout(el._timer); });
            el.addEventListener('mouseleave', function() {
                el._timer = setTimeout(function() { el.classList.remove('show'); }, 3000);
            });
            el.addEventListener('click', function() {
                clearTimeout(el._timer);
                el.classList.remove('show');
            });
            document.body.appendChild(el);
        }
        // Structured content: message span + optional action button. The message
        // is always set via textContent (never innerHTML) so it can't inject markup.
        el.textContent = '';
        var span = document.createElement('span');
        span.className = 'eos-toast-msg';
        span.textContent = msg == null ? '' : String(msg);
        el.appendChild(span);
        var act = opts.action;
        if (act && act.label && typeof act.onClick === 'function') {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'eos-toast-action';
            btn.textContent = act.label;
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                clearTimeout(el._timer);
                el.classList.remove('show');
                act.onClick();
            });
            el.appendChild(btn);
        }
        var isErr = ok === false;
        el.className = 'eos-toast ' + (isErr ? 'eos-toast-err' : 'eos-toast-ok') + ' show';
        clearTimeout(el._timer);
        el._timer = setTimeout(function() { el.classList.remove('show'); }, opts.timeout || (isErr ? 14000 : 7000));
    },
    _updateToastBell: function() {
        // Embedded pages (?embed=1 — iframed by a host shell like portal) skip
        // the bell: the host page already shows one. Mirrors eos.js's nav guard.
        try {
            if (new URLSearchParams(location.search).get('embed') === '1') return;
        } catch (e) {}
        var bell = document.getElementById('eos-toast-bell');
        if (!bell) {
            bell = document.createElement('button');
            bell.id = 'eos-toast-bell';
            bell.type = 'button';
            bell.className = 'eos-toast-bell';
            bell.title = 'Recent notifications — click to reopen';
            bell.setAttribute('aria-label', 'Recent notifications');
            bell.addEventListener('click', function() { EOS_UI.toastHistory(); });
            document.body.appendChild(bell);
        }
        var n = EOS_UI._toastLog.length;
        bell.style.display = n ? '' : 'none';
        bell.innerHTML = '<span class="eos-toast-bell-icon">&#128276;</span>' +
                         '<span class="eos-toast-bell-count">' + n + '</span>';
    },
    _fmtAgo: function(ms) {
        var s = Math.max(0, Math.floor(ms / 1000));
        if (s < 60) return s + 's ago';
        var m = Math.floor(s / 60);
        if (m < 60) return m + 'm ago';
        var h = Math.floor(m / 60);
        if (h < 24) return h + 'h ago';
        return Math.floor(h / 24) + 'd ago';
    },
    toastHistory: function() {
        var rows = EOS_UI._toastLog.map(function(t, i) {
            var ago = EOS_UI._fmtAgo(Date.now() - t.ts);
            var cls = t.ok ? 'ok' : 'err';
            return '<div class="eos-toast-log-row eos-toast-log-' + cls + '" data-idx="' + i + '">' +
                   '<div class="eos-toast-log-msg">' + EOS_UI.esc(t.msg) + '</div>' +
                   '<div class="eos-toast-log-meta">' +
                       '<span class="eos-toast-log-ago">' + ago + '</span>' +
                       '<button type="button" class="eos-toast-log-btn" data-act="reopen" data-idx="' + i + '" title="Show this notification again">Reopen</button>' +
                       '<button type="button" class="eos-toast-log-btn" data-act="copy" data-idx="' + i + '" title="Copy notification text to clipboard">Copy</button>' +
                   '</div>' +
                   '</div>';
        }).join('');
        if (!rows) rows = '<div class="eos-toast-log-empty">No notifications yet.</div>';
        var footer = EOS_UI._toastLog.length
            ? '<div class="eos-toast-log-footer"><button type="button" class="eos-btn" id="eos-toast-log-clear" title="Remove all entries from the notification history">Clear all</button></div>'
            : '';
        EOS_UI.modal({
            title: 'Recent notifications',
            body: '<div class="eos-toast-log">' + rows + '</div>' + footer,
            width: '520px',
        });
        var clearBtn = document.getElementById('eos-toast-log-clear');
        if (clearBtn) clearBtn.addEventListener('click', function() {
            EOS_UI._toastLog = [];
            EOS_UI._toastPersist();
            EOS_UI._updateToastBell();
            EOS_UI.closeModal();
        });
        document.querySelectorAll('.eos-toast-log-row').forEach(function(row) {
            row.querySelectorAll('.eos-toast-log-btn').forEach(function(btn) {
                btn.addEventListener('click', function(ev) {
                    ev.stopPropagation();
                    var idx = parseInt(btn.getAttribute('data-idx'), 10);
                    var entry = EOS_UI._toastLog[idx];
                    if (!entry) return;
                    if (btn.getAttribute('data-act') === 'copy') {
                        EOS_UI.copy(entry.msg);
                        btn.textContent = 'Copied';
                        setTimeout(function() { btn.textContent = 'Copy'; }, 1200);
                    } else {
                        EOS_UI._toastShow(entry.msg, entry.ok);
                    }
                });
            });
            row.addEventListener('click', function() {
                var idx = parseInt(row.getAttribute('data-idx'), 10);
                var entry = EOS_UI._toastLog[idx];
                if (entry) EOS_UI._toastShow(entry.msg, entry.ok);
            });
        });
    },

    // Escape HTML — TEXT context only (does NOT encode " or '). For attribute
    // values use escAttr (below). Mixing them up is an XSS hole — see
    // scripts/check-attr-escaper.py.
    esc: function(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; },
    // Shared "save calculation -> vault" surface for deterministic calculator apps.
    // Backed by BaseApp.save_calculation / list_calculations (emptyos/sdk/base_app.py);
    // collapses the per-app save-button + recent-list JS into one mount.
    //   opts.app      — url prefix segment (app id), e.g. 'cable-stress'
    //   opts.button   — selector or element for the Save button
    //   opts.list     — selector or element for the recent-calculations container
    //   opts.getState — () => {label, inputs, result, method?} | null  (null = nothing to save)
    // Returns { reload, save }.
    savedCalculations: function(opts) {
        var self = this;
        var btn = typeof opts.button === 'string' ? document.querySelector(opts.button) : opts.button;
        var list = typeof opts.list === 'string' ? document.querySelector(opts.list) : opts.list;
        var prefix = '/' + opts.app;
        function when(iso) { return iso ? String(iso).replace('T', ' ').slice(0, 16) : ''; }
        function render(items) {
            if (!list) return;
            if (!items.length) { list.innerHTML = '<div class="saved-empty">No saved calculations yet.</div>'; return; }
            list.innerHTML = items.map(function(it) {
                var links = (window.EOS && EOS.noteActions) ? EOS.noteActions(it.path) : '';
                return '<div class="saved-item"><span class="t">' + self.esc(it.title || '')
                    + '<div class="when">' + when(it.created) + '</div></span><span>' + links + '</span></div>';
            }).join('');
        }
        function reload() {
            if (!(window.EOS && EOS.api)) return Promise.resolve();
            return EOS.api(prefix + '/api/calculations')
                .then(function(r) { render((r && r.items) || []); })
                .catch(function() {});
        }
        function save() {
            if (!(window.EOS && EOS.post)) return;
            var st = opts.getState ? opts.getState() : null;
            if (!st) return;
            if (btn) btn.disabled = true;
            return EOS.post(prefix + '/api/save-calculation', {
                label: st.label, inputs: st.inputs, result: st.result, method: st.method,
            }).then(function(r) {
                if (r && r.ok) { if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast('Saved to vault'); return reload(); }
                if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast((r && r.error) || 'Save failed', false);
            }).catch(function(e) {
                if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast('Save failed: ' + (e.message || e), false);
            }).finally(function() { if (btn) btn.disabled = false; });
        }
        if (btn) btn.addEventListener('click', save);
        reload();
        return { reload: reload, save: save };
    },

    // ── Stateful deep-links — "open the answer in its real tool, with the case
    // loaded" (see .claude/rules/deep-link-to-app.md). prefillForm is the
    // consumer half of BaseApp.app_link: it reads URL params and fills the form,
    // auto-running the page's compute when ?run=1. shareLink is the inverse —
    // build a shareable URL from the CURRENT form state. Both work over one
    // {param: selector} field map, so a page wires them once and the companion's
    // link, a bookmark, and the Share button all use the same contract.

    // Read URL query params into a form. `fields` maps a param name to a CSS
    // selector (or element). Sets <select>/<input> values; checks/unchecks
    // checkboxes for "true"/"false"/"1"/"0". When ?run=1 (or opts.run forced),
    // calls onReady() once after filling so results are ready on open. Returns
    // {params, filled:[...], ran:bool}. No-op (ran:false) when no mapped params
    // are present, so a cold page load is unaffected.
    prefillForm: function(opts) {
        opts = opts || {};
        var fields = opts.fields || {};
        var qp = new URLSearchParams(location.search);
        var filled = [];
        Object.keys(fields).forEach(function(param) {
            if (!qp.has(param)) return;
            var el = typeof fields[param] === 'string'
                ? document.querySelector(fields[param]) : fields[param];
            if (!el) return;
            var v = qp.get(param);
            if (el.type === 'checkbox') {
                el.checked = (v === '1' || v === 'true');
            } else {
                el.value = v;
            }
            // Fire input + change so debounced/listener-driven pages react.
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            filled.push(param);
        });
        var wantRun = opts.run === true || (opts.run !== false && qp.get('run') === '1');
        var ran = false;
        if (filled.length && wantRun && typeof opts.onReady === 'function') {
            try { opts.onReady(); ran = true; } catch (e) { /* page compute failed — non-fatal */ }
        }
        return { params: qp, filled: filled, ran: ran };
    },

    // Build a shareable absolute URL from the CURRENT form state, over the same
    // {param: selector} map prefillForm consumes (round-trip safe). Skips empty
    // values. opts.run (default true) appends run=1 so the recipient auto-computes.
    buildShareUrl: function(fields, opts) {
        opts = opts || {};
        var pairs = [];
        Object.keys(fields || {}).forEach(function(param) {
            var el = typeof fields[param] === 'string'
                ? document.querySelector(fields[param]) : fields[param];
            if (!el) return;
            var v = el.type === 'checkbox' ? (el.checked ? '1' : '') : (el.value == null ? '' : String(el.value));
            if (v === '') return;
            pairs.push(encodeURIComponent(param) + '=' + encodeURIComponent(v));
        });
        if (opts.run !== false) pairs.push('run=1');
        return location.origin + location.pathname + (pairs.length ? '?' + pairs.join('&') : '');
    },

    // Mount a 🔗 Share button that copies the current form state as a case URL.
    // opts: {mount, fields, label?, run?}. Returns {url: fn}.
    shareLink: function(opts) {
        opts = opts || {};
        var host = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
        if (!host) return { url: function() { return EOS_UI.buildShareUrl(opts.fields, opts); } };
        var btn = document.createElement('button');
        btn.className = opts.className || 'eos-btn-sm';
        btn.type = 'button';
        btn.textContent = '🔗 ' + (opts.label || 'Share');
        btn.title = 'Copy a link to this exact case';
        btn.onclick = function() {
            EOS_UI.copy(EOS_UI.buildShareUrl(opts.fields, opts), { toast: 'Case link copied' });
        };
        host.appendChild(btn);
        return { url: function() { return EOS_UI.buildShareUrl(opts.fields, opts); }, el: btn };
    },

    // Thin "loaded from a link" banner shown when a page opened with deep-link
    // params. opts: {mount, fields, onSave?}. Renders Copy-link + (if onSave) a
    // Save button. Returns the banner element, or null when no params present.
    deepLinkBanner: function(opts) {
        opts = opts || {};
        var qp = new URLSearchParams(location.search);
        var has = Object.keys(opts.fields || {}).some(function(p) { return qp.has(p); });
        if (!has) return null;
        var host = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
        if (!host) return null;
        var bar = document.createElement('div');
        bar.className = 'eos-deeplink-banner';
        bar.innerHTML = '<span>🔗 Loaded from a shared link — recomputed here.</span>';
        var copy = document.createElement('button');
        copy.className = 'eos-btn-sm';
        copy.textContent = 'Copy link';
        copy.onclick = function() { EOS_UI.copy(EOS_UI.buildShareUrl(opts.fields, opts), { toast: 'Case link copied' }); };
        bar.appendChild(copy);
        if (typeof opts.onSave === 'function') {
            var save = document.createElement('button');
            save.className = 'eos-btn-sm';
            save.textContent = 'Save case';
            save.onclick = function() { opts.onSave(); };
            bar.appendChild(save);
        }
        host.insertBefore(bar, host.firstChild);
        return bar;
    },

    // Escape for an HTML ATTRIBUTE value — encodes & " ' < (mirror of window.escAttr).
    escAttr: function(s) { return (s == null ? '' : String(s)).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;'); },
    // Render a SandboxedWrite diff into themed HTML. `lines` is the
    // diff_lines payload [{kind, text}] where kind ∈ add|del|ctx|hunk. Returns a
    // self-contained .eos-diff scroll box (CSS in eos-components.css). Shared by
    // the element-edit consumers (designer, viz) — see .claude/rules/proposed-action.md
    // and .claude/rules/artifact-element-edit.md.
    diffLinesHtml: function(lines) {
        lines = lines || [];
        var cls = { add: 'd-add', del: 'd-del', ctx: 'd-ctx', hunk: 'd-hunk' };
        var rows = lines.length
            ? lines.map(function(l) { return '<div class="dl ' + (cls[l.kind] || 'd-ctx') + '">' + EOS_UI.esc(l.text) + '</div>'; }).join('')
            : '<div class="dl d-ctx">(no change)</div>';
        return '<div class="eos-diff">' + rows + '</div>';
    },

    // Find `text` (case-insensitive, whitespace-tolerant) inside `root`,
    // scroll it into view, and briefly wrap it in <mark class="eos-flash">
    // for visual confirmation. Auto-unwraps after `opts.duration` ms (default 2600).
    //
    // Returns true on match, false otherwise. Cross-element matches fall back
    // to scroll-only (no flash) because Range.surroundContents can't wrap
    // across element boundaries — extend with a multi-range fallback if a
    // second consumer hits the gap.
    //
    // Consumers (Rule 9 — extracted on the 2nd consumer signal):
    //   - apps/learn/  — click a saved reader-note → flash the quote in the lesson
    //   - apps/reader/ — (candidate) click a passage marker → flash in the book body
    //
    // Pairs with the `.eos-flash` keyframe defined in eos-components.css.
    flashText: function(root, text, opts) {
        opts = opts || {};
        var duration = opts.duration || 2600;
        var block = opts.block || 'center';
        var behavior = opts.behavior || 'smooth';
        var className = opts.className || 'eos-flash';
        if (!root || !text) return false;
        var needle = String(text).replace(/\s+/g, ' ').trim().toLowerCase();
        if (!needle) return false;
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        var node;
        while ((node = walker.nextNode())) {
            var hayRaw = node.nodeValue;
            var hay = hayRaw.replace(/\s+/g, ' ').toLowerCase();
            var pos = hay.indexOf(needle);
            if (pos < 0) continue;
            // Map collapsed-string position back to original-string position.
            var origStart = 0, seen = 0, lastSpace = false;
            while (origStart < hayRaw.length && seen < pos) {
                var c = hayRaw[origStart];
                if (/\s/.test(c)) { if (!lastSpace) seen++; lastSpace = true; }
                else { seen++; lastSpace = false; }
                origStart++;
            }
            var origEnd = origStart, matchLen = 0;
            lastSpace = origStart > 0 && /\s/.test(hayRaw[origStart - 1]);
            while (origEnd < hayRaw.length && matchLen < needle.length) {
                var c2 = hayRaw[origEnd];
                if (/\s/.test(c2)) { if (!lastSpace) matchLen++; lastSpace = true; }
                else { matchLen++; lastSpace = false; }
                origEnd++;
            }
            try {
                var range = document.createRange();
                range.setStart(node, Math.max(0, origStart));
                range.setEnd(node, Math.min(node.nodeValue.length, origEnd));
                var mark = document.createElement('mark');
                mark.className = className;
                range.surroundContents(mark);
                mark.scrollIntoView({ block: block, behavior: behavior });
                setTimeout(function() {
                    var parent = mark.parentNode;
                    if (!parent) return;
                    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
                    parent.removeChild(mark);
                    parent.normalize();
                }, duration);
                return true;
            } catch (e) {
                var fallback = node.parentElement;
                if (fallback) fallback.scrollIntoView({ block: block, behavior: behavior });
                return true;
            }
        }
        return false;
    },

    // Render a row of action-result links produced by a [DO:] execution or
    // pending-action apply. `links` is the shape returned by /rooms/api/do
    // and /rooms/api/pending/{id}/apply:
    //   [{type:"note", path:"...", label?}, {type:"app", url:"...", label?}, ...]
    // Returns "" when there are no usable links so callers can concatenate
    // safely. Used by page-assistant.js (click-to-execute + auto-exec server
    // results) and rooms/pages/index.html (review-gate apply + chat scrollback).
    actionLinks: function(links) {
        if (!links || !links.length) return '';
        var parts = [];
        links.forEach(function(l) {
            if (l.type === 'note' && l.path && window.EOS && EOS.noteActions) {
                parts.push(EOS.noteActions(l.path, l.label || ''));
            } else if (l.type === 'app' && l.url) {
                var url = (window.escAttr || EOS_UI.esc)(l.url);
                var label = (window.esc || EOS_UI.esc)(l.label || l.url);
                parts.push('<a href="' + url + '">' + label + '</a>');
            }
        });
        if (!parts.length) return '';
        return '<div class="eos-action-links">' +
               parts.join('<span class="eos-action-links-sep">·</span>') +
               '</div>';
    },

    // Strip markdown noise so TTS reads prose, not punctuation. Collapses fenced
    // code blocks to a short marker (too long to speak verbatim), flattens inline
    // code to plain text, drops heading/bold/italic chars, normalises newlines.
    // Used by apps/assistant speakText and the hands-free overlay — extracted here
    // because both were verbatim copies of the same six regex replacements.
    stripMarkdownForTts: function(text) {
        return String(text || '')
            .replace(/```[\s\S]*?```/g, '(code block)')
            .replace(/`[^`]+`/g, function(m) { return m.slice(1, -1); })
            .replace(/[#*_~\[\]]/g, '')
            .replace(/\n{2,}/g, '. ')
            .replace(/\n/g, ' ')
            .trim();
    },

    // Tab switching — works with .eos-tab buttons and .eos-tab-content panels.
    // Usage 1 (array): EOS_UI.switchTab(['log','history','calendar'], 'history')
    // Usage 2 (auto):  EOS_UI.switchTab(name) — finds tabs by data-tab attribute
    // HTML: <div class="eos-tabs"><div class="eos-tab" data-tab="log" onclick="EOS_UI.switchTab('log')">Log</div>...</div>
    //       <div id="tab-log" class="eos-tab-content active">...</div>
    switchTab: function(tabsOrName, name) {
        if (typeof tabsOrName === 'string') {
            // Auto mode: find all tabs by data-tab attribute
            name = tabsOrName;
            document.querySelectorAll('.eos-tab[data-tab]').forEach(function(el) {
                el.classList.toggle('active', el.getAttribute('data-tab') === name);
            });
        } else {
            // Legacy array mode
            tabsOrName.forEach(function(t, i) {
                var tabEl = document.querySelectorAll('.eos-tab')[i];
                if (tabEl) tabEl.classList.toggle('active', tabsOrName[i] === name);
            });
        }
        document.querySelectorAll('.eos-tab-content').forEach(function(el) {
            var id = el.id.replace('tab-', '');
            el.classList.toggle('active', id === name);
        });
        EOS_UI._initTabA11y();
    },

    // Make .eos-tab strips keyboard-navigable fleet-wide: role=tab + tabindex=0
    // so the global Enter/Space delegate in eos.js activates them. Idempotent;
    // called on DOMContentLoaded and after every switchTab (covers dynamic tabs).
    _initTabA11y: function() {
        document.querySelectorAll('.eos-tab').forEach(function(el) {
            if (el.getAttribute('role') !== 'tab') el.setAttribute('role', 'tab');
            if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
            el.setAttribute('aria-selected', el.classList.contains('active') ? 'true' : 'false');
        });
    },

    // SVG Sparkline — inline trend chart
    // Usage: EOS_UI.sparkline(targetId, [3,5,2,8,6,9,4], {color:'#8b5cf6', height:40, width:120})
    sparkline: function(targetId, values, opts) {
        var el = document.getElementById(targetId);
        if (!el || !values || !values.length) return;
        opts = opts || {};
        var w = opts.width || el.offsetWidth || 120;
        var h = opts.height || 40;
        var color = opts.color || 'var(--accent)';
        var fill = opts.fill || false;

        var min = Math.min.apply(null, values);
        var max = Math.max.apply(null, values);
        var range = max - min || 1;
        var pad = 2;
        var step = (w - pad * 2) / (values.length - 1);

        var points = values.map(function(v, i) {
            var x = pad + i * step;
            var y = h - pad - ((v - min) / range) * (h - pad * 2);
            return x.toFixed(1) + ',' + y.toFixed(1);
        });

        var svg = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">';
        if (fill) {
            svg += '<polygon points="' + pad + ',' + (h - pad) + ' ' + points.join(' ') + ' ' + (w - pad) + ',' + (h - pad) +
                '" fill="' + color + '" opacity="0.1"/>';
        }
        svg += '<polyline points="' + points.join(' ') + '" fill="none" stroke="' + color +
            '" stroke-width="' + (opts.strokeWidth || 2) + '" stroke-linecap="round" stroke-linejoin="round"/>';
        // Last point dot
        var last = points[points.length - 1].split(',');
        svg += '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="3" fill="' + color + '"/>';
        svg += '</svg>';
        el.innerHTML = svg;
    },

    // SVG line chart with labeled axes — dependency-free (no Plotly/Chart.js).
    // Built for parameter-sweep curves (BaseApp.sweep_method output maps 1:1).
    // Usage: EOS_UI.lineChart({mount:'#c', x:[10,20,30], y:[820,760,690],
    //                          x_label:'Ambient (°C)', y_label:'Ampacity (A)',
    //                          title:'Sensitivity', color:'var(--accent)'})
    // y entries may be null (failed sweep points) — the line breaks across gaps.
    lineChart: function(opts) {
        var el = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
        if (!el) return;
        var xs = opts.x || [], ys = opts.y || [];
        var pairs = [];
        for (var i = 0; i < xs.length; i++) {
            var yv = ys[i];
            if (typeof xs[i] === 'number' && typeof yv === 'number' && isFinite(yv)) {
                pairs.push([xs[i], yv]);
            }
        }
        if (pairs.length < 1) {
            el.innerHTML = '<div style="color:var(--text-muted);font-size:12.5px;padding:12px">No data to plot.</div>';
            return;
        }
        var w = opts.width || el.offsetWidth || 520;
        if (w < 200) w = 520;
        var h = opts.height || 260;
        var color = opts.color || 'var(--accent)';
        var padL = 56, padR = 16, padT = opts.title ? 28 : 12, padB = 40;
        var plotW = w - padL - padR, plotH = h - padT - padB;

        var xMin = Math.min.apply(null, pairs.map(function(p){return p[0];}));
        var xMax = Math.max.apply(null, pairs.map(function(p){return p[0];}));
        var yMin = Math.min.apply(null, pairs.map(function(p){return p[1];}));
        var yMax = Math.max.apply(null, pairs.map(function(p){return p[1];}));
        var xRange = (xMax - xMin) || 1, yRange = (yMax - yMin) || 1;
        // Pad the y domain 5% so the curve isn't flush to the frame.
        yMin -= yRange * 0.05; yMax += yRange * 0.05; yRange = yMax - yMin;

        var sx = function(v){ return padL + ((v - xMin) / xRange) * plotW; };
        var sy = function(v){ return padT + plotH - ((v - yMin) / yRange) * plotH; };
        var fmt = function(v){
            var a = Math.abs(v);
            if (a !== 0 && (a < 0.01 || a >= 100000)) return v.toExponential(1);
            return (Math.round(v * 100) / 100).toString();
        };

        // Build polyline segments, breaking on null/non-finite gaps.
        var segs = [], cur = [];
        for (var j = 0; j < xs.length; j++) {
            var yj = ys[j];
            if (typeof xs[j] === 'number' && typeof yj === 'number' && isFinite(yj)) {
                cur.push(sx(xs[j]).toFixed(1) + ',' + sy(yj).toFixed(1));
            } else if (cur.length) { segs.push(cur); cur = []; }
        }
        if (cur.length) segs.push(cur);

        var muted = 'var(--text-muted)', border = 'var(--border)';
        var svg = '<svg width="100%" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="xMidYMid meet" role="img">';
        if (opts.title) {
            svg += '<text x="' + padL + '" y="16" fill="var(--text)" font-size="13" font-weight="600">' + EOS_UI.esc(opts.title) + '</text>';
        }
        // Axes
        svg += '<line x1="' + padL + '" y1="' + (padT + plotH) + '" x2="' + (padL + plotW) + '" y2="' + (padT + plotH) + '" stroke="' + border + '" stroke-width="1"/>';
        svg += '<line x1="' + padL + '" y1="' + padT + '" x2="' + padL + '" y2="' + (padT + plotH) + '" stroke="' + border + '" stroke-width="1"/>';
        // y ticks (min, mid, max) with gridlines
        [yMin, (yMin + yMax) / 2, yMax].forEach(function(tv) {
            var ty = sy(tv);
            svg += '<line x1="' + padL + '" y1="' + ty.toFixed(1) + '" x2="' + (padL + plotW) + '" y2="' + ty.toFixed(1) + '" stroke="' + border + '" stroke-width="0.5" opacity="0.4"/>';
            svg += '<text x="' + (padL - 6) + '" y="' + (ty + 3).toFixed(1) + '" fill="' + muted + '" font-size="10" text-anchor="end">' + fmt(tv) + '</text>';
        });
        // x ticks (min, mid, max)
        [xMin, (xMin + xMax) / 2, xMax].forEach(function(tv) {
            var tx = sx(tv);
            svg += '<text x="' + tx.toFixed(1) + '" y="' + (padT + plotH + 16) + '" fill="' + muted + '" font-size="10" text-anchor="middle">' + fmt(tv) + '</text>';
        });
        // Axis labels
        if (opts.x_label) {
            svg += '<text x="' + (padL + plotW / 2) + '" y="' + (h - 4) + '" fill="' + muted + '" font-size="11" text-anchor="middle">' + EOS_UI.esc(opts.x_label) + '</text>';
        }
        if (opts.y_label) {
            var ly = padT + plotH / 2;
            svg += '<text x="12" y="' + ly + '" fill="' + muted + '" font-size="11" text-anchor="middle" transform="rotate(-90 12 ' + ly + ')">' + EOS_UI.esc(opts.y_label) + '</text>';
        }
        // Series
        segs.forEach(function(seg) {
            svg += '<polyline points="' + seg.join(' ') + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
        });
        pairs.forEach(function(p) {
            svg += '<circle cx="' + sx(p[0]).toFixed(1) + '" cy="' + sy(p[1]).toFixed(1) + '" r="2.5" fill="' + color + '"/>';
        });
        svg += '</svg>';
        el.innerHTML = svg;
    },


    // SVG Donut chart from {category: amount} data
    donut: function(targetId, data, colors) {
        colors = colors || ['#8b5cf6','#3b82f6','#10b981','#f59e0b','#ef4444','#ec4899','#06b6d4','#84cc16','#f97316','#6366f1','#14b8a6','#e11d48'];
        var entries = Object.entries(data).sort(function(a,b) { return b[1]-a[1]; });
        var total = entries.reduce(function(s,e) { return s+e[1]; }, 0);
        if (total === 0) { document.getElementById(targetId).innerHTML = ''; return; }

        var r=50, cx=60, cy=60, C=2*Math.PI*r, offset=0;
        var circles = entries.map(function(e,i) {
            var pct=e[1]/total, dash=pct*C;
            var c='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="'+colors[i%colors.length]+'" stroke-width="14" stroke-dasharray="'+dash.toFixed(1)+' '+(C-dash).toFixed(1)+'" stroke-dashoffset="-'+offset.toFixed(1)+'" style="transition:stroke-dasharray 0.5s"/>';
            offset+=dash; return c;
        }).join('');
        var svg = '<div><svg width="130" height="130" viewBox="0 0 120 120" style="transform:rotate(-90deg)">'+circles+'</svg></div>';
        var legend = '<div class="eos-legend">' + entries.slice(0,8).map(function(e,i) {
            var pct = (e[1]/total*100).toFixed(0);
            var val = typeof e[1]==='number' && e[1]%1!==0 ? e[1].toFixed(1) : e[1];
            return '<div class="eos-legend-row"><span class="eos-legend-dot" style="background:'+colors[i%colors.length]+'"></span><span class="eos-legend-name">'+e[0]+'</span><span class="eos-legend-val">'+val+'</span><span class="eos-legend-pct">'+pct+'%</span></div>';
        }).join('') + '</div>';

        document.getElementById(targetId).innerHTML = svg + legend;
    },

    // SVG Ring (health score, level progress, etc.)
    ring: function(targetId, score, max, color) {
        max = max || 100;
        var ringEl = document.getElementById(targetId);
        if (!ringEl) return;
        var circumference = parseFloat(ringEl.getAttribute('stroke-dasharray') || '377');
        var offset = circumference - (circumference * score / max);
        ringEl.style.strokeDashoffset = offset;
        ringEl.style.stroke = color || (score >= 80 ? '#10b981' : score >= 60 ? '#f59e0b' : '#ef4444');
    },

    // Heatmap from {date: count} data — legacy shim, delegates to yearHeatmap.
    // Prefer EOS_UI.yearHeatmap({mount, data, ...}) for new code.
    // showMonthLabels off here so existing consumers (90-day strips) don't regress visually.
    heatmap: function(targetId, data, days) {
        var months = Math.max(1, Math.round((days || 90) / 30));
        return EOS_UI.yearHeatmap({mount: '#' + targetId, data: data, months: months, showMonthLabels: false});
    },

    // Year/month heatmap — intensity grid keyed by YYYY-MM-DD count.
    // opts: {mount, data, months=6, intensity?, tooltipFor?, onCellClick?,
    //        showMonthLabels=true, showStats=false, fromDate?}
    // Returns {refresh(newData), el}.
    yearHeatmap: function(opts) {
        opts = opts || {};
        var mount = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
        if (!mount) return null;
        var months = opts.months || 6;
        var bucket = opts.intensity || function(c) {
            return c === 0 ? 0 : c <= 1 ? 1 : c <= 2 ? 2 : c <= 4 ? 3 : 4;
        };
        var tipFor = opts.tooltipFor || function(date, count) {
            return date + ': ' + count;
        };
        var pad2 = function(n) { return (n < 10 ? '0' : '') + n; };
        var iso = function(d) {
            return d.getFullYear() + '-' + pad2(d.getMonth()+1) + '-' + pad2(d.getDate());
        };

        mount.classList.add('eos-hm-wrap');
        mount.innerHTML =
            (opts.showMonthLabels !== false ? '<div class="eos-hm-months"></div>' : '') +
            '<div class="eos-hm-grid"></div>' +
            (opts.showStats ? '<div class="eos-hm-stats"></div>' : '');
        var grid = mount.querySelector('.eos-hm-grid');
        var labels = mount.querySelector('.eos-hm-months');
        var stats = mount.querySelector('.eos-hm-stats');

        var render = function(data) {
            data = data || {};
            var end = opts.fromDate ? new Date(opts.fromDate) : new Date();
            var start = new Date(end); start.setMonth(start.getMonth() - months);
            var html = [];
            var monthMarks = [];
            var seenMonth = -1;
            var total = 0, active = 0;
            for (var d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
                var key = iso(d);
                var count = data[key] || 0;
                if (count > 0) { total += count; active++; }
                var lvl = bucket(count);
                if (d.getMonth() !== seenMonth) {
                    seenMonth = d.getMonth();
                    monthMarks.push(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][seenMonth]);
                }
                html.push(
                    '<div class="eos-hm-cell eos-hm-' + lvl + '" data-tip="' +
                    EOS_UI.escAttr(tipFor(key, count)) + '" data-date="' + key +
                    '" data-count="' + count + '"></div>'
                );
            }
            grid.innerHTML = html.join('');
            if (labels) {
                labels.innerHTML = monthMarks.map(function(m) {
                    return '<span class="eos-hm-month-label">' + m + '</span>';
                }).join('');
            }
            if (stats) {
                stats.innerHTML =
                    '<span><strong>' + total + '</strong> entries</span>' +
                    '<span><strong>' + active + '</strong> active days</span>';
            }
            if (opts.onCellClick) {
                grid.querySelectorAll('.eos-hm-cell').forEach(function(c) {
                    c.addEventListener('click', function() {
                        opts.onCellClick(c.getAttribute('data-date'),
                            parseInt(c.getAttribute('data-count'), 10) || 0);
                    });
                });
            }
        };
        render(opts.data || {});
        return { refresh: render, el: mount };
    },

    // Month grid — 7×6 calendar grid with per-day items overlaid.
    // opts: {mount, month?='YYYY-MM', cells=[{date,items?:[]}], onDayClick?,
    //        onMonthChange?, renderCell?, weekStart='monday', showOtherMonth=true,
    //        title?}
    // Returns {refresh(newCells), setMonth(yyyymm), el, month()}.
    monthGrid: function(opts) {
        opts = opts || {};
        var mount = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
        if (!mount) return null;
        var pad2 = function(n) { return (n < 10 ? '0' : '') + n; };
        var ymOf = function(d) { return d.getFullYear() + '-' + pad2(d.getMonth()+1); };
        var iso = function(d) { return ymOf(d) + '-' + pad2(d.getDate()); };

        var month = opts.month;
        if (!month) { var t = new Date(); month = ymOf(t); }
        var weekStart = opts.weekStart === 'sunday' ? 0 : 1;
        var showOther = opts.showOtherMonth !== false;
        var cellsData = {};
        (opts.cells || []).forEach(function(c) { cellsData[c.date] = c; });

        mount.classList.add('eos-mg-wrap');
        mount.innerHTML =
            '<div class="eos-mg-header">' +
                '<button class="eos-mg-nav" data-dir="-1" aria-label="Previous month" title="Previous month">&#8249;</button>' +
                '<div class="eos-mg-title"></div>' +
                '<button class="eos-mg-nav" data-dir="1" aria-label="Next month" title="Next month">&#8250;</button>' +
            '</div>' +
            '<div class="eos-mg-weekdays"></div>' +
            '<div class="eos-mg-grid"></div>';

        var titleEl = mount.querySelector('.eos-mg-title');
        var weekEl = mount.querySelector('.eos-mg-weekdays');
        var gridEl = mount.querySelector('.eos-mg-grid');

        var monthName = ['January','February','March','April','May','June',
            'July','August','September','October','November','December'];
        var dayHeaders = weekStart === 1
            ? ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
            : ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
        weekEl.innerHTML = dayHeaders.map(function(d) {
            return '<div class="eos-mg-weekday">' + d + '</div>';
        }).join('');

        var renderCellHtml = opts.renderCell || function(cell) {
            var items = (cell && cell.items) || [];
            if (!items.length) return '';
            var dots = items.slice(0, 4).map(function(it) {
                var color = it.color ? ' style="background:' + EOS_UI.escAttr(it.color) + '"' : '';
                return '<span class="eos-mg-dot' + (it.tone ? ' tone-' + it.tone : '') + '"' + color + '></span>';
            }).join('');
            var more = items.length > 4 ? '<span class="eos-mg-more">+' + (items.length - 4) + '</span>' : '';
            return '<div class="eos-mg-dots">' + dots + more + '</div>';
        };

        var render = function() {
            var parts = month.split('-');
            var y = parseInt(parts[0], 10);
            var m = parseInt(parts[1], 10) - 1;
            titleEl.textContent = (opts.title ? opts.title + ' — ' : '') + monthName[m] + ' ' + y;

            var firstOfMonth = new Date(y, m, 1);
            var firstDow = firstOfMonth.getDay(); // 0=Sun
            var lead = (firstDow - weekStart + 7) % 7;
            var gridStart = new Date(y, m, 1 - lead);
            var today = new Date();
            var todayIso = iso(today);
            var html = [];
            for (var i = 0; i < 42; i++) {
                var d = new Date(gridStart);
                d.setDate(gridStart.getDate() + i);
                var ds = iso(d);
                var inMonth = d.getMonth() === m;
                var cell = cellsData[ds];
                var classes = ['eos-mg-cell'];
                if (!inMonth) classes.push('eos-mg-other-month');
                if (ds === todayIso) classes.push('eos-mg-today');
                if (cell && cell.items && cell.items.length) classes.push('eos-mg-has-items');
                if (!showOther && !inMonth) {
                    html.push('<div class="' + classes.join(' ') + '" data-date="' + ds + '"></div>');
                    continue;
                }
                html.push(
                    '<div class="' + classes.join(' ') + '" data-date="' + ds + '">' +
                        '<div class="eos-mg-day">' + d.getDate() + '</div>' +
                        renderCellHtml(cell || {date: ds, items: []}) +
                    '</div>'
                );
            }
            gridEl.innerHTML = html.join('');
            if (opts.onDayClick) {
                gridEl.querySelectorAll('.eos-mg-cell').forEach(function(c) {
                    c.addEventListener('click', function() {
                        var ds = c.getAttribute('data-date');
                        opts.onDayClick(ds, (cellsData[ds] && cellsData[ds].items) || []);
                    });
                });
            }
        };

        mount.querySelectorAll('.eos-mg-nav').forEach(function(b) {
            b.addEventListener('click', function() {
                var dir = parseInt(b.getAttribute('data-dir'), 10);
                var parts = month.split('-');
                var y = parseInt(parts[0], 10);
                var m = parseInt(parts[1], 10) - 1 + dir;
                var nd = new Date(y, m, 1);
                month = ymOf(nd);
                render();
                if (opts.onMonthChange) opts.onMonthChange(month);
            });
        });

        render();
        return {
            refresh: function(newCells) {
                cellsData = {};
                (newCells || []).forEach(function(c) { cellsData[c.date] = c; });
                render();
            },
            setMonth: function(ym) { month = ym; render(); },
            month: function() { return month; },
            el: mount,
        };
    },

    // Render entry list
    entries: function(targetId, items, config) {
        config = config || {};
        var el = document.getElementById(targetId);
        if (!items.length) { el.innerHTML = '<div class="eos-empty">No items</div>'; return; }
        var textField = config.text || 'description';
        var valField = config.value || 'amount';
        var catField = config.category || 'category';
        var dateField = config.date || 'date';
        var prefix = config.prefix || '';
        var colors = config.colors || ['#8b5cf6','#3b82f6','#10b981','#f59e0b','#ef4444','#ec4899','#06b6d4','#84cc16'];

        el.innerHTML = items.map(function(e) {
            var text = e[textField] || e.text || e.name || e.note || '';
            var val = e[valField];
            var valStr = val != null ? prefix + (typeof val==='number' ? val.toFixed(2) : val) : '';
            var cat = e[catField] || '';
            var date = (e[dateField] || e.timestamp || '').slice(0,10);
            var ci = Math.abs(cat.split('').reduce(function(h,c){return((h<<5)-h)+c.charCodeAt(0)|0},0)) % colors.length;
            return '<div class="eos-entry card-in">' +
                '<span class="eos-entry-date">' + date.slice(5) + '</span>' +
                '<span class="eos-entry-text">' + EOS_UI.esc(text) + '</span>' +
                (cat ? '<span class="eos-entry-badge" style="background:'+colors[ci]+'18;color:'+colors[ci]+'">' + EOS_UI.esc(cat) + '</span>' : '') +
                (valStr ? '<span class="eos-entry-val">' + valStr + '</span>' : '') +
                '</div>';
        }).join('');
    },

    // Open modal
    openModal: function(modalId) {
        document.getElementById(modalId).classList.add('show');
    },
    // The master FAB dock (eos.js #eos-fab-dock) is fixed bottom-right at
    // z-index 9999 — above the modal overlay (z-index 200). On a narrow
    // viewport the modal's right-aligned primary button lands under it and
    // becomes unclickable. Yield the corner while a modal is open, the same
    // way page-assistant.js yields it to the companion rail's Send button.
    // eos.js sets inline display:flex, so restore that, not ''.
    _fabYield: function(hide) {
        var dock = document.getElementById('eos-fab-dock');
        if (dock) dock.style.display = hide ? 'none' : 'flex';
    },

    closeModal: function(modalId) {
        if (modalId) { var el = document.getElementById(modalId); if (el) el.classList.remove('show'); return; }
        var overlay = document.getElementById('eos-modal-overlay');
        if (overlay) overlay.remove();
        EOS_UI._fabYield(false);
        if (EOS_UI._modalOnClose) EOS_UI._modalOnClose();
        EOS_UI._modalOnClose = null;
        if (EOS_UI._modalEscHandler) {
            document.removeEventListener('keydown', EOS_UI._modalEscHandler);
            EOS_UI._modalEscHandler = null;
        }
    },

    // --- Radial neighborhood graph ---
    // Tiny SVG graph for "1 entity + N peers on each side" views (KB notes,
    // app dependencies, related items). Not a force layout — for full network
    // graphs see emptyos/web/static/topology.html.
    //
    // EOS_UI.radialGraph(container, {
    //   center:    {label, color?},                       // required
    //   outgoing:  [{label, color?, clickable?, data?}],  // fan right
    //   incoming:  [{label, color?, clickable?, data?}],  // fan left
    //   onNodeClick: function(node) {},                   // peripheral nodes only
    //   maxPerSide: 8,                                    // truncate beyond
    // });
    radialGraph: function(container, opts) {
        opts = opts || {};
        if (typeof container === 'string') container = document.getElementById(container) || document.querySelector(container);
        if (!container) return;
        var center = opts.center || {label: '', color: 'var(--accent)'};
        var outgoing = (opts.outgoing || []).slice(0, opts.maxPerSide || 8);
        var incoming = (opts.incoming || []).slice(0, opts.maxPerSide || 8);

        if (!outgoing.length && !incoming.length) {
            container.innerHTML = '<div class="rg-empty">No incoming or outgoing links yet.</div>';
            return;
        }

        // Layout: stack peripheral nodes vertically on each side, lined up
        // with a small column. Labels render OUTSIDE the circle (right of
        // right-side circles, left of left-side circles) so vertical stacks
        // don't overlap. Width auto-grows with the longest label.
        var maxRows = Math.max(outgoing.length, incoming.length, 1);
        var rowH = 36;
        var H = Math.max(180, 72 + maxRows * rowH);
        var W = 720, cx = W/2, cy = H/2;
        var colX = 240;  // horizontal distance from center to each side column

        function stack(items, side) {
            var n = items.length;
            if (!n) return [];
            var totalH = (n - 1) * rowH;
            var startY = cy - totalH/2;
            var x = side === 'right' ? cx + colX : cx - colX;
            return items.map(function(item, i) {
                return {x: x, y: startY + i * rowH, item: item, side: side};
            });
        }
        var rightPts = stack(outgoing, 'right');
        var leftPts = stack(incoming, 'left');

        function trunc(s, n) { s = String(s||''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
        function lbl(s) { return EOS_UI.esc(trunc(s, 22)); }
        // Widen viewBox horizontally so peripheral labels have room. SVG content
        // outside the viewBox is clipped by the .eos-rg-wrap overflow:hidden, so
        // we reserve ~160px of padding each side for the longest labels.
        var pad = 160;
        var vbX = -pad, vbW = W + pad * 2;

        var svg = ['<svg viewBox="' + vbX + ' 0 ' + vbW + ' ' + H + '" preserveAspectRatio="xMidYMid meet">'];
        rightPts.forEach(function(p) {
            svg.push('<line class="rg-edge rg-edge-out" x1="' + cx + '" y1="' + cy + '" x2="' + p.x + '" y2="' + p.y + '"/>');
        });
        leftPts.forEach(function(p) {
            svg.push('<line class="rg-edge rg-edge-in" x1="' + p.x + '" y1="' + p.y + '" x2="' + cx + '" y2="' + cy + '"/>');
        });
        var centerColor = center.color || 'var(--accent)';
        svg.push('<g class="rg-node rg-node-center">' +
            '<circle cx="' + cx + '" cy="' + cy + '" r="22" fill="color-mix(in srgb,' + centerColor + ' 22%,transparent)" stroke="' + centerColor + '" stroke-width="2"/>' +
            '<text x="' + cx + '" y="' + (cy + 38) + '" text-anchor="middle">' + lbl(center.label) + '</text>' +
            '</g>');
        function drawNode(p) {
            var item = p.item;
            var color = item.color || 'var(--text-muted)';
            var clickable = item.clickable !== false;
            var anchor, labelX;
            if (p.side === 'right') { anchor = 'start'; labelX = p.x + 22; }
            else                    { anchor = 'end';   labelX = p.x - 22; }
            return '<g class="rg-node" data-clickable="' + clickable + '">' +
                '<circle cx="' + p.x + '" cy="' + p.y + '" r="14" fill="color-mix(in srgb,' + color + ' 16%,transparent)" stroke="' + color + '" stroke-width="1.5"' + (clickable ? '' : ' opacity="0.5"') + '/>' +
                '<text x="' + labelX + '" y="' + (p.y + 4) + '" text-anchor="' + anchor + '">' + lbl(item.label) + '</text>' +
                '</g>';
        }
        rightPts.forEach(function(p) { svg.push(drawNode(p)); });
        leftPts.forEach(function(p) { svg.push(drawNode(p)); });
        svg.push('</svg>');
        container.innerHTML = svg.join('');

        if (typeof opts.onNodeClick === 'function') {
            var groups = container.querySelectorAll('.rg-node:not(.rg-node-center)');
            groups.forEach(function(g, idx) {
                if (g.dataset.clickable === 'false') return;
                var pt = idx < rightPts.length ? rightPts[idx] : leftPts[idx - rightPts.length];
                g.style.cursor = 'pointer';
                g.addEventListener('click', function() { opts.onNodeClick(pt.item); });
            });
        }
    },

    // --- Callout post-pass for third-party markdown renderers (marked etc.) ---
    // Those render "> [!TYPE] Title" as <blockquote><p>[!TYPE] Title …</p>…;
    // restyle into the same .obs-callout boxes renderMarkdown produces from raw
    // text. Any type works (warning/PREDICT/UNVERIFIED/…) — class is
    // obs-callout-<type>; unstyled types inherit the base accent.
    upgradeCallouts: function(html) {
        return String(html == null ? '' : html).replace(
            /<blockquote>\s*<p>\[!(\w+)\]\s*([^<\n]*)/g,
            function(_, type, title) {
                return '<blockquote class="obs-callout obs-callout-' + type.toLowerCase() + '">' +
                    '<p><strong class="obs-callout-tag">' + type.toUpperCase() +
                    (title.trim() ? ' · ' + title.trim() : '') + '</strong>';
            });
    },

    // --- Render markdown into a DOM element (with esc fallback + append mode) ---
    // The shared "paint accumulated text as markdown into a node" op used by every
    // streaming chat surface (rooms, agent). Keeps the renderMarkdown call + the
    // innerHTML/append handling in one place; call sites keep their own
    // bundle-not-loaded guard since they can't reach this method if EOS_UI is absent.
    paintMarkdown: function(el, text, opts) {
        opts = opts || {};
        if (!el) return;
        var html = this.renderMarkdown ? this.renderMarkdown(text || '', opts) : this.esc(text || '');
        el.innerHTML = opts.append ? (el.innerHTML + html) : html;
    },

    // --- Vault JSON-field decode (mirrors BaseApp.vault_decode_json) ---
    // Frontmatter fields holding nested structures are stored as "@json <json>".
    // Returns dflt ([] by default) on anything malformed.
    decodeVaultJson: function(raw, dflt) {
        if (dflt === undefined) dflt = [];
        if (raw == null) return dflt;
        if (Array.isArray(raw) || (typeof raw === 'object')) return raw;
        if (typeof raw !== 'string') return dflt;
        var s = raw.trim();
        if (s.indexOf('@json ') === 0) s = s.slice(6).trim();
        if (!s) return dflt;
        try { return JSON.parse(s); } catch (e) { return dflt; }
    },

    // --- Note body render WITH viz-artifact embeds ---
    // Prose chunks render via the unchanged renderMarkdown (escaping intact);
    // each `<!-- eos:viz-embed embed_id=... mode=... shape=... -->` marker is
    // substituted for a sandboxed iframe to the viz serve endpoint. The marker
    // is dropped from prose BEFORE escaping so it never shows as literal text.
    // `vizEmbeds` is the note's decoded viz_embeds frontmatter (list of
    // {embed_id, mode, source_viz_id, shape, heavy, height}). Safe: marker
    // values + serve URLs are bake-authored ids, never free user text; the
    // iframe is sandbox="allow-scripts" only (opaque origin). See
    // .claude/rules/... artifact-embedding (the plan) + viz/embeds.py.
    renderMarkdownWithEmbeds: function(bodyMd, vizEmbeds, opts) {
        bodyMd = bodyMd || '';
        var meta = {};
        (vizEmbeds || []).forEach(function(e) { if (e && e.embed_id) meta[e.embed_id] = e; });
        var MARKER = /<!--\s*eos:viz-embed\s+(.*?)-->/g;
        var out = [];
        var last = 0;
        var m;
        while ((m = MARKER.exec(bodyMd)) !== null) {
            out.push(EOS_UI.renderMarkdown(bodyMd.slice(last, m.index), opts));
            last = MARKER.lastIndex;
            var kv = {};
            (m[1] || '').replace(/([\w-]+)=(\S+)/g, function(_, k, v) { kv[k] = v; return ''; });
            var eid = kv.embed_id || '';
            out.push(EOS_UI._vizEmbedHtml(eid, meta[eid], kv));
        }
        out.push(EOS_UI.renderMarkdown(bodyMd.slice(last), opts));
        return out.join('');
    },

    // Build the iframe / poster / placeholder for one embed marker.
    _vizEmbedHtml: function(embedId, entry, kv) {
        if (!embedId) return '<div class="eos-embed-missing">embed removed</div>';
        var mode = (entry && entry.mode) || kv.mode || 'snapshot';
        var shape = (entry && entry.shape) || kv.shape || '';
        var heavy = entry ? !!entry.heavy : false;
        var height = String((entry && entry.height) || 360);
        var src = (mode === 'reference')
            ? '/viz/api/html/' + encodeURIComponent((entry && entry.source_viz_id) || embedId)
            : '/viz/api/embed/' + encodeURIComponent(embedId);
        if (heavy) {
            return '<div class="eos-embed-poster" data-src="' + EOS_UI.escAttr(src) + '" '
                + 'data-height="' + EOS_UI.escAttr(height) + '" '
                + 'onclick="EOS_UI._loadEmbedPoster(this)">&#9654; Load ' + EOS_UI.esc(shape || 'visual') + '</div>';
        }
        return '<iframe class="eos-embed-frame" src="' + EOS_UI.escAttr(src) + '" loading="lazy" '
            + 'sandbox="allow-scripts" title="' + EOS_UI.escAttr(shape) + ' embed" '
            + 'style="width:100%;height:' + EOS_UI.escAttr(height) + 'px;border:0;border-radius:12px;'
            + 'display:block;overflow:hidden;"></iframe>';
    },

    _loadEmbedPoster: function(el) {
        var src = el.getAttribute('data-src');
        var h = el.getAttribute('data-height') || '360';
        var f = document.createElement('iframe');
        f.className = 'eos-embed-frame';
        f.src = src;
        f.loading = 'lazy';
        f.setAttribute('sandbox', 'allow-scripts');
        f.style.cssText = 'width:100%;height:' + h + 'px;border:0;border-radius:12px;display:block;overflow:hidden;';
        el.replaceWith(f);
    },

    // Render structured diff lines ({kind: add|del|ctx|hunk, text}) as HTML.
    // Shared by the viz-embed re-sync modal; reuses .pending-diff styling.
    renderDiffLines: function(lines) {
        if (!lines || !lines.length) return '<div class="eos-diff-empty">No changes.</div>';
        return '<div class="eos-diff">' + lines.map(function(l) {
            return '<div class="eos-diff-' + EOS_UI.escAttr(l.kind || 'ctx') + '">' + EOS_UI.esc(l.text || '') + '</div>';
        }).join('') + '</div>';
    },

    // --- Markdown renderer with Obsidian-flavored note links ---
    // opts.wikiLink(target, label) -> HTML string. Override to route wikilinks
    // somewhere other than EOS.viewNote (e.g. KB hash routes). Receives bare
    // target slug (no #anchor, no |alias) and the display label.
    renderMarkdown: function(text, opts) {
        opts = opts || {};
        // Step 1: Extract wikilinks + vault paths BEFORE esc (they contain [] which survive esc, but do it cleanly)
        var _links = [];
        var _ph = function(html) { var id = '\x00LINK' + _links.length + '\x00'; _links.push(html); return id; };

        // Obsidian-style embeds: ![[path/to/image.svg]] or ![[image.png|alt text]]
        // Must run BEFORE the wikilink rule, otherwise the inner [[..]] matches
        // and a stray `!` is left in the output.
        var IMG_EXT = /\.(svg|png|jpe?g|gif|webp|avif|bmp)$/i;
        var AV_EXT = /\.(mp4|webm|mov|mp3|wav|ogg)$/i;
        var PDF_EXT = /\.pdf$/i;
        text = text.replace(/!\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g, function(_, target, alt) {
            var t = target.trim();
            var url = '/api/vault/file?path=' + encodeURIComponent(t);
            var label = (alt || t.split('/').pop()).trim();
            if (IMG_EXT.test(t)) {
                return _ph('<img class="obs-embed-img" src="' + url + '" alt="' + EOS_UI.escAttr(label) + '" loading="lazy" />');
            }
            if (AV_EXT.test(t)) {
                var tag = /\.(mp3|wav|ogg)$/i.test(t) ? 'audio' : 'video';
                return _ph('<' + tag + ' class="obs-embed-av" src="' + url + '" controls></' + tag + '>');
            }
            if (PDF_EXT.test(t)) {
                return _ph('<a class="obs-embed-link" href="' + url + '" target="_blank" rel="noopener">📄 ' + EOS_UI.esc(label) + '</a>');
            }
            // Unknown extension — fall back to a link, never leave the raw token
            return _ph('<a class="obs-embed-link" href="' + url + '" target="_blank" rel="noopener">' + EOS_UI.esc(label) + '</a>');
        });

        // Standard markdown images: ![alt](url) and media: ![alt](audio.mp3) etc.
        // Resolves relative paths against the note's directory (from opts.notePath
        // or EOS_UI._notePath set by viewNote), and routes vault-relative paths
        // through /api/vault/file. Absolute URLs (http://, data:, //) pass through.
        var _notePath = (opts.notePath || EOS_UI._notePath || '').replace(/^\/+/, '');
        var _noteDir = _notePath.replace(/[^/]+$/, '');  // dir + trailing slash, or '' at vault root
        function _resolveMdRef(raw) {
            var u;
            try { u = decodeURIComponent(raw.trim()); } catch (e) { u = raw.trim(); }
            if (/^(?:https?:\/\/|data:|\/\/|mailto:|#)/i.test(u)) return { src: u, vault: false };
            var vaultPath;
            if (u.charAt(0) === '/') {
                vaultPath = u.replace(/^\/+/, '');
            } else {
                // Resolve against the note's directory
                var parts = (_noteDir + u).split('/');
                var stack = [];
                parts.forEach(function(p) {
                    if (p === '..') stack.pop();
                    else if (p && p !== '.') stack.push(p);
                });
                vaultPath = stack.join('/');
            }
            return { src: '/api/vault/file?path=' + encodeURIComponent(vaultPath), vault: true };
        }
        text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, function(_, alt, raw) {
            var resolved = _resolveMdRef(raw);
            var lower = raw.toLowerCase();
            if (IMG_EXT.test(lower)) {
                return _ph('<img class="obs-embed-img" src="' + resolved.src + '" alt="' + EOS_UI.escAttr(alt) + '" loading="lazy" />');
            }
            if (AV_EXT.test(lower)) {
                var tag = /\.(mp3|wav|ogg)$/i.test(lower) ? 'audio' : 'video';
                return _ph('<' + tag + ' class="obs-embed-av" src="' + resolved.src + '" controls></' + tag + '>');
            }
            if (PDF_EXT.test(lower)) {
                return _ph('<a class="obs-embed-link" href="' + resolved.src + '" target="_blank" rel="noopener">📄 ' + EOS_UI.esc(alt || raw) + '</a>');
            }
            // Non-media extension — leave as a plain markdown link (handled later) by re-emitting
            // the [alt](url) form without the leading ! so the escape step still shows something.
            return _ph('<a class="obs-embed-link" href="' + resolved.src + '" target="_blank" rel="noopener">' + EOS_UI.esc(alt || raw) + '</a>');
        });

        // Wikilinks: [[Note Name]] or [[Note Name|Display]] or [[Note#section]]
        text = text.replace(/\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|([^\]]+?))?\]\]/g, function(_, target, display) {
            var label = display || target;
            if (typeof opts.wikiLink === 'function') {
                return _ph(opts.wikiLink(target.trim(), label));
            }
            var path = target.replace(/\s/g, '-');
            if (!path.endsWith('.md')) path += '.md';
            return _ph('<a href="#" onclick="EOS.viewNote(\'' + EOS.escPath(path) + '\');return false" class="obs-link note-ref" title="' + EOS_UI.escAttr(target) + '">📎 ' + EOS_UI.esc(label) + '</a>');
        });

        // Full vault paths: /path/to/vault/.../Note.md
        var vp = (EOS.vaultPath || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        text = text.replace(new RegExp('(' + vp + '/[^\\s,)\\]]+\\.md)', 'g'), function(m) {
            var name = EOS.fileName(m).replace(/-/g, ' ');
            return _ph('<a href="#" onclick="EOS.viewNote(\'' + EOS.escPath(m) + '\');return false" class="obs-link note-ref" title="' + EOS_UI.escAttr(m) + '">📄 ' + EOS_UI.esc(name) + '</a>');
        });

        // Bare filenames: Something-Name.md
        // Lookbehind rejects /, word chars, AND hyphens — otherwise
        // "Inbox/test-sandbox.md" wrongly matched the inner "sandbox.md"
        // tail at the hyphen, mangling hyphen-bearing paths in chat.
        text = text.replace(/(?<![\/\w-])(\b[\w][\w-]+\.md)\b/g, function(m) {
            var name = m.replace('.md', '').replace(/-/g, ' ');
            return _ph('<a href="#" onclick="EOS.viewNote(\'' + EOS.escPath(m) + '\');return false" class="obs-link note-ref">' + EOS_UI.esc(name) + '</a>');
        });

        // Step 2: Separate frontmatter from body
        var frontmatter = '';
        var body = text;
        var fmMatch = text.match(/^---\n([\s\S]*?)\n---\n?/);
        if (fmMatch) {
            frontmatter = fmMatch[1];
            body = text.slice(fmMatch[0].length);
        }

        // Step 3: Extract code blocks before escaping (preserve content)
        var _codeBlocks = [];
        var _olBlocks = [];
        body = body.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
            var id = '\x00CODE' + _codeBlocks.length + '\x00';
            _codeBlocks.push('<pre class="obs-code-block"><code class="lang-' + (lang || 'text') + '">' + EOS_UI.esc(code.trimEnd()) + '</code></pre>');
            return id;
        });

        // Obsidian comments: paired %%...%% never renders (author-only notes).
        // Runs AFTER code extraction so %% inside a fence is preserved.
        body = body.replace(/%%[\s\S]*?%%/g, '');

        // Step 4: Escape HTML + markdown formatting
        var html = EOS_UI.esc(body)
            // Obsidian tags: #tag
            .replace(/(^|\s)#([\w\u4e00-\u9fff][\w\u4e00-\u9fff-]*)/g, '$1<span class="obs-tag">#$2</span>')
            // Headers (proper sizes)
            .replace(/^#{4} (.+)$/gm, '<h5>$1</h5>')
            .replace(/^#{3} (.+)$/gm, '<h4>$1</h4>')
            .replace(/^#{2} (.+)$/gm, '<h3>$1</h3>')
            .replace(/^#{1} (.+)$/gm, '<h2>$1</h2>')
            // Checkboxes (leading indent → visually nested)
            .replace(/^(\s*)- \[x\] (.+)$/gm, function(_, ind, t) { return '<li class="obs-done' + (ind ? ' obs-li-nested' : '') + '">✅ ' + t + '</li>'; })
            .replace(/^(\s*)- \[ \] (.+)$/gm, function(_, ind, t) { return '<li class="obs-todo' + (ind ? ' obs-li-nested' : '') + '">⬜ ' + t + '</li>'; })
            // Bold + italic. Bold spans may cross SINGLE newlines (vault prose is
            // often hard-wrapped mid-sentence) but never a blank line (paragraph
            // break) — `\n(?!\n)`. Italic stays single-line on purpose: a
            // multi-line `*` span would eat `* `-style bullet lists.
            .replace(/\*\*\*((?:.|\n(?!\n))+?)\*\*\*/g, '<strong><em>$1</em></strong>')
            .replace(/\*\*((?:.|\n(?!\n))+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            // Strikethrough
            .replace(/~~(.+?)~~/g, '<del>$1</del>')
            // Highlight: ==text== → <mark>
            .replace(/==([^=\n](?:[^=\n]|=(?!=))*?)==/g, '<mark class="obs-mark">$1</mark>')
            // Inline code
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            // Blockquote (including callouts)
            .replace(/^&gt; \[!(\w+)\](.*)$/gm, '<blockquote class="obs-callout obs-callout-$1"><strong>$1</strong>$2</blockquote>')
            .replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>')
            // Tables
            .replace(/((?:^\|.+\|$\n?)+)/gm, function(block) {
                var rows = block.trim().split('\n').filter(function(r) { return r.trim(); });
                if (rows.length < 2) return block;
                // Alignment row: |---|---| or |:---:|---| etc. The old
                // /^\|[\s:-]+\|$/ form never matched multi-column separators
                // (inner | not in the class), so every table rendered its
                // header as a data row with a visible "---" row under it.
                var isAlignRow = /^\|(?:\s*:?-{2,}:?\s*\|)+$/.test(rows[1].trim());
                var startIdx = isAlignRow ? 2 : 0;
                var headerRow = isAlignRow ? rows[0] : null;
                var thead = '';
                if (headerRow) {
                    var hCells = headerRow.split('|').slice(1, -1);
                    thead = '<thead><tr>' + hCells.map(function(c) { return '<th>' + c.trim() + '</th>'; }).join('') + '</tr></thead>';
                }
                var tbody = rows.slice(startIdx).map(function(r) {
                    var cells = r.split('|').slice(1, -1);
                    return '<tr>' + cells.map(function(c) { return '<td>' + c.trim() + '</td>'; }).join('') + '</tr>';
                }).join('');
                return '<table class="obs-table">' + thead + '<tbody>' + tbody + '</tbody></table>';
            })
            // Unordered lists — indented bullets get a nested class, then top-level
            .replace(/^\s{2,}[-*] (.+)$/gm, '<li class="obs-li-nested">$1</li>')
            .replace(/^[-*] (.+)$/gm, '<li>$1</li>')
            // Numbered lists — placeholder tag so ordered runs become a real <ol>
            // (previously collapsed into <ul> and lost their numbering)
            .replace(/^\d+\. (.+)$/gm, '<oli>$1</oli>')
            .replace(/((?:<oli>.*<\/oli>\n?)+)/g, function(b) {
                var h = '<ol>' + b.replace(/<(\/?)oli>/g, '<$1li>').replace(/\n/g, '') + '</ol>';
                var id = '\x00OL' + _olBlocks.length + '\x00';
                _olBlocks.push(h);
                return id;
            })
            // Horizontal rule
            .replace(/^---$/gm, '<hr>')
            // Wrap consecutive <li> in <ul>
            .replace(/((?:<li[^>]*>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
            // Merge consecutive blockquotes
            .replace(/<\/blockquote>\n?<blockquote>/g, '<br>')
            // Paragraphs
            .replace(/\n{2,}/g, '</p><p>')
            .replace(/([^>])\n([^<])/g, '$1<br>$2')
            .replace(/^(.+)/, '<p>$1</p>')
            .replace(/<p>\s*<\/p>/g, '');

        // Step 5: Restore code blocks + ordered lists
        _codeBlocks.forEach(function(block, i) {
            html = html.replace('\x00CODE' + i + '\x00', block);
        });
        _olBlocks.forEach(function(block, i) {
            html = html.replace('\x00OL' + i + '\x00', block);
        });

        // Step 6: Add frontmatter display. Handles both inline values
        // (`key: value`, `tags: [a, b]`) and block-style lists — the vault's
        // mandated tag style (`tags:\n  - a`) — which the old line-by-line
        // parser silently skipped.
        if (frontmatter) {
            var fmHtml = '<div class="obs-frontmatter"><div class="obs-fm-label">Properties</div>';
            var fmLines = frontmatter.split('\n');
            var fmRows = [];
            for (var fi = 0; fi < fmLines.length; fi++) {
                var fm2 = fmLines[fi].match(/^(\w[\w-]*)\s*:\s*(.*)$/);
                if (!fm2) continue;
                var fmKey = fm2[1];
                var fmVal = (fm2[2] || '').trim();
                if (!fmVal) {
                    // Block-style list: collect the indented "- item" lines below
                    var items = [];
                    while (fi + 1 < fmLines.length) {
                        var im = fmLines[fi + 1].match(/^\s+-\s+(.+)$/);
                        if (!im) break;
                        items.push(im[1].trim());
                        fi++;
                    }
                    if (!items.length) continue;  // empty key: — nothing to show
                    fmVal = items.join(', ');
                }
                fmRows.push([fmKey, fmVal]);
            }
            fmRows.forEach(function(kv) {
                var val = kv[1];
                // Render tags as pills
                if (kv[0] === 'tags') {
                    var tags = val.replace(/[\[\]"']/g, '').split(',').map(function(t) { return t.trim(); }).filter(Boolean);
                    val = tags.map(function(t) { return '<span class="obs-tag">#' + EOS_UI.esc(t) + '</span>'; }).join(' ');
                } else {
                    val = EOS_UI.esc(val);
                }
                fmHtml += '<div class="obs-fm-row"><span class="obs-fm-key">' + EOS_UI.esc(kv[0]) + '</span><span class="obs-fm-val">' + val + '</span></div>';
            });
            fmHtml += '</div>';
            html = fmHtml + html;
        }

        // Step 3: Restore link placeholders
        _links.forEach(function(link, i) {
            html = html.replace('\x00LINK' + i + '\x00', link);
        });

        return html;
    },

    // --- Note Viewer / Editor ---
    // Universal vault note viewer+editor that can be called from any app page.
    // Usage: EOS.viewNote('30_Resources/Books/My-Book.md')
    //        EOS.editNote('20_Areas/Health/mood-log.md')

    _noteOverlay: null,

    _ensureNoteUI: function() {
        if (EOS_UI._noteOverlay) return;
        var overlay = document.createElement('div');
        overlay.id = 'eos-note-overlay';
        overlay.className = 'eos-note-overlay';
        overlay.innerHTML =
            '<div class="eos-note-panel">' +
                '<div class="eos-note-header">' +
                    '<div class="eos-note-title" id="eos-note-title"></div>' +
                    '<div class="eos-note-actions">' +
                        '<button class="eos-note-btn" id="eos-note-viewer" title="Open in viewer" onclick="EOS_UI._openNoteInViewer()">Open ↗</button>' +
                        '<button class="eos-note-btn eos-note-btn-edit" id="eos-note-edit-btn" title="Edit this note inline" onclick="EOS_UI._toggleEdit()">Edit</button>' +
                        '<button class="eos-note-btn eos-note-btn-close" title="Close note panel" onclick="EOS_UI.closeNote()">×</button>' +
                    '</div>' +
                '</div>' +
                '<div class="eos-note-backlinks" id="eos-note-backlinks" style="display:none"></div>' +
                '<div class="eos-note-body" id="eos-note-body">' +
                    '<pre class="eos-note-content" id="eos-note-view"></pre>' +
                    '<textarea class="eos-note-editor" id="eos-note-editor" style="display:none"></textarea>' +
                '</div>' +
                '<div class="eos-note-footer" id="eos-note-footer" style="display:none">' +
                    '<button class="eos-btn eos-btn-sm" title="Save changes to this note" onclick="EOS_UI._saveNote()">Save</button>' +
                    '<button class="eos-btn eos-btn-sm eos-btn-ghost" title="Discard edits and keep the original" onclick="EOS_UI._cancelEdit()">Cancel</button>' +
                    '<span class="eos-note-status" id="eos-note-status"></span>' +
                '</div>' +
            '</div>';
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) EOS_UI.closeNote();
        });
        document.body.appendChild(overlay);
        EOS_UI._noteOverlay = overlay;
    },

    _notePath: '',
    _noteEditing: false,
    _noteOriginal: '',

    viewNote: function(path) {
        EOS_UI._ensureNoteUI();
        path = EOS.normPath(path);
        EOS_UI._notePath = path;
        EOS_UI._noteEditing = false;
        var overlay = document.getElementById('eos-note-overlay');
        var titleEl = document.getElementById('eos-note-title');
        var viewEl = document.getElementById('eos-note-view');
        var editorEl = document.getElementById('eos-note-editor');
        var footerEl = document.getElementById('eos-note-footer');
        var editBtn = document.getElementById('eos-note-edit-btn');

        // Show loading
        var noteTitle = EOS.fileName(path).replace(/-/g, ' ');
        titleEl.textContent = noteTitle;
        viewEl.textContent = 'Loading...';
        editorEl.style.display = 'none';
        viewEl.style.display = 'block';
        footerEl.style.display = 'none';
        editBtn.textContent = 'Edit';
        overlay.classList.add('open');
        document.body.style.overflow = 'hidden';

        // Live backlinks chip strip — render from /link/api/backlinks lookup
        var backlinksEl = document.getElementById('eos-note-backlinks');
        if (backlinksEl) {
            EOS_UI.backlinkBadges(backlinksEl, { target: EOS.fileName(path).replace(/\.md$/, '') });
        }

        // Fetch content
        fetch(EOS.base + '/api/vault/read?path=' + encodeURIComponent(path))
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.error) {
                    viewEl.textContent = 'Error: ' + d.error;
                    return;
                }
                EOS_UI._noteOriginal = d.content;
                // Render viz-artifact embeds when the note carries any; otherwise
                // the plain markdown path (byte-identical for notes without embeds).
                viewEl.innerHTML = (d.viz_embeds && d.viz_embeds.length)
                    ? EOS_UI.renderMarkdownWithEmbeds(d.content, d.viz_embeds)
                    : EOS_UI.renderMarkdown(d.content);
                viewEl.style.whiteSpace = 'normal';
            })
            .catch(function(e) {
                viewEl.textContent = 'Failed to load: ' + e.message;
            });
    },

    editNote: function(path) {
        EOS_UI.viewNote(path);
        setTimeout(function() { EOS_UI._toggleEdit(); }, 300);
    },

    _editorKeydown: function(e) {
        var ta = e.target;
        // Ctrl+S / Cmd+S — save
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            EOS_UI._saveNote();
            return;
        }
        // Tab — indent
        if (e.key === 'Tab') {
            e.preventDefault();
            var start = ta.selectionStart, end = ta.selectionEnd;
            if (e.shiftKey) {
                // Unindent: remove leading tab/spaces from selected lines
                var before = ta.value.substring(0, start);
                var sel = ta.value.substring(start, end);
                var after = ta.value.substring(end);
                var lineStart = before.lastIndexOf('\n') + 1;
                var block = ta.value.substring(lineStart, end);
                var unindented = block.replace(/^(\t|    )/gm, '');
                var diff = block.length - unindented.length;
                ta.value = ta.value.substring(0, lineStart) + unindented + after;
                ta.selectionStart = Math.max(lineStart, start - (diff > 0 ? Math.min(diff, 4) : 0));
                ta.selectionEnd = end - diff;
            } else if (start === end) {
                ta.value = ta.value.substring(0, start) + '    ' + ta.value.substring(end);
                ta.selectionStart = ta.selectionEnd = start + 4;
            } else {
                // Indent selected lines
                var before2 = ta.value.substring(0, start);
                var lineStart2 = before2.lastIndexOf('\n') + 1;
                var block2 = ta.value.substring(lineStart2, end);
                var indented = block2.replace(/^/gm, '    ');
                var diff2 = indented.length - block2.length;
                ta.value = ta.value.substring(0, lineStart2) + indented + ta.value.substring(end);
                ta.selectionStart = start + 4;
                ta.selectionEnd = end + diff2;
            }
            return;
        }
        // Ctrl+B — bold
        if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
            e.preventDefault();
            EOS_UI._wrapSelection(ta, '**', '**');
            return;
        }
        // Ctrl+I — italic
        if ((e.ctrlKey || e.metaKey) && e.key === 'i') {
            e.preventDefault();
            EOS_UI._wrapSelection(ta, '*', '*');
            return;
        }
        // Ctrl+K — link
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            EOS_UI._wrapSelection(ta, '[[', ']]');
            return;
        }
    },

    _wrapSelection: function(ta, before, after) {
        var start = ta.selectionStart, end = ta.selectionEnd;
        var sel = ta.value.substring(start, end);
        ta.value = ta.value.substring(0, start) + before + sel + after + ta.value.substring(end);
        ta.selectionStart = start + before.length;
        ta.selectionEnd = end + before.length;
        ta.focus();
    },

    closeNote: function() {
        var overlay = document.getElementById('eos-note-overlay');
        if (overlay) {
            overlay.classList.remove('open');
            document.body.style.overflow = '';
        }
    },

    _toggleEdit: function() {
        var viewEl = document.getElementById('eos-note-view');
        var editorEl = document.getElementById('eos-note-editor');
        var footerEl = document.getElementById('eos-note-footer');
        var editBtn = document.getElementById('eos-note-edit-btn');

        EOS_UI._noteEditing = !EOS_UI._noteEditing;
        if (EOS_UI._noteEditing) {
            editorEl.value = EOS_UI._noteOriginal;
            viewEl.style.display = 'none';
            editorEl.style.display = 'block';
            footerEl.style.display = 'flex';
            editBtn.textContent = 'View';
            editorEl.onkeydown = EOS_UI._editorKeydown;
            EOS_UI.wikiLinkInput(editorEl);
            editorEl.focus();
        } else {
            EOS_UI._noteOriginal = editorEl.value;
            viewEl.innerHTML = EOS_UI.renderMarkdown(EOS_UI._noteOriginal);
            viewEl.style.whiteSpace = 'normal';
            viewEl.style.display = 'block';
            editorEl.style.display = 'none';
            footerEl.style.display = 'none';
            editorEl.onkeydown = null;
            editBtn.textContent = 'Edit';
        }
    },

    _cancelEdit: function() {
        var editorEl = document.getElementById('eos-note-editor');
        editorEl.value = EOS_UI._noteOriginal;
        EOS_UI._noteEditing = true;
        EOS_UI._toggleEdit();
    },

    _saveNote: function() {
        var editorEl = document.getElementById('eos-note-editor');
        var statusEl = document.getElementById('eos-note-status');
        var content = editorEl.value;
        statusEl.textContent = 'Saving...';

        fetch(EOS.base + '/api/vault/write', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: EOS_UI._notePath, content: content}),
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.ok) {
                EOS_UI._noteOriginal = content;
                statusEl.textContent = 'Saved!';
                EOS_UI.toast('Note saved', true);
                setTimeout(function() { statusEl.textContent = ''; }, 2000);
            } else {
                statusEl.textContent = 'Error: ' + (d.error || 'unknown');
                EOS_UI.toast('Save failed', false);
            }
        })
        .catch(function(e) {
            statusEl.textContent = 'Failed: ' + e.message;
            EOS_UI.toast('Save failed', false);
        });
    },

    _openNoteInViewer: function() {
        if (EOS_UI._notePath) {
            EOS.openInViewer(EOS_UI._notePath);
        }
    },

    // --- Page Header ---
    // Render the canonical app-page header: title + optional subtitle + optional
    // tab strip + optional right-side actions. Replaces hand-rolled .header /
    // .app-header / .jh patterns across apps.
    //
    // opts = {
    //   title: string,                              // required
    //   subtitle?: string,
    //   tabs?: [{id, label, active?, href?}],       // optional pill-mode bar; href => <a> (cross-page nav)
    //   onTabChange?: function(id),                 // optional handler for in-page tabs (ignored when href set)
    //   actions?: string,                           // HTML for right-side buttons (use .eos-btn*)
    //   mount?: string | HTMLElement                // if given, writes into element; else returns HTML
    // }
    //
    // Returns the HTML string (always). When mount is provided, also writes it
    // and wires tab clicks (in-page tabs only — href tabs use native navigation).
    pageHeader: function(opts) {
        opts = opts || {};
        var titleHtml = '<div class="eos-ph-titles">'
            + '<h1 class="eos-ph-title">' + EOS_UI.esc(opts.title || '') + '</h1>'
            + (opts.subtitle ? '<div class="eos-ph-sub">' + EOS_UI.esc(opts.subtitle) + '</div>' : '')
            + '</div>';
        var tabsHtml = '';
        if (opts.tabs && opts.tabs.length) {
            tabsHtml = '<div class="eos-ph-tabs" role="tablist">'
                + opts.tabs.map(function(t) {
                    var cls = 'eos-ph-tab' + (t.active ? ' active' : '');
                    if (t.href) {
                        return '<a class="' + cls + '" href="' + EOS_UI.escAttr(t.href) + '"'
                            + ' role="tab" aria-selected="' + (t.active ? 'true' : 'false') + '"'
                            + '>' + EOS_UI.esc(t.label) + '</a>';
                    }
                    return '<button class="' + cls + '"'
                        + ' data-ph-tab="' + EOS_UI.escAttr(t.id) + '"'
                        + ' role="tab" aria-selected="' + (t.active ? 'true' : 'false') + '"'
                        + '>' + EOS_UI.esc(t.label) + '</button>';
                }).join('')
                + '</div>';
        }
        var actionsHtml = opts.actions ? '<div class="eos-ph-actions">' + opts.actions + '</div>' : '';
        // Order: titles | tabs | actions. Tabs+actions both right-aligned via flex.
        var html = '<header class="eos-page-header">' + titleHtml + tabsHtml + actionsHtml + '</header>';
        if (opts.mount) {
            var el = (typeof opts.mount === 'string') ? document.getElementById(opts.mount) : opts.mount;
            if (el) {
                el.outerHTML = html;
                // After outerHTML replacement, the original element is gone — re-find by class.
                if (opts.onTabChange && opts.tabs) {
                    var tabs = document.querySelectorAll('.eos-page-header .eos-ph-tab');
                    tabs.forEach(function(btn) {
                        btn.addEventListener('click', function() {
                            tabs.forEach(function(b) {
                                b.classList.toggle('active', b === btn);
                                b.setAttribute('aria-selected', b === btn ? 'true' : 'false');
                            });
                            opts.onTabChange(btn.getAttribute('data-ph-tab'));
                        });
                    });
                }
            }
        }
        return html;
    },

    // --- Stat Cards ---
    // Render a row of stat cards into a target element.
    // items: [{value, label, color?}] or {label: value, ...}
    statCards: function(targetId, items) {
        var el = document.getElementById(targetId);
        if (!el) return;
        // Normalize: accept either array or object
        if (!Array.isArray(items)) {
            items = Object.entries(items).map(function(e) { return {label: e[0], value: e[1]}; });
        }
        el.innerHTML = items.map(function(s, i) {
            // Prefer semantic variant; fall back to legacy inline color.
            var variantCls = s.variant ? ' eos-stat-card--' + s.variant : '';
            var colorAttr = (!s.variant && s.color) ? ' style="color:' + s.color + '"' : '';
            // Optional onClick callback: card becomes a door without inline JS.
            var clickable = typeof s.onClick === 'function';
            var clickCls = clickable ? ' eos-stat-card--click' : '';
            var clickAttr = clickable ? ' data-stat-index="' + i + '" role="button" tabindex="0"' : '';
            return '<div class="eos-stat-card' + variantCls + clickCls + '" style="animation-delay:' + (i * 0.05) + 's"' + clickAttr + '>' +
                '<div class="eos-stat-val"' + colorAttr + '>' + EOS_UI.esc(String(s.value)) + '</div>' +
                '<div class="eos-stat-lbl">' + EOS_UI.esc(s.label) + '</div>' +
                '</div>';
        }).join('');
        items.forEach(function(s, i) {
            if (typeof s.onClick !== 'function') return;
            var card = el.querySelector('[data-stat-index="' + i + '"]');
            if (!card) return;
            card.addEventListener('click', s.onClick);
            card.addEventListener('keydown', function(event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    card.click();
                }
            });
        });
    },

    // --- Entity Card ---
    // Build a list-item card for entities (projects, posts, contacts, etc.).
    // Returns an HTML string. Compose in lists: items.map(EOS_UI.entityCard).join('')
    // opts: {
    //   title: string,
    //   subtitle?: string,
    //   badges?: [{label, variant}],        // variant = "status-active", "priority-high", "age-fresh", ...
    //   meta?: string | HTML,                // bottom row (date, counts, small notes)
    //   body?: string,                       // optional body text (small paragraph between head and meta)
    //   actions?: string,                    // HTML for action buttons (rendered in eec-actions)
    //   onClick?: string,                    // onclick JS expression; wraps card as clickable
    //                                        // JSON.stringify() every value you
    //                                        // interpolate: the sink escapes the
    //                                        // attribute, not your JS string.
    //   id?: string,                         // optional DOM id
    //   className?: string,                  // extra classes on the card root
    // }
    // --- Status -> shared badge variant.
    // Four apps (forge, replay, projects, reports) each mapped their own status
    // vocabulary onto `.eos-badge-status-*`, and the naive form
    // `'eos-badge-status-' + status` emits a class with no styling whenever the
    // status is outside the shared set — a colourless badge, silent in review.
    //
    // statusVariant(status, map?) -> a variant safe to concatenate after
    // 'eos-badge-'. `map` translates an app's vocabulary to shared names
    // (bare, e.g. {'build-failed': 'blocked'}); without it a status that is
    // already a shared name passes through. Anything unrecognised -> 'neutral'.
    STATUS_VARIANTS: ['active', 'archived', 'blocked', 'completed', 'draft',
                      'fail', 'idea', 'pass', 'published', 'running', 'shelved'],
    statusVariant: function(status, map) {
        var s = (status == null ? '' : String(status)).trim();
        if (!s) return 'neutral';
        if (map && Object.prototype.hasOwnProperty.call(map, s)) s = String(map[s] || '');
        return EOS_UI.STATUS_VARIANTS.indexOf(s) >= 0 ? 'status-' + s : 'neutral';
    },

    entityCard: function(opts) {
        var esc = EOS_UI.esc;
        var parts = [];
        var headRight = '';
        if (opts.badges && opts.badges.length) {
            headRight = '<div class="eec-badges right">' + opts.badges.map(function(b) {
                return '<span class="eos-badge eos-badge-' + escAttr(b.variant || 'neutral') + '">' + esc(b.label) + '</span>';
            }).join('') + '</div>';
        }
        parts.push('<div class="eec-head">');
        parts.push('<div style="flex:1;min-width:0">');
        parts.push('<div class="eec-title">' + esc(opts.title || '') + '</div>');
        if (opts.subtitle) parts.push('<div class="eec-sub">' + esc(opts.subtitle) + '</div>');
        parts.push('</div>');
        if (headRight) parts.push(headRight);
        parts.push('</div>');
        if (opts.body) parts.push('<div class="eec-body">' + opts.body + '</div>');
        if (opts.meta) parts.push('<div class="eec-meta">' + opts.meta + '</div>');
        if (opts.actions) parts.push('<div class="eec-actions">' + opts.actions + '</div>');

        var cls = 'eos-entity-card' + (opts.className ? ' ' + opts.className : '');
        if (!opts.onClick) cls += ' no-hover';
        var attrs = '';
        if (opts.id) attrs += ' id="' + escAttr(opts.id) + '"';
        // NOT the same contract as statCards, which now takes a function and
        // emits no inline handler. Here onClick is still a JS expression string:
        // escAttr makes the ATTRIBUTE safe, so callers must JSON.stringify
        // anything they interpolate into the expression itself.
        if (opts.onClick) attrs += ' onclick="' + EOS_UI.escAttr(opts.onClick) + '" role="button" tabindex="0"';
        return '<div class="' + cls + '"' + attrs + '>' + parts.join('') + '</div>';
    },

    // --- Microphone recorder — mount a record button, get a Blob back.
    //
    // Extraction of duplication that already existed: shadowing, braindump,
    // speaking, dictation, vlog and writing-editor each hand-roll the same
    // getUserMedia + MediaRecorder + blob block. `EOS_DICTATE` is NOT reusable
    // for this — it is a global overlay hardwired to POST to
    // /dictation/api/transcribe and insert at a text caret.
    //
    // Two things copied deliberately from the better of those implementations,
    // and one bug deliberately not copied:
    //   • opus negotiation (shadowing) — Chrome defaults to a larger codec.
    //   • releasing the mic tracks in onstop (braindump). shadowing does NOT,
    //     so its tab keeps the recording indicator lit after a take. Bug; fixed
    //     here so no future consumer inherits it.
    //   • a size floor, because a stray click yields a ~200-byte blob that STT
    //     happily turns into a confident hallucination.
    //
    // opts: {mount, onBlob(blob, meta), onStart?, onTick?(ms), onError?(msg),
    //        maxMs? (default 120000), minBytes? (default 1200),
    //        label? (default 'Record'), stopLabel? (default 'Stop')}
    // -> {start, stop, isRecording, el} | null
    recorder: function(opts) {
        opts = opts || {};
        var el = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
        if (!el) { console.warn('EOS_UI.recorder: mount not found'); return null; }

        var maxMs = opts.maxMs || 120000;
        var minBytes = opts.minBytes || 1200;
        var label = opts.label || 'Record';
        var stopLabel = opts.stopLabel || 'Stop';

        var rec = null, chunks = [], stream = null, t0 = 0, tick = null, capMs = 0;

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'eos-rec-btn';
        btn.setAttribute('aria-label', label);
        var timer = document.createElement('span');
        timer.className = 'eos-rec-timer';
        el.appendChild(btn);
        el.appendChild(timer);

        function paint(on) {
            btn.classList.toggle('recording', !!on);
            btn.innerHTML = (on ? '⏹ ' : '🎙 ') + EOS_UI.esc(on ? stopLabel : label);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            if (!on) timer.textContent = '';
        }

        function cleanup() {
            if (tick) { clearInterval(tick); tick = null; }
            // Release the mic so the browser's recording indicator goes out.
            if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
            paint(false);
        }

        function fail(msg) {
            cleanup();
            if (opts.onError) opts.onError(msg);
            else if (EOS_UI.toast) EOS_UI.toast(msg);
        }

        async function start() {
            if (rec) return;
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
                fail('This browser has no microphone recording support.');
                return;
            }
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true },
                });
            } catch (e) {
                fail('Microphone unavailable — check the browser permission.');
                return;
            }
            var mrOpts = {};
            try {
                if (window.MediaRecorder.isTypeSupported &&
                    MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                    mrOpts = { mimeType: 'audio/webm;codecs=opus' };
                }
            } catch (e) { /* fall through to browser default */ }

            try { rec = new MediaRecorder(stream, mrOpts); }
            catch (e) { fail('Could not start the recorder.'); return; }

            chunks = [];
            rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
            rec.onstop = function () {
                var type = (chunks[0] && chunks[0].type) || 'audio/webm';
                var blob = new Blob(chunks, { type: type });
                var ms = capMs || (Date.now() - t0);
                cleanup();
                rec = null;
                if (blob.size < minBytes) {
                    if (opts.onError) opts.onError('That was too short — try again.');
                    else if (EOS_UI.toast) EOS_UI.toast('That was too short — try again.');
                    return;
                }
                // durationMs is a MEASURED wall-clock duration. Downstream
                // words-per-minute is only meaningful when it comes from here
                // rather than from a number the user typed.
                if (opts.onBlob) opts.onBlob(blob, { durationMs: ms, mimeType: type, size: blob.size });
            };

            t0 = Date.now();
            capMs = 0;
            rec.start();
            paint(true);
            tick = setInterval(function () {
                var ms = Date.now() - t0;
                var s = Math.floor(ms / 1000);
                timer.textContent = Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2);
                if (opts.onTick) opts.onTick(ms);
                if (ms >= maxMs) { capMs = maxMs; stop(); }
            }, 250);
            if (opts.onStart) opts.onStart();
        }

        function stop() {
            if (!rec) return;
            try { rec.stop(); } catch (e) { cleanup(); rec = null; }
        }

        btn.addEventListener('click', function () { rec ? stop() : start(); });
        paint(false);
        return { start: start, stop: stop, isRecording: function () { return !!rec; }, el: el };
    },

    // --- Provenance chip — required on AI-authored content.
    // opts: {mode: 'local'|'cloud'|'user', model?: string, provider?: string,
    //        cost?: number, title?: string (tooltip)}
    // Returns an HTML string; caller inserts it into an AI card's top-right.
    provenance: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var mode = opts.mode || 'user';
        var icon = mode === 'local' ? '🔒' : mode === 'cloud' ? '☁' : '👤';
        var parts = ['<span class="eos-badge-provenance-icon">' + icon + '</span>'];
        parts.push('<span>' + esc(mode) + '</span>');
        if (opts.provider) parts.push('<span>·</span><span>' + esc(opts.provider) + '</span>');
        if (opts.model) parts.push('<span>·</span><span>' + esc(opts.model) + '</span>');
        if (opts.cost != null && mode === 'cloud') {
            var c = typeof opts.cost === 'number' ? '~$' + opts.cost.toFixed(Math.max(3, Math.ceil(-Math.log10(opts.cost || 0.001)))) : opts.cost;
            parts.push('<span class="eos-badge-provenance-cost">· ' + esc(c) + '</span>');
        }
        var title = opts.title ? ' title="' + escAttr(opts.title) + '"' : '';
        return '<span class="eos-badge-provenance eos-badge-provenance-' + escAttr(mode) + '"' + title + '>' + parts.join('') + '</span>';
    },

    // --- Pin to home "Pinned references" (backed by the optional quickref app) ---
    // Any app that shows an answer/result worth keeping can offer one-click pin:
    //   EOS_UI.pinButton({mount:'#bar', title:q, body:answer, source:'search-ai'});
    // Feature-detects quickref ONCE (cached); if it isn't installed, renders
    // nothing — so public/core apps degrade gracefully where quickref is absent.
    // First consumers: search (AI Ask), assistant (chat answers).
    _quickrefAvail: null,  // null = unprobed; true/false once resolved
    quickrefAvailable: async function() {
        if (EOS_UI._quickrefAvail !== null) return EOS_UI._quickrefAvail;
        try {
            // EOS.api throws on non-200 (404 when quickref isn't installed) and
            // shares the POST's base + auth path — keep both on the one helper.
            await EOS.api('/quickref/api/cards');
            EOS_UI._quickrefAvail = true;
        } catch (e) { EOS_UI._quickrefAvail = false; }
        return EOS_UI._quickrefAvail;
    },
    // opts: {mount, title, body?, fields?, tags?, source?, label?, onPinned?}
    // Returns the button element (appended to mount), or null when nothing was
    // rendered (no mount, quickref absent, or nothing to pin).
    pinButton: async function(opts) {
        opts = opts || {};
        var mount = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
        if (!mount) return null;
        var title = (opts.title || '').trim();
        var body = (opts.body || '').trim();
        var fields = opts.fields || [];
        if (!title || (!body && !fields.length)) return null;
        if (!(await EOS_UI.quickrefAvailable())) return null;  // quickref not installed
        // label '' → icon-only (chat action rows); undefined → 'Pin' (toolbars).
        var label = opts.label === undefined ? 'Pin' : opts.label;
        var face = function(word) { return word ? '📌 ' + EOS_UI.esc(word) : '📌'; };
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'eos-pin-btn' + (label ? '' : ' eos-pin-btn-icon');
        btn.title = 'Pin to home';
        btn.innerHTML = face(label);
        btn.onclick = async function() {
            btn.disabled = true; btn.innerHTML = face(label ? 'Pinning…' : '');
            try {
                var res = await EOS.api('/quickref/api/cards', { method: 'POST', body: JSON.stringify({
                    title: title, note: body, fields: fields,
                    tags: opts.tags || ['pinned'], pinned: true, source: opts.source || ''
                }) });
                if (res && res.id) {
                    btn.innerHTML = face(label ? 'Pinned' : '✓'); btn.classList.add('pinned');
                    EOS_UI.toast('Pinned to home — Quick Ref');
                    if (opts.onPinned) opts.onPinned(res);
                } else { btn.innerHTML = face(label ? 'Pin failed' : '✕'); btn.disabled = false; }
            } catch (e) { btn.innerHTML = face(label ? 'Pin failed' : '✕'); btn.disabled = false; }
        };
        mount.appendChild(btn);
        return btn;
    },

    // --- Cost formatting (assistant turn footers, session badges) ---
    // Server is the single source of truth for cost. Missing cost → '$0';
    // never fabricate. Tiny costs collapse to '<$0.0001' so the column
    // doesn't show '$0.0000' which reads as zero-cost.
    fmtCost: function(c) {
        if (!c || c <= 0) return '$0';
        if (c < 0.0001) return '<$0.0001';
        return '$' + c.toFixed(4);
    },

    // --- Turn footer (per-assistant-turn metadata strip) ---
    // Returns a detached <div class="turn-footer"> with the parts:
    // · {elapsed}s · {tokens} · {cache%}% cache · {N tool(s)} · {$cost} · {extras...}
    // The caller decides where to mount it (append inside the assistant
    // bubble, insertBefore the next sibling, etc.) and accumulates any
    // session-level aggregates (cost badges, totals). This helper only
    // formats and renders.
    //
    // opts: {
    //   usage: {prompt_tokens?, completion_tokens?, input_tokens?, output_tokens?,
    //           cached_tokens?, cache_read_input_tokens?, cost?},
    //   turnStart: ms epoch | 0,
    //   turnTools: int,        // tool_call count for this turn
    //   extraParts: [string],  // optional trailing tags ('plan mode', etc.)
    // }
    turnFooter: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var usage = opts.usage || {};
        var pt = parseInt(usage.prompt_tokens || usage.input_tokens || 0, 10) || 0;
        var ct = parseInt(usage.completion_tokens || usage.output_tokens || 0, 10) || 0;
        var cached = parseInt(usage.cached_tokens || usage.cache_read_input_tokens || 0, 10) || 0;
        var elapsed = opts.turnStart ? (Date.now() - opts.turnStart) / 1000 : 0;
        var cost = parseFloat(usage.cost);
        if (!isFinite(cost) || cost < 0) cost = 0;
        var tools = parseInt(opts.turnTools || 0, 10) || 0;

        var parts = [elapsed.toFixed(1) + 's'];
        var total = pt + ct;
        if (total > 0) parts.push(total.toLocaleString() + ' tokens');
        if (cached > 0 && pt > 0) parts.push(Math.round(100 * cached / pt) + '% cache');
        if (tools > 0) parts.push(tools + ' tool' + (tools === 1 ? '' : 's'));
        if (cost > 0) parts.push(EOS_UI.fmtCost(cost));
        if (Array.isArray(opts.extraParts)) {
            opts.extraParts.forEach(function(p) { if (p) parts.push(String(p)); });
        }

        var footer = document.createElement('div');
        footer.className = 'turn-footer';
        footer.innerHTML = '· ' + parts.map(esc).join('<span class="sep">·</span>');
        return footer;
    },

    // --- Empty state ---
    // opts: {message, icon?, actionLabel?, onAction? (JS expression string)}
    emptyState: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var parts = [];
        if (opts.icon) parts.push('<span class="eos-empty-state-icon">' + opts.icon + '</span>');
        parts.push('<p class="eos-empty-state-message">' + esc(opts.message || 'Nothing here yet.') + '</p>');
        if (opts.actionLabel && opts.onAction) {
            parts.push('<div class="eos-empty-state-action"><button class="eos-btn eos-btn-sm" title="' + EOS_UI.escAttr(opts.actionTip || opts.actionLabel) + '" onclick="' + opts.onAction.replace(/"/g, '&quot;') + '">' + esc(opts.actionLabel) + '</button></div>');
        }
        return '<div class="eos-empty-state">' + parts.join('') + '</div>';
    },

    // --- Error state ---
    // opts: {message, onRetry? (JS expression string)}
    errorState: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var parts = ['<span class="eos-error-state-icon">⚠</span>'];
        parts.push('<p class="eos-error-state-message">' + esc(opts.message || 'Something went wrong.') + '</p>');
        if (opts.onRetry) {
            parts.push('<div class="eos-error-state-action"><button class="eos-btn eos-btn-sm eos-btn-ghost" title="Try loading again" onclick="' + opts.onRetry.replace(/"/g, '&quot;') + '">Retry</button></div>');
        }
        return '<div class="eos-error-state">' + parts.join('') + '</div>';
    },

    // --- Entity list — fetch + render + delete + state lifecycle ---
    // The CRUD-list workhorse. Apps that want a generic list of items rendered
    // as eos-entity-cards drop one call into their page:
    //
    //   var list = EOS_UI.entityList({
    //       url:        '/myapp/api/items',
    //       mountId:    'item-list',
    //       deleteUrl:  '/myapp/api/items',     // {id} appended; omit to disable delete
    //       onClick:    function(id, item) { ... },
    //       renderRow:  function(item) { ... }, // optional override; default uses entityCard
    //       titleField: 'title',
    //       metaFields: ['updated', 'created'],
    //       emptyMessage: 'No items yet.',
    //       emptyAction:  {label: '+ Add', onAction: 'openAdd()'},
    //       confirmLabelField: 'title',
    //   });
    //   list.reload();    // call after add/edit modal saves
    //
    // Handles loading (skeleton), empty (emptyState), error (errorState w/ retry),
    // and per-row delete via confirmDelete + DELETE deleteUrl/{id}. Returns
    // {reload, render, items}.
    entityList: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var mount = document.getElementById(opts.mountId);
        if (!mount) {
            console.warn('[entityList] mount #' + opts.mountId + ' not found');
            return {reload: function(){}, render: function(){}, items: []};
        }
        var state = {items: [], loading: false, error: null};
        var instanceKey = '_eosListInst_' + opts.mountId;

        function pickTitle(item) {
            if (opts.titleField && item[opts.titleField] != null) return item[opts.titleField];
            return item.title || item.name || item.label || item.id || '(untitled)';
        }
        function pickMeta(item) {
            var fields = opts.metaFields || ['updated', 'created'];
            var bits = [];
            fields.forEach(function(f) {
                var v = item[f];
                if (v) bits.push('<span class="eec-meta-field">' + esc(String(v)) + '</span>');
            });
            return bits.join(' · ');
        }
        function rowHtml(item) {
            if (opts.renderRow) return opts.renderRow(item);
            var id = item.id != null ? String(item.id) : (item.file || '');
            var clickAttr = opts.onClick
                ? 'window.' + instanceKey + '.click(' + JSON.stringify(id) + ')'
                : null;
            var card = EOS_UI.entityCard({
                title: pickTitle(item),
                subtitle: opts.subtitleField ? item[opts.subtitleField] : '',
                meta: pickMeta(item),
                onClick: clickAttr,
                className: 'eos-entity-card-row',
            });
            if (opts.deleteUrl && id) {
                var del = '<button class="eos-row-del" data-del="' + escAttr(id) +
                    '" aria-label="Delete"></button>';
                card = card.replace('<div class="eec-head">', del + '<div class="eec-head">');
            }
            return card;
        }
        function render() {
            if (state.loading && !state.items.length) {
                mount.innerHTML = '<div class="eos-loading"><span class="eos-spinner"></span></div>';
                return;
            }
            if (state.error) {
                mount.innerHTML = EOS_UI.errorState({
                    message: state.error,
                    onRetry: 'window.' + instanceKey + '.reload()',
                });
                return;
            }
            if (!state.items.length) {
                var empty = {message: opts.emptyMessage || 'Nothing here yet.'};
                if (opts.emptyAction) {
                    empty.actionLabel = opts.emptyAction.label;
                    empty.onAction = opts.emptyAction.onAction;
                }
                mount.innerHTML = EOS_UI.emptyState(empty);
                return;
            }
            mount.innerHTML = state.items.map(rowHtml).join('');
            mount.querySelectorAll('.eos-row-del').forEach(function(btn) {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    var id = btn.dataset.del;
                    var item = state.items.find(function(x) {
                        return String(x.id != null ? x.id : x.file) === id;
                    }) || {};
                    var label = opts.confirmLabelField ? item[opts.confirmLabelField] : pickTitle(item);
                    EOS_UI.confirmDelete({label: String(label || id)}).then(function(ok) {
                        if (!ok) return;
                        fetch(opts.deleteUrl + '/' + encodeURIComponent(id), {method: 'DELETE'})
                            .then(function(r) { return r.json(); })
                            .then(function(data) {
                                if (data && data.error) { EOS_UI.toast(data.error, false); return; }
                                EOS_UI.toast('Deleted');
                                instance.reload();
                            })
                            .catch(function(err) { EOS_UI.toast('Delete failed: ' + err, false); });
                    });
                });
            });
        }
        function reload() {
            state.loading = true;
            state.error = null;
            render();
            return fetch(opts.url, {headers: {'Accept': 'application/json'}})
                .then(function(r) {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.json();
                })
                .then(function(data) {
                    state.items = Array.isArray(data) ? data : (data.items || []);
                    state.loading = false;
                    render();
                })
                .catch(function(err) {
                    state.error = err.message || String(err);
                    state.loading = false;
                    render();
                });
        }
        var instance = {
            reload: reload,
            render: render,
            click: function(id) {
                var item = state.items.find(function(x) {
                    return String(x.id != null ? x.id : x.file) === id;
                });
                if (opts.onClick) opts.onClick(id, item);
            },
            get items() { return state.items; },
        };
        window[instanceKey] = instance;
        reload();
        return instance;
    },

    // --- Warning banner ---
    // Inline warning strip — feature-disabled notices, public-mode gates, etc.
    // Returns HTML string; caller assigns to a container's innerHTML.
    // opts: {message, icon? (default per tone), tone? "warning"|"info"|"error" (default warning)}
    warningBanner: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var tone = (opts.tone === 'info' || opts.tone === 'error') ? opts.tone : 'warning';
        var icon = opts.icon || (tone === 'info' ? 'ⓘ' : (tone === 'error' ? '⚠' : '⚠'));
        return '<div class="eos-warning-banner eos-warning-banner--' + tone + '">' +
            '<span class="eos-warning-banner-icon">' + esc(icon) + '</span>' +
            '<span class="eos-warning-banner-message">' + esc(opts.message || '') + '</span>' +
            '</div>';
    },

    // --- Modal ---
    // Show a modal with custom content. Returns the modal element.
    // options: {title, body (HTML string), onClose?, width?}
    modal: function(options) {
        var existing = document.getElementById('eos-modal-overlay');
        if (existing) existing.remove();

        var overlay = document.createElement('div');
        overlay.id = 'eos-modal-overlay';
        overlay.className = 'eos-modal-overlay show';
        overlay.onclick = function(e) { if (e.target === overlay) EOS_UI.closeModal(); };

        var modal = document.createElement('div');
        modal.className = 'eos-modal';
        if (options.width) modal.style.maxWidth = options.width;

        modal.innerHTML =
            '<div class="eos-modal-header">' +
                '<h2>' + EOS_UI.esc(options.title || '') + '</h2>' +
                '<button class="eos-modal-close" title="Close (Esc)" onclick="EOS_UI.closeModal()">&times;</button>' +
            '</div>' +
            '<div class="eos-modal-body">' + (options.body || '') + '</div>';

        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        EOS_UI._fabYield(true);
        EOS_UI._modalOnClose = options.onClose || null;

        // innerHTML-injected `autofocus` attributes don't auto-focus — do it manually.
        var autoEl = modal.querySelector('[autofocus]');
        if (autoEl) setTimeout(function() { try { autoEl.focus(); } catch (e) {} }, 0);

        // Escape key closes modal
        if (EOS_UI._modalEscHandler) {
            document.removeEventListener('keydown', EOS_UI._modalEscHandler);
        }
        EOS_UI._modalEscHandler = function(e) {
            if (e.key === 'Escape' && document.getElementById('eos-modal-overlay')) {
                EOS_UI.closeModal();
            }
        };
        document.addEventListener('keydown', EOS_UI._modalEscHandler);
        return modal;
    },
    _modalOnClose: null,

    // --- Form Builder ---
    // Build a simple form inside a modal. Returns form HTML string.
    // fields: [{key, label, type?, placeholder?, value?, options?}]
    formHtml: function(fields, submitLabel) {
        // First field gets autofocus by default; pass {autofocus:true} to force it on a specific field
        // (overrides default-first behavior when an earlier field already opted out via autofocus:false).
        var explicitAuto = fields.some(function(f) { return f.autofocus === true; });
        return fields.map(function(f, i) {
            // A field without `key` silently produces id="eos-form-undefined" for
            // EVERY field, so _submitForm() reads nothing back and the form fails
            // its own required-check ("name required") with the input discarded.
            // Passing `name:` instead of `key:` is the easy way to hit this.
            if (!f.key && window.console) {
                console.warn('EOS_UI.formHtml: field is missing `key` (did you pass `name:`?)', f);
            }
            var id = 'eos-form-' + f.key;
            var val = f.value || '';
            var auto = (f.autofocus === true) || (!explicitAuto && f.autofocus !== false && i === 0);
            var autoAttr = auto ? ' autofocus' : '';
            var input;
            if (f.type === 'textarea') {
                input = '<textarea id="' + id + '" class="eos-form-input" placeholder="' + EOS_UI.escAttr(f.placeholder || '') + '" rows="3"' + autoAttr + '>' + EOS_UI.esc(val) + '</textarea>';
            } else if (f.type === 'select' && f.options) {
                var opts = f.options.map(function(o) {
                    return '<option value="' + EOS_UI.escAttr(o) + '"' + (o === val ? ' selected' : '') + '>' + EOS_UI.esc(o) + '</option>';
                }).join('');
                input = '<select id="' + id + '" class="eos-form-input"' + autoAttr + '>' + opts + '</select>';
            } else if (f.type === 'number') {
                input = '<input id="' + id + '" class="eos-form-input" type="number" value="' + EOS_UI.escAttr(val) + '" placeholder="' + EOS_UI.escAttr(f.placeholder || '') + '" step="any"' + autoAttr + '>';
            } else if (f.type === 'date') {
                input = '<input id="' + id + '" class="eos-form-input" type="date" value="' + EOS_UI.escAttr(val) + '"' + autoAttr + '>';
            } else {
                input = '<input id="' + id + '" class="eos-form-input" type="text" value="' + EOS_UI.escAttr(val) + '" placeholder="' + EOS_UI.escAttr(f.placeholder || '') + '"' + autoAttr + '>';
            }
            // ✨ field-suggest opt-in: stamp data-suggest-* on the group so the
            // auto-mounter injects a "suggest from my vault" button next to the
            // input. See .claude/rules/field-suggest.md.
            var sg = '';
            if (f.suggest && typeof f.suggest === 'object') {
                var s = f.suggest;
                sg = ' data-suggest-app="' + EOS_UI.escAttr(s.app || '') + '"' +
                     ' data-suggest-field="' + EOS_UI.escAttr(s.field || f.key) + '"' +
                     ' data-suggest-mode="' + EOS_UI.escAttr(s.mode || 'fill') + '"';
                if (s.endpoint) sg += ' data-suggest-endpoint="' + EOS_UI.escAttr(s.endpoint) + '"';
                if (s.label) sg += ' data-suggest-label="' + EOS_UI.escAttr(s.label) + '"';
            }
            return '<div class="eos-form-group"' + sg + '><label class="eos-form-label">' + EOS_UI.esc(f.label) + '</label>' + input + '</div>';
        }).join('') +
        '<div class="eos-form-actions"><button class="eos-btn eos-btn-primary" title="Submit this form" onclick="EOS_UI._submitForm()">' + EOS_UI.esc(submitLabel || 'Save') + '</button></div>';
    },

    // ✨ Field-suggest — attach an AI "suggest from my vault" affordance to one
    // form input. Renders a small button beside the input; click → fetch
    // grounded candidate values → a popup pick-list → click fills (mode 'fill')
    // or comma-appends (mode 'append') the field. The user always picks; nothing
    // is auto-filled. See .claude/rules/field-suggest.md.
    //
    //   EOS_UI.fieldSuggest({
    //     input: '#premise' | el,   // input/textarea/select OR a container of one
    //     app: 'reader',            // app id for the platform endpoint
    //     field: 'premise',         // declared field id (default: input id/name)
    //     endpoint: '/x/api/...',   // OPTIONAL bespoke endpoint override
    //     mode: 'fill' | 'append',  // append → comma-join for list fields
    //     label: '✨ Suggest',
    //     context: function(){ return {cefr:'C1'}; },  // sibling values to ground on
    //     mount: el,                // where to put the button (default: after input)
    //   });
    //
    // Returns { button, refresh } or null when the input can't be resolved.
    fieldSuggest: function(opts) {
        opts = opts || {};
        var host = typeof opts.input === 'string' ? document.querySelector(opts.input) : opts.input;
        if (!host) return null;
        // Resolve the actual form control (host may be a wrapper, e.g. the
        // .eos-form-group div the formHtml auto-mounter passes).
        var ctrl = /^(INPUT|TEXTAREA|SELECT)$/.test(host.tagName) ? host
                 : host.querySelector('input, textarea, select');
        if (!ctrl) return null;
        if (ctrl.getAttribute('data-suggest-mounted') === '1') return null;
        ctrl.setAttribute('data-suggest-mounted', '1');

        var app = opts.app || '';
        var field = opts.field || ctrl.getAttribute('data-field') || ctrl.name ||
                    (ctrl.id || '').replace(/^eos-form-/, '');
        var mode = opts.mode || 'fill';
        var endpoint = opts.endpoint || '';
        var label = opts.label || '✨ Suggest';

        var bar = document.createElement('div');
        bar.className = 'eos-suggest-bar';
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'eos-btn-sm eos-suggest-btn';
        btn.textContent = label;
        bar.appendChild(btn);
        var pop = document.createElement('div');
        pop.className = 'eos-suggest-pop';
        pop.style.display = 'none';
        bar.appendChild(pop);

        var mount = opts.mount || (host !== ctrl ? host : ctrl.parentNode);
        if (mount === ctrl.parentNode) mount.insertBefore(bar, ctrl.nextSibling);
        else mount.appendChild(bar);

        // Outside-click close: the document listener is attached only WHILE the
        // popup is open and removed on close, so re-mounting (e.g. a formModal
        // reopened with a fresh DOM) doesn't leak a listener per mount.
        var _onOutside = function(e) { if (!bar.contains(e.target)) closePop(); };
        var closePop = function() {
            pop.style.display = 'none';
            pop.innerHTML = '';
            document.removeEventListener('click', _onOutside, true);
        };

        var gatherContext = function() {
            if (typeof opts.context === 'function') { try { return opts.context() || {}; } catch (e) { return {}; } }
            // Auto: sibling eos-form-* controls within the nearest form-ish container.
            var box = ctrl.closest('.eos-modal, .eos-form, form') || ctrl.parentNode.parentNode;
            var out = {};
            if (box && box.querySelectorAll) {
                var sibs = box.querySelectorAll('input, textarea, select');
                for (var i = 0; i < sibs.length; i++) {
                    var el = sibs[i];
                    if (el === ctrl) continue;
                    var k = (el.id || '').replace(/^eos-form-/, '') || el.getAttribute('data-field') || el.name;
                    if (k && el.value) out[k] = el.value;
                }
            }
            return out;
        };

        var fill = function(text) {
            if (mode === 'append' && ctrl.value && ctrl.value.trim()) {
                ctrl.value = ctrl.value.replace(/[,\s]+$/, '') + ', ' + text;
            } else {
                ctrl.value = text;
            }
            ctrl.dispatchEvent(new Event('input', { bubbles: true }));
            closePop();
            ctrl.focus();
        };

        var run = async function() {
            var orig = btn.textContent;
            btn.disabled = true; btn.textContent = 'Thinking…';
            closePop();
            var url = endpoint || '/api/sdk/suggest-field';
            var payload = endpoint ? { field: field, context: gatherContext() }
                                   : { app: app, field: field, context: gatherContext() };
            var data;
            try {
                var r = await fetch(url, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                data = await r.json();
            } catch (e) { data = { error: String(e) }; }
            btn.disabled = false; btn.textContent = orig;
            var list = (data && (data.suggestions || data.premises)) || [];
            if (!list.length) {
                if (EOS_UI.toast) EOS_UI.toast((data && (data.error || data.note)) || 'No suggestions', false);
                return;
            }
            pop.innerHTML = list.map(function(s, i) {
                return '<button type="button" class="eos-suggest-item" data-i="' + i + '">' + EOS_UI.esc(s) + '</button>';
            }).join('');
            pop.style.display = 'flex';
            // Defer so the click that opened the popup doesn't immediately close it.
            setTimeout(function() { document.addEventListener('click', _onOutside, true); }, 0);
            Array.prototype.forEach.call(pop.querySelectorAll('.eos-suggest-item'), function(b) {
                b.addEventListener('click', function() { fill(list[Number(b.dataset.i)]); });
            });
        };
        btn.addEventListener('click', run);
        return { button: btn, refresh: run };
    },

    // App-page header — title + subtitle + right-aligned actions.
    // Solves the recurring pattern where apps roll their own `.page-header`
    // with a `position:absolute` button that floats to body when the parent
    // forgets `position:relative` (collides with global `.nav-more` etc).
    //
    // Usage:
    //   document.getElementById('header-mount').innerHTML = EOS_UI.appHeader({
    //     title: 'Stillness',
    //     subtitle: 'Breathe. Be present.',
    //     actions: [
    //       {label: '⚙', onclick: 'openAppSettings()', title: 'Settings'},
    //     ],
    //   });
    //
    // Or insert directly:
    //   document.body.insertAdjacentHTML('afterbegin', EOS_UI.appHeader({...}));
    //
    // Returns an HTML string. Header is `position:relative` so any internal
    // absolute-positioned children anchor here, not to <body>.
    appHeader: function(opts) {
        opts = opts || {};
        var title = opts.title || '';
        var subtitle = opts.subtitle || '';
        var actions = (opts.actions || []).map(function(a) {
            var attrs = ' onclick="' + (a.onclick || '') + '"';
            if (a.title) attrs += ' title="' + String(a.title).replace(/"/g, '&quot;') + '"';
            if (a.id) attrs += ' id="' + a.id + '"';
            var cls = a.className || 'eos-btn eos-btn-sm';
            return '<button class="' + cls + '"' + attrs + '>' + (a.label || '') + '</button>';
        }).join('');
        return '<div class="eos-app-header" style="position:relative;display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:14px 0 12px;border-bottom:1px solid var(--border);margin-bottom:16px;flex-wrap:wrap">' +
               '<div style="min-width:0;flex:1">' +
               (title ? '<h1 style="font-size:20px;font-weight:700;margin:0;color:var(--text-heading)">' + title + '</h1>' : '') +
               (subtitle ? '<div style="font-size:13px;color:var(--text-secondary);margin-top:3px">' + subtitle + '</div>' : '') +
               '</div>' +
               (actions ? '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">' + actions + '</div>' : '') +
               '</div>';
    },

    // Defensive DOM writers — no-op when the target element isn't mounted
    // (e.g. tab-specific stat boxes when a different tab is active).
    // Fixes the recurring "Cannot set properties of null" class of bugs.
    safeText: function(id, v) {
        var el = typeof id === 'string' ? document.getElementById(id) : id;
        if (el) el.textContent = v == null ? '' : v;
    },
    safeHtml: function(id, v) {
        var el = typeof id === 'string' ? document.getElementById(id) : id;
        if (el) el.innerHTML = v == null ? '' : v;
    },

    // Collect form values by field keys
    formValues: function(fields) {
        var vals = {};
        fields.forEach(function(f) {
            var el = document.getElementById('eos-form-' + f.key);
            if (el) vals[f.key] = f.type === 'number' ? parseFloat(el.value) || 0 : el.value;
        });
        return vals;
    },

    _formCallback: null,
    _formFields: null,

    // Show a form modal. onSubmit(values) called with form data.
    formModal: function(title, fields, onSubmit) {
        // Accept object form: formModal({title, fields, onSubmit}) — common mistake.
        if (typeof title === 'object' && title !== null && !Array.isArray(title) && Array.isArray(title.fields)) {
            var opts = title;
            title = opts.title; fields = opts.fields; onSubmit = opts.onSubmit;
        }
        EOS_UI._formCallback = onSubmit;
        EOS_UI._formFields = fields;
        EOS_UI.modal({
            title: title,
            body: EOS_UI.formHtml(fields, 'Save'),
        });
    },

    _submitForm: function() {
        if (EOS_UI._formCallback && EOS_UI._formFields) {
            var vals = EOS_UI.formValues(EOS_UI._formFields);
            EOS_UI._formCallback(vals);
        }
        EOS_UI.closeModal();
    },

    // --- Attachment Picker ---
    // Modal with two tabs: Vault (search vault notes) / Upload (drag-drop OS files).
    // On pick, fires onPick({type, path, name}) and closes the modal.
    // opts: {
    //   onPick: function({type:'vault'|'upload', path, name}) — required
    //   uploadUrl: string — POST endpoint for multipart upload (default '/assistant/api/upload')
    //   searchUrl: string — GET endpoint returning {files:[{path,name,folder,tags}]} (default '/assistant/api/vault-files')
    //   title: string — modal title (default 'Attach')
    // }
    attachmentPicker: function(opts) {
        opts = opts || {};
        var onPick = opts.onPick || function() {};
        var uploadUrl = opts.uploadUrl || '/assistant/api/upload';
        var searchUrl = opts.searchUrl || '/assistant/api/vault-files';

        var body =
            '<div class="eos-tabs" id="eos-att-tabs">' +
                '<div class="eos-tab active" data-tab="vault" onclick="EOS_UI._attTab(\'vault\')">Vault</div>' +
                '<div class="eos-tab" data-tab="upload" onclick="EOS_UI._attTab(\'upload\')">Upload</div>' +
            '</div>' +
            '<div id="eos-att-vault">' +
                '<input id="eos-att-search" class="eos-form-input" type="text" placeholder="Search vault notes…" autofocus>' +
                '<div id="eos-att-list" class="eos-att-list" style="margin-top:10px;max-height:50vh;overflow-y:auto"></div>' +
            '</div>' +
            '<div id="eos-att-upload" style="display:none">' +
                '<div id="eos-att-drop" class="eos-att-drop">' +
                    '<div class="eos-att-drop-icon">⬆</div>' +
                    '<div>Drop files here, or <label class="eos-att-browse">browse<input id="eos-att-file" type="file" multiple style="display:none"></label></div>' +
                    '<div class="eos-att-hint">Files land in the vault inbox and stay searchable.</div>' +
                '</div>' +
                '<div id="eos-att-progress" style="margin-top:10px"></div>' +
            '</div>';

        EOS_UI.modal({title: opts.title || 'Attach', body: body, width: '560px'});
        EOS_UI._attOnPick = onPick;
        EOS_UI._attUploadUrl = uploadUrl;
        EOS_UI._attSearchUrl = searchUrl;

        if (opts.initialTab === 'upload') EOS_UI._attTab('upload');
        EOS_UI._attLoadVault('');
        if (opts.initialFiles && opts.initialFiles.length) {
            EOS_UI._attTab('upload');
            EOS_UI._attUpload(opts.initialFiles);
        }

        var search = document.getElementById('eos-att-search');
        var debounce = null;
        search.addEventListener('input', function() {
            clearTimeout(debounce);
            debounce = setTimeout(function() { EOS_UI._attLoadVault(search.value); }, 180);
        });
        search.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                var first = document.querySelector('#eos-att-list .eos-att-row');
                if (first) first.click();
            }
        });

        var drop = document.getElementById('eos-att-drop');
        var fileInput = document.getElementById('eos-att-file');
        fileInput.addEventListener('change', function() {
            EOS_UI._attUpload(Array.from(fileInput.files || []));
            fileInput.value = '';
        });
        ['dragenter','dragover'].forEach(function(ev) {
            drop.addEventListener(ev, function(e) { e.preventDefault(); drop.classList.add('drag'); });
        });
        ['dragleave','drop'].forEach(function(ev) {
            drop.addEventListener(ev, function(e) { e.preventDefault(); drop.classList.remove('drag'); });
        });
        drop.addEventListener('drop', function(e) {
            var files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
            if (files.length) EOS_UI._attUpload(files);
        });
    },

    _attTab: function(name) {
        var tabs = document.querySelectorAll('#eos-att-tabs .eos-tab');
        tabs.forEach(function(t) { t.classList.toggle('active', t.dataset.tab === name); });
        document.getElementById('eos-att-vault').style.display = (name === 'vault') ? '' : 'none';
        document.getElementById('eos-att-upload').style.display = (name === 'upload') ? '' : 'none';
        if (name === 'vault') {
            var s = document.getElementById('eos-att-search');
            if (s) s.focus();
        }
    },

    _attLoadVault: function(q) {
        var list = document.getElementById('eos-att-list');
        if (!list) return;
        list.innerHTML = '<div class="eos-att-empty">Searching…</div>';
        var url = EOS_UI._attSearchUrl + '?limit=50&q=' + encodeURIComponent(q || '');
        fetch(url).then(function(r) { return r.json(); }).then(function(d) {
            var files = (d && d.files) || [];
            if (!files.length) {
                list.innerHTML = '<div class="eos-att-empty">No notes match.</div>';
                return;
            }
            list.innerHTML = files.map(function(f) {
                var folder = f.folder ? '<span class="eos-att-folder">' + EOS_UI.esc(f.folder) + '/</span>' : '';
                var tags = (f.tags || []).slice(0, 3).map(function(t) {
                    return '<span class="obs-tag">#' + EOS_UI.esc(t) + '</span>';
                }).join(' ');
                return '<div class="eos-att-row" data-path="' + EOS_UI.escAttr(f.path) + '" data-name="' + EOS_UI.escAttr(f.name) + '">' +
                            '<div class="eos-att-row-main">' + folder + '<span class="eos-att-name">' + EOS_UI.esc(f.name) + '</span></div>' +
                            (tags ? '<div class="eos-att-row-tags">' + tags + '</div>' : '') +
                        '</div>';
            }).join('');
            Array.from(list.querySelectorAll('.eos-att-row')).forEach(function(row) {
                row.onclick = function() {
                    var cb = EOS_UI._attOnPick;
                    EOS_UI.closeModal();
                    if (cb) cb({type: 'vault', path: row.dataset.path, name: row.dataset.name});
                };
            });
        }).catch(function() {
            list.innerHTML = '<div class="eos-att-empty">Search failed.</div>';
        });
    },

    _attUpload: function(files) {
        if (!files || !files.length) return;
        var prog = document.getElementById('eos-att-progress');
        var url = EOS_UI._attUploadUrl;
        var cb = EOS_UI._attOnPick;
        var remaining = files.length;
        files.forEach(function(file) {
            var line = document.createElement('div');
            line.className = 'eos-att-uprow';
            line.textContent = '⏳ ' + file.name;
            if (prog) prog.appendChild(line);

            var fd = new FormData();
            fd.append('file', file);
            fetch(url, {method: 'POST', body: fd}).then(function(r) { return r.json(); }).then(function(d) {
                if (d && d.path) {
                    line.textContent = '✓ ' + (d.name || file.name);
                    if (cb) cb({type: 'upload', path: d.path, name: d.name || file.name});
                } else {
                    line.textContent = '✗ ' + file.name + ' — ' + ((d && d.error) || 'upload failed');
                }
            }).catch(function(e) {
                line.textContent = '✗ ' + file.name + ' — ' + (e && e.message ? e.message : 'network error');
            }).finally(function() {
                remaining -= 1;
                if (remaining <= 0 && cb) {
                    setTimeout(function() {
                        if (document.getElementById('eos-att-progress')) EOS_UI.closeModal();
                    }, 600);
                }
            });
        });
    },

    // --- Model tier picker (shared across chat apps) ---
    // Tiers are user-facing aliases that map to concrete provider names.
    // fast → openai-nano, standard (default) → openai-mini (gpt-5.4-mini), pro → openai (gpt-5.4).
    // "auto" leaves the global chain in charge (claude-cli → openai-mini → ollama).
    TIERS: {
        auto:     { provider: 'auto',         label: 'Auto',     hint: 'System chooses (Claude free tier → OpenAI mini → local)' },
        fast:     { provider: 'openai-nano',  label: 'Fast',     hint: 'OpenAI nano — cheapest, quickest' },
        standard: { provider: 'openai-mini',  label: 'Standard', hint: 'OpenAI mini — best $/quality (default)' },
        pro:      { provider: 'openai',       label: 'Pro',      hint: 'OpenAI full (gpt-5.4) — strongest reasoning' },
    },

    // Map a provider name back to a tier key (for highlighting current selection).
    tierFromProvider: function(providerName) {
        if (!providerName) return 'auto';
        for (var k in EOS_UI.TIERS) {
            if (EOS_UI.TIERS[k].provider === providerName) return k;
        }
        return null; // unknown provider (e.g. claude-cli, ollama) — don't highlight a tier
    },

    // Show a modal with tier buttons. onSelect receives {tier, provider}.
    // opts: { current: 'standard'|<provider-name>, onSelect: function, title?: string }
    tierPicker: function(opts) {
        opts = opts || {};
        var current = opts.current || 'standard';
        // Accept either a tier key or a provider name.
        if (!EOS_UI.TIERS[current]) {
            var fromProv = EOS_UI.tierFromProvider(current);
            current = fromProv || 'auto';
        }
        var order = ['auto', 'fast', 'standard', 'pro'];
        var html = '<div class="eos-tier-picker">';
        order.forEach(function(k) {
            var t = EOS_UI.TIERS[k];
            var active = (k === current) ? ' eos-tier-active' : '';
            html += '<button type="button" class="eos-tier-btn' + active + '" data-tier="' + k + '" title="' + EOS_UI.escAttr(t.hint || ('Use the ' + t.label + ' tier')) + '">'
                 +   '<span class="eos-tier-label">' + EOS_UI.esc(t.label) + '</span>'
                 +   '<span class="eos-tier-hint">' + EOS_UI.esc(t.hint) + '</span>'
                 + '</button>';
        });
        html += '</div>';
        EOS_UI.modal({ title: opts.title || 'Model tier', body: html });
        // Wire buttons after modal is in DOM.
        setTimeout(function() {
            var btns = document.querySelectorAll('.eos-tier-btn');
            btns.forEach(function(b) {
                b.onclick = function() {
                    var tier = b.getAttribute('data-tier');
                    var prov = (EOS_UI.TIERS[tier] || {}).provider;
                    EOS_UI.closeModal();
                    if (typeof opts.onSelect === 'function') opts.onSelect({tier: tier, provider: prov});
                };
            });
        }, 0);
    },

    // --- Model pill (shared toolbar chip for think-using apps) ---
    //
    // Visible, switchable provider on every user-facing think surface.
    // Input-side mirror of EOS_UI.provenance() (which surfaces cloud/local
    // at render time). Without this, the free-first emptyos.toml policy
    // erodes silently the first time claude-cli rate-limits and the chain
    // falls back to paid mini.
    //
    // Usage:
    //   <span id="model-pill"></span>
    //   <script>
    //     EOS_UI.modelPill({
    //       app: 'viz',                  // required — the app id (matches manifest)
    //       mount: '#model-pill',        // CSS selector or element
    //       domain: 'code',              // optional — for chain resolution
    //       onSwitch: function(prov){}   // optional — fired after persist
    //     });
    //   </script>
    //
    // Cost taxonomy is a JS-side map — extend EOS_UI.MODEL_COSTS to add
    // providers. Lookup is prefix-based on provider name.
    MODEL_COSTS: {
        'claude-cli':  'free',   // Max subscription covers it
        'claude':      'free',
        'ollama':      'local',
        'openrouter':  'mixed',  // free when variant ends ':free'
        'glm':         'mixed',  // Ollama Cloud — free tier to start, metered above limits
        'openai':      'paid',
        'openai-mini': 'paid',
        'openai-nano': 'paid',
        'openai-gpt-4o-mini-vision': 'paid',
        'human':       'human',
    },

    _modelCostFor: function(name, variant) {
        var costs = EOS_UI.MODEL_COSTS || {};
        if (costs[name]) {
            var c = costs[name];
            if (c === 'mixed') {
                return /:free$/i.test(variant || '') ? 'free' : 'paid';
            }
            return c;
        }
        // Prefix fallback: openai-foo → openai
        var pref = (name || '').split('-')[0];
        if (costs[pref]) {
            var c2 = costs[pref];
            if (c2 === 'mixed') {
                return /:free$/i.test(variant || '') ? 'free' : 'paid';
            }
            return c2;
        }
        return 'unknown';
    },

    _modelCostIcon: function(klass) {
        return {free: '🆓', local: '🔒', paid: '💰', human: '👤', unknown: '·'}[klass] || '·';
    },

    // Auth-mode chip (input-side mirror of the cost icon): how the active
    // provider authenticates. 'local' is intentionally chipless — the cost
    // icon 🔒 already conveys local. Backed by Provider.auth_mode; see
    // .claude/rules/model-pill.md.
    _authModeIcon: function(mode) {
        return {login: '🪪', 'api-key': '🔑', byok: '🔑', none: '⚠'}[mode] || '';
    },
    _authModeLabel: function(mode) {
        return {
            login: 'CLI login (e.g. Max subscription) — no API key',
            'api-key': 'server-side API key',
            byok: 'your own API key (BYOK)',
            none: 'cloud provider with no key configured — calls will fail',
            local: 'local provider — no credential needed',
        }[mode] || '';
    },

    // --- Model-ability gating (sibling of modelPill / provenance) ---
    // Intrinsic model STRENGTH (weak<standard<strong), orthogonal to MODEL_COSTS.
    // Used to disable features a weak active model can't do well —
    // degrade-and-explain, never silent-hide. See .claude/rules/model-ability.md.
    ABILITY_ORDER: {weak: 0, standard: 1, strong: 2},
    ABILITY_LABELS: {weak: 'basic', standard: 'standard', strong: 'strong'},

    abilityMeets: function(active, required) {
        if (!required) return true;
        var o = EOS_UI.ABILITY_ORDER;
        var a = (o[active] == null) ? 1 : o[active];
        var r = (o[required] == null) ? 1 : o[required];
        return a >= r;
    },

    // Resolve the active think ability for an (app, domain).
    // → Promise<{ability, provider, model}>. Never rejects.
    fetchActiveAbility: function(app, domain) {
        var url = '/api/capabilities/think/effective?app=' + encodeURIComponent(app || '')
                + '&domain=' + encodeURIComponent(domain || '');
        return fetch(url).then(function(r) { return r.json(); }).then(function(d) {
            var m = (d.providers || []).filter(function(p) { return p.name === d.provider; })[0];
            return {
                ability: d.active_ability || 'standard',
                provider: d.provider || '',
                model: (m && m.model) || '',
            };
        }).catch(function() { return {ability: 'standard', provider: '', model: ''}; });
    },

    // "Needs a stronger model" banner HTML — reuses .eos-warning-banner--info.
    abilityBannerHtml: function(res) {
        var esc = EOS_UI.esc;
        var need = EOS_UI.ABILITY_LABELS[res.minAbility] || res.minAbility;
        var cur = EOS_UI.ABILITY_LABELS[res.ability] || res.ability;
        var who = res.model || res.provider || 'the current model';
        return '<div class="eos-warning-banner eos-warning-banner--info">'
            + '<span class="eos-warning-banner-icon">⚡</span>'
            + '<span class="eos-warning-banner-message">Needs a <b>' + esc(need) + '</b> model — '
            + 'current: ' + esc(who) + ' (' + esc(cur) + '). Switch via the model pill, '
            + 'or add your own key in Settings → BYOK.</span></div>';
    },

    // Gate a feature by minimum ability. opts: {app, domain?, minAbility, onState?}.
    // → Promise<{meets, ability, provider, model, minAbility}>. The caller decides
    // how to reflect it (disable a button, gray an <option>, show abilityBannerHtml).
    abilityGate: function(opts) {
        opts = opts || {};
        if (!opts.app) { console.warn('EOS_UI.abilityGate: app required'); }
        var minAbility = opts.minAbility || 'standard';
        return EOS_UI.fetchActiveAbility(opts.app, opts.domain || '').then(function(info) {
            var res = {
                meets: EOS_UI.abilityMeets(info.ability, minAbility),
                ability: info.ability, provider: info.provider, model: info.model,
                minAbility: minAbility,
            };
            if (opts.onState) { try { opts.onState(res); } catch (e) {} }
            return res;
        });
    },

    modelPill: function(opts) {
        opts = opts || {};
        var app = opts.app;
        if (!app) {
            console.warn('EOS_UI.modelPill: app is required');
            return null;
        }
        var domain = opts.domain || '';
        var mount = opts.mount;
        if (typeof mount === 'string') mount = document.querySelector(mount);
        if (!mount) {
            console.warn('EOS_UI.modelPill: mount element not found:', opts.mount);
            return null;
        }

        var state = {effective: null, providers: [], loading: true};

        function render() {
            if (state.loading) {
                mount.innerHTML = '<span class="eos-model-pill eos-model-pill-loading">·</span>';
                return;
            }
            var eff = state.effective || {};
            var current = eff.provider || '';
            var meta = state.providers.find(function(p){ return p.name === current; }) || {};
            var klass = EOS_UI._modelCostFor(current, meta.variant);
            var icon = EOS_UI._modelCostIcon(klass);
            var subtitle = meta.variant ? meta.variant.replace(/^[^:]+:/, '') : (meta.model || '');
            var authMode = meta.auth_mode || eff.active_auth_mode || '';
            var authIcon = EOS_UI._authModeIcon(authMode);
            var authChip = authIcon
                ? '<span class="eos-model-pill-auth" title="Auth: ' + EOS_UI.escAttr(EOS_UI._authModeLabel(authMode)) + '">' + authIcon + '</span>'
                : '';
            var pillClass = 'eos-model-pill eos-model-pill-' + klass;
            if (eff.source && eff.source !== 'chain') pillClass += ' eos-model-pill-pinned';
            mount.innerHTML =
                '<button type="button" class="' + pillClass + '" title="Click to switch model (this app only)">' +
                  '<span class="eos-model-pill-icon">' + icon + '</span>' +
                  '<span class="eos-model-pill-name">' + EOS_UI.esc(current || 'none') + '</span>' +
                  (subtitle ? '<span class="eos-model-pill-sub">' + EOS_UI.esc(subtitle) + '</span>' : '') +
                  authChip +
                  '<span class="eos-model-pill-caret">▾</span>' +
                '</button>';
            mount.querySelector('button').onclick = openPopover;
        }

        function openPopover() {
            var rows = state.providers
                .filter(function(p){ return EOS_UI._modelCostFor(p.name, p.variant) !== 'human'; })
                .map(function(p) {
                    var klass = EOS_UI._modelCostFor(p.name, p.variant);
                    var icon = EOS_UI._modelCostIcon(klass);
                    var active = (state.effective && state.effective.provider === p.name) ? ' eos-model-row-active' : '';
                    var unavail = p.available ? '' : ' eos-model-row-unavail';
                    var subtitle = p.variant ? p.variant.replace(/^[^:]+:/, '') : (p.model || '');
                    return '<button type="button" class="eos-model-row' + active + unavail + '" data-prov="' + EOS_UI.escAttr(p.name) + '" title="Switch this app to ' + EOS_UI.escAttr(p.name) + '">' +
                            '<span class="eos-model-row-icon">' + icon + '</span>' +
                            '<span class="eos-model-row-name">' + EOS_UI.esc(p.name) + '</span>' +
                            (subtitle ? '<span class="eos-model-row-sub">' + EOS_UI.esc(subtitle) + '</span>' : '') +
                            (p.available ? '' : '<span class="eos-model-row-warn">unreachable</span>') +
                           '</button>';
                }).join('');
            var clearBtn = '<button type="button" class="eos-model-row eos-model-row-clear" data-prov="" title="Clear the override and use the default provider chain">' +
                            '<span class="eos-model-row-icon">↺</span>' +
                            '<span class="eos-model-row-name">Use chain default</span>' +
                           '</button>';
            var hdr = '<div class="eos-model-popover-head">' +
                       '<span>Model for ' + EOS_UI.esc(app) + '</span>' +
                       '<span class="eos-model-popover-source">' +
                         (state.effective && state.effective.source !== 'chain'
                           ? '📌 pinned via settings'
                           : 'using chain default')
                       + '</span>' +
                     '</div>';
            EOS_UI.modal({
                title: 'Switch model',
                width: '440px',
                body: hdr + '<div class="eos-model-popover-body">' + rows + clearBtn + '</div>',
            });
            setTimeout(function() {
                document.querySelectorAll('.eos-model-row').forEach(function(btn) {
                    btn.onclick = function() {
                        var prov = btn.getAttribute('data-prov') || '';
                        switchTo(prov);
                    };
                });
            }, 0);
        }

        async function switchTo(provName) {
            try {
                var res = await fetch('/settings/api/set', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({key: 'think.app.' + app, value: provName || ''}),
                });
                var data = await res.json();
                if (!res.ok || (data && data.error)) {
                    EOS_UI.toast('Switch failed: ' + (data && data.error || res.status), false);
                    return;
                }
                EOS_UI.closeModal();
                if (provName) {
                    EOS_UI.toast('Model: ' + provName, true);
                } else {
                    EOS_UI.toast('Cleared override · using chain default', true);
                }
                await load();
                if (typeof opts.onSwitch === 'function') opts.onSwitch(provName || null);
            } catch (e) {
                EOS_UI.toast('Switch error: ' + e.message, false);
            }
        }

        async function load() {
            state.loading = true;
            render();
            try {
                var url = '/api/capabilities/think/effective?app=' + encodeURIComponent(app);
                if (domain) url += '&domain=' + encodeURIComponent(domain);
                var res = await fetch(url);
                var data = await res.json();
                state.effective = data;
                state.providers = data.providers || [];
            } catch (e) {
                state.effective = {provider: '?', source: 'error'};
                state.providers = [];
            }
            state.loading = false;
            render();
        }

        load();
        return {refresh: load};
    },

    // --- Chat transcript (shared by chat-shaped apps) ---
    //
    // Owns rendering of user/assistant/system messages and streaming turns
    // inside an `.eos-chat-transcript` container. Apps keep control of
    // sessions, WebSockets, and tool-call specifics; the transcript just
    // renders message bubbles consistently.
    //
    // Usage:
    //   var tr = EOS_UI.chatTranscript(document.getElementById('transcript'));
    //   tr.clear();
    //   tr.appendUser('hello');
    //   var turn = tr.startAssistant();
    //   tr.appendChunk(turn, 'streaming text…');
    //   tr.finalize(turn);                 // re-renders as markdown
    //   tr.showEmpty('<p>Start typing…</p>');
    chatTranscript: function(mountEl) {
        if (!mountEl) return null;
        function scroll() { mountEl.scrollTop = mountEl.scrollHeight; }
        return {
            el: mountEl,
            clear: function() { mountEl.innerHTML = ''; },
            showEmpty: function(html) {
                mountEl.innerHTML = '<div class="eos-chat-empty">' + (html || '') + '</div>';
            },
            appendUser: function(text) {
                var t = document.createElement('div');
                t.className = 'eos-chat-turn user';
                t.innerHTML = '<div class="eos-chat-role-label user">You</div>' +
                              '<div class="eos-chat-turn-content"></div>';
                t.querySelector('.eos-chat-turn-content').textContent = text || '';
                mountEl.appendChild(t); scroll(); return t;
            },
            appendAssistant: function(text, opts) {
                opts = opts || {};
                var t = document.createElement('div');
                t.className = 'eos-chat-turn assistant';
                var label = opts.label || 'Assistant';
                t.innerHTML = '<div class="eos-chat-role-label assistant">' + EOS_UI.esc(label) + '</div>' +
                              '<div class="eos-chat-turn-content markdown"></div>';
                var body = t.querySelector('.eos-chat-turn-content');
                body.innerHTML = EOS_UI.renderMarkdown ? EOS_UI.renderMarkdown(text || '') : EOS_UI.esc(text || '');
                mountEl.appendChild(t); scroll(); return t;
            },
            appendSystem: function(text) {
                var t = document.createElement('div');
                t.className = 'eos-chat-turn system';
                t.innerHTML = '<div class="eos-chat-role-label">System</div>' +
                              '<div class="eos-chat-turn-content dim"></div>';
                t.querySelector('.eos-chat-turn-content').textContent = text || '';
                mountEl.appendChild(t); scroll(); return t;
            },
            startAssistant: function(opts) {
                opts = opts || {};
                var t = document.createElement('div');
                t.className = 'eos-chat-turn assistant';
                var label = opts.label || 'Assistant';
                t.innerHTML = '<div class="eos-chat-role-label assistant">' + EOS_UI.esc(label) + '</div>' +
                              '<div class="eos-chat-turn-content streaming"></div>';
                mountEl.appendChild(t); scroll(); return t;
            },
            appendChunk: function(turn, text) {
                if (!turn) return;
                var body = turn.querySelector('.eos-chat-turn-content');
                if (body) { body.textContent = (body.textContent || '') + (text || ''); scroll(); }
            },
            setStreamText: function(turn, fullText) {
                // For protocols that send cumulative text, not deltas.
                if (!turn) return;
                var body = turn.querySelector('.eos-chat-turn-content');
                if (body) { body.textContent = fullText || ''; scroll(); }
            },
            finalize: function(turn, opts) {
                opts = opts || {};
                if (!turn) return;
                var body = turn.querySelector('.eos-chat-turn-content');
                if (!body) return;
                var text = opts.text != null ? opts.text : body.textContent;
                body.classList.remove('streaming');
                body.classList.add('markdown');
                body.innerHTML = EOS_UI.renderMarkdown ? EOS_UI.renderMarkdown(text) : EOS_UI.esc(text);
                scroll();
            },
            scrollToBottom: scroll,
        };
    },

    // --- Loading State ---
    loading: function(targetId, show) {
        var el = document.getElementById(targetId);
        if (!el) return;
        if (show !== false) {
            el.innerHTML = '<div class="eos-loading"><div class="eos-spinner"></div></div>';
        }
    },

    // Canonical delete confirmation. Wraps confirm() with consistent copy +
    // danger styling so every app's delete prompt reads the same.
    //   var ok = await EOS_UI.confirmDelete({label: 'EmptyOS deck'});
    //   if (ok) { await fetch('/myapp/api/items/' + id, {method:'DELETE'}); ... }
    // Optional: action ('Delete' default), extra (extra sentence appended).
    confirmDelete: function(opts) {
        opts = opts || {};
        var label = opts.label ? '"' + opts.label + '"' : 'this item';
        var extra = opts.extra ? ' ' + opts.extra : '';
        return EOS_UI.confirm({
            message: 'Delete ' + label + '? This cannot be undone.' + extra,
            action: opts.action || 'Delete',
            danger: true,
        });
    },

    // --- Confirm Dialog ---
    // Default is SAFE — action='Confirm', danger=false (primary button styling).
    // For destructive operations, use the options form with danger: true so the
    // button renders in eos-btn-danger (red). Or use EOS_UI.confirmDelete() which
    // wraps this with canonical delete copy + danger styling.
    //
    // Callback form (safe): EOS_UI.confirm('Are you sure?', function() { /* yes */ })
    // Async form (safe):    if (!await EOS_UI.confirm('Are you sure?')) return;
    // Destructive:          EOS_UI.confirm({message: 'Delete X?', action: 'Delete', danger: true})
    // Custom non-destructive: EOS_UI.confirm({message: '...', action: 'Publish'})
    // Aliases are honoured, not ignored. Six apps independently reached for
    // {title, body, confirmText|confirmLabel|okText} — the vocabulary of every
    // other dialog API — and silently got a bare "Are you sure?" behind a
    // primary button on destructive actions (vault-backup restore among them).
    // A dropped key on a confirm dialog is invisible in review and in every
    // static check, so the component accepts the shape callers actually write.
    confirm: function(messageOrOpts, onYes) {
        var message, action, danger, title, cancel = 'Cancel';
        if (typeof messageOrOpts === 'object') {
            var o = messageOrOpts;
            message = o.message || o.body || 'Are you sure?';
            // A caller that split title/body meant both to be read.
            if (o.title && o.message && o.body) message = o.message + ' ' + o.body;
            action = o.action || o.confirmText || o.confirmLabel ||
                     o.okText || o.okLabel || 'Confirm';
            danger = !!o.danger;
            title = o.title || 'Confirm';
            // onConfirm was dropped silently, which is worse than a wrong
            // label: substation-project's delete button ran no callback at all.
            onYes = onYes || o.onYes || o.onConfirm;
            cancel = o.cancelLabel || o.cancelText || 'Cancel';
        } else {
            message = messageOrOpts || 'Are you sure?';
            action = 'Confirm';
            danger = false;
            title = 'Confirm';
        }
        var btnClass = danger ? 'eos-btn eos-btn-danger' : 'eos-btn eos-btn-primary';
        var resolve;
        var promise = new Promise(function(res) { resolve = res; });
        EOS_UI.modal({
            title: title,
            body: '<p style="margin:0 0 16px;font-size:15px;color:var(--text-secondary)">' + EOS_UI.esc(message) + '</p>' +
                '<div class="eos-form-actions">' +
                    '<button class="eos-btn" id="eos-confirm-no" title="Cancel — nothing will change">' + EOS_UI.esc(cancel) + '</button>' +
                    '<button class="' + btnClass + '" id="eos-confirm-yes" title="Confirm and proceed">' + EOS_UI.esc(action) + '</button>' +
                '</div>',
        });
        // Resolve BEFORE closeModal so the overlay-close _modalOnClose (which
        // also calls resolve) doesn't win the race — Promises are first-write.
        document.getElementById('eos-confirm-no').onclick = function() { resolve(false); EOS_UI.closeModal(); };
        document.getElementById('eos-confirm-yes').onclick = function() { resolve(true); if(onYes) onYes(); EOS_UI.closeModal(); };
        // Overlay click or Escape → cancel
        var origClose = EOS_UI._modalOnClose;
        EOS_UI._modalOnClose = function() { resolve(false); if(origClose) origClose(); };
        return promise;
    },

    // --- Dictionary Popup (double-click word lookup) ---

    _dictInit: false,

    initDict: function() {
        if (EOS_UI._dictInit) return;
        if (document.body.hasAttribute('data-no-dict')) return;
        EOS_UI._dictInit = true;

        // Create popup element
        var popup = document.createElement('div');
        popup.id = 'eos-dict-popup';
        document.body.appendChild(popup);

        // Global dblclick handler
        document.addEventListener('dblclick', function(e) {
            if (e.target.closest('#eos-dict-popup')) return;
            if (e.target.closest('input, textarea, [contenteditable]')) return;
            var sel = window.getSelection().toString().trim();
            if (!sel || sel.length > 40 || sel.indexOf(' ') > 15) return;
            EOS_UI.showDict(sel, e.clientX, e.clientY);
        });

        // Click outside to close
        document.addEventListener('click', function(e) {
            if (!e.target.closest('#eos-dict-popup')) {
                document.getElementById('eos-dict-popup').classList.remove('active');
            }
        });
    },

    showDict: function(word, x, y) {
        var popup = document.getElementById('eos-dict-popup');
        popup.innerHTML = '<div class="eos-dict-loading">Looking up...</div>';
        popup.classList.add('active');
        popup.style.left = Math.min(x, window.innerWidth - 320) + 'px';
        popup.style.top = Math.min(y + 10, window.innerHeight - 360) + 'px';

        // Try free dictionary API first, then EmptyOS dictionary app
        fetch('https://api.dictionaryapi.dev/api/v2/entries/en/' + encodeURIComponent(word))
            .then(function(r) { return r.ok ? r.json() : Promise.reject('not found'); })
            .then(function(json) {
                if (!json[0]) throw 'empty';
                var entry = json[0];
                // Find audio URL from phonetics
                var audioUrl = '';
                if (entry.phonetics) {
                    for (var pi = 0; pi < entry.phonetics.length; pi++) {
                        if (entry.phonetics[pi].audio) { audioUrl = entry.phonetics[pi].audio; break; }
                    }
                }
                EOS_UI._renderDict(popup, {
                    word: entry.word,
                    ipa: entry.phonetic || (entry.phonetics && entry.phonetics[0] && entry.phonetics[0].text) || '',
                    audio: audioUrl,
                    meanings: entry.meanings.slice(0, 3).map(function(m) {
                        return {
                            pos: m.partOfSpeech,
                            defs: m.definitions.slice(0, 2).map(function(d) {
                                return { def: d.definition, ex: d.example || '' };
                            })
                        };
                    })
                });
            })
            .catch(function() {
                // Fallback to EmptyOS dictionary app
                fetch(EOS.base + '/dictionary/api/lookup?word=' + encodeURIComponent(word))
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.error) {
                            popup.innerHTML = '<div class="eos-dict-word">' + EOS_UI.esc(word) + '</div>' +
                                '<div style="color:var(--text-muted);margin-top:8px">No definition found</div>';
                            return;
                        }
                        EOS_UI._renderDict(popup, {
                            word: data.word || word,
                            ipa: data.phonetic || '',
                            meanings: [{pos: data.part_of_speech || '', defs: [{def: data.definition || '', ex: ''}]}],
                            chinese: data.chinese || ''
                        });
                    })
                    .catch(function() {
                        popup.innerHTML = '<div class="eos-dict-word">' + EOS_UI.esc(word) + '</div>' +
                            '<div style="color:var(--text-muted);margin-top:8px">Lookup failed</div>';
                    });
            });
    },

    _renderDict: function(popup, data) {
        var html = '<div class="eos-dict-word">' + EOS_UI.esc(data.word);
        if (data.audio) html += ' <span class="eos-dict-play" onclick="EOS_UI._dictPlay(\'' + data.audio.replace(/'/g, "\\'") + '\')">&#128264;</span>';
        html += '</div>';
        if (data.ipa) html += '<span class="eos-dict-ipa">' + EOS_UI.esc(data.ipa) + '</span>';
        if (data.meanings) {
            data.meanings.forEach(function(m) {
                if (m.pos) html += '<div class="eos-dict-pos">' + EOS_UI.esc(m.pos) + '</div>';
                m.defs.forEach(function(d) {
                    html += '<div class="eos-dict-def">' + EOS_UI.esc(d.def) + '</div>';
                    if (d.ex) html += '<div class="eos-dict-ex">"' + EOS_UI.esc(d.ex) + '"</div>';
                });
            });
        }
        if (data.chinese) {
            html += '<div class="eos-dict-zh">' + EOS_UI.esc(data.chinese) + '</div>';
        }
        html += '<button class="eos-dict-save" title="Save this word to your vault dictionary" onclick="EOS_UI._dictSave(' + EOS_UI.escAttr(JSON.stringify(data.word)) + ')">Save to Dictionary</button>';
        popup.innerHTML = html;
    },

    _dictPlay: function(url) {
        try { new Audio(url).play(); } catch(e) {}
    },

    _dictSave: function(word) {
        fetch(EOS.base + '/dictionary/api/lookup?word=' + encodeURIComponent(word), {method: 'GET'})
            .then(function() {
                EOS_UI.toast('Saved: ' + word);
                document.getElementById('eos-dict-popup').classList.remove('active');
            })
            .catch(function() { EOS_UI.toast('Save failed', false); });
    },

    // ── Compare Table (reusable across apps) ──────────────
    // Usage: EOS_UI.compareTable({
    //   container: '#my-div' or element,
    //   columns: [{key:'balance', label:'Final Balance', format:'$'}, ...],
    //   rows: [{name:'Floor', values:{balance:1366994, income:54680}}, ...],
    //   highlight: 'max'  // highlight best per column: 'max', 'min', or null
    // })
    compareTable: function(opts) {
        var el = typeof opts.container === 'string' ? document.querySelector(opts.container) : opts.container;
        if (!el) return;
        var cols = opts.columns || [];
        var rows = opts.rows || [];
        var hi = opts.highlight || null;

        // Find best values per column
        var best = {}, worst = {};
        if (hi) {
            cols.forEach(function(c) {
                var vals = rows.map(function(r) { return r.values[c.key]; }).filter(function(v) { return typeof v === 'number'; });
                if (vals.length) {
                    best[c.key] = hi === 'max' ? Math.max.apply(null, vals) : Math.min.apply(null, vals);
                    worst[c.key] = hi === 'max' ? Math.min.apply(null, vals) : Math.max.apply(null, vals);
                }
            });
        }

        var html = '<table class="eos-compare-table"><thead><tr><th>Scenario</th>';
        cols.forEach(function(c) { html += '<th>' + EOS_UI.esc(c.label) + '</th>'; });
        html += '</tr></thead><tbody>';
        rows.forEach(function(r) {
            html += '<tr><td>' + EOS_UI.esc(r.name) + '</td>';
            cols.forEach(function(c) {
                var v = r.values[c.key];
                var cls = '';
                if (hi && typeof v === 'number') {
                    if (v === best[c.key]) cls = ' class="eos-cmp-best"';
                    else if (v === worst[c.key]) cls = ' class="eos-cmp-worst"';
                }
                var display = v;
                if (typeof v === 'number') {
                    if (c.format === '$') display = '$' + Math.round(v).toLocaleString();
                    else if (c.format === '%') display = v.toFixed(1) + '%';
                    else display = v.toLocaleString();
                } else if (typeof v === 'boolean') {
                    display = v ? 'Yes' : 'No';
                }
                html += '<td' + cls + '>' + display + '</td>';
            });
            html += '</tr>';
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    },

    // Compare bar chart (requires Plotly)
    // Usage: EOS_UI.compareChart({
    //   container: '#chart-div',
    //   labels: ['Floor','Target','Strong'],
    //   values: [1366994, 1826840, 2402035],
    //   colors: ['#f59e0b','#3b82f6','#10b981'],  // optional
    //   title: 'Final Balance',
    //   yformat: '$,.0f',
    // })
    compareChart: function(opts) {
        var el = typeof opts.container === 'string' ? document.querySelector(opts.container) : opts.container;
        if (!el || typeof Plotly === 'undefined') return;
        el.style.display = 'block';
        var colors = opts.colors || ['#f59e0b','#3b82f6','#10b981','#8b5cf6','#ef4444','#06b6d4','#ec4899','#84cc16'];
        var hasLongLabels = opts.labels.some(function(l) { return l.length > 12; });
        Plotly.newPlot(el, [{
            x: opts.labels, y: opts.values, type: 'bar',
            marker: { color: colors.slice(0, opts.labels.length) },
            text: opts.values.map(function(v) { return opts.yformat === '$,.0f' ? '$' + Math.round(v).toLocaleString() : v; }),
            textposition: 'outside',
        }], {
            title: opts.title || '',
            xaxis: { tickangle: hasLongLabels ? -30 : 0 },
            yaxis: { title: opts.ytitle || '', tickformat: opts.yformat || '' },
            margin: {t:40, b: hasLongLabels ? 100 : 50, l:80, r:20},
            height: opts.height || 380,
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: getComputedStyle(document.body).getPropertyValue('--text'), size: 12 },
        }, {responsive: true});
    },

    // --- Settings slide-out (shared) ---
    // Usage:
    //   EOS_UI.settingsPanel({
    //     id: 'app-settings',                // panel DOM id
    //     title: 'App Settings',
    //     fields: [
    //       {key: 'projects.stale_days', label: 'Stale After (days)', type: 'number', default: 90, hint: 'Flagged stale after N days.'},
    //       {key: 'projects.show_foo', label: 'Show foo', type: 'boolean', default: false},
    //     ],
    //     onSave: function(values) { /* optional — after save */ },
    //   });
    // Fields read from/written to /settings/api/config + /settings/api/set-bulk.
    // Types: 'number' | 'text' | 'boolean' | 'select' (with options:[]) | 'textarea'
    // Renders an app's ⚙ settings drawer.
    //
    // Pass `fields: [...]` to declare them inline, or `app: '<app-id>'` to
    // derive them from that app's manifest `[provides.settings] schema` (the
    // same source /settings renders from). Prefer `app:` — a hand-mirrored
    // list silently drifts from the manifest the moment a setting is added,
    // which is how five apps ended up hiding eleven of their own settings.
    // Schema fetch is lazy (first open) so construction stays synchronous.
    settingsPanel: function(opts) {
        var id = opts.id || 'eos-app-settings';
        var existing = document.getElementById(id);
        if (existing) existing.remove();

        var panel = document.createElement('div');
        panel.id = id;
        panel.className = 'eos-settings-panel';

        var fields = opts.fields || null;
        var fieldEls = [];

    function buildBody(flds) {
        return flds.map(function(f, i) {
            var inputId = id + '-f-' + i;
            var input;
            // 'toggle' is a checkbox rendered as a switch on the standalone
            // /settings page; here it's just a checkbox — same value shape.
            if (f.type === 'boolean' || f.type === 'toggle') {
                input = '<label class="sf-checkbox"><input type="checkbox" id="' + inputId + '"> <span>' + EOS_UI.esc(f.label) + '</span></label>';
                return '<div class="sf-group">' + input + (f.hint ? '<div class="sf-hint">' + EOS_UI.esc(f.hint) + '</div>' : '') + '</div>';
            }
            if (f.type === 'select') {
                var opts2 = (f.options || []).map(function(o) {
                    var val = (typeof o === 'string') ? o : o.value;
                    var lab = (typeof o === 'string') ? o : (o.label || o.value);
                    return '<option value="' + EOS_UI.escAttr(val) + '">' + EOS_UI.esc(lab) + '</option>';
                }).join('');
                input = '<select class="sf-select" id="' + inputId + '">' + opts2 + '</select>';
            } else if (f.type === 'textarea') {
                input = '<textarea class="sf-textarea" id="' + inputId + '" rows="3"' + (f.placeholder ? ' placeholder="' + EOS_UI.escAttr(f.placeholder) + '"' : '') + '></textarea>';
            } else {
                var t = (f.type === 'number') ? 'number' : (f.type === 'password' ? 'password' : 'text');
                input = '<input type="' + t + '" class="sf-input" id="' + inputId + '"' + (f.placeholder ? ' placeholder="' + EOS_UI.escAttr(f.placeholder) + '"' : '') + (f.min != null ? ' min="' + f.min + '"' : '') + (f.max != null ? ' max="' + f.max + '"' : '') + '>';
            }
            return '<div class="sf-group">' +
                '<label class="sf-label" for="' + inputId + '">' + EOS_UI.esc(f.label) + '</label>' +
                input +
                (f.hint ? '<div class="sf-hint">' + EOS_UI.esc(f.hint) + '</div>' : '') +
            '</div>';
        }).join('');
    }

        var close = function() { panel.classList.remove('open'); };

        // Render + wire. Idempotent: re-running replaces the body, so the lazy
        // manifest path can build after construction without leaking handlers.
        function render() {
            panel.innerHTML =
                '<div class="sp-head">' +
                    '<h3>' + EOS_UI.esc(opts.title || 'Settings') + '</h3>' +
                    '<button class="eos-btn-sm eos-btn-ghost" data-act="close" title="Close without saving">Close</button>' +
                '</div>' +
                '<div class="sp-body">' +
                    (fields.length ? buildBody(fields)
                                   : '<div class="sf-hint">This app declares no settings.</div>') +
                '</div>' +
                '<div class="sp-foot">' +
                    '<button class="eos-btn-sm eos-btn-ghost" data-act="close" title="Discard changes and close">Cancel</button>' +
                    '<button class="eos-btn-sm eos-btn-primary" data-act="save" title="Save these settings">Save</button>' +
                '</div>';
            panel.querySelectorAll('[data-act="close"]').forEach(function(b) { b.onclick = close; });
            panel.querySelector('[data-act="save"]').onclick = save;
            fieldEls = fields.map(function(_, i) { return document.getElementById(id + '-f-' + i); });
        }

        // Fetch this app's manifest schema once. Fails soft to an empty panel
        // rather than throwing out of the ⚙ click handler.
        var ensured = false;
        var ensureFields = async function() {
            if (ensured) return;
            ensured = true;
            if (!fields) {
                try {
                    var r = await EOS.api('/settings/api/schema');
                    var sections = (r && r.sections) || [];
                    var mine = null;
                    for (var i = 0; i < sections.length; i++) {
                        if (sections[i].app_id === opts.app) { mine = sections[i]; break; }
                    }
                    fields = (mine && mine.settings) || [];
                } catch(e) {
                    fields = [];
                }
            }
            render();
        };

        var load = async function() {
            try {
                var r = await EOS.api('/settings/api/config');
                var s = (r && r.settings) || {};
                fields.forEach(function(f, i) {
                    var el = fieldEls[i];
                    var v = (s[f.key] != null) ? s[f.key] : f.default;
                    if (f.type === 'boolean' || f.type === 'toggle') el.checked = !!v;
                    else el.value = (v == null ? '' : v);
                });
            } catch(e) {
                fields.forEach(function(f, i) {
                    var el = fieldEls[i];
                    if (f.type === 'boolean' || f.type === 'toggle') el.checked = !!f.default;
                    else el.value = (f.default == null ? '' : f.default);
                });
            }
        };

        var save = async function() {
            var payload = {};
            for (var i = 0; i < fields.length; i++) {
                var f = fields[i], el = fieldEls[i];
                if (f.type === 'boolean' || f.type === 'toggle') {
                    payload[f.key] = el.checked;
                } else if (f.type === 'number') {
                    var n = parseFloat(el.value);
                    if (el.value !== '' && isNaN(n)) { EOS_UI.toast(f.label + ' must be a number', false); return; }
                    payload[f.key] = (el.value === '' ? null : n);
                } else {
                    payload[f.key] = el.value;
                }
            }
            try {
                var r = await EOS.post('/settings/api/set-bulk', payload);
                if (r && r.error) { EOS_UI.toast(r.error, false); return; }
                EOS_UI.toast('Settings saved');
                close();
                if (opts.onSave) opts.onSave(payload);
            } catch(e) {
                EOS_UI.toast('Failed to save settings', false);
            }
        };

        document.body.appendChild(panel);
        // Inline `fields` render eagerly (unchanged for every existing caller);
        // manifest-derived panels render on first open, once the schema lands.
        if (fields) { ensured = true; render(); }

        return {
            open: async function() { await ensureFields(); await load(); panel.classList.add('open'); },
            close: close,
            panel: panel,
        };
    },

    // --- Hash-based deep-link routing (shared) ---
    // Usage:
    //   var route = EOS_UI.hashRoute({
    //     onShow: function(id) { showDetail(id); },    // called when hash set
    //     onHide: function() { hideDetailDom(); },     // called when hash empty
    //   });
    //   // In your showDetail(id): route.set(id);
    //   // In your hideDetail():   route.clear();
    //   // On page init:           route.init();       // reads current hash
    hashRoute: function(opts) {
        var onShow = opts.onShow || function(){};
        var onHide = opts.onHide || function(){};

        var read = function() {
            return location.hash ? decodeURIComponent(location.hash.slice(1)) : '';
        };
        var apply = function() {
            var id = read();
            if (id) onShow(id); else onHide();
        };

        if (EOS_UI._hashRouteListener) {
            window.removeEventListener('popstate', EOS_UI._hashRouteListener);
        }
        EOS_UI._hashRouteListener = apply;
        window.addEventListener('popstate', apply);

        return {
            // set(id) re-fires onShow — right for navigation-style callers
            // (onclick="_route.set(id)" where onShow does the render).
            // set(id, {silent: true}) updates the hash WITHOUT re-firing —
            // use it when calling from INSIDE your showDetail(id), where the
            // default runs the whole detail render (and its fetches) twice.
            set: function(id, opts) {
                if (!id || read() === id) return;
                history.pushState({eosId: id}, '', '#' + encodeURIComponent(id));
                if (!(opts && opts.silent)) apply();
            },
            clear: function() {
                if (!location.hash) return;
                history.pushState({}, '', location.pathname + location.search);
                apply();
            },
            init: function() { apply(); },
            current: read,
        };
    },

    // ── Test Panel ──────────────────────────────────────────
    //   EOS_UI.testPanel({id, app_id, title?, onComplete?})
    //   Returns {open(), close(), panel}
    //   Slide-out panel showing per-test pass/fail results for an app.
    testPanel: function(opts) {
        var id = opts.id || 'eos-test-panel';
        var appId = opts.app_id || '';
        var title = opts.title || 'Tests';
        var onComplete = opts.onComplete || null;

        // Remove existing panel with same id
        var old = document.getElementById(id);
        if (old) old.remove();

        var panel = document.createElement('div');
        panel.id = id;
        panel.className = 'eos-settings-panel eos-test-panel';
        panel.innerHTML =
            '<div class="sp-head"><span>' + EOS_UI.esc(title) + '</span><span class="sp-close" style="cursor:pointer">&times;</span></div>' +
            '<div class="sp-body">' +
              '<div class="tp-summary"></div>' +
              '<div class="tp-actions" style="display:flex;gap:8px;align-items:center;margin:10px 0">' +
                '<button class="tp-run-btn" title="Run this app\'s test suite" style="padding:6px 16px;border-radius:4px;background:var(--accent);color:#000;border:none;cursor:pointer;font-weight:600">Run Tests</button>' +
                '<input class="tp-filter" placeholder="-k filter (optional)" style="flex:1;padding:5px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg-input);color:var(--text)">' +
                '<span class="tp-status" style="font-size:12px;color:var(--text-muted)"></span>' +
              '</div>' +
              '<div class="tp-test-list"></div>' +
              '<details class="tp-output-wrap" style="margin-top:10px"><summary style="cursor:pointer;font-size:12px;color:var(--text-muted)">Raw output</summary><pre class="tp-output" style="max-height:300px;overflow:auto;font-size:11px;background:var(--bg-surface);color:var(--text);border:1px solid var(--border);padding:8px;border-radius:4px;white-space:pre-wrap"></pre></details>' +
            '</div>' +
            '<div class="sp-foot"><button class="tp-close-btn" title="Close the test panel" style="padding:6px 16px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer">Close</button></div>';
        document.body.appendChild(panel);

        var summaryEl = panel.querySelector('.tp-summary');
        var listEl = panel.querySelector('.tp-test-list');
        var outputEl = panel.querySelector('.tp-output');
        var statusEl = panel.querySelector('.tp-status');
        var runBtn = panel.querySelector('.tp-run-btn');
        var filterInput = panel.querySelector('.tp-filter');

        panel.querySelector('.sp-close').onclick = function() { close(); };
        panel.querySelector('.tp-close-btn').onclick = function() { close(); };
        runBtn.onclick = function() { runTests(); };

        function close() { panel.classList.remove('open'); }

        function renderSummary(summary) {
            if (!summary) { summaryEl.innerHTML = '<div style="color:var(--text-muted);padding:8px 0">Never run</div>'; return; }
            var p = summary.passed || 0, f = summary.failed || 0, e = summary.errors || 0, s = summary.skipped || 0;
            var total = p + f + e + s;
            var allPass = f === 0 && e === 0;
            summaryEl.innerHTML =
                '<div style="display:flex;gap:12px;align-items:center;padding:8px 0">' +
                  '<span style="font-size:24px;font-weight:700;color:' + (allPass ? 'var(--accent)' : '#f44') + '">' + (allPass ? p + ' PASS' : f + ' FAIL') + '</span>' +
                  '<span style="color:var(--text-muted);font-size:13px">' + total + ' tests' + (s ? ', ' + s + ' skipped' : '') + '</span>' +
                  (summary.wall_time ? '<span style="color:var(--text-muted);font-size:12px">' + summary.wall_time + 's</span>' : '') +
                  (summary.timestamp ? '<span style="color:var(--text-muted);font-size:11px">' + summary.timestamp + '</span>' : '') +
                '</div>';
        }

        function renderTests(tests) {
            if (!tests || !tests.length) { listEl.innerHTML = ''; return; }
            var html = '';
            var currentClass = '';
            tests.forEach(function(t) {
                // Group by class: TestFoo::test_bar → header "TestFoo"
                var parts = t.name.split('::');
                var cls = parts.length > 1 ? parts[0] : '';
                var testName = parts.length > 1 ? parts.slice(1).join('::') : t.name;
                if (cls && cls !== currentClass) {
                    currentClass = cls;
                    html += '<div style="font-size:11px;font-weight:600;color:var(--text-muted);margin:8px 0 2px;border-bottom:1px solid var(--border);padding-bottom:2px">' + EOS_UI.esc(cls) + '</div>';
                }
                var color = t.status === 'PASSED' ? '#0f8' : t.status === 'FAILED' ? '#f44' : '#888';
                var dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + color + ';margin-right:6px"></span>';
                html += '<div style="font-size:12px;padding:2px 0;display:flex;align-items:center">' + dot + '<span>' + EOS_UI.esc(testName) + '</span></div>';
            });
            listEl.innerHTML = html;
        }

        function runTests() {
            runBtn.disabled = true;
            statusEl.textContent = 'Running...';
            listEl.innerHTML = '';
            outputEl.textContent = '';
            var body = {app_id: appId, timeout: 300};
            var f = filterInput.value.trim();
            if (f) body.filter = f;
            EOS.post('/tests/api/run-app', body).then(function(res) {
                runBtn.disabled = false;
                if (res.error) { statusEl.textContent = 'Error: ' + res.error; return; }
                statusEl.textContent = '';
                renderSummary(res.summary);
                renderTests(res.tests || []);
                outputEl.textContent = res.output || '';
                if (onComplete) onComplete(res);
            }).catch(function(e) {
                runBtn.disabled = false;
                statusEl.textContent = 'Failed: ' + e.message;
            });
        }

        async function open() {
            panel.classList.add('open');
            // Load last-run from history
            try {
                var history = await EOS.api('/tests/api/history');
                // Find this app's test file in history
                var keys = Object.keys(history);
                var appKey = keys.find(function(k) { return k.indexOf('test_' + appId.replace(/-/g, '_')) >= 0; });
                if (appKey) {
                    renderSummary(history[appKey]);
                } else {
                    renderSummary(null);
                }
            } catch(e) {
                renderSummary(null);
            }
        }

        return { open: open, close: close, panel: panel };
    },

    // === Interactive Components ===

    /** Render a filter bar with pill buttons. Returns {setActive, getActive}. */
    // categories: [{key, label, count?}]. Pass includeAll:false in opts to
    // suppress the leading "All" pill; pass initial to override default active.
    filterBar: function(targetId, categories, onChange, opts) {
        var el = document.getElementById(targetId);
        if (!el) return {};
        opts = opts || {};
        var includeAll = opts.includeAll !== false;
        var active = opts.initial || (includeAll ? 'all' : (categories[0] && categories[0].key));
        function render() {
            var pills = (includeAll ? [{key:'all', label: opts.allLabel || 'All'}] : []).concat(categories);
            el.innerHTML = pills.map(function(c) {
                var count = (c.count != null)
                    ? '<span class="eos-filter-pill-count">' + EOS_UI.esc(String(c.count)) + '</span>'
                    : '';
                return '<button class="eos-filter-pill' + (c.key === active ? ' active' : '') +
                    '" data-key="' + EOS_UI.escAttr(c.key) + '">' + EOS_UI.esc(c.label) + count + '</button>';
            }).join('');
            el.querySelectorAll('.eos-filter-pill').forEach(function(btn) {
                btn.onclick = function() {
                    active = btn.dataset.key;
                    render();
                    if (onChange) onChange(active);
                };
            });
        }
        render();
        return {
            setActive: function(key) { active = key; render(); },
            getActive: function() { return active; },
            update: function(newCategories) { categories = newCategories; render(); },
        };
    },

    /** Render a filterable card grid. Returns {update, filter}. */
    cardGrid: function(targetId, items, opts) {
        var el = document.getElementById(targetId);
        if (!el) return {};
        opts = opts || {};
        var renderCard = opts.render; // function(item, index) -> HTML string
        var onClick = opts.onClick;   // function(item)
        var currentFilter = null;
        var currentTagFilter = null;

        function render() {
            el.classList.add('filtering');
            setTimeout(function() {
                var filtered = items;
                if (currentFilter && currentFilter !== 'all') {
                    filtered = filtered.filter(function(item) {
                        return item.category === currentFilter || item.categoryGroup === currentFilter;
                    });
                }
                if (currentTagFilter) {
                    filtered = filtered.filter(function(item) {
                        return item.tags && item.tags.indexOf(currentTagFilter) !== -1;
                    });
                }
                el.innerHTML = filtered.map(function(item, i) {
                    return renderCard ? renderCard(item, i) : '<div class="eos-icard">' + EOS_UI.esc(item.title || '') + '</div>';
                }).join('');
                el.classList.remove('filtering');
                if (onClick) {
                    el.querySelectorAll('.eos-icard').forEach(function(card, i) {
                        card.onclick = function() { onClick(filtered[i]); };
                    });
                }
            }, 150);
        }
        render();
        return {
            update: function(newItems) { items = newItems; render(); },
            filter: function(category) { currentFilter = category; render(); },
            filterTag: function(tag) { currentTagFilter = (currentTagFilter === tag) ? null : tag; render(); },
            getTagFilter: function() { return currentTagFilter; }
        };
    },

    /** Create a slide-up detail panel. Returns {open, close, setContent}. */
    slidePanel: function(opts) {
        opts = opts || {};
        var panel = document.createElement('div');
        panel.className = 'eos-slide-panel';
        panel.innerHTML = '<div class="eos-slide-panel-backdrop"></div>' +
            '<button class="eos-slide-panel-close" title="Close panel (Esc)">ESC</button>' +
            '<div class="eos-slide-panel-sheet"><div class="eos-slide-panel-body"></div></div>';
        document.body.appendChild(panel);
        var body = panel.querySelector('.eos-slide-panel-body');
        var closeBtn = panel.querySelector('.eos-slide-panel-close');
        var backdrop = panel.querySelector('.eos-slide-panel-backdrop');

        function close() {
            panel.classList.remove('open');
            document.body.style.overflow = '';
            if (opts.onClose) opts.onClose();
        }
        closeBtn.onclick = close;
        backdrop.onclick = close;
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && panel.classList.contains('open')) close();
        });

        return {
            open: function(html) {
                body.innerHTML = html || '';
                panel.classList.add('open');
                document.body.style.overflow = 'hidden';
                panel.querySelector('.eos-slide-panel-sheet').scrollTop = 0;
                if (opts.onOpen) opts.onOpen();
            },
            close: close,
            setContent: function(html) { body.innerHTML = html; }
        };
    },

    /** Render a tag cloud with frequency-based sizing. Returns {setActive}. */
    tagCloud: function(targetId, tags, onClick) {
        // tags: [{name, count}] or {name: count}
        var el = document.getElementById(targetId);
        if (!el) return {};
        var tagList = Array.isArray(tags) ? tags :
            Object.keys(tags).map(function(k) { return {name: k, count: tags[k]}; });
        tagList.sort(function(a, b) { return a.name.localeCompare(b.name); });
        var maxCount = Math.max.apply(null, tagList.map(function(t) { return t.count; })) || 1;
        var active = null;

        function render() {
            el.innerHTML = tagList.map(function(t) {
                var size = 11 + (t.count / maxCount) * 7;
                return '<span class="eos-tag-cloud-item' + (active === t.name ? ' active' : '') +
                    '" data-tag="' + EOS_UI.escAttr(t.name) + '" style="font-size:' + size + 'px">' +
                    EOS_UI.esc(t.name) + '</span>';
            }).join('');
            el.querySelectorAll('.eos-tag-cloud-item').forEach(function(item) {
                item.onclick = function() {
                    active = (active === item.dataset.tag) ? null : item.dataset.tag;
                    render();
                    if (onClick) onClick(active);
                };
            });
        }
        render();
        return { setActive: function(tag) { active = tag; render(); } };
    },

    /** Set up scroll-tracking nav that highlights active section. */
    scrollNav: function(navId, sectionIds) {
        var nav = document.getElementById(navId);
        if (!nav) return;
        var links = nav.querySelectorAll('a[href^="#"]');
        var sections = sectionIds.map(function(id) { return document.getElementById(id); }).filter(Boolean);
        function update() {
            var scrollY = window.scrollY + 100;
            var current = '';
            sections.forEach(function(sec) {
                if (sec.offsetTop <= scrollY) current = sec.id;
            });
            links.forEach(function(a) {
                a.classList.toggle('active', a.getAttribute('href') === '#' + current);
            });
        }
        var ticking = false;
        window.addEventListener('scroll', function() {
            if (!ticking) { requestAnimationFrame(function() { update(); ticking = false; }); ticking = true; }
        });
        // Smooth scroll
        links.forEach(function(a) {
            a.addEventListener('click', function(e) {
                e.preventDefault();
                var target = document.querySelector(a.getAttribute('href'));
                if (target) target.scrollIntoView({ behavior: 'smooth' });
            });
        });
        update();
    },

    /** Set up IntersectionObserver reveal animations for .eos-reveal elements. */
    reveal: function(selector) {
        var els = document.querySelectorAll(selector || '.eos-reveal');
        if (!els.length) return;
        var observer = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('revealed');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });
        els.forEach(function(el) { observer.observe(el); });
    },

    // --- Cloud consent UX --------------------------------------------------
    // Show the consent modal for a pending cloud-provider request.
    // opts: {id, provider, capability, data_summary, findings}
    // Calls POST /api/cloud/consent with {id, approved, remember}.
    cloudConsent: function(opts) {
        var title = 'Cloud provider: ' + EOS_UI.esc(opts.provider || '?');
        var cap = EOS_UI.esc(opts.capability || '');
        var summary = opts.data_summary ? EOS_UI.esc(opts.data_summary) : '';
        var findings = Array.isArray(opts.findings) ? opts.findings : [];
        var findingsHtml = '';
        if (findings.length) {
            var items = findings.map(function(f) {
                return '<li><b>' + EOS_UI.esc(f.pattern || '?') + '</b>' +
                       (f.preview ? ' — <code>' + EOS_UI.esc(f.preview) + '</code>' : '') +
                       '</li>';
            }).join('');
            findingsHtml =
                '<div style="margin:0 0 12px;padding:10px 12px;border-radius:6px;' +
                'background:rgba(210,153,34,0.10);border:1px solid rgba(210,153,34,0.45);font-size:12px">' +
                    '<div style="font-weight:600;color:#d29922;margin-bottom:4px">' +
                        '&#9888; Local scan detected ' + findings.length + ' potential ' +
                        (findings.length === 1 ? 'match' : 'matches') +
                    '</div>' +
                    '<ul style="margin:4px 0 0;padding-left:18px">' + items + '</ul>' +
                '</div>';
        }
        var body =
            '<p style="margin:0 0 12px;font-size:13px;color:var(--text-secondary)">' +
                'This call will leave your machine and be sent to <b>' + EOS_UI.esc(opts.provider || '') + '</b>' +
                (cap ? ' (capability: <code>' + cap + '</code>)' : '') + '.' +
            '</p>' +
            findingsHtml +
            (summary ?
                '<details' + (findings.length ? ' open' : '') + ' style="margin:0 0 16px">' +
                '<summary style="cursor:pointer;font-size:12px;color:var(--text-secondary)">Preview data</summary>' +
                '<pre style="margin:8px 0 0;padding:8px;background:var(--bg-card,#0d1117);border:1px solid var(--border,#30363d);border-radius:6px;font-size:12px;white-space:pre-wrap;max-height:320px;overflow:auto">' +
                summary + '</pre></details>'
                : '') +
            '<label style="display:flex;align-items:center;gap:6px;margin:0 0 16px;font-size:13px;cursor:pointer">' +
                '<input type="checkbox" id="eos-consent-remember" checked> ' +
                'Remember for this session' +
            '</label>' +
            '<div class="eos-form-actions">' +
                '<button class="eos-btn" id="eos-consent-deny" title="Block this cloud call">Deny</button>' +
                '<button class="eos-btn eos-btn-primary" id="eos-consent-approve" title="Allow this cloud call">Approve</button>' +
            '</div>';
        EOS_UI.modal({title: title, body: body, width: '520px'});
        var send = function(approved) {
            var remember = document.getElementById('eos-consent-remember');
            var payload = {
                id: opts.id,
                approved: approved,
                remember: remember ? !!remember.checked : true,
            };
            EOS_UI.closeModal();
            fetch('/api/cloud/consent', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            }).catch(function() {});
        };
        var approveBtn = document.getElementById('eos-consent-approve');
        var denyBtn = document.getElementById('eos-consent-deny');
        if (approveBtn) approveBtn.onclick = function() { send(true); };
        if (denyBtn) denyBtn.onclick = function() { send(false); };
    },

    // Small badge indicating which provider handled a request.
    // opts: {provider, is_cloud}
    // Returns HTML string (use as innerHTML or template fragment).
    providerBadge: function(opts) {
        if (!opts || !opts.provider) return '';
        var cls = opts.is_cloud ? 'eos-badge eos-provider-cloud' : 'eos-badge eos-provider-local';
        var icon = opts.is_cloud ? '&#9729;' : '&#8962;';  // cloud / home
        return '<span class="' + cls + '" title="' +
            (opts.is_cloud ? 'Cloud provider — data left your machine' : 'Local provider — data stayed on your machine') +
            '">' + icon + ' ' + EOS_UI.esc(opts.provider) + '</span>';
    },

    // --- Agent tool-permission UX ------------------------------------------
    // Show a permission modal for a pending agent tool call.
    // opts: {id, session_id, tool, input, summary}
    // Calls POST /agent/api/permission/{id}/{approve|deny} with {scope}.
    agentPermission: function(opts) {
        var title = 'Tool permission: ' + EOS_UI.esc(opts.tool || '?');
        var summary = opts.summary || '';
        var inputJson = '';
        try {
            inputJson = JSON.stringify(opts.input || {}, null, 2);
        } catch (e) { inputJson = ''; }
        var body =
            '<p style="margin:0 0 12px;font-size:13px;color:var(--text-secondary)">' +
                'The agent is requesting to run <b>' + EOS_UI.esc(opts.tool || '') + '</b>.' +
            '</p>' +
            (summary ?
                '<div style="margin:0 0 12px;padding:8px 10px;border-radius:6px;' +
                'background:rgba(56,139,253,0.08);border:1px solid rgba(56,139,253,0.35);' +
                'font-family:var(--font-mono,monospace);font-size:12px;white-space:pre-wrap">' +
                EOS_UI.esc(summary) + '</div>' : '') +
            (inputJson ?
                '<details style="margin:0 0 16px">' +
                '<summary style="cursor:pointer;font-size:12px;color:var(--text-secondary)">Full input</summary>' +
                '<pre style="margin:8px 0 0;padding:8px;background:var(--bg-card,#0d1117);border:1px solid var(--border,#30363d);border-radius:6px;font-size:12px;white-space:pre-wrap;max-height:240px;overflow:auto">' +
                EOS_UI.esc(inputJson) + '</pre></details>' : '') +
            '<label style="display:flex;align-items:center;gap:6px;margin:0 0 16px;font-size:13px;cursor:pointer">' +
                '<input type="checkbox" id="eos-agent-perm-session"> ' +
                'Approve for the rest of this session' +
            '</label>' +
            '<div class="eos-form-actions">' +
                '<button class="eos-btn" id="eos-agent-perm-deny" title="Refuse this tool call">Deny</button>' +
                '<button class="eos-btn eos-btn-primary" id="eos-agent-perm-approve" title="Allow this tool call">Approve</button>' +
            '</div>';
        EOS_UI.modal({title: title, body: body, width: '520px'});
        var send = function(approved) {
            var remember = document.getElementById('eos-agent-perm-session');
            var scope = (remember && remember.checked) ? 'session' : 'once';
            EOS_UI.closeModal();
            var action = approved ? 'approve' : 'deny';
            fetch('/agent/api/permission/' + encodeURIComponent(opts.id) + '/' + action, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({scope: scope}),
            }).catch(function() {});
        };
        var approveBtn = document.getElementById('eos-agent-perm-approve');
        var denyBtn = document.getElementById('eos-agent-perm-deny');
        if (approveBtn) approveBtn.onclick = function() { send(true); };
        if (denyBtn) denyBtn.onclick = function() { send(false); };
    },

    // Install a global WebSocket listener that shows the consent modal
    // whenever a `cloud:consent_requested` event arrives. Call once per page.
    initCloudConsent: function() {
        if (EOS_UI._cloudConsentInit) return;
        EOS_UI._cloudConsentInit = true;
        if (typeof EmptyOSRealtime === 'undefined') return;
        if (!window.eosRealtime) {
            try {
                window.eosRealtime = new EmptyOSRealtime();
                window.eosRealtime.connect();
            } catch (e) { return; }
        }
        window.eosRealtime.on('cloud:consent_requested', function(data) {
            EOS_UI.cloudConsent(data || {});
        });
    },

    // --- Demo banner ------------------------------------------------------
    // Fetch demo status and, if enabled, prepend a dismissible banner to the
    // top of the page. Runs once per page load on any app.
    initDemoBanner: function() {
        if (EOS_UI._demoBannerInit) return;
        EOS_UI._demoBannerInit = true;
        fetch('/api/demo/status').then(function(r) { return r.ok ? r.json() : null; })
            .then(function(j) {
                if (!j || !j.enabled || !j.banner) return;
                if (document.getElementById('eos-demo-banner')) return;
                var bar = document.createElement('div');
                bar.id = 'eos-demo-banner';
                bar.className = 'eos-demo-banner';
                var links = '';
                if (j.about_url) {
                    links += ' <a class="eos-demo-banner-link" href="' + EOS_UI.escAttr(j.about_url) +
                             '" target="_blank" rel="noopener">About &rarr;</a>';
                }
                if (j.install_url) {
                    links += ' <a class="eos-demo-banner-link" href="' + EOS_UI.escAttr(j.install_url) +
                             '" target="_blank" rel="noopener">Source &rarr;</a>';
                }
                bar.innerHTML =
                    '<span class="eos-demo-banner-text">' + EOS_UI.esc(j.banner) + '</span>' + links;
                document.body.insertBefore(bar, document.body.firstChild);
                document.body.classList.add('eos-demo-active');
            })
            .catch(function() {});
    },

    // --- AI (think) offline banner ----------------------------------------
    // Fetch think-capability status. If no provider is available (or offline
    // is simulated), render a single system-level banner so users know
    // enhancement features will be unavailable. CRUD paths keep working.
    // Pages can listen for the `eos:think-status` event if they want to
    // disable/enable their own AI buttons.
    initThinkStatus: function() {
        if (EOS_UI._thinkStatusInit) return;
        EOS_UI._thinkStatusInit = true;

        var render = function(j) {
            var existing = document.getElementById('eos-think-banner');
            if (!j || j.available) {
                if (existing) existing.remove();
                document.body.classList.remove('eos-think-offline');
                return;
            }
            var reason = j.reason || 'AI providers are offline';
            var text = j.simulated
                ? 'AI is simulated offline. Enhancement features will show this banner instead of results.'
                : 'AI is offline — ' + reason + '. CRUD features still work; AI-powered buttons are disabled.';
            if (existing) {
                var span = existing.querySelector('.eos-think-banner-text');
                if (span) span.textContent = text;
                return;
            }
            var bar = document.createElement('div');
            bar.id = 'eos-think-banner';
            bar.className = 'eos-think-banner';
            bar.innerHTML =
                '<span class="eos-think-banner-icon" aria-hidden="true">&#9888;</span>' +
                '<span class="eos-think-banner-text">' + EOS_UI.esc(text) + '</span>';
            document.body.insertBefore(bar, document.body.firstChild);
            document.body.classList.add('eos-think-offline');
        };

        var check = function() {
            fetch('/api/think-status').then(function(r) { return r.ok ? r.json() : null; })
                .then(function(j) {
                    render(j);
                    try {
                        window.dispatchEvent(new CustomEvent('eos:think-status', { detail: j || { available: false } }));
                    } catch (e) {}
                })
                .catch(function() {});
        };

        check();
        // Re-check periodically — settings change, providers come back, etc.
        setInterval(check, 30000);
        // Re-check when the tab regains focus so banner reacts fast after toggling the setting.
        window.addEventListener('focus', check);
    },

    // PWA install banner — dismissible. Pages that want it call EOS_UI.pwaInstall.mount().
    // Non-destructive: already-installed or permanently-dismissed users see nothing.
    pwaInstall: {
        _DISMISS_KEY: 'eos:pwa-install-dismissed',
        _isStandalone: function() {
            return window.matchMedia && window.matchMedia('(display-mode: standalone)').matches
                || window.navigator.standalone === true;
        },
        _isIOS: function() {
            var ua = navigator.userAgent || '';
            return /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
        },
        _dismissed: function() {
            try { return localStorage.getItem(this._DISMISS_KEY) === '1'; } catch(e) { return false; }
        },
        _dismiss: function() {
            try { localStorage.setItem(this._DISMISS_KEY, '1'); } catch(e) {}
        },
        mount: function() {
            var self = this;
            if (self._isStandalone() || self._dismissed()) return;

            var render = function(mode) {
                if (document.getElementById('eos-pwa-banner')) return;
                var bar = document.createElement('div');
                bar.id = 'eos-pwa-banner';
                bar.className = 'eos-pwa-banner';
                var msg = mode === 'ios'
                    ? 'Install EmptyOS: tap Share, then "Add to Home Screen".'
                    : 'Install EmptyOS as an app for faster access.';
                var actionBtn = mode === 'ios'
                    ? ''
                    : '<button class="eos-pwa-install-btn" data-action="install" title="Install EmptyOS as an app on this device">Install</button>';
                bar.innerHTML =
                    '<span class="eos-pwa-banner-msg">' + msg + '</span>' +
                    actionBtn +
                    '<button class="eos-pwa-dismiss-btn" data-action="dismiss" aria-label="Dismiss" title="Dismiss this banner">&times;</button>';
                bar.addEventListener('click', function(e) {
                    var action = e.target.getAttribute('data-action');
                    if (action === 'install' && window._eosInstallPromptEvent) {
                        window._eosInstallPromptEvent.prompt();
                        window._eosInstallPromptEvent.userChoice.finally(function() {
                            window._eosInstallPromptEvent = null;
                            bar.remove();
                        });
                    } else if (action === 'dismiss') {
                        self._dismiss();
                        bar.remove();
                    }
                });
                document.body.insertBefore(bar, document.body.firstChild);
            };

            // iOS: no beforeinstallprompt — render hint immediately for non-standalone Safari.
            if (self._isIOS()) {
                render('ios');
                return;
            }
            // Other browsers: only render once the install prompt is ready.
            if (window._eosInstallPromptEvent) {
                render('prompt');
            } else {
                window.addEventListener('eos:pwa-installable', function() { render('prompt'); }, { once: true });
            }
        },
    },

    // --- Wikilink autocomplete ---
    // Attach to any <textarea> or <input>: typing `[[` opens a floating picker
    // listing matching vault notes. ↑/↓ navigates, Enter/Tab inserts the
    // selected target as `[[Title]]`, Esc closes.
    //
    // Usage: EOS_UI.wikiLinkInput(textarea, {kinds: ['kb','note']});
    //   kinds   — array of tags to restrict suggestions (default: all notes)
    //   onPick  — optional callback(picked) → string to insert (default `[[Title]]`)
    //   limit   — max suggestions (default 10)
    _wlPopup: null,
    _wlState: null,

    wikiLinkInput: function(el, opts) {
        opts = opts || {};
        if (!el || el._eosWikiAttached) return;
        el._eosWikiAttached = true;
        var self = EOS_UI;

        el.addEventListener('input', function() { self._wlMaybeOpen(el, opts); });
        el.addEventListener('keydown', function(e) {
            if (!self._wlState || self._wlState.el !== el) return;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                self._wlMove(1);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                self._wlMove(-1);
            } else if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault();
                self._wlAccept();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                self._wlClose();
            }
        });
        el.addEventListener('blur', function() {
            // Delay so a click on the popup can land before close.
            setTimeout(function() { self._wlClose(); }, 160);
        });
    },

    _wlScanAtCursor: function(el) {
        // Return {start, query} if cursor is inside an unclosed `[[…`, else null.
        var pos = el.selectionStart;
        if (pos == null) return null;
        var v = el.value || '';
        var before = v.slice(0, pos);
        var open = before.lastIndexOf('[[');
        if (open < 0) return null;
        var afterOpen = before.slice(open + 2);
        if (/[\]\n]/.test(afterOpen)) return null;
        return { start: open, query: afterOpen };
    },

    _wlMaybeOpen: function(el, opts) {
        var scan = EOS_UI._wlScanAtCursor(el);
        if (!scan) { EOS_UI._wlClose(); return; }
        var kinds = (opts.kinds || []).join(',');
        var url = '/link/api/suggest?limit=' + (opts.limit || 10) +
                  '&q=' + encodeURIComponent(scan.query) +
                  (kinds ? '&kinds=' + encodeURIComponent(kinds) : '');
        fetch(url).then(function(r) { return r.json(); }).then(function(data) {
            var items = (data && data.suggestions) || [];
            EOS_UI._wlRender(el, items, scan.start, scan.query, opts);
        }).catch(function() { EOS_UI._wlClose(); });
    },

    _wlEnsurePopup: function() {
        if (EOS_UI._wlPopup) return EOS_UI._wlPopup;
        var p = document.createElement('div');
        p.className = 'eos-wl-popup';
        p.setAttribute('role', 'listbox');
        document.body.appendChild(p);
        EOS_UI._wlPopup = p;
        return p;
    },

    _wlRender: function(el, items, start, query, opts) {
        var p = EOS_UI._wlEnsurePopup();
        if (!items.length) { EOS_UI._wlClose(); return; }
        var html = items.map(function(it, i) {
            var k = it.kind ? '<span class="eos-wl-kind">' + EOS_UI.esc(it.kind) + '</span>' : '';
            return '<div class="eos-wl-row' + (i === 0 ? ' active' : '') + '" data-idx="' + i + '">' +
                   k +
                   '<span class="eos-wl-title">' + EOS_UI.esc(it.title || it.name || '') + '</span>' +
                   '</div>';
        }).join('');
        p.innerHTML = html;
        // Position near caret — fall back to bottom of input rect
        var rect = el.getBoundingClientRect();
        p.style.left = (rect.left + 12) + 'px';
        p.style.top = (rect.bottom + 4) + 'px';
        p.classList.add('open');
        EOS_UI._wlState = { el: el, items: items, active: 0, start: start, query: query, opts: opts };
        p.onclick = function(e) {
            var row = e.target.closest('.eos-wl-row');
            if (!row) return;
            EOS_UI._wlState.active = parseInt(row.dataset.idx, 10) || 0;
            EOS_UI._wlAccept();
        };
        p.onmousedown = function(e) { e.preventDefault(); };  // keep focus
    },

    _wlMove: function(delta) {
        var st = EOS_UI._wlState;
        if (!st) return;
        st.active = (st.active + delta + st.items.length) % st.items.length;
        var p = EOS_UI._wlPopup;
        if (!p) return;
        Array.prototype.forEach.call(p.querySelectorAll('.eos-wl-row'), function(r, i) {
            r.classList.toggle('active', i === st.active);
        });
    },

    _wlAccept: function() {
        var st = EOS_UI._wlState;
        if (!st) return;
        var picked = st.items[st.active];
        if (!picked) { EOS_UI._wlClose(); return; }
        var insert = (st.opts.onPick && st.opts.onPick(picked)) || ('[[' + (picked.title || picked.name) + ']]');
        var el = st.el;
        var pos = el.selectionStart;
        var v = el.value || '';
        // Replace from `[[` through current cursor with the new wikilink
        el.value = v.slice(0, st.start) + insert + v.slice(pos);
        var newPos = st.start + insert.length;
        el.selectionStart = el.selectionEnd = newPos;
        EOS_UI._wlClose();
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.focus();
    },

    _wlClose: function() {
        if (EOS_UI._wlPopup) EOS_UI._wlPopup.classList.remove('open');
        EOS_UI._wlState = null;
    },

    // --- Complete task with note (+ link) ---
    // Opens the "Complete with note" modal for any vault task, POSTs to the
    // task app's shared `/task/api/complete-with-note`, and wires the
    // existing wikilink autocomplete (typing `[[` opens a live vault-note
    // picker) into the note textarea — that's the whole "link it to a
    // relevant note" capability, no separate picker needed.
    //
    // Usage:
    //   EOS_UI.completeTaskWithNote({file, line, text}, {
    //       onSuccess: function(res) { ... },  // default: EOS_UI.toast(...)
    //       onError: function(err) { ... },     // default: EOS_UI.toast(..., false)
    //   });
    //
    // Page-specific flourish (confetti, sound, list reload) belongs in the
    // caller's onSuccess/onError, not here — this stays generic. First
    // consumer: apps/public/core/task/ (promoted from its page-local copy,
    // 2026-07-03). Reachable from any checkbox via right-click/long-press
    // as an alternative to a plain click's instant toggle.
    _completeNoteState: null,

    completeTaskWithNote: function(task, opts) {
        opts = opts || {};
        if (!task || !task.file || !task.line) return;
        EOS_UI._completeNoteState = { file: task.file, line: task.line, text: task.text || '', opts: opts };
        EOS_UI.modal({
            title: 'Complete with note',
            body:
                '<div class="eos-form-group">' +
                    '<label class="eos-form-label" for="eos-complete-note-input">Completion note</label>' +
                    '<textarea id="eos-complete-note-input" class="eos-form-input" rows="3" placeholder="What changed? Type [[ to link a note."></textarea>' +
                    '<div id="eos-complete-note-error" style="display:none;margin-top:6px;font-size:12px;color:var(--danger)"></div>' +
                '</div>' +
                '<div class="eos-form-actions">' +
                    '<button class="eos-btn eos-btn-primary" onclick="EOS_UI._submitCompleteNote()" title="Save the note and mark this task done">Save</button>' +
                '</div>',
            onClose: function() { EOS_UI._completeNoteState = null; },
        });
        setTimeout(function() {
            var input = document.getElementById('eos-complete-note-input');
            if (input) {
                input.focus();
                EOS_UI.wikiLinkInput(input);
            }
        }, 0);
    },

    _submitCompleteNote: function() {
        var st = EOS_UI._completeNoteState;
        if (!st) return;
        var input = document.getElementById('eos-complete-note-input');
        var err = document.getElementById('eos-complete-note-error');
        var note = input ? input.value.trim() : '';
        if (!note) {
            if (err) { err.textContent = 'Write a note before completing.'; err.style.display = 'block'; }
            if (input) input.focus();
            return;
        }
        var opts = st.opts;
        var file = st.file, line = st.line, text = st.text;
        EOS_UI._completeNoteState = null;
        EOS_UI.closeModal();
        EOS.post('/task/api/complete-with-note', { file: file, line: line, text: text, note: note })
            .then(function(res) {
                if (res && res.error) throw new Error(res.error);
                if (opts.onSuccess) opts.onSuccess(res);
                else EOS_UI.toast(res.status === 'noted' ? 'Completion note saved' : 'Task completed with note');
            })
            .catch(function(e) {
                if (opts.onError) opts.onError(e);
                else EOS_UI.toast('Failed to complete task' + (e && e.message ? ': ' + e.message : ''), false);
            });
    },

    // --- Transclusion (kb-blocks, generic vault notes) ---
    // Fetches a block's body via `/kb/api/blocks/<slug>` and renders it inline
    // through EOS_UI.renderMarkdown(). Wraps in a frame with a source link so
    // the reader knows the content lives elsewhere.
    //
    // Usage:
    //   EOS_UI.transclude(mountEl, {slug: 'maxwell-stress'});
    //   EOS_UI.transclude(mountEl, {slug: 'maxwell-stress', section: 'Pitfalls'});
    //   EOS_UI.transclude(mountEl, {slug, depth: 2, onMissing: fn});
    //   depth caps recursive transclusion (default 2; max 3) to prevent cycles.
    //
    // After the KB Notes+Blocks unification, transclude resolves against any
    // kb-tagged note (formerly only kb-blocks). Optional `section` slices the
    // body to a single `## Heading` block, server-side via
    // /kb/api/notes/<slug>/section/<name>.
    transclude: function(mountEl, opts) {
        opts = opts || {};
        var mount = (typeof mountEl === 'string') ? document.getElementById(mountEl) : mountEl;
        if (!mount) return;
        // Accept `slug` directly, or `slug#section` as a shorthand.
        var raw = (opts.slug || '').trim();
        if (!raw) { mount.innerHTML = ''; return; }
        var hashIdx = raw.indexOf('#');
        var slug = hashIdx >= 0 ? raw.slice(0, hashIdx) : raw;
        var section = opts.section || (hashIdx >= 0 ? raw.slice(hashIdx + 1) : '');
        var depth = Math.min(3, Math.max(0, opts.depth == null ? 2 : opts.depth));
        var refLabel = section ? (slug + '#' + section) : slug;
        mount.classList.add('eos-transclude');
        mount.innerHTML = '<div class="eos-transclude-loading">Loading [[' + EOS_UI.esc(refLabel) + ']]…</div>';
        var url = section
            ? '/kb/api/notes/' + encodeURIComponent(slug) + '/section/' + encodeURIComponent(section)
            : '/kb/api/notes/' + encodeURIComponent(slug);
        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(note) {
                if (note.error) {
                    if (opts.onMissing) { opts.onMissing(slug); }
                    mount.innerHTML = '<div class="eos-transclude-missing">⚠ <code>[[' + EOS_UI.esc(refLabel) + ']]</code> not found</div>';
                    return;
                }
                // /api/notes returns body at top-level for the section endpoint, or
                // inside properties.references for the full-note endpoint. Tolerate both.
                var body = note.body || '';
                if (depth <= 0) {
                    body = body.replace(/!\[\[[^\]]+\]\]/g, '<em>(nested transclusion suppressed)</em>');
                }
                var rendered = EOS_UI.renderMarkdown(body);
                var titleLabel = (note.title || slug) + (section ? ' §' + section : '');
                mount.innerHTML =
                    '<div class="eos-transclude-frame">' +
                        '<div class="eos-transclude-head">' +
                            '<a href="/kb/#' + encodeURIComponent(slug) + '" class="eos-transclude-source">📎 ' + EOS_UI.esc(titleLabel) + '</a>' +
                        '</div>' +
                        '<div class="eos-transclude-body">' + rendered + '</div>' +
                    '</div>';
                // Resolve nested ![[slug]] transclusions one level deeper
                if (depth > 0) {
                    var bodyEl = mount.querySelector('.eos-transclude-body');
                    if (bodyEl) {
                        bodyEl.innerHTML = bodyEl.innerHTML.replace(/!\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]/g, function(_, target) {
                            var id = 'tx-' + Math.random().toString(36).slice(2, 9);
                            setTimeout(function() {
                                EOS_UI.transclude(document.getElementById(id), { slug: target.trim(), depth: depth - 1 });
                            }, 0);
                            return '<div id="' + id + '"></div>';
                        });
                    }
                }
            })
            .catch(function(e) {
                mount.innerHTML = '<div class="eos-transclude-missing">⚠ Failed to load <code>[[' + EOS_UI.esc(refLabel) + ']]</code>: ' + EOS_UI.esc(e.message || '') + '</div>';
            });
    },

    // --- KB Popover (floating ⓘ tooltip) ---
    // Anchor an inline ⓘ button on a form field; on click, open a floating
    // popover that fetches a KB note (or one of its `##` sections) and
    // renders the body via EOS_UI.renderMarkdown(). Toggle behaviour: clicking
    // the same anchor again closes the open popover; clicking a different
    // anchor swaps the content.
    //
    // Soft-fails when the kb app is offline / the slug is missing — the
    // popover degrades to "KB lookup unavailable" and the host UI continues.
    //
    // Usage:
    //   <button onclick="EOS_UI.kbPopover('cable-pulling-cof-and-back-tension',
    //     'Coefficient of friction (COF)', this)">ⓘ</button>
    //   EOS_UI.kbPopover(slug, null, anchorEl);                  // whole note
    //   EOS_UI.kbPopover(slug, section, anchorEl, {maxWidth:560});
    //
    // First consumer: apps/personal/cable-pulling/ (ppKbPopover).
    // Second consumer: apps/personal/cable-hdd/. Extracted per CLAUDE.md
    // rule 9 — 2026-05-25.
    kbPopover: function(slug, section, anchorEl, opts) {
        opts = opts || {};
        var maxW = opts.maxWidth || 480;
        var maxH = opts.maxHeight || '60vh';
        var existing = document.getElementById('eos-kb-popover');
        if (existing) {
            var prevSlug = existing.getAttribute('data-slug');
            var prevSection = existing.getAttribute('data-section') || '';
            existing.remove();
            if (prevSlug === slug && prevSection === (section || '')) return;
        }
        var pop = document.createElement('div');
        pop.id = 'eos-kb-popover';
        pop.setAttribute('data-slug', slug);
        pop.setAttribute('data-section', section || '');
        pop.style.cssText = 'position:fixed;z-index:9999;max-width:' + maxW + 'px;max-height:' + maxH + ';overflow:auto;'
            + 'background:var(--bg-card);border:1px solid var(--border);border-radius:8px;'
            + 'padding:12px 14px;font-size:12px;line-height:1.5;color:var(--text);'
            + 'box-shadow:0 8px 24px rgba(0,0,0,0.4)';
        var rect = anchorEl.getBoundingClientRect();
        pop.style.top = Math.min(rect.bottom + 6, window.innerHeight - 360) + 'px';
        pop.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - (maxW + 20))) + 'px';
        var refLabel = '[[' + slug + ']]' + (section ? ' › ' + EOS_UI.esc(section) : '');
        pop.innerHTML = '<div style="opacity:0.6">Loading ' + refLabel + '…</div>';
        document.body.appendChild(pop);

        var url = section
            ? '/kb/api/notes/' + encodeURIComponent(slug) + '/section/' + encodeURIComponent(section)
            : '/kb/api/notes/' + encodeURIComponent(slug);
        fetch(url)
            .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function(data) {
                var body = data.body || data.content || '';
                var title = data.title || slug;
                var head = '<div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:6px">'
                    + '<strong style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:var(--text-muted)">'
                    + EOS_UI.esc(section ? title + ' › ' + section : title) + '</strong>'
                    + '<a href="/kb/#' + encodeURIComponent(slug) + '" target="_blank" '
                    + 'style="font-size:10px;color:var(--accent);text-decoration:none">open in KB →</a>'
                    + '</div>';
                var rendered = EOS_UI.renderMarkdown ? EOS_UI.renderMarkdown(body) : EOS_UI.esc(body);
                pop.innerHTML = head + '<div class="eos-transclude-body">' + rendered + '</div>';
            })
            .catch(function() {
                pop.innerHTML = '<div style="opacity:0.7">KB lookup unavailable. '
                    + 'Note: <code>' + EOS_UI.esc(slug) + '</code></div>';
            });

        setTimeout(function() {
            document.addEventListener('click', function dismissOnce(e) {
                if (!pop.contains(e.target) && e.target !== anchorEl) {
                    pop.remove();
                    document.removeEventListener('click', dismissOnce);
                }
            });
        }, 0);
    },

    // Inline ⓘ affordance — returns HTML for a button that opens kbPopover.
    // Use in template strings:
    //   '<label>Friction μ ' + EOS_UI.kbInfoBtn(slug, 'COF section') + '</label>'
    kbInfoBtn: function(slug, section, opts) {
        opts = opts || {};
        var attr = "EOS_UI.kbPopover('" + EOS_UI.esc(slug) + "', "
            + (section ? "'" + section.replace(/'/g, "\\'") + "'" : "null")
            + ", this)";
        var title = opts.title || ('Open KB note ' + slug);
        return '<button type="button" class="eos-kb-info" onclick="' + attr + '" '
            + 'title="' + EOS_UI.escAttr(title) + '">ⓘ</button>';
    },

    // Warning renderer — handles both legacy string warnings and the structured
    // dict shape used by calculator engines: {code?, message, kb_slug?, kb_section?}.
    // Returns an HTML string. Wrap in your own container; the renderer only
    // produces the inner content.
    //
    //   <div id="warnings"></div>
    //   warnings.innerHTML = list.map(w =>
    //     '<div class="warning-item">' + EOS_UI.kbWarning(w) + '</div>'
    //   ).join('');
    kbWarning: function(w) {
        if (typeof w === 'string') return '⚠ ' + EOS_UI.esc(w);
        var msg = (w && w.message) || String(w);
        var slug = w && w.kb_slug;
        var section = w && w.kb_section;
        if (!slug) return '⚠ ' + EOS_UI.esc(msg);
        var attr = "EOS_UI.kbPopover('" + EOS_UI.esc(slug) + "', "
            + (section ? "'" + section.replace(/'/g, "\\'") + "'" : 'null')
            + ', this)';
        return '⚠ ' + EOS_UI.esc(msg)
            + ' <button type="button" class="eos-kb-why" onclick="' + attr + '" title="Explain this warning from the knowledge base">why?</button>';
    },

    // --- Backlink badges ---
    // Render a row of chip-links from `/link/api/backlinks?title=...` into mountEl.
    // mountEl can be an element or an id string.
    //
    // Usage: EOS_UI.backlinkBadges('my-mount', {target: 'note-title'});
    //   target  — the title to look up backlinks for (required)
    //   max     — cap visible chips (default 12)
    //   empty   — text shown when no backlinks (default: hide mount)
    //   onClick — callback(path) → if returns truthy, default navigation is skipped
    backlinkBadges: function(mountEl, opts) {
        opts = opts || {};
        var mount = (typeof mountEl === 'string') ? document.getElementById(mountEl) : mountEl;
        if (!mount) return;
        var target = (opts.target || '').trim();
        if (!target) { mount.innerHTML = ''; mount.style.display = 'none'; return; }
        var max = opts.max || 12;
        mount.classList.add('eos-bl-row');
        mount.innerHTML = '<span class="eos-bl-loading">Loading backlinks…</span>';
        mount.style.display = '';
        fetch('/link/api/backlinks?title=' + encodeURIComponent(target))
            .then(function(r) { return r.json(); })
            .then(function(items) {
                var list = Array.isArray(items) ? items : (items && items.backlinks) || [];
                if (!list.length) {
                    if (opts.empty) {
                        mount.innerHTML = '<span class="eos-bl-empty">' + EOS_UI.esc(opts.empty) + '</span>';
                    } else {
                        mount.innerHTML = '';
                        mount.style.display = 'none';
                    }
                    return;
                }
                var shown = list.slice(0, max);
                var more = list.length - shown.length;
                var html = '<span class="eos-bl-label">⇇ Linked from</span>' +
                    shown.map(function(p) {
                        var name = String(p).split(/[\\/]/).pop().replace(/\.md$/, '').replace(/-/g, ' ');
                        return '<a class="eos-bl-chip" href="#" data-path="' + EOS_UI.escAttr(p) + '">' +
                               EOS_UI.esc(name) + '</a>';
                    }).join('') +
                    (more > 0 ? '<span class="eos-bl-more">+' + more + ' more</span>' : '');
                mount.innerHTML = html;
                Array.prototype.forEach.call(mount.querySelectorAll('.eos-bl-chip'), function(a) {
                    a.addEventListener('click', function(e) {
                        e.preventDefault();
                        var path = a.dataset.path;
                        var handled = opts.onClick && opts.onClick(path);
                        if (!handled && window.EOS && EOS.viewNote) EOS.viewNote(path);
                    });
                });
            })
            .catch(function() {
                mount.innerHTML = '';
                mount.style.display = 'none';
            });
    },

    // ── aiFormFill — chat-driven form filler ─────────────────────
    //
    //   EOS_UI.aiFormFill({
    //       title: 'Create org',
    //       intro: 'Tell me about the org…',          // first assistant message
    //       schema: [                                  // same shape as formModal
    //           {key:'name', label:'Name', type:'text', required:true},
    //           {key:'kind', label:'Kind', type:'select', options:['team','household','other']},
    //           {key:'mission', label:'Mission', type:'textarea'},
    //       ],
    //       endpoint: '/api/sdk/ai-form-fill',          // optional override
    //       initial: {name: 'Foo'},                     // optional prefill
    //       submitLabel: 'Create',
    //       onSubmit: async function(values) { ... },   // called when user clicks submit
    //       onCancel: function() { ... },               // optional
    //   })
    //
    // Falls back to a "Fill manually" link if the AI extraction endpoint
    // is unreachable (503/offline) so the form is still usable.
    aiFormFill: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var schema = opts.schema || [];
        var endpoint = opts.endpoint || '/api/sdk/ai-form-fill';
        var submitLabel = opts.submitLabel || 'Create';
        var values = Object.assign({}, opts.initial || {});
        var history = [];
        var intro = opts.intro || ("I'll help you fill out this form. Tell me about it in your own words.");
        history.push({role: 'assistant', content: intro});

        // Build modal body shell
        var body =
            '<div class="eos-aifill" style="display:flex;flex-direction:column;gap:14px;min-height:320px">' +
                '<div class="eos-aifill-chat" id="aifill-chat" style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px;max-height:260px;overflow-y:auto;font-size:13px;line-height:1.5"></div>' +
                '<div class="eos-aifill-preview" id="aifill-preview" style="background:color-mix(in srgb, var(--accent) 6%, var(--bg-card));border:1px solid color-mix(in srgb, var(--accent) 30%, var(--border));border-radius:10px;padding:10px"></div>' +
                '<div class="eos-aifill-input" style="display:flex;gap:6px">' +
                    '<input type="text" id="aifill-input" placeholder="Type a reply…" autofocus style="flex:1;padding:9px 12px;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:8px;font-size:14px">' +
                    '<button class="eos-btn" id="aifill-send" title="Send your reply to the AI" onclick="EOS_UI._aiFillSend()">Send</button>' +
                '</div>' +
                '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;border-top:1px solid var(--border);padding-top:10px">' +
                    '<a id="aifill-manual" href="javascript:void(0)" style="color:var(--text-muted);font-size:12px;text-decoration:underline">Fill manually instead</a>' +
                    '<div style="display:flex;gap:8px">' +
                        '<button class="eos-btn" title="Close without creating anything" onclick="EOS_UI.closeModal()">Cancel</button>' +
                        '<button class="eos-btn eos-btn-primary" id="aifill-submit" title="Submit the AI-filled form" onclick="EOS_UI._aiFillSubmit()" disabled>' + esc(submitLabel) + '</button>' +
                    '</div>' +
                '</div>' +
            '</div>';

        EOS_UI.modal({title: opts.title || 'Create with AI', body: body, width: '600px'});

        // Stash state on EOS_UI so the inline button handlers can reach it.
        EOS_UI._aiFill = {
            schema: schema,
            values: values,
            history: history,
            endpoint: endpoint,
            onSubmit: opts.onSubmit,
            onCancel: opts.onCancel,
            submitLabel: submitLabel,
        };

        EOS_UI._aiFillRenderChat();
        EOS_UI._aiFillRenderPreview();
        EOS_UI._aiFillRefreshSubmitState();

        // Wire keyboard
        var inp = document.getElementById('aifill-input');
        if (inp) inp.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                EOS_UI._aiFillSend();
            }
        });

        // Wire fallback to manual fill
        var manual = document.getElementById('aifill-manual');
        if (manual && opts.onManual) {
            manual.onclick = function() {
                EOS_UI.closeModal();
                try { opts.onManual(values); } catch (e) {}
            };
        } else if (manual) {
            // Default fallback: open formModal with same schema + values
            manual.onclick = function() {
                EOS_UI.closeModal();
                EOS_UI.formModal(opts.title || 'Create', schema.map(function(f) {
                    return Object.assign({}, f, {value: values[f.key] || ''});
                }), opts.onSubmit);
            };
        }
    },

    _aiFill: null,
    _aiFillBusy: false,

    _aiFillRenderChat: function() {
        var s = EOS_UI._aiFill;
        if (!s) return;
        var el = document.getElementById('aifill-chat');
        if (!el) return;
        var esc = EOS_UI.esc;
        el.innerHTML = s.history.map(function(m) {
            var bg = m.role === 'assistant' ? 'color-mix(in srgb, var(--accent) 8%, var(--bg-card))' : 'var(--bg-card)';
            var align = m.role === 'assistant' ? 'flex-start' : 'flex-end';
            var who = m.role === 'assistant' ? '✨ AI' : 'You';
            return '<div style="display:flex;justify-content:' + align + ';margin-bottom:8px">' +
                '<div style="max-width:80%;background:' + bg + ';border:1px solid var(--border);border-radius:10px;padding:8px 12px">' +
                    '<div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:3px">' + esc(who) + '</div>' +
                    '<div style="color:var(--text);white-space:pre-wrap">' + esc(m.content) + '</div>' +
                '</div>' +
            '</div>';
        }).join('');
        el.scrollTop = el.scrollHeight;
    },

    _aiFillRenderPreview: function() {
        var s = EOS_UI._aiFill;
        if (!s) return;
        var el = document.getElementById('aifill-preview');
        if (!el) return;
        var esc = EOS_UI.esc;
        var parts = ['<div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px">Form preview</div>'];
        var rows = s.schema.map(function(f) {
            var v = s.values[f.key];
            var hasValue = v !== undefined && v !== null && v !== '';
            var display = hasValue ? esc(String(v)) : '<span style="color:var(--text-muted);font-style:italic">' + (f.required ? '(required, not yet filled)' : '(not set)') + '</span>';
            var labelColor = hasValue ? 'var(--text-heading)' : 'var(--text-muted)';
            return '<div style="display:flex;gap:8px;margin-bottom:4px;font-size:13px">' +
                '<span style="color:' + labelColor + ';font-weight:500;min-width:90px">' + esc(f.label || f.key) + ':</span>' +
                '<span style="flex:1">' + display + '</span>' +
                (hasValue ? '<button class="eos-btn" style="font-size:11px;padding:2px 8px" title="Edit this field by hand" onclick="EOS_UI._aiFillEdit(' + escAttr(JSON.stringify(f.key)) + ')">Edit</button>' : '') +
            '</div>';
        });
        parts.push(rows.join(''));
        el.innerHTML = parts.join('');
    },

    _aiFillRefreshSubmitState: function() {
        var s = EOS_UI._aiFill;
        if (!s) return;
        var btn = document.getElementById('aifill-submit');
        if (!btn) return;
        var missing = s.schema.filter(function(f) {
            return f.required && !s.values[f.key];
        });
        btn.disabled = missing.length > 0;
        btn.title = missing.length ? 'Missing required: ' + missing.map(function(f){return f.label||f.key;}).join(', ') : '';
    },

    _aiFillEdit: function(key) {
        var s = EOS_UI._aiFill;
        if (!s) return;
        var field = s.schema.find(function(f) { return f.key === key; });
        if (!field) return;
        var current = s.values[key] || '';
        var newVal = window.prompt('Edit "' + (field.label || key) + '"', current);
        if (newVal === null) return;
        s.values[key] = newVal;
        EOS_UI._aiFillRenderPreview();
        EOS_UI._aiFillRefreshSubmitState();
    },

    _aiFillSend: async function() {
        if (EOS_UI._aiFillBusy) return;
        var s = EOS_UI._aiFill;
        if (!s) return;
        var inp = document.getElementById('aifill-input');
        var text = (inp.value || '').trim();
        if (!text) return;
        s.history.push({role: 'user', content: text});
        inp.value = '';
        EOS_UI._aiFillBusy = true;
        EOS_UI._aiFillRenderChat();
        // Show thinking indicator
        s.history.push({role: 'assistant', content: '…thinking'});
        EOS_UI._aiFillRenderChat();
        s.history.pop(); // remove thinking placeholder before real reply lands

        try {
            var res = await fetch(s.endpoint, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    schema: s.schema,
                    history: s.history,
                    current: s.values,
                }),
            });
            var data = await res.json();
            if (!res.ok || data.error) {
                var msg = data.error || ('AI extraction failed (' + res.status + ')');
                s.history.push({role: 'assistant', content: '⚠ ' + msg + '\n\nFill manually using the link below, or try a simpler description.'});
                EOS_UI._aiFillRenderChat();
                return;
            }
            // Merge filled values
            if (data.current && typeof data.current === 'object') {
                s.values = data.current;
            } else if (data.filled && typeof data.filled === 'object') {
                Object.keys(data.filled).forEach(function(k) { s.values[k] = data.filled[k]; });
            }
            var reply = data.next_question || (data.ready ? "Looks good — click " + s.submitLabel + " to confirm, or keep refining." : "Got it.");
            s.history.push({role: 'assistant', content: reply});
            EOS_UI._aiFillRenderChat();
            EOS_UI._aiFillRenderPreview();
            EOS_UI._aiFillRefreshSubmitState();
        } catch (e) {
            s.history.push({role: 'assistant', content: '⚠ Network error — try again or fill manually.'});
            EOS_UI._aiFillRenderChat();
        } finally {
            EOS_UI._aiFillBusy = false;
            var i = document.getElementById('aifill-input');
            if (i) i.focus();
        }
    },

    _aiFillSubmit: async function() {
        var s = EOS_UI._aiFill;
        if (!s) return;
        var missing = s.schema.filter(function(f) {
            return f.required && !s.values[f.key];
        });
        if (missing.length) {
            if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast('Missing: ' + missing.map(function(f){return f.label||f.key;}).join(', '), false);
            return;
        }
        var btn = document.getElementById('aifill-submit');
        if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
        try {
            if (typeof s.onSubmit === 'function') await s.onSubmit(s.values);
            EOS_UI.closeModal();
        } catch (e) {
            if (btn) { btn.disabled = false; btn.textContent = s.submitLabel; }
            if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast('Save failed: ' + (e.message || e), false);
        }
    },

    // === 4D Timeline drawer =========================================
    // Mandatory app-UI pattern (see .claude/rules/app-ui-patterns.md).
    // Slides in from the right with three vertical sections:
    //   PAST   — created/updated, ## Timeline section, git log, syslog
    //   FUTURE — due/expires_at/next_review, scheduler jobs, pending,
    //            reminders
    //   NOW    — current frontmatter snapshot + tags + status/lifecycle
    // Usage:
    //   EOS_UI.timeline4D('20_Areas/Career/job-acme.md')          // path
    //   EOS_UI.timeline4D('20_Areas/Career/job-acme.md', { kind: 'job' })
    // Apps opting in via [provides.timeline] get a 📅 button auto-mounted
    // on `.detail-header` by _eosInitTimelineAutoMount below; calling this
    // method directly is the manual-mount escape hatch.
    timeline4D: function(notePath, opts) {
        opts = opts || {};
        var id = 'eos-timeline-panel';
        var existing = document.getElementById(id);
        if (existing) existing.remove();

        var panel = document.createElement('div');
        panel.id = id;
        panel.className = 'eos-settings-panel eos-timeline-panel';
        panel.innerHTML =
            '<div class="sp-head">' +
                '<h3>📅 ' + EOS_UI.esc(opts.title || 'Timeline') + '</h3>' +
                '<button class="eos-btn-sm eos-btn-ghost" data-act="close" title="Close the timeline drawer">Close</button>' +
            '</div>' +
            '<div class="sp-body tl-body">' +
                '<div class="tl-pathline">' + EOS_UI.esc(notePath) + '</div>' +
                '<div class="tl-loading">Loading…</div>' +
                '<section class="tl-section tl-future" style="display:none">' +
                    '<h4 class="tl-h">Future</h4>' +
                    '<div class="tl-list"></div>' +
                '</section>' +
                '<section class="tl-section tl-now" style="display:none">' +
                    '<h4 class="tl-h">Now</h4>' +
                    '<div class="tl-list"></div>' +
                '</section>' +
                '<section class="tl-section tl-past" style="display:none">' +
                    '<h4 class="tl-h">Past</h4>' +
                    '<div class="tl-list"></div>' +
                    '<button class="eos-btn-sm eos-btn-ghost tl-more" style="display:none" title="Load older timeline events">Show more</button>' +
                '</section>' +
            '</div>';
        document.body.appendChild(panel);
        // Drive .open in next tick so CSS transition fires
        setTimeout(function() { panel.classList.add('open'); }, 10);

        var close = function() {
            panel.classList.remove('open');
            setTimeout(function() {
                if (panel.parentNode) panel.parentNode.removeChild(panel);
                if (typeof opts.onClose === 'function') opts.onClose();
            }, 200);
        };
        panel.querySelectorAll('[data-act="close"]').forEach(function(b) { b.onclick = close; });

        var fmtTs = function(ts) {
            if (!ts) return '';
            // ISO 8601 → "MMM D, YYYY" + optional "HH:mm" if time present
            try {
                var d = new Date(ts);
                if (isNaN(d.getTime())) return EOS_UI.esc(String(ts));
                var date = d.toISOString().slice(0,10);
                if (String(ts).length > 10) {
                    var time = d.toTimeString().slice(0,5);
                    return date + ' ' + time;
                }
                return date;
            } catch(e) { return EOS_UI.esc(String(ts)); }
        };

        var sourceIcon = function(src) {
            switch ((src || '').toLowerCase()) {
                case 'vault': return '📝';
                case 'git': return '⚙';
                case 'timeline-section': return '◆';
                case 'reactor': return '✨';
                case 'syslog': return '·';
                case 'scheduler': return '⏰';
                case 'rooms': return '⏳';
                case 'reminders': return '🔔';
                default: return '·';
            }
        };

        var renderRow = function(e) {
            return '<div class="tl-row">' +
                '<span class="tl-icon">' + EOS_UI.esc(sourceIcon(e.source)) + '</span>' +
                '<span class="tl-ts">' + EOS_UI.esc(fmtTs(e.ts)) + '</span>' +
                '<span class="tl-text">' + EOS_UI.esc(e.text || '') +
                    (e.kind ? ' <span class="tl-kind">' + EOS_UI.esc(e.kind) + '</span>' : '') +
                '</span>' +
            '</div>';
        };

        var renderNow = function(now) {
            var fm = now.frontmatter || {};
            var rows = [];
            if (now.status)    rows.push(['status', now.status]);
            if (now.lifecycle) rows.push(['lifecycle', now.lifecycle]);
            if ((now.tags || []).length) rows.push(['tags', now.tags.join(', ')]);
            // Show up to 8 most-interesting frontmatter keys (skip noisy ones)
            var skip = {'tags':1,'created':1,'updated':1,'status':1,'lifecycle':1};
            var fmKeys = Object.keys(fm).filter(function(k) { return !skip[k]; }).slice(0, 8);
            fmKeys.forEach(function(k) {
                var v = fm[k];
                if (v == null || v === '') return;
                if (Array.isArray(v)) v = v.join(', ');
                rows.push([k, String(v)]);
            });
            if (!rows.length) return '<div class="tl-empty">No current state recorded yet.</div>';
            return '<div class="tl-now-grid">' + rows.map(function(r) {
                return '<div class="tl-k">' + EOS_UI.esc(r[0]) + '</div>' +
                       '<div class="tl-v">' + EOS_UI.esc(r[1]) + '</div>';
            }).join('') + '</div>';
        };

        var url = '/api/sdk/timeline?path=' + encodeURIComponent(notePath) +
                  (opts.kind ? '&kind=' + encodeURIComponent(opts.kind) : '');
        fetch(url).then(function(r) { return r.json(); }).then(function(data) {
            panel.querySelector('.tl-loading').style.display = 'none';

            var futureSec = panel.querySelector('.tl-future');
            var nowSec    = panel.querySelector('.tl-now');
            var pastSec   = panel.querySelector('.tl-past');

            // FUTURE
            if (data.future && data.future.length) {
                futureSec.querySelector('.tl-list').innerHTML = data.future.map(renderRow).join('');
                futureSec.style.display = '';
            }
            // NOW (always show — it's the snapshot)
            nowSec.querySelector('.tl-list').innerHTML = renderNow(data.now || {});
            nowSec.style.display = '';
            // PAST
            var past = data.past || [];
            if (past.length) {
                var initial = past.slice(0, 8);
                var rest = past.slice(8);
                pastSec.querySelector('.tl-list').innerHTML = initial.map(renderRow).join('');
                pastSec.style.display = '';
                if (rest.length) {
                    var more = pastSec.querySelector('.tl-more');
                    more.textContent = 'Show ' + rest.length + ' more';
                    more.style.display = '';
                    more.onclick = function() {
                        pastSec.querySelector('.tl-list').innerHTML =
                            past.map(renderRow).join('');
                        more.style.display = 'none';
                    };
                }
            } else if (!(data.future && data.future.length)) {
                pastSec.style.display = '';
                pastSec.querySelector('.tl-list').innerHTML =
                    '<div class="tl-empty">No history yet. Edits, scheduled jobs and event ripples will show up here.</div>';
            }
        }).catch(function(e) {
            panel.querySelector('.tl-loading').textContent = 'Failed to load: ' + (e.message || e);
        });

        return { close: close };
    },
};

// --- 4D timeline auto-mount ---
// Reads /api/sdk/timeline-apps once per page; if the current page's app
// declares [provides.timeline], any element carrying `data-entity-path`
// gets a 📅 button auto-injected (idempotent). Late-rendering detail
// views are caught via MutationObserver so apps don't need to call into
// EOS_UI from their showDetail.
//
// App-side contract (3 lines on entering detail view):
//   <header data-entity-path="20_Areas/Career/job-acme.md"> ... </header>
//   — OR set/update an existing element:
//   headerEl.setAttribute('data-entity-path', resolvedPath)
//
// Opt-out: place `data-timeline-button="manual"` anywhere on the page.
(function _eosInitTimelineAutoMount() {
    if (typeof document === 'undefined') return;
    var APP_MATCH = null;          // {app_id, route_prefix, name, ...}
    var FETCH_TRIED = false;

    // Idempotent by DOM presence, NOT a WeakSet: an app's detail render may
    // replace the host's innerHTML *after* we mount (wiping our button). A
    // WeakSet would then permanently believe the host is decorated and never
    // re-add it. Checking for the button in the DOM lets us re-mount when a
    // childList mutation (below) signals the host was rebuilt.
    var mountButton = function(host) {
        if (!host || !host.querySelector) return;
        if (host.querySelector(':scope > .eos-timeline-btn')) return;
        var btn = document.createElement('button');
        btn.className = 'eos-btn-sm eos-btn-ghost eos-timeline-btn';
        btn.type = 'button';
        btn.title = '4D timeline — past, future, now';
        btn.innerHTML = '📅 Timeline';
        btn.style.marginLeft = '8px';
        btn.addEventListener('click', function(ev) {
            ev.preventDefault();
            ev.stopPropagation();
            // Re-read at click time so swapped detail views work
            var p = host.getAttribute('data-entity-path') || window.EOS_TIMELINE_PATH || '';
            if (!p) {
                if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast('No entity path on this view yet.', false);
                return;
            }
            EOS_UI.timeline4D(p, { title: (APP_MATCH && APP_MATCH.name) || 'Timeline' });
        });
        host.appendChild(btn);
    };

    var scan = function(root) {
        if (!APP_MATCH) return;
        var hosts = (root && root.querySelectorAll) ? root.querySelectorAll('[data-entity-path]') : [];
        for (var i = 0; i < hosts.length; i++) mountButton(hosts[i]);
    };

    var bootObserver = function() {
        // Initial scan of body
        scan(document.body);
        // Watch for new [data-entity-path] elements (detail-view swaps)
        var mo = new MutationObserver(function(records) {
            for (var i = 0; i < records.length; i++) {
                var r = records[i];
                for (var j = 0; j < r.addedNodes.length; j++) {
                    var n = r.addedNodes[j];
                    if (n.nodeType !== 1) continue;
                    if (n.hasAttribute && n.hasAttribute('data-entity-path')) mountButton(n);
                    scan(n);
                }
                if (r.type === 'attributes' && r.target && r.target.hasAttribute('data-entity-path')) {
                    mountButton(r.target);
                }
                // A host whose innerHTML was replaced (detail re-render) loses its
                // button; the mutation target is the host itself. Re-mount it.
                if (r.type === 'childList' && r.target && r.target.nodeType === 1 &&
                    r.target.hasAttribute && r.target.hasAttribute('data-entity-path')) {
                    mountButton(r.target);
                }
            }
        });
        mo.observe(document.body, {
            childList: true, subtree: true, attributes: true, attributeFilter: ['data-entity-path'],
        });
    };

    // Resilient one-shot init. The timeline-apps fetch can transiently fail
    // (daemon restart / flap); giving up permanently would disable the 📅
    // button for the whole page session with no recovery. Retry a few times
    // with backoff, and only latch FETCH_TRIED once we've actually attached the
    // observer — a failed attempt leaves the door open for the next try.
    var go = async function() {
        if (FETCH_TRIED) return;
        if (document.querySelector('[data-timeline-button="manual"]')) { FETCH_TRIED = true; return; }
        var path = window.location.pathname || '';
        // Backoff spanning ~30s so a full daemon restart (boot takes ~10-20s),
        // not just a momentary blip, is covered before we give up.
        var delays = [0, 600, 1500, 3000, 6000, 9000, 12000];
        for (var attempt = 0; attempt < delays.length; attempt++) {
            if (delays[attempt]) await new Promise(function(r){ setTimeout(r, delays[attempt]); });
            try {
                var resp = await fetch('/api/sdk/timeline-apps');
                if (!resp.ok) continue;          // transient (503 during boot, etc.)
                var data = await resp.json();
                var apps = (data && data.apps) || [];
                for (var i = 0; i < apps.length; i++) {
                    var pref = apps[i].route_prefix || '';
                    if (pref && path.indexOf(pref) === 0) { APP_MATCH = apps[i]; break; }
                }
                // A successful fetch is authoritative: if no app on this route
                // declares a timeline, stop retrying.
                FETCH_TRIED = true;
                if (!APP_MATCH) return;
                bootObserver();
                return;
            } catch (e) { /* network blip — fall through to next attempt */ }
        }
        // All attempts failed (daemon down the whole time). Leave FETCH_TRIED
        // false so a later manual EOS_UI.timeline4D() call still works.
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', go);
    } else {
        go();
    }
})();

// ── ✨ field-suggest auto-mount ────────────────────────────────────────────
// Any element carrying data-suggest-field + data-suggest-app gets the ✨
// suggest button injected via EOS_UI.fieldSuggest (idempotent). Covers both
// formModal (formHtml stamps the attrs on the .eos-form-group div) and raw page
// inputs (<textarea data-suggest-field="prompt" data-suggest-app="viz">). Late
// renders (modal bodies, detail swaps) caught via MutationObserver. No gating
// fetch — the /api/sdk/suggest-field endpoint validates the declaration.
// See .claude/rules/field-suggest.md.
(function _eosInitSuggestAutoMount() {
    if (typeof document === 'undefined') return;
    var mount = function(host) {
        if (!host || !host.getAttribute) return;
        var app = host.getAttribute('data-suggest-app') || '';
        var field = host.getAttribute('data-suggest-field') || '';
        if (!field) return;
        EOS_UI.fieldSuggest({
            input: host,
            app: app,
            field: field,
            mode: host.getAttribute('data-suggest-mode') || 'fill',
            endpoint: host.getAttribute('data-suggest-endpoint') || '',
            label: host.getAttribute('data-suggest-label') || '✨ Suggest',
        });
    };
    var scan = function(root) {
        if (!root || !root.querySelectorAll) return;
        var hosts = root.querySelectorAll('[data-suggest-field]');
        for (var i = 0; i < hosts.length; i++) mount(hosts[i]);
        if (root.getAttribute && root.getAttribute('data-suggest-field')) mount(root);
    };
    var boot = function() {
        scan(document.body);
        if (!window.MutationObserver) return;
        var mo = new MutationObserver(function(records) {
            for (var i = 0; i < records.length; i++) {
                var r = records[i];
                for (var j = 0; j < r.addedNodes.length; j++) {
                    var n = r.addedNodes[j];
                    if (n.nodeType === 1) scan(n);
                }
            }
        });
        mo.observe(document.body, { childList: true, subtree: true });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
})();

// Global fetch wrapper — turns 503 `{error:"ai_offline"}` responses into
// a toast + banner refresh so clicking an AI button while AI is offline
// gives honest feedback instead of a silent failure. The response object
// itself is returned unchanged so callers can still handle .ok / .status.
// Render the toast bell on page load if sessionStorage carried a log over
// from a previous page navigation in this tab.
(function _eosInitToastBell() {
    if (typeof document === 'undefined') return;
    function go() {
        if (window.EOS_UI && EOS_UI._toastLog && EOS_UI._toastLog.length) {
            EOS_UI._updateToastBell();
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', go);
    } else {
        go();
    }
})();

(function _eosWrapFetch() {
    if (typeof window === 'undefined' || window._eosFetchWrapped) return;
    window._eosFetchWrapped = true;
    var _origFetch = window.fetch.bind(window);
    var _lastToast = 0;
    window.fetch = function(input, init) {
        return _origFetch(input, init).then(function(res) {
            if (res && res.status === 503) {
                var probe = res.clone();
                probe.json().then(function(j) {
                    if (!j) return;
                    var err = j.error || '';
                    if (err === 'ai_offline' || err === 'capability_offline') {
                        var now = Date.now();
                        if (now - _lastToast > 2500) {
                            _lastToast = now;
                            if (window.EOS_UI && EOS_UI.toast) {
                                EOS_UI.toast(j.message || 'AI is offline — try again when a provider is available.');
                            }
                        }
                        if (window.EOS_UI && EOS_UI.initThinkStatus) {
                            EOS_UI._thinkStatusInit = false;
                            EOS_UI.initThinkStatus();
                        }
                    }
                }).catch(function() {});
            }
            return res;
        });
    };
})();

// ── View helpers (viewSwitcher / kanbanLayout / inlineCellEdit / pillBadge) ──
// Extracted from apps/boards in 2026-04 so tasks/projects/future apps can grow
// table/kanban/calendar views with the same chrome. Card content + group
// resolution are pluggable; the helpers own only the layout + drag-drop.

EOS_UI.pillBadge = function(value, colorMap) {
    var palette = ['blue','amber','green','emerald','red','purple','orange','gray'];
    var color = (colorMap && colorMap[value]) || 'gray';
    if (palette.indexOf(color) === -1) color = 'gray';
    return '<span class="eos-pill eos-pill-' + color + '">' + EOS_UI.esc(String(value)) + '</span>';
};

// EOS_UI.viewSwitcher({mountId, views, active, onChange})
//   views   = ['table','kanban',...] OR [{type, label?, icon?}, ...]
//   onChange(viewType) fires on click; helper toggles .active for you.
EOS_UI.viewSwitcher = function(opts) {
    var ICONS = {table:'☰', kanban:'▥', calendar:'📅', timeline:'⏳', chart:'📈', gallery:'▦', list:'≡', summary:'▤', map:'🗺', pivot:'⊞'};
    var mount = document.getElementById(opts.mountId);
    if (!mount) return null;
    var views = (opts.views || []).map(function(v) {
        if (typeof v === 'string') return {type: v};
        return v;
    });
    if (!views.length) views = [{type: 'table'}];
    var active = opts.active || views[0].type;
    mount.classList.add('eos-view-tabs');
    mount.innerHTML = views.map(function(v) {
        var label = v.label || (v.type.charAt(0).toUpperCase() + v.type.slice(1));
        var icon = v.icon != null ? v.icon : (ICONS[v.type] || '');
        return '<button type="button" class="eos-view-tab' + (v.type === active ? ' active' : '') +
               '" data-view="' + EOS_UI.escAttr(v.type) + '">' +
               (icon ? '<span class="eos-view-tab-icon">' + EOS_UI.esc(icon) + '</span>' : '') +
               EOS_UI.esc(label) + '</button>';
    }).join('');
    mount.querySelectorAll('.eos-view-tab').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var v = btn.getAttribute('data-view');
            mount.querySelectorAll('.eos-view-tab').forEach(function(b) { b.classList.remove('active'); });
            btn.classList.add('active');
            if (typeof opts.onChange === 'function') opts.onChange(v);
        });
    });
    return {
        setActive: function(v) {
            mount.querySelectorAll('.eos-view-tab').forEach(function(b) {
                b.classList.toggle('active', b.getAttribute('data-view') === v);
            });
        }
    };
};

// EOS_UI.kanbanLayout({mountId, items, groups, getGroup|inGroup, renderCard, onMove, getItemId, colorMap, wrapCards, onQuickAdd, colBadgeFor})
//   groups     = [{key, label, color?}]
//   getGroup   = function(item) -> string  (1:1 grouping — most common)
//   inGroup    = function(item, groupKey) -> bool  (multi-membership — overrides getGroup)
//   renderCard = function(item) -> HTML string. If wrapCards=true (default), this is the
//                card body and the helper wraps in <div class="eos-kanban-card"...>.
//                If wrapCards=false, you return a complete element (e.g. EOS_UI.entityCard);
//                the helper just sets data-id + draggable on the root via post-render hook.
//   onMove     = function(item, newGroupKey) — called on successful drop
//   getItemId  = function(item) -> stable string id (default: item.id || item.file)
//   colorMap   = {value: paletteName} — overrides per-group `color` if group.color absent
//   wrapCards  = boolean (default true) — see renderCard above
//   onQuickAdd = function(groupKey, text) — when set, each column gets a "+ Add"
//                footer that expands to an inline input (Enter commits, Esc cancels).
//   colBadgeFor= function(groupKey, itemsInGroup) -> string — extra badge text shown
//                next to the count (e.g. "Σ 34"); empty/null renders nothing.
EOS_UI.kanbanLayout = function(opts) {
    var mount = document.getElementById(opts.mountId);
    if (!mount) return;
    var items = opts.items || [];
    var groups = opts.groups || [];
    var inGroup = opts.inGroup;
    var getGroup = opts.getGroup || function(it) { return ''; };
    if (!inGroup) inGroup = function(it, key) { return getGroup(it) === key; };
    var getId = opts.getItemId || function(it) { return it.id || it.file || ''; };
    var renderCard = opts.renderCard || function(it) { return EOS_UI.esc(String(it.title || it.name || it.text || getId(it))); };
    var colorMap = opts.colorMap || {};
    var wrap = opts.wrapCards !== false;

    mount.classList.add('eos-kanban');
    mount.innerHTML = groups.map(function(grp) {
        var inG = items.filter(function(it) { return inGroup(it, grp.key); });
        var color = grp.color || colorMap[grp.key] || 'gray';
        var headerPill = '<span class="eos-pill eos-pill-' + EOS_UI.escAttr(color) + '" style="margin-right:0.4rem">' +
                         EOS_UI.esc(grp.label || grp.key || '—') + '</span>';
        var badge = '';
        if (typeof opts.colBadgeFor === 'function') {
            var b = opts.colBadgeFor(grp.key, inG);
            if (b) badge = '<span class="eos-kanban-col-badge">' + EOS_UI.esc(String(b)) + '</span>';
        }
        var quickAdd = '';
        if (typeof opts.onQuickAdd === 'function') {
            quickAdd = '<div class="eos-kanban-quickadd" data-group-key="' + EOS_UI.escAttr(grp.key) + '">' +
                '<button type="button" class="eos-kanban-quickadd-btn">＋ Add</button>' +
                '<input type="text" class="eos-kanban-quickadd-input" placeholder="Title… (Enter)" style="display:none">' +
                '</div>';
        }
        return '<div class="eos-kanban-col">' +
            '<div class="eos-kanban-col-header"><span class="eos-kanban-col-title">' + headerPill + '</span>' +
            badge +
            '<span class="eos-kanban-col-count">' + inG.length + '</span></div>' +
            '<div class="eos-kanban-items" data-group-key="' + EOS_UI.escAttr(grp.key) + '">' +
            inG.map(function(it) {
                if (wrap) {
                    return '<div class="eos-kanban-card" draggable="true" data-id="' +
                           EOS_UI.escAttr(getId(it)) + '">' + renderCard(it) + '</div>';
                }
                // Caller-rendered card; we mark the first child element as the
                // drag handle by tagging the wrapper itself.
                return '<div class="eos-kanban-card-host" draggable="true" data-id="' +
                       EOS_UI.escAttr(getId(it)) + '">' + renderCard(it) + '</div>';
            }).join('') +
            '</div>' + quickAdd + '</div>';
    }).join('');

    // Quick-add wiring: button expands to an input; Enter commits, Esc/blur-empty collapses.
    if (typeof opts.onQuickAdd === 'function') {
        mount.querySelectorAll('.eos-kanban-quickadd').forEach(function(qa) {
            var btn = qa.querySelector('.eos-kanban-quickadd-btn');
            var input = qa.querySelector('.eos-kanban-quickadd-input');
            btn.addEventListener('click', function() {
                btn.style.display = 'none';
                input.style.display = '';
                input.focus();
            });
            function collapse() {
                input.value = '';
                input.style.display = 'none';
                btn.style.display = '';
            }
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    var text = input.value.trim();
                    if (text) opts.onQuickAdd(qa.getAttribute('data-group-key') || '', text);
                    collapse();
                } else if (e.key === 'Escape') {
                    collapse();
                    e.stopPropagation();
                }
            });
            input.addEventListener('blur', function() { if (!input.value.trim()) collapse(); });
        });
    }

    // Drag-drop wiring. Track the drag id in module-local scope to avoid
    // depending on dataTransfer (Safari quirks).
    var dragId = '';
    var dragSelector = wrap ? '.eos-kanban-card' : '.eos-kanban-card-host';
    mount.querySelectorAll(dragSelector).forEach(function(card) {
        card.addEventListener('dragstart', function(e) {
            dragId = card.getAttribute('data-id') || '';
            card.classList.add('dragging');
            if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
        });
        card.addEventListener('dragend', function() {
            card.classList.remove('dragging');
        });
    });
    mount.querySelectorAll('.eos-kanban-items').forEach(function(zone) {
        zone.addEventListener('dragover', function(e) { e.preventDefault(); zone.classList.add('drag-over'); });
        zone.addEventListener('dragleave', function() { zone.classList.remove('drag-over'); });
        zone.addEventListener('drop', function(e) {
            e.preventDefault();
            zone.classList.remove('drag-over');
            mount.querySelectorAll('.dragging').forEach(function(el) { el.classList.remove('dragging'); });
            if (!dragId) return;
            var targetKey = zone.getAttribute('data-group-key') || '';
            var item = items.find(function(it) { return getId(it) === dragId; });
            dragId = '';
            if (!item) return;
            if (inGroup(item, targetKey)) return;  // already there → no-op
            if (typeof opts.onMove === 'function') opts.onMove(item, targetKey);
        });
    });
};

// EOS_UI.inlineCellEdit({el, value, type, options, onSave, onCancel})
//   type = 'text' | 'select' | 'date' | 'number'
//   options = list of strings (for select)
//   onSave(newValue) on blur or Enter; Esc reverts.
EOS_UI.inlineCellEdit = function(opts) {
    var el = opts.el;
    if (!el || el.querySelector('input,select,textarea')) return;
    var old = opts.value != null ? String(opts.value) : el.textContent;
    var type = opts.type || 'text';
    var input;
    if (type === 'select') {
        input = document.createElement('select');
        (opts.options || []).forEach(function(o) {
            var op = document.createElement('option');
            op.value = o; op.textContent = o;
            if (String(o) === old) op.selected = true;
            input.appendChild(op);
        });
    } else {
        input = document.createElement('input');
        input.type = (type === 'date' ? 'date' : (type === 'number' ? 'number' : 'text'));
        input.value = old;
    }
    input.className = 'eos-cell-edit-input';
    el.innerHTML = '';
    el.appendChild(input);
    input.focus();
    if (input.select) try { input.select(); } catch (e) {}
    var done = false;
    function commit() {
        if (done) return;
        done = true;
        var v = input.value;
        if (typeof opts.onSave === 'function') opts.onSave(v);
    }
    function revert() {
        if (done) return;
        done = true;
        el.textContent = old;
        if (typeof opts.onCancel === 'function') opts.onCancel();
    }
    input.addEventListener('blur', commit);
    input.addEventListener('change', function() { if (type === 'select') commit(); });
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') input.blur();
        else if (e.key === 'Escape') { revert(); input.blur(); }
    });
};

// Sticky job-progress card — bottom-right, survives scrolling. Use for any
// long-running async job (MV render, podcast gen, ComfyUI batch, etc.).
//   var job = EOS_UI.jobProgress({id, onCancel});
//   job.update({stage:'Generating images', detail:'scene 9 running', pct:42});
//   job.done({stage:'Ready', detail:'output.mp4'});  // auto-hides after 4s
//   job.hide();
// Multiple instances coexist when given distinct ids; same id reuses the card.
EOS_UI.jobProgress = function(opts) {
    opts = opts || {};
    var id = opts.id || 'eos-job-default';
    var elId = 'eos-job-' + id.replace(/[^a-z0-9_-]/gi, '_');
    function ensure() {
        var box = document.getElementById(elId);
        if (box) return box;
        // Stack multiple cards by counting existing ones
        var existing = document.querySelectorAll('.eos-job-progress').length;
        box = document.createElement('div');
        box.id = elId;
        box.className = 'eos-job-progress';
        box.style.bottom = 'calc(env(safe-area-inset-bottom) + ' + (88 + existing * 86) + 'px)';
        box.innerHTML =
            '<div class="eos-job-row">' +
                '<span class="eos-spinner" style="width:14px;height:14px;border-width:2px"></span>' +
                '<b class="eos-job-stage">Working…</b>' +
                '<span class="eos-job-detail"></span>' +
                '<a class="eos-job-link" style="display:none"></a>' +
                '<button class="eos-btn eos-btn-sm eos-btn-ghost eos-job-cancel" style="margin-left:auto;display:none" title="Cancel this running job">Cancel</button>' +
            '</div>' +
            '<div class="eos-bar"><div class="eos-bar-fill eos-job-bar" style="width:0%"></div></div>';
        document.body.appendChild(box);
        if (opts.onCancel) {
            box.querySelector('.eos-job-cancel').addEventListener('click', opts.onCancel);
        }
        return box;
    }
    function update(state) {
        state = state || {};
        var box = ensure();
        box.style.display = '';
        if (state.stage != null) box.querySelector('.eos-job-stage').textContent = state.stage;
        box.querySelector('.eos-job-detail').textContent = state.detail || '';
        if (state.pct != null) {
            box.querySelector('.eos-job-bar').style.width = Math.max(0, Math.min(100, state.pct)) + '%';
        }
        var btn = box.querySelector('.eos-job-cancel');
        if (btn) btn.style.display = (opts.onCancel && state.cancellable !== false) ? '' : 'none';
        var link = box.querySelector('.eos-job-link');
        if (link && state.href != null) {
            link.style.display = state.href ? '' : 'none';
            if (state.href) {
                link.href = String(state.href);
                link.textContent = state.linkText || 'Open details';
            }
        }
    }
    function hide() {
        var box = document.getElementById(elId);
        if (!box) return;
        box.style.display = 'none';
        var bar = box.querySelector('.eos-job-bar'); if (bar) bar.style.width = '0%';
    }
    function done(state) {
        update(Object.assign({pct:100}, state||{}));
        var box = document.getElementById(elId);
        if (box) {
            var btn = box.querySelector('.eos-job-cancel');
            if (btn) btn.style.display = 'none';
        }
        setTimeout(hide, (state && state.lingerMs) || 4000);
    }
    function destroy() {
        var box = document.getElementById(elId);
        if (box) box.remove();
    }
    return {update: update, hide: hide, done: done, destroy: destroy, id: id};
};

// Auto-init dictionary popup + cloud-consent listener + demo banner on all pages
// PWA install banner auto-mounts only on home-like pages to avoid noise on every app.
function _eosAutoInit() {
    EOS_UI.initDict();
    EOS_UI.initCloudConsent();
    EOS_UI.initDemoBanner();
    EOS_UI.initThinkStatus();
    EOS_UI._initTabA11y();
    var p = location.pathname;
    var isHomeLike = p === '/' || p === '/hub/' || p === '/hub' ||
                     document.body && document.body.getAttribute('data-pwa-install') === 'true';
    if (isHomeLike) EOS_UI.pwaInstall.mount();
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _eosAutoInit);
} else {
    _eosAutoInit();
}

// --- Spotlight primitive — used by the product tour to highlight a real
// element on a real page and attach a tooltip. Pure DOM, no deps.
//
// EOS_UI.spotlight(selector, {
//   title?, body, stepLabel? ("2 / 6"),
//   nextLabel? ("Next →"), prevLabel? ("← Back"), skipLabel? ("Skip"),
//   onNext?, onPrev?, onSkip?,            // called when buttons clicked
//   placement? ("bottom"|"top"|"auto"),   // tooltip side; default auto
//   padding? (default 8),                 // px around target
//   waitFor? ms (default 6000)            // poll for selector before giving up
// }) -> {update(), close()}
//
// If selector is missing, a center-screen tooltip with no cutout is shown so
// the tour still progresses (e.g. "Welcome" steps without a target element).
EOS_UI.spotlight = function(selector, opts) {
    opts = opts || {};
    var pad = opts.padding == null ? 8 : opts.padding;
    var waitFor = opts.waitFor == null ? 6000 : opts.waitFor;
    var existing = document.getElementById('eos-spotlight-root');
    if (existing) existing.remove();

    var root = document.createElement('div');
    root.id = 'eos-spotlight-root';
    root.className = 'eos-spotlight';
    root.innerHTML =
        '<div class="eos-spotlight-mask" id="eos-spotlight-mask"></div>' +
        '<div class="eos-spotlight-tooltip" id="eos-spotlight-tip" role="dialog" aria-modal="true">' +
            '<div class="eos-spotlight-step" id="eos-spotlight-step"></div>' +
            '<div class="eos-spotlight-title" id="eos-spotlight-title"></div>' +
            '<div class="eos-spotlight-body" id="eos-spotlight-body"></div>' +
            '<div class="eos-spotlight-actions">' +
                '<button class="eos-btn eos-btn-sm eos-btn-ghost" id="eos-spotlight-skip" title="End the tour"></button>' +
                '<div style="flex:1"></div>' +
                '<button class="eos-btn eos-btn-sm eos-btn-ghost" id="eos-spotlight-prev" title="Go back one step"></button>' +
                '<button class="eos-btn eos-btn-sm" id="eos-spotlight-next" title="Continue to the next step"></button>' +
            '</div>' +
        '</div>';
    document.body.appendChild(root);

    var mask = document.getElementById('eos-spotlight-mask');
    var tip = document.getElementById('eos-spotlight-tip');
    var stepEl = document.getElementById('eos-spotlight-step');
    var titleEl = document.getElementById('eos-spotlight-title');
    var bodyEl = document.getElementById('eos-spotlight-body');
    var skipBtn = document.getElementById('eos-spotlight-skip');
    var prevBtn = document.getElementById('eos-spotlight-prev');
    var nextBtn = document.getElementById('eos-spotlight-next');

    stepEl.textContent = opts.stepLabel || '';
    titleEl.textContent = opts.title || '';
    bodyEl.innerHTML = opts.body || '';
    skipBtn.textContent = opts.skipLabel || 'Skip tour';
    prevBtn.textContent = opts.prevLabel || '← Back';
    nextBtn.textContent = opts.nextLabel || 'Next →';
    skipBtn.style.display = opts.onSkip ? '' : 'none';
    prevBtn.style.display = opts.onPrev ? '' : 'none';
    nextBtn.style.display = opts.onNext ? '' : 'none';
    if (opts.onSkip) skipBtn.onclick = opts.onSkip;
    if (opts.onPrev) prevBtn.onclick = opts.onPrev;
    if (opts.onNext) nextBtn.onclick = opts.onNext;

    function position() {
        var target = selector ? document.querySelector(selector) : null;
        if (!target) {
            // Center-screen mode — no cutout
            mask.style.background = 'rgba(0,0,0,0.55)';
            mask.style.clipPath = '';
            tip.style.left = '50%';
            tip.style.top = '50%';
            tip.style.transform = 'translate(-50%, -50%)';
            return;
        }
        var r = target.getBoundingClientRect();
        var W = window.innerWidth, H = window.innerHeight;
        var x = Math.max(0, r.left - pad), y = Math.max(0, r.top - pad);
        var w = Math.min(W - x, r.width + pad * 2), h = Math.min(H - y, r.height + pad * 2);
        // Cutout via clip-path (evenodd)
        mask.style.background = 'rgba(0,0,0,0.55)';
        mask.style.clipPath =
            'polygon(0 0, 0 100%, 100% 100%, 100% 0, 0 0,' +
            x + 'px ' + y + 'px,' +
            x + 'px ' + (y + h) + 'px,' +
            (x + w) + 'px ' + (y + h) + 'px,' +
            (x + w) + 'px ' + y + 'px,' +
            x + 'px ' + y + 'px)';
        // Place tooltip
        var tipW = Math.min(360, W - 24);
        tip.style.width = tipW + 'px';
        // Prefer below; if no room, put above
        var place = opts.placement || 'auto';
        var below = (y + h + 12 + 200 < H);
        if (place === 'top') below = false;
        if (place === 'bottom') below = true;
        var tipX = Math.max(12, Math.min(W - tipW - 12, r.left));
        var tipY = below ? (y + h + 12) : Math.max(12, y - 12 - tip.offsetHeight);
        tip.style.transform = '';
        tip.style.left = tipX + 'px';
        tip.style.top = tipY + 'px';
        try { target.scrollIntoView({block: 'center', behavior: 'smooth'}); } catch (e) {}
    }

    var pollStart = Date.now();
    function tryPosition() {
        if (selector && !document.querySelector(selector)) {
            if (Date.now() - pollStart < waitFor) {
                return setTimeout(tryPosition, 120);
            }
            // give up — center-screen fallback
        }
        position();
    }
    tryPosition();

    var onResize = function() { position(); };
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', onResize, true);

    return {
        update: function(newOpts) {
            newOpts = newOpts || {};
            if (newOpts.title != null) titleEl.textContent = newOpts.title;
            if (newOpts.body != null) bodyEl.innerHTML = newOpts.body;
            if (newOpts.stepLabel != null) stepEl.textContent = newOpts.stepLabel;
            position();
        },
        close: function() {
            window.removeEventListener('resize', onResize);
            window.removeEventListener('scroll', onResize, true);
            if (root.parentNode) root.parentNode.removeChild(root);
        },
        reposition: position,
    };
};

/**
 * EOS_UI.searchBar(opts) — global app + vault search input.
 * Mounts into opts.mount (DOM element or selector). Returns {focus, destroy}.
 *
 * opts:
 *   mount         — required. Container element or selector string.
 *   placeholder   — input placeholder text. Default "Search apps, vault, commands…".
 *   showKbdHint   — show "/" hint chip on the right. Default true.
 *   focusKey      — global key that focuses the bar from anywhere. Default "/". Pass null to disable.
 *   onSelectApp   — fn(app) called when a result is chosen. Default: location.href = app.web_prefix + '/'.
 *   onFallback    — fn(query) called when Enter pressed with no app match. Default: location.href = '/search/?q=' + query.
 *   maxResults    — cap on app matches. Default 8.
 *   filter        — fn(app, query) → bool. Override the default name/id/desc match.
 */
EOS_UI.searchBar = function(opts) {
    opts = opts || {};
    var mount = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
    if (!mount) { console.warn('EOS_UI.searchBar: no mount element'); return null; }
    var placeholder = opts.placeholder || 'Search apps, vault, commands…';
    var showKbd = opts.showKbdHint !== false;
    var focusKey = opts.focusKey === undefined ? '/' : opts.focusKey;
    var maxResults = opts.maxResults || 8;
    var onSelect = opts.onSelectApp || function(a) { location.href = (a.web_prefix || ('/' + a.id)) + '/'; };
    var onFallback = opts.onFallback || function(q) { location.href = '/search/?q=' + encodeURIComponent(q); };
    // Optional smart slot {when(q), label(q), hint, run(q, ctl)} — when present
    // and when(q) is true, a first-class item is prepended at flat[0] so plain
    // Enter fires it (intent routing). Absent ⇒ behavior unchanged.
    var smart = opts.smart || null;
    var filter = opts.filter || function(a, q) {
        return (a.name || '').toLowerCase().indexOf(q) >= 0
            || (a.id || '').toLowerCase().indexOf(q) >= 0
            || (a.description || '').toLowerCase().indexOf(q) >= 0;
    };

    function escHtml(s) {
        if (s == null) return '';
        return String(s).replace(/[&<>"']/g, function(c){
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
        });
    }

    var wrap = document.createElement('div');
    wrap.className = 'eos-search';
    wrap.innerHTML =
        '<span class="eos-search-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg></span>' +
        '<input class="eos-search-input" type="text" placeholder="' + escHtml(placeholder) + '" autocomplete="off" spellcheck="false">' +
        (showKbd && focusKey ? '<span class="eos-search-kbd">' + escHtml(focusKey) + '</span>' : '') +
        '<div class="eos-search-results"></div>';
    mount.appendChild(wrap);

    var input = wrap.querySelector('.eos-search-input');
    var results = wrap.querySelector('.eos-search-results');
    var apps = [];
    var idx = -1;

    fetch('/api/apps').then(function(r){return r.json();}).then(function(list){
        apps = (list || []).filter(function(a){ return a && (a.web_prefix || a.id); });
    }).catch(function(){});

    // currentItems is the flat list shown in render order; each item is
    // {kind: 'app'|'note'|'fallback', payload: ...}. Keyboard arrows + clicks
    // index into this array. Section headers don't go into it.
    var currentItems = [];
    var noteFetchSeq = 0;
    var noteDebounce = null;
    var lastNotes = [];
    var lastNotesQuery = '';

    function renderSections(qLower, qRaw, notesLoading) {
        var html = '';
        var flat = [];
        var matchedApps = apps.filter(function(a){ return filter(a, qLower); }).slice(0, maxResults);

        if (smart && smart.when && smart.when(qRaw)) {
            flat.push({kind: 'smart', payload: {q: qRaw}});
            html += '<div class="eos-search-item' + (idx === 0 ? ' active' : '') + '" data-i="0">' +
                '<span class="eos-search-item-icon">↵</span>' +
                '<span class="eos-search-item-name">' + escHtml(smart.label ? smart.label(qRaw) : qRaw) + '</span>' +
                '<span class="eos-search-item-desc">' + escHtml(smart.hint || '') + '</span>' +
                '</div>';
        }

        if (matchedApps.length) {
            html += '<div class="eos-search-section">Apps</div>';
            matchedApps.forEach(function(a){
                flat.push({kind: 'app', payload: a});
                var i = flat.length - 1;
                html += '<div class="eos-search-item' + (i === idx ? ' active' : '') + '" data-i="' + i + '">' +
                    '<span class="eos-search-item-icon">' + escHtml(a.icon || '\u25A2') + '</span>' +
                    '<span class="eos-search-item-name">' + escHtml(a.name || a.id) + '</span>' +
                    '<span class="eos-search-item-desc">' + escHtml((a.description || '').slice(0, 60)) + '</span>' +
                    '</div>';
            });
        }

        if (lastNotes.length || notesLoading) {
            html += '<div class="eos-search-section">Notes' +
                    (notesLoading ? ' <span class="eos-search-loading">searching...</span>' : '') +
                    '</div>';
            lastNotes.slice(0, maxResults).forEach(function(n){
                flat.push({kind: 'note', payload: n});
                var i = flat.length - 1;
                var title = n.title || (n.path || '').split('/').pop() || 'untitled';
                var snippet = (n.snippet || n.preview || n.excerpt || '').replace(/\s+/g, ' ').slice(0, 80);
                html += '<div class="eos-search-item' + (i === idx ? ' active' : '') + '" data-i="' + i + '">' +
                    '<span class="eos-search-item-icon">\u{1F4DD}</span>' +
                    '<span class="eos-search-item-name">' + escHtml(title) + '</span>' +
                    '<span class="eos-search-item-desc">' + escHtml(snippet) + '</span>' +
                    '</div>';
            });
        }

        // Always-on fallback link to the full search page
        html += '<div class="eos-search-section">Other</div>';
        flat.push({kind: 'fallback', payload: {q: qRaw}});
        var fi = flat.length - 1;
        html += '<div class="eos-search-item' + (fi === idx ? ' active' : '') + '" data-i="' + fi + '">' +
            '<span class="eos-search-item-icon">\u{1F50D}</span>' +
            '<span class="eos-search-item-name">Open full search</span>' +
            '<span class="eos-search-item-desc">All vault matches for &ldquo;' + escHtml(qRaw) + '&rdquo;</span>' +
            '</div>';

        results.innerHTML = html;
        results.classList.add('open');
        currentItems = flat;
    }

    function fetchNotes(qRaw) {
        var seq = ++noteFetchSeq;
        if (qRaw !== lastNotesQuery) { lastNotes = []; lastNotesQuery = qRaw; }
        renderSections(qRaw.toLowerCase(), qRaw, true);
        fetch('/search/api/search?q=' + encodeURIComponent(qRaw) + '&top=8')
            .then(function(r){ return r.json(); })
            .then(function(data){
                if (seq !== noteFetchSeq) return;
                lastNotes = (data && data.results) || [];
                renderSections(qRaw.toLowerCase(), qRaw, false);
            })
            .catch(function(){
                if (seq !== noteFetchSeq) return;
                renderSections(qRaw.toLowerCase(), qRaw, false);
            });
    }

    input.addEventListener('input', function(){
        var qRaw = this.value.trim();
        if (!qRaw) {
            results.classList.remove('open');
            idx = -1; currentItems = []; lastNotes = []; lastNotesQuery = '';
            clearTimeout(noteDebounce);
            return;
        }
        idx = 0;
        renderSections(qRaw.toLowerCase(), qRaw, false);
        clearTimeout(noteDebounce);
        noteDebounce = setTimeout(function(){ fetchNotes(qRaw); }, 200);
    });

    input.addEventListener('keydown', function(e){
        var items = results.querySelectorAll('.eos-search-item');
        if (e.key === 'ArrowDown') {
            e.preventDefault(); idx = Math.min(idx + 1, items.length - 1); paintActive(items);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault(); idx = Math.max(idx - 1, 0); paintActive(items);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            var picked = currentItems[idx];
            if (picked) pickItem(picked);
            else if (this.value.trim()) onFallback(this.value.trim());
        } else if (e.key === 'Escape') {
            results.classList.remove('open'); this.blur();
        }
    });

    function pickItem(item) {
        if (item.kind === 'smart') {
            var i = currentItems.indexOf(item);
            var nameEl = results.querySelector('.eos-search-item[data-i="' + i + '"] .eos-search-item-name');
            return smart.run(item.payload.q || input.value.trim(), {
                setBusy: function(label){ if (nameEl) nameEl.textContent = label; },
                close: function(){ results.classList.remove('open'); input.value = ''; },
            });
        }
        if (item.kind === 'app') return onSelect(item.payload);
        if (item.kind === 'note') {
            var path = item.payload.path || '';
            if (path) location.href = '/search/?q=' + encodeURIComponent(input.value.trim()) + '&open=' + encodeURIComponent(path);
            else onFallback(input.value.trim());
            return;
        }
        if (item.kind === 'fallback') return onFallback(item.payload.q || input.value.trim());
    }

    results.addEventListener('click', function(e){
        var item = e.target.closest('.eos-search-item');
        if (!item) return;
        var picked = currentItems[+item.dataset.i];
        if (picked) pickItem(picked);
    });

    function paintActive(items) {
        items.forEach(function(el, i){ el.classList.toggle('active', i === idx); });
    }

    function onDocClick(e){
        if (!wrap.contains(e.target)) results.classList.remove('open');
    }
    document.addEventListener('click', onDocClick);

    var onGlobalKey = null;
    if (focusKey) {
        onGlobalKey = function(e){
            if (e.key === focusKey && document.activeElement !== input
                && !e.target.matches('input, textarea, [contenteditable]')) {
                e.preventDefault();
                input.focus();
            }
        };
        document.addEventListener('keydown', onGlobalKey);
    }

    return {
        focus: function(){ input.focus(); },
        value: function(){ return input.value.trim(); },
        clear: function(){ input.value = ''; results.classList.remove('open'); idx = -1; currentItems = []; },
        destroy: function(){
            document.removeEventListener('click', onDocClick);
            if (onGlobalKey) document.removeEventListener('keydown', onGlobalKey);
            wrap.remove();
        },
    };
};

// EOS_UI.methodPicker({mount, app, endpoint, value, onChange})
//   Calculator framework — renders a <select> of methods declared at
//   /<app>/api/methods. Methods with available=false are disabled with the
//   disabled_reason rendered in the option text. Returns {get, set, refresh}.
//
//   Pair with [[provides.methods.<endpoint>]] manifest blocks. See
//   emptyos/sdk/method_registry.py for the contract.
EOS_UI.methodPicker = function(opts) {
    var mount = typeof opts.mount === 'string'
        ? document.querySelector(opts.mount) : opts.mount;
    if (!mount) return null;
    var app = opts.app;
    var endpoint = opts.endpoint || 'solve';
    var current = opts.value || '';
    var onChange = typeof opts.onChange === 'function' ? opts.onChange : function(){};

    function render(items) {
        var defaultId = '';
        items.forEach(function(m){ if (m.default) defaultId = m.id; });
        var picked = current || defaultId || (items[0] && items[0].id) || '';
        current = picked;
        var html = '<label class="eos-method-picker">';
        html += '<span class="eos-method-picker-label">Method</span>';
        html += '<select class="eos-method-picker-select">';
        items.forEach(function(m){
            var label = EOS_UI.esc(m.label || m.id);
            if (!m.available) label += ' — ' + EOS_UI.esc(m.disabled_reason || 'unavailable');
            html += '<option value="' + EOS_UI.escAttr(m.id) + '"' +
                (m.id === picked ? ' selected' : '') +
                (!m.available ? ' disabled' : '') +
                (m.description ? ' title="' + EOS_UI.escAttr(m.description) + '"' : '') +
                '>' + label + (m.default ? ' (default)' : '') + '</option>';
        });
        html += '</select>';
        if (items.length) {
            var picked_meta = items.find(function(m){ return m.id === picked; });
            if (picked_meta && picked_meta.references && picked_meta.references.length) {
                html += '<span class="eos-method-picker-refs">refs: ' +
                    picked_meta.references.map(EOS_UI.esc).join(' · ') + '</span>';
            }
        }
        html += '</label>';
        mount.innerHTML = html;
        var sel = mount.querySelector('select');
        sel.addEventListener('change', function(){
            current = sel.value;
            onChange(current, items.find(function(m){ return m.id === current; }));
            // Re-render to refresh the refs line for the new pick
            render(items);
        });
        // Fire onChange once at boot so callers see the initial value
        onChange(current, items.find(function(m){ return m.id === current; }));
    }

    function refresh() {
        return fetch('/' + app + '/api/methods')
            .then(function(r){ return r.json(); })
            .then(function(j){
                // Endpoint-aware first (the canonical {ep: [...]} map from
                // CalculatorRoutesMixin), then the single-endpoint {methods}
                // convenience — so the picker works against both shapes.
                render((endpoint && j[endpoint]) || j.methods || []);
            })
            .catch(function(e){
                mount.innerHTML = '<div class="eos-method-picker-error">' +
                    EOS_UI.esc('method picker failed: ' + e.message) + '</div>';
            });
    }

    refresh();
    return {
        get: function(){ return current; },
        set: function(v){ current = v; refresh(); },
        refresh: refresh,
    };
};

// EOS_UI.conformancePanel({mount, app, title})
//   Calculator framework — the "validated against" contract surface for an app
//   declaring [[provides.conformance.<endpoint>]]. Lists each case (label +
//   references) and a Run button that POSTs /<app>/api/conformance/run, then
//   shows pass/fail per case with each field's relative error vs its tolerance.
//   Feature-detects: if /<app>/api/conformance 404s or has no cases, renders
//   nothing (safe to drop into any page). Collapsed by default.
//   Returns {refresh, run, el}. See emptyos/sdk/conformance.py for the contract.
EOS_UI.conformancePanel = function(opts) {
    var mount = typeof opts.mount === 'string'
        ? document.querySelector(opts.mount) : opts.mount;
    if (!mount) return null;
    var app = opts.app;
    var title = opts.title || 'Validated against standards';
    var cases = [];

    function fmt(v) {
        if (v == null) return '—';
        if (typeof v === 'number') return String(Math.round(v * 10000) / 10000);
        return String(v);
    }

    function diffsTable(result) {
        if (!result || !result.methods) return '';
        var rows = [];
        Object.keys(result.methods).forEach(function(mid){
            var m = result.methods[mid] || {};
            (m.diffs || []).forEach(function(d){
                rows.push('<tr class="' + (d.passed ? 'ok' : 'bad') + '">' +
                    '<td>' + EOS_UI.esc(mid) + '</td>' +
                    '<td>' + EOS_UI.esc(d.field) + '</td>' +
                    '<td>' + EOS_UI.esc(fmt(d.expected)) + '</td>' +
                    '<td>' + EOS_UI.esc(fmt(d.got)) + '</td>' +
                    '<td>' + (d.rel_pct == null ? '—' : EOS_UI.esc(d.rel_pct) + '%') + '</td>' +
                    '<td>≤' + EOS_UI.esc(d.tolerance_pct) + '%</td></tr>');
            });
            if (m.error) {
                rows.push('<tr class="bad"><td>' + EOS_UI.esc(mid) +
                    '</td><td colspan="5">' + EOS_UI.esc(m.error) + '</td></tr>');
            }
        });
        if (!rows.length) return '';
        return '<table class="eos-conf-diffs"><thead><tr><th>method</th><th>field</th>' +
            '<th>expected</th><th>got</th><th>Δ</th><th>tol</th></tr></thead><tbody>' +
            rows.join('') + '</tbody></table>';
    }

    function caseRow(c, result) {
        var refs = (c.references && c.references.length)
            ? '<span class="eos-conf-refs">' + c.references.map(EOS_UI.esc).join(' · ') + '</span>' : '';
        var badge = '';
        if (result) {
            badge = result.passed
                ? '<span class="eos-conf-badge ok">✓ pass</span>'
                : '<span class="eos-conf-badge bad">✗ fail</span>';
        }
        return '<div class="eos-conf-case"><div class="eos-conf-case-head">' + badge +
            '<span class="eos-conf-case-label">' + EOS_UI.esc(c.label || c.case_id) + '</span>' +
            refs + '</div>' + diffsTable(result) + '</div>';
    }

    function render(results) {
        if (!cases.length) { mount.innerHTML = ''; return; }
        var resById = {};
        (results || []).forEach(function(r){ resById[r.case_id] = r; });
        mount.innerHTML =
            '<details class="eos-conf-panel"><summary class="eos-conf-summary">' +
            '<span class="eos-conf-title">🛡 ' + EOS_UI.esc(title) + '</span>' +
            '<span class="eos-conf-count">' + cases.length + '</span>' +
            '<button type="button" class="eos-conf-run" title="Run all conformance cases">Run</button></summary>' +
            '<div class="eos-conf-body">' +
            cases.map(function(c){ return caseRow(c, resById[c.case_id]); }).join('') +
            '</div></details>';
        var btn = mount.querySelector('.eos-conf-run');
        if (btn) btn.addEventListener('click', function(e){
            e.preventDefault(); e.stopPropagation(); run();
        });
    }

    function run() {
        var btn = mount.querySelector('.eos-conf-run');
        if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
        return fetch('/' + app + '/api/conformance/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: '{}',
            })
            .then(function(r){ return r.json(); })
            .then(function(j){ render(j.results || []); })
            .catch(function(){ if (btn) { btn.disabled = false; btn.textContent = 'Run'; } });
    }

    function refresh() {
        return fetch('/' + app + '/api/conformance')
            .then(function(r){ return r.ok ? r.json() : {cases: []}; })
            .then(function(j){ cases = j.cases || []; render(null); })
            .catch(function(){ mount.innerHTML = ''; });
    }

    refresh();
    return { refresh: refresh, run: run, el: mount };
};

// EOS_UI.streamPane(mountEl, opts) — dark live-progress pane for LLM / agent
// streams. Extracted from apps/viz/ (first consumer); designed for any app
// that has a long-running self.think_stream() or claude-cli subprocess and
// wants to show progress live instead of a "thinking..." spinner.
//
// Usage:
//   var pane = EOS_UI.streamPane(document.getElementById('pane-host'));
//   pane.open('Waiting for first token…');
//   var res = await fetch('/myapp/api/run-stream', {...});
//   await pane.consumeNdjson(res, {
//       onStarted: function(ev) { pane.setPhase('Streaming ' + ev.shape); },
//       onChunk:   function(ev) { pane.appendText(ev.text); pane.setCounter(EOS_UI.fmtBytes(ev.bytes) + ' · ' + EOS_UI.fmtElapsed(ev.elapsed_ms)); },
//       onDone:    function(ev) { pane.setDone('✓ Done', (ev.size_kb||0)+' KB'); },
//   });
//
// Default ndjson event handlers cover {type:"started"|"chunk"|"tool_use"|
// "tool_result"|"done"|"error"}. Override any of them via the handlers arg.
//
// The pane is reusable — calling open() again clears the body.
EOS_UI.streamPane = function(mountEl, opts) {
    if (!mountEl) return null;
    opts = opts || {};
    var pane = document.createElement('div');
    pane.className = 'eos-stream-pane';
    pane.innerHTML =
        '<div class="eos-stream-head">' +
            '<span class="eos-stream-phase">Idle</span>' +
            '<span class="eos-stream-counter"></span>' +
        '</div>' +
        '<pre class="eos-stream-body"></pre>';
    mountEl.innerHTML = '';
    mountEl.appendChild(pane);
    var phaseEl = pane.querySelector('.eos-stream-phase');
    var counterEl = pane.querySelector('.eos-stream-counter');
    var bodyEl = pane.querySelector('.eos-stream-body');

    function scroll() { bodyEl.scrollTop = bodyEl.scrollHeight; }

    var api = {
        el: pane,
        open: function(initialPhase) {
            mountEl.style.display = '';
            pane.style.display = 'flex';
            bodyEl.textContent = '';
            phaseEl.textContent = initialPhase || 'Waiting for first token…';
            counterEl.textContent = '';
        },
        close: function() { pane.style.display = 'none'; },
        setPhase: function(text) { phaseEl.textContent = text || ''; },
        setCounter: function(text) { counterEl.textContent = text || ''; },
        appendText: function(text) {
            if (text == null) return;
            bodyEl.appendChild(document.createTextNode(text));
            scroll();
        },
        appendBlock: function(html) {
            bodyEl.insertAdjacentHTML('beforeend', html || '');
            scroll();
        },
        appendTool: function(toolName, summary) {
            var head = '<div class="eos-stream-tool-head">&rarr; ' + EOS_UI.esc(toolName || 'tool') + '</div>';
            var body = summary ? '<div class="eos-stream-tool-body">' + EOS_UI.esc(summary) + '</div>' : '';
            api.appendBlock('<div class="eos-stream-tool">' + head + body + '</div>');
        },
        appendResult: function(preview, maxLen) {
            var n = maxLen || 200;
            var s = (preview || '').slice(0, n);
            if (!s) return;
            var more = preview && preview.length > n ? '&hellip;' : '';
            api.appendBlock('<div class="eos-stream-result">' + EOS_UI.esc(s) + more + '</div>');
        },
        appendProse: function(text, isError) {
            var cls = 'eos-stream-prose' + (isError ? ' err' : '');
            api.appendBlock('<div class="' + cls + '">' + EOS_UI.esc(text || '') + '</div>');
        },
        setDone: function(phaseText, counterText) {
            phaseEl.textContent = phaseText || '✓ Done';
            if (counterText != null) counterEl.textContent = counterText;
        },
        setError: function(msg) {
            phaseEl.textContent = '✗ Error';
            api.appendProse('Error: ' + (msg || 'unknown'), true);
        },
        // Drain an ndjson body. handlers is {onStarted, onChunk, onToolUse,
        // onToolResult, onDone, onError, onEvent}. Each gets the parsed event
        // object; onEvent (if provided) is called for EVERY event regardless
        // of type — useful for caller-side bookkeeping. Returns the last
        // {type:"done"} or {type:"error"} event for convenience.
        consumeNdjson: function(res, handlers) {
            handlers = handlers || {};
            return EOS_UI._consumeNdjson(res, function(ev) {
                if (handlers.onEvent) handlers.onEvent(ev);
                var t = ev && ev.type;
                if (t === 'started' && handlers.onStarted) handlers.onStarted(ev);
                else if (t === 'chunk' && handlers.onChunk) handlers.onChunk(ev);
                else if (t === 'tool_use' && handlers.onToolUse) handlers.onToolUse(ev);
                else if (t === 'tool_result' && handlers.onToolResult) handlers.onToolResult(ev);
                else if (t === 'done' && handlers.onDone) handlers.onDone(ev);
                else if (t === 'error' && handlers.onError) handlers.onError(ev);
            });
        },
    };
    return api;
};

// Low-level ndjson body reader. Reads line-delimited JSON from a fetch()
// response body, invoking onEvent(parsedObj) per line. Non-JSON lines are
// skipped silently (treated as heartbeats / debug). Used by streamPane;
// also exposed so apps can use it directly without a pane.
EOS_UI._consumeNdjson = async function(res, onEvent) {
    var reader = res.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buf = '';
    while (true) {
        var r = await reader.read();
        if (r.done) break;
        buf += decoder.decode(r.value, {stream: true});
        var lines = buf.split('\n');
        buf = lines.pop();
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line) continue;
            try { onEvent(JSON.parse(line)); }
            catch (e) { /* heartbeat or debug — ignore */ }
        }
    }
    if (buf.trim()) {
        try { onEvent(JSON.parse(buf.trim())); } catch (e) {}
    }
};

EOS_UI.fmtBytes = function(n) {
    n = +n || 0;
    if (n < 1024) return n + ' B';
    return (n / 1024).toFixed(1) + ' KB';
};

EOS_UI.fmtElapsed = function(ms) {
    var s = (+ms || 0) / 1000;
    if (s < 60) return s.toFixed(1) + 's';
    return Math.floor(s/60) + 'm' + Math.round(s%60) + 's';
};

// EOS_UI.summarizeToolInput(tool, input) — one-line summary of a claude-cli
// tool_use input, for display inside an EOS_UI.streamPane tool block.
// Knows the common Anthropic tool names; falls back to truncated JSON.
EOS_UI.summarizeToolInput = function(tool, input) {
    if (!input || typeof input !== 'object') return '';
    if (tool === 'Read') return input.file_path || input.path || '';
    if (tool === 'Edit' || tool === 'Write') {
        var path = input.file_path || input.path || '';
        var oldS = (input.old_string || '').slice(0, 80);
        var newS = (input.new_string || input.content || '').slice(0, 80);
        return path +
            (oldS ? '\n  - ' + oldS + (input.old_string && input.old_string.length > 80 ? '…' : '') : '') +
            (newS ? '\n  + ' + newS : '');
    }
    if (tool === 'Glob') return input.pattern || '';
    if (tool === 'Grep') return (input.pattern || '') + (input.path ? '  in ' + input.path : '');
    if (tool === 'Bash') return (input.command || '').slice(0, 200);
    try { return JSON.stringify(input).slice(0, 200); } catch (e) { return ''; }
};

/* ── EOS_UI.store — tiny state→render helper (NOT a framework) ───────────
   Makes the documented STATE + render() convention the path of least
   resistance (.claude/rules/app-conventions-for-export.md §1):

       function row(it) { return '<li>' + EOS_UI.esc(it.text) + '</li>'; }
       function render(s) {
           document.getElementById('list').innerHTML = s.items.map(row).join('');
       }
       var S = EOS_UI.store({items: [], filter: ''}, render);
       // after a fetch:
       S.set({items: data.items});          // merge patch → render(state)
       S.set('filter', 'done');             // single-key form → render(state)

   render(state) is re-invoked on every set(). No virtual DOM, no diffing,
   no subscriptions — innerHTML re-render stays the model. A set() issued
   from inside render() is coalesced into one follow-up render, not a
   recursive stack. Export/AI note: keeping render a pure function of state
   is what lets the export shim pre-populate state and lets AI page edits
   operate at the state level instead of splicing HTML.
*/
EOS_UI.store = function(initial, render) {
    var state = {};
    for (var k in (initial || {})) {
        if (Object.prototype.hasOwnProperty.call(initial, k)) state[k] = initial[k];
    }
    var rendering = false;
    var queued = false;
    function invoke() {
        if (typeof render !== 'function') return;
        if (rendering) { queued = true; return; }
        rendering = true;
        try {
            // Drain iteratively, not recursively: a render() that calls set()
            // must not grow the stack once per pass. The cap turns a
            // set-on-every-render loop into a loud failure instead of a
            // frozen tab.
            var passes = 0;
            do {
                queued = false;
                render(state);
                if (++passes >= 50) {
                    queued = false;
                    throw new Error('EOS_UI.store: render() kept calling set() — check for a set/render loop');
                }
            } while (queued);
        } finally {
            rendering = false;
            queued = false;
        }
    }
    return {
        // Returns the live state object, not a copy — mutating it skips
        // render(). Go through set() so the DOM can't drift from the state.
        get: function(key) { return key === undefined ? state : state[key]; },
        set: function(patch, value) {
            if (typeof patch === 'string') {
                state[patch] = value;
            } else {
                for (var k in (patch || {})) {
                    if (Object.prototype.hasOwnProperty.call(patch, k)) state[k] = patch[k];
                }
            }
            invoke();
            return this;
        },
        // Render without changing state (initial mount, or after an external
        // mutation you couldn't route through set()).
        render: invoke,
    };
};

/* ── Keyboard accessibility for [onclick] divs/spans ─────────────────────
   Any non-native-focusable element with an onclick handler gets:
     - tabindex="0" so it joins the tab order
     - role="button" so screen readers announce it
     - Enter/Space firing click via a single delegated keydown listener
   Skips: native focusables (button/a/input/etc.), modal backdrops that
   only fire on event.target===this, and elements explicitly opted out
   with data-no-key-enhance.
   Re-runs on DOM mutations so dynamically rendered cards are covered.
*/
(function() {
    var NATIVE = {BUTTON:1, A:1, INPUT:1, SELECT:1, TEXTAREA:1, LABEL:1, SUMMARY:1};
    function enhance(root) {
        var nodes = (root || document).querySelectorAll('[onclick]');
        for (var i = 0; i < nodes.length; i++) {
            var el = nodes[i];
            if (NATIVE[el.tagName]) continue;
            if (el.hasAttribute('data-no-key-enhance')) continue;
            var oc = el.getAttribute('onclick') || '';
            // Modal backdrop pattern: click only fires when target is the backdrop itself
            if (oc.indexOf('event.target===this') !== -1 || oc.indexOf('event.target === this') !== -1) continue;
            if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
            if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
        }
    }
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var el = e.target;
        if (!el || !el.hasAttribute || !el.hasAttribute('onclick')) return;
        if (NATIVE[el.tagName]) return;
        // Don't hijack Space inside text inputs nested in a clickable wrapper
        var ae = document.activeElement;
        if (ae && (NATIVE[ae.tagName] || ae.isContentEditable)) return;
        e.preventDefault();
        el.click();
    });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { enhance(); watch(); });
    } else {
        enhance(); watch();
    }
    function watch() {
        if (!window.MutationObserver) return;
        var mo = new MutationObserver(function(muts) {
            for (var i = 0; i < muts.length; i++) {
                var added = muts[i].addedNodes;
                for (var j = 0; j < added.length; j++) {
                    var n = added[j];
                    if (n.nodeType === 1) enhance(n);
                }
            }
        });
        mo.observe(document.body, {childList: true, subtree: true});
    }
})();
