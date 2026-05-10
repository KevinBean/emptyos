/**
 * eos-deck — shared deck/slideshow renderer.
 *
 * Two modes share the same shadow-DOM player:
 *
 *   mode: 'manual'  — ppt-style. Slides are pre-rendered HTML; advance with
 *                     arrow keys / space / click. No audio. Optional speaker
 *                     notes overlay (S key).
 *
 *   mode: 'timed'   — podcast-style. Image scenes synced to an audio track
 *                     with subtitles. Auto-advance on audio.timeupdate.
 *                     Drop-in successor to the legacy SlideshowPlayer.
 *
 * Manual mode usage:
 *   EOS_DECK.create(container, {
 *     mode: 'manual',
 *     slides: [
 *       { html: '<h1>Title</h1>', notes: 'optional speaker notes' },
 *       { html: '<h2>Section</h2><ul><li>a</li></ul>' },
 *     ],
 *     theme: 'dark' | 'light' | 'mono',     // default 'dark'
 *     aspect: '16:9' | '4:3',               // default '16:9'
 *     startIndex: 0,
 *     onSlideChange: function (idx) {},
 *   });
 *
 * Timed mode usage (drop-in compat with old SlideshowPlayer):
 *   EOS_DECK.create(container, {
 *     mode: 'timed',
 *     audioUrl: '...',
 *     slideshowUrl: '...' ,                 // optional, fetches data
 *     timings: [...], scenes: [...],        // or pass directly
 *     compact: false,
 *   });
 *
 * Returned instance:
 *   { goto(i), next(), prev(), play(), pause(), enterFullscreen(),
 *     toggleNotes(), destroy(), get audio() }
 *
 * Themes are pure CSS — see eos-deck.css. The shadow root pulls them in via
 * CSS custom properties bridged from the host document, so a deck embedded
 * in a light-theme app inherits the host theme unless explicitly overridden.
 */
