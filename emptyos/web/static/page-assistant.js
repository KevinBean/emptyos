/**
 * EmptyOS Page Assistant — AI sidebar that can ACT on every page.
 *
 * Pages register actions via: EOS.registerActions({ name: fn, ... })
 * AI can output [ACTION:name(param)] to execute them.
 * AI can output [BUTTON:label|action(param)] to generate clickable buttons.
 *
 * Auto-loaded by eos.js.
 */
(function() {
  'use strict';

  var appId = '';
  var isOpen = false;
  var isStreaming = false;
  var registeredActions = {};
  var actionDescriptions = [];
  var pageDescription = '';
  var pageQuickActions = null;
  var pageGpt = 'general-assistant';  // Default GPT; pages override via gpt: "finance-advisor"

  // ── Companion mode (the one-brain rail) ──────────────────────────────────
  // When the voice-assistant `feature.companion-frame.enabled` flag is on, the
  // rail becomes the TEXT face of the same brain Aura speaks through: turns go
  // to /voice-assistant/api/companion_turn (turn_events text_only=True) instead
  // of rooms, gaining verb-registry dispatch, cards, pending Apply/Reject, and
  // the ⚡ session auto-accept grant. Off ⇒ everything below is inert and the
  // rail behaves exactly as before (routes to rooms).
  var companionMode = false;
  var companionMessages = [];       // [{role, content}] threaded through the brain
  var companionId = null;           // active persona (companion switch)
  var companionStream = false;      // feature.companion-stream.enabled — token-by-token rail
  var companionForms = false;       // feature.companion-forms.enabled — form-card primitive
  var companionProactive = false;   // feature.companion-proactive.enabled — system-initiated nudges
  var companionActions = false;      // feature.companion-actions.enabled — offer this page's actions as [BUTTON:] controls
  var autoAccept = false;           // ⚡ session grant state
  var isPinned = false;             // docked rail vs overlay
  var focusMode = false;            // 📍 focus on this page's app vs 🌐 universal
  var _forcePageContext = false;    // one-shot: force page grounding for the next companion turn
                                    // (set when a page-oriented quick button is clicked, e.g. "📋 This page"),
                                    // consumed by _companionPageContextStr() so free-form turns stay ungrounded
  var _mediaRec = null;             // in-rail voice MediaRecorder
  var _recChunks = [];              // recorded audio chunks
  var _recording = false;
  var _voiceAudio = null;           // <audio> element for reply playback
  var _audioQueue = [];             // queued TTS reply URLs
  var _audioPlaying = false;
  var COMPANION_HIST_KEY = 'eos.companion.history';
  var COMPANION_PIN_KEY = 'eos.companion.pinned';
  var COMPANION_FOCUS_KEY = 'eos.companion.focus';
  var COMPANION_WIDTH = 380;        // keep in sync with .pa-drawer width
  var PIN_MIN_VW = 980;             // below this, pinning collapses to overlay

  // Public API: pages register their actions + context
  window.EOS = window.EOS || {};
  EOS.registerActions = function(actions, descriptions, config) {
    Object.assign(registeredActions, actions);
    if (descriptions) actionDescriptions = actionDescriptions.concat(descriptions);
    if (config) {
      if (config.description) pageDescription = config.description;
      if (config.quickActions) pageQuickActions = config.quickActions;
      if (config.gpt) pageGpt = config.gpt;
    }
  };
  // Drain any registrations the eos.js stub queued before this module loaded —
  // page-assistant.js is async-injected AFTER pages' inline registerActions run,
  // so without this replay their page actions would be permanently lost.
  if (window.EOS && EOS._pendingActions && EOS._pendingActions.length) {
    EOS._pendingActions.splice(0).forEach(function(a) { EOS.registerActions(a[0], a[1], a[2]); });
  }

  // Skip on pages whose primary purpose IS chat/assistant — the page already
  // has its own chat UI, so the floating capture+assistant FABs collide with
  // the input bar and add nothing.
  if (/^\/(assistant|agent|voice-assistant)(\/|$)/.test(location.pathname)) return;
  // Skip in embedded panes (?embed=1 — iframed by a host shell like portal):
  // the host page carries its own companion rail; a second FAB inside the
  // frame is doubled chrome. Mirrors eos.js's nav guard.
  try {
    if (new URLSearchParams(location.search).get('embed') === '1') return;
  } catch (e) {}

  // Wait for EOS.nav to set the current app
  function waitForApp() {
    if (window.EOS && EOS._currentApp !== undefined) {
      appId = EOS._currentApp || 'hub';
      // Default navigation actions available on all pages
      registeredActions.navigate = function(url) { location.href = url; };
      registeredActions.scroll_to = function(sel) { var el = document.querySelector(sel); if (el) el.scrollIntoView({behavior:'smooth'}); };
      registeredActions.refresh = function() { location.reload(); };
      registeredActions.open_app = function(id) { location.href = '/' + id + '/'; };
      actionDescriptions.push(
        { name: 'navigate', description: 'Go to a URL', params: ['url'] },
        { name: 'scroll_to', description: 'Scroll to a CSS selector on the page', params: ['selector'] },
        { name: 'refresh', description: 'Refresh the current page', params: [] },
        { name: 'open_app', description: 'Open an app by ID', params: ['app_id'] }
      );
      init();
    } else {
      setTimeout(waitForApp, 100);
    }
  }
  waitForApp();

  function init() {
    var style = document.createElement('style');
    style.textContent = [
      '.pa-drawer{position:fixed;top:0;right:0;bottom:0;width:min(380px,90vw);background:var(--bg);border-left:1px solid var(--border);box-shadow:-8px 0 24px rgba(0,0,0,0.15);z-index:9992;transform:translateX(100%);transition:transform 0.25s ease;display:flex;flex-direction:column;padding-bottom:env(safe-area-inset-bottom,0px)}',
      '.pa-drawer.open{transform:translateX(0)}',
      '.pa-header{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;flex-shrink:0;padding-top:calc(14px + env(safe-area-inset-top,0px))}',
      '.pa-title{font-size:15px;font-weight:600;color:var(--text-heading);flex:1}',
      '.pa-close{font-size:20px;color:var(--text-muted);cursor:pointer;padding:4px 8px;border-radius:6px}.pa-close:hover{color:var(--text);background:color-mix(in srgb,var(--text) 5%,transparent)}',
      '.pa-messages{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:10px}',
      '.pa-msg{max-width:88%;padding:10px 14px;border-radius:14px;font-size:13px;line-height:1.5;word-wrap:break-word}',
      '.pa-msg.user{align-self:flex-end;background:var(--accent);color: var(--accent-ink, #fff);border-bottom-right-radius:4px}',
      '.pa-msg.assistant{align-self:flex-start;background:var(--bg-card);border:1px solid var(--border);color:var(--text);border-bottom-left-radius:4px}',
      '.pa-msg.assistant strong{color:var(--text-heading)}.pa-msg.assistant code{background:color-mix(in srgb,var(--accent) 12%,transparent);padding:1px 4px;border-radius:3px;font-size:12px}',
      '.pa-action-btn{display:inline-block;margin:4px 4px 4px 0;padding:6px 14px;border-radius:8px;border:1px solid var(--accent);background:color-mix(in srgb,var(--accent) 10%,transparent);color:var(--accent);font-size:12px;font-weight:600;cursor:pointer;transition:all 0.15s}',
      '.pa-action-btn:hover{background:var(--accent);color: var(--accent-ink, #fff)}',
      '.pa-action-done{opacity:0.5;pointer-events:none;border-style:dashed}',
      '.pa-action-result{font-size:11px;color:var(--success);margin-top:4px}',
      '.pa-input-row{padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:8px;flex-shrink:0;padding-bottom:calc(12px + env(safe-area-inset-bottom,0px))}',
      '.pa-input{flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:10px;background:var(--bg-card);color:var(--text);font-size:14px;outline:none;font-family:inherit;resize:none}',
      '.pa-input:focus{border-color:var(--accent)}',
      '.pa-send{padding:10px 16px;border-radius:10px;background:var(--accent);color:var(--accent-ink,#fff);border:none;font-size:14px;font-weight:600;cursor:pointer;flex-shrink:0}.pa-send:disabled{opacity:0.4}',
      '.pa-quick{display:flex;flex-wrap:wrap;gap:6px;padding:8px 16px;flex-shrink:0}',
      '.pa-quick-btn{padding:6px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text-secondary);font-size:11px;cursor:pointer;white-space:nowrap;flex-shrink:0;transition:all 0.15s}.pa-quick-btn:hover{border-color:var(--accent);color:var(--accent)}',
      '@media (max-width:640px),(pointer:coarse){.pa-quick-btn{min-height:36px;padding:8px 14px;font-size:12px}}',
      '.pa-quick-btn.capture{border-color:color-mix(in srgb,var(--accent) 30%,var(--border));background:color-mix(in srgb,var(--accent) 5%,var(--bg-card));max-width:180px;overflow:hidden;text-overflow:ellipsis}',
      '.pa-quick-sep{width:100%;height:0;flex-basis:100%}',
      '.pa-spinner{display:inline-block;width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:pa-spin 0.6s linear infinite}',
      '@keyframes pa-spin{to{transform:rotate(360deg)}}',
      '.pa-welcome{text-align:center;padding:20px;color:var(--text-muted);font-size:13px;line-height:1.6}',
      /* Chrome (padding, radius, shadow, hover) comes from .eos-fab-pill;
         only positioning + open-state modifiers live here. Position rules are
         inert when the FAB is docked inside #eos-fab-others (see eos.js). */
      '.pa-fab { position:fixed; bottom:calc(env(safe-area-inset-bottom,0px) + 90px); right:calc(env(safe-area-inset-right,0px) + 16px); z-index:9990; -webkit-tap-highlight-color:transparent; }',
      '.pa-fab.open { opacity:0; pointer-events:none; }',
      '.cap-fab { position:fixed; bottom:calc(env(safe-area-inset-bottom,0px) + 144px); right:calc(env(safe-area-inset-right,0px) + 16px); z-index:9990; }',
      '.cap-modal{position:fixed;bottom:calc(env(safe-area-inset-bottom,0px) + 190px);right:calc(env(safe-area-inset-right,0px) + 16px);width:min(320px,calc(100vw - 32px - env(safe-area-inset-left,0px) - env(safe-area-inset-right,0px)));background:var(--bg);border:1px solid var(--border);border-radius:14px;box-shadow:0 8px 32px rgba(0,0,0,0.2);z-index:9993;padding:14px;display:none}',
      '.cap-modal.show{display:block;animation:cardIn 0.2s ease}',
      '.cap-input{width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:10px;background:var(--bg-card);color:var(--text);font-size:14px;outline:none;font-family:inherit;resize:none;min-height:60px}',
      '.cap-input:focus{border-color:var(--accent)}',
      '.cap-tags{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}',
      '.cap-tag{padding:4px 10px;border-radius:6px;border:1px solid var(--border);background:var(--bg-card);color:var(--text-secondary);font-size:11px;cursor:pointer;transition:all 0.15s}',
      '.cap-tag:hover,.cap-tag.active{border-color:var(--accent);color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,var(--bg-card))}',
      '.cap-send{margin-top:10px;width:100%;padding:10px;border-radius:10px;background:var(--accent);color: var(--accent-ink, #fff);border:none;font-size:14px;font-weight:600;cursor:pointer}.cap-send:disabled{opacity:0.4}',
      '@media(max-width:500px){.pa-drawer{width:100vw}}',
      /* Pinned (docked rail): keep the drawer open and reflow page content by
         reserving a right gutter on the body. Only applied ≥980px (JS guards). */
      'body.eos-companion-pinned{margin-right:var(--eos-companion-gutter,0px)!important;transition:margin-right .2s ease}',
      'body.eos-companion-pinned .pa-drawer{box-shadow:none;border-left:1px solid var(--border)}',
      'body.eos-companion-pinned .pa-fab,body.eos-companion-pinned .cap-fab{display:none!important}',
      /* Header control buttons (pin / auto-accept / mic hand-off) */
      '.pa-hbtn{font-size:14px;color:var(--text-muted);cursor:pointer;padding:3px 7px;border-radius:7px;border:1px solid transparent;line-height:1;background:none;user-select:none}',
      '.pa-hbtn:hover{color:var(--text);background:color-mix(in srgb,var(--text) 6%,transparent)}',
      '.pa-hbtn.on{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 35%,transparent);background:color-mix(in srgb,var(--accent) 10%,transparent)}',
      /* Scope chip — wider, carries a text label (🌐 Universal / 📍 app) */
      '.pa-scope{font-size:11px;font-weight:600;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      /* Mic recording state — pulsing red dot */
      '.pa-hbtn.recording{color:#f87171;border-color:color-mix(in srgb,#f87171 45%,transparent);background:color-mix(in srgb,#f87171 12%,transparent);animation:pa-rec-pulse 1s ease-in-out infinite}',
      '@keyframes pa-rec-pulse{0%,100%{opacity:1}50%{opacity:.5}}',
      /* Pending Apply/Reject card in companion mode */
      '.pa-pending{margin-top:8px;padding:10px 12px;border:1px solid color-mix(in srgb,var(--accent) 30%,var(--border));border-radius:10px;background:color-mix(in srgb,var(--accent) 5%,var(--bg-card))}',
      '.pa-pending-desc{font-size:12px;color:var(--text);margin-bottom:8px}',
      '.pa-pending-verb{font-size:10px;color:var(--text-muted);font-family:ui-monospace,monospace;margin-bottom:6px}',
      '.pa-pending-row{display:flex;gap:6px}',
      '.pa-pending.done{opacity:.6}.pa-pending.done .pa-pending-row{display:none}',
      '.pa-apply{flex:1;padding:6px 12px;border-radius:8px;border:none;background:var(--accent);color: var(--accent-ink, #fff);font-size:12px;font-weight:600;cursor:pointer}',
      '.pa-reject{flex:1;padding:6px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text-secondary);font-size:12px;cursor:pointer}',
      '.pa-apply:disabled,.pa-reject:disabled{opacity:.5;pointer-events:none}',
      /* Confirm gate card (reuses the pending frame; Run/Skip) */
      '.pa-confirm{margin-top:8px;padding:10px 12px;border:1px solid color-mix(in srgb,var(--warning,#f59e0b) 40%,var(--border));border-radius:10px;background:color-mix(in srgb,var(--warning,#f59e0b) 6%,var(--bg-card))}',
      '.pa-confirm-verb{font-size:10px;color:var(--text-muted);font-family:ui-monospace,monospace;margin-bottom:6px}',
      '.pa-confirm-desc{font-size:12px;color:var(--text);margin-bottom:4px}',
      '.pa-confirm-args{font-size:11px;color:var(--text-secondary);margin-bottom:8px;word-break:break-word}',
      '.pa-confirm-row{display:flex;gap:6px}.pa-confirm.done .pa-confirm-row{display:none}.pa-confirm.done{opacity:.7}',
      '.pa-confirm-say{margin-top:6px;font-size:12px;color:var(--text)}',
      /* Form card — companion asks for structured fields (propose-not-autofill) */
      '.pa-form{display:flex;flex-direction:column;gap:8px}',
      '.pa-form-row{display:flex;flex-direction:column;gap:3px}',
      '.pa-form-label{font-size:11px;color:var(--text-secondary)}',
      '.pa-form-input{padding:6px 8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:13px;width:100%;box-sizing:border-box}',
      '.pa-form-submit{margin-top:2px;padding:6px 12px;border-radius:8px;border:none;background:var(--accent);color: var(--accent-ink, #fff);font-size:12px;font-weight:600;cursor:pointer}',
      '.pa-form-submit:disabled{opacity:.5;pointer-events:none}.pa-form.done{opacity:.7}',
      /* Proactive nudge bubble (system-initiated) + FAB unread badge */
      '.pa-msg.nudge{align-self:flex-start;background:color-mix(in srgb,var(--accent) 8%,var(--bg-card));border:1px solid color-mix(in srgb,var(--accent) 30%,var(--border))}',
      '.pa-nudge-src{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--accent);margin-bottom:4px}',
      '.pa-fab-badge{position:absolute;top:-4px;right:-4px;min-width:16px;height:16px;padding:0 4px;border-radius:8px;background:var(--danger,#f87171);color: var(--ink-on-vivid);font-size:10px;font-weight:700;line-height:16px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.3)}',
      /* Companion cards (sidebar-styled; data shapes shared with Aura) */
      '.pa-card{margin-top:8px;border:1px solid var(--border);border-radius:10px;background:var(--bg-card);overflow:hidden}',
      '.pa-card-title{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);padding:8px 12px 0}',
      '.pa-card-body{padding:8px 12px}',
      '.pa-stat{display:flex;justify-content:space-between;gap:10px;padding:3px 0;font-size:12px}',
      '.pa-stat .k{color:var(--text-secondary)}.pa-stat .v{color:var(--text-heading);font-weight:600}',
      '.pa-ent-sub{font-size:11px;color:var(--text-muted);margin:2px 0 6px}',
      '.pa-row{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;border-top:1px solid color-mix(in srgb,var(--border) 60%,transparent)}',
      '.pa-row:first-child{border-top:none}',
      '.pa-row .dot{width:7px;height:7px;border-radius:50%;background:var(--text-muted);flex-shrink:0}',
      '.pa-row .dot.overdue{background:#f87171}.pa-row .dot.today{background:var(--accent)}.pa-row .dot.done{background:var(--success,#34d399)}',
      '.pa-row .tg{font-size:10px;color:var(--text-muted);margin-left:auto;flex-shrink:0}',
      '.pa-clink{display:inline-block;margin-top:6px;font-size:12px;color:var(--accent);text-decoration:none}',
    ].join('\n');
    document.head.appendChild(style);

    // --- Capture FAB + Modal ---
    var capFab = document.createElement('button');
    capFab.className = 'eos-fab-pill cap-fab';
    capFab.innerHTML = '<span class="eos-fab-pill-icon">⚡</span><span class="eos-fab-pill-label">capture</span>';
    capFab.title = 'Quick Capture (Ctrl+Shift+C)';
    capFab.onclick = _capToggle;
    var dockTarget = document.getElementById('eos-fab-others') || document.body;
    dockTarget.appendChild(capFab);

    var capModal = document.createElement('div');
    capModal.className = 'cap-modal';
    capModal.id = '__cap_modal';
    capModal.innerHTML = [
      '<textarea class="cap-input" id="__cap_input" placeholder="Capture a thought..." rows="2"></textarea>',
      '<div class="cap-tags" id="__cap_tags">',
      ['idea','task','dev','bug','note'].map(function(t) {
        return '<span class="cap-tag" data-tag="' + t + '" onclick="_capTag(this)">' + t + '</span>';
      }).join(''),
      '</div>',
      '<button class="cap-send" id="__cap_send" onclick="_capSubmit()">Capture</button>',
    ].join('');
    document.body.appendChild(capModal);

    document.getElementById('__cap_input').addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); _capSubmit(); }
    });

    // --- AI FAB ---
    var fab = document.createElement('button');
    fab.className = 'eos-fab-pill pa-fab';
    fab.innerHTML = '<span class="eos-fab-pill-icon">🤖</span><span class="eos-fab-pill-label">assistant</span>';
    fab.title = 'AI Assistant (Ctrl+Shift+A)';
    fab.onclick = toggle;
    var dockTarget = document.getElementById('eos-fab-others') || document.body;
    dockTarget.appendChild(fab);

    // --- Drawer ---
    var drawer = document.createElement('div');
    drawer.className = 'pa-drawer';
    // Starts closed (isOpen=false): slid off-screen via transform. Mark inert so
    // its 12 controls (input, Send, mic, pin, quick buttons) stay out of the tab
    // order + a11y tree while hidden — otherwise a keyboard user tabs into an
    // invisible off-screen drawer. toggle() keeps this in sync with .open.
    drawer.inert = true;
    drawer.innerHTML = [
      '<div class="pa-header">',
      '  <span class="pa-title">AI Assistant</span>',
      '  <span style="font-size:11px;color:var(--text-muted);background:var(--bg-card);padding:2px 8px;border-radius:6px">' + _esc(appId) + '</span>',
      '  <button class="pa-hbtn pa-scope" id="__pa_scope" title="Companion scope" style="display:none"></button>',
      '  <button class="pa-hbtn" id="__pa_auto" title="Auto-accept this session" style="display:none">&#9889;</button>',
      '  <button class="pa-hbtn" id="__pa_mic" title="Speak (record here)" style="display:none">&#127908;</button>',
      '  <button class="pa-hbtn" id="__pa_expand" title="Open full voice (Aura)" style="display:none">&#11014;</button>',
      '  <button class="pa-hbtn" id="__pa_pin" title="Pin as a docked rail" style="display:none">&#128204;</button>',
      '  <span class="pa-close" id="__pa_close">&times;</span>',
      '</div>',
      '<div class="pa-messages" id="__pa_msgs">',
      '  <div class="pa-welcome">Ask me anything, or I can help with actions on this page.</div>',
      '</div>',
      '<div class="pa-quick" id="__pa_quick"></div>',
      '<div class="pa-input-row">',
      '  <input class="pa-input" id="__pa_input" placeholder="Ask or command..." autocomplete="off">',
      '  <button class="pa-send" id="__pa_send">Send</button>',
      '</div>',
    ].join('\n');
    document.body.appendChild(drawer);

    document.getElementById('__pa_close').onclick = toggle;
    document.getElementById('__pa_send').onclick = function() { send(); };
    document.getElementById('__pa_input').addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });

    // Quick actions — page-specific + defaults + recent captures
    var quickEl = document.getElementById('__pa_quick');
    quickEl.addEventListener('click', function(e) {
      var btn = e.target.closest('.pa-quick-btn');
      if (btn && btn.dataset.msg) {
        // A page-oriented quick button ("This page" / "What now?" / a registered
        // page action) is an explicit request to ground in THIS page — force page
        // context for this one turn even when the 📍 focus toggle is off. Capture
        // buttons (💡) are about the capture, not the page, so they don't.
        if (!btn.classList.contains('capture')) _forcePageContext = true;
        send(btn.dataset.msg);
      }
    });
    _renderQuickButtons(quickEl);

    // Keyboard shortcuts
    document.addEventListener('keydown', function(e) {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'A') { e.preventDefault(); toggle(); }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'C') {
        // Only if not in a text field
        if (!e.target.matches('input,textarea,[contenteditable]')) { e.preventDefault(); _capToggle(); }
      }
      if (e.key === 'Escape') {
        var modal = document.getElementById('__cap_modal');
        if (modal && modal.classList.contains('show')) { _capToggle(); e.preventDefault(); }
      }
    });

    _initCompanion();
  }

  // ═══ Companion mode — feature-detect + wire the rail to the one brain ═══
  function _initCompanion() {
    if (!window.EOS || !EOS.api) return;
    EOS.api('/voice-assistant/api/companion/status').then(function(s) {
      if (!s || !s.enabled) return;   // flag dark → rail stays on rooms
      companionMode = true;
      companionStream = !!s.stream;   // sub-features light up per /api/companion/status
      companionForms = !!s.forms;
      companionProactive = !!s.proactive;
      companionActions = !!s.actions;
      var title = document.querySelector('.pa-title');
      if (title) title.textContent = 'Companion';
      ['__pa_scope', '__pa_auto', '__pa_mic', '__pa_expand', '__pa_pin'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.style.display = '';
      });
      document.getElementById('__pa_pin').onclick = _togglePin;
      document.getElementById('__pa_auto').onclick = _toggleAutoAccept;
      // Voice happens IN the rail now — record/play in place, no full-screen jump.
      document.getElementById('__pa_mic').onclick = _companionMicToggle;
      // ⤴ Expand keeps full-screen Aura reachable for hands-free / immersive.
      document.getElementById('__pa_expand').onclick = function() { location.href = '/voice-assistant/'; };
      // Scope chip — 🌐 universal vs 📍 this page's app.
      focusMode = localStorage.getItem(COMPANION_FOCUS_KEY) === '1';
      document.getElementById('__pa_scope').onclick = _toggleFocus;
      _paintScope();
      // The rail is the single companion location — expose a small API so the
      // OTHER voice entry points (topbar ✨, FAB 🎙 speed-dial) open the SIDEBAR
      // in voice mode instead of jumping to full-screen. Full-screen stays
      // reachable via ⤴ Expand. eos.js's launchers check EOS.companion.enabled
      // at click time, so init order doesn't matter.
      EOS.companion = {
        enabled: true,
        open: function() {
          if (!isOpen) toggle();
          var inp = document.getElementById('__pa_input');
          if (inp) setTimeout(function() { inp.focus(); }, 250);
        },
        openVoice: function() {
          if (!isOpen) toggle();
          if (!_recording) _companionMicToggle();
        },
        expand: function() { location.href = '/voice-assistant/'; },
      };
      // Proactive nudges — the system speaks first, softly, into the rail.
      if (companionProactive && window.EOS && EOS.on) {
        EOS.on('proactive:companion', _onProactiveNudge);
      }
      _loadCompanionHistory();
      _refreshAutoAccept();
      // Restore a previously-pinned rail.
      if (localStorage.getItem(COMPANION_PIN_KEY) === '1' && window.innerWidth >= PIN_MIN_VW) {
        _setPinned(true);
      }
    }).catch(function() {});
  }

  function _toggleFocus() {
    focusMode = !focusMode;
    try { localStorage.setItem(COMPANION_FOCUS_KEY, focusMode ? '1' : '0'); } catch (e) {}
    _paintScope();
  }

  function _paintScope() {
    var el = document.getElementById('__pa_scope');
    if (!el) return;
    el.textContent = focusMode ? ('📍 ' + (appId || 'this page')) : '🌐 Universal';
    el.classList.toggle('on', focusMode);
    el.title = focusMode
      ? ('Focused on ' + (appId || 'this page') + ' — click for universal (all apps)')
      : 'Universal (all apps) — click to focus on ' + (appId || 'this page');
  }

  function _focusApp() { return (focusMode || _forcePageContext) ? (appId || null) : null; }

  // ═══ Proactive nudges — system-initiated messages into the rail ═══
  var _nudgeQueue = [];
  function _onProactiveNudge(payload) {
    if (!payload || !payload.text) return;
    if (isOpen) { _renderNudge(payload); }
    else { _nudgeQueue.push(payload); _setFabBadge(_nudgeQueue.length); }
  }
  function _renderNudge(payload) {
    var welcome = document.querySelector('.pa-welcome');
    if (welcome) welcome.remove();
    var msgs = document.getElementById('__pa_msgs');
    if (!msgs) return;
    var div = document.createElement('div');
    div.className = 'pa-msg assistant nudge';
    var src = payload.source ? '<div class="pa-nudge-src">' + _esc(payload.source) + '</div>' : '';
    var link = '';
    var lk = payload.link;
    if (lk) {
      var href = (typeof lk === 'string') ? lk : lk.href;
      var ltext = (typeof lk === 'string') ? 'Open' : (lk.text || 'Open');
      var safe = href ? _safeUrl(href) : null;
      if (safe) link = '<a class="pa-clink" href="' + _escAttr(safe) + '" target="_blank" rel="noopener noreferrer">' + _esc(ltext) + ' &#8599;</a>';
    }
    div.innerHTML = src + _mdRich(payload.text) + link;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  }
  function _setFabBadge(n) {
    var fab = document.querySelector('.pa-fab');
    if (!fab) return;
    var badge = fab.querySelector('.pa-fab-badge');
    if (n > 0) {
      if (!badge) { badge = document.createElement('span'); badge.className = 'pa-fab-badge'; fab.appendChild(badge); }
      badge.textContent = n > 9 ? '9+' : String(n);
    } else if (badge) { badge.remove(); }
  }
  function _flushNudges() {
    if (!_nudgeQueue.length) return;
    _nudgeQueue.forEach(_renderNudge);
    _nudgeQueue = [];
    _setFabBadge(0);
  }

  function _togglePin() { _setPinned(!isPinned); }

  function _setPinned(on) {
    if (on && window.innerWidth < PIN_MIN_VW) on = false;  // too narrow → overlay only
    isPinned = on;
    document.body.classList.toggle('eos-companion-pinned', on);
    document.documentElement.style.setProperty('--eos-companion-gutter', on ? COMPANION_WIDTH + 'px' : '0px');
    var pin = document.getElementById('__pa_pin');
    if (pin) pin.classList.toggle('on', on);
    try { localStorage.setItem(COMPANION_PIN_KEY, on ? '1' : '0'); } catch (e) {}
    if (on && !isOpen) toggle();   // a pinned rail is always open
  }

  function _toggleAutoAccept() {
    var want = !autoAccept;
    EOS.post('/voice-assistant/api/autopilot/session', { enabled: want, ttl_s: 3600 })
      .then(function(r) { if (r && (r.ok || r.active !== undefined)) { autoAccept = !!(r.active); _paintAutoAccept(); } })
      .catch(function() { if (window.EOS_UI) EOS_UI.toast('Could not toggle auto-accept', false); });
  }

  function _refreshAutoAccept() {
    EOS.api('/voice-assistant/api/autopilot').then(function(r) {
      autoAccept = !!(r && r.grants && r.grants.length);
      _paintAutoAccept();
    }).catch(function() {});
  }

  function _paintAutoAccept() {
    var el = document.getElementById('__pa_auto');
    if (!el) return;
    el.classList.toggle('on', autoAccept);
    el.title = autoAccept ? 'Auto-accept ON this session — click to stop' : 'Auto-accept this session';
  }

  function _renderQuickButtons(el) {
    var pageActions = pageQuickActions || [];
    var defaults = [
      { label: '📋 This page', msg: 'What can I do on this ' + appId + ' page? Show available actions as buttons.' },
      { label: '🎯 What now?', msg: 'Based on my tasks and priorities, what should I focus on right now?' },
    ];
    var actions = pageActions.length ? pageActions : defaults;

    var html = actions.map(function(a) {
      return '<button class="pa-quick-btn" data-msg="' + _escAttr(a.msg) + '">' + _esc(a.label) + '</button>';
    }).join('');

    el.innerHTML = html;

  }

  var _capturesLoaded = false;
  function _loadCaptures() {
    if (_capturesLoaded || !window.EOS || !EOS.api) return;
    _capturesLoaded = true;
    var el = document.getElementById('__pa_quick');
    EOS.api('/quick-action/api/list?limit=5').then(function(captures) {
      if (!captures || !captures.length) return;
      // Dedupe by normalized text — the same reminder captured on two days
      // renders two identical "act on this" buttons, which reads as broken.
      var _seen = {};
      captures = captures.filter(function(c) {
        var k = (c && c.text || '').trim().toLowerCase();
        if (!k || _seen[k]) return false;
        _seen[k] = true; return true;
      });
      if (!captures.length) return;
      var capHtml = '<div class="pa-quick-sep"></div>' +
        captures.slice(0, 3).map(function(c) {
          var text = c.text || '';
          var label = text.length > 30 ? text.slice(0, 28) + '…' : text;
          var tag = c.tag ? ' #' + c.tag : '';
          var msg = 'About this capture: "' + text + '"' + tag + '. Help me act on it — should I create a task, expand it, or file it somewhere?';
          return '<button class="pa-quick-btn capture" title="' + _escAttr(text) + '" data-msg="' + _escAttr(msg) + '">💡 ' + _esc(label) + '</button>';
        }).join('');
      el.innerHTML += capHtml;
    }).catch(function() {});
  }

  function toggle() {
    isOpen = !isOpen;
    document.querySelector('.pa-fab').classList.toggle('open', isOpen);
    var drawerEl = document.querySelector('.pa-drawer');
    drawerEl.classList.toggle('open', isOpen);
    // Keep the closed drawer out of the tab order / a11y tree (see creation).
    // Must clear inert BEFORE the focus() below — you can't focus inside inert.
    drawerEl.inert = !isOpen;
    // Hide the master FAB dock (eos.js #eos-fab-dock) — it lives in the same
    // bottom-right corner as the drawer's Send button. eos.js sets inline
    // display:flex; restore that, not '', when closing.
    var dock = document.getElementById('eos-fab-dock');
    if (dock) dock.style.display = isOpen ? 'none' : 'flex';
    if (isOpen) {
      _loadCaptures();
      _flushNudges();   // drain any nudges that arrived while the rail was closed
      setTimeout(function() { document.getElementById('__pa_input').focus(); }, 250);
    }
  }

  function appendMsg(role, text) {
    var msgs = document.getElementById('__pa_msgs');
    var div = document.createElement('div');
    div.className = 'pa-msg ' + role;
    // Companion assistant prose renders rich markdown + client-action buttons
    // (so a page-oriented answer can offer the page's registered actions as
    // clickable buttons); the rooms path keeps renderResponse for its full
    // [BUTTON:|DO:]/[ACTION:] token set.
    if (role !== 'assistant') div.innerHTML = _esc(text);
    else div.innerHTML = companionMode ? _renderClientActionButtons(_mdRich(text)) : renderResponse(text);
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
  }

  // [BUTTON:label|action(param)] → clickable client-action button that runs a
  // page-registered action (window.__paExec → registeredActions[action]). Shared
  // by renderResponse (rooms path) and the companion path so a companion answer
  // can offer the page's real actions as buttons, not only prose. Client-actions
  // only — the server [DO:] and auto-[ACTION:] tokens stay in renderResponse.
  function _renderClientActionButtons(html) {
    return html.replace(/\[BUTTON:([^|]+)\|(\w+)\(([^)]*)\)\]/g, function(_, label, action, param) {
      var id = 'pa-btn-' + (++_btnCounter);
      return '<button class="pa-action-btn" id="' + id + '" onclick="window.__paExec(\'' + _escAttr(action) + '\',\'' + _escAttr(param) + '\',this)">' + _esc(label) + '</button>';
    });
  }

  // --- Render AI response: markdown + ACTION buttons ---
  function renderResponse(text) {
    // Parse [ACTION:name(param)] → execute immediately
    // Parse [BUTTON:label|DO:app.method({json})] → click-to-execute server action
    // Parse [BUTTON:label|action(param)] → render clickable client-action button
    var html = _renderMd(text);

    // [BUTTON:label|DO:app.method({json})] — server-action click-to-execute.
    // Must run BEFORE the bare [BUTTON:] regex since the inner shape contains
    // a colon which the \w+ in the bare regex rejects (the whole token would
    // otherwise fall through as raw text).
    html = html.replace(
      /\[BUTTON:([^|\]]+)\|DO:([\w-]+)\.(\w+)\((\{[^{}]*\})\)\]/g,
      function(_, label, appId, method, jsonStr) {
        var id = 'pa-do-' + (++_btnCounter);
        var spec = JSON.stringify({app: appId, method: method, json: jsonStr});
        return '<button class="pa-action-btn" id="' + id +
               '" data-do="' + _escAttr(spec) +
               '" onclick="window.__paExecDo(this)">' + _esc(label) + '</button>';
      }
    );

    // [BUTTON:label|action(param)] — client-action button (page-registered actions).
    html = _renderClientActionButtons(html);

    // Replace [ACTION:name(param)] — auto-execute and show result
    html = html.replace(/\[ACTION:(\w+)\(([^)]*)\)\]/g, function(_, action, param) {
      _execAction(action, param.replace(/^['"]|['"]$/g, ''));
      return '<span class="pa-action-result">&#10003; Executed: ' + _esc(action) + '</span>';
    });

    return html;
  }
  var _btnCounter = 0;

  // Global action executor (called from button onclick)
  window.__paExec = function(action, param, btn) {
    _execAction(action, param);
    if (btn) {
      btn.classList.add('pa-action-done');
      btn.textContent = '✓ ' + btn.textContent;
    }
  };

  // Click-to-execute server action — fires when user clicks a
  // [BUTTON:label|DO:app.method({json})] button. POSTs to /rooms/api/do
  // which validates against the agent's server_actions allowlist before
  // calling call_app.
  window.__paExecDo = function(btn) {
    if (!btn || btn.disabled) return;
    var raw = btn.getAttribute('data-do');
    if (!raw) return;
    var spec, args;
    try {
      spec = JSON.parse(raw);
      args = JSON.parse(spec.json);
    } catch (e) {
      if (window.EOS_UI) EOS_UI.toast('Bad action payload', false);
      return;
    }
    var orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '...';
    EOS.post('/rooms/api/do', {
      agent_id: pageGpt,
      app: spec.app,
      method: spec.method,
      args: args,
    }).then(function(res) {
      if (res && res.ok) {
        btn.classList.add('pa-action-done');
        btn.innerHTML = '&#10003; ' + orig;
        _renderActionResult(btn, res);
        if (window.EOS_UI) EOS_UI.toast(spec.app + '.' + spec.method + ' done', true);
      } else {
        btn.disabled = false;
        btn.innerHTML = orig;
        if (window.EOS_UI) EOS_UI.toast((res && (res.error || res.message)) || 'Action failed', false);
      }
    }).catch(function(e) {
      btn.disabled = false;
      btn.innerHTML = orig;
      if (window.EOS_UI) EOS_UI.toast('Error: ' + (e && e.message || e), false);
    });
  };

  // Build the link-row HTML for an action result — delegates to the shared
  // EOS_UI.actionLinks helper so this surface and rooms/index.html stay in sync.
  function _actionLinksHtml(links) {
    return (window.EOS_UI && EOS_UI.actionLinks) ? EOS_UI.actionLinks(links) : '';
  }

  // Render the post-click result chip below the button.
  function _renderActionResult(btn, res) {
    var html = _actionLinksHtml(res && res.links);
    if (!html || !btn.parentNode) return;
    var wrap = document.createElement('div');
    wrap.innerHTML = html;
    btn.parentNode.insertBefore(wrap.firstChild, btn.nextSibling);
  }

  // Global undo (called from inline server-results undo button)
  window.__paUndo = function(btn) {
    btn.disabled = true;
    var orig = btn.innerHTML;
    btn.innerHTML = '...';
    EOS.post('/rooms/api/undo', {}).then(function(res) {
      if (res && res.ok) {
        btn.innerHTML = '&#10003; Undone';
        btn.classList.add('pa-action-done');
        if (window.EOS_UI) {
          EOS_UI.toast('Undone: ' + res.undid.app + '.' + res.undid.method);
        }
      } else {
        btn.disabled = false;
        btn.innerHTML = orig;
        if (window.EOS_UI) {
          EOS_UI.toast((res && (res.message || res.error)) || 'Undo failed', false);
        }
      }
    }).catch(function(e) {
      btn.disabled = false;
      btn.innerHTML = orig;
      if (window.EOS_UI) EOS_UI.toast('Undo error: ' + e.message, false);
    });
  };

  function _execAction(name, param) {
    var fn = registeredActions[name];
    if (fn) {
      try { fn(param); } catch(e) { console.error('PA action error:', name, e); }
    } else {
      console.warn('PA: unknown action:', name);
    }
  }

  // DELIBERATELY minimal (NOT the shared EOS_UI.renderMarkdown). renderResponse
  // (the rooms path) calls this so the [BUTTON:]/[DO:]/[ACTION:] action tokens
  // survive as literal `[...]` for the regex passes that turn them into
  // click-to-execute buttons — a rich renderer would escape/mangle the brackets
  // and break the buttons. The companion path (_mdRich) has no such tokens and
  // DOES use the shared renderer. Do not "consolidate" this to the shared bundle.
  function _renderMd(text) {
    return _esc(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br>');
  }

  // Rich markdown for companion prose (lists/tables/headings) — the Aura brain
  // strips [INTENT:] server-side and emits no [BUTTON:]/[ACTION:] client tokens
  // (those are a rooms convention), so full markdown is safe here. Falls back to
  // the minimal renderer when the shared bundle isn't loaded.
  function _mdRich(text) {
    if (window.EOS_UI && EOS_UI.renderMarkdown) {
      try { return EOS_UI.renderMarkdown(text || '', {}); } catch (e) {}
    }
    return _renderMd(text || '');
  }

  function _getPageContext() {
    var ctx = { app: appId, url: location.pathname };
    if (pageDescription) ctx.page_description = pageDescription;
    // Gather visible metrics/data
    var cards = document.querySelectorAll('.mc-val,.sys-val,.pulse-pill .pv,.hc-score,.health-score,.w-v');
    if (cards.length) {
      ctx.metrics = [];
      cards.forEach(function(c) { if (c.textContent.trim()) ctx.metrics.push(c.textContent.trim()); });
    }
    // Page-specific live data (pages register EOS.getPageMetrics for rich context)
    if (EOS.getPageMetrics) {
      try { ctx.live_data = EOS.getPageMetrics(); } catch(e) {}
    }
    // Include registered actions
    ctx.available_actions = actionDescriptions;
    return ctx;
  }

  // Compact page-context string sent to the companion ONLY when 📍 focus is on
  // — the same visible metrics + live_data the rooms path sends, so a focused
  // turn is grounded in what the user is looking at. Server ignores it unless
  // feature.companion-focus-context.enabled. Capped so it can't blow the budget.
  function _companionPageContextStr() {
    // Consume the one-shot force flag (set by a page-oriented quick button).
    // _focusApp() is read before this in every call site's payload literal, so
    // it still sees the flag true this turn; resetting here keeps the NEXT
    // free-form turn ungrounded unless 📍 focus is actually on.
    var force = _forcePageContext; _forcePageContext = false;
    if (!focusMode && !force) return '';
    var ctx = _getPageContext();
    var s = 'Page: ' + (ctx.app || '') + ' (' + (ctx.url || '') + ')';
    if (ctx.page_description) s += '\n' + ctx.page_description;
    // Registered page actions — so "what can I do on this page?" is answered
    // from the real action set instead of the model guessing.
    if (ctx.available_actions && ctx.available_actions.length) {
      s += '\nActions on this page: ' + ctx.available_actions
        .map(function(a) { return a.name + '(' + ((a.params || []).join(', ')) + ')'; })
        .join(', ');
    }
    if (ctx.live_data) s += '\nLive data: ' + (typeof ctx.live_data === 'string' ? ctx.live_data : JSON.stringify(ctx.live_data));
    if (ctx.metrics && ctx.metrics.length) s += '\nVisible: ' + ctx.metrics.join(', ');
    return s.slice(0, 1200);
  }

  function send(presetMsg) {
    if (isStreaming) return;
    var input = document.getElementById('__pa_input');
    var msg = presetMsg || input.value.trim();
    if (!msg) return;
    if (!presetMsg) input.value = '';

    var welcome = document.querySelector('.pa-welcome');
    if (welcome) welcome.remove();
    if (!isOpen) toggle();

    appendMsg('user', msg);
    var assistDiv = appendMsg('assistant', '');
    assistDiv.innerHTML = '<span class="pa-spinner"></span>';

    isStreaming = true;
    document.getElementById('__pa_send').disabled = true;

    if (companionMode) { _companionTurn(msg, assistDiv); return; }

    var ctx = _getPageContext();
    var apiCall;

    if (pageGpt) {
      // Route to Rooms — agent has persona, knowledge, server actions
      // Build context: page identity + description + live data
      var gptContext = 'Page: ' + appId + ' (' + location.pathname + ')';
      if (ctx.page_description) gptContext += '\n' + ctx.page_description;
      if (ctx.live_data) gptContext += '\n\nLive data:\n' + ctx.live_data;
      if (ctx.metrics && ctx.metrics.length) gptContext += '\nVisible metrics: ' + ctx.metrics.join(', ');
      apiCall = EOS.post('/rooms/api/chat', {
        agent_id: pageGpt,
        text: msg,
        context: gptContext,
        client_actions: ctx.available_actions || [],
      });
    } else {
      // Fallback to assistant — ad-hoc system hint
      var systemHint = 'You are the EmptyOS Page Assistant for the "' + appId + '" page.\n' +
        'Current URL: ' + location.pathname + '\n';
      if (ctx.page_description) systemHint += 'Page: ' + ctx.page_description + '\n';
      if (ctx.available_actions && ctx.available_actions.length) {
        systemHint += 'Available actions (use [ACTION:name(param)] to execute):\n' +
          ctx.available_actions.map(function(a) { return '- ' + a.name + '(' + (a.params||[]).join(', ') + '): ' + a.description; }).join('\n') + '\n';
      }
      if (ctx.live_data) systemHint += 'Live data from this page:\n' + ctx.live_data + '\n';
      if (ctx.metrics && ctx.metrics.length) systemHint += 'Visible metrics: ' + ctx.metrics.join(', ') + '\n';
      systemHint += 'You can also output [BUTTON:label|action(param)] to show clickable buttons.\n';
      systemHint += 'Be concise. Answer in the user\'s language.';
      apiCall = EOS.post('/assistant/api/chat', {
        message: systemHint + '\n\nUser: ' + msg,
        context: true,
      });
    }

    apiCall
    .then(function(data) {
      var text = data.response || data.error || 'No response';
      assistDiv.innerHTML = renderResponse(text);
      // Show server action results if any
      if (data.server_results && data.server_results.length) {
        var srHtml = '<div style="margin-top:6px;padding:6px 10px;background:color-mix(in srgb,var(--accent) 8%,transparent);border-radius:8px;font-size:11px">';
        var anyReversible = false;
        data.server_results.forEach(function(r) {
          var icon = r.ok ? '&#10003;' : '&#10007;';
          var color = r.ok ? 'var(--success,#34d399)' : '#f87171';
          srHtml += '<div style="color:'+color+'">'+icon+' '+_esc(r.app)+'.'+_esc(r.method)+(r.ok?' — done':' — '+_esc(r.error||'failed'))+'</div>';
          srHtml += _actionLinksHtml(r.links);
          if (r.ok && r.reversible) anyReversible = true;
        });
        if (anyReversible) {
          srHtml += '<button class="pa-action-btn" style="margin-top:6px" onclick="window.__paUndo(this)">&#8617; Undo</button>';
        }
        srHtml += '</div>';
        assistDiv.innerHTML += srHtml;
      }
      document.getElementById('__pa_msgs').scrollTop = document.getElementById('__pa_msgs').scrollHeight;
      isStreaming = false;
      document.getElementById('__pa_send').disabled = false;
    })
    .catch(function(e) {
      assistDiv.innerHTML = '<span style="color:#f87171">Error: ' + _esc(e.message) + '</span>';
      isStreaming = false;
      document.getElementById('__pa_send').disabled = false;
    });
  }

  // ═══ Quick Capture ═══
  var _capOpen = false;
  var _capSelectedTag = '';

  function _capToggle() {
    _capOpen = !_capOpen;
    var modal = document.getElementById('__cap_modal');
    modal.classList.toggle('show', _capOpen);
    if (_capOpen) {
      var input = document.getElementById('__cap_input');
      input.value = '';
      setTimeout(function() { input.focus(); }, 100);
    }
  }
  window._capToggle = _capToggle;

  window._capTag = function(el) {
    document.querySelectorAll('.cap-tag').forEach(function(t) { t.classList.remove('active'); });
    if (_capSelectedTag === el.dataset.tag) {
      _capSelectedTag = '';
    } else {
      _capSelectedTag = el.dataset.tag;
      el.classList.add('active');
    }
  };

  window._capSubmit = function() {
    var input = document.getElementById('__cap_input');
    var text = input.value.trim();
    if (!text) return;
    var btn = document.getElementById('__cap_send');
    btn.disabled = true;
    btn.textContent = 'Saving...';
    var body = { text: text };
    if (_capSelectedTag) body.tag = _capSelectedTag;
    EOS.post('/quick-action/api/add', body).then(function(res) {
      btn.disabled = false;
      btn.textContent = 'Capture';
      _capToggle();
      _capSelectedTag = '';
      document.querySelectorAll('.cap-tag').forEach(function(t) { t.classList.remove('active'); });
      if (window.EOS_UI) {
        var msg = (res && res.routed_to)
          ? '→ ' + (res.project_name || res.routed_to)
          : 'Captured';
        EOS_UI.toast(msg);
      }
    }).catch(function() {
      btn.disabled = false;
      btn.textContent = 'Capture';
      if (window.EOS_UI) EOS_UI.toast('Failed to capture', false);
    });
  };

  // Close capture modal when clicking outside
  document.addEventListener('click', function(e) {
    if (_capOpen && !e.target.closest('.cap-modal') && !e.target.closest('.cap-fab')) {
      _capToggle();
    }
  });

  // ═══ Companion turn — one brain, text face ═══
  function _companionTurn(msg, assistDiv) {
    if (companionStream) { _companionTurnStream(msg, assistDiv); return; }
    companionMessages.push({ role: 'user', content: msg });
    EOS.post('/voice-assistant/api/companion_turn', {
      text: msg,
      messages: companionMessages.slice(0, -1),
      companion: companionId,
      focus_app: _focusApp(),
      context: _companionPageContextStr(),
      client_actions: companionActions ? actionDescriptions : undefined,
    }).then(function(data) {
      if (data && data.disabled) {
        // Flag flipped off mid-session — fall back to the rooms path.
        companionMode = false;
        companionMessages.pop();
        assistDiv.remove();
        send(msg);
        return;
      }
      var reply = (data && data.reply_text) || (data && data.error) || 'No response';
      assistDiv.innerHTML = renderResponse(reply) + _companionExtrasHtml(data);
      _wireCompanionExtras(assistDiv);
      if (data && data.session) {
        companionMessages = Array.isArray(data.session.messages) ? data.session.messages : companionMessages;
        companionId = data.session.companion || null;
      } else {
        companionMessages.push({ role: 'assistant', content: reply });
      }
      _saveCompanionHistory();
      var m = document.getElementById('__pa_msgs');
      if (m) m.scrollTop = m.scrollHeight;
      isStreaming = false;
      document.getElementById('__pa_send').disabled = false;
    }).catch(function(e) {
      assistDiv.innerHTML = '<span style="color:#f87171">Error: ' + _esc(e && e.message || e) + '</span>';
      isStreaming = false;
      document.getElementById('__pa_send').disabled = false;
    });
  }

  // Streaming companion turn — paints prose + cards/links/pending as NDJSON
  // events arrive off /api/companion_turn_stream (the same turn_events brain,
  // uncollapsed). Prose repaints per delta; extras append as their events land.
  function _companionTurnStream(msg, assistDiv) {
    companionMessages.push({ role: 'user', content: msg });
    var priorMessages = companionMessages.slice(0, -1);
    assistDiv.innerHTML = '<div class="pa-prose"></div><div class="pa-extras"></div>';
    var proseEl = assistDiv.querySelector('.pa-prose');
    var extrasEl = assistDiv.querySelector('.pa-extras');
    var msgsEl = document.getElementById('__pa_msgs');
    var fullText = '';
    var extras = { cards: [], links: [], pending: [], confirm: [] };

    function paintExtras() {
      extrasEl.innerHTML = _companionExtrasHtml(extras);
      _wireCompanionExtras(extrasEl);
    }
    function errBox(m) {
      proseEl.innerHTML = '<span style="color:var(--danger,#f87171)">Error: ' + _esc(m) + '</span>';
    }
    function finish() {
      isStreaming = false;
      var sb = document.getElementById('__pa_send'); if (sb) sb.disabled = false;
      if (msgsEl) msgsEl.scrollTop = msgsEl.scrollHeight;
    }

    (async function () {
      try {
        for await (var ev of EOS.streamPost('/voice-assistant/api/companion_turn_stream', {
          text: msg, messages: priorMessages, companion: companionId,
          focus_app: _focusApp(), context: _companionPageContextStr(),
          client_actions: companionActions ? actionDescriptions : undefined,
        })) {
          if (!ev) continue;
          if (ev.disabled) {           // flag flipped off mid-session → fall back
            companionStream = false;
            companionMessages.pop();
            assistDiv.remove();
            send(msg);
            return;
          }
          var t = ev.type;
          if (t === 'text') {
            fullText += ev.delta || '';
            proseEl.innerHTML = _mdRich(fullText);
          } else if (t === 'card') {
            extras.cards.push({ intent: ev.intent, renderer: ev.renderer, data: ev.data, title: ev.title });
            paintExtras();
          } else if (t === 'link') {
            extras.links.push({ intent: ev.intent, text: ev.text, href: ev.href });
            paintExtras();
          } else if (t === 'pending_action') {
            extras.pending.push({ action_id: ev.action_id, verb: ev.verb, args: ev.args, description: ev.description });
            paintExtras();
          } else if (t === 'confirm_required') {
            extras.confirm.push({ verb: ev.verb, args: ev.args, description: ev.description });
            paintExtras();
          } else if (t === 'companion_switch') {
            companionId = ev.companion || null;
          } else if (t === 'done') {
            if (Array.isArray(ev.messages)) companionMessages = ev.messages;
            if (ev.companion !== undefined) companionId = ev.companion || null;
            // Final render — parse client-action buttons now the text is complete
            // (per-delta renders above stay markdown-only to avoid partial tokens).
            if (ev.full_text) { fullText = ev.full_text; proseEl.innerHTML = _renderClientActionButtons(_mdRich(fullText)); }
          } else if (t === 'error') {
            errBox(ev.error || 'turn failed');
          } else if (ev.error && !t) {
            errBox(ev.error);
          }
          if (msgsEl) msgsEl.scrollTop = msgsEl.scrollHeight;
        }
        // No `done` with a threaded history → append the assistant turn locally.
        var last = companionMessages[companionMessages.length - 1];
        if (last && last.role !== 'assistant' && fullText) {
          companionMessages.push({ role: 'assistant', content: fullText });
        }
        _saveCompanionHistory();
      } catch (e) {
        errBox(e && e.message || e);
      } finally {
        finish();
      }
    })();
  }

  // ═══ Companion voice — record + play IN the rail (same brain) ═══
  function _companionMicToggle() {
    if (_recording) { _stopRecording(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      if (window.EOS_UI) EOS_UI.toast('Mic not available here', false);
      return;
    }
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      .then(function(stream) {
        _recChunks = [];
        _mediaRec = new MediaRecorder(stream);
        _mediaRec.ondataavailable = function(e) { if (e.data && e.data.size) _recChunks.push(e.data); };
        _mediaRec.onstop = function() {
          stream.getTracks().forEach(function(t) { t.stop(); });
          var blob = new Blob(_recChunks, { type: 'audio/webm' });
          if (blob.size > 800) _companionVoiceTurn(blob);
        };
        _mediaRec.start();
        _recording = true;
        _setMicState(true);
        if (!isOpen) toggle();
      })
      .catch(function() { if (window.EOS_UI) EOS_UI.toast('Mic permission denied', false); });
  }

  function _stopRecording() {
    _recording = false;
    _setMicState(false);
    try { if (_mediaRec && _mediaRec.state !== 'inactive') _mediaRec.stop(); } catch (e) {}
  }

  function _setMicState(on) {
    var mic = document.getElementById('__pa_mic');
    if (mic) { mic.classList.toggle('recording', on); mic.title = on ? 'Stop & send' : 'Speak (record here)'; }
  }

  function _companionVoiceTurn(blob) {
    if (isStreaming) return;
    isStreaming = true;
    document.getElementById('__pa_send').disabled = true;
    var welcome = document.querySelector('.pa-welcome');
    if (welcome) welcome.remove();
    var thinking = appendMsg('assistant', '');
    thinking.innerHTML = '<span class="pa-spinner"></span>';
    var fd = new FormData();
    fd.append('audio', blob, 'rec.webm');
    fd.append('messages', JSON.stringify(companionMessages));
    if (companionId) fd.append('companion', companionId);
    var fa = _focusApp(); if (fa) fd.append('focus_app', fa);
    var pc = _companionPageContextStr(); if (pc) fd.append('context', pc);
    EOS.api('/voice-assistant/api/companion_voice_turn', { method: 'POST', body: fd })
      .then(function(data) {
        if (data && data.disabled) { thinking.remove(); companionMode = false; return; }
        if (data && data.transcript) {
          // Insert the transcribed user message before the assistant bubble.
          var um = document.createElement('div');
          um.className = 'pa-msg user';
          um.innerHTML = _esc(data.transcript);
          thinking.parentNode.insertBefore(um, thinking);
          companionMessages.push({ role: 'user', content: data.transcript });
        }
        var reply = (data && data.reply_text) || (data && data.error) || 'No response';
        thinking.innerHTML = renderResponse(reply) + _companionExtrasHtml(data);
        _wireCompanionExtras(thinking);
        if (data && data.session && Array.isArray(data.session.messages)) {
          companionMessages = data.session.messages;
          companionId = data.session.companion || null;
        } else {
          companionMessages.push({ role: 'assistant', content: reply });
        }
        _saveCompanionHistory();
        if (data && data.audio_urls && data.audio_urls.length) _enqueueReplyAudio(data.audio_urls);
        var m = document.getElementById('__pa_msgs'); if (m) m.scrollTop = m.scrollHeight;
        isStreaming = false;
        document.getElementById('__pa_send').disabled = false;
      })
      .catch(function(e) {
        thinking.innerHTML = '<span style="color:#f87171">Voice error: ' + _esc(e && e.message || e) + '</span>';
        isStreaming = false;
        document.getElementById('__pa_send').disabled = false;
      });
  }

  function _enqueueReplyAudio(urls) {
    _audioQueue = _audioQueue.concat(urls);
    if (!_voiceAudio) {
      _voiceAudio = new Audio();
      _voiceAudio.onended = function() { _audioPlaying = false; _playNextReplyAudio(); };
      _voiceAudio.onerror = function() { _audioPlaying = false; _playNextReplyAudio(); };
    }
    _playNextReplyAudio();
  }

  function _playNextReplyAudio() {
    if (_audioPlaying || !_audioQueue.length) return;
    _audioPlaying = true;
    _voiceAudio.src = _audioQueue.shift();
    var p = _voiceAudio.play();
    if (p && p.catch) p.catch(function() { _audioPlaying = false; _playNextReplyAudio(); });
  }

  // Render the non-prose surfaces of a companion reply: cards, links, pending,
  // confirm gates. (Form cards come through `cards` via the `form` renderer.)
  function _companionExtrasHtml(data) {
    if (!data) return '';
    var html = '';
    (data.cards || []).forEach(function(c) { html += _renderCard(c); });
    (data.links || []).forEach(function(l) {
      if (!l || !l.href) return;
      var safe = _safeUrl(l.href);
      if (!safe) return;  // reject javascript:/data:/vbscript:/unknown schemes
      html += '<a class="pa-clink" href="' + _escAttr(safe) + '" target="_blank" rel="noopener noreferrer">' + _esc(l.text || 'Open') + ' &#8599;</a>';
    });
    (data.pending || []).forEach(function(p) {
      if (!p || !p.action_id) return;
      html += '<div class="pa-pending" data-act="' + _escAttr(p.action_id) + '">' +
        '<div class="pa-pending-verb">' + _esc(p.verb || '') + '</div>' +
        '<div class="pa-pending-desc">' + _esc(p.description || 'Apply this action?') + '</div>' +
        '<div class="pa-pending-row">' +
        '<button class="pa-apply">Apply</button>' +
        '<button class="pa-reject">Reject</button>' +
        '</div></div>';
    });
    (data.confirm || []).forEach(function(c) {
      if (!c || !c.verb) return;
      html += _renderConfirmHtml(c);
    });
    return html;
  }

  // Confirm gate — a `confirm = true` intent the model wants to run. The user
  // Runs (→ /api/confirm-intent) or Skips. Args shown for review before firing.
  function _renderConfirmHtml(c) {
    var argsPretty = Object.keys(c.args || {}).map(function(k) {
      var v = c.args[k];
      return _esc(k) + ': ' + _esc(typeof v === 'string' ? v : JSON.stringify(v));
    }).join(' · ');
    return '<div class="pa-confirm" data-verb="' + _escAttr(c.verb) + '" data-args="' + _escAttr(JSON.stringify(c.args || {})) + '">' +
      '<div class="pa-confirm-verb">' + _esc(c.verb) + '</div>' +
      '<div class="pa-confirm-desc">' + _esc(c.description || 'Run this action?') + '</div>' +
      (argsPretty ? '<div class="pa-confirm-args">' + argsPretty + '</div>' : '') +
      '<div class="pa-confirm-row">' +
      '<button class="pa-apply pa-confirm-run">Run</button>' +
      '<button class="pa-reject pa-confirm-skip">Skip</button>' +
      '</div></div>';
  }

  // Wire every interactive surface in a freshly-rendered extras block:
  // pending Apply/Reject, confirm Run/Skip, and form submit.
  function _wireCompanionExtras(root) {
    root.querySelectorAll('.pa-pending').forEach(function(card) {
      if (card._wired) return; card._wired = true;
      var id = card.getAttribute('data-act');
      card.querySelector('.pa-apply').onclick = function() { _resolvePending(card, id, true); };
      card.querySelector('.pa-reject').onclick = function() { _resolvePending(card, id, false); };
    });
    root.querySelectorAll('.pa-confirm').forEach(function(card) {
      if (card._wired) return; card._wired = true;
      var verb = card.getAttribute('data-verb');
      var args = {};
      try { args = JSON.parse(card.getAttribute('data-args') || '{}'); } catch (e) {}
      card.querySelector('.pa-confirm-run').onclick = function() { _runConfirm(card, verb, args); };
      card.querySelector('.pa-confirm-skip').onclick = function() {
        card.classList.add('done');
        card.insertAdjacentHTML('beforeend', '<div class="pa-action-result">Skipped</div>');
      };
    });
    root.querySelectorAll('.pa-form').forEach(function(form) {
      var btn = form.querySelector('.pa-form-submit');
      if (!btn || btn._wired) return; btn._wired = true;
      btn.onclick = function() { _submitForm(form); };
    });
  }
  // A sanitized deep-link chip from a confirm/form result (out.link).
  function _companionLinkHtml(link) {
    if (!link || !link.href) return '';
    var safe = _safeUrl(link.href);
    if (!safe) return '';
    return '<a class="pa-clink" href="' + _escAttr(safe) + '" target="_blank" rel="noopener noreferrer">' + _esc(link.text || 'Open') + ' &#8599;</a>';
  }

  // ↶ Undo chip for a reversible companion action (apply / confirm / form).
  // Posts to the voice-assistant undo route, which reverses the last logged
  // reversible intent via its registry-declared inverse.
  function _companionUndoHtml(reversible) {
    if (!reversible) return '';
    return '<button class="pa-action-btn pa-undo-btn" onclick="window.__paCompanionUndo(this)">&#8617; Undo</button>';
  }
  window.__paCompanionUndo = function(btn) {
    if (!btn || btn.disabled) return;
    btn.disabled = true; btn.textContent = '...';
    EOS.post('/voice-assistant/api/undo', {}).then(function(res) {
      if (res && res.ok) {
        btn.textContent = '↷ Undone';
        if (window.EOS_UI) EOS_UI.toast('Undone', true);
      } else {
        btn.disabled = false; btn.innerHTML = '&#8617; Undo';
        if (window.EOS_UI) EOS_UI.toast((res && (res.message || res.error)) || 'Nothing to undo', false);
      }
    }).catch(function() {
      btn.disabled = false; btn.innerHTML = '&#8617; Undo';
      if (window.EOS_UI) EOS_UI.toast('Undo failed', false);
    });
  };

  // Run a confirmed intent, then render its say + card inline in the card.
  function _runConfirm(card, verb, args) {
    var btns = card.querySelectorAll('button');
    btns.forEach(function(b) { b.disabled = true; });
    EOS.post('/voice-assistant/api/confirm-intent', { verb: verb, args: args }).then(function(out) {
      if (!out || out.error) {
        btns.forEach(function(b) { b.disabled = false; });
        if (window.EOS_UI) EOS_UI.toast((out && out.error) || 'Could not run', false);
        return;
      }
      card.classList.add('done');
      var extra = '';
      if (out.say) extra += '<div class="pa-confirm-say">' + _mdRich(out.say) + '</div>';
      if (out.card) extra += _renderCard(out.card);
      extra += _companionLinkHtml(out.link);
      card.insertAdjacentHTML('beforeend', '<div class="pa-action-result">&#10003; Done</div>' + extra + _companionUndoHtml(out.reversible));
      if (window.EOS_UI) EOS_UI.toast('Done', true);
    }).catch(function() {
      btns.forEach(function(b) { b.disabled = false; });
      if (window.EOS_UI) EOS_UI.toast('Error running action', false);
    });
  }

  // Submit a form card: merge the edited field values over the submit args and
  // fire the declared verb via /api/confirm-intent (user submits — never autofilled).
  function _submitForm(form) {
    var verb = form.getAttribute('data-verb');
    var baseArgs = {};
    try { baseArgs = JSON.parse(form.getAttribute('data-args') || '{}'); } catch (e) {}
    var vals = {};
    form.querySelectorAll('[data-field]').forEach(function(inp) { vals[inp.getAttribute('data-field')] = inp.value; });
    var args = Object.assign({}, baseArgs, vals);
    var btn = form.querySelector('.pa-form-submit');
    btn.disabled = true; btn.textContent = '...';
    EOS.post('/voice-assistant/api/confirm-intent', { verb: verb, args: args }).then(function(out) {
      if (!out || out.error) {
        btn.disabled = false; btn.textContent = 'Submit';
        if (window.EOS_UI) EOS_UI.toast((out && out.error) || 'Could not submit', false);
        return;
      }
      form.classList.add('done');
      form.querySelectorAll('input,select,button').forEach(function(e) { e.disabled = true; });
      var extra = '';
      if (out.say) extra += '<div class="pa-confirm-say">' + _mdRich(out.say) + '</div>';
      if (out.card) extra += _renderCard(out.card);
      extra += _companionLinkHtml(out.link);
      form.insertAdjacentHTML('afterend', '<div class="pa-action-result">&#10003; Submitted</div>' + extra + _companionUndoHtml(out.reversible));
      if (window.EOS_UI) EOS_UI.toast('Submitted', true);
    }).catch(function() {
      btn.disabled = false; btn.textContent = 'Submit';
      if (window.EOS_UI) EOS_UI.toast('Error submitting', false);
    });
  }

  function _resolvePending(card, id, apply) {
    var btns = card.querySelectorAll('button');
    btns.forEach(function(b) { b.disabled = true; });
    var url = '/voice-assistant/api/pending/' + encodeURIComponent(id) + (apply ? '/apply' : '/reject');
    EOS.post(url, {}).then(function(res) {
      if (res && res.status && res.status !== 'pending' && !res.error) {
        card.classList.add('done');
        var undo = apply ? _companionUndoHtml(res.reversible) : '';
        card.insertAdjacentHTML('beforeend', '<div class="pa-action-result">' + (apply ? '&#10003; Applied' : 'Rejected') + '</div>' + undo);
        if (window.EOS_UI) EOS_UI.toast(apply ? 'Applied' : 'Rejected', apply);
      } else {
        btns.forEach(function(b) { b.disabled = false; });
        if (window.EOS_UI) EOS_UI.toast((res && res.error) || 'Could not resolve', false);
      }
    }).catch(function() {
      btns.forEach(function(b) { b.disabled = false; });
      if (window.EOS_UI) EOS_UI.toast('Error resolving action', false);
    });
  }

  // Sidebar-styled renderers — same DATA shapes as Aura's cards (stat-tile,
  // entity-card, task-list, memory-list); unknown renderers fall back to title.
  function _renderCard(c) {
    if (!c || !c.renderer) return '';
    var titleHtml = c.title ? '<div class="pa-card-title">' + _esc(c.title) + '</div>' : '';
    var body = '';
    var d = c.data;
    if (c.renderer === 'stat-tile') {
      var rows = Array.isArray(d) ? d : (d ? [d] : []);
      body = rows.map(function(s) {
        return '<div class="pa-stat"><span class="k">' + _esc(s.label || '') + '</span><span class="v">' + _esc(String(s.value != null ? s.value : '')) + '</span></div>';
      }).join('');
    } else if (c.renderer === 'entity-card') {
      d = d || {};
      body = '<div style="font-weight:600;color:var(--text-heading);font-size:13px">' + _esc(d.title || '') + '</div>';
      if (d.subtitle) body += '<div class="pa-ent-sub">' + _esc(d.subtitle) + '</div>';
      (d.fields || []).forEach(function(f) {
        body += '<div class="pa-stat"><span class="k">' + _esc(f.label || '') + '</span><span class="v">' + _esc(String(f.value != null ? f.value : '')) + '</span></div>';
      });
    } else if (c.renderer === 'task-list') {
      var items = Array.isArray(d) ? d : [];
      if (!items.length) body = '<div style="font-size:12px;color:var(--text-muted)">Nothing here.</div>';
      body = items.map(function(t) {
        var tone = t.done ? 'done' : (t.tone || '');
        return '<div class="pa-row"><span class="dot ' + _esc(tone) + '"></span><span>' + _esc(t.text || '') + '</span>' +
          (t.tag ? '<span class="tg">#' + _esc(t.tag) + '</span>' : '') + '</div>';
      }).join('');
    } else if (c.renderer === 'memory-list') {
      var facts = Array.isArray(d) ? d : (d && d.items) || [];
      body = facts.map(function(f) {
        var txt = (typeof f === 'string') ? f : (f.text || f.body || '');
        return '<div class="pa-row"><span class="dot"></span><span>' + _esc(txt) + '</span></div>';
      }).join('');
    } else if (c.renderer === 'form') {
      // Companion asks for structured fields — user reviews/edits then submits.
      // Gated by feature.companion-forms.enabled; degrades to a title otherwise.
      if (!companionForms) {
        body = '<div style="font-size:12px;color:var(--text-muted)">' + _esc(c.title || 'form') + '</div>';
      } else {
        d = d || {};
        var submit = d.submit || {};
        var fieldsHtml = (d.fields || []).map(_formFieldHtml).join('');
        body = '<div class="pa-form" data-verb="' + _escAttr(submit.verb || '') +
          '" data-args="' + _escAttr(JSON.stringify(submit.args || {})) + '">' +
          fieldsHtml +
          '<button class="pa-form-submit">' + _esc(submit.label || 'Submit') + '</button>' +
          '</div>';
      }
    } else {
      // Unknown renderer (e.g. research-result) — show the title, skip the body.
      body = '<div style="font-size:12px;color:var(--text-muted)">' + _esc(c.renderer) + '</div>';
    }
    return '<div class="pa-card">' + titleHtml + '<div class="pa-card-body">' + body + '</div></div>';
  }

  // One labelled input for a form card. Supported types: text, number, date, select.
  function _formFieldHtml(f) {
    if (!f || !f.name) return '';
    var name = _escAttr(f.name);
    var val = f.value != null ? String(f.value) : '';
    var label = '<label class="pa-form-label">' + _esc(f.label || f.name) + (f.required ? ' *' : '') + '</label>';
    var input;
    if (f.type === 'select') {
      var opts = (f.options || []).map(function(o) {
        var ov = (o && typeof o === 'object') ? o.value : o;
        var ol = (o && typeof o === 'object') ? (o.label != null ? o.label : o.value) : o;
        return '<option value="' + _escAttr(String(ov)) + '"' + (String(ov) === val ? ' selected' : '') + '>' + _esc(String(ol)) + '</option>';
      }).join('');
      input = '<select class="pa-form-input" data-field="' + name + '">' + opts + '</select>';
    } else {
      var type = (f.type === 'number' || f.type === 'date') ? f.type : 'text';
      input = '<input class="pa-form-input" type="' + type + '" data-field="' + name + '" value="' + _escAttr(val) + '">';
    }
    return '<div class="pa-form-row">' + label + input + '</div>';
  }

  // ── Companion history persistence (memory across page loads) ──
  function _saveCompanionHistory() {
    try {
      localStorage.setItem(COMPANION_HIST_KEY, JSON.stringify({
        messages: companionMessages.slice(-40),
        companion: companionId,
      }));
    } catch (e) {}
  }

  function _loadCompanionHistory() {
    var raw;
    try { raw = JSON.parse(localStorage.getItem(COMPANION_HIST_KEY) || 'null'); } catch (e) { raw = null; }
    if (!raw || !Array.isArray(raw.messages) || !raw.messages.length) return;
    companionMessages = raw.messages;
    companionId = raw.companion || null;
    var welcome = document.querySelector('.pa-welcome');
    if (welcome) welcome.remove();
    companionMessages.forEach(function(m) {
      if (m && (m.role === 'user' || m.role === 'assistant') && m.content) appendMsg(m.role, m.content);
    });
  }

  function _esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
  function _escAttr(s) { return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
  // Allow only navigable web/mail schemes (incl. in-app relative paths, which
  // resolve to the page origin). Rejects javascript:/data:/vbscript:/unknown.
  function _safeUrl(href) {
    try {
      var u = new URL(href, location.href);
      if (u.protocol === 'http:' || u.protocol === 'https:' || u.protocol === 'mailto:') return u.href;
    } catch (e) {}
    return null;
  }
})();
