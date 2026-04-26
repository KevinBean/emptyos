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

    // --- Theme ---
    var theme = localStorage.getItem('eos-theme') || 'eos';
    document.documentElement.className = 'theme-' + theme;

    // --- API base ---
    var base = location.protocol + '//' + location.host;

    // --- EOS namespace ---
    window.EOS = window.EOS || {};
    EOS.base = base;

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

    EOS.api = async function(path, options) {
        var resp = await fetch(base + path, _injectByokHeaders(options));
        if (!resp.ok) throw new Error('API error: ' + resp.status);
        return resp.json();
    };

    EOS.post = async function(path, data) {
        return EOS.api(path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data),
        });
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

    function _renderNav(navApps, currentApp) {
        var nav = document.createElement('nav');
        nav.className = 'nav';
        var isHome = !currentApp || currentApp === 'hub';
        var links = '<a href="/"' + (isHome ? ' class="current"' : '') + '>Home</a>';
        navApps.forEach(function(a) {
            var cls = a.id === currentApp ? ' class="current"' : '';
            links += '<a href="' + a.prefix + '/"' + cls + '>' + a.name + '</a>';
        });
        if (currentApp && currentApp !== 'hub' && !navApps.some(function(a) { return a.id === currentApp; })) {
            links += '<a href="#" class="current">' + currentApp + '</a>';
        }
        links += '<span class="nav-theme" onclick="EOS.cycleTheme()" title="Cycle theme (full list in Settings)">◐</span>';
        links += '<span class="nav-more" onclick="EOS.toggleDrawer()" title="All Apps">⋯</span>';
        nav.innerHTML = links;
        // Replace any existing nav (so the async filter can re-render cleanly)
        var existing = document.querySelector('body > nav.nav');
        if (existing) existing.replaceWith(nav);
        else document.body.prepend(nav);
        return nav;
    }

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
            apps.forEach(function(a) {
                var id = (a && (a.id || a.name || a)) + '';
                loaded[id] = true;
            });
            var filtered = navApps.filter(function(a) { return loaded[a.id]; });
            // Only re-render if we actually pruned something (avoid flicker)
            if (filtered.length !== navApps.length) _renderNav(filtered, currentApp);
        }).catch(function(){ /* fall back to whatever we already rendered */ });

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
            drawer.innerHTML = '<div class="app-drawer-header"><span class="app-drawer-title">All Apps</span><span class="app-drawer-close" onclick="EOS.toggleDrawer(false)">&times;</span></div>' +
                '<input class="app-drawer-search" id="drawer-search" placeholder="Filter apps..." autocomplete="off">' +
                '<div class="app-drawer-list" id="drawer-list"></div>';
            document.body.appendChild(drawer);

            // Load apps
            fetch(base + '/api/apps/clusters').then(function(r) { return r.json(); }).then(function(clusters) {
                var allApps = [];
                clusters.forEach(function(c) { (c.apps || []).forEach(function(a) { allApps.push(a); }); });
                allApps.sort(function(a, b) { return (a.name || '').localeCompare(b.name || ''); });
                EOS._drawerApps = allApps;
                EOS._renderDrawer('');
            }).catch(function() {});

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

    EOS._drawerApps = [];
    EOS._renderDrawer = function(q) {
        var list = document.getElementById('drawer-list');
        if (!list) return;
        var apps = EOS._drawerApps.filter(function(a) {
            if (!q) return true;
            return (a.name || '').toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q);
        });
        list.innerHTML = apps.map(function(a) {
            return '<a class="app-drawer-item" href="' + (a.web_prefix || '#') + '/"><div class="adi-name">' + (a.name || a.id) + '</div><div class="adi-desc">' + (a.description || '') + '</div></a>';
        }).join('') || '<div style="grid-column:1/-1;text-align:center;padding:20px;color:var(--text-muted);font-size:13px">No matches</div>';
    };

    EOS.toggleDrawer = function(force) {
        var overlay = document.getElementById('app-drawer-overlay');
        var drawer = document.getElementById('app-drawer');
        if (!overlay || !drawer) return;
        var open = force !== undefined ? force : !drawer.classList.contains('open');
        overlay.classList.toggle('open', open);
        drawer.classList.toggle('open', open);
        if (open) {
            var input = document.getElementById('drawer-search');
            if (input) { input.value = ''; EOS._renderDrawer(''); setTimeout(function() { input.focus(); }, 250); }
        }
    };

    // --- Utilities ---
    window.esc = function(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    };

    window.escAttr = function(s) {
        return (s || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
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

    // Escape a path for use in JS string inside HTML onclick
    // Normalizes to forward slashes first, then escapes quotes
    EOS.escPath = function(p) {
        return EOS.normPath(p).replace(/\\/g, '/').replace(/'/g, "\\'");
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

    EOS.THEMES = ['eos', 'soft-light', 'warm-dark', 'void-dark', 'nord'];
    EOS.THEME_LABELS = {
        'eos': 'Warm light',
        'soft-light': 'Soft light',
        'warm-dark': 'Amber dark',
        'void-dark': 'Void dark',
        'nord': 'Nord',
    };

    EOS.setTheme = function(name) {
        localStorage.setItem('eos-theme', name);
        document.documentElement.className = 'theme-' + name;
        // Keep the PWA theme-color meta in sync (controls iOS/Android status bar tint)
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.content = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
        try { window.dispatchEvent(new CustomEvent('eos:theme-changed', {detail: {theme: name}})); } catch(e) {}
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

    // --- Keyboard Shortcuts ---
    // Auto-load eos-keys.css + eos-keys.js on every page
    var keyCss = document.createElement('link');
    keyCss.rel = 'stylesheet';
    keyCss.href = base + '/static/eos-keys.css';
    document.head.appendChild(keyCss);

    var keyScript = document.createElement('script');
    keyScript.src = base + '/static/eos-keys.js';
    document.body.appendChild(keyScript);

    // --- App-presence map (used to gate tier-specific UI) ---
    // Tiers (core / demo / standard / dev) bundle different app sets. Globally
    // injected UI — hands-free overlay, voice FAB — must self-disable when its
    // owning app isn't loaded, otherwise the demo bundle ships dead buttons.
    // First page load waits on /api/apps; subsequent loads use the localStorage
    // cache synchronously so injectors fire without a fetch round-trip, then
    // refresh the cache in the background.
    EOS._loadedAppIds = null;
    try {
        var cached = localStorage.getItem('eos-loaded-app-ids');
        if (cached) EOS._loadedAppIds = JSON.parse(cached);
    } catch(e) {}
    EOS._appIdsReady = fetch(base + '/api/apps')
        .then(function(r) { return r.json(); })
        .then(function(list) {
            var ids = {};
            (list || []).forEach(function(a) { if (a && a.id) ids[a.id] = true; });
            EOS._loadedAppIds = ids;
            try { localStorage.setItem('eos-loaded-app-ids', JSON.stringify(ids)); } catch(e) {}
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
    // was already populated from localStorage; otherwise awaits the fetch.
    EOS._whenAppsKnown = function(fn) {
        if (EOS._loadedAppIds) { fn(); return; }
        EOS._appIdsReady.then(fn);
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

    // --- Page Assistant (AI sidebar) ---
    // Loads EmptyOS's own page-assistant.js when the `assistant` app is present.
    // Skipped on tiers (core/demo) that don't bundle it — page-assistant calls
    // /assistant/api/chat, so without the app it would be dead UI on every page.
    EOS._currentApp = null;
    var _origNav = EOS.nav;
    EOS.nav = function(currentApp) {
        EOS._currentApp = currentApp;
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
            banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;font-family:inherit';
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
            html += '<span onclick="delete EOS._jobs[\'' + id.replace(/'/g, "\\'") + '\'];EOS._renderJobBanner()" style="cursor:pointer;opacity:0.5;font-size:16px" title="Dismiss">&times;</span>';
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

        // Build the entry list from currently-loaded apps. If nothing matches
        // (e.g. demo tier with no voice-assistant), skip the dock entirely —
        // the master ✨ FAB shouldn't appear with an empty dial.
        var entries = [];
        if (EOS.hasApp('voice-assistant')) {
            entries.push({icon: '🎙️', label: 'voice', href: '/voice-assistant/', title: 'Voice Assistant'});
        }
        if (entries.length === 0) return;

        var dock = document.createElement('div');
        dock.id = 'eos-fab-dock';
        dock.style.cssText = 'position:fixed;bottom:max(20px, calc(env(safe-area-inset-bottom) + 16px));right:16px;display:flex;flex-direction:column-reverse;gap:10px;z-index:9999;align-items:flex-end;';

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

        // Container for other tools
        var othersContainer = document.createElement('div');
        othersContainer.id = 'eos-fab-others';
        othersContainer.style.cssText = 'display:flex;flex-direction:column-reverse;gap:10px;align-items:flex-end;transition:all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);';
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
            btn.onclick = function() { location.href = e.href; };
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
