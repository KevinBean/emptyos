/**
 * eos-export-shim.js — runtime polyfill for exported EmptyOS apps.
 *
 * Loaded from _assets/eos-export-shim.js inside a bundle produced by
 * emptyos/sdk/exporter.py. Makes the page survive without a daemon by:
 *
 *   1. Intercepting fetch() calls to `{prefix}/api/*` and routing them to
 *      (a) app-registered handlers, (b) the static _data/state.json snapshot,
 *      or (c) a generic IndexedDB collection keyed by URL path.
 *   2. Stubbing EOS.on/EOS.emit as an in-page EventTarget (no WebSocket).
 *   3. Degrading EOS.openInViewer / EOS.noteActions / EOS.viewerLink to a
 *      "copy path" affordance because no vault viewer is reachable.
 *   4. Rendering a "🔒 Offline export" pill with a panel listing which
 *      fallbacks are active and collecting a BYOK API key when enabled.
 *
 * The app's own code does not need to know it's in export mode — but it MAY
 * check `window.EOS_IS_EXPORT` to hide features that have no meaningful
 * fallback (e.g. a "Sync with daemon" button).
 *
 * Configuration arrives via window globals set by the exporter:
 *   window.EOS_IS_EXPORT         — always true
 *   window.EOS_APP_ID            — the app's id
 *   window.EOS_APP_PREFIX        — e.g. "/boards"
 *   window.EOS_EXPORT_FALLBACKS  — array of "capability:strategy" strings
 *   window.EOS_EXPORT_DATA       — the state snapshot (populated by loader)
 *   window.EOS_EXPORT_ROUTES     — GET-route → state-path mapping (populated by loader)
 */

