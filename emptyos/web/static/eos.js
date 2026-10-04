/**
 * EmptyOS Page Shell — shared initialization for all app pages.
 *
 * Usage: <script src="/static/eos.js"></script>
 *
 * Provides:
 *   - Theme loading
 *   - EOS.api(path) helper
 *   - EOS.nav(appName) nav bar
 *   - EOS.realtime connection
 *   - esc() HTML escape
 */
(function() {
    'use strict';

    // Dev overlay — `?debug=clickable` outlines z-order-intercepted CTAs.
    // Self-contained module loaded only when the query param is present so
    // production users never see it.
    try {
        if (location.search.indexOf('debug=clickable') >= 0) {
            var s = document.createElement('script');
            s.src = '/static/eos-debug-clickable.js';
            s.async = true;
            (document.head || document.documentElement).appendChild(s);
        }
    } catch (e) { /* ignore */ }

    // Dev overlays — `?debug=readability` outlines low-contrast / tiny / faded
    // text with WCAG ratios; `?debug=affordance` outlines interaction-affordance
    // gaps (content clipped with no scroll; buttons acting as tabs without
    // roles). Both audits sit on the shared eos-audit-walk.js harness, so it
    // must EXECUTE FIRST: dynamically-inserted scripts default to async (i.e.
    // whichever lands first wins), and `async = false` is what restores
    // insertion-order execution.
    try {
        var _dbgAudits = [];
        if (location.search.indexOf('debug=readability') >= 0) _dbgAudits.push('eos-readability.js');
        if (location.search.indexOf('debug=affordance') >= 0) _dbgAudits.push('eos-ui-affordance.js');
        if (_dbgAudits.length) {
            var _head = document.head || document.documentElement;
            var _loadAudit = function(file) {
                var s = document.createElement('script');
                s.src = '/static/' + file;
                s.async = false;            // preserve insertion order (harness before audit)
                _head.appendChild(s);
            };
            _loadAudit('eos-audit-walk.js');
            _dbgAudits.forEach(_loadAudit);
        }
    } catch (e) { /* ignore */ }

    // --- Theme ---
    // Use classList — preserves other classes (e.g. `eos-full-screen` injected
    // by the kernel for apps with `[app] full_screen = true`). Strip any
    // existing `theme-*` first so swapping themes works idempotently.
    // THE theme registry. `EOS.THEMES` is assigned from this same array later
    // in the file (not a copy), and the pre-paint bootstrap in server.py
    // deliberately holds no list at all — so a new theme is added here, in
    // EOS.THEME_LABELS, in theme.css, and in the settings schema. Nowhere else.
    var _KNOWN_THEMES = [
        'eos', 'digital-garden', 'soft-light', 'tatami',
        'warm-dark', 'void-dark', 'nord', 'forest', 'deep-sea', 'vino',
    ];
    function _setThemeClass(name) {
        // A stale/dropped theme id (e.g. one left in localStorage after a theme
        // was renamed/removed, like 'botanical-scroll') maps to an undefined
        // `theme-<x>` class → every token falls back to bare :root and the UI
        // renders pale/unstyled. Validate + self-heal to 'eos'.
        if (_KNOWN_THEMES.indexOf(name) < 0) {
            name = 'eos';
            try { localStorage.setItem('eos-theme', 'eos'); } catch(e) {}
        }
        var html = document.documentElement;
        Array.from(html.classList).forEach(function(c) {
            if (c.indexOf('theme-') === 0) html.classList.remove(c);
        });
        html.classList.add('theme-' + name);
    }
    _setThemeClass(localStorage.getItem('eos-theme') || 'eos');

    // --- Redact mode ---
    // Three triggers, all toggle html.eos-redact:
    //   1. ?demo=1 / ?redact=1 URL param   — per-iframe (ppt embed slides)
    //   2. localStorage 'eos.presentation' — fast cross-tab sync, no flicker
    //   3. Server settings 'presentation.enabled' (fetched below) — source of
    //      truth, drives the backend regex middleware too. localStorage is a
    //      pre-paint hint so a fresh tab doesn't flash personal data before
    //      the fetch completes.
    try {
        var qp = new URLSearchParams(location.search);
        if (qp.has('demo') || qp.has('redact')) {
            document.documentElement.classList.add('eos-redact');
        }
        if (localStorage.getItem('eos.presentation') === '1') {
            document.documentElement.classList.add('eos-redact');
        }
    } catch (e) {}

    // --- API base ---
    var base = location.protocol + '//' + location.host;

    // --- EOS namespace ---
    window.EOS = window.EOS || {};
    EOS.base = base;

    // Trusted app-identity icon registry. Manifests may select an id from this
    // list, but never supply SVG markup or a sprite URL. Four separately
    // painted symbols let the active theme recolor an icon without replacing
    // the artwork or issuing a new request.
    EOS.APP_ICON_IDS = [
        'hub', 'settings', 'store', 'task', 'journal', 'projects', 'focus', 'search',
        'assistant', 'quick-action', 'kb', 'cad', 'voice-assistant', 'boards',
        'publish', 'dictionary', 'code', 'cable-network', 'jobs', 'explore',
        'viz', 'canvas', 'worklog', 'studio',
    ];
    var _APP_ICON_ID_SET = {};
    var _appIconSpriteRevision = 'live';
    EOS.APP_ICON_IDS.forEach(function(id) { _APP_ICON_ID_SET[id] = true; });
    EOS.registerAppIconIds = function(ids) {
        (ids || []).forEach(function(id) {
            if (/^[a-z0-9][a-z0-9-]{0,47}$/.test(id || '')) _APP_ICON_ID_SET[id] = true;
        });
    };
    EOS.setAppIconRevision = function(revision) {
        _appIconSpriteRevision = String(revision == null ? 'live' : revision).replace(/[^a-zA-Z0-9_-]/g, '') || 'live';
    };
    // An app with no icon yet (187 of 230 at time of writing) gets a letter
    // monogram rather than a bare glyph: in a dense list a lone box reads as an
    // unchecked checkbox. Drawn as an SVG on the same 1024 viewBox as a real
    // icon so it scales with its slot identically — a CSS font-size could not,
    // since the slot is 40px on a hub card and ~18px in the nav crumb.
    function _monogramSvg(label) {
        var text = String(label == null ? '' : label).trim();
        if (!text) return '';
        var m = text.match(/[A-Za-z0-9]/);
        var ch = m ? m[0].toUpperCase() : text.charAt(0);
        // Geometry is expressed in the sprite's own 1024 viewBox so the tile
        // matches a real icon's optical weight at every slot size: a 32px inset
        // leaves the same margin the drawn icons use, rx 224 (~22%) matches
        // their corner radius, and y 536 rather than 512 nudges the glyph down
        // off the cap-height centre so it reads optically centred.
        return '<svg class="eos-app-icon eos-app-icon-monogram" viewBox="0 0 1024 1024" aria-hidden="true" focusable="false">' +
            '<rect class="eos-app-icon-monogram-bg" x="32" y="32" width="960" height="960" rx="224"></rect>' +
            '<text class="eos-app-icon-monogram-text" x="512" y="536" text-anchor="middle" dominant-baseline="central">' +
            esc(ch) + '</text></svg>';
    }
    EOS.appIcon = function(iconId, fallback, label) {
        if (!_APP_ICON_ID_SET[iconId]) {
            // A manifest emoji, when the app declares one, still wins.
            if (fallback) {
                return '<span class="eos-app-icon-fallback" aria-hidden="true">' +
                    esc(fallback) + '</span>';
            }
            return _monogramSvg(label) ||
                '<span class="eos-app-icon-fallback" aria-hidden="true">▢</span>';
        }
        var stem = '/api/app-icons/sprite?revision=' + _appIconSpriteRevision + '#app-' + iconId + '-';
        return '<svg class="eos-app-icon" viewBox="0 0 1024 1024" aria-hidden="true" focusable="false">' +
            '<use class="eos-app-icon-paper" href="' + stem + 'paper"></use>' +
            '<use class="eos-app-icon-accent" href="' + stem + 'accent"></use>' +
            '<use class="eos-app-icon-accent-2" href="' + stem + 'accent-2"></use>' +
            '<use class="eos-app-icon-ink" href="' + stem + 'ink"></use>' +
            '</svg>';
    };

    // BYOK — visitor-supplied API keys for cloud providers. Stored in
    // localStorage by the Settings panel; injected as headers on every
    // EOS.api / EOS.post / EOS.stream call. The server's byok_middleware
    // (emptyos/web/server.py) extracts these headers and routes them to
    // openai_compat.py via a per-request contextvar. One visitor's key
    // never bleeds into another visitor's request.
    var BYOK_HEADERS = {
        openai: 'X-User-OpenAI-Key',
        anthropic: 'X-User-Anthropic-Key',
    };
    EOS.byok = {
        get: function(provider) {
            try { return (localStorage.getItem('eos.byok.' + provider) || '').trim(); }
            catch (e) { return ''; }
        },
        set: function(provider, key) {
            try {
                if (key) localStorage.setItem('eos.byok.' + provider, key);
                else localStorage.removeItem('eos.byok.' + provider);
            } catch (e) {}
        },
        list: function() {
            var out = {};
            Object.keys(BYOK_HEADERS).forEach(function(p){
                var k = EOS.byok.get(p);
                if (k) out[p] = k;
            });
            return out;
        },
    };
    function _injectByokHeaders(opts) {
        var keys = EOS.byok.list();
        if (!Object.keys(keys).length) return opts;
        opts = opts || {};
        var headers = Object.assign({}, opts.headers || {});
        Object.keys(keys).forEach(function(provider){
            headers[BYOK_HEADERS[provider]] = keys[provider];
        });
        return Object.assign({}, opts, {headers: headers});
    }

    // Is this request target the daemon itself? THE security property of the
    // fetch patch below: a visitor's API key must never ride a cross-origin
    // request. Unparseable → false, so it fails closed. Pure + exported so it
    // can be pinned under `node --test`.
    //
    // Both sides go through URL rather than reading `location.origin`, which is
    // an OPTIONAL property (absent in some sandboxed/embedded contexts and on
    // `about:blank`). Comparing a real origin against `undefined` would fail
    // closed EVERYWHERE — safe, but it would disable BYOK with no symptom.
    // `location.href` is the one thing always present.
    EOS.byok.sameOrigin = function(url) {
        try { return new URL(String(url), location.href).origin === new URL(location.href).origin; }
        catch (e) { return false; }
    };

    // --- Shared fetch-wrapper chain -----------------------------------------
    // Four bundles independently monkey-patched `window.fetch`: BYOK injection
    // (below), the 503 `ai_offline` toast (eos-components.js), provenance chips
    // (eos-provenance.js) and the standalone export shim. Each captured the
    // previous `window.fetch` and delegated, so the chain worked — but three
    // things about it were accidental rather than designed:
    //
    //   * ORDER was whatever a page's <script> tags happened to be. Measured
    //     2026-09-05: 239 pages load eos.js first, 5 load eos-components.js
    //     first, so the same two layers nested in opposite orders by page.
    //   * each layer invented its own idempotence flag (`__eosByok`,
    //     `_eosFetchWrapped`, `_eosProvWrapped`, and the export shim has none).
    //   * a layer that forgot to delegate would silently break every request on
    //     the page, with nothing naming the culprit.
    //
    // `EOS.wrapFetch` owns the single patch and runs named layers in
    // registration order; `EOS.fetchLayers()` names them for debugging.
    //
    // It is deliberately ORDER-INDEPENDENT rather than relying on those 244
    // pages keeping their script order: a bundle loading before eos.js queues
    // onto `__eosFetchPending` and eos.js drains it *after* registering its own
    // layer, so every page ends up with the same chain regardless of tag order.
    function _fetchChain() {
        var c = window.__eosFetchChain;
        if (c) return c;
        var layers = [];
        var native = window.fetch.bind(window);
        window.fetch = function(input, init) {
            var i = -1;
            return (function next(inp, ini) {
                i++;
                return i < layers.length ? layers[i].fn(inp, ini, next) : native(inp, ini);
            })(input, init);
        };
        c = window.__eosFetchChain = {
            layers: layers,
            use: function(name, fn) {
                for (var j = 0; j < layers.length; j++) {
                    if (layers[j].name === name) return false;   // idempotent per name
                }
                layers.push({name: name, fn: fn});
                return true;
            },
        };
        return c;
    }

    // fn(input, init, next) — call next(input, init) to continue the chain.
    EOS.wrapFetch = function(name, fn) {
        var chain = _fetchChain();
        var added = chain.use(name, fn);
        var pending = window.__eosFetchPending;
        if (pending && pending.length) {
            window.__eosFetchPending = [];
            for (var i = 0; i < pending.length; i++) chain.use(pending[i][0], pending[i][1]);
        }
        return added;
    };

    EOS.fetchLayers = function() {
        var c = window.__eosFetchChain;
        return c ? c.layers.map(function(l) { return l.name; }) : [];
    };

    // --- BYOK on raw fetch() ------------------------------------------------
    // EOS.api / EOS.apiSafe attach BYOK headers in _apiFetch, but 94 app pages
    // call `fetch()` directly — 847 call sites (audit F2, 2026-09-03). 24 of
    // them reach `self.think()` endpoints, so a visitor-supplied key could
    // never arrive: BYOK was silently dead on exactly the apps that ship in the
    // public distribution, which is the only place BYOK is the intended
    // mechanism. `kb` alone had 48 raw calls and zero EOS.api calls.
    //
    // Migrating those call sites is NOT the fix. `EOS.api` THROWS on non-2xx
    // and raw `fetch` does not, so a mechanical swap changes control flow at
    // every one of the 847 — a large regression surface for a header problem.
    // Patching the sink fixes every current and future page, and changes no
    // control flow at all.
    //
    // Never overwrites a header the caller set, so EOS.api's own fetch (which
    // lands here too) is idempotent rather than double-injected.
    if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
        EOS.wrapFetch('byok', function(input, init, next) {
            try {
                var keys = EOS.byok.list();
                var providers = Object.keys(keys);
                if (providers.length) {
                    var isReq = typeof Request !== 'undefined' && input instanceof Request;
                    var url = isReq ? input.url : String(input);
                    if (EOS.byok.sameOrigin(url)) {
                        var headers = new Headers(
                            (init && init.headers) || (isReq ? input.headers : undefined));
                        providers.forEach(function(p) {
                            if (!headers.has(BYOK_HEADERS[p])) headers.set(BYOK_HEADERS[p], keys[p]);
                        });
                        if (isReq && !init) return next(new Request(input, {headers: headers}), init);
                        return next(input, Object.assign({}, init || {}, {headers: headers}));
                    }
                }
            } catch (e) { /* key injection must never break the request itself */ }
            return next(input, init);
        });
    }

    // --- Presentation mode (runtime privacy toggle) ---
    // Backed by SettingsService on the server (presentation.enabled). The
    // backend regex middleware reads the same setting and scrubs JSON
    // responses; this client side handles the visual layer + UI affordance.
    EOS.presentation = {
        on: function() {
            document.documentElement.classList.add('eos-redact');
            try { localStorage.setItem('eos.presentation', '1'); } catch (e) {}
            _refreshPresentingPill(true);
        },
        off: function() {
            document.documentElement.classList.remove('eos-redact');
            try { localStorage.setItem('eos.presentation', '0'); } catch (e) {}
            _refreshPresentingPill(false);
        },
        toggle: async function() {
            try {
                var resp = await fetch(base + '/api/presentation/toggle', {method: 'POST'});
                var data = await resp.json();
                if (data.enabled) EOS.presentation.on();
                else EOS.presentation.off();
                return data.enabled;
            } catch (e) {
                // Offline / auth fail — flip locally anyway so the visual
                // hide still works; user can refresh once back online.
                var cur = document.documentElement.classList.contains('eos-redact');
                if (cur) EOS.presentation.off();
                else EOS.presentation.on();
                return !cur;
            }
        },
        isOn: function() {
            return document.documentElement.classList.contains('eos-redact');
        },
    };

    function _refreshPresentingPill(active) {
        var pill = document.querySelector('.nav-presenting');
        if (!pill) return;
        pill.classList.toggle('on', !!active);
        pill.title = active
            ? 'Presentation mode: ON — personal data hidden (Ctrl+Shift+P)'
            : 'Presentation mode: OFF (Ctrl+Shift+P)';
    }

    // Cross-tab sync — when another tab toggles, mirror the class here.
    window.addEventListener('storage', function(ev) {
        if (ev.key !== 'eos.presentation') return;
        if (ev.newValue === '1') EOS.presentation.on();
        else EOS.presentation.off();
    });

    // Reconcile with server on every page load — URL param trigger and a
    // stale localStorage hint should both be overridden by the persisted
    // setting if it exists.
    fetch(base + '/api/presentation/state').then(function(r){return r.json();}).then(function(d){
        if (d && typeof d.enabled === 'boolean') {
            if (d.enabled) EOS.presentation.on();
            else {
                // Only turn off if URL doesn't force it on (?demo=1 / ?redact=1).
                var qp = new URLSearchParams(location.search);
                if (!qp.has('demo') && !qp.has('redact')) EOS.presentation.off();
            }
        }
    }).catch(function(){});

    // Shared request builder for EOS.api / EOS.apiSafe — BYOK headers +
    // offline-write queueing happen here so the two wrappers can't drift.
    function _apiFetch(path, options) {
        var requestOptions = _injectByokHeaders(options);
        var method = String((requestOptions && requestOptions.method) || 'GET').toUpperCase();
        if (EOS.offlineWrites.enabled && method === 'POST' && EOS.offlineWrites.matches(path)) {
            requestOptions = Object.assign({}, requestOptions || {});
            requestOptions.headers = Object.assign({}, requestOptions.headers || {}, {
                'X-EOS-Offline-Queue': '1',
                'X-EOS-Offline-Write-ID': EOS.offlineWrites.id()
            });
        }
        return fetch(base + path, requestOptions);
    }

    function _toastOfflineQueued(resp) {
        if (resp.headers.get('X-EOS-Offline-Queued') === '1' && window.EOS_UI && EOS_UI.toast) {
            EOS_UI.toast('Saved offline — it will sync when the connection returns.', true);
        }
    }

    EOS.api = async function(path, options) {
        var resp = await _apiFetch(path, options);
        if (!resp.ok) throw new Error('API error: ' + resp.status);
        var payload = await resp.json();
        _toastOfflineQueued(resp);
        return payload;
    };

    // Like EOS.api but NEVER throws — network failures and non-OK responses
    // normalise to the {error} object most app pages already branch on,
    // preserving the server's JSON error detail when the body carries one.
    // Use this where a failed call should degrade to an inline message
    // instead of an uncaught rejection that silently blanks the view.
    EOS.apiSafe = async function(path, options) {
        var resp;
        try {
            resp = await _apiFetch(path, options);
        } catch (e) {
            return {error: String((e && e.message) || e)};
        }
        var payload = null;
        try { payload = await resp.json(); } catch (e) { /* non-JSON body */ }
        if (!resp.ok) {
            return {error: (payload && payload.error) || ('HTTP ' + resp.status)};
        }
        _toastOfflineQueued(resp);
        return (payload === null || payload === undefined) ? {} : payload;
    };

    EOS.post = async function(path, data) {
        return EOS.api(path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data),
        });
    };

    // Shared mobile mutation queue (dark default). Only the four explicitly
    // adopted app families opt in; AI, upload, auth, billing, and outbound
    // actions never enter the queue.
    var _offlineWritesCached = false;
    try { _offlineWritesCached = localStorage.getItem('eos.offlineWrites.enabled') === '1'; }
    catch (e) {}
    EOS.offlineWrites = {
        enabled: _offlineWritesCached,
        matches: function(path) {
            var p = String(path || '');
            // Prefix-adopted families: every POST under these is a bounded write.
            if (/^\/(task|journal|projects|people)\/api\//.test(p)) return true;
            // worklog is enumerated, not prefixed: its /api/ also carries AI
            // (smart-parse, update/draft) and the import merge gate, none of
            // which may be replayed blind. The $ anchors keep /update out of
            // /update/draft. Capturing work at the office is the whole reason
            // the queue matters here — the daemon is often asleep at home.
            return /^\/worklog\/api\/(log|status|plan|update)$/.test(p);
        },
        id: function() {
            if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
            return 'offline-' + Date.now() + '-' + Math.random().toString(16).slice(2);
        },
        flush: function() {
            if (!navigator.serviceWorker || !navigator.serviceWorker.controller) return;
            navigator.serviceWorker.controller.postMessage({type: 'eos:flush-offline-writes'});
        }
    };
    // A cached opt-in is what makes a cold offline PWA launch useful. Flush
    // already-authorized queued writes before refreshing the live flag.
    if (EOS.offlineWrites.enabled && navigator.onLine) EOS.offlineWrites.flush();
    fetch(base + '/settings/api/get?key=feature.projects-offline-writes.enabled')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            EOS.offlineWrites.enabled = data && data.value === true;
            try { localStorage.setItem('eos.offlineWrites.enabled', EOS.offlineWrites.enabled ? '1' : '0'); }
            catch (e) {}
            if (EOS.offlineWrites.enabled && navigator.onLine) EOS.offlineWrites.flush();
        }).catch(function() {});
    window.addEventListener('online', function() {
        if (EOS.offlineWrites.enabled) EOS.offlineWrites.flush();
    });

    // Open the assistant from a launcher (topbar ✨, FAB 🎙). When the in-page
    // companion rail is active (page-assistant.js sets EOS.companion.enabled),
    // open it in the SIDEBAR — voice mode when `voice` is true — instead of
    // navigating to full-screen Aura. Full-screen stays reachable from the
    // rail's ⤴ Expand. Falls back to navigation when the rail isn't present
    // (e.g. on /voice-assistant/ itself, or when the companion flag is off).
    EOS._openAssistant = function(voice) {
        if (EOS.companion && EOS.companion.enabled) {
            if (voice) EOS.companion.openVoice(); else EOS.companion.open();
            return;
        }
        location.href = EOS.hasApp('voice-assistant') ? '/voice-assistant/'
            : (EOS.hasApp('assistant') ? '/assistant/' : '/');
    };

    EOS.stream = async function*(path, options) {
        var resp = await fetch(base + path, _injectByokHeaders(options));
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';
        while (true) {
            var result = await reader.read();
            if (result.done) break;
            buffer += decoder.decode(result.value, {stream: true});
            var lines = buffer.split('\n');
            buffer = lines.pop(); // keep incomplete last line
            for (var i = 0; i < lines.length; i++) {
                if (lines[i].trim()) {
                    try { yield JSON.parse(lines[i]); } catch(e) { yield {text: lines[i]}; }
                }
            }
        }
        if (buffer.trim()) {
            try { yield JSON.parse(buffer); } catch(e) { yield {text: buffer}; }
        }
    };

    // Stream POST with body — for LLM calls
    EOS.streamPost = function(path, data) {
        return EOS.stream(path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data),
        });
    };

    // Stream text directly into an element (most common pattern)
    EOS.streamToElement = async function(path, data, elementId, options) {
        var el = document.getElementById(elementId);
        if (!el) return '';
        el.textContent = '';
        var full = '';
        options = options || {};
        var render = options.markdown ? EOS_UI.renderMarkdown : function(t) { return t; };
        for await (var chunk of EOS.streamPost(path, data)) {
            var text = chunk.text || chunk.content || '';
            if (text) {
                full += text;
                el.innerHTML = render(full);
            }
            if (chunk.done) break;
        }
        if (options.onDone) options.onDone(full);
        return full;
    };

    // --- Realtime ---
    EOS.realtime = null;

    // Eagerly connect realtime when the script loads. This is necessary for
    // server-initiated capture requests (browser-speech listen provider) to
    // reach this tab — they're dispatched the moment a daemon-side capability
    // call needs the browser's mic, which can be before any page code calls
    // EOS.on(). The connection is cheap (one WS, auto-reconnects).
    function _ensureRealtime() {
        if (typeof EmptyOSRealtime === 'undefined') return null;
        if (!EOS.realtime) {
            EOS.realtime = new EmptyOSRealtime();
            EOS.realtime.connect();
        }
        return EOS.realtime;
    }

    EOS.on = function(eventType, callback) {
        var rt = _ensureRealtime();
        if (!rt) return function() {};  // realtime.js not loaded on this page
        return rt.on(eventType, callback);
    };

    // Auto-connect on page load so capture requests can find this tab.
    if (typeof EmptyOSRealtime !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() { _ensureRealtime(); });
        } else {
            _ensureRealtime();
        }
    }

    // Auto-load the tour orchestrator so the spotlight + Next button appear
    // on every page the tour navigates to. Without this, the orchestrator
    // is only present on pages that <script src> it explicitly, and the
    // tour appears to "die" the moment it reaches a page that doesn't.
    // Tiny script (~5KB), no harm if loaded twice (it's IIFE-scoped).
    function _loadTourOrchestrator() {
        if (window.EOS && window.EOS.tour) return;  // already loaded
        if (document.querySelector('script[src*="/static/eos-tour.js"]')) return;
        var s = document.createElement('script');
        s.src = base + '/static/eos-tour.js';
        s.async = true;
        document.head.appendChild(s);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _loadTourOrchestrator);
    } else {
        _loadTourOrchestrator();
    }

    // --- Nav bar (dynamic from API) ---
    // Default nav apps — generic across all deployments. Picks from the
    // standard-tier apps that any community install will have. Users override
    // via localStorage 'eos-nav-apps' (set from Settings or per-user JS).
    // Whatever the source, the nav is filtered at render time against the
    // /api/apps result so links to apps that aren't loaded just disappear.
    var DEFAULT_NAV = [
        {id:'task', prefix:'/task', name:'Tasks'},
        {id:'journal', prefix:'/journal', name:'Journal'},
        {id:'projects', prefix:'/projects', name:'Projects'},
        {id:'focus', prefix:'/focus', name:'Focus'},
        {id:'search', prefix:'/search', name:'Search'},
    ];

    // Last _renderNav arguments, so the account menu can re-render the nav
    // when /api/health answers after the first paint.
    var _lastNavArgs = null;

    // --eos-nav-h follows the nav's RENDERED height (theme.css). The CSS values
    // are first-paint estimates; the real height varies with the theme's
    // border, a touch pointer (44px tap targets), the viewport and whether the
    // account button has arrived yet — a fixed number was wrong on each. The
    // safe-area inset is excluded: body and the overlays add env() themselves.
    var _navRO = null;
    var _insetProbe = null;
    function _safeTop() {
        if (!_insetProbe) {
            _insetProbe = document.createElement('div');
            _insetProbe.style.cssText = 'position:fixed;top:0;left:0;width:0;visibility:hidden;' +
                'pointer-events:none;height:env(safe-area-inset-top,0px)';
            document.body.appendChild(_insetProbe);
        }
        return _insetProbe.getBoundingClientRect().height || 0;
    }
    function _syncNavHeight(nav) {
        if (!nav) return;
        var set = function() {
            var h = Math.round(nav.getBoundingClientRect().height - _safeTop());
            if (h > 0) document.documentElement.style.setProperty('--eos-nav-h', h + 'px');
        };
        set();
        if (_navRO) _navRO.disconnect();
        if (typeof ResizeObserver !== 'undefined') {
            _navRO = new ResizeObserver(set);
            // border-box: a theme switch can change only the nav's border
            // (nord adds 1px), which a default content-box observer never sees.
            _navRO.observe(nav, { box: 'border-box' });
        }
    }

    function _renderNav(navApps, currentApp, currentLabel, currentIcon, currentIconId) {
        _lastNavArgs = [navApps, currentApp, currentLabel, currentIcon, currentIconId];
        var nav = document.createElement('nav');
        nav.className = 'nav';
        // The landing app comes from /api/health `home` (server.py home_target);
        // until it answers, hub — the pre-2026-10 default — is assumed.
        var isHome = !currentApp || currentApp === (EOS._homeApp || 'hub');

        // Left: back-to-home link + a clear location breadcrumb. The crumb
        // always names the current app (even when it's also a quick-link) so
        // deep pages stop feeling anonymous. App name + icon come from /api/apps
        // (resolved on the async pass below); the synchronous first paint uses
        // the id + a neutral glyph until then.
        var links = '<a href="/" class="nav-home' + (isHome ? ' current' : '') + '" title="Home">⌂ Home</a>';
        if (currentApp && !isHome) {
            var label = currentLabel || currentApp;
            links += '<span class="nav-crumb" title="' + escAttr(label) + '">' +
                     '<span class="nav-crumb-sep" aria-hidden="true">›</span>' +
                     '<span class="nav-crumb-icon">' + EOS.appIcon(currentIconId, currentIcon, label) + '</span>' +
                     '<span class="nav-crumb-name">' + esc(label) + '</span>' +
                     '</span>';
        }

        // Quick app links (configurable via Settings → layout.nav_apps).
        navApps.forEach(function(a) {
            var cls = a.id === currentApp ? ' class="current"' : '';
            links += '<a href="' + a.prefix + '/"' + cls + '>' + esc(a.name) + '</a>';
        });

        // Spacer absorbs free space so the tool cluster sits hard-right.
        links += '<span class="nav-spacer"></span>';

        // Right tool cluster: search · assistant · presentation · theme · all-apps.
        links += '<span class="nav-search-btn" onclick="EOS._openSearchOverlay()" title="Search (press /)">\u{1F50D}</span>';
        var asst = EOS.hasApp('voice-assistant') ? '/voice-assistant/'
                 : (EOS.hasApp('assistant') ? '/assistant/' : '');
        if (asst) {
            links += '<span class="nav-assistant" onclick="EOS._openAssistant(false)" title="Assistant">✨</span>';
        }
        var presentingOn = document.documentElement.classList.contains('eos-redact');
        links += '<span class="nav-presenting' + (presentingOn ? ' on' : '') + '"' +
                 ' onclick="EOS.presentation.toggle()"' +
                 ' title="Presentation mode (Ctrl+Shift+P)">' +
                 '<span class="nav-presenting-icon">👁</span>' +
                 '</span>';
        links += '<span class="nav-theme" onclick="EOS.cycleTheme()" title="Cycle theme (full list in Settings)">◐</span>';
        links += '<span class="nav-more" onclick="EOS.toggleDrawer()" title="All Apps">⋯</span>';
        if (EOS._account) {
            var who = EOS._accountName || '';
            // Array.from splits by code point, so an emoji name gives a whole
            // glyph rather than half a surrogate pair.
            var initial = who ? Array.from(who)[0].toUpperCase() : '\u{1F464}';
            links += '<button type="button" class="nav-account" aria-expanded="false"' +
                     ' aria-controls="eos-account-menu" onclick="EOS._toggleAccountMenu(this)"' +
                     ' title="' + escAttr(who ? who + ' — account' : 'Account') + '">' +
                     esc(initial) + '</button>';
        }
        nav.innerHTML = links;
        // Replace any existing nav (so the async filter can re-render cleanly)
        var existing = document.querySelector('body > nav.nav');
        var avatarHadFocus = !!(existing && document.activeElement &&
            document.activeElement === existing.querySelector('.nav-account'));
        if (existing) existing.replaceWith(nav);
        if (avatarHadFocus) {
            var newAvatar = nav.querySelector('.nav-account');
            if (newAvatar) newAvatar.focus();
        }
        else document.body.prepend(nav);
        _syncNavHeight(nav);
        // A late fetch re-renders the nav; an open account menu (a sibling,
        // not a child) stays open and follows the new nav in the tab order.
        var menu = document.getElementById('eos-account-menu');
        if (menu) {
            var acctBtn = nav.querySelector('.nav-account');
            if (!acctBtn) {
                EOS._closeAccountMenu();
            } else {
                acctBtn.setAttribute('aria-expanded', 'true');
                // Moving a node blurs whatever inside it had focus; put it back.
                var held = menu.contains(document.activeElement) ? document.activeElement : null;
                _accountMenuMoving = true;
                try { nav.after(menu); } finally { _accountMenuMoving = false; }
                if (held) held.focus();
            }
        }
        return nav;
    }

    // --- Account menu (nav, far right) -----------------------------------
    // Shown for every owner, since /api/health returns `account` only to an
    // authenticated caller (local mode counts). It always carries the owner's
    // name, Settings and Usage; "Manage account" and "Sign out" appear only
    // when the server names where they go (see /api/health).
    EOS._account = null;
    EOS._accountName = '';

    function _rerenderNav() {
        if (_lastNavArgs && document.querySelector('body > nav.nav')) {
            _renderNav.apply(null, _lastNavArgs);
        }
    }

    // True while the menu node is being moved or removed: either blurs a
    // focused item, and that focusout must not re-enter the close.
    var _accountMenuMoving = false;

    EOS._closeAccountMenu = function(returnFocus) {
        var menu = document.getElementById('eos-account-menu');
        if (menu) {
            _accountMenuMoving = true;
            try { menu.remove(); } finally { _accountMenuMoving = false; }
        }
        var btn = document.querySelector('body > nav.nav .nav-account');
        if (btn) {
            btn.setAttribute('aria-expanded', 'false');
            if (returnFocus) btn.focus();
        }
    };

    function _signOut(btn) {
        var url = EOS._account && EOS._account.sign_out_url;
        if (!url) return;
        btn.disabled = true;
        fetch(url, { method: 'POST', credentials: 'same-origin' }).then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            location.href = '/';
        }).catch(function() {
            btn.disabled = false;
            btn.textContent = 'Sign-out failed — try again';
        });
    }

    EOS._toggleAccountMenu = function(btn) {
        if (document.getElementById('eos-account-menu')) { EOS._closeAccountMenu(); return; }
        var acct = EOS._account || {};
        var name = EOS._accountName;
        // A disclosure (button + aria-expanded), not an ARIA menu: Tab moves
        // through it like any links, which is all the keyboard contract asks.
        var items = name
            ? '<div class="eos-account-name">' + esc(name) + '</div>'
            : '<a class="eos-account-name" href="/settings/">Add your name</a>';
        items += '<a href="/settings/">Settings</a>';
        if (EOS.hasApp('billing')) items += '<a href="/billing/">Usage</a>';
        if (acct.manage_url) items += '<a href="' + escAttr(acct.manage_url) + '">Manage account</a>';
        if (acct.sign_out_url) {
            items += '<div class="eos-account-sep"></div>' +
                     '<button type="button" class="eos-account-signout">Sign out</button>';
        }
        var menu = document.createElement('div');
        menu.id = 'eos-account-menu';
        menu.className = 'eos-account-menu';
        menu.setAttribute('aria-label', 'Account');
        menu.innerHTML = items;
        var nav = btn.closest('nav');
        menu.style.top = (nav ? nav.getBoundingClientRect().bottom + 4 : 48) + 'px';
        // Right after the nav, so Tab from the avatar lands in the menu and
        // Shift+Tab from its first item goes back to the avatar.
        if (nav) nav.after(menu); else document.body.appendChild(menu);
        var so = menu.querySelector('.eos-account-signout');
        if (so) so.addEventListener('click', function() { _signOut(so); });
        // Tabbing to something outside closes it. Focus going NOWHERE does
        // not: a tap on the name row, Safari's click on the avatar (it does
        // not focus buttons), a window switch all do that, and closing there
        // made the avatar reopen its own menu. Escape and an outside click
        // (the document handlers) cover the rest.
        menu.addEventListener('focusout', function(e) {
            if (_accountMenuMoving) return;
            var to = e.relatedTarget;
            if (!to || menu.contains(to) || (to.closest && to.closest('.nav-account'))) return;
            EOS._closeAccountMenu();
        });
        btn.setAttribute('aria-expanded', 'true');
        var first = menu.querySelector('a, button');
        if (first) first.focus();
    };

    document.addEventListener('click', function(e) {
        var menu = document.getElementById('eos-account-menu');
        if (!menu || menu.contains(e.target)) return;
        if (e.target.closest && e.target.closest('.nav-account')) return;
        EOS._closeAccountMenu();
    });
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape' || !document.getElementById('eos-account-menu')) return;
        EOS._closeAccountMenu(true);
    });

    function _loadAccount(account) {
        if (!account || typeof account !== 'object') return;
        EOS._account = account;
        _rerenderNav();
        fetch(base + '/settings/api/get?key=user.name').then(function(r) { return r.json(); }).then(function(d) {
            var n = d && typeof d.value === 'string' ? d.value.trim() : '';
            if (n && n !== EOS._accountName) { EOS._accountName = n; _rerenderNav(); }
        }).catch(function() {});
    }

    // --- Global search overlay -------------------------------------------
    // A hidden panel under the nav hosting EOS_UI.searchBar (apps + vault +
    // full-search) so the rich search that lives on hub is reachable from
    // every page. Opened by the nav 🔍 button or the `/` key (owned by
    // eos-keys.js, which calls EOS._openSearchOverlay when no visible search
    // input exists). Built once; lazy-loads eos-components.js if a page didn't
    // include it, so search works everywhere.
    function _mountSearchBar(inner) {
        if (!inner || inner.getAttribute('data-mounted')) return true;
        if (!(window.EOS_UI && EOS_UI.searchBar)) return false;
        EOS_UI.searchBar({ mount: inner, focusKey: null,
            placeholder: 'Search apps, vault, commands…' });
        inner.setAttribute('data-mounted', '1');
        return true;
    }
    function _ensureSearchOverlay() {
        if (document.getElementById('eos-search-overlay')) return;
        if (!document.body) { setTimeout(_ensureSearchOverlay, 20); return; }
        var ov = document.createElement('div');
        ov.id = 'eos-search-overlay';
        ov.className = 'eos-search-overlay';
        var inner = document.createElement('div');
        inner.className = 'eos-search-overlay-inner';
        ov.appendChild(inner);
        document.body.appendChild(ov);
        // Click on the backdrop (not the panel) closes.
        ov.addEventListener('click', function(e) {
            if (e.target === ov) EOS._closeSearchOverlay();
        });
        // Esc closes whenever the overlay is open.
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && ov.classList.contains('open')) EOS._closeSearchOverlay();
        });
        if (!_mountSearchBar(inner)) {
            // eos-components.js not on this page — inject it, then mount. If the
            // overlay was already opened while the script loaded, focus now.
            var s = document.createElement('script');
            s.src = base + '/static/eos-components.js';
            s.onload = function() {
                _mountSearchBar(inner);
                if (ov.classList.contains('open')) {
                    var inp = ov.querySelector('.eos-search-input');
                    if (inp) { try { inp.focus(); inp.select(); } catch (e) {} }
                }
            };
            document.body.appendChild(s);
        }
    }
    EOS._openSearchOverlay = function() {
        _ensureSearchOverlay();
        var ov = document.getElementById('eos-search-overlay');
        if (!ov) return;
        _mountSearchBar(ov.querySelector('.eos-search-overlay-inner'));
        ov.classList.add('open');
        var input = ov.querySelector('.eos-search-input');
        if (input) { try { input.focus(); input.select(); } catch (e) {} }
    };
    EOS._closeSearchOverlay = function() {
        var ov = document.getElementById('eos-search-overlay');
        if (ov) ov.classList.remove('open');
    };

    EOS.nav = function(currentApp) {
        var navApps = DEFAULT_NAV;
        try {
            var saved = localStorage.getItem('eos-nav-apps');
            if (saved) navApps = JSON.parse(saved);
        } catch(e) {}

        // Render synchronously with the (default or saved) list so the page
        // doesn't flash chrome-less. Then re-render once we know which apps
        // are actually loaded — links to missing apps get pruned.
        _renderNav(navApps, currentApp);
        fetch('/api/apps').then(function(r) { return r.json(); }).then(function(data) {
            var apps = Array.isArray(data) ? data : (data && data.apps) || [];
            if (!apps.length) return;
            var loaded = {};
            var nameById = {};
            var iconById = {};
            var iconIdById = {};
            EOS.registerAppIconIds(apps.map(function(a) { return a && a.icon_id; }));
            apps.forEach(function(a) {
                var id = (a && (a.id || a.name || a)) + '';
                loaded[id] = true;
                if (a && a.name) nameById[id] = a.name;
                if (a && a.icon) iconById[id] = a.icon;
                if (a && a.icon_id) iconIdById[id] = a.icon_id;
            });
            var filtered = navApps.filter(function(a) { return loaded[a.id]; });
            // Seed the app-presence map from this fetch if the dedicated probe
            // hasn't resolved yet, so EOS.hasApp() is reliable for the assistant
            // gate on the re-render below.
            if (!EOS._loadedAppIds) EOS._loadedAppIds = loaded;
            // Always re-render once the app set is known: prunes dead quick-links,
            // upgrades the breadcrumb to the real app name + icon, and lets the
            // assistant button appear now that EOS.hasApp() is populated.
            _renderNav(filtered, currentApp, nameById[currentApp], iconById[currentApp], iconIdById[currentApp]);
        }).catch(function(){ /* fall back to whatever we already rendered */ });

        // Global search overlay (built once) — reuses EOS_UI.searchBar.
        _ensureSearchOverlay();

        // App drawer (created once)
        if (!document.getElementById('app-drawer-overlay')) {
            var overlay = document.createElement('div');
            overlay.id = 'app-drawer-overlay';
            overlay.className = 'app-drawer-overlay';
            overlay.onclick = function() { EOS.toggleDrawer(false); };
            document.body.appendChild(overlay);

            var drawer = document.createElement('div');
            drawer.id = 'app-drawer';
            drawer.className = 'app-drawer';
            // Closed by default: inert + aria-hidden keep its app links out of
            // the tab order and the accessibility tree on every page (they're
            // otherwise duplicate nav targets, since the drawer only hides via
            // transform). toggleDrawer() flips these in lockstep with .open.
            drawer.setAttribute('role', 'dialog');
            drawer.setAttribute('aria-modal', 'true');
            drawer.setAttribute('aria-label', 'All Apps');
            drawer.setAttribute('aria-hidden', 'true');
            drawer.inert = true;
            drawer.innerHTML = '<div class="app-drawer-header"><span class="app-drawer-title">All Apps</span><span class="app-drawer-close" onclick="EOS.toggleDrawer(false)">&times;</span></div>' +
                '<input class="app-drawer-search" id="drawer-search" placeholder="Filter apps..." autocomplete="off">' +
                '<div class="app-drawer-list" id="drawer-list"></div>';
            document.body.appendChild(drawer);

            // Load apps grouped into declared store_category sections, so the
            // drawer browses by function group rather than one long list.
            fetch(base + '/api/apps/sections').then(function(r) { return r.json(); })
                .then(function(sections) {
                    EOS.registerAppIconIds([].concat.apply([], (sections || []).map(function(s) {
                        return (s.apps || []).map(function(a) { return a.icon_id; });
                    })));
                    EOS._drawerSections = (sections || []).map(function(s) {
                        return {key: s.key, label: s.label, icon: s.icon, apps: s.apps || []};
                    });
                    EOS._renderDrawer('');
                })
                .catch(function(){ EOS._drawerSections = []; EOS._renderDrawer(''); });

            document.getElementById('drawer-search').addEventListener('input', function() {
                EOS._renderDrawer(this.value.trim().toLowerCase());
            });
        }

        // Load custom nav from settings API (async, updates on next page load)
        fetch(base + '/settings/api/get?key=layout.nav_apps').then(function(r) { return r.json(); }).then(function(d) {
            if (d.value && Array.isArray(d.value) && d.value.length > 0) {
                localStorage.setItem('eos-nav-apps', JSON.stringify(d.value));
            }
        }).catch(function() {});
    };

    EOS._drawerSections = [];

    // App ranking — fetched from /app-analytics/api/ranking (server-side
    // recency+frequency over ui:viewed events). Cached per page-load so
    // the hub apps panel and the nav drawer pay one round-trip max, then
    // share the same Promise. Returns {} on failure (app-analytics not
    // installed, daemon down) — consumers fall back to alphabetical.
    EOS._fetchAppRanking = function() {
        if (EOS._appRankingPromise) return EOS._appRankingPromise;
        // Routed through EOS.api for the same auth-header injection the rest
        // of the kernel uses (relevant in private/public network modes).
        EOS._appRankingPromise = EOS.api('/app-analytics/api/ranking?days=90&half_life=14')
            .then(function(data) {
                var out = {};
                (data && data.ranking || []).forEach(function(row) {
                    if (row && row.app) out[row.app] = row.score || 0;
                });
                return out;
            })
            .catch(function() { return {}; });
        return EOS._appRankingPromise;
    };

    // Match by name, description, OR user_intent phrases (function search).
    EOS._drawerMatch = function(a, q) {
        if (!q) return true;
        if ((a.name || '').toLowerCase().includes(q)) return true;
        if ((a.description || '').toLowerCase().includes(q)) return true;
        return (a.user_intent || []).some(function(p) { return String(p).toLowerCase().includes(q); });
    };

    EOS._toggleDrawerSec = function(hdr) {
        var s = hdr.closest('.app-drawer-section');
        if (s) s.classList.toggle('collapsed');
    };

    EOS._renderDrawer = function(q) {
        var list = document.getElementById('drawer-list');
        if (!list) return;
        var html = '';
        (EOS._drawerSections || []).forEach(function(s) {
            var apps = (s.apps || []).filter(function(a) { return EOS._drawerMatch(a, q); });
            if (!apps.length) return;
            var cards = apps.map(function(a) {
                return '<a class="app-drawer-item" href="' + (a.web_prefix || '#') + '/">' +
                    '<span class="adi-icon">' + EOS.appIcon(a.icon_id, a.icon, a.name || a.id) + '</span>' +
                    '<span class="adi-copy"><span class="adi-name">' + esc(a.name || a.id) + '</span>' +
                    '<span class="adi-desc">' + esc(a.description || '') + '</span></span></a>';
            }).join('');
            html += '<div class="app-drawer-section">' +
                '<div class="app-drawer-sec-hdr" onclick="EOS._toggleDrawerSec(this)">' +
                    '<span class="ads-icon">' + esc(s.icon || '') + '</span>' +
                    '<span class="ads-name">' + esc(s.label || s.key) + '</span>' +
                    '<span class="ads-count">' + apps.length + '</span>' +
                    '<span class="ads-toggle">&#9654;</span>' +
                '</div>' +
                '<div class="app-drawer-grid">' + cards + '</div>' +
            '</div>';
        });
        list.innerHTML = html || '<div style="text-align:center;padding:20px;color:var(--text-muted);font-size:13px">No matches</div>';
    };

    EOS.toggleDrawer = function(force) {
        var overlay = document.getElementById('app-drawer-overlay');
        var drawer = document.getElementById('app-drawer');
        if (!overlay || !drawer) return;
        var open = force !== undefined ? force : !drawer.classList.contains('open');
        overlay.classList.toggle('open', open);
        drawer.classList.toggle('open', open);
        drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
        drawer.inert = !open;
        overlay.setAttribute('aria-hidden', open ? 'false' : 'true');
        if (open) {
            var input = document.getElementById('drawer-search');
            if (input) { input.value = ''; EOS._renderDrawer(''); setTimeout(function() { input.focus(); }, 250); }
        }
    };

    // --- Utilities ---
    window.esc = function(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : s;
        return d.innerHTML;
    };

    window.escAttr = function(s) {
        // String() first — numeric callers (escAttr(voltage_kv)) must not throw.
        return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
    };

    EOS.formatDate = function(d) {
        return new Date(d).toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric'});
    };

    // --- Geocoding (via /geocode app — OSM Nominatim, cached + throttled server-side) ---
    EOS.geocode = async function(address, limit) {
        if (!address) return [];
        var url = '/geocode/api/lookup?q=' + encodeURIComponent(address);
        if (limit) url += '&limit=' + encodeURIComponent(limit);
        try {
            var r = await EOS.api(url);
            return Array.isArray(r) ? r : [];
        } catch(e) { return []; }
    };

    EOS.reverseGeocode = async function(lat, lon) {
        if (lat == null || lon == null) return {};
        try {
            return await EOS.api('/geocode/api/reverse?lat=' + encodeURIComponent(lat) + '&lon=' + encodeURIComponent(lon));
        } catch(e) { return {}; }
    };

    // Batch forward geocode — sequential server-side (Nominatim <=1 req/s),
    // so this call itself takes ~1s per address. Returns [] on any failure.
    EOS.geocodeBatch = async function(addresses, limit) {
        if (!Array.isArray(addresses) || !addresses.length) return [];
        try {
            var r = await EOS.api('/geocode/api/batch-lookup', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({addresses: addresses, limit: limit || 5}),
            });
            return Array.isArray(r.results) ? r.results : [];
        } catch(e) { return []; }
    };

    // --- Routing (via /routing app — OSRM, cached + throttled server-side) ---
    // points: [[lat,lng], ...] or [{lat, lng|lon}, ...]  (min 2)
    // returns: {geometry:[[lat,lng],...], distance_m, duration_s, legs, waypoints} or {error}
    // Named getRoute (not route) to avoid collision with EOS.route — the SPA router below.
    EOS.getRoute = async function(points, profile) {
        if (!points || points.length < 2) return {error: 'need at least 2 points'};
        try {
            var r = await EOS.post('/routing/api/route', {points: points, profile: profile || 'driving'});
            return r || {error: 'empty response'};
        } catch(e) { return {error: 'routing failed: ' + (e.message || e)}; }
    };

    // Format metres / seconds into human strings
    EOS.fmtDistance = function(m) {
        m = +m || 0;
        return m >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m';
    };
    EOS.fmtDuration = function(s) {
        s = +s || 0;
        var h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
        return h ? h + 'h ' + m + 'm' : m + ' min';
    };

    // --- Path normalization ---
    // All paths normalized to forward slashes internally
    EOS.normPath = function(p) {
        return (p || '').replace(/\\/g, '/');
    };

    // Extract just the filename (no directory, no extension)
    EOS.fileName = function(p) {
        return EOS.normPath(p).split('/').pop().replace(/\.md$/, '');
    };

    // Get vault-relative path (strip vault base + normalize slashes)
    EOS.vaultRelative = function(p) {
        var norm = EOS.normPath(p);
        // Strip everything up to and including the vault folder name
        var vn = EOS.vaultName || 'Main Vault';
        var re = new RegExp('^.*' + vn.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '/?');
        return norm.replace(re, '');
    };

    // Escape a path for a single-quoted JS string inside a double-quoted HTML
    // onclick — and only there: entityCard({onClick}) wants the raw path in a
    // bare JSON.stringify instead. normPath turns every backslash to `/`, so
    // none survives to confuse the escapes below; then `'` is escaped for the
    // JS string, and
    // writes `" & < >` and line breaks as JS hex escapes: the attribute then
    // holds no raw quote to close it and no entity for the parser to decode,
    // and the JS string still reads back the exact path. Hex, not HTML
    // entities, because some callers already wrap this in EOS_UI.escAttr —
    // an entity would be escaped twice there.
    EOS.escPath = function(p) {
        return EOS.normPath(p).replace(/'/g, "\\'")
            .replace(/"/g, '\\x22').replace(/&/g, '\\x26')
            .replace(/</g, '\\x3c').replace(/>/g, '\\x3e')
            .replace(/\r/g, '\\r').replace(/\n/g, '\\n');
    };

    // --- Vault Viewer Integration ---
    // Note links open in the configured viewer (default: Obsidian, via the
    // `obsidian` plugin). URI templates come from /api/health `viewer.uri_templates`,
    // so swapping the plugin swaps the scheme — no code change here.
    EOS.vaultName = localStorage.getItem('eos-vault-name') || 'Main Vault';
    EOS.vaultPath = localStorage.getItem('eos-vault-path') || '';
    EOS.viewerTemplates = {
        open: 'obsidian://open?vault={vault}&file={path}',
        new: 'obsidian://new?vault={vault}&file={path}',
    };

    // Load vault + viewer config from server (async, updates on next page load)
    fetch(base + '/api/health').then(function(r) { return r.json(); }).then(function(d) {
        if (d.vault_name) {
            EOS.vaultName = d.vault_name;
            localStorage.setItem('eos-vault-name', d.vault_name);
        }
        if (d.vault_path) {
            EOS.vaultPath = d.vault_path;
            localStorage.setItem('eos-vault-path', d.vault_path);
        }
        if (d.viewer && d.viewer.uri_templates) {
            EOS.viewerTemplates = d.viewer.uri_templates;
        }
        if (d.home) {
            // "/portal/" → "portal"; a Space route like "/workspaces/#eng" → "workspaces".
            var homeApp = String(d.home).replace(/^\/+/, '').split(/[\/#?]/)[0];
            if (homeApp && homeApp !== EOS._homeApp) { EOS._homeApp = homeApp; _rerenderNav(); }
        }
        _loadAccount(d.account);
    }).catch(function() {});

    // Encode path for viewer URI — encode spaces/specials but NOT slashes
    EOS._encodeViewerPath = function(p) {
        return p.split('/').map(function(s) { return encodeURIComponent(s); }).join('/');
    };

    EOS._buildViewerUri = function(action, filePath) {
        var tmpl = (EOS.viewerTemplates && EOS.viewerTemplates[action]) || '';
        if (!tmpl) return '';
        var path = EOS.vaultRelative(filePath);
        if (action === 'open') path = path.replace(/\.md$/, '');
        return tmpl
            .replace('{vault}', encodeURIComponent(EOS.vaultName))
            .replace('{path}', EOS._encodeViewerPath(path));
    };

    EOS.openInViewer = function(filePath) {
        var uri = EOS._buildViewerUri('open', filePath);
        if (uri) window.open(uri, '_self');
    };

    EOS.createInViewer = function(filePath, content) {
        var uri = EOS._buildViewerUri('new', filePath);
        if (!uri) return;
        if (content) uri += '&content=' + encodeURIComponent(content);
        window.open(uri, '_self');
    };

    // Helper: render a file path as a clickable external link
    EOS.viewerLink = function(filePath, label) {
        var display = label || EOS.fileName(filePath);
        return '<a href="#" onclick="EOS.openInViewer(\'' + EOS.escPath(filePath) + '\');return false" class="obs-link" title="Open external">' + esc(display) + ' <span class="obs-icon">↗</span></a>';
    };

    EOS.greeting = function() {
        var h = new Date().getHours();
        return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
    };

    EOS.frogIcons = {english: '📚', exercise: '💪', job_search: '💼'};

    EOS.flash = function(elementId, message, duration) {
        var el = document.getElementById(elementId);
        if (!el) return;
        el.textContent = message;
        setTimeout(function() { el.textContent = ''; }, duration || 2000);
    };

    EOS.debounce = function(fn, ms) {
        var timer;
        return function() {
            clearTimeout(timer);
            timer = setTimeout(fn, ms || 500);
        };
    };

    EOS.THEMES = _KNOWN_THEMES;   // same array — never a second list to sync
    EOS.THEME_LABELS = {
        'eos': 'Warm light',
        'digital-garden': 'Digital Garden',
        'soft-light': 'Soft light',
        'tatami': 'Tatami',
        'warm-dark': 'Amber dark',
        'void-dark': 'Void dark',
        'nord': 'Nord',
        'forest': 'Forest',
        'deep-sea': 'Deep sea',
        'vino': 'Vino',
    };

    EOS.setTheme = function(name) {
        localStorage.setItem('eos-theme', name);
        _setThemeClass(name);
        // Keep the PWA theme-color meta in sync (controls iOS/Android status bar tint)
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.content = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
        try { window.dispatchEvent(new CustomEvent('eos:theme-changed', {detail: {theme: name}})); } catch(e) {}
    };

    // Compat: some apps call EOS.toast(...). Canonical is EOS_UI.toast.
    // Defer to EOS_UI when it's loaded; map common second-arg shapes ('success',
    // 'error', 'warn', 'info', true/false) onto EOS_UI's (msg, ok) signature.
    EOS.toast = function(msg, kind) {
        var ok = kind === true || kind === 'success' || kind === 'info' || kind === undefined;
        if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) {
            return EOS_UI.toast(msg, ok);
        }
        // EOS_UI may load async; queue once
        setTimeout(function() {
            if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) EOS_UI.toast(msg, ok);
        }, 50);
    };

    EOS.cycleTheme = function() {
        var current = localStorage.getItem('eos-theme') || 'eos';
        var i = EOS.THEMES.indexOf(current);
        var next = EOS.THEMES[(i + 1) % EOS.THEMES.length];
        EOS.setTheme(next);
        if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) {
            EOS_UI.toast('Theme: ' + (EOS.THEME_LABELS[next] || next), true);
        }
    };

    // --- Note Viewer/Editor shortcuts ---
    // Delegates to EOS_UI component (requires eos-components.js)
    EOS.viewNote = function(path) {
        if (typeof EOS_UI !== 'undefined') EOS_UI.viewNote(path);
    };
    EOS.editNote = function(path) {
        if (typeof EOS_UI !== 'undefined') EOS_UI.editNote(path);
    };

    // Helper: render a note link with view + edit buttons
    EOS.noteActions = function(filePath, label) {
        var safe = EOS.escPath(filePath);
        var display = label || EOS.fileName(filePath).replace(/-/g, ' ');
        return '<span class="eos-note-link">' +
            '<a href="#" onclick="EOS.viewNote(\'' + safe + '\');return false" class="obs-link" title="View note">' + esc(display) + '</a>' +
            ' <a href="#" onclick="EOS.editNote(\'' + safe + '\');return false" class="eos-note-edit-icon" title="Edit">✎</a>' +
            ' <a href="#" onclick="EOS.openInViewer(\'' + safe + '\');return false" class="obs-icon" title="Open external">↗</a>' +
            '</span>';
    };

    // --- Quick-entry host ---
    // A page can be showing inside a small transient window instead of a normal
    // tab: the desktop shell's frameless always-on-top panel (preloaded, shown
    // on a global hotkey) or plugins/command-launcher's borderless Chrome
    // window. They differ in one way that matters to any code wanting OUT of
    // that window — the shell has a bridge and the browser window does not —
    // and `?launcher=1` is the older spelling of `?quick=1`, so both are one
    // mode. This is the single place that knows all of that; consumers ask it
    // rather than probing `window.pywebview` and parsing the query themselves.
    EOS.quickHost = {
        // Whether this page is in a quick window at all.
        active: function() {
            try {
                var p = new URLSearchParams(location.search);
                return p.get('quick') === '1' || p.get('launcher') === '1';
            } catch (_) { return false; }
        },
        // The shell's API, or null. Probed by `hide`, which only the quick
        // window's bridge publishes — never by a user-agent string, and never
        // assumed from `window.pywebview` alone (the shell's MAIN window has a
        // bridge too, without these verbs).
        bridge: function() {
            try {
                var api = window.pywebview && window.pywebview.api;
                return api && typeof api.hide === 'function' ? api : null;
            } catch (_) { return null; }
        },
        // Put the window away. The shell hides a preloaded window; a browser
        // launcher window is transient and simply closes.
        dismiss: function() {
            var api = EOS.quickHost.bridge();
            if (api) {
                try {
                    // `hide` is a pywebview bridge call: it resolves a Promise
                    // and does not throw synchronously, so a try/catch around
                    // it guards a failure that cannot happen. The one the shell
                    // CAN report is `{ok: false}` — hide_quick swallows its own
                    // exception (Window.hide can block on `shown`) — and
                    // discarding that strands a frameless, title-bar-less,
                    // always-on-top panel the user has no way to close.
                    var p = api.hide();
                    if (p && typeof p.then === 'function') {
                        p.then(function(r) {
                            if (!r || r.ok === false) { try { window.close(); } catch (_) {} }
                        }, function() { try { window.close(); } catch (_) {} });
                    }
                    return;
                } catch (_) {}
            }
            try { window.close(); } catch (_) {}
        },
        // Hand a same-origin path to the full-size window and step aside. The
        // caller owns the path; this owns where it goes.
        openMain: function(path) {
            var api = EOS.quickHost.bridge();
            if (api && typeof api.open_main === 'function') {
                try { api.open_main(path); return; } catch (_) {}
            }
            try { window.open(path, '_blank', 'noopener'); } catch (_) {}
            EOS.quickHost.dismiss();
        }
    };

    // --- Keyboard Shortcuts ---
    // Auto-load eos-keys.css + eos-keys.js on every page
    var keyCss = document.createElement('link');
    keyCss.rel = 'stylesheet';
    keyCss.href = base + '/static/eos-keys.css';
    document.head.appendChild(keyCss);

    // The script below loads asynchronously, so a page registering its own
    // shortcuts at load (`if (EOS.keys) EOS.keys.register(...)`) usually ran
    // before EOS.keys existed and silently registered nothing. Queue them here;
    // eos-keys.js replays the queue when it publishes the real API.
    if (!EOS.keys) {
        var _pendingKeys = [];
        EOS.keys = {
            register: function(key, desc, fn) { _pendingKeys.push([key, desc, fn]); },
            _pending: _pendingKeys,
        };
    }

    var keyScript = document.createElement('script');
    keyScript.src = base + '/static/eos-keys.js';
    // eos.js may be loaded synchronously in <head> before <body> exists.
    // Defer until body is present rather than crashing.
    (function attachKeys() {
        if (!document.body) return setTimeout(attachKeys, 10);
        document.body.appendChild(keyScript);
    })();

    // --- App-presence map (used to gate tier-specific UI) ---
    // Tiers (core / demo / standard / dev) bundle different app sets. Globally
    // injected UI — hands-free overlay, voice FAB — must self-disable when its
    // owning app isn't loaded, otherwise the demo bundle ships dead buttons.
    // First page load waits on /api/apps; subsequent loads use the localStorage
    // cache synchronously so injectors fire without a fetch round-trip, then
    // refresh the cache in the background.
    EOS._loadedAppIds = null;
    // URL-prefix segment → real app id, for the apps whose web prefix differs
    // from their id (e.g. company → /orgs/). Lets prefix-derived nav resolve
    // back to the true id instead of probing /api/apps/<prefix> (→ 404).
    EOS._prefixToId = null;
    try {
        var cached = localStorage.getItem('eos-loaded-app-ids');
        if (cached) EOS._loadedAppIds = JSON.parse(cached);
        var cachedP = localStorage.getItem('eos-prefix-to-id');
        if (cachedP) EOS._prefixToId = JSON.parse(cachedP);
    } catch(e) {}
    EOS._appIdsReady = fetch(base + '/api/apps')
        .then(function(r) { return r.json(); })
        .then(function(list) {
            var ids = {};
            var pmap = {};
            EOS.registerAppIconIds((list || []).map(function(a) { return a && a.icon_id; }));
            (list || []).forEach(function(a) {
                if (!a || !a.id) return;
                ids[a.id] = true;
                var pref = (a.web_prefix || a.prefix || '') + '';
                var seg = pref.replace(/^\/+/, '').split('/')[0];
                if (seg) pmap[seg] = a.id;
            });
            EOS._loadedAppIds = ids;
            EOS._prefixToId = pmap;
            try {
                localStorage.setItem('eos-loaded-app-ids', JSON.stringify(ids));
                localStorage.setItem('eos-prefix-to-id', JSON.stringify(pmap));
            } catch(e) {}
            return ids;
        })
        .catch(function() {
            if (!EOS._loadedAppIds) EOS._loadedAppIds = {};
            return EOS._loadedAppIds;
        });
    EOS.hasApp = function(id) {
        return EOS._loadedAppIds ? !!EOS._loadedAppIds[id] : false;
    };
    // Run `fn()` as soon as the app-id set is known. Synchronous if the cache
    // was already populated from localStorage AND body is ready; otherwise
    // awaits the fetch / DOMContentLoaded so callers can safely touch document.body.
    EOS._whenAppsKnown = function(fn) {
        var run = function() {
            if (!document.body) return setTimeout(run, 10);
            fn();
        };
        if (EOS._loadedAppIds) { run(); return; }
        EOS._appIdsReady.then(run);
    };

    // --- Hands-Free Mode (gesture PTT + voice intents) ---
    // Auto-loads on every page; the chip stays inert until the user toggles it.
    // Install a buffering stub so page scripts that run before the overlay
    // finishes loading can still call EOS.handsFree.registerGesture. The real
    // overlay drains this queue on boot.
    EOS.handsFree = EOS.handsFree || {
        _queue: [],
        registerGesture: function() {
            EOS.handsFree._queue.push(['registerGesture', Array.prototype.slice.call(arguments)]);
        },
        registeredGestures: function() { return {}; },
        toggle: function() {}, on: function() {}, off: function() {},
        status: function() { return {state: 'off', ready: false}; },
    };

    EOS._whenAppsKnown(function() {
        if (!EOS.hasApp('hands-free')) return;
        var hfCss = document.createElement('link');
        hfCss.rel = 'stylesheet';
        hfCss.href = base + '/static/eos-hands-free.css';
        document.head.appendChild(hfCss);

        var hfScript = document.createElement('script');
        hfScript.src = base + '/static/eos-hands-free.js';
        document.body.appendChild(hfScript);
    });

    // --- Dictation overlay (push-to-talk into the focused field) ---
    // Loads on every page when the `dictation` app is enabled. Unlike hands-free
    // it has no persistent chip (only a transient HUD) so it runs on chat
    // surfaces too. The overlay self-gates on /dictation/api/config; with the
    // dark flag off it binds no hotkey (byte-identical no-op).
    EOS._whenAppsKnown(function() {
        if (!EOS.hasApp('dictation')) return;
        var dxCss = document.createElement('link');
        dxCss.rel = 'stylesheet';
        dxCss.href = base + '/static/eos-dictate.css';
        document.head.appendChild(dxCss);

        var dxScript = document.createElement('script');
        dxScript.src = base + '/static/eos-dictate.js';
        document.body.appendChild(dxScript);
    });

    // --- Page Assistant (AI sidebar) ---
    // Loads EmptyOS's own page-assistant.js when the `assistant` app is present.
    // Skipped on tiers (core/demo) that don't bundle it — page-assistant calls
    // /assistant/api/chat, so without the app it would be dead UI on every page.
    EOS._currentApp = null;
    // Pre-init queue for page-registered actions. A page calls EOS.registerActions
    // in its inline <script>, which runs NOW — before page-assistant.js is
    // async-injected (below, gated on _whenAppsKnown). Without this stub the page's
    // guarded `if (EOS.registerActions)` call finds nothing and the action is
    // silently dropped. Queue such calls; page-assistant.js drains them on load.
    EOS._pendingActions = EOS._pendingActions || [];
    if (!EOS.registerActions) {
        EOS.registerActions = function(a, d, c) { EOS._pendingActions.push([a, d, c]); };
    }
    // ?embed=1 — the page is iframed by a host shell (portal's app pane) that
    // already draws the nav. eos-components.js and page-assistant.js read the
    // same flag for their own chrome.
    function _isEmbedded() {
        try { return new URLSearchParams(location.search).get('embed') === '1'; }
        catch (e) { return false; }
    }
    // html.eos-embed zeroes theme.css's --eos-nav-h, the room every page keeps
    // for the fixed nav. The server already sets it before first paint
    // (server.py _EMBED_BOOTSTRAP_TAG); this covers a page served any other way
    // (a standalone export).
    if (_isEmbedded()) document.documentElement.classList.add('eos-embed');

    var _origNav = EOS.nav;
    EOS.nav = function(currentApp) {
        EOS._currentApp = currentApp;
        // Anonymous public face (set server-side on a public_routes page): render
        // minimal chrome only — no owner quick-links, no /api/apps probe (would
        // 401), no AI assistant / health check. Authenticated owners skip this.
        if (window.EOS_PUBLIC_FACE) {
            if (!_isEmbedded()) { try { _renderNav([], currentApp); } catch (e) {} }
            return;
        }
        // Embedded in a host shell's pane: the host draws the nav, so no second
        // bar here — for every page, whether it calls EOS.nav('<id>') itself
        // (~70 tracked pages) or is auto-mounted below. The app id and the
        // health check still apply; the page assistant skips embeds on its own.
        if (_isEmbedded()) {
            if (currentApp) _checkAppHealth(currentApp);
            return;
        }
        _origNav(currentApp);

        EOS._whenAppsKnown(function() {
            if (!EOS.hasApp('assistant')) return;
            var paScript = document.createElement('script');
            paScript.src = base + '/static/page-assistant.js';
            document.body.appendChild(paScript);
        });

        // App health check — detect unavailable dependencies
        if (currentApp) _checkAppHealth(currentApp);
    };

    // Auto-mount nav: any page that hasn't explicitly called EOS.nav() gets one
    // derived from its URL prefix (which equals the app id by convention).
    // Opt-out by setting <body data-no-nav>, used by full-screen islands like
    // voice-assistant where the nav would compete with primary chrome.
    // Also opt-out via ?embed=1 — the page is being iframed by a host shell
    // (portal) that already provides its own sidebar/chrome. Skipping the
    // kernel nav avoids the double-nav you'd otherwise see inside the frame.
    function _autoMountNav() {
        if (EOS._currentApp) return;
        if (document.body && document.body.hasAttribute('data-no-nav')) return;
        var seg = (location.pathname.split('/')[1] || '').toLowerCase();
        if (seg === 'static' || seg === 'api' || seg === 'docs' || seg === 'ws') return;
        EOS.nav(seg || 'home');
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { setTimeout(_autoMountNav, 0); });
    } else {
        setTimeout(_autoMountNav, 0);
    }

    EOS.KNOWN_SERVICES = {
        ollama:    { port: 11434, start: 'ollama serve', check: 'http://localhost:11434', desc: 'Local LLM (Ollama)' },
        comfyui:   { port: 8188,  start: 'run_nvidia_gpu.bat', check: 'http://localhost:8188', desc: 'GPU image/video generation' },
        voice_api: { port: 8601,  start: 'voice-api server', check: 'http://localhost:8601', desc: 'TTS (F5-TTS) + STT (Whisper)' },
        applio:    { port: 6969,  start: 'Applio launcher', check: 'http://localhost:6969', desc: 'AI voice conversion' },
        blender:   { port: 8400,  start: 'blender --background --python server.py', check: 'http://localhost:8400', desc: '3D rendering' },
    };
    var KNOWN_FIXES = EOS.KNOWN_SERVICES;

    function _getCachedHealth() {
        try {
            var cached = sessionStorage.getItem('eos-health-cache');
            if (cached) {
                var parsed = JSON.parse(cached);
                if (Date.now() - parsed._ts < 60000) return Promise.resolve(parsed); // 60s TTL
            }
        } catch(e) {}
        return fetch(base + '/api/health?full=true').then(function(r) { return r.ok ? r.json() : null; }).then(function(data) {
            if (data) { try { data._ts = Date.now(); sessionStorage.setItem('eos-health-cache', JSON.stringify(data)); } catch(e) {} }
            return data;
        }).catch(function() { return null; });
    }

    function _checkAppHealth(appId) {
        EOS._whenAppsKnown(function() {
            // Resolve the segment to a real app id. The nav segment usually IS
            // the id, but a few apps publish a different web prefix (company →
            // /orgs/); probing /api/apps/<prefix> there would 404. Unknown
            // segments (static/api/etc.) are skipped — no spurious request.
            var realId = (EOS._loadedAppIds && EOS._loadedAppIds[appId]) ? appId
                       : (EOS._prefixToId && EOS._prefixToId[appId]);
            if (!realId) return;
            _doCheckAppHealth(realId);
        });
    }

    function _doCheckAppHealth(appId) {
        fetch(base + '/api/apps/' + appId).then(function(r) {
            return r.ok ? r.json() : null;
        }).then(function(info) {
            if (!info || !info.requires) return;
            return _getCachedHealth().then(function(health) {
                if (!health) return;
                var issues = [];
                // Check capabilities
                (info.requires.capabilities || []).forEach(function(cap) {
                    var providers = health.capabilities && health.capabilities[cap];
                    if (Array.isArray(providers)) {
                        var avail = providers.filter(function(p) { return p.available; });
                        if (avail.length === 0) {
                            var offlineNames = providers.map(function(p) { return p.name; });
                            issues.push({ type: 'capability', name: cap, providers: offlineNames });
                        }
                    }
                });
                // Check required services + connectors
                (info.requires.services || []).concat(info.requires.connectors || []).forEach(function(svc) {
                    var svcList = health.services || [];
                    var found = svcList.find(function(s) { return s.name === svc; });
                    if (found && found.status === 'unhealthy') {
                        issues.push({ type: 'service', name: svc });
                    } else if (!found) {
                        // Also check plugins
                        var plugList = health.plugins || [];
                        var plug = plugList.find(function(p) { return p.id === svc || p.id === svc.replace('_', '-'); });
                        if (plug && !plug.loaded) {
                            issues.push({ type: 'service', name: svc });
                        }
                    }
                });
                if (issues.length > 0) _showHealthBanner(appId, issues);
            });
        }).catch(function() {});
    }

    function _showHealthBanner(appId, issues) {
        var banner = document.createElement('div');
        banner.id = 'app-health-banner';
        banner.style.cssText = 'background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);border-radius:10px;padding:12px 16px;margin:0 0 12px;font-size:13px;color:var(--warning);position:relative';

        var lines = ['<div style="font-weight:600;margin-bottom:6px">Some features may be unavailable</div>'];
        issues.forEach(function(issue) {
            if (issue.type === 'capability') {
                lines.push('<div style="font-size:12px;color:var(--text-heading);margin:4px 0">&#9889; <b>' + issue.name + '</b> — no providers online</div>');
                (issue.providers || []).forEach(function(prov) {
                    var fix = KNOWN_FIXES[prov];
                    if (fix) {
                        lines.push('<div style="font-size:11px;color:var(--text-secondary);margin-left:16px">Start <b>' + fix.desc + '</b>: <code style="background:var(--accent-bg);padding:1px 6px;border-radius:3px;font-size:11px">' + fix.start + '</code> → ' + fix.check + '</div>');
                    }
                });
            } else {
                var fix = KNOWN_FIXES[issue.name];
                lines.push('<div style="font-size:12px;color:var(--text-heading);margin:4px 0">&#9881; <b>' + issue.name + '</b> service ' + (issue.missing ? 'not found' : 'unhealthy') + '</div>');
                if (fix) {
                    lines.push('<div style="font-size:11px;color:var(--text-secondary);margin-left:16px">Start: <code style="background:var(--accent-bg);padding:1px 6px;border-radius:3px;font-size:11px">' + fix.start + '</code></div>');
                }
            }
        });
        lines.push('<div style="font-size:10px;color:var(--text-muted);margin-top:6px"><a href="/topology" style="color:var(--accent);text-decoration:none">View full system topology →</a></div>');
        lines.push('<span onclick="this.parentElement.remove()" style="position:absolute;top:8px;right:12px;cursor:pointer;color:var(--text-muted);font-size:16px">&times;</span>');
        banner.innerHTML = lines.join('');

        // Insert after header
        var header = document.querySelector('.eos-header');
        if (header && header.nextSibling) {
            header.parentNode.insertBefore(banner, header.nextSibling);
        } else {
            var page = document.querySelector('.page') || document.body;
            page.prepend(banner);
        }
    }
    EOS._showHealthBanner = _showHealthBanner;

    // --- Job Progress Banner (system-wide, sticky) ---
    // Listens for job:started/job:progress/job:completed/job:failed via WebSocket.
    // Any app calling self.start_job() shows a banner on ALL pages.

    EOS._jobs = {};  // active jobs keyed by id

    EOS._initJobBanner = function() {
        EOS.on('job:started', function(data) { EOS._onJob(data); });
        EOS.on('job:progress', function(data) { EOS._onJob(data); });
        EOS.on('job:completed', function(data) { EOS._onJobDone(data, false); });
        EOS.on('job:failed', function(data) { EOS._onJobDone(data, true); });
    };

    EOS._onJob = function(data) {
        EOS._jobs[data.id] = data;
        EOS._renderJobBanner();
    };

    EOS._onJobDone = function(data, failed) {
        data._done = true;
        data._failed = failed;
        EOS._jobs[data.id] = data;
        EOS._renderJobBanner();
        // Auto-dismiss after 4s
        setTimeout(function() {
            delete EOS._jobs[data.id];
            EOS._renderJobBanner();
        }, 4000);
    };

    EOS._dismissJob = function(id) {
        delete EOS._jobs[id];
        EOS._renderJobBanner();
    };

    EOS._renderJobBanner = function() {
        var ids = Object.keys(EOS._jobs);
        var banner = document.getElementById('eos-job-banner');

        if (ids.length === 0) {
            if (banner) banner.remove();
            return;
        }

        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'eos-job-banner';
            banner.style.cssText = 'position:fixed;top:0;left:0;right:0;padding-top:env(safe-area-inset-top,0px);z-index:9999;font-family:inherit';
            document.body.appendChild(banner);
        }

        var html = '';
        ids.forEach(function(id) {
            var j = EOS._jobs[id];
            var pct = j.pct || 0;
            var done = j._done;
            var failed = j._failed;

            var bg = failed ? 'rgba(239,68,68,0.95)' : done ? 'rgba(34,197,94,0.95)' : 'color-mix(in srgb, var(--bg) 97%, transparent)';
            var border = failed ? 'rgba(239,68,68,0.4)' : done ? 'rgba(34,197,94,0.3)' : 'rgba(99,102,241,0.3)';
            var barColor = failed ? '#ef4444' : done ? '#22c55e' : '#6366f1';

            var label = esc(j.label || j.id);
            var phase = j.phase && j.phase !== 'starting' && j.phase !== 'done' && j.phase !== 'error' ? ' — ' + esc(j.phase) : '';
            var detail = j.detail && j.detail !== 'completed' ? ' · ' + esc(j.detail) : '';
            var app = j.app ? '<span style="opacity:0.5;font-size:11px;margin-right:6px">' + esc(j.app) + '</span>' : '';
            var icon = failed ? '✕' : done ? '✓' : '⟳';
            var pctText = !done && !failed && pct > 0 ? ' ' + pct + '%' : '';

            html += '<div style="background:' + bg + ';border-bottom:1px solid ' + border + ';padding:8px 16px;display:flex;align-items:center;gap:10px;font-size:13px;color:var(--text-heading)">';
            html += '<span style="font-size:15px;' + (!done && !failed ? 'animation:eos-job-spin 1s linear infinite;display:inline-block' : '') + '">' + icon + '</span>';
            html += '<span style="flex:1">' + app + '<b>' + label + '</b>' + phase + detail + pctText + '</span>';

            // Progress bar (only for in-progress)
            if (!done && !failed && pct > 0) {
                html += '<div style="width:120px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">';
                html += '<div style="width:' + pct + '%;height:100%;background:' + barColor + ';border-radius:2px;transition:width 0.3s ease"></div>';
                html += '</div>';
            }

            // Dismiss button
            html += '<span onclick="EOS._dismissJob(\'' + id.replace(/'/g, "\\'") + '\')" style="cursor:pointer;opacity:0.5;font-size:16px" title="Dismiss">&times;</span>';
            html += '</div>';
        });

        banner.innerHTML = html;
    };

    // Inject spinner keyframe (once)
    if (!document.getElementById('eos-job-style')) {
        var style = document.createElement('style');
        style.id = 'eos-job-style';
        style.textContent = '@keyframes eos-job-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}';
        document.head.appendChild(style);
    }

    // Also poll /api/jobs on page load to pick up already-running jobs
    fetch(base + '/api/jobs').then(function(r) { return r.json(); }).then(function(jobs) {
        if (!Array.isArray(jobs)) return;
        jobs.forEach(function(j) {
            if (!j.finished) {
                EOS._jobs[j.id] = j;
            }
        });
        if (Object.keys(EOS._jobs).length > 0) EOS._renderJobBanner();
    }).catch(function() {});

    // Start listening (lazy — realtime connects on first EOS.on call)
    EOS._initJobBanner();

    // --- Client-side deep-path routing ---
    // Apps opt-in: EOS.route({ '/story/:id': p => openStory(p.id), '*': showList })
    // On page load, matches pathname against the app prefix + patterns.

    EOS.appPrefix = '';  // set by app before calling EOS.route()

    function _matchRoute(pattern, path) {
        var paramNames = [];
        var regexStr = '^' + pattern.replace(/:([^/]+)/g, function(_, name) {
            paramNames.push(name);
            return '([^/]+)';
        }) + '/?$';
        var match = path.match(new RegExp(regexStr));
        if (!match) return null;
        var params = {};
        for (var i = 0; i < paramNames.length; i++) {
            params[paramNames[i]] = decodeURIComponent(match[i + 1]);
        }
        return params;
    }

    EOS.route = function(routes) {
        var prefix = EOS.appPrefix || '';
        var subpath = location.pathname;
        // Strip prefix: /expense/reports/2024 -> /reports/2024
        if (prefix && subpath.startsWith(prefix)) subpath = subpath.slice(prefix.length);
        if (!subpath || subpath === '/') subpath = '/';

        var keys = Object.keys(routes);
        for (var i = 0; i < keys.length; i++) {
            if (keys[i] === '*') continue;
            var params = _matchRoute(keys[i], subpath);
            if (params !== null) {
                routes[keys[i]](params);
                return params;
            }
        }
        if (routes['*']) routes['*']({});
        return null;
    };

    EOS.navigate = function(subpath) {
        var prefix = EOS.appPrefix || '';
        history.pushState(null, '', prefix + subpath);
        window.dispatchEvent(new Event('eos:navigate'));
    };

    // Re-route on browser back/forward
    window.addEventListener('popstate', function() {
        window.dispatchEvent(new Event('eos:navigate'));
    });

    // --- Haptics ---
    // Light tap feedback on primary actions. No-op on devices that ignore
    // navigator.vibrate (most desktops, iOS Safari currently — but harmless).
    EOS.haptic = function(ms) {
        try { if (navigator.vibrate) navigator.vibrate(ms || 10); } catch (e) {}
    };
    // Delegated: any tap on a known action surface fires a 10ms pulse.
    document.addEventListener('pointerdown', function(e) {
        if (e.pointerType !== 'touch') return; // only on touch input
        var t = e.target.closest && e.target.closest(
            '.eos-fab-pill, .eos-fab-master, .eos-chat-send, .msg-action-btn, .hfc-btn, .eos-tab, .rec-btn'
        );
        if (t) EOS.haptic(10);
    }, {passive: true});

    // --- PWA ---
    // Inject meta tags (once, idempotent)
    if (!document.querySelector('link[rel="manifest"]')) {
        var link = document.createElement('link');
        link.rel = 'manifest';
        link.href = '/manifest.webmanifest';
        document.head.appendChild(link);
    }
    if (!document.querySelector('meta[name="theme-color"]')) {
        var tc = document.createElement('meta');
        tc.name = 'theme-color';
        tc.content = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || '#1a1a2e';
        document.head.appendChild(tc);
    }
    // Apple PWA tags
    [['mobile-web-app-capable', 'yes'], ['apple-mobile-web-app-status-bar-style', 'black-translucent']].forEach(function(pair) {
        if (!document.querySelector('meta[name="' + pair[0] + '"]')) {
            var m = document.createElement('meta');
            m.name = pair[0];
            m.content = pair[1];
            document.head.appendChild(m);
        }
    });
    if (!document.querySelector('link[rel="apple-touch-icon"]')) {
        var icon = document.createElement('link');
        icon.rel = 'apple-touch-icon';
        icon.href = '/static/icon-192.png';
        document.head.appendChild(icon);
    }
    // iOS PWA splash screens — Apple ignores any image whose dimensions don't
    // match the device exactly, so we ship a per-device table. PNGs generated
    // by scripts/generate_splash_screens.py — its SIZES list must stay in sync
    // with this table (file-w, file-h columns).
    var isApple = /iPhone|iPad|iPod/.test(navigator.userAgent);
    if (isApple && !document.querySelector('link[rel="apple-touch-startup-image"]')) {
        var splash = [
            // [pt-width, pt-height, dpr, file-w, file-h]
            [375, 667, 2, 750, 1334],     // iPhone SE / 8
            [414, 896, 2, 828, 1792],     // iPhone XR / 11
            [375, 812, 3, 1125, 2436],    // iPhone X / XS / 11 Pro
            [390, 844, 3, 1170, 2532],    // iPhone 12 / 13 / 14
            [393, 852, 3, 1179, 2556],    // iPhone 14 Pro / 15 / 16
            [414, 896, 3, 1242, 2688],    // iPhone XS Max / 11 Pro Max
            [428, 926, 3, 1284, 2778],    // iPhone 12/13 Pro Max / 14 Plus
            [430, 932, 3, 1290, 2796],    // iPhone 14/15/16 Pro Max
            [768, 1024, 2, 1536, 2048],   // iPad mini / Air
            [810, 1080, 2, 1620, 2160],   // iPad 10.2
            [834, 1112, 2, 1668, 2224],   // iPad Air 10.5
            [834, 1194, 2, 1668, 2388],   // iPad Pro 11
            [1024, 1366, 2, 2048, 2732]   // iPad Pro 12.9
        ];
        splash.forEach(function(s) {
            var l = document.createElement('link');
            l.rel = 'apple-touch-startup-image';
            l.href = '/static/splash/splash-' + s[3] + 'x' + s[4] + '.png';
            l.media = '(device-width: ' + s[0] + 'px) and (device-height: ' + s[1] + 'px) ' +
                      'and (-webkit-device-pixel-ratio: ' + s[2] + ') and (orientation: portrait)';
            document.head.appendChild(l);
        });
    }

    // Register service worker
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js').catch(function() {});
    }

    // Install prompt — stash event for later. EOS_UI.pwaInstall (in eos-components.js)
    // exposes a helper that home.html can call to surface an Install button.
    window._eosInstallPromptEvent = null;
    window.addEventListener('beforeinstallprompt', function(e) {
        e.preventDefault();
        window._eosInstallPromptEvent = e;
        // Broadcast so pages (home, settings) can show an Install button if they want.
        window.dispatchEvent(new Event('eos:pwa-installable'));
    });
    window.addEventListener('appinstalled', function() {
        window._eosInstallPromptEvent = null;
        try { localStorage.setItem('eos:pwa-installed', '1'); } catch(e) {}
    });
    // Global UI Dock (Speed Dial) to regulate floating buttons
    EOS._whenAppsKnown(function() {
        // Skip the dock on pages whose primary purpose IS assistant/voice — the
        // chat Send button sits in the same bottom-right corner and gets covered.
        if (location.pathname.startsWith('/voice-assistant')) return;
        if (location.pathname.startsWith('/assistant')) return;
        if (location.pathname.startsWith('/agent')) return;
        // Apps that declare `[app] full_screen = true` in their manifest opt
        // out via the eos-full-screen class injected onto <html> by the
        // kernel page-server. Use this for focus timers, tours, single-shot
        // generators — anything where global chrome steals attention.
        if (document.documentElement.classList.contains('eos-full-screen')) return;

        // Build the entry list from currently-loaded apps. If nothing matches
        // (e.g. demo tier with no voice-assistant), skip the dock entirely —
        // the master ✨ FAB shouldn't appear with an empty dial.
        var entries = [];
        if (EOS.hasApp('voice-assistant')) {
            entries.push({icon: '🎙️', label: 'voice', href: '/voice-assistant/', title: 'Voice Assistant',
                          onclick: function() { EOS._openAssistant(true); }});
        }
        if (entries.length === 0) return;

        var dock = document.createElement('div');
        dock.id = 'eos-fab-dock';
        // The dock sizes to the master button only. The pills container is
        // absolute-positioned above it so it doesn't inflate the dock's
        // bounding box — otherwise elementFromPoint reports the dock as
        // topmost for any click in the ~200px above the FAB, intercepting
        // right-aligned in-app buttons (table-row Edit/Delete, top-right Add).
        dock.style.cssText = 'position:fixed;bottom:max(20px, calc(env(safe-area-inset-bottom) + 16px));right:max(16px, calc(env(safe-area-inset-right) + 12px));z-index:9999;';

        // Master menu button — only toggles the speed dial. Voice Assistant
        // is a separate entry inside the dial, not wired to this button.
        var auraFab = document.createElement('button');
        auraFab.type = 'button';
        auraFab.className = 'eos-fab-master';
        auraFab.title = 'EmptyOS tools';
        auraFab.setAttribute('aria-label', 'Open tools menu');
        auraFab.innerHTML = '<span class="eos-fab-master-icon">\u2728</span>';

        var animStyle = document.createElement('style');
        animStyle.textContent =
            '.eos-fab-master { width:48px; height:48px; border-radius:50%; background:var(--accent); color:var(--accent-ink); border:1px solid color-mix(in srgb, var(--accent) 60%, var(--border)); box-shadow:0 4px 14px var(--shadow); display:flex; align-items:center; justify-content:center; cursor:pointer; padding:0; transition:transform 0.15s var(--ease-out, ease-out), box-shadow 0.15s ease-out; } ' +
            '.eos-fab-master:hover { transform:translateY(-1px); box-shadow:0 6px 18px color-mix(in srgb, var(--accent) 25%, var(--shadow)); } ' +
            '.eos-fab-master:active { transform:translateY(0); } ' +
            '.eos-fab-master-icon { font-size:20px; line-height:1; } ' +
            '.eos-fab-hidden { opacity: 0; pointer-events: none; transform: translateY(8px) scale(0.96); } ' +
            '.eos-fab-visible { opacity: 1; pointer-events: auto; transform: translateY(0) scale(1); } ' +
            '#eos-fab-others > * { position: relative !important; right: auto !important; bottom: auto !important; } ' +
            '@media (prefers-reduced-motion: reduce) { .eos-fab-master, .eos-fab-hidden, .eos-fab-visible { transition: opacity 0.12s ease !important; transform: none !important; } }';
        document.head.appendChild(animStyle);

        // Container for other tools — absolute-positioned above the master.
        // bottom:58px = master 48px + 10px gap. right:0 anchors to dock.
        var othersContainer = document.createElement('div');
        othersContainer.id = 'eos-fab-others';
        othersContainer.style.cssText = 'position:absolute;bottom:58px;right:0;display:flex;flex-direction:column-reverse;gap:10px;align-items:flex-end;transition:all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);';
        othersContainer.className = 'eos-fab-hidden';

        var hoverTimeout;
        dock.onmouseenter = function() {
            clearTimeout(hoverTimeout);
            othersContainer.className = 'eos-fab-visible';
        };
        dock.onmouseleave = function() {
            hoverTimeout = setTimeout(function() {
                othersContainer.className = 'eos-fab-hidden';
            }, 400); // 400ms delay before hiding to prevent accidental dismissal
        };

        var isTouch = false;
        dock.addEventListener('touchstart', function() { isTouch = true; }, {passive: true});
        document.addEventListener('touchstart', function(e) {
            if (isTouch && !dock.contains(e.target)) {
                dock.onmouseleave();
            }
        }, {passive: true});

        // Single role: toggle the speed dial. No navigation.
        auraFab.onclick = function(e) {
            e.preventDefault();
            if (othersContainer.className === 'eos-fab-hidden') dock.onmouseenter();
            else dock.onmouseleave();
        };

        // Entries inside the dial — built from app-presence above.
        entries.forEach(function(e) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'eos-fab-pill';
            btn.title = e.title;
            btn.innerHTML = '<span class="eos-fab-pill-icon">' + e.icon + '</span><span class="eos-fab-pill-label">' + e.label + '</span>';
            btn.onclick = e.onclick || function() { location.href = e.href; };
            othersContainer.appendChild(btn);
        });

        dock.appendChild(auraFab);
        dock.appendChild(othersContainer);

        function injectDock() {
            if (!document.body) return setTimeout(injectDock, 10);
            document.body.appendChild(dock);
        }
        injectDock();
    });

})();

// Runtime UI translation — loads eos-i18n.js, which is a no-op unless the user
// picked a non-English ui.language. Kept out of the page <head> so it can
// translate whatever eos.js + the app rendered. See eos-i18n.js.
(function () {
    if (window.__eosI18nLoaded) return;
    window.__eosI18nLoaded = true;
    try {
        var s = document.createElement('script');
        s.src = '/static/eos-i18n.js';
        s.defer = true;
        (document.head || document.documentElement).appendChild(s);
    } catch (e) { /* fail-silent */ }
})();
