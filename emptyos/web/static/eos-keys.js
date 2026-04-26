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
    }

    var selectedIdx = 0;
    var filteredActions = [];

    function filterPalette(query) {
        var q = query.toLowerCase().trim();
        filteredActions = allActions.filter(function(a) {
            if (!q) return true;
            return (a.name || '').toLowerCase().includes(q) ||
                   (a.desc || '').toLowerCase().includes(q) ||
                   (a.id || '').toLowerCase().includes(q);
        }).slice(0, 12);
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
                if (query) { hidePalette(); location.href = '/search/?q=' + encodeURIComponent(query); }
                return;
            }
            selectAction(selectedIdx);
        } else if (e.key === 'Escape') {
            hidePalette();
        }
    }

    function quickCapture(text) {
        hidePalette();
        fetch(EOS.base + '/quick-action/api/smart-add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: text}),
        })
        .then(function(r) { return r.json(); })
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
        hidePalette();

        if (action.type === 'action') {
            if (action.id === 'theme-toggle') {
                var themes = ['eos', 'void-dark', 'warm-dark', 'nord', 'soft-light'];
                var current = localStorage.getItem('eos-theme') || 'eos';
                var next = themes[(themes.indexOf(current) + 1) % themes.length];
                EOS.setTheme(next);
            } else if (action.id === 'shortcuts') {
                showHelp();
            } else if (action.id === 'reload') {
                location.reload();
            } else if (action.id === 'vault-search') {
                location.href = '/search/';
            }
        } else if (action.path) {
            location.href = action.path;
        }
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
            '<div class="help-header"><h2>Keyboard Shortcuts</h2><button onclick="EOS.keys.hideHelp()">&times;</button></div>' +
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
                location.href = target.path;
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

        // / — focus search input (if exists on page)
        if (e.key === '/' && !e.ctrlKey) {
            var searchInput = document.querySelector('#search, [data-shortcut-search], .eos-search-input');
            if (searchInput) {
                e.preventDefault();
                searchInput.focus();
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