(function () {
  'use strict';
  if (!window.EOS_IS_EXPORT) return;

  var APP_ID = window.EOS_APP_ID || 'app';
  var APP_PREFIX = window.EOS_APP_PREFIX || '';
  var FALLBACKS = window.EOS_EXPORT_FALLBACKS || [];

  function hasFallback(needle) {
    for (var i = 0; i < FALLBACKS.length; i++) {
      if (FALLBACKS[i] === needle || FALLBACKS[i].indexOf(needle + ':') === 0) return true;
    }
    return false;
  }

  // ---------------------------------------------------------------
  // IndexedDB — one database per app, one object store keyed by path.
  // ---------------------------------------------------------------
  var DB_NAME = 'eos_export_' + APP_ID.replace(/[^a-z0-9_-]/gi, '_');
  var DB_STORE = 'kv';
  var _dbPromise = null;

  function openDB() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise(function (resolve, reject) {
      if (!window.indexedDB) return reject(new Error('IndexedDB unavailable'));
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () {
        req.result.createObjectStore(DB_STORE);
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
    return _dbPromise;
  }

  async function idbGet(key) {
    try {
      var db = await openDB();
      return await new Promise(function (resolve, reject) {
        var tx = db.transaction(DB_STORE, 'readonly');
        var req = tx.objectStore(DB_STORE).get(key);
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    } catch (e) { return undefined; }
  }

  async function idbSet(key, value) {
    try {
      var db = await openDB();
      return await new Promise(function (resolve, reject) {
        var tx = db.transaction(DB_STORE, 'readwrite');
        var req = tx.objectStore(DB_STORE).put(value, key);
        req.onsuccess = function () { resolve(true); };
        req.onerror = function () { reject(req.error); };
      });
    } catch (e) { return false; }
  }

  async function idbAll() {
    try {
      var db = await openDB();
      return await new Promise(function (resolve, reject) {
        var out = {};
        var tx = db.transaction(DB_STORE, 'readonly');
        var store = tx.objectStore(DB_STORE);
        var req = store.openCursor();
        req.onsuccess = function () {
          var cursor = req.result;
          if (!cursor) return resolve(out);
          out[cursor.key] = cursor.value; cursor.continue();
        };
        req.onerror = function () { reject(req.error); };
      });
    } catch (e) { return {}; }
  }

  // ---------------------------------------------------------------
  // Route matching & handler registry
  // ---------------------------------------------------------------
  var _routes = []; // {method, pattern: RegExp, keys: string[], handler}
  var _localBus = new EventTarget();
  var _readyResolvers = [];
  var _ready = false;

  function _whenReady() {
    return new Promise(function (resolve) {
      if (_ready) return resolve();
      _readyResolvers.push(resolve);
    });
  }

  function _compile(path) {
    // Convert /api/boards/:id/items to a regex + key list
    var keys = [];
    var re = path.replace(/:([A-Za-z_][A-Za-z0-9_]*)/g, function (_, k) {
      keys.push(k); return '([^/]+)';
    }).replace(/\*/g, '.*');
    return { re: new RegExp('^' + re + '$'), keys: keys };
  }

  function registerRoute(method, path, handler) {
    var c = _compile(path);
    _routes.push({ method: method.toUpperCase(), re: c.re, keys: c.keys, handler: handler });
  }

  function _matchRoute(method, path) {
    for (var i = 0; i < _routes.length; i++) {
      var r = _routes[i];
      if (r.method !== method.toUpperCase()) continue;
      var m = r.re.exec(path);
      if (!m) continue;
      var params = {};
      for (var j = 0; j < r.keys.length; j++) params[r.keys[j]] = decodeURIComponent(m[j + 1]);
      return { handler: r.handler, params: params };
    }
    return null;
  }

  function _resolveSnapshotKey(expr, params) {
    // Supports:
    //   "state.boards", "state.items[$id]", "state.items[$id].meta"   — single path
    //   {boards: "state.boards", presets: "state.presets"}             — composite response
    if (expr == null) return undefined;
    if (typeof expr === 'object') {
      var out = {};
      for (var k in expr) {
        if (Object.prototype.hasOwnProperty.call(expr, k)) {
          out[k] = _resolveSnapshotKey(expr[k], params);
        }
      }
      return out;
    }
    if (typeof expr !== 'string') return undefined;
    var data = window.EOS_EXPORT_DATA || {};
    var path = expr.replace(/^state\.?/, '');
    var cursor = data;
    var tokens = path.match(/[^.\[\]]+|\[\$[A-Za-z_][A-Za-z0-9_]*\]/g) || [];
    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i];
      if (t.indexOf('[$') === 0) {
        var key = t.slice(2, -1);
        cursor = cursor && cursor[params[key]];
      } else {
        cursor = cursor && cursor[t];
      }
      if (cursor === undefined) return undefined;
    }
    return cursor;
  }

  // ---------------------------------------------------------------
  // Static-asset rewriter — eos.js dynamically appends <link>/<script>
  // tags pointing at absolute "/static/..." paths. In export mode we
  // rewrite them to sibling "_assets/..." before the browser resolves them.
  // ---------------------------------------------------------------
  function _rewriteStaticHref(node) {
    if (!node || !node.tagName) return node;
    var tag = node.tagName.toUpperCase();
    if (tag === 'LINK' && node.href) {
      var href = node.getAttribute('href') || '';
      if (href.indexOf('/static/') !== -1) {
        node.setAttribute('href', '_assets/' + href.split('/static/').pop());
      }
    } else if (tag === 'SCRIPT' && node.src) {
      var src = node.getAttribute('src') || '';
      if (src.indexOf('/static/') !== -1) {
        node.setAttribute('src', '_assets/' + src.split('/static/').pop());
      }
    }
    return node;
  }
  var _origHeadAppend = document.head.appendChild.bind(document.head);
  document.head.appendChild = function (node) { return _origHeadAppend(_rewriteStaticHref(node)); };
  // Body may not exist yet when this runs inside <head>; patch lazily.
  function _patchBody() {
    if (!document.body) return setTimeout(_patchBody, 10);
    var _origBodyAppend = document.body.appendChild.bind(document.body);
    document.body.appendChild = function (node) { return _origBodyAppend(_rewriteStaticHref(node)); };
  }
  _patchBody();

  // ---------------------------------------------------------------
  // Fetch interceptor
  // ---------------------------------------------------------------
  var _origFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();

    // Pass through: absolute URLs (external APIs), page assets
    var isApiCall = (url.indexOf('/api/') !== -1) && !/^https?:\/\//.test(url);
    if (!isApiCall) return _origFetch(input, init);

    // Strip query string for matching
    var pathOnly = url.split('?')[0];

    // Parse body if JSON
    var body = null;
    try { if (init && init.body && typeof init.body === 'string') body = JSON.parse(init.body); } catch (_) {}
    var req = { method: method, url: url, path: pathOnly, body: body, headers: (init && init.headers) || {} };

    try {
      var matched = _matchRoute(method, pathOnly);
      if (matched) {
        var result = await matched.handler(req, matched.params);
        return _toResponse(result);
      }

      // GET: try snapshot routes table
      if (method === 'GET' && window.EOS_EXPORT_ROUTES) {
        for (var pattern in window.EOS_EXPORT_ROUTES) {
          if (!Object.prototype.hasOwnProperty.call(window.EOS_EXPORT_ROUTES, pattern)) continue;
          var parts = pattern.split(' ');
          if (parts.length !== 2) continue;
          if (parts[0].toUpperCase() !== 'GET') continue;
          var c = _compile(parts[1]);
          var m = c.re.exec(pathOnly);
          if (!m) continue;
          var params = {};
          for (var j = 0; j < c.keys.length; j++) params[c.keys[j]] = decodeURIComponent(m[j + 1]);
          var val = _resolveSnapshotKey(window.EOS_EXPORT_ROUTES[pattern], params);
          return _toResponse(val === undefined ? [] : val);
        }
      }

      // Generic IndexedDB fallback keyed by URL path
      if (method === 'GET') {
        var cached = await idbGet(pathOnly);
        if (cached !== undefined) return _toResponse(cached);
        return _toResponse([]); // empty array as a kinder default than {}
      }

      if (method === 'POST' || method === 'PATCH' || method === 'PUT') {
        if (hasFallback('vault:indexeddb') || hasFallback('vault:localstorage') || FALLBACKS.length === 0) {
          // Append/replace for collection endpoints; replace for item endpoints.
          var existing = (await idbGet(pathOnly)) || [];
          if (Array.isArray(existing)) {
            existing.push(body || {});
            await idbSet(pathOnly, existing);
          } else {
            await idbSet(pathOnly, body || {});
          }
          return _toResponse({ ok: true, offline: true });
        }
      }

      if (method === 'DELETE') {
        await idbSet(pathOnly, null);
        return _toResponse({ ok: true, offline: true });
      }

      return _toResponse({ ok: false, offline: true, reason: 'no handler' }, 501);
    } catch (e) {
      console.error('[eos-export] fetch handler error', e);
      return _toResponse({ error: String(e), offline: true }, 500);
    }
  };

  function _toResponse(data, status) {
    return new Response(
      typeof data === 'string' ? data : JSON.stringify(data === undefined ? null : data),
      { status: status || 200, headers: { 'Content-Type': 'application/json' } }
    );
  }

  // ---------------------------------------------------------------
  // EOS.* overrides — applied after eos.js loads.
  // ---------------------------------------------------------------
  function _overrideEOS() {
    if (!window.EOS) window.EOS = {};

    // Event subscription — in-page EventTarget (no WebSocket, no cross-tab)
    window.EOS.on = function (eventType, callback) {
      var handler = function (e) { try { callback(e.detail); } catch (err) { console.warn(err); } };
      _localBus.addEventListener(eventType, handler);
      return function () { _localBus.removeEventListener(eventType, handler); };
    };

    // Vault-viewer affordances — degrade to "copy path".
    var vaultDisabled = !hasFallback('viewer') && !hasFallback('viewer:none') ? true : true;
    window.EOS.openInViewer = function (path) {
      _copyToClipboard(path);
      _toast('Path copied — external viewer unavailable in export');
    };
    window.EOS.createInViewer = function (path, content) {
      _copyToClipboard(path);
      _toast('Path copied — cannot create in external viewer');
    };
    window.EOS.viewerLink = function (filePath, label) {
      var esc = window.esc || function (s) { return String(s); };
      var displayLabel = label || filePath.split('/').pop();
      return '<a href="#" onclick="EOS.openInViewer(\'' + esc(filePath) + '\');return false;" ' +
             'title="Copy path — viewer offline">' + esc(displayLabel) + ' 📋</a>';
    };
    window.EOS.noteActions = function (filePath, label) {
      var esc = window.esc || function (s) { return String(s); };
      return '<span class="eos-export-note-actions" title="Vault links disabled in export">' +
             '<button class="btn btn-ghost" onclick="EOS.openInViewer(\'' + esc(filePath) + '\')">📋 Copy path</button>' +
             '</span>';
    };

    // Geocoding / routing — pass through when fallback allows; otherwise disable.
    if (!hasFallback('geo')) {
      window.EOS.geocode = async function () { _toast('Geocode disabled in export'); return []; };
      window.EOS.reverseGeocode = async function () { return null; };
      window.EOS.getRoute = async function () { _toast('Routing disabled in export'); return null; };
    }

    // Cross-app calls go through the RPC registry (group-export mode) or
    // return {unavailable} (single-app export). Live-mode EOS.callApp proxies
    // through POST /api/apps/{app}/rpc/{method} — provided server-side.
    window.EOS.callApp = callApp;

    // Capability bridges — implemented as opt-in per fallback. Apps call
    // EOS.think / EOS.speak / EOS.listen / EOS.draw / EOS.animate the same
    // way they would server-side; these route to BYOK cloud, browser APIs,
    // or a polite refusal toast.
    window.EOS.think = _exportThink;
    window.EOS.speak = _exportSpeak;
    window.EOS.listen = _exportListen;
    window.EOS.draw = _exportDraw;
    window.EOS.animate = _exportAnimate;
    window.EOS.see = _exportSee;

    // Health probe — return a shaped object so pages that read EOS.vaultName don't crash.
    if (window.EOS.api) {
      var origApi = window.EOS.api.bind(window.EOS);
      window.EOS.api = async function (path, opts) {
        if (path === '/api/health' || path === 'api/health') {
          return { ok: true, offline: true, vault_name: 'Offline Export', vault_path: '' };
        }
        return origApi(path, opts);
      };
    }
  }

  // ---------------------------------------------------------------
  // Capability bridges
  // ---------------------------------------------------------------
  var CONSENT_KEY_OPENAI = 'eos_export_consent_openai_v1';

  function _hasOpenAIConsent() {
    return localStorage.getItem(CONSENT_KEY_OPENAI) === 'true';
  }

  function _askOpenAIConsent() {
    var msg =
      'This exported app wants to send a prompt to OpenAI using the API key ' +
      'stored in this browser.\n\n' +
      'The prompt and your key go directly to api.openai.com — they do not ' +
      'pass through any EmptyOS server.\n\n' +
      'Allow OpenAI calls from this bundle?';
    var ok = false;
    try { ok = window.confirm(msg); } catch (_) { ok = false; }
    if (ok) localStorage.setItem(CONSENT_KEY_OPENAI, 'true');
    return ok;
  }

  /**
   * EOS.think(prompt, opts) — text-out think bridge.
   *
   * In live mode apps call self.think(); pages call their own /api endpoint
   * which calls self.think(). In export mode, neither self nor the daemon
   * exist. EOS.think gives page code a same-shape async fn that returns
   * a string (or throws). Routes:
   *
   *   - if fallback "think:byok-openai" is declared AND localStorage has a
   *     key AND the per-domain consent token is set → POST chat-completions
   *   - otherwise → throw with a structured offline reason so callers can
   *     fall back to a static heuristic
   *
   * opts: { system?, model?, max_tokens?, temperature? }
   */
  async function _exportThink(prompt, opts) {
    opts = opts || {};
    var key = (window.EOS_EXPORT && window.EOS_EXPORT.openaiKey && window.EOS_EXPORT.openaiKey()) || '';
    if (!hasFallback('think:byok-openai')) {
      throw _offlineErr('think', 'no_fallback', 'No think fallback declared in this bundle');
    }
    if (!key) {
      _toast('Add an OpenAI key (🔒 pill) to enable AI here');
      throw _offlineErr('think', 'no_key', 'No OpenAI key in this browser');
    }
    if (!_hasOpenAIConsent() && !_askOpenAIConsent()) {
      throw _offlineErr('think', 'no_consent', 'User declined to send prompts to OpenAI');
    }
    var messages = [];
    if (opts.system) messages.push({ role: 'system', content: String(opts.system) });
    messages.push({ role: 'user', content: String(prompt || '') });
    var body = {
      model: opts.model || 'gpt-4o-mini',
      messages: messages,
      max_tokens: opts.max_tokens || 800,
      temperature: opts.temperature == null ? 0.5 : opts.temperature,
    };
    var res;
    try {
      // Bypass our own /api/* interceptor — _origFetch is the un-patched
      // browser fetch captured at boot.
      res = await _origFetch('https://api.openai.com/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + key,
        },
        body: JSON.stringify(body),
      });
    } catch (e) {
      throw _offlineErr('think', 'network', 'OpenAI request failed: ' + e);
    }
    if (!res.ok) {
      var detail = '';
      try { detail = (await res.json()).error.message || ''; } catch (_) {}
      throw _offlineErr('think', 'http_' + res.status, 'OpenAI returned ' + res.status + (detail ? ': ' + detail : ''));
    }
    var data;
    try { data = await res.json(); } catch (_) { throw _offlineErr('think', 'parse', 'OpenAI returned non-JSON'); }
    var text = '';
    try { text = data.choices[0].message.content || ''; } catch (_) {}
    return text;
  }

  /**
   * EOS.speak(text, opts) — Web Speech API fallback.
   *
   * No BYOK needed. Browsers ship at least one voice; quality varies by OS.
   * opts: { rate?, pitch?, volume?, lang?, voiceURI? }
   */
  async function _exportSpeak(text, opts) {
    if (!hasFallback('speak') && !hasFallback('speak:web-speech-api')) {
      _toast('Speech disabled in export');
      throw _offlineErr('speak', 'no_fallback', 'No speak fallback declared');
    }
    var synth = window.speechSynthesis;
    if (!synth || !window.SpeechSynthesisUtterance) {
      _toast('Speech synthesis not available in this browser');
      throw _offlineErr('speak', 'unsupported', 'speechSynthesis not supported');
    }
    opts = opts || {};
    var u = new window.SpeechSynthesisUtterance(String(text || ''));
    if (opts.rate != null) u.rate = opts.rate;
    if (opts.pitch != null) u.pitch = opts.pitch;
    if (opts.volume != null) u.volume = opts.volume;
    if (opts.lang) u.lang = opts.lang;
    if (opts.voiceURI) {
      var voices = synth.getVoices();
      var pick = voices.find(function (v) { return v.voiceURI === opts.voiceURI; });
      if (pick) u.voice = pick;
    }
    return new Promise(function (resolve, reject) {
      u.onend = function () { resolve({ ok: true, offline: true }); };
      u.onerror = function (e) { reject(_offlineErr('speak', 'synth_error', String(e.error || e))); };
      try { synth.speak(u); } catch (e) { reject(_offlineErr('speak', 'speak_throw', String(e))); }
    });
  }

  /**
   * EOS.listen(opts) — MediaRecorder + Web Speech recognition fallback.
   *
   * Returns { transcript } when SpeechRecognition is available; otherwise
   * throws. Browser support is patchy (Chrome only on desktop today).
   */
  async function _exportListen(opts) {
    if (!hasFallback('listen') && !hasFallback('listen:web-speech-api')) {
      _toast('Microphone listen disabled in export');
      throw _offlineErr('listen', 'no_fallback', 'No listen fallback declared');
    }
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      _toast('Speech recognition not available in this browser');
      throw _offlineErr('listen', 'unsupported', 'SpeechRecognition not supported');
    }
    opts = opts || {};
    var rec = new SR();
    rec.lang = opts.lang || 'en-US';
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    return new Promise(function (resolve, reject) {
      rec.onresult = function (e) {
        var t = '';
        try { t = e.results[0][0].transcript || ''; } catch (_) {}
        resolve({ transcript: t, offline: true });
      };
      rec.onerror = function (e) { reject(_offlineErr('listen', 'rec_error', String(e.error || e))); };
      rec.onend = function () { /* in case onresult never fired */ };
      try { rec.start(); } catch (e) { reject(_offlineErr('listen', 'start_throw', String(e))); }
    });
  }

  function _stubCapability(cap) {
    return async function () {
      _toast(cap + ' is not available in this export');
      throw _offlineErr(cap, 'no_fallback', cap + ' has no offline fallback');
    };
  }
  // Image/video gen needs GPU — no realistic browser-side fallback.
  var _exportDraw = _stubCapability('draw');
  var _exportAnimate = _stubCapability('animate');
  var _exportSee = _stubCapability('see');

  function _offlineErr(cap, reason, msg) {
    var e = new Error(msg);
    e.offline = true;
    e.capability = cap;
    e.reason = reason;
    return e;
  }

  // ---------------------------------------------------------------
  // UI: offline pill + settings panel
  // ---------------------------------------------------------------
  function _installPill() {
    if (document.getElementById('eos-export-pill')) return;
    var pill = document.createElement('button');
    pill.id = 'eos-export-pill';
    pill.textContent = '🔒 Offline export';
    pill.setAttribute('aria-label', 'Open offline export info');
    pill.style.cssText = [
      'position:fixed', 'top:10px', 'right:10px', 'z-index:99999',
      'background:#0b1020', 'color:#e8ebf7', 'border:1px solid #2a3355',
      'border-radius:999px', 'padding:6px 14px', 'font:500 12px/1.4 system-ui,sans-serif',
      'cursor:pointer', 'box-shadow:0 2px 12px rgba(0,0,0,.25)'
    ].join(';');
    pill.onclick = _openPanel;
    document.body.appendChild(pill);
  }

  function _openPanel() {
    var existing = document.getElementById('eos-export-panel');
    if (existing) { existing.remove(); return; }
    var panel = document.createElement('div');
    panel.id = 'eos-export-panel';
    panel.style.cssText = [
      'position:fixed', 'top:42px', 'right:10px', 'z-index:99999',
      'background:#0b1020', 'color:#e8ebf7', 'border:1px solid #2a3355',
      'border-radius:10px', 'padding:14px 16px', 'width:320px',
      'font:400 13px/1.5 system-ui,sans-serif', 'box-shadow:0 12px 32px rgba(0,0,0,.35)'
    ].join(';');
    var fbList = FALLBACKS.length
      ? FALLBACKS.map(function (f) { return '<li>' + f + '</li>'; }).join('')
      : '<li><em>(none declared)</em></li>';
    panel.innerHTML =
      '<div style="font-weight:600;margin-bottom:6px;">🔒 ' + (APP_ID) + ' — offline</div>' +
      '<div style="color:#a8b2d8;margin-bottom:10px;">This is a standalone export. Data is stored in your browser (IndexedDB). Some features are disabled.</div>' +
      _capabilitiesHtml() +
      '<div style="font-weight:600;margin-bottom:4px;">Declared fallbacks</div>' +
      '<ul style="margin:0 0 10px 18px;padding:0;color:#c0c8e8;">' + fbList + '</ul>' +
      (hasFallback('think:byok-openai') ? _byokHtml() : '') +
      '<button id="eos-export-close" style="margin-top:8px;background:#2a3355;color:#e8ebf7;border:0;border-radius:6px;padding:6px 10px;cursor:pointer;">Close</button>';
    document.body.appendChild(panel);
    document.getElementById('eos-export-close').onclick = function () { panel.remove(); };
    var save = document.getElementById('eos-export-byok-save');
    if (save) save.onclick = _saveByok;
  }

  function _capabilitiesHtml() {
    var caps = window.EOS_EXPORT_CAPABILITIES || {};
    var keys = Object.keys(caps);
    if (!keys.length) return '';
    var DOT = {
      available: '#8de28d',
      byok: '#f0c060',
      disabled: '#9090a0',
      'auto-rpc-only': '#9090a0',
      'single-app': '#9090a0',
      unavailable: '#e07070',
    };
    var rows = keys.map(function (k) {
      var c = caps[k] || {};
      var color = DOT[c.status] || '#9090a0';
      var label = c.strategy || c.note || c.reason || c.status || '';
      return (
        '<li style="display:flex;align-items:center;gap:6px;margin:0;padding:1px 0;">' +
          '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:' + color + ';"></span>' +
          '<span style="flex:0 0 80px;color:#c0c8e8;font-family:ui-monospace,monospace;">' + k + '</span>' +
          '<span style="color:#8090b0;font-size:11px;">' + label + '</span>' +
        '</li>'
      );
    }).join('');
    return (
      '<div style="font-weight:600;margin-bottom:4px;">What works here</div>' +
      '<ul style="margin:0 0 10px 0;padding:0;list-style:none;">' + rows + '</ul>'
    );
  }

  function _byokHtml() {
    var current = localStorage.getItem('eos_export_openai_key') || '';
    var masked = current ? current.slice(0, 3) + '…' + current.slice(-4) : '';
    return '<div style="font-weight:600;margin-bottom:4px;">OpenAI API key (BYOK)</div>' +
      '<div style="color:#a8b2d8;margin-bottom:6px;">Enables AI features. Stored only in this browser.</div>' +
      (current ? '<div style="color:#8de28d;margin-bottom:4px;">Current: ' + masked + '</div>' : '') +
      '<input id="eos-export-byok-input" type="password" placeholder="sk-..." style="width:100%;box-sizing:border-box;background:#1a2040;border:1px solid #2a3355;color:#e8ebf7;border-radius:6px;padding:6px;"/>' +
      '<button id="eos-export-byok-save" style="margin-top:6px;background:#3a6;color:#fff;border:0;border-radius:6px;padding:6px 10px;cursor:pointer;">Save</button>';
  }

  function _saveByok() {
    var el = document.getElementById('eos-export-byok-input');
    if (!el) return;
    var val = el.value.trim();
    if (val) {
      localStorage.setItem('eos_export_openai_key', val);
      _toast('Saved');
    }
    var panel = document.getElementById('eos-export-panel');
    if (panel) panel.remove();
  }

  function _toast(msg) {
    if (window.EOS_UI && window.EOS_UI.toast) return window.EOS_UI.toast(msg, true);
    console.log('[eos-export]', msg);
  }

  function _copyToClipboard(text) {
    try { navigator.clipboard.writeText(text); }
    catch (_) {
      var ta = document.createElement('textarea'); ta.value = text;
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (__) {}
      ta.remove();
    }
  }

  // ---------------------------------------------------------------
  // Cross-app RPC registry (group-export mode)
  //
  // In a group bundle, apps call each other via EOS.callApp(target, method,
  // kwargs) instead of fetch('/target/api/...'). Each app's export hook
  // registers its public methods here. Unbundled targets degrade to
  // {offline:true, unavailable:true}.
  // ---------------------------------------------------------------
  var _appMethods = {};  // "appId:method" → async function(kwargs) -> any

  function registerAppMethod(appId, method, handler) {
    _appMethods[appId + ':' + method] = handler;
  }

  async function callApp(appId, method, kwargs) {
    var bundled = (window.EOS_BUNDLED_APPS || []);
    if (bundled.length && bundled.indexOf(appId) === -1) {
      return { offline: true, unavailable: true, reason: 'not_bundled', app: appId, method: method };
    }
    var fn = _appMethods[appId + ':' + method];
    if (!fn) {
      return { offline: true, unavailable: true, reason: 'no_handler', app: appId, method: method };
    }
    try {
      return await fn(kwargs || {});
    } catch (e) {
      console.error('[eos-export] call_app error', e);
      return { offline: true, error: String(e), app: appId, method: method };
    }
  }

  // ---------------------------------------------------------------
  // Collection helper — share the load/save/findById/set-field boilerplate
  // that every "list of records keyed by id" export hook needs. Apps with
  // a stable id and a SETTABLE_FIELDS whitelist call this once; the helper
  // wires the cross-app `list_all` + `set_field` handlers and returns
  // primitives the hook composes its remaining domain-specific handlers from.
  //
  // Apps whose data isn't a flat keyed list (captures, journals, etc.)
  // shouldn't use this — they hand-roll their own routes.
  // ---------------------------------------------------------------
  /**
   * Register a collection-shaped app data store.
   *
   * opts:
   *   appId          — e.g. "task" / "projects" / "people"
   *   dataKey        — primary IDB key + canonical GET path, e.g. "/task/api/tasks"
   *   mirrorKeys     — additional IDB keys to keep in sync (GET aliases). Optional.
   *   snapshotPath   — function(state) -> rows. Default: state[appId][lastSegmentOf(dataKey)] || state[lastSegmentOf(dataKey)] || []
   *   idField        — primary key on rows. Default "id".
   *   settableFields — array of field names allowed via cross-app set_field.
   *   eventPrefix    — emits "<eventPrefix>:updated" on field change. Default appId.
   *   onSetField     — optional async (row, field, value) -> void. Default: row[field] = value.
   *
   * Returns: { load(), save(rows), findById(rows, id), setField(id, field, value) }.
   */
  function registerCollection(opts) {
    var appId = opts.appId;
    var dataKey = opts.dataKey;
    var mirrorKeys = opts.mirrorKeys || [];
    var idField = opts.idField || 'id';
    var settable = {};
    (opts.settableFields || []).forEach(function (f) { settable[f] = 1; });
    var eventPrefix = opts.eventPrefix || appId;
    var onSetField = opts.onSetField || function (row, field, value) { row[field] = value; };
    var lastSeg = dataKey.split('/').pop();
    var snapshotPath = opts.snapshotPath || function (state) {
      var nested = state && state[appId];
      if (nested) {
        if (Array.isArray(nested[lastSeg])) return nested[lastSeg];
        // Common shapes: state[app][app] (e.g. state.people.people),
        // state[app][app+'s'] (e.g. state.task.tasks).
        if (Array.isArray(nested[appId])) return nested[appId];
        if (Array.isArray(nested[appId + 's'])) return nested[appId + 's'];
      }
      if (Array.isArray(state[lastSeg])) return state[lastSeg];
      return [];
    };

    async function load() {
      var stored = await idbGet(dataKey);
      if (Array.isArray(stored)) return stored;
      var rows = snapshotPath(window.EOS_EXPORT_DATA || {}) || [];
      await idbSet(dataKey, rows);
      for (var i = 0; i < mirrorKeys.length; i++) await idbSet(mirrorKeys[i], rows);
      return rows;
    }
    async function save(rows) {
      await idbSet(dataKey, rows);
      for (var i = 0; i < mirrorKeys.length; i++) await idbSet(mirrorKeys[i], rows);
    }
    function findById(rows, id) {
      for (var i = 0; i < rows.length; i++) if (rows[i][idField] === id) return i;
      return -1;
    }
    async function setField(id, field, value) {
      if (!settable[field]) {
        return { error: "field '" + field + "' not settable", settable: Object.keys(settable) };
      }
      var rows = await load();
      var i = findById(rows, id);
      if (i < 0) return { error: appId + ' not found', id: id };
      await onSetField(rows[i], field, value);
      await save(rows);
      _localBus.dispatchEvent(new CustomEvent(eventPrefix + ':updated', {
        detail: { id: id, field: field, value: value },
      }));
      return { ok: true, id: id, field: field, value: value };
    }

    // Auto-register cross-app methods. Hooks can re-register if they need
    // domain-specific behaviour (e.g. task.add).
    registerAppMethod(appId, 'list_all', async function () { return await load(); });
    registerAppMethod(appId, 'set_field', async function (kwargs) {
      kwargs = kwargs || {};
      return await setField(kwargs.id || '', kwargs.field || '', kwargs.value);
    });

    return { load: load, save: save, findById: findById, setField: setField };
  }

  // ---------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------
  window.EOS_EXPORT = {
    appId: APP_ID,
    prefix: APP_PREFIX,
    fallbacks: FALLBACKS,
    hasFallback: hasFallback,
    registerRoute: registerRoute,
    registerAppMethod: registerAppMethod,
    registerCollection: registerCollection,
    callApp: callApp,
    get: idbGet,
    set: idbSet,
    all: idbAll,
    openaiKey: function () { return localStorage.getItem('eos_export_openai_key') || ''; },
    emit: function (type, data) { _localBus.dispatchEvent(new CustomEvent(type, { detail: data })); },
    whenReady: _whenReady,
    _ready: function () {
      _ready = true;
      _readyResolvers.forEach(function (r) { r(); });
      _readyResolvers = [];
    }
  };

  // ---------------------------------------------------------------
  // Bootstrap: install pill + patch EOS after the DOM is ready.
  // ---------------------------------------------------------------
  function _boot() {
    _installPill();
    _overrideEOS();
    // Re-apply overrides if eos.js loads later via async script.
    if (!window.EOS || !window.EOS.on || window.EOS.on.toString().indexOf('_localBus') === -1) {
      setTimeout(_overrideEOS, 50);
    }
    // Snapshot is inlined into the HTML by the exporter, so we can signal ready
    // immediately without awaiting a fetch.
    if (window.EOS_EXPORT && window.EOS_EXPORT._ready) window.EOS_EXPORT._ready();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot);
  } else {
    _boot();
  }
})();
