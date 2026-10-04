// voice-assistant -- page logic, extracted verbatim from pages/index.html (P4 Atomic
// split, .claude/rules/multi-module-apps.md frontend pattern). Loaded at the
// same position as the old inline <script>, so global scope and load order
// vs eos.js / eos-components.js are unchanged.
  // ── Capability gap check — surface missing listen/think/speak at boot ─
  (async function checkCapabilities() {
    try {
      const r = await fetch('/api/capabilities/full');
      if (!r.ok) return;
      const data = await r.json();
      const need = ['listen', 'think', 'speak'];
      const missing = need.filter(c => {
        const cap = data.capabilities && data.capabilities[c];
        return !cap || !cap.active;
      });
      if (!missing.length) return;
      const banner = document.getElementById('cap-gap-banner');
      const msg = document.getElementById('cap-gap-msg');
      const fix = document.getElementById('cap-gap-fix');
      if (!banner) return;
      const labels = { listen: 'voice input', think: 'a language model', speak: 'voice output' };
      const human = missing.map(c => labels[c] || c).join(', ');
      msg.textContent = 'Aura needs ' + human + ' before she can run end-to-end.';
      fix.href = '/system?capability=' + encodeURIComponent(missing[0]);
      banner.classList.add('show');
    } catch (e) { /* silent — banner stays hidden */ }
  })();

  // ── Element refs ────────────────────────────────────────────────────
  const body = document.body;
  const recBtn = document.getElementById('rec-btn');
  const recHint = document.getElementById('rec-hint');
  const transcriptEl = document.getElementById('transcript');
  const transcriptWrap = transcriptEl.parentElement;
  const scopeCanvas = document.getElementById('scope-canvas');
  const scopeLvl = document.getElementById('scope-lvl');
  const brandCompanion = document.getElementById('brand-companion');
  const brandTime = document.getElementById('brand-time');
  const railDay = document.getElementById('rail-day');
  const railDate = document.getElementById('rail-date');
  const railCompanion = document.getElementById('rail-companion');
  const picker = document.getElementById('picker');
  const pickerList = document.getElementById('picker-list');

  function escAura(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function getCSS(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // ── Clock + date ─────────────────────────────────────────────────────
  function refreshClock() {
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    brandTime.textContent = `${hh}:${mm}:${ss}`;
    railDay.textContent = d.toLocaleDateString(undefined, {weekday: 'short'}).toUpperCase();
    railDate.textContent = d.toLocaleDateString(undefined, {month: 'short', day: '2-digit'}).toUpperCase();
  }
  refreshClock();
  setInterval(refreshClock, 1000);

  // ── State driver ────────────────────────────────────────────────────
  function setState(state) {
    body.className = body.className.replace(/state-\w+/g, '').trim();
    body.classList.add(`state-${state}`);
    if (state === 'idle') { stopWaveform(); scopeLvl.textContent = '—'; recHint.textContent = 'tap to talk'; }
    else if (state === 'listening') { recHint.textContent = 'recording'; }
    else if (state === 'thinking')  { drawThinkingPulse(); recHint.textContent = 'thinking'; scopeLvl.textContent = '—'; }
    else if (state === 'speaking')  { recHint.textContent = 'speaking'; }
  }

  // ── Transcript / turns ──────────────────────────────────────────────
  let currentTurnEl = null;
  function fmtTurnTs() {
    const d = new Date();
    return String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
  }
  // BYOK header injector — picks up keys from EOS.byok (set by Settings →
  // Demo). Both chat_text and chat_stream use raw fetch (multipart for
  // audio, JSON for text), so EOS.api's auto-injection doesn't fire here;
  // we add the headers explicitly.
  function _byokHeaders() {
    if (!window.EOS || !EOS.byok) return {};
    var keys = EOS.byok.list();
    var headers = {};
    if (keys.openai) headers['X-User-OpenAI-Key'] = keys.openai;
    if (keys.anthropic) headers['X-User-Anthropic-Key'] = keys.anthropic;
    return headers;
  }

  // Tracks user-text we've just rendered locally (timestamp ms). The user_text
  // event handler consults this to avoid double-rendering when both the
  // optimistic local append and a server echo fire for the same text.
  var _recentUserTexts = [];
  function rememberUserText(text) {
    _recentUserTexts.push({text: text.trim(), at: Date.now()});
    // Keep only the last 5 / past 5s
    var cutoff = Date.now() - 5000;
    _recentUserTexts = _recentUserTexts.filter(function(e){ return e.at > cutoff; }).slice(-5);
  }
  function recentlyRenderedUserText(text) {
    var t = (text || '').trim();
    var cutoff = Date.now() - 2000;
    return _recentUserTexts.some(function(e){ return e.text === t && e.at > cutoff; });
  }

  function appendUserTurn(text) {
    rememberUserText(text);
    const el = document.createElement('div');
    el.className = 'turn turn-user';
    el.innerHTML = `<div class="turn-ts">${fmtTurnTs()}</div>
      <div class="turn-body"><span class="turn-role">You</span>${escAura(text)}</div>`;
    transcriptEl.appendChild(el);

    const aura = document.createElement('div');
    aura.className = 'turn turn-aura streaming';
    aura.innerHTML = `<div class="turn-ts">${fmtTurnTs()}</div>
      <div class="turn-body"><span class="turn-role">Aura</span><span class="aura-text"></span></div>`;
    transcriptEl.appendChild(aura);
    currentTurnEl = aura;
    scrollToBottom();
    return aura;
  }
  // Each in-flight stream binds to its OWN aura bubble (passed as `el`) so a
  // second send while the first reply is still streaming can't steal the
  // first stream's deltas. `currentTurnEl` is only a "most-recent turn" handle
  // for the post-stream confirm-gate path; never trust it for live deltas.
  function appendAuraDelta(delta, el) {
    const target = el || currentTurnEl;
    if (!target) return;
    const t = target.querySelector('.aura-text');
    if (!t) return;
    t.textContent = (t.textContent || '') + delta;
    scrollToBottom();
  }
  function finishAuraTurn(el) {
    const target = el || currentTurnEl;
    if (target) target.classList.remove('streaming');
    // Only release the global handle if this finishing stream actually owns
    // it — otherwise a slow earlier stream would null out a newer turn.
    if (currentTurnEl === target) currentTurnEl = null;
  }
  function scrollToBottom() {
    transcriptWrap.scrollTop = transcriptWrap.scrollHeight;
  }

  // Inline patch annotations (replaces right-column trace)
  function pushPatch(text, kind) {
    const el = document.createElement('div');
    el.className = 'patch';
    const line = document.createElement('div');
    line.className = 'patch-line ' + (kind || '');
    line.innerHTML = text;
    el.appendChild(line);
    transcriptEl.appendChild(el);
    scrollToBottom();
    return el;
  }
  function pushTrace(html, kind) { return pushPatch(html, kind); }

  // Pre-turn orient preamble — italic single line shown while the main
  // reply is still spinning up. Just a "what I'm about to do" hint.
  function pushOrient(text) {
    if (!text) return;
    const el = document.createElement('div');
    el.className = 'orient';
    el.textContent = text;
    transcriptEl.appendChild(el);
    scrollToBottom();
  }

  // ── Card renderers (inline in transcript) ───────────────────────────
  const CARD_RENDERERS = {
    'stat-tile': function(data) {
      const items = Array.isArray(data) ? data : [data];
      return items.map(it => `
        <div class="aura-card-stat">
          <span class="aura-card-stat-value">${escAura(it.value)}</span>
          <span class="aura-card-stat-label">${escAura(it.label || '')}</span>
        </div>`).join('');
    },
    'entity-card': function(data) {
      const fields = (data.fields || []).map(f =>
        `<span><strong>${escAura(f.label)}</strong> ${escAura(f.value)}</span>`
      ).join('');
      return `
        <div class="aura-card-entity-title">${escAura(data.title || '')}</div>
        ${data.subtitle ? `<div class="aura-card-entity-subtitle">${escAura(data.subtitle)}</div>` : ''}
        ${fields ? `<div class="aura-card-entity-fields">${fields}</div>` : ''}`;
    },
    'task-list': function(data) {
      const rows = Array.isArray(data) ? data : [];
      if (!rows.length) return '<div class="aura-card-stat-label">No tasks.</div>';
      return rows.map(r => {
        const tone = (r.tone || '').toLowerCase();
        const tag = r.tag ? `<span class="aura-card-task-tag ${tone}">${escAura(r.tag)}</span>` : '';
        const cls = r.done ? 'done' : '';
        return `<div class="aura-card-task-row ${cls}"><span>${escAura(r.text)}</span>${tag}</div>`;
      }).join('');
    },
    'memory-list': function(data) {
      const rows = Array.isArray(data) ? data : [];
      if (!rows.length) return '<div class="aura-card-stat-label">Nothing remembered.</div>';
      return rows.map(r => {
        const kind = (r.kind || 'user').toLowerCase();
        const date = (r.created || '').slice(0, 10);
        return `<div class="memory-row">
          <span class="memory-kind kind-${kind}">${escAura(kind)}</span>
          <span class="memory-body">${escAura(r.body || '')}</span>
          ${date ? `<span class="memory-date">${escAura(date)}</span>` : ''}
        </div>`;
      }).join('');
    },
    'research-result': function(data) {
      const report = (data && data.report) || '';
      const sources = (data && data.sources) || [];
      // Render `[n]` markers in the body as clickable links to the matching source.
      const sourceByN = {};
      sources.forEach(s => { sourceByN[String(s.n)] = s; });
      const reportHtml = escAura(report).replace(/\[(\d+)\]/g, (m, n) => {
        const s = sourceByN[n];
        if (!s) return m;
        return `<a class="research-cite" href="${escAura(s.url)}" target="_blank" rel="noopener">[${n}]</a>`;
      }).replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>');
      const srcRows = sources.map(s =>
        `<div class="research-source"><span class="research-n">[${s.n}]</span>
          <a href="${escAura(s.url)}" target="_blank" rel="noopener">${escAura(s.title)}</a>
          <span class="research-site">${escAura((new URL(s.url)).hostname || '')}</span>
        </div>`
      ).join('');
      return `
        <div class="research-body"><p>${reportHtml}</p></div>
        ${srcRows ? `<div class="research-sources">${srcRows}</div>` : ''}`;
    },
  };
  function renderCard(event) {
    const fn = CARD_RENDERERS[event.renderer];
    if (!fn) { console.warn('Unknown card renderer:', event.renderer); return; }
    const card = document.createElement('div');
    card.className = 'aura-card';
    card.innerHTML = (event.title ? `<div class="aura-card-title">${escAura(event.title)}</div>` : '') + fn(event.data);
    transcriptEl.appendChild(card);
    scrollToBottom();
    requestAnimationFrame(() => card.classList.add('show'));
  }

  // Post-intent inline link — "go check what just got created". Speak-side
  // ignores it (TTS won't read URLs); the visible transcript renders a small
  // clickable chip alongside the spoken confirmation. Emitted as
  // {type:'link', intent, text, href} from chat_pipeline.
  function renderLink(event) {
    if (!event || !event.href) return;
    const chip = document.createElement('a');
    chip.className = 'aura-link-chip';
    chip.href = event.href;
    chip.textContent = (event.text || 'Open') + ' →';
    transcriptEl.appendChild(chip);
    scrollToBottom();
    requestAnimationFrame(() => chip.classList.add('show'));
  }

  // Pending-action card (risk-gated intent awaiting Apply / Reject).
  // Emitted as {type:'pending_action', action_id, verb, args, description}
  // when an intent marked risk=high fires and no autopilot grant covers it.
  function renderPendingAction(event) {
    const card = document.createElement('div');
    card.className = 'aura-card pending-action';
    card.dataset.actionId = event.action_id;
    const argsBlock = event.args && Object.keys(event.args).length
      ? `<pre class="pending-args">${escAura(JSON.stringify(event.args, null, 2))}</pre>`
      : '';
    card.innerHTML = `
      <div class="pending-head">⚠ Review action</div>
      <div class="pending-verb"><code>${escAura(event.verb)}</code></div>
      ${event.description ? `<div class="pending-desc">${escAura(event.description)}</div>` : ''}
      ${argsBlock}
      <div class="pending-actions">
        <button class="pending-apply">Apply</button>
        <button class="pending-reject">Reject</button>
      </div>
      <div class="pending-status"></div>`;
    transcriptEl.appendChild(card);
    scrollToBottom();
    requestAnimationFrame(() => card.classList.add('show'));

    const status = card.querySelector('.pending-status');
    const applyBtn = card.querySelector('.pending-apply');
    const rejectBtn = card.querySelector('.pending-reject');
    async function resolve(action) {
      applyBtn.disabled = true; rejectBtn.disabled = true;
      status.textContent = action === 'apply' ? 'Applying...' : 'Rejecting...';
      try {
        const res = await fetch(`/voice-assistant/api/pending/${event.action_id}/${action}`, {method: 'POST'});
        const data = await res.json();
        if (data.error) {
          status.textContent = 'failed: ' + data.error;
          status.className = 'pending-status err';
          applyBtn.disabled = false; rejectBtn.disabled = false;
        } else {
          status.textContent = action === 'apply' ? '✓ applied' : '✗ rejected';
          status.className = 'pending-status ok';
          card.classList.add('resolved');
        }
      } catch (e) {
        status.textContent = 'network error';
        status.className = 'pending-status err';
        applyBtn.disabled = false; rejectBtn.disabled = false;
      }
    }
    applyBtn.addEventListener('click', () => resolve('apply'));
    rejectBtn.addEventListener('click', () => resolve('reject'));
  }

  // ── Oscilloscope ────────────────────────────────────────────────────
  const scopeCtx = scopeCanvas.getContext('2d');
  let scopeRAF = null;
  let frameTrail = [];   // last N frames for phosphor decay

  // LITE mode — phones, coarse-pointer, reduced-motion, or low-core devices.
  // The phosphor scope's per-frame shadowBlur + multi-line trail + DPR-scaled
  // backing store saturate a mobile GPU and jank the whole interaction (it
  // animates through listening/thinking/speaking — the entire active session).
  // In LITE we drop shadowBlur, shorten the trail, cap the backing-store DPR,
  // and throttle the loops to ~30fps. Desktop keeps the full effect.
  const LITE = (function () {
    try {
      const mm = window.matchMedia;
      if (mm) {
        if (mm('(prefers-reduced-motion: reduce)').matches) return true;
        if (mm('(max-width: 720px)').matches) return true;
        if (mm('(pointer: coarse)').matches) return true;
      }
      if ((navigator.hardwareConcurrency || 8) <= 4) return true;
    } catch (e) { /* matchMedia unavailable — assume full */ }
    return false;
  })();
  const TRAIL_LEN = LITE ? 3 : 10;

  // ~30fps gate for the animation loops in LITE mode (halves draw work).
  let _scopeLastDraw = 0;
  function scopeShouldDraw(now) {
    now = now || performance.now();
    if (now - _scopeLastDraw < 32) return false;
    _scopeLastDraw = now;
    return true;
  }

  function resizeScope() {
    // Cap the backing store to 1× on mobile — a 3× DPR fill every frame is the
    // bulk of the cost, and the scope is decorative.
    const dpr = Math.min(window.devicePixelRatio || 1, LITE ? 1 : 2);
    scopeCanvas.width  = scopeCanvas.clientWidth  * dpr;
    scopeCanvas.height = scopeCanvas.clientHeight * dpr;
    scopeCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', resizeScope);
  setTimeout(resizeScope, 0);

  function drawGraticule() {
    const W = scopeCanvas.clientWidth, H = scopeCanvas.clientHeight;
    scopeCtx.clearRect(0, 0, W, H);
    scopeCtx.strokeStyle = 'rgba(107, 96, 82, 0.18)';
    scopeCtx.lineWidth = 1;
    const step = 24;
    scopeCtx.beginPath();
    for (let x = step; x < W; x += step) { scopeCtx.moveTo(x + 0.5, 0); scopeCtx.lineTo(x + 0.5, H); }
    for (let y = step; y < H; y += step) { scopeCtx.moveTo(0, y + 0.5); scopeCtx.lineTo(W, y + 0.5); }
    scopeCtx.stroke();
    // centre baseline (slightly stronger)
    scopeCtx.strokeStyle = 'rgba(107, 96, 82, 0.32)';
    scopeCtx.beginPath();
    scopeCtx.moveTo(0, H/2 + 0.5); scopeCtx.lineTo(W, H/2 + 0.5);
    scopeCtx.stroke();
  }

  function drawIdleLine() {
    const W = scopeCanvas.clientWidth, H = scopeCanvas.clientHeight;
    drawGraticule();
    scopeCtx.strokeStyle = 'rgba(232, 220, 203, 0.18)';
    scopeCtx.lineWidth = 1;
    scopeCtx.beginPath();
    scopeCtx.moveTo(0, H/2);
    scopeCtx.lineTo(W, H/2);
    scopeCtx.stroke();
  }

  function captureFrame(getData) {
    const buf = getData();
    if (!buf) return null;
    // sample at canvas-width resolution
    const W = scopeCanvas.clientWidth;
    const samples = new Float32Array(W);
    for (let x = 0; x < W; x++) {
      const idx = Math.floor((x / W) * buf.length);
      samples[x] = (buf[idx] - 128) / 128;
    }
    return samples;
  }

  function drawWaveLine(samples, color, alpha) {
    const W = scopeCanvas.clientWidth, H = scopeCanvas.clientHeight;
    scopeCtx.strokeStyle = color;
    scopeCtx.globalAlpha = alpha;
    scopeCtx.lineWidth = 2;
    scopeCtx.lineJoin = 'round';
    scopeCtx.beginPath();
    for (let x = 0; x < W; x++) {
      const y = H/2 + samples[x] * (H * 0.42);
      if (x === 0) scopeCtx.moveTo(x, y);
      else scopeCtx.lineTo(x, y);
    }
    scopeCtx.stroke();
    scopeCtx.globalAlpha = 1;
  }

  function tickPhosphor(getData, color) {
    const samples = captureFrame(getData);
    if (samples) {
      frameTrail.push(samples);
      if (frameTrail.length > TRAIL_LEN) frameTrail.shift();
    }
    drawGraticule();
    // halo (blurred wide) — glow. shadowBlur is the dominant per-frame cost on
    // mobile GPUs, so LITE skips it and uses a plain wide-alpha line instead.
    if (samples) {
      if (LITE) {
        drawWaveLine(samples, color, 0.5);
      } else {
        scopeCtx.save();
        scopeCtx.shadowColor = color;
        scopeCtx.shadowBlur = 12;
        drawWaveLine(samples, color, 0.7);
        scopeCtx.restore();
      }
    }
    // decaying trail
    for (let i = 0; i < frameTrail.length - 1; i++) {
      const a = (i + 1) / frameTrail.length * 0.35;
      drawWaveLine(frameTrail[i], color, a);
    }
    // crisp head
    if (samples) drawWaveLine(samples, color, 1);
  }

  function stopWaveform() {
    if (scopeRAF) cancelAnimationFrame(scopeRAF);
    scopeRAF = null;
    frameTrail = [];
    drawIdleLine();
  }

  function startListeningWave() {
    if (scopeRAF) cancelAnimationFrame(scopeRAF);
    frameTrail = [];
    const buf = new Uint8Array(analyser.fftSize);
    const color = getCSS('--amber') || '#ff8b3d';
    const tick = () => {
      if (!isRecording) return;
      scopeRAF = requestAnimationFrame(tick);
      if (LITE && !scopeShouldDraw()) return;
      analyser.getByteTimeDomainData(buf);
      tickPhosphor(() => buf, color);
      // RMS readout
      let sum = 0;
      for (let i = 0; i < buf.length; i++) { const d = (buf[i]-128); sum += d*d; }
      const rms = Math.sqrt(sum / buf.length);
      const db = rms > 0 ? Math.round(20 * Math.log10(rms / 128)) : -60;
      scopeLvl.textContent = `${db.toString().padStart(3,' ')} dB`;
    };
    tick();
  }

  // TTS analyser is wired lazily on first speak (createMediaElementSource
  // can only be called once per audio element).
  let ttsAnalyser = null;
  function ensureTTSAnalyser() {
    if (ttsAnalyser) return;
    if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
    try {
      const src = audioContext.createMediaElementSource(audioElement);
      ttsAnalyser = audioContext.createAnalyser();
      ttsAnalyser.fftSize = 512;
      src.connect(ttsAnalyser);
      ttsAnalyser.connect(audioContext.destination);
    } catch (e) { console.warn('TTS analyser wiring failed', e); }
  }
  function startSpeakingWave() {
    ensureTTSAnalyser();
    if (!ttsAnalyser) return;
    if (scopeRAF) cancelAnimationFrame(scopeRAF);
    frameTrail = [];
    const buf = new Uint8Array(ttsAnalyser.fftSize);
    const color = getCSS('--teal') || '#5fb3a8';
    const tick = () => {
      if (!body.classList.contains('state-speaking')) return;
      scopeRAF = requestAnimationFrame(tick);
      if (LITE && !scopeShouldDraw()) return;
      ttsAnalyser.getByteTimeDomainData(buf);
      tickPhosphor(() => buf, color);
      scopeLvl.textContent = 'OUT';
    };
    tick();
  }

  function drawThinkingPulse() {
    if (scopeRAF) cancelAnimationFrame(scopeRAF);
    frameTrail = [];
    const start = performance.now();
    const color = getCSS('--amber') || '#ff8b3d';
    const tick = (now) => {
      if (!body.classList.contains('state-thinking')) return;
      scopeRAF = requestAnimationFrame(tick);
      if (LITE && !scopeShouldDraw(now)) return;
      const t = (now - start) / 1000;
      const W = scopeCanvas.clientWidth;
      const samples = new Float32Array(W);
      for (let x = 0; x < W; x++) {
        const phase = (x / W) * Math.PI * 4 - t * 2.4;
        samples[x] = Math.sin(phase) * 0.18;
      }
      drawGraticule();
      if (!LITE) {
        scopeCtx.save();
        scopeCtx.shadowColor = color;
        scopeCtx.shadowBlur = 8;
        drawWaveLine(samples, color, 0.6);
        scopeCtx.restore();
      }
      drawWaveLine(samples, color, 0.95);
    };
    scopeRAF = requestAnimationFrame(tick);
  }

  // ── Recording + VAD ─────────────────────────────────────────────────
  let mediaRecorder, audioChunks = [];
  let isRecording = false;
  let audioContext, analyser, microphone, dataArray, animationId;
  const audioElement = new Audio();
  let messages = [];
  let wakeLock = null;
  let isContinuous = false;
  let vadSilenceStart = 0;
  let vadSpeaking = false;
  let vadSpeechStart = 0;
  let vadThreshold = 5;            // dynamic; recalibrated each recording
  let vadCalibStart = 0;
  let vadCalibSum = 0;
  let vadCalibCount = 0;
  const VAD_THRESHOLD_FLOOR = 3;   // never trust below this — silence isn't truly 0
  const VAD_THRESHOLD_MULT = 2.5;  // speech must be N× the noise floor
  const VAD_CALIB_MS = 400;        // sample noise floor during first N ms
  const VAD_SILENCE_MS = 2500;
  const VAD_MIN_SPEECH_MS = 1500;

  // ── Barge-in (airi borrow, opt-in) ──────────────────────────────────
  // Lets the user interrupt Aura by speaking over the TTS, instead of only
  // Esc / the REC button. OFF by default: browsers have no true AEC, so a
  // live mic during playback can pick up Aura's own voice. Mitigations:
  // (1) echoCancellation+noiseSuppression on the monitor stream, (2) we
  // calibrate the threshold *while TTS is playing* and require speech to be
  // a high multiple of that (TTS-bleed-inclusive) floor, (3) a sustained
  // min-speech window. Server-STT path only (a concurrent mic during the
  // Web-Speech path is unreliable). Enabling on more devices is gated on the
  // manual device matrix in .claude/rules/testing.md.
  let bargeInEnabled = localStorage.getItem('aura.bargeIn') === '1';
  let bargeStream = null, bargeAnalyser = null, bargeData = null, bargeRaf = 0;
  let bargeSpeechStart = 0, bargeThreshold = 0;
  let bargeCalibStart = 0, bargeCalibSum = 0, bargeCalibCount = 0;
  const BARGE_CALIB_MS = 350;
  const BARGE_THRESHOLD_FLOOR = 8;    // higher than VAD floor — resist TTS bleed
  const BARGE_THRESHOLD_MULT = 4.0;   // speech must clearly exceed ambient + TTS
  const BARGE_MIN_SPEECH_MS = 600;    // sustained speech before cutting TTS

  async function startBargeMonitor(onBarge) {
    if (!bargeInEnabled || bargeStream) return;
    try {
      bargeStream = await navigator.mediaDevices.getUserMedia(
        {audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: false}});
      if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
      if (audioContext.state === 'suspended') await audioContext.resume();
      bargeAnalyser = audioContext.createAnalyser();
      bargeAnalyser.fftSize = 512;
      audioContext.createMediaStreamSource(bargeStream).connect(bargeAnalyser);
      bargeData = new Uint8Array(bargeAnalyser.frequencyBinCount);
      bargeSpeechStart = 0; bargeThreshold = BARGE_THRESHOLD_FLOOR;
      bargeCalibStart = Date.now(); bargeCalibSum = 0; bargeCalibCount = 0;
      const tick = () => {
        if (!bargeStream) return;
        bargeAnalyser.getByteTimeDomainData(bargeData);
        const rms = getRMS(bargeData);
        const el = Date.now() - bargeCalibStart;
        if (el < BARGE_CALIB_MS) { bargeCalibSum += rms; bargeCalibCount += 1; }
        else if (bargeCalibCount > 0) {
          bargeThreshold = Math.max(BARGE_THRESHOLD_FLOOR,
            (bargeCalibSum / bargeCalibCount) * BARGE_THRESHOLD_MULT);
          bargeCalibCount = 0;
        }
        if (rms > bargeThreshold) {
          if (!bargeSpeechStart) bargeSpeechStart = Date.now();
          else if (Date.now() - bargeSpeechStart > BARGE_MIN_SPEECH_MS) {
            stopBargeMonitor();
            try { onBarge(); } catch (e) { console.error(e); }
            return;
          }
        } else { bargeSpeechStart = 0; }
        bargeRaf = requestAnimationFrame(tick);
      };
      bargeRaf = requestAnimationFrame(tick);
    } catch (e) { bargeStream = null; }  // mic denied → silently no barge-in
  }

  function stopBargeMonitor() {
    if (bargeRaf) { cancelAnimationFrame(bargeRaf); bargeRaf = 0; }
    if (bargeStream) {
      try { bargeStream.getTracks().forEach(t => t.stop()); } catch (e) {}
      bargeStream = null;
    }
    bargeAnalyser = null; bargeData = null;
  }

  function setBargeIn(on) {
    bargeInEnabled = !!on;
    localStorage.setItem('aura.bargeIn', bargeInEnabled ? '1' : '0');
    if (!bargeInEnabled) stopBargeMonitor();
    const btn = document.getElementById('barge-toggle');
    if (btn) {
      btn.setAttribute('aria-pressed', bargeInEnabled ? 'true' : 'false');
      btn.textContent = bargeInEnabled ? '🎙 barge-in: on' : '🎙 barge-in: off';
    }
  }

  async function acquireWakeLock() {
    if (!('wakeLock' in navigator)) return;
    try {
      if (!wakeLock || wakeLock.released) wakeLock = await navigator.wakeLock.request('screen');
    } catch (err) { console.warn('Wake Lock failed:', err); }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && isRecording) acquireWakeLock();
  });

  function getRMS(data) {
    let sum = 0;
    for (let i = 0; i < data.length; i++) { const d = data[i] - 128; sum += d * d; }
    return Math.sqrt(sum / data.length);
  }

  // Web Speech API path — used when the server's listen capability can only
  // route to a browser-side provider (i.e. demo + most stranger installs that
  // don't run whisper). Skips audio upload entirely: browser captures, browser
  // transcribes, we send the resulting text via /api/chat_text. The server
  // never sees raw audio bytes.
  let webSpeechRecognition = null;

  async function startWebSpeechRecording(continuous) {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      pushTrace('<span>err · Web Speech API not supported in this browser (try Chrome / Edge / Safari)</span>', 'err');
      setState('idle'); isContinuous = false;
      return;
    }
    isContinuous = continuous;
    try {
      webSpeechRecognition = new Recognition();
      webSpeechRecognition.continuous = false;
      webSpeechRecognition.interimResults = false;
      webSpeechRecognition.lang = 'en-US';
      audioElement.pause();  // half-duplex: don't pick up our own TTS
      setState('listening');
      webSpeechRecognition.onresult = (e) => {
        const text = (e.results[0][0].transcript || '').trim();
        webSpeechRecognition = null;
        if (!text) { setState('idle'); return; }
        sendText(text);  // routes to /api/chat_text — same as typing
      };
      webSpeechRecognition.onerror = (e) => {
        webSpeechRecognition = null;
        const msg = e.error || 'speech recognition failed';
        if (msg !== 'no-speech' && msg !== 'aborted') {
          pushTrace(`<span>err · listen: ${escAura(msg)}</span>`, 'err');
        }
        setState('idle'); isContinuous = false;
      };
      webSpeechRecognition.onend = () => {
        // If neither result nor error fired, end is the only signal
        webSpeechRecognition = null;
      };
      webSpeechRecognition.start();
    } catch (err) {
      pushTrace(`<span>err · listen start: ${escAura(err.message || err)}</span>`, 'err');
      setState('idle'); isContinuous = false;
    }
  }

  // Server-side STT path — uploads recorded audio to /api/chat_stream where
  // the daemon transcribes via whisper / openai. Only used on installs that
  // have a server-side STT provider configured.
  async function startMediaRecording(continuous) {
    try {
      isContinuous = continuous;
      const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
      acquireWakeLock();
      if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
      if (audioContext.state === 'suspended') await audioContext.resume();
      analyser = audioContext.createAnalyser();
      microphone = audioContext.createMediaStreamSource(stream);
      microphone.connect(analyser);
      analyser.fftSize = 512;
      dataArray = new Uint8Array(analyser.frequencyBinCount);

      mediaRecorder = new MediaRecorder(stream);
      audioChunks = [];
      mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
      mediaRecorder.onstop = async () => {
        cancelAnimationFrame(animationId);
        microphone.disconnect();
        const audioBlob = new Blob(audioChunks, {type: 'audio/webm'});
        await processAudio(audioBlob);
      };
      mediaRecorder.start(100);
      isRecording = true; vadSpeaking = false; vadSilenceStart = 0; vadSpeechStart = 0;
      vadCalibStart = Date.now(); vadCalibSum = 0; vadCalibCount = 0; vadThreshold = 5;
      setState('listening');
      audioElement.pause();
      resizeScope();
      startListeningWave();
      updateVoiceMeter();
    } catch (err) {
      console.error(err);
      pushTrace(`<span>err · mic blocked: ${escAura(err.message || err)}</span>`, 'err');
      setState('idle');
      isContinuous = false;
    }
  }

  // Decide which path to use based on what the server reports as available.
  // Cached after first probe to avoid an extra HTTP per click.
  let _listenPathCache = null;
  async function getListenPath() {
    if (_listenPathCache) return _listenPathCache;
    try {
      const data = await fetch('/api/health?full=true').then(r => r.json());
      const providers = (data.capabilities || {}).listen || [];
      const serverSide = providers.find(p => p.available && p.name !== 'browser-speech' && p.name !== 'human');
      _listenPathCache = serverSide ? 'server' : 'browser';
    } catch (e) {
      _listenPathCache = 'browser';  // safe default — Web Speech needs no daemon STT
    }
    return _listenPathCache;
  }

  // Re-entrancy latch. `isRecording` only flips true *after* the getUserMedia
  // await inside startMediaRecording, so it alone can't block a second caller
  // that arrives during that window — and a second caller clobbers the global
  // mediaRecorder/microphone, orphaning the in-flight recording (the user's
  // speech is then never delivered). The latch closes that window.
  let recordingArmed = false;

  async function startRecording(continuous = true) {
    if (isRecording || recordingArmed) return;
    recordingArmed = true;
    try {
      const path = await getListenPath();
      if (path === 'server') {
        return await startMediaRecording(continuous);
      }
      return await startWebSpeechRecording(continuous);
    } finally {
      recordingArmed = false;
    }
  }

  function updateVoiceMeter() {
    if (!isRecording) return;
    analyser.getByteTimeDomainData(dataArray);
    const rms = getRMS(dataArray);
    // Calibrate noise floor for the first VAD_CALIB_MS — assumes the user
    // hasn't started speaking yet. Threshold = max(floor, noise × mult).
    const calibElapsed = Date.now() - vadCalibStart;
    if (calibElapsed < VAD_CALIB_MS) {
      vadCalibSum += rms;
      vadCalibCount += 1;
    } else if (vadCalibCount > 0) {
      const noiseFloor = vadCalibSum / vadCalibCount;
      vadThreshold = Math.max(VAD_THRESHOLD_FLOOR, noiseFloor * VAD_THRESHOLD_MULT);
      vadCalibCount = 0;  // mark calibration done
    }

    if (rms > vadThreshold) {
      if (!vadSpeaking) vadSpeechStart = Date.now();
      vadSpeaking = true;
      vadSilenceStart = 0;
    } else if (vadSpeaking) {
      if (vadSilenceStart === 0) vadSilenceStart = Date.now();
      else if (Date.now() - vadSilenceStart > VAD_SILENCE_MS &&
               Date.now() - vadSpeechStart > VAD_MIN_SPEECH_MS) {
        vadSpeaking = false;
        stopRecording();
      }
    }
    animationId = requestAnimationFrame(updateVoiceMeter);
  }

  function stopRecording() {
    if (webSpeechRecognition) {
      try { webSpeechRecognition.stop(); } catch (e) {}
      // onresult / onend handlers will transition state from here
      return;
    }
    if (mediaRecorder && isRecording) {
      mediaRecorder.stop();
      mediaRecorder.stream.getTracks().forEach(track => track.stop());
      isRecording = false;
      setState('thinking');
    }
  }

  // ── NDJSON event-stream consumer (shared by audio + text) ────────
  async function consumeStream(res, opts) {
    opts = opts || {};
    const onDoneIdle = opts.onDoneIdle || (() => setState('idle'));
    const idle = () => { stopBargeMonitor(); onDoneIdle(); };
    // This stream's own aura bubble. Text path passes it in (eagerly created
    // by sendText); audio path creates it lazily on the `user_text` event.
    // Never read the module-global currentTurnEl here — overlapping streams
    // would collide on it (the empty-first-bubble desync bug).
    let turnEl = opts.turnEl || null;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let audioQueue = [];
    let isPlaying = false;
    let streamDone = false;
    let bargedOut = false;     // user spoke over the TTS — ignore further audio
    let bargeStarted = false;  // monitor kicked once per turn

    function doBargeIn() {
      bargedOut = true;
      audioQueue = [];
      isPlaying = false;
      try { audioElement.pause(); audioElement.currentTime = 0; } catch (e) {}
      // Cut the *server* turn too, not just playback. Without this the LLM keeps
      // generating and TTS keeps synthesizing sentences nobody will hear (paid in
      // full, discarded) — and worse, this stream's `done` lands seconds later,
      // mid-utterance, and its idle() re-arms the mic on top of the new turn.
      // Aborting the fetch disconnects the client, which Starlette's
      // StreamingResponse turns into a cancel of the turn_events generator.
      try { if (opts.abort) opts.abort.abort(); } catch (e) {}
      startRecording(true);    // cut TTS, straight into listening
    }

    async function playNextAudio() {
      if (bargedOut) return;
      if (isPlaying || audioQueue.length === 0) {
        if (audioQueue.length === 0 && streamDone && !isPlaying) idle();
        return;
      }
      isPlaying = true;
      setState('speaking');
      // Kick the barge-in monitor once playback starts (opt-in, server STT only).
      if (!bargeStarted && bargeInEnabled && _listenPathCache === 'server') {
        bargeStarted = true;
        startBargeMonitor(doBargeIn);
      }
      const url = audioQueue.shift();
      audioElement.src = url;
      try { await audioElement.play(); startSpeakingWave(); }
      catch (e) { console.error(e); isPlaying = false; playNextAudio(); }
    }
    audioElement.onended = () => { isPlaying = false; playNextAudio(); };

    while (true) {
      let value, done;
      try {
        ({value, done} = await reader.read());
      } catch (err) {
        // doBargeIn aborted this fetch — expected, not a failure. It already tore
        // down everything this turn owned (queue, playback, barge monitor) and a
        // new turn is live, so fall silent: do NOT idle() or re-arm the mic.
        if (bargedOut) return;
        throw err;
      }
      if (done) {
        // Same guard for the race where the server finished just before the abort
        // landed: this turn is abandoned, the next one owns the mic now.
        if (bargedOut) return;
        streamDone = true;
        finishAuraTurn(turnEl);
        if (!isPlaying && audioQueue.length === 0) idle();
        break;
      }
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let event;
        try { event = JSON.parse(line); } catch (e) { console.error(e); continue; }
        if (event.type === 'user_text') {
          // Skip if we've already rendered this exact text in the last 2s
          // (sendText optimistically renders before the POST). Safety belt
          // for the text path; audio paths still see this event normally.
          if (recentlyRenderedUserText(event.text)) continue;
          turnEl = appendUserTurn(event.text);
        } else if (event.type === 'text') {
          appendAuraDelta(event.delta, turnEl);
        } else if (event.type === 'orient') {
          pushOrient(event.text);
        } else if (event.type === 'settle') {
          // Two-speed: the fast answer is complete; stop the "typing" cursor
          // while the quality second-opinion runs silently. A later correction
          // still appends to this same bubble (no cursor returns).
          if (turnEl) turnEl.classList.remove('streaming');
        } else if (event.type === 'audio') {
          if (bargedOut) continue;   // user interrupted — drop remaining TTS
          audioQueue.push(event.url);
          playNextAudio();
        } else if (event.type === 'emotion') {
          // Silent affect marker — rests on the body until the next one.
          document.body.setAttribute('data-emotion', event.emotion);
        } else if (event.type === 'card') {
          renderCard(event);
        } else if (event.type === 'link') {
          renderLink(event);
        } else if (event.type === 'pending_action') {
          renderPendingAction(event);
        } else if (event.type === 'confirm_required') {
          handleConfirmRequired(event);
        } else if (event.type === 'companion_switch') {
          activeCompanion = event.companion || null;
          updateCompanionUI();
        } else if (event.type === 'done') {
          messages = event.messages;
          if (event.companion !== undefined) { activeCompanion = event.companion || null; updateCompanionUI(); }
          finishAuraTurn(turnEl);
        } else if (event.type === 'error') {
          pushTrace(`<span>err · ${escAura(event.error)}</span>`, 'err');
          finishAuraTurn(turnEl);
          setState('idle');
          isContinuous = false;
          streamDone = true;
          audioQueue = [];
          isPlaying = false;
          stopBargeMonitor();
        }
      }
    }
  }

  let activeCompanion = null;

  async function processAudio(blob) {
    if (blob.size < 500) { setState('idle'); isContinuous = false; return; }
    const formData = new FormData();
    formData.append('audio', blob, 'recording.webm');
    formData.append('messages', JSON.stringify(messages));
    if (activeCompanion) formData.append('companion', activeCompanion);
    const abort = new AbortController();
    try {
      const res = await fetch('/voice-assistant/api/chat_stream',
        {method: 'POST', body: formData, headers: _byokHeaders(), signal: abort.signal});
      await consumeStream(res, {abort: abort, onDoneIdle: () => {
        if (isContinuous) startRecording(true);
        else setState('idle');
      }});
    } catch (err) {
      if (abort.signal.aborted) return;   // barge-in cut this turn on purpose
      console.error(err);
      setState('idle'); isContinuous = false;
    }
  }

  async function sendText(text) {
    const trimmed = (text || '').trim();
    if (!trimmed) return;
    const turnEl = appendUserTurn(trimmed);
    setState('thinking');
    // Text mode still speaks its reply, so it can be barged into as well.
    const abort = new AbortController();
    try {
      const res = await fetch('/voice-assistant/api/chat_text', {
        method: 'POST',
        headers: Object.assign({'Content-Type': 'application/json'}, _byokHeaders()),
        body: JSON.stringify({text: trimmed, messages, companion: activeCompanion}),
        signal: abort.signal,
      });
      await consumeStream(res, {turnEl: turnEl, abort: abort, onDoneIdle: () => setState('idle')});
    } catch (err) {
      if (abort.signal.aborted) return;   // barge-in cut this turn on purpose
      console.error(err);
      setState('idle');
    }
  }

  // ── Confirm gate (inline patch card in transcript) ─────────────────
  let pendingConfirm = null;
  function handleConfirmRequired(event) {
    pendingConfirm = {verb: event.verb, args: event.args};
    const argsPretty = Object.entries(event.args || {})
      .map(([k, v]) => `${escAura(k)}: ${escAura(typeof v === 'string' ? v : JSON.stringify(v))}`)
      .join(' · ');
    const card = document.createElement('div');
    card.className = 'patch-confirm';
    card.innerHTML = `
      <div class="patch-confirm-head">Confirm · ${escAura(event.verb)}</div>
      <div class="patch-confirm-desc">${escAura(event.description || event.verb)}</div>
      <div class="patch-confirm-args">${argsPretty || '—'}</div>
      <div class="patch-confirm-actions">
        <button class="rec-btn compact" data-act="skip"><span>SKIP</span></button>
        <button class="rec-btn compact danger" data-act="run"><span>RUN</span></button>
      </div>`;
    transcriptEl.appendChild(card);
    scrollToBottom();
    card.querySelector('[data-act="skip"]').addEventListener('click', () => { card.remove(); cancelPendingConfirm(); });
    card.querySelector('[data-act="run"]').addEventListener('click', async () => { card.remove(); await runPendingConfirm(); });
  }

  async function runPendingConfirm() {
    if (!pendingConfirm) return;
    const {verb, args} = pendingConfirm;
    pendingConfirm = null;
    try {
      const res = await fetch('/voice-assistant/api/confirm-intent', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({verb, args}),
      });
      const out = await res.json();
      if (out.error) { pushTrace(`<span>err · ${escAura(out.error)}</span>`, 'err'); return; }
      if (out.say) {
        if (currentTurnEl) appendAuraDelta(' ' + out.say);
        else {
          const el = document.createElement('div');
          el.className = 'turn turn-aura';
          el.innerHTML = `<div class="turn-ts">${fmtTurnTs()}</div>
            <div class="turn-body"><span class="turn-role">Aura</span>${escAura(out.say)}</div>`;
          transcriptEl.appendChild(el);
        }
        pushTrace(`tool · ${escAura(verb)} <span class="arr">→</span> ok`, 'tool');
      }
      if (out.card) renderCard({renderer: out.card.renderer, data: out.card.data, title: out.card.title});
    } catch (e) { console.error(e); }
  }
  function cancelPendingConfirm() {
    if (!pendingConfirm) return;
    const verb = pendingConfirm.verb;
    pendingConfirm = null;
    pushTrace(`skip · ${escAura(verb)}`, 'skip');
  }

  // ── Text input ──────────────────────────────────────────────────────
  const textInput = document.getElementById('text-input');
  const textSend = document.getElementById('text-send');
  function autoGrow() {
    textInput.style.height = 'auto';
    textInput.style.height = Math.min(textInput.scrollHeight, 140) + 'px';
  }
  textInput.addEventListener('input', autoGrow);
  textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const t = textInput.value;
      textInput.value = ''; autoGrow();
      sendText(t);
    }
  });
  textSend.addEventListener('click', () => {
    const t = textInput.value;
    textInput.value = ''; autoGrow();
    sendText(t);
  });

  // ── Text-mode toggle ────────────────────────────────────────────────
  const textToggleBtn = document.getElementById('btn-text-toggle');
  textToggleBtn.addEventListener('click', () => {
    const on = body.classList.toggle('text-mode');
    textToggleBtn.classList.toggle('active', on);
    if (on) setTimeout(() => textInput.focus(), 60);
  });

  // ── Settings drawer ─────────────────────────────────────────────────
  const settingsDrawer = document.getElementById('settings-drawer');
  document.getElementById('btn-settings').addEventListener('click', async () => {
    settingsDrawer.classList.add('show');
    await loadCompanionsForFilter();
    activateSettingsTab('history');
  });
  document.getElementById('settings-close').addEventListener('click', () => settingsDrawer.classList.remove('show'));
  document.querySelectorAll('.settings-tab').forEach(t => {
    t.addEventListener('click', () => activateSettingsTab(t.dataset.tab));
  });
  // Barge-in toggle (opt-in; persisted in localStorage). setBargeIn syncs the label.
  (function initBargeToggle() {
    const btn = document.getElementById('barge-toggle');
    if (!btn) return;
    setBargeIn(bargeInEnabled);   // sync label/aria from stored value
    btn.addEventListener('click', () => setBargeIn(!bargeInEnabled));
  })();
  function activateSettingsTab(name) {
    document.querySelectorAll('.settings-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.settings-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === name));
    if (name === 'history') renderHistory();
  }

  // ── History ─────────────────────────────────────────────────────────
  const historyList = document.getElementById('history-list');
  const historyFilter = document.getElementById('history-filter');
  let historyCompanionsLoaded = false;
  async function loadCompanionsForFilter() {
    if (historyCompanionsLoaded) return;
    try {
      const res = await fetch('/voice-assistant/api/companions');
      const list = await res.json();
      list.forEach(c => {
        const o = document.createElement('option');
        o.value = c.id; o.textContent = c.name;
        historyFilter.appendChild(o);
      });
      historyCompanionsLoaded = true;
    } catch (e) { console.error(e); }
  }
  function fmtTime(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    } catch { return iso; }
  }
  async function renderHistory() {
    const companion = historyFilter.value;
    const url = '/voice-assistant/api/history?limit=100' + (companion ? `&companion=${encodeURIComponent(companion)}` : '');
    historyList.innerHTML = '<div class="history-empty">Loading…</div>';
    try {
      const res = await fetch(url);
      const rows = await res.json();
      if (!rows || !rows.length) { historyList.innerHTML = '<div class="history-empty">No chats yet.</div>'; return; }
      historyList.innerHTML = rows.map(r => `
        <div class="history-row">
          <div class="history-meta">${escAura(fmtTime(r.time))} · ${escAura(r.companion || 'aura')}</div>
          <div class="history-user">${escAura(r.user || '')}</div>
          <div class="history-assistant">${escAura(r.assistant || '')}</div>
        </div>`).join('');
    } catch (e) {
      // error-state: intentional - Aura is a declared visual island without the shared bundle (index.html eos-ui: exempt); the message names the failure
      historyList.innerHTML = '<div class="history-empty">Could not load history.</div>';
      console.error(e);
    }
  }
  historyFilter.addEventListener('change', renderHistory);

  // ── Autopilot session toggle ────────────────────────────────────────
  // "Auto-accept" chip in the brand bar. ON issues a session-scoped grant
  // covering every eligible verb; OFF revokes. 1h TTL by default; the
  // countdown ticks down client-side from the grant's expires_at.
  const apChip = document.getElementById('btn-autopilot');
  const apState = apChip ? apChip.querySelector('.ap-state') : null;
  let apExpiresMs = 0;
  let apTickHandle = null;

  function apRender(active, secondsLeft) {
    if (!apChip || !apState) return;
    if (!active) {
      apChip.classList.remove('on');
      apState.textContent = 'auto-accept · off';
      return;
    }
    apChip.classList.add('on');
    const m = Math.max(0, Math.floor(secondsLeft / 60));
    apState.textContent = `auto-accept · ${m}m left`;
  }
  function apStartTick() {
    if (apTickHandle) clearInterval(apTickHandle);
    apTickHandle = setInterval(() => {
      const left = Math.max(0, Math.floor((apExpiresMs - Date.now()) / 1000));
      if (left <= 0) { apRender(false, 0); clearInterval(apTickHandle); apTickHandle = null; return; }
      apRender(true, left);
    }, 1000);
  }
  async function apRefresh() {
    try {
      const res = await fetch('/voice-assistant/api/autopilot');
      const data = await res.json();
      const grants = data.grants || [];
      if (!grants.length) { apRender(false, 0); apExpiresMs = 0; return; }
      // Use the earliest expiry among voice-scope grants as the chip countdown.
      const expiries = grants.map(g => g.expires_at ? new Date(g.expires_at).getTime() : Infinity);
      apExpiresMs = Math.min(...expiries);
      const left = Math.max(0, Math.floor((apExpiresMs - Date.now()) / 1000));
      apRender(true, left);
      apStartTick();
    } catch (e) { console.warn('autopilot status failed', e); }
  }
  async function apToggle() {
    const isOn = apChip.classList.contains('on');
    try {
      const res = await fetch('/voice-assistant/api/autopilot/session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(isOn ? {enabled: false} : {enabled: true, ttl_s: 3600}),
      });
      const data = await res.json();
      if (!data.ok) { console.warn('autopilot toggle failed', data); return; }
      await apRefresh();
    } catch (e) { console.warn('autopilot toggle error', e); }
  }
  if (apChip) {
    apChip.addEventListener('click', apToggle);
    apRefresh();
  }

  // ── Companion picker (rail click) ───────────────────────────────────
  async function loadCompanions() {
    try {
      const res = await fetch('/voice-assistant/api/companions');
      const list = await res.json();
      pickerList.innerHTML = '';
      const aura = document.createElement('div');
      aura.className = 'picker-row active';
      aura.dataset.companion = '';
      aura.innerHTML = `<span class="ch">CH 0</span><span>Aura</span>`;
      aura.addEventListener('click', () => switchCompanion(null));
      pickerList.appendChild(aura);
      list.forEach((c, i) => {
        const row = document.createElement('div');
        row.className = 'picker-row';
        row.dataset.companion = c.id;
        row.innerHTML = `<span class="ch">CH ${i+1}</span><span>${escAura(c.name)}</span>`;
        row.addEventListener('click', () => switchCompanion(c.id));
        pickerList.appendChild(row);
      });
    } catch (e) { console.error(e); }
  }
  function switchCompanion(id) {
    activeCompanion = id || null;
    updateCompanionUI();
    const row = pickerList.querySelector(`.picker-row[data-companion="${id || ''}"]`);
    const name = row ? row.textContent.replace(/^CH\s*\d+/, '').trim() : 'Aura';
    pushTrace(`switch <span class="arr">→</span> ${escAura(name)}`, 'tool');
    picker.classList.remove('show');
  }
  function updateCompanionUI() {
    pickerList.querySelectorAll('.picker-row').forEach(r => {
      r.classList.toggle('active', (r.dataset.companion || '') === (activeCompanion || ''));
    });
    let label = 'Aura';
    const row = pickerList.querySelector(`.picker-row[data-companion="${activeCompanion || ''}"]`);
    if (row) label = row.textContent.replace(/^CH\s*\d+/, '').trim();
    brandCompanion.textContent = label.toUpperCase();
    railCompanion.textContent = label.toUpperCase();
  }
  loadCompanions();

  // Pre-warm the quality-context caches (ambient-context fan-out + intent
  // embeddings) so the session's first utterance doesn't pay the cold build
  // (~23s measured cold vs ~3s warm). Fire-and-forget; failure is harmless —
  // the first turn just pays the build itself, as before.
  fetch('/voice-assistant/api/warmup', {method: 'POST', headers: _byokHeaders()}).catch(() => {});

  // Rail toggles picker
  document.getElementById('rail').addEventListener('click', (e) => {
    e.stopPropagation();
    picker.classList.toggle('show');
  });
  document.addEventListener('click', (e) => {
    if (!picker.contains(e.target) && !document.getElementById('rail').contains(e.target)) {
      picker.classList.remove('show');
    }
  });

  // ── iOS audio unlock ────────────────────────────────────────────────
  let audioUnlocked = false;
  function unlockAudio() {
    if (audioUnlocked) return;
    audioElement.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';
    audioElement.play().then(() => { audioUnlocked = true; }).catch(() => {});
  }

  // ── Voice trigger ──────────────────────────────────────────────────
  function toggleVoice() {
    unlockAudio();
    if (body.classList.contains('state-speaking') || body.classList.contains('state-thinking')) {
      audioElement.pause();
      isContinuous = false;
      setState('idle');
      return;
    }
    if (isRecording) {
      isContinuous = false;
      stopRecording();
    } else {
      startRecording(true);
    }
  }
  recBtn.addEventListener('click', toggleVoice);
  // Spacebar push-to-talk when not focused on text input.
  document.addEventListener('keydown', (e) => {
    if (e.code !== 'Space') return;
    if (document.activeElement === textInput) return;
    if (e.repeat) return;
    e.preventDefault();
    toggleVoice();
  });

  // First paint
  drawIdleLine();
  setState('idle');