(function () {
  'use strict';

  // ── Embedded base CSS (shared by both modes; theme overrides come from
  // eos-deck.css via CSS custom properties bridged through the host).
  var BASE_CSS = `
    :host { all: initial; display: block;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
      --deck-bg: #0a0a1a;
      --deck-fg: #f3f4f6;
      --deck-muted: rgba(243,244,246,0.55);
      --deck-accent: #f97316;
      --deck-accent-2: #fb923c;
      --deck-surface: rgba(255,255,255,0.08);
      --deck-surface-hover: rgba(255,255,255,0.16);
      --deck-border: rgba(255,255,255,0.06);
      --deck-shadow: 0 1px 4px rgba(0,0,0,0.4);
    }
    :host(.theme-light) {
      --deck-bg: #fafafa;
      --deck-fg: #111;
      --deck-muted: rgba(17,17,17,0.55);
      --deck-accent: #d97706;
      --deck-accent-2: #f59e0b;
      --deck-surface: rgba(0,0,0,0.06);
      --deck-surface-hover: rgba(0,0,0,0.12);
      --deck-border: rgba(0,0,0,0.08);
      --deck-shadow: 0 1px 4px rgba(0,0,0,0.15);
    }
    :host(.theme-mono) {
      --deck-bg: #111;
      --deck-fg: #e5e5e5;
      --deck-muted: rgba(229,229,229,0.55);
      --deck-accent: #e5e5e5;
      --deck-accent-2: #ffffff;
      --deck-surface: rgba(255,255,255,0.06);
      --deck-surface-hover: rgba(255,255,255,0.14);
      --deck-border: rgba(255,255,255,0.08);
      --deck-shadow: 0 1px 4px rgba(0,0,0,0.6);
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    .deck-root {
      position: relative;
      background: var(--deck-bg);
      color: var(--deck-fg);
      border-radius: 14px;
      overflow: hidden;
      border: 1px solid var(--deck-border);
    }
    .deck-stage {
      position: relative;
      width: 100%;
      padding-top: 56.25%; /* aspect default 16:9 */
      overflow: hidden;
      cursor: pointer;
    }
    .deck-stage.aspect-4-3 { padding-top: 75%; }

    /* ── Manual-mode slide container ── */
    .deck-slide {
      position: absolute;
      inset: 0;
      padding: clamp(24px, 5%, 64px);
      overflow: auto;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.25s ease;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }
    .deck-slide.active { opacity: 1; pointer-events: auto; }
    .deck-slide.blank { opacity: 0; background: #000; }

    /* Slide content typography — kept generous for projection. */
    .deck-slide h1 { font-size: clamp(28px, 5vw, 56px); font-weight: 700; margin-bottom: 0.5em; line-height: 1.15; }
    .deck-slide h2 { font-size: clamp(22px, 3.6vw, 40px); font-weight: 600; margin-bottom: 0.6em; line-height: 1.2; color: var(--deck-accent); }
    .deck-slide h3 { font-size: clamp(18px, 2.6vw, 28px); font-weight: 600; margin-bottom: 0.5em; }
    .deck-slide p, .deck-slide li { font-size: clamp(14px, 1.6vw, 22px); line-height: 1.6; margin-bottom: 0.4em; }
    .deck-slide ul, .deck-slide ol { padding-left: 1.4em; margin-bottom: 0.6em; }
    .deck-slide img { max-width: 100%; max-height: 100%; min-height: 0; flex: 0 1 auto; width: auto; height: auto; object-fit: contain; display: block; margin: 0.4em auto; border-radius: 8px; }
    .deck-slide code { font-family: 'SF Mono', Consolas, Menlo, monospace; font-size: 0.92em; padding: 2px 6px; background: var(--deck-surface); border-radius: 4px; }
    .deck-slide pre { background: var(--deck-surface); padding: 14px 18px; border-radius: 10px; overflow: auto; margin: 0.6em 0; }
    .deck-slide pre code { background: transparent; padding: 0; }
    .deck-slide blockquote { border-left: 3px solid var(--deck-accent); padding-left: 0.9em; color: var(--deck-muted); font-style: italic; }
    .deck-slide a { color: var(--deck-accent-2); text-decoration: underline; }
    .deck-slide table { border-collapse: collapse; width: 100%; margin: 0.6em 0; }
    .deck-slide th, .deck-slide td { border: 1px solid var(--deck-border); padding: 6px 10px; text-align: left; }

    /* Live-embed iframes (![embed: /journal/]) */
    .deck-slide .ppt-embed {
      display: block;
      width: 100%;
      height: clamp(280px, 60vh, 720px);
      border: 1px solid var(--deck-border);
      border-radius: 10px;
      background: var(--deck-bg);
      margin: 0.4em 0;
    }
    /* When a slide contains ONLY an embed (no other content), let it fill the stage. */
    .deck-slide:has(> .ppt-embed:only-child),
    .deck-slide:has(> p > .ppt-embed:only-child) {
      padding: 16px;
    }
    .deck-slide:has(> .ppt-embed:only-child) .ppt-embed,
    .deck-slide:has(> p > .ppt-embed:only-child) .ppt-embed {
      height: calc(100% - 8px);
    }

    /* ── Timed-mode image stack (kept compatible with legacy SlideshowPlayer) ── */
    .deck-img {
      position: absolute; inset: 0;
      width: 100%; height: 100%;
      object-fit: cover;
      transition: opacity 0.8s ease;
    }
    .deck-img.behind { z-index: 1; }
    .deck-img.front { z-index: 2; }
    .deck-img.hidden { opacity: 0; }
    .deck-gradient {
      position: absolute; bottom: 0; left: 0; right: 0;
      height: 50%;
      background: linear-gradient(transparent, rgba(0,0,0,0.85));
      z-index: 3; pointer-events: none;
    }
    .deck-subtitle {
      position: absolute; bottom: 52px; left: 16px; right: 16px;
      z-index: 4;
      font-size: 14px; line-height: 1.5;
      color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,0.8);
      text-align: center;
      pointer-events: none;
      transition: opacity 0.3s;
      min-height: 22px;
    }
    .deck-subtitle .speaker-a { color: var(--deck-accent); font-weight: 600; }
    .deck-subtitle .speaker-b { color: #38bdf8; font-weight: 600; }
    .deck-topic {
      position: absolute; top: 12px; left: 14px; right: 14px;
      z-index: 4;
      font-size: 13px; font-weight: 700;
      color: rgba(255,255,255,0.7);
      text-shadow: 0 1px 4px rgba(0,0,0,0.8);
      pointer-events: none;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }

    /* ── Controls bar (shared) ── */
    .deck-controls {
      position: absolute; bottom: 0; left: 0; right: 0;
      z-index: 5;
      display: flex; align-items: center; gap: 8px;
      padding: 8px 12px;
      background: rgba(0,0,0,0.5);
      backdrop-filter: blur(4px);
      opacity: 0;
      transition: opacity 0.2s;
    }
    .deck-stage:hover .deck-controls,
    .deck-controls.always { opacity: 1; }
    .deck-btn {
      width: 32px; height: 32px;
      border: none; border-radius: 8px;
      background: rgba(255,255,255,0.12);
      color: #fff;
      font-size: 14px;
      cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0;
      transition: background 0.15s;
    }
    .deck-btn:hover { background: rgba(255,255,255,0.22); }
    .deck-btn:active { transform: scale(0.93); }
    .deck-btn[disabled] { opacity: 0.4; cursor: default; }

    .deck-pos {
      font-size: 12px;
      color: rgba(255,255,255,0.7);
      font-variant-numeric: tabular-nums;
      flex-shrink: 0;
      padding: 0 6px;
    }

    /* ── Speaker notes overlay (manual mode) ── */
    .deck-notes {
      position: absolute; bottom: 0; left: 0; right: 0;
      max-height: 35%;
      z-index: 6;
      padding: 14px 18px;
      background: rgba(0,0,0,0.85);
      color: #fff;
      font-size: 13px;
      line-height: 1.5;
      overflow: auto;
      transform: translateY(100%);
      transition: transform 0.25s ease;
    }
    .deck-notes.shown { transform: translateY(0); }
    .deck-notes-label {
      font-size: 10px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: rgba(255,255,255,0.5);
      margin-bottom: 4px;
    }

    /* ── Overview / grid (manual mode) ── */
    .deck-overview {
      position: absolute; inset: 0;
      z-index: 7;
      background: var(--deck-bg);
      padding: 24px;
      overflow: auto;
      display: none;
    }
    .deck-overview.shown { display: block; }
    .deck-overview-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 14px;
    }
    .deck-thumb {
      position: relative;
      aspect-ratio: 16 / 9;
      background: var(--deck-surface);
      border: 1px solid var(--deck-border);
      border-radius: 8px;
      cursor: pointer;
      overflow: hidden;
      padding: 10px 12px;
      font-size: 10px;
      line-height: 1.3;
      color: var(--deck-fg);
    }
    .deck-thumb:hover { border-color: var(--deck-accent); }
    .deck-thumb-num {
      position: absolute; top: 4px; right: 6px;
      font-size: 10px;
      color: var(--deck-muted);
    }
    .deck-thumb h1, .deck-thumb h2, .deck-thumb h3 { font-size: 12px; line-height: 1.3; margin-bottom: 4px; }
    .deck-thumb p, .deck-thumb li { font-size: 9px; line-height: 1.3; }
    .deck-thumb img { display: none; }

    /* ── Scrub bar (timed mode) ── */
    .deck-scrub {
      flex: 1;
      position: relative;
      height: 20px;
      display: flex; align-items: center;
      cursor: pointer;
    }
    .deck-scrub-track {
      width: 100%; height: 4px;
      background: rgba(255,255,255,0.15);
      border-radius: 2px;
      position: relative;
    }
    .deck-scrub-fill {
      height: 100%; width: 0%;
      background: linear-gradient(90deg, var(--deck-accent), var(--deck-accent-2));
      border-radius: 2px;
      transition: width 0.1s linear;
    }
    .deck-scrub-thumb {
      position: absolute; top: 50%; left: 0%;
      transform: translate(-50%, -50%);
      width: 12px; height: 12px;
      border-radius: 50%;
      background: #fff;
      box-shadow: 0 1px 4px rgba(0,0,0,0.4);
      transition: left 0.1s linear;
    }
    .deck-time {
      font-size: 11px;
      color: rgba(255,255,255,0.6);
      font-variant-numeric: tabular-nums;
      flex-shrink: 0;
      min-width: 72px;
      text-align: center;
    }
    .deck-speed {
      width: auto;
      padding: 0 8px;
      height: 26px;
      border: none;
      border-radius: 13px;
      background: rgba(255,255,255,0.12);
      color: rgba(255,255,255,0.85);
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
      flex-shrink: 0;
      font-family: inherit;
    }
    .deck-speed:hover { background: rgba(255,255,255,0.22); }

    /* ── Fullscreen ── */
    .deck-root.fullscreen {
      position: fixed !important;
      inset: 0;
      z-index: 999999;
      border-radius: 0;
      border: none;
    }
    .deck-root.fullscreen .deck-stage { padding-top: 0 !important; height: 100%; }
    .deck-root.fullscreen .deck-slide { padding: clamp(40px, 7%, 96px); }

    /* ── Loading shim (timed only) ── */
    .deck-loading {
      position: absolute; top: 50%; left: 50%;
      transform: translate(-50%, -50%);
      z-index: 10;
      color: var(--deck-muted);
      font-size: 13px;
      font-weight: 500;
    }
  `;

  function fmtTime(s) {
    if (isNaN(s) || s < 0) s = 0;
    var m = Math.floor(s / 60);
    var sec = Math.floor(s % 60);
    return m + ':' + (sec < 10 ? '0' : '') + sec;
  }

  function findSegment(timings, t) {
    var lo = 0, hi = timings.length - 1, best = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (timings[mid].start_ms <= t) { best = mid; lo = mid + 1; }
      else { hi = mid - 1; }
    }
    if (best >= 0 && t <= timings[best].end_ms) return best;
    return -1;
  }
  function findScene(scenes, t) {
    for (var i = scenes.length - 1; i >= 0; i--) {
      if (t >= scenes[i].start_ms) return i;
    }
    return 0;
  }
  function escHtml(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // ── Diagram + chart enhancement ─────────────────────────────────
  // Markdown produces <pre><code class="language-mermaid">…</code></pre> and
  // <pre><code class="language-chart">…</code></pre>. Walk the rendered slide
  // and swap those nodes with rendered diagrams / charts. Libraries are loaded
  // from CDN on demand — slides without these surfaces pay nothing.
  var _scriptCache = {};
  function _loadScript(src) {
    if (_scriptCache[src]) return _scriptCache[src];
    _scriptCache[src] = new Promise(function (resolve, reject) {
      var el = document.createElement('script');
      el.src = src;
      el.async = true;
      el.onload = function () { resolve(); };
      el.onerror = function () { reject(new Error('failed to load ' + src)); };
      document.head.appendChild(el);
    });
    return _scriptCache[src];
  }
  var _mermaidId = 0;
  function _renderMermaid(codeEl) {
    var pre = codeEl.parentElement;
    var src = codeEl.textContent || '';
    var holder = document.createElement('div');
    holder.className = 'deck-mermaid';
    holder.style.display = 'flex';
    holder.style.justifyContent = 'center';
    holder.style.alignItems = 'center';
    holder.style.width = '100%';
    holder.style.flex = '1 1 auto';
    holder.style.minHeight = '0';
    holder.style.maxHeight = '70vh';
    holder.style.overflow = 'hidden';
    holder.textContent = '⏳ rendering diagram…';
    if (pre && pre.parentNode) pre.parentNode.replaceChild(holder, pre);
    _loadScript('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js')
      .then(function () {
        if (!window.mermaid) throw new Error('mermaid global missing');
        if (!window.__eos_mermaid_init) {
          window.mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' });
          window.__eos_mermaid_init = true;
        }
        var id = 'eos-mermaid-' + (++_mermaidId);
        return window.mermaid.render(id, src);
      })
      .then(function (out) {
        holder.innerHTML = out && out.svg ? out.svg : '';
        var svg = holder.querySelector('svg');
        if (svg) {
          // Mermaid sets fixed width/height on the SVG which can overflow the
          // slide. Drop them so the inline max-width/height take over.
          svg.removeAttribute('height');
          svg.removeAttribute('width');
          svg.style.maxWidth = '100%';
          svg.style.maxHeight = '100%';
          svg.style.width = 'auto';
          svg.style.height = 'auto';
        }
      })
      .catch(function (err) {
        holder.textContent = '⚠ diagram error: ' + (err && err.message ? err.message : err);
        holder.style.color = '#e06c6c';
        holder.style.fontFamily = 'monospace';
        holder.style.fontSize = '12px';
      });
  }
  function _renderChart(codeEl) {
    var pre = codeEl.parentElement;
    var src = codeEl.textContent || '';
    var spec;
    try { spec = JSON.parse(src); }
    catch (e) {
      var err = document.createElement('div');
      err.textContent = '⚠ chart JSON parse error: ' + e.message;
      err.style.color = '#e06c6c';
      err.style.fontFamily = 'monospace';
      err.style.fontSize = '12px';
      if (pre && pre.parentNode) pre.parentNode.replaceChild(err, pre);
      return;
    }
    var wrap = document.createElement('div');
    wrap.className = 'deck-chart';
    wrap.style.position = 'relative';
    wrap.style.width = '100%';
    wrap.style.maxWidth = '720px';
    wrap.style.flex = '1 1 auto';
    wrap.style.minHeight = '180px';
    wrap.style.maxHeight = '70vh';
    wrap.style.margin = '0 auto';
    var canvas = document.createElement('canvas');
    wrap.appendChild(canvas);
    if (pre && pre.parentNode) pre.parentNode.replaceChild(wrap, pre);
    _loadScript('https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js')
      .then(function () {
        if (!window.Chart) throw new Error('Chart global missing');
        var type = (spec.type || 'bar').toLowerCase();
        var labels = spec.labels || [];
        var values = spec.values || [];
        var palette = ['#5b9bd5', '#ed7d31', '#a5a5a5', '#ffc000', '#70ad47', '#264478', '#9e480e'];
        var dsColor = (type === 'pie' || type === 'doughnut')
          ? labels.map(function (_, i) { return palette[i % palette.length]; })
          : palette[0];
        new window.Chart(canvas, {
          type: type,
          data: {
            labels: labels,
            datasets: [{
              label: spec.label || '',
              data: values,
              backgroundColor: dsColor,
              borderColor: (type === 'line') ? palette[0] : undefined,
              borderWidth: (type === 'line') ? 2 : 1,
              fill: (type === 'line') ? false : undefined,
              tension: (type === 'line') ? 0.3 : undefined,
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: !!spec.label || type === 'pie' || type === 'doughnut' },
              title: spec.title ? { display: true, text: spec.title } : undefined,
            },
          },
        });
      })
      .catch(function (err) {
        wrap.textContent = '⚠ chart error: ' + (err && err.message ? err.message : err);
        wrap.style.color = '#e06c6c';
      });
  }
  function _enhance(root) {
    if (!root || !root.querySelectorAll) return;
    var mermaids = root.querySelectorAll('code.language-mermaid');
    for (var i = 0; i < mermaids.length; i++) _renderMermaid(mermaids[i]);
    var charts = root.querySelectorAll('code.language-chart');
    for (var j = 0; j < charts.length; j++) _renderChart(charts[j]);
    // Audio/video: apply responsive sizing + flex-aware layout. Markdown
    // wraps these in <p>; unwrap so flexbox parents can stretch them.
    var medias = root.querySelectorAll('.ppt-audio, .ppt-video');
    for (var k = 0; k < medias.length; k++) {
      var m = medias[k];
      m.style.maxWidth = '100%';
      if (m.tagName.toLowerCase() === 'video' || m.classList.contains('ppt-video-yt')) {
        m.style.flex = '1 1 auto';
        m.style.minHeight = '180px';
        m.style.maxHeight = '70vh';
        m.style.width = '100%';
      } else {
        m.style.width = 'min(720px, 100%)';
        m.style.margin = '0.6em auto';
        m.style.display = 'block';
      }
      var p = m.parentElement;
      if (p && p.tagName === 'P' && p.childNodes.length === 1) {
        p.parentNode.replaceChild(m, p);
      }
    }
    // Images: same unwrap so the flex sizing in CSS actually applies.
    // Markdown wraps standalone images in <p>; the <p> won't flex inside
    // the slide column and the image overflows in small preview panes.
    var imgs = root.querySelectorAll('img');
    for (var ii = 0; ii < imgs.length; ii++) {
      var img = imgs[ii];
      var pp = img.parentElement;
      if (pp && pp.tagName === 'P' && pp.childNodes.length === 1) {
        pp.parentNode.replaceChild(img, pp);
      }
    }
  }

  // ── Audio autoplay gate ─────────────────────────────────────────
  // Browsers block audio.play() until the user has interacted with the page.
  // Show a single play overlay; clicking it unlocks audio for the whole tab.
  function _showAudioGate(stage, audioEl) {
    if (stage._eos_audio_gate) return;
    var gate = document.createElement('div');
    gate.className = 'deck-audio-gate';
    gate.style.cssText =
      'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
      'background:rgba(0,0,0,0.55);z-index:50;cursor:pointer;font-size:48px;color:#fff;';
    gate.innerHTML =
      '<div style="display:flex;flex-direction:column;align-items:center;gap:12px">' +
      '<div style="font-size:64px;line-height:1">▶</div>' +
      '<div style="font-size:14px;opacity:0.85">Click to start narration</div>' +
      '</div>';
    gate.addEventListener('click', function () {
      try { audioEl.play(); } catch (e) {}
      gate.remove();
      stage._eos_audio_gate = null;
    });
    stage.appendChild(gate);
    stage._eos_audio_gate = gate;
  }

  // ── Manual mode ─────────────────────────────────────────────────
  function buildManual(shadow, root, opts) {
    var slides = (opts.slides || []).map(function (s) {
      return {
        html: s.html || '',
        notes: s.notes || '',
        theme: s.theme || null,
        audio_url: s.audio_url || s.audioUrl || '',
      };
    });
    if (slides.length === 0) {
      slides.push({ html: '<h1>Empty deck</h1><p>Add slides to your markdown.</p>', notes: '' });
    }

    var stage = document.createElement('div');
    stage.className = 'deck-stage';
    if (opts.aspect === '4:3') stage.classList.add('aspect-4-3');
    root.appendChild(stage);

    slides.forEach(function (s, i) {
      var el = document.createElement('div');
      el.className = 'deck-slide' + (i === 0 ? ' active' : '');
      el.innerHTML = s.html;
      el._notes = s.notes;
      // Hidden narration audio — plays on slide enter, pauses on leave.
      if (s.audio_url) {
        var aud = document.createElement('audio');
        aud.setAttribute('data-eos-narration', '1');
        aud.preload = 'metadata';
        aud.src = s.audio_url;
        aud.style.display = 'none';
        el.appendChild(aud);
      }
      stage.appendChild(el);
      _enhance(el);
    });

    // Speaker notes overlay
    var notes = document.createElement('div');
    notes.className = 'deck-notes';
    notes.innerHTML = '<div class="deck-notes-label">Speaker notes</div><div class="deck-notes-body"></div>';
    stage.appendChild(notes);
    var notesBody = notes.querySelector('.deck-notes-body');

    // Overview
    var overview = document.createElement('div');
    overview.className = 'deck-overview';
    overview.innerHTML = '<div class="deck-overview-grid"></div>';
    stage.appendChild(overview);
    var overviewGrid = overview.querySelector('.deck-overview-grid');
    slides.forEach(function (s, i) {
      var t = document.createElement('div');
      t.className = 'deck-thumb';
      t.innerHTML = '<span class="deck-thumb-num">' + (i + 1) + '</span>' + s.html;
      t.addEventListener('click', function () { goto(i); toggleOverview(false); });
      overviewGrid.appendChild(t);
      _enhance(t);
    });

    // Controls
    var controls = document.createElement('div');
    controls.className = 'deck-controls';
    controls.innerHTML =
      '<button class="deck-btn deck-prev" title="Previous (←)">&#8249;</button>' +
      '<button class="deck-btn deck-next" title="Next (→)">&#8250;</button>' +
      '<span class="deck-pos">1 / ' + slides.length + '</span>' +
      '<div style="flex:1"></div>' +
      '<button class="deck-btn deck-overview-btn" title="Overview (O)">&#9783;</button>' +
      '<button class="deck-btn deck-notes-btn" title="Notes (S)">&#9998;</button>' +
      '<button class="deck-btn deck-fs-btn" title="Fullscreen (F)">&#x26F6;</button>';
    stage.appendChild(controls);

    var posEl = controls.querySelector('.deck-pos');
    var prevBtn = controls.querySelector('.deck-prev');
    var nextBtn = controls.querySelector('.deck-next');
    var fsBtn = controls.querySelector('.deck-fs-btn');
    var notesBtn = controls.querySelector('.deck-notes-btn');
    var ovBtn = controls.querySelector('.deck-overview-btn');

    var idx = Math.max(0, Math.min(slides.length - 1, opts.startIndex || 0));
    var notesShown = false;
    var overviewShown = false;
    var blanked = false;
    var slideEls = stage.querySelectorAll('.deck-slide');

    function render() {
      slideEls.forEach(function (el, i) {
        var becameActive = i === idx && !el.classList.contains('active');
        var becameInactive = i !== idx && el.classList.contains('active');
        el.classList.toggle('active', i === idx);
        el.classList.toggle('blank', blanked);
        // Narration / autoplay audio: play on enter, pause on leave.
        if (becameInactive) {
          var leaving = el.querySelectorAll('audio[data-eos-narration], video, audio');
          for (var li = 0; li < leaving.length; li++) {
            try { leaving[li].pause(); leaving[li].currentTime = 0; } catch (e) {}
          }
        }
        if (becameActive || (i === idx && !el._eos_audio_started)) {
          var entering = el.querySelectorAll('audio[data-eos-narration]');
          for (var ei = 0; ei < entering.length; ei++) {
            var a = entering[ei];
            try {
              var p = a.play();
              if (p && p.catch) {
                p.catch(function () {
                  // Autoplay blocked (no user gesture yet on slide 1). Show
                  // a one-time play overlay so the user can unlock audio.
                  _showAudioGate(stage, a);
                });
              }
              el._eos_audio_started = true;
            } catch (e) {}
          }
        }
      });
      posEl.textContent = (idx + 1) + ' / ' + slides.length;
      notesBody.textContent = slides[idx].notes || '';
      prevBtn.disabled = idx === 0;
      nextBtn.disabled = idx === slides.length - 1;
      if (typeof opts.onSlideChange === 'function') {
        try { opts.onSlideChange(idx); } catch (e) {}
      }
    }
    function goto(i) {
      idx = Math.max(0, Math.min(slides.length - 1, i));
      blanked = false;
      render();
    }
    function next() { if (idx < slides.length - 1) goto(idx + 1); }
    function prev() { if (idx > 0) goto(idx - 1); }
    function toggleNotes(force) {
      notesShown = (typeof force === 'boolean') ? force : !notesShown;
      notes.classList.toggle('shown', notesShown);
    }
    function toggleOverview(force) {
      overviewShown = (typeof force === 'boolean') ? force : !overviewShown;
      overview.classList.toggle('shown', overviewShown);
    }
    function blank() { blanked = !blanked; render(); }

    prevBtn.addEventListener('click', function (e) { e.stopPropagation(); prev(); });
    nextBtn.addEventListener('click', function (e) { e.stopPropagation(); next(); });
    notesBtn.addEventListener('click', function (e) { e.stopPropagation(); toggleNotes(); });
    ovBtn.addEventListener('click', function (e) { e.stopPropagation(); toggleOverview(); });

    stage.addEventListener('click', function (e) {
      // ignore clicks on controls / inside notes / overview / links
      if (e.target.closest('.deck-controls')) return;
      if (e.target.closest('.deck-notes')) return;
      if (e.target.closest('.deck-overview')) return;
      if (e.target.closest('a')) return;
      next();
    });

    render();

    return {
      goto: goto, next: next, prev: prev,
      toggleNotes: toggleNotes,
      toggleOverview: toggleOverview,
      blank: blank,
      get index() { return idx; },
      get count() { return slides.length; },
      _fsBtn: fsBtn,
      _kind: 'manual',
    };
  }

  // ── Timed mode (legacy SlideshowPlayer port, simplified) ────────
  function buildTimed(shadow, root, opts) {
    root.innerHTML = '';
    var loadingEl = document.createElement('div');
    loadingEl.className = 'deck-loading';
    loadingEl.textContent = 'Loading slideshow...';
    root.appendChild(loadingEl);

    var stage = document.createElement('div');
    stage.className = 'deck-stage';
    if (opts.aspect === '4:3') stage.classList.add('aspect-4-3');
    root.appendChild(stage);

    stage.innerHTML =
      '<img class="deck-img front" src="" alt="">' +
      '<img class="deck-img behind hidden" src="" alt="">' +
      '<div class="deck-gradient"></div>' +
      '<div class="deck-topic"></div>' +
      '<div class="deck-subtitle"></div>' +
      '<div class="deck-controls always">' +
        '<button class="deck-btn deck-play">&#9654;</button>' +
        '<div class="deck-scrub">' +
          '<div class="deck-scrub-track">' +
            '<div class="deck-scrub-fill"></div>' +
            '<div class="deck-scrub-thumb"></div>' +
          '</div>' +
        '</div>' +
        '<span class="deck-time">0:00 / 0:00</span>' +
        '<button class="deck-speed">1x</button>' +
        '<button class="deck-btn deck-fs-btn">&#x26F6;</button>' +
      '</div>';

    var imgs = stage.querySelectorAll('.deck-img');
    var imgA = imgs[0], imgB = imgs[1];
    var subtitleEl = stage.querySelector('.deck-subtitle');
    var topicEl = stage.querySelector('.deck-topic');
    var playBtn = stage.querySelector('.deck-play');
    var fsBtn = stage.querySelector('.deck-fs-btn');
    var timeEl = stage.querySelector('.deck-time');
    var scrub = stage.querySelector('.deck-scrub');
    var fillEl = stage.querySelector('.deck-scrub-fill');
    var thumbEl = stage.querySelector('.deck-scrub-thumb');
    var speedBtn = stage.querySelector('.deck-speed');

    var data = null;
    var audio = new Audio();
    audio.preload = 'auto';
    audio.crossOrigin = 'anonymous';
    var currentScene = -1;
    var frontIsA = true;
    var isSeeking = false;
    var speeds = [0.75, 1, 1.25, 1.5, 2];
    var speedIdx = 1;

    function cycleSpeed() {
      speedIdx = (speedIdx + 1) % speeds.length;
      audio.playbackRate = speeds[speedIdx];
      speedBtn.textContent = speeds[speedIdx] + 'x';
    }
    speedBtn.addEventListener('click', function (e) { e.stopPropagation(); cycleSpeed(); });

    function crossfadeTo(url) {
      if (!url) return;
      var target = frontIsA ? imgB : imgA;
      var current = frontIsA ? imgA : imgB;
      target.src = url;
      target.classList.remove('hidden');
      target.classList.add('front');
      target.classList.remove('behind');
      current.classList.add('hidden');
      current.classList.remove('front');
      current.classList.add('behind');
      frontIsA = !frontIsA;
    }
    function togglePlay() { if (audio.paused) audio.play(); else audio.pause(); }
    audio.addEventListener('play', function () { playBtn.innerHTML = '&#10074;&#10074;'; });
    audio.addEventListener('pause', function () { playBtn.innerHTML = '&#9654;'; });
    audio.addEventListener('ended', function () { playBtn.innerHTML = '&#9654;'; });

    audio.addEventListener('timeupdate', function () {
      if (isSeeking || !data) return;
      var ct = audio.currentTime;
      var dur = audio.duration || data.duration_s || 1;
      var pct = (ct / dur) * 100;
      fillEl.style.width = pct + '%';
      thumbEl.style.left = pct + '%';
      timeEl.textContent = fmtTime(ct) + ' / ' + fmtTime(dur);

      var t = ct * 1000;
      if (data.scenes && data.scenes.length > 0) {
        var sc = findScene(data.scenes, t);
        if (sc !== currentScene && sc >= 0) {
          crossfadeTo(data.scenes[sc].image_url);
          currentScene = sc;
        }
      }
      if (data.timings && data.timings.length > 0) {
        var seg = findSegment(data.timings, t);
        if (seg >= 0) {
          var s = data.timings[seg];
          var cls = s.speaker === 'A' ? 'speaker-a' : 'speaker-b';
          var lbl = s.speaker === 'A' ? 'A' : 'B';
          subtitleEl.innerHTML = '<span class="' + cls + '">' + lbl + ':</span> ' + escHtml(s.text);
        } else {
          subtitleEl.innerHTML = '';
        }
      }
    });

    function seekFromEvent(e) {
      var rect = scrub.getBoundingClientRect();
      var x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
      var pct = Math.max(0, Math.min(1, x / rect.width));
      var dur = audio.duration || data.duration_s || 1;
      audio.currentTime = pct * dur;
      fillEl.style.width = (pct * 100) + '%';
      thumbEl.style.left = (pct * 100) + '%';
    }
    var dragging = false;
    scrub.addEventListener('mousedown', function (e) { dragging = true; isSeeking = true; seekFromEvent(e); });
    scrub.addEventListener('touchstart', function (e) { dragging = true; isSeeking = true; seekFromEvent(e); }, { passive: true });
    document.addEventListener('mousemove', function (e) { if (dragging) seekFromEvent(e); });
    document.addEventListener('touchmove', function (e) { if (dragging) seekFromEvent(e); }, { passive: true });
    document.addEventListener('mouseup', function () { if (dragging) { dragging = false; isSeeking = false; } });
    document.addEventListener('touchend', function () { if (dragging) { dragging = false; isSeeking = false; } });
    scrub.addEventListener('click', seekFromEvent);

    playBtn.addEventListener('click', function (e) { e.stopPropagation(); togglePlay(); });
    stage.addEventListener('click', function (e) {
      if (e.target.closest('.deck-controls')) return;
      togglePlay();
    });

    function fallbackToAudio(reason) {
      // Slideshow unavailable — replace the broken empty stage with a plain
      // <audio> element so the episode is still listenable.
      try {
        var url = opts.audioUrl || (data && data.audio_url) || '';
        if (!url) {
          loadingEl.textContent = reason || 'Slideshow unavailable';
          return;
        }
        root.innerHTML =
          '<div style="padding:14px;background:#1a1a2e;border-radius:10px">' +
          '<audio controls preload="metadata" style="width:100%" src="' + escHtml(url) + '"></audio>' +
          '<div style="font-size:11px;color:rgba(255,255,255,0.5);margin-top:6px">' +
          escHtml(reason || 'Slideshow data unavailable — audio only') +
          '</div></div>';
      } catch (e) {}
    }

    async function init() {
      if (opts.slideshowUrl) {
        try {
          var r = await fetch(opts.slideshowUrl);
          if (!r.ok) { fallbackToAudio('Slideshow data not found (' + r.status + ')'); return; }
          data = await r.json();
        } catch (e) {
          fallbackToAudio('Could not reach slideshow data');
          return;
        }
      } else {
        data = {
          topic: opts.topic || '',
          audio_url: opts.audioUrl || '',
          timings: opts.timings || [],
          scenes: opts.scenes || [],
          duration_s: opts.duration_s || 0,
          cover_url: opts.cover_url || '',
        };
      }
      loadingEl.style.display = 'none';
      audio.src = data.audio_url || opts.audioUrl;
      topicEl.textContent = data.topic || '';
      var first = (data.scenes && data.scenes.length > 0) ? data.scenes[0].image_url : (data.cover_url || '');
      if (first) imgA.src = first;
      currentScene = 0;
    }
    init();

    return {
      play: function () { audio.play(); },
      pause: function () { audio.pause(); },
      get audio() { return audio; },
      _fsBtn: fsBtn,
      _audio: audio,
      _kind: 'timed',
    };
  }

  // ── Public factory ──────────────────────────────────────────────
  function create(container, opts) {
    opts = opts || {};
    var mode = opts.mode || 'manual';

    var host = document.createElement('div');
    host.className = 'eos-deck-host theme-' + (opts.theme || 'dark');
    container.appendChild(host);
    var shadow = host.attachShadow({ mode: 'open' });
    var style = document.createElement('style');
    style.textContent = BASE_CSS;
    shadow.appendChild(style);

    var root = document.createElement('div');
    root.className = 'deck-root';
    shadow.appendChild(root);

    // Theme propagation: shadow :host inherits via class on host element.
    if (opts.theme === 'light') host.classList.add('theme-light');
    else if (opts.theme === 'mono') host.classList.add('theme-mono');

    var inst;
    if (mode === 'timed') inst = buildTimed(shadow, root, opts);
    else inst = buildManual(shadow, root, opts);

    // Fullscreen — shared
    var isFullscreen = false;
    function toggleFullscreen() {
      if (isFullscreen) {
        root.classList.remove('fullscreen');
        if (document.exitFullscreen) document.exitFullscreen().catch(function () {});
        isFullscreen = false;
      } else {
        root.classList.add('fullscreen');
        if (root.requestFullscreen) root.requestFullscreen().catch(function () {});
        isFullscreen = true;
      }
    }
    if (inst._fsBtn) {
      inst._fsBtn.addEventListener('click', function (e) { e.stopPropagation(); toggleFullscreen(); });
    }
    document.addEventListener('fullscreenchange', function () {
      if (!document.fullscreenElement && isFullscreen) {
        root.classList.remove('fullscreen');
        isFullscreen = false;
      }
    });

    // Keyboard — both modes; manual uses arrow nav, timed uses seek.
    host.tabIndex = 0;
    host.addEventListener('keydown', function (e) {
      var k = e.key;
      if (mode === 'manual') {
        if (k === 'ArrowRight' || k === 'PageDown' || k === ' ') { e.preventDefault(); inst.next(); }
        else if (k === 'ArrowLeft' || k === 'PageUp') { e.preventDefault(); inst.prev(); }
        else if (k === 'Home') { e.preventDefault(); inst.goto(0); }
        else if (k === 'End') { e.preventDefault(); inst.goto(inst.count - 1); }
        else if (k === 'f' || k === 'F') toggleFullscreen();
        else if (k === 's' || k === 'S') inst.toggleNotes();
        else if (k === 'o' || k === 'O') inst.toggleOverview();
        else if (k === 'b' || k === 'B') inst.blank();
        else if (k === 'Escape' && isFullscreen) toggleFullscreen();
      } else {
        if (k === ' ' || k === 'k') { e.preventDefault(); if (inst._audio.paused) inst.play(); else inst.pause(); }
        else if (k === 'f' || k === 'F') toggleFullscreen();
        else if (k === 'ArrowLeft') { inst._audio.currentTime = Math.max(0, inst._audio.currentTime - 5); }
        else if (k === 'ArrowRight') { inst._audio.currentTime = Math.min(inst._audio.duration, inst._audio.currentTime + 5); }
        else if (k === 'Escape' && isFullscreen) toggleFullscreen();
      }
    });

    inst.enterFullscreen = function () { if (!isFullscreen) toggleFullscreen(); };
    inst.exitFullscreen = function () { if (isFullscreen) toggleFullscreen(); };
    inst.destroy = function () {
      if (inst._audio) { try { inst._audio.pause(); inst._audio.src = ''; } catch (e) {} }
      host.remove();
    };
    return inst;
  }

  window.EOS_DECK = { create: create };

  // Backwards compat shim — let podcast app keep calling SlideshowPlayer.
  if (!window.SlideshowPlayer) {
    window.SlideshowPlayer = {
      create: function (container, opts) {
        return create(container, Object.assign({ mode: 'timed' }, opts || {}));
      },
    };
  }
})();
