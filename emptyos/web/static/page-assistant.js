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

  // Skip on pages whose primary purpose IS chat/assistant — the page already
  // has its own chat UI, so the floating capture+assistant FABs collide with
  // the input bar and add nothing.
  if (/^\/(assistant|agent|voice-assistant)(\/|$)/.test(location.pathname)) return;

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
      '.pa-msg.user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px}',
      '.pa-msg.assistant{align-self:flex-start;background:var(--bg-card);border:1px solid var(--border);color:var(--text);border-bottom-left-radius:4px}',
      '.pa-msg.assistant strong{color:var(--text-heading)}.pa-msg.assistant code{background:color-mix(in srgb,var(--accent) 12%,transparent);padding:1px 4px;border-radius:3px;font-size:12px}',
      '.pa-action-btn{display:inline-block;margin:4px 4px 4px 0;padding:6px 14px;border-radius:8px;border:1px solid var(--accent);background:color-mix(in srgb,var(--accent) 10%,transparent);color:var(--accent);font-size:12px;font-weight:600;cursor:pointer;transition:all 0.15s}',
      '.pa-action-btn:hover{background:var(--accent);color:#fff}',
      '.pa-action-done{opacity:0.5;pointer-events:none;border-style:dashed}',
      '.pa-action-result{font-size:11px;color:var(--success);margin-top:4px}',
      '.pa-input-row{padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:8px;flex-shrink:0;padding-bottom:calc(12px + env(safe-area-inset-bottom,0px))}',
      '.pa-input{flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:10px;background:var(--bg-card);color:var(--text);font-size:14px;outline:none;font-family:inherit;resize:none}',
      '.pa-input:focus{border-color:var(--accent)}',
      '.pa-send{padding:10px 16px;border-radius:10px;background:var(--accent);color:#fff;border:none;font-size:14px;font-weight:600;cursor:pointer;flex-shrink:0}.pa-send:disabled{opacity:0.4}',
      '.pa-quick{display:flex;flex-wrap:wrap;gap:6px;padding:8px 16px;flex-shrink:0}',
      '.pa-quick-btn{padding:6px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text-secondary);font-size:11px;cursor:pointer;white-space:nowrap;flex-shrink:0;transition:all 0.15s}.pa-quick-btn:hover{border-color:var(--accent);color:var(--accent)}',
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
      '.cap-send{margin-top:10px;width:100%;padding:10px;border-radius:10px;background:var(--accent);color:#fff;border:none;font-size:14px;font-weight:600;cursor:pointer}.cap-send:disabled{opacity:0.4}',
      '@media(max-width:500px){.pa-drawer{width:100vw}}',
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
    drawer.innerHTML = [
      '<div class="pa-header">',
      '  <span class="pa-title">AI Assistant</span>',
      '  <span style="font-size:11px;color:var(--text-muted);background:var(--bg-card);padding:2px 8px;border-radius:6px">' + _esc(appId) + '</span>',
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
      if (btn && btn.dataset.msg) send(btn.dataset.msg);
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
    document.querySelector('.pa-drawer').classList.toggle('open', isOpen);
    if (isOpen) {
      _loadCaptures();
      setTimeout(function() { document.getElementById('__pa_input').focus(); }, 250);
    }
  }

  function appendMsg(role, text) {
    var msgs = document.getElementById('__pa_msgs');
    var div = document.createElement('div');
    div.className = 'pa-msg ' + role;
    div.innerHTML = role === 'assistant' ? renderResponse(text) : _esc(text);
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
  }

  // --- Render AI response: markdown + ACTION buttons ---
  function renderResponse(text) {
    // Parse [ACTION:name(param)] → execute immediately
    // Parse [BUTTON:label|action(param)] → render clickable button
    var html = _renderMd(text);

    // Replace [BUTTON:label|action(param)] with clickable buttons
    html = html.replace(/\[BUTTON:([^|]+)\|(\w+)\(([^)]*)\)\]/g, function(_, label, action, param) {
      var id = 'pa-btn-' + (++_btnCounter);
      return '<button class="pa-action-btn" id="' + id + '" onclick="window.__paExec(\'' + _escAttr(action) + '\',\'' + _escAttr(param) + '\',this)">' + _esc(label) + '</button>';
    });

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

  function _renderMd(text) {
    return _esc(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br>');
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
    EOS.post('/quick-action/api/add', body).then(function() {
      btn.disabled = false;
      btn.textContent = 'Capture';
      _capToggle();
      _capSelectedTag = '';
      document.querySelectorAll('.cap-tag').forEach(function(t) { t.classList.remove('active'); });
      if (window.EOS_UI) EOS_UI.toast('Captured');
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

  function _esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
  function _escAttr(s) { return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
})();
