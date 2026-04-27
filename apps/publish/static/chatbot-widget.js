/* EmptyOS chatbot widget — drop-in floating bubble for sites built by the
 * publish app. Self-contained: no framework, no external CSS file beyond the
 * site's own theme variables.
 *
 * The widget reads three meta tags from <head>:
 *   <meta name="chatbot-endpoint" content="https://chat.binbian.net">
 *   <meta name="chatbot-site-id"  content="eos">
 *   <meta name="chatbot-starters" content='["Q1","Q2","Q3"]'>     (optional)
 *
 * Theming: pulls --bg-card, --border, --text, --text-heading, --accent,
 * --accent-bg from the site's stylesheet so dark/light/nord/etc. all match.
 */
(function () {
  'use strict';

  const meta = (name) => {
    const el = document.querySelector(`meta[name="${name}"]`);
    return el ? el.getAttribute('content') : '';
  };

  const ENDPOINT = meta('chatbot-endpoint').replace(/\/$/, '');
  const SITE_ID = meta('chatbot-site-id');
  if (!ENDPOINT || !SITE_ID) {
    console.warn('[chatbot] missing chatbot-endpoint or chatbot-site-id meta tags');
    return;
  }

  let starters = [];
  try { starters = JSON.parse(meta('chatbot-starters') || '[]'); } catch (_) {}

  // Persistent identifiers
  const SID_KEY = 'eos.chat.sid';
  const HIST_KEY = 'eos.chat.hist';
  let sessionId = localStorage.getItem(SID_KEY);
  if (!sessionId) {
    sessionId = crypto.randomUUID ? crypto.randomUUID()
      : 'sid-' + Math.random().toString(36).slice(2);
    localStorage.setItem(SID_KEY, sessionId);
  }

  // Conversation history lives in sessionStorage — gone when tab closes
  // (privacy default). Named `convo` (not `history`) so we don't shadow
  // the global `window.history` used for replaceState below.
  let convo = [];
  try { convo = JSON.parse(sessionStorage.getItem(HIST_KEY) || '[]'); } catch (_) {}
  const saveConvo = () => sessionStorage.setItem(HIST_KEY, JSON.stringify(convo));

  // ── Style injection ──────────────────────────────────────────────
  const STYLE = `
    .eos-chat-fab {
      position: fixed; bottom: 20px; right: 20px; z-index: 9000;
      width: 52px; height: 52px; border-radius: 50%; border: none;
      background: var(--accent, #6c5ce7); color: #fff; cursor: pointer;
      box-shadow: 0 6px 20px rgba(0,0,0,0.18);
      display: flex; align-items: center; justify-content: center;
      font-size: 22px; transition: transform 0.15s, box-shadow 0.15s;
    }
    .eos-chat-fab:hover { transform: translateY(-2px); box-shadow: 0 10px 28px rgba(0,0,0,0.22); }
    .eos-chat-panel {
      position: fixed; bottom: 84px; right: 20px; z-index: 9000;
      width: 380px; max-width: calc(100vw - 40px); height: 520px;
      max-height: calc(100vh - 120px);
      background: var(--bg, #fff); color: var(--text, #222);
      border: 1px solid var(--border, rgba(0,0,0,0.1));
      border-radius: 14px; box-shadow: 0 12px 36px rgba(0,0,0,0.18);
      display: none; flex-direction: column; overflow: hidden;
      font-family: var(--font, system-ui, sans-serif);
    }
    .eos-chat-panel.open { display: flex; }
    .eos-chat-head {
      padding: 12px 14px; border-bottom: 1px solid var(--border, rgba(0,0,0,0.08));
      display: flex; align-items: center; justify-content: space-between;
      font-size: 0.95rem; color: var(--text-heading, #111); font-weight: 600;
    }
    .eos-chat-close {
      background: none; border: none; cursor: pointer; font-size: 18px;
      color: var(--text-secondary, #666); padding: 4px 8px;
    }
    .eos-chat-close:hover { color: var(--text-heading, #111); }
    .eos-chat-body {
      flex: 1; overflow-y: auto; padding: 14px; font-size: 0.9rem; line-height: 1.55;
    }
    .eos-chat-msg {
      margin-bottom: 12px; padding: 10px 12px; border-radius: 12px;
      max-width: 90%; word-wrap: break-word;
    }
    .eos-chat-msg.user {
      background: var(--accent-bg, rgba(108,92,231,0.08));
      margin-left: auto; border-bottom-right-radius: 4px;
    }
    .eos-chat-msg.bot {
      background: var(--bg-card, rgba(0,0,0,0.04));
      margin-right: auto; border-bottom-left-radius: 4px;
    }
    .eos-chat-msg a {
      color: var(--accent, #6c5ce7); text-decoration: underline;
      text-underline-offset: 2px;
    }
    .eos-chat-sources {
      display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px;
    }
    .eos-chat-source-chip {
      font-size: 0.72rem; padding: 4px 8px; border-radius: 999px;
      background: var(--bg-card, rgba(0,0,0,0.04));
      border: 1px solid var(--border, rgba(0,0,0,0.08));
      color: var(--text-secondary, #555); cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
    }
    .eos-chat-source-chip:hover {
      background: var(--accent-bg, rgba(108,92,231,0.08));
      border-color: var(--accent, #6c5ce7); color: var(--accent, #6c5ce7);
    }
    .eos-chat-meta-badge {
      font-size: 0.65rem; padding: 2px 6px; border-radius: 4px;
      background: var(--bg-card, rgba(0,0,0,0.04));
      color: var(--text-muted, #888); margin-left: 6px;
      vertical-align: middle; letter-spacing: 0.02em; text-transform: uppercase;
    }
    .eos-chat-starters {
      display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 14px 0;
    }
    .eos-chat-starter {
      font-size: 0.78rem; padding: 6px 10px; border-radius: 999px;
      background: var(--bg-card, rgba(0,0,0,0.04));
      border: 1px solid var(--border, rgba(0,0,0,0.1));
      color: var(--text, #222); cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .eos-chat-starter:hover {
      background: var(--accent, #6c5ce7); color: #fff;
      border-color: var(--accent, #6c5ce7);
    }
    .eos-chat-input-wrap {
      border-top: 1px solid var(--border, rgba(0,0,0,0.08));
      padding: 10px 12px; display: flex; gap: 8px; align-items: center;
    }
    .eos-chat-input {
      flex: 1; padding: 8px 10px; font-size: 0.9rem;
      background: var(--bg-input, var(--bg-card, #f8f8f8));
      border: 1px solid var(--border, rgba(0,0,0,0.1));
      border-radius: 8px; color: var(--text, #222);
      font-family: inherit;
    }
    .eos-chat-input:focus {
      outline: none; border-color: var(--accent, #6c5ce7);
    }
    .eos-chat-send {
      background: var(--accent, #6c5ce7); color: #fff; border: none;
      padding: 8px 14px; border-radius: 8px; cursor: pointer;
      font-size: 0.85rem; font-weight: 500;
    }
    .eos-chat-send:disabled { opacity: 0.5; cursor: not-allowed; }
    .eos-chat-empty {
      color: var(--text-muted, #888); font-size: 0.85rem;
      text-align: center; padding: 24px 14px;
    }
    .eos-chat-flash {
      animation: eos-chat-flash-anim 1.6s ease-out;
    }
    @keyframes eos-chat-flash-anim {
      0% { background-color: var(--accent-bg, rgba(108,92,231,0.18)); }
      100% { background-color: transparent; }
    }
  `;
  const styleEl = document.createElement('style');
  styleEl.textContent = STYLE;
  document.head.appendChild(styleEl);

  // ── DOM ─────────────────────────────────────────────────────────
  const fab = document.createElement('button');
  fab.className = 'eos-chat-fab';
  fab.setAttribute('aria-label', 'Open chat');
  fab.textContent = '💬';

  const panel = document.createElement('div');
  panel.className = 'eos-chat-panel';
  panel.innerHTML = `
    <div class="eos-chat-head">
      <span>Ask about this site</span>
      <button class="eos-chat-close" aria-label="Close chat">✕</button>
    </div>
    <div class="eos-chat-starters" hidden></div>
    <div class="eos-chat-body"></div>
    <div class="eos-chat-input-wrap">
      <input class="eos-chat-input" type="text" placeholder="Ask a question..." maxlength="2000">
      <button class="eos-chat-send">Send</button>
    </div>
  `;

  document.body.appendChild(fab);
  document.body.appendChild(panel);

  const $body = panel.querySelector('.eos-chat-body');
  const $input = panel.querySelector('.eos-chat-input');
  const $send = panel.querySelector('.eos-chat-send');
  const $startersWrap = panel.querySelector('.eos-chat-starters');
  const $close = panel.querySelector('.eos-chat-close');

  // ── Rendering ───────────────────────────────────────────────────
  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function renderMarkdown(text) {
    // Tiny markdown subset: links [text](url), **bold**, `code`. Everything
    // else is escaped.
    let s = escapeHtml(text);
    // Restore link syntax that escapeHtml mangled — we operate on the
    // pre-escaped text instead. Re-do: escape, then re-introduce links.
    // Simpler: do replacements on the raw, then escape only outside matches.
    const raw = text;
    const out = [];
    let i = 0;
    const linkRe = /\[([^\]]+)\]\(([^)]+)\)/g;
    let m;
    while ((m = linkRe.exec(raw)) !== null) {
      out.push(escapeHtml(raw.slice(i, m.index)));
      const label = escapeHtml(m[1]);
      const url = escapeHtml(m[2]);
      out.push(`<a href="${url}" data-eos-chat-link="1">${label}</a>`);
      i = m.index + m[0].length;
    }
    out.push(escapeHtml(raw.slice(i)));
    let html = out.join('');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\n/g, '<br>');
    return html;
  }

  function renderStarters() {
    if (!starters.length || convo.length > 0) {
      $startersWrap.hidden = true;
      return;
    }
    $startersWrap.hidden = false;
    $startersWrap.innerHTML = starters.map(q =>
      `<button class="eos-chat-starter">${escapeHtml(q)}</button>`
    ).join('');
    $startersWrap.querySelectorAll('.eos-chat-starter').forEach(btn => {
      btn.addEventListener('click', () => {
        $input.value = btn.textContent;
        send();
      });
    });
  }

  function renderHistory() {
    if (!convo.length) {
      $body.innerHTML = '<div class="eos-chat-empty">Ask a question about this site to get started.</div>';
      return;
    }
    $body.innerHTML = '';
    for (const turn of convo) {
      appendMessage(turn.role, turn.content, turn.sources, turn.source);
    }
  }

  function appendMessage(role, text, sources, source) {
    const empty = $body.querySelector('.eos-chat-empty');
    if (empty) empty.remove();

    const div = document.createElement('div');
    div.className = 'eos-chat-msg ' + (role === 'user' ? 'user' : 'bot');
    div.innerHTML = renderMarkdown(text || '');

    if (role !== 'user' && source && source !== 'model') {
      const badge = document.createElement('span');
      badge.className = 'eos-chat-meta-badge';
      badge.textContent = source;
      div.appendChild(badge);
    }

    if (role !== 'user' && sources && sources.length) {
      const wrap = document.createElement('div');
      wrap.className = 'eos-chat-sources';
      for (const src of sources) {
        const chip = document.createElement('span');
        chip.className = 'eos-chat-source-chip';
        const label = src.section ? `${src.title} → ${src.section}` : src.title;
        chip.textContent = '📄 ' + label;
        chip.addEventListener('click', () => navigateToSource(src));
        wrap.appendChild(chip);
      }
      div.appendChild(wrap);
    }

    $body.appendChild(div);
    $body.scrollTop = $body.scrollHeight;
    return div;
  }

  // ── Link-aware navigation ────────────────────────────────────────
  function navigateToSource(src) {
    if (!src || !src.url) return;
    let target;
    try { target = new URL(src.url, location.origin); }
    catch (_) { return; }

    const samePage = target.pathname === location.pathname;
    const portfolioSpa = location.pathname === '/' && target.pathname === '/';

    if ((samePage || portfolioSpa) && target.hash) {
      const el = document.querySelector(target.hash);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        flashHighlight(el);
        if (window.history && window.history.replaceState) {
          window.history.replaceState(null, '', target.hash);
        }
        return;
      }
      // Fall through: SPA might not have rendered yet — let hashchange fire.
      location.hash = target.hash;
      return;
    }
    // Different page (or same path with no hash) — full navigate.
    location.href = target.href;
  }

  function flashHighlight(el) {
    el.classList.remove('eos-chat-flash');
    // Force reflow so the animation restarts when re-clicking the same chip.
    void el.offsetWidth;
    el.classList.add('eos-chat-flash');
  }

  // Intercept inline markdown link clicks too.
  $body.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-eos-chat-link]');
    if (!a) return;
    e.preventDefault();
    navigateToSource({ url: a.getAttribute('href') });
  });

  // ── Sending ──────────────────────────────────────────────────────
  let busy = false;

  async function send() {
    if (busy) return;
    const text = $input.value.trim();
    if (!text) return;
    $input.value = '';

    convo.push({ role: 'user', content: text });
    saveConvo();
    appendMessage('user', text);
    $startersWrap.hidden = true;

    const botDiv = appendMessage('bot', '...', null, null);
    busy = true;
    $send.disabled = true;

    let acc = '';
    let sources = null;
    let sourceTag = 'live';

    try {
      const resp = await fetch(`${ENDPOINT}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          site_id: SITE_ID,
          messages: convo.filter(h => h.role === 'user' || h.role === 'assistant')
            .map(h => ({ role: h.role, content: h.content })),
          session_id: sessionId,
        }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        botDiv.innerHTML = renderMarkdown(
          err.error
            ? `Sorry — ${err.error}`
            : (resp.status === 429
              ? 'Too many questions in a short window. Please try again later.'
              : 'Something went wrong. Please try again.')
        );
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      botDiv.textContent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const evt = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 2);
          if (!evt.startsWith('data:')) continue;
          const payload = evt.slice(5).trim();
          let parsed;
          try { parsed = JSON.parse(payload); } catch (_) { continue; }
          if (parsed.delta) {
            acc += parsed.delta;
            botDiv.innerHTML = renderMarkdown(acc);
            $body.scrollTop = $body.scrollHeight;
          }
          if (parsed.done) {
            sources = parsed.sources || null;
            sourceTag = parsed.source || 'live';
            // Service post-processed the full reply (sources block stripped) —
            // prefer it over the locally-accumulated stream so the user never
            // sees a fragment of "---SOURCES---" if marker detection lagged.
            if (typeof parsed.clean_reply === 'string') {
              acc = parsed.clean_reply;
            }
          }
          if (parsed.error) {
            botDiv.innerHTML = renderMarkdown(`Sorry — ${parsed.error}`);
          }
        }
      }
    } catch (e) {
      console.error('[chatbot]', e);
      botDiv.innerHTML = renderMarkdown('Network error. Please try again.');
    } finally {
      busy = false;
      $send.disabled = false;
    }

    // Re-render the bot message with sources + badge once streaming is done.
    if (acc) {
      botDiv.remove();
      appendMessage('bot', acc, sources, sourceTag);
      convo.push({ role: 'assistant', content: acc, sources, source: sourceTag });
      saveConvo();
    }
  }

  // ── UI wiring ────────────────────────────────────────────────────
  fab.addEventListener('click', () => {
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) {
      renderHistory();
      renderStarters();
      setTimeout(() => $input.focus(), 50);
    }
  });
  $close.addEventListener('click', () => panel.classList.remove('open'));
  $send.addEventListener('click', send);
  $input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
})();
