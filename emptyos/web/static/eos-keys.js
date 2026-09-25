/**
 * EmptyOS Keyboard Shortcuts — command palette + global navigation.
 *
 * Auto-loaded by eos.js. No manual setup needed.
 *
 * Global shortcuts:
 *   Ctrl+K / Cmd+K  — Command palette (fuzzy search all apps + actions)
 *   Ctrl+/          — Show shortcut help
 *   Escape          — Close palette/overlays
 *   G → letter      — Go-to navigation (g-t = tasks, g-j = journal, etc.)
 *
 * Per-app shortcuts (registered by apps via EOS.keys.register):
 *   n — New item (task, expense, note, etc.)
 *   r — Refresh
 *   / — Focus search input
 */
(function() {
    'use strict';

    // Idempotency guard — pages that both hard-code <script src="...eos-keys.js">
    // (e.g. auto_ui.py) AND load eos.js (which auto-injects this script) would
    // otherwise double-mount the palette overlay, producing two #eos-palette-input
    // elements that break strict-mode locators in tests.
    if (window.__EOS_KEYS_LOADED) return;
    window.__EOS_KEYS_LOADED = true;

    // --- Launcher mode ---
    // Active when the page is showing in a small transient window — either
    // plugins/command-launcher's borderless Chrome `--app=` window or the
    // desktop shell's frameless quick panel. In this mode: the palette is the
    // entire UI, selecting an app opens it in the FULL-SIZE window rather than
    // inside this 720x480 one, and Esc puts the window away.
    //
    // Both spellings and both hosts are EOS.quickHost's business (eos.js) —
    // this used to read `launcher=1` itself, which meant the shell's `?quick=1`
    // window fell through to the ordinary palette and "open app" navigated the
    // little always-on-top panel to a full app page.
    var IS_LAUNCHER = (window.EOS && EOS.quickHost) ? EOS.quickHost.active() : false;

    // Next theme in the cycle, read from the live registry rather than a copy.
    // The two call sites below each carried their own hardcoded list, both
    // frozen at the pre-digital-garden set — so cycling *from* any theme added
    // after them scored indexOf === -1 and silently snapped back to 'eos'
    // instead of advancing. Deriving from EOS.THEMES means a new theme joins
    // the cycle for free.
    function _nextTheme() {
        var themes = (window.EOS && EOS.THEMES) || ['eos'];
        var current = localStorage.getItem('eos-theme') || 'eos';
        var i = themes.indexOf(current);
        return themes[(i + 1) % themes.length];   // unknown → i=-1 → themes[0]
    }

    function launcherDismiss() {
        // Chrome --app= windows allow window.close() from script; the desktop
        // shell's window does not close, it hides (it is preloaded and reused).
        // EOS.quickHost knows which host this is.
        if (window.EOS && EOS.quickHost) return EOS.quickHost.dismiss();
        try { window.close(); } catch (_) {}
    }

    // Open a same-origin path in the full-size window. In the shell that is the
    // main window; in a browser launcher window it is the user's real browser.
    function launcherOpenMain(path) {
        if (window.EOS && EOS.quickHost) return EOS.quickHost.openMain(path);
        try { window.open(path, '_blank', 'noopener'); } catch (_) {}
    }

    // --- State ---
    var palette = null;
    var helpOverlay = null;
    var gPrefix = false;
    var gTimer = null;
    var appShortcuts = {};
    var paletteVisible = false;

    // --- Go-to map (loaded from API, fallback to hardcoded) ---
    var GO_MAP = {};
    var shortcutsLoaded = false;

    // Load shortcuts from server (settings-configurable). Server already filters
    // its returned go_map to loaded apps; the offline fallback below filters via
    // EOS.hasApp() so g+letter never opens a 404 in trimmed tiers (core/demo).
    function loadShortcuts() {
        if (shortcutsLoaded) return Promise.resolve();
        return fetch(EOS.base + '/api/shortcuts')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.go_map) GO_MAP = data.go_map;
                shortcutsLoaded = true;
            })
            .catch(function() {
                var fallback = {
                    'h': {path: '/', label: 'Home'},
                    't': {path: '/task/', label: 'Tasks'},
                    'j': {path: '/journal/', label: 'Journal'},
                    'e': {path: '/expense/', label: 'Expense'},
                    's': {path: '/search/', label: 'Search'},
                    'a': {path: '/assistant/', label: 'Assistant'},
                };
                var ready = (window.EOS && EOS._appIdsReady) || Promise.resolve(null);
                return ready.then(function() {
                    GO_MAP = {};
                    Object.keys(fallback).forEach(function(k) {
                        var entry = fallback[k];
                        var first = (entry.path || '/').replace(/^\//, '').split('/')[0].split('#')[0];
                        if (!first || (EOS.hasApp && EOS.hasApp(first))) GO_MAP[k] = entry;
                    });
                    shortcutsLoaded = true;
                });
            });
    }
    // Load immediately
    loadShortcuts();

    // --- Palette data (loaded from API) ---
    var allActions = [];
    var actionsLoaded = false;

    function loadActions() {
        if (actionsLoaded) return Promise.resolve();
        return fetch(EOS.base + '/api/apps/clusters')
            .then(function(r) { return r.json(); })
            .then(function(clusters) {
                allActions = [];
                clusters.forEach(function(c) {
                    (c.apps || []).forEach(function(app) {
                        allActions.push({
                            type: 'app',
                            id: app.id,
                            name: app.name || app.id,
                            desc: app.description || '',
                            path: (app.web_prefix || '/' + app.id) + '/',
                            icon: '',
                        });
                    });
                });
                // Add built-in actions
                allActions.push({type:'action', id:'theme-toggle', name:'Toggle Theme', desc:'Switch between dark themes', path:'', icon:''});
                allActions.push({type:'action', id:'shortcuts', name:'Keyboard Shortcuts', desc:'Show all shortcuts', path:'', icon:''});
                allActions.push({type:'action', id:'reload', name:'Reload Page', desc:'Refresh current page', path:'', icon:''});
                allActions.push({type:'action', id:'vault-search', name:'Search Vault', desc:'Search all notes (type then Enter)', path:'', icon:''});
                allActions.push({type:'action', id:'console', name:'Console', desc:'Run CLI commands in browser', path:'/console', icon:''});
                allActions.push({type:'action', id:'topology', name:'Topology', desc:'App dependency graph', path:'/topology', icon:''});
                allActions.push({type:'action', id:'restart-daemon', name:'Restart Daemon', desc:'Run restart.bat (Windows) — kills python.exe and relaunches', path:'', icon:''});
                actionsLoaded = true;
            })
            .catch(function() { actionsLoaded = true; });
    }

    // --- Create palette DOM ---
    function createPalette() {
        if (palette) return;

        var overlay = document.createElement('div');
        overlay.id = 'eos-palette-overlay';
        overlay.onclick = function(e) { if (e.target === overlay) hidePalette(); };

        var container = document.createElement('div');
        container.id = 'eos-palette';

        container.innerHTML =
            '<input id="eos-palette-input" type="text" placeholder="Search apps... (> to capture, ? to search vault)" autocomplete="off" spellcheck="false">' +
            '<div id="eos-palette-results"></div>' +
            '<div id="eos-palette-footer">' +
                '<span class="pf-key">↑↓</span> navigate ' +
                '<span class="pf-key">↵</span> open ' +
                '<span class="pf-key">></span> capture ' +
                '<span class="pf-key">?</span> search vault ' +
                '<span class="pf-key">esc</span> close' +
            '</div>';

        overlay.appendChild(container);
        document.body.appendChild(overlay);
        palette = overlay;

        var input = document.getElementById('eos-palette-input');
        input.addEventListener('input', function() { filterPalette(this.value); });
        input.addEventListener('keydown', handlePaletteKey);
    }

    function showPalette() {
        loadActions().then(function() {
            createPalette();
            palette.classList.add('show');
            paletteVisible = true;
            var input = document.getElementById('eos-palette-input');
            input.value = '';
            filterPalette('');
            setTimeout(function() { input.focus(); }, 50);
        });
    }

    function hidePalette() {
        if (palette) palette.classList.remove('show');
        paletteVisible = false;
        // Dismiss the window only when the palette IS the window. A page with
        // its own launcher chrome (portal's composer, hub's grid) has content
        // behind the overlay and owns its own Esc — closing the window from
        // under the palette would discard the draft the user was returning to,
        // which is exactly what portal-quick's ESC_OWNERS list stands down to
        // let us avoid. Hub closes itself (hub.js Escape handler), so neither
        // page is left stranded.
        if (IS_LAUNCHER && !hasOwnLauncherChrome()) launcherDismiss();
    }

    var selectedIdx = 0;
    var filteredActions = [];

    function filterPalette(query) {
        var q = query.toLowerCase().trim();
        if (!q) {
            filteredActions = allActions.slice(0, 12);
        } else {
            // Score matches so name/id hits outrank description-only hits.
            // Otherwise typing "journal" can pick Calendar (whose description
            // mentions "journal entries") above the actual Journal app.
            // Lower score = better match.
            //   0: exact name/id match
            //   1: name/id starts with query
            //   2: name/id contains query
            //   3: description contains query (and name/id doesn't)
            var scored = [];
            allActions.forEach(function(a) {
                var name = (a.name || '').toLowerCase();
                var id = (a.id || '').toLowerCase();
                var desc = (a.desc || '').toLowerCase();
                var score = -1;
                if (name === q || id === q) score = 0;
                else if (name.indexOf(q) === 0 || id.indexOf(q) === 0) score = 1;
                else if (name.indexOf(q) >= 0 || id.indexOf(q) >= 0) score = 2;
                else if (desc.indexOf(q) >= 0) score = 3;
                if (score >= 0) scored.push({a: a, s: score});
            });
            scored.sort(function(x, y) { return x.s - y.s; });
            filteredActions = scored.slice(0, 12).map(function(x) { return x.a; });
        }
        selectedIdx = 0;
        renderPaletteResults();
    }

    function renderPaletteResults() {
        var el = document.getElementById('eos-palette-results');
        if (!el) return;
        if (!filteredActions.length) {
            el.innerHTML = '<div class="pr-empty">No matches</div>';
            return;
        }
        el.innerHTML = filteredActions.map(function(a, i) {
            var cls = i === selectedIdx ? 'pr-item selected' : 'pr-item';
            var badge = a.type === 'action' ? '<span class="pr-badge">Action</span>' : '';
            // Find go-to shortcut for this app
            var shortcut = '';
            for (var key in GO_MAP) {
                if (GO_MAP[key].path === a.path) {
                    shortcut = '<span class="pr-shortcut">g ' + key + '</span>';
                    break;
                }
            }
            return '<div class="' + cls + '" data-idx="' + i + '" onclick="EOS.keys._select(' + i + ')">' +
                '<div class="pr-name">' + esc(a.name) + badge + shortcut + '</div>' +
                '<div class="pr-desc">' + esc(a.desc) + '</div>' +
            '</div>';
        }).join('');
    }

    function handlePaletteKey(e) {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            selectedIdx = Math.min(selectedIdx + 1, filteredActions.length - 1);
            renderPaletteResults();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            selectedIdx = Math.max(selectedIdx - 1, 0);
            renderPaletteResults();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            var val = document.getElementById('eos-palette-input').value.trim();
            // > prefix = quick capture
            if (val.startsWith('>')) {
                var text = val.substring(1).trim();
                if (text) quickCapture(text);
                return;
            }
            // ? prefix = vault search
            if (val.startsWith('?')) {
                var query = val.substring(1).trim();
                if (query) {
                    var searchUrl = '/search/?q=' + encodeURIComponent(query);
                    if (IS_LAUNCHER) {
                        // launcherOpenMain, not a raw window.open: in the shell's
                        // quick window the bridge is the only route to the main
                        // window, and it dismisses us itself. A bare window.open
                        // there drops the query on the floor.
                        launcherOpenMain(searchUrl);
                    } else {
                        hidePalette();
                        location.href = searchUrl;
                    }
                }
                return;
            }
            selectAction(selectedIdx);
        } else if (e.key === 'Escape') {
            hidePalette();
        }
    }

    function quickCapture(text) {
        // In launcher mode the toast would never be seen (window closes immediately),
        // so await the fetch before dismissing — the user gets a visible 200ms beat
        // when they see their text vanish into the daemon.
        var p = fetch(EOS.base + '/quick-action/api/smart-add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: text}),
        });
        if (IS_LAUNCHER) {
            p.finally(function() { launcherDismiss(); });
            return;
        }
        hidePalette();
        p.then(function(r) { return r.json(); })
        .then(function(d) {
            if (typeof EOS_UI !== 'undefined') EOS_UI.toast('Captured: ' + (d.tag || '') + ' ' + text.substring(0, 30));
        })
        .catch(function() {
            if (typeof EOS_UI !== 'undefined') EOS_UI.toast('Capture failed', false);
        });
    }

    function selectAction(idx) {
        var action = filteredActions[idx];
        if (!action) return;

        // In launcher mode, the launcher window is a transient palette — opening
        // an app or running an action that needs the full UI should happen in the
        // user's main browser, not inside this 720×480 borderless window.
        if (IS_LAUNCHER) {
            if (action.type === 'action') {
                if (action.id === 'theme-toggle') {
                    EOS.setTheme(_nextTheme());
                    // theme-toggle is the one action that stays inside the launcher;
                    // user wants to flip and keep typing. Don't dismiss.
                    return;
                }
                // shortcuts/reload/restart-daemon don't make sense in a transient
                // launcher window — open the canonical page full-size instead.
                var fallback = action.id === 'vault-search' ? '/search/' : '/';
                launcherOpenMain(fallback);
            } else if (action.path) {
                launcherOpenMain(action.path);
            }
            launcherDismiss();
            return;
        }

        hidePalette();

        if (action.type === 'action') {
            if (action.id === 'theme-toggle') {
                EOS.setTheme(_nextTheme());
            } else if (action.id === 'shortcuts') {
                showHelp();
            } else if (action.id === 'reload') {
                location.reload();
            } else if (action.id === 'vault-search') {
                location.href = '/search/';
            } else if (action.id === 'restart-daemon') {
                triggerRestartDaemon();
            }
        } else if (action.path) {
            location.href = action.path;
        }
    }

    // --- Restart daemon (Ctrl+K → "restart") ---
    // Confirm-gated. Spawns a detached cmd that survives `taskkill /F /IM python.exe`.
    // After the POST the current daemon dies in ~2s; we poll until it's back, then reload.
    function triggerRestartDaemon() {
        hidePalette();
        if (!confirm('Restart EmptyOS daemon?\n\nrestart.bat will kill ALL python.exe processes (including this one), then relaunch. The page will lose its connection — wait ~15-20s while it recovers.')) return;
        var toastFn = (window.EOS_UI && EOS_UI.toast) ? EOS_UI.toast : function(m){ console.log(m); };
        toastFn('Restarting daemon…', 'info');
        fetch('/settings/api/restart-daemon', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: true })
        }).then(function(r) { return r.json(); })
          .catch(function(e) { return { error: 'request lost (daemon already dying?): ' + e.message }; })
          .then(function(r) {
            if (r && r.error) {
                alert('Restart failed: ' + r.error);
                return;
            }
            // Show a sticky banner because the page can't update much else now.
            var banner = document.createElement('div');
            banner.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:9999;padding:12px 18px;background:var(--warning);color:#000;border-radius:10px;font-size:13px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,.3);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;';
            banner.textContent = 'Daemon restarting… polling for recovery';
            document.body.appendChild(banner);

            var tries = 0;
            var probe = setInterval(function() {
                tries++;
                fetch('/api/health', { cache: 'no-store' })
                    .then(function(resp) {
                        if (resp.ok) {
                            clearInterval(probe);
                            banner.style.background = '#22c55e';
                            banner.textContent = 'Daemon back up — reloading';
                            setTimeout(function() { location.reload(); }, 600);
                        }
                    }).catch(function() { /* still down */ });
                if (tries > 60) {
                    clearInterval(probe);
                    banner.style.background = '#ef4444';
                    banner.style.color = '#fff';
                    banner.textContent = 'Daemon did not come back within 2 minutes. Check terminal output.';
                }
            }, 2000);
        });
    }

    // --- Help overlay ---
    function createHelp() {
        if (helpOverlay) return;

        helpOverlay = document.createElement('div');
        helpOverlay.id = 'eos-help-overlay';
        helpOverlay.onclick = function(e) { if (e.target === helpOverlay) hideHelp(); };

        var content = document.createElement('div');
        content.id = 'eos-help-panel';

        var goRows = '';
        var sortedKeys = Object.keys(GO_MAP).sort();
        for (var i = 0; i < sortedKeys.length; i++) {
            var k = sortedKeys[i];
            goRows += '<tr><td><kbd>g</kbd> <kbd>' + k + '</kbd></td><td>' + esc(GO_MAP[k].label) + '</td></tr>';
        }

        var appRows = '';
        for (var key in appShortcuts) {
            appRows += '<tr><td><kbd>' + esc(key) + '</kbd></td><td>' + esc(appShortcuts[key].desc) + '</td></tr>';
        }

        content.innerHTML =
            '<div class="help-header"><h2>Keyboard Shortcuts</h2><button onclick="EOS.keys.hideHelp()" aria-label="Hide help">&times;</button></div>' +
            '<div class="help-section">' +
                '<h3>Global</h3>' +
                '<table>' +
                    '<tr><td><kbd>Ctrl</kbd>+<kbd>K</kbd></td><td>Command palette</td></tr>' +
                    '<tr><td><kbd>Ctrl</kbd>+<kbd>/</kbd></td><td>This help</td></tr>' +
                    '<tr><td><kbd>Esc</kbd></td><td>Close overlay</td></tr>' +
                '</table>' +
            '</div>' +
            '<div class="help-section">' +
                '<h3>Go To (press <kbd>g</kbd> then a letter)</h3>' +
                '<table>' + goRows + '</table>' +
            '</div>' +
            (appRows ? '<div class="help-section"><h3>This Page</h3><table>' + appRows + '</table></div>' : '');

        helpOverlay.appendChild(content);
        document.body.appendChild(helpOverlay);
    }

    function showHelp() {
        createHelp();
        helpOverlay.classList.add('show');
    }

    function hideHelp() {
        if (helpOverlay) helpOverlay.classList.remove('show');
    }

    // --- Global key handler ---
    function isInputFocused() {
        var el = document.activeElement;
        if (!el) return false;
        var tag = el.tagName;
        return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
    }

    document.addEventListener('keydown', function(e) {
        // Ctrl+K / Cmd+K — command palette
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            if (paletteVisible) hidePalette(); else showPalette();
            return;
        }

        // Ctrl+/ — help
        if ((e.ctrlKey || e.metaKey) && e.key === '/') {
            e.preventDefault();
            showHelp();
            return;
        }

        // Ctrl+Shift+P / Cmd+Shift+P — presentation mode toggle
        // (Hide personal data without restarting the daemon — for screenshare.)
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'P' || e.key === 'p')) {
            e.preventDefault();
            if (window.EOS && EOS.presentation && EOS.presentation.toggle) {
                EOS.presentation.toggle();
            }
            return;
        }

        // Escape — close overlays
        if (e.key === 'Escape') {
            if (paletteVisible) { hidePalette(); return; }
            if (helpOverlay && helpOverlay.classList.contains('show')) { hideHelp(); return; }
            return;
        }

        // Skip if input is focused
        if (isInputFocused()) return;

        // G-prefix navigation
        if (gPrefix) {
            gPrefix = false;
            clearTimeout(gTimer);
            var target = GO_MAP[e.key];
            if (target) {
                e.preventDefault();
                // Same rule as selectAction: a quick window is 360px wide with
                // no title bar and no nav, so navigating IT to a full app page
                // strands the user. Hand the path to the main window instead.
                if (IS_LAUNCHER) launcherOpenMain(target.path);
                else location.href = target.path;
            }
            return;
        }

        if (e.key === 'g' && !e.ctrlKey && !e.metaKey && !e.altKey) {
            gPrefix = true;
            gTimer = setTimeout(function() { gPrefix = false; }, 1000);
            return;
        }

        // ? — help
        if (e.key === '?') {
            showHelp();
            return;
        }

        // / — focus search. Prefer a search input already visible on the page
        // (e.g. hub's inline searchBar); otherwise open the global nav search
        // overlay (EOS._openSearchOverlay, defined in eos.js). Single `/` owner
        // — the nav overlay's searchBar is created with focusKey:null so it
        // doesn't bind a second `/`.
        if (e.key === '/' && !e.ctrlKey) {
            var inputs = document.querySelectorAll('#search, [data-shortcut-search], .eos-search-input');
            var visible = Array.prototype.filter.call(inputs, function(el) {
                return el.offsetParent !== null;  // rendered + not display:none
            })[0];
            if (visible) {
                e.preventDefault();
                visible.focus();
            } else if (window.EOS && EOS._openSearchOverlay) {
                e.preventDefault();
                EOS._openSearchOverlay();
            } else if (inputs[0]) {
                e.preventDefault();
                inputs[0].focus();
            }
            return;
        }

        // Per-app shortcuts
        var shortcut = appShortcuts[e.key];
        if (shortcut && shortcut.fn) {
            e.preventDefault();
            shortcut.fn();
        }
    });

    // --- Launcher mode boot ---
    // Auto-mount the palette + hide chrome + close on focus loss — UNLESS the
    // page declares its own launcher chrome via `<body data-own-launcher-mode>`.
    // A plain HTML attribute (not a JS flag) so the check is race-free: this
    // script loads asynchronously (eos.js injects it as a dynamic <script>,
    // async by default), so its execution order relative to the page's own
    // bottom-of-body launcher IIFE (hub.js/portal.js) is not guaranteed — but
    // the attribute is part of the initial `<body ...>` markup, present the
    // instant the element exists, before any script runs. Found while wiring
    // Portal into the global-hotkey launcher (gap analysis,
    // portal-not-in-global-launcher): hub's own targeted-hide launcher chrome
    // (hub.js applyLauncherMode) was ALSO silently superseded by this palette
    // every time — confirmed live, hub and portal rendered byte-identical
    // full-screen palettes. Not a portal-only bug; a platform one.
    function hasOwnLauncherChrome() {
        return !!(document.body && document.body.hasAttribute('data-own-launcher-mode'));
    }
    if (IS_LAUNCHER) {
        // Set body class as early as possible so launcher-mode CSS can hide chrome
        // before paint. Document might not be ready yet — use readystatechange.
        function applyLauncherBody() {
            if (document.body && !hasOwnLauncherChrome()) document.body.classList.add('launcher-mode');
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', applyLauncherBody);
        } else {
            applyLauncherBody();
        }
        // Show the palette as soon as the page is ready and actions have loaded —
        // skipped when the page renders its own launcher chrome instead.
        var bootShow = function() { applyLauncherBody(); if (!hasOwnLauncherChrome()) showPalette(); };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', bootShow);
        } else {
            // Defer one tick so the palette's overlay z-index sits above any
            // late-arriving page content.
            setTimeout(bootShow, 0);
        }
        // Click-outside the palette container (not just on the overlay) → dismiss.
        // Lose focus → dismiss. Anywhere-else hides the launcher window.
        window.addEventListener('blur', function() {
            // 120ms debounce: switching to dev-tools briefly fires blur. Adjust if
            // it feels too eager.
            setTimeout(function() {
                if (!document.hasFocus()) launcherDismiss();
            }, 120);
        });
    }

    // --- Public API ---
    EOS.keys = {
        // Register a per-app shortcut
        register: function(key, desc, fn) {
            appShortcuts[key] = {desc: desc, fn: fn};
        },

        // Show/hide
        showPalette: showPalette,
        hidePalette: hidePalette,
        showHelp: showHelp,
        hideHelp: hideHelp,

        // Internal (for onclick)
        _select: selectAction,

        // Exposed for hands-free overlay — same registry the palette uses
        _appShortcuts: appShortcuts,
        _allActions: function() { return allActions; },
        _filteredActions: function() { return filteredActions; },
        _loadActions: loadActions,
        _filterPalette: filterPalette,
    };
})();
