/**
 * EOS_PDF — Lazy-loaded PDF viewer drawer over any app surface.
 *
 * Usage:
 *   EOS_PDF.open({ path: "30_Resources/.../foo.pdf", page: 67, serveBase: "/learn/api/pdf" });
 *
 * PDF.js loads from cdnjs on first call. After loading, the drawer renders
 * the PDF binary fetched from `<serveBase>/<path>`. Drawer dismisses via
 * Esc, close button, or backdrop click.
 *
 * Optional `onHighlight(page)` shows a "+ Highlight" button in the header;
 * click passes the page currently on screen. v1 scope is page + a typed
 * quote/note captured by the caller afterward — not click-drag text-layer
 * selection (a bigger feature, deliberately deferred; see
 * apps/public/standard/library/INTENT.md § Future).
 *
 * First consumer: apps/learn/ for clause citations referencing local PDFs.
 * Second consumer: apps/public/standard/library/ (paper PDFs + highlight capture).
 */
(function () {
  'use strict';

  var PDFJS_VERSION = '3.11.174';  // legacy build — wider browser support, simpler API
  var PDFJS_BASE = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/' + PDFJS_VERSION + '/';
  var PDFJS_SCRIPT = PDFJS_BASE + 'pdf.min.js';
  var PDFJS_WORKER = PDFJS_BASE + 'pdf.worker.min.js';

  var _pdfjsLoad = null;
  function loadPdfJs() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (_pdfjsLoad) return _pdfjsLoad;
    _pdfjsLoad = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = PDFJS_SCRIPT;
      s.onload = function () {
        if (!window.pdfjsLib) {
          reject(new Error('pdfjsLib not exposed after load'));
          return;
        }
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
        resolve(window.pdfjsLib);
      };
      s.onerror = function () { reject(new Error('Failed to load PDF.js from CDN')); };
      document.head.appendChild(s);
    });
    return _pdfjsLoad;
  }

  // ─── Drawer + styles (injected once) ─────────────────────────────
  var _stylesInjected = false;
  function injectStyles() {
    if (_stylesInjected) return;
    _stylesInjected = true;
    var css = [
      '.eos-pdf-backdrop {',
      '  position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 9998;',
      '  opacity: 0; transition: opacity .18s ease; pointer-events: none;',
      '}',
      '.eos-pdf-backdrop.open { opacity: 1; pointer-events: auto; }',
      '.eos-pdf-drawer {',
      '  position: fixed; top: 0; right: 0; bottom: 0; width: min(900px, 92vw);',
      '  background: var(--bg-card, #1e1e1e); border-left: 1px solid var(--border, #333);',
      '  box-shadow: -4px 0 24px rgba(0,0,0,0.4); z-index: 9999;',
      '  transform: translateX(100%); transition: transform .22s ease;',
      '  display: flex; flex-direction: column;',
      '}',
      '.eos-pdf-drawer.open { transform: translateX(0); }',
      '.eos-pdf-header {',
      '  display: flex; justify-content: space-between; align-items: center;',
      '  padding: 12px 16px; border-bottom: 1px solid var(--border, #333);',
      '  background: var(--bg, #141414); gap: 12px;',
      '}',
      '.eos-pdf-header .title { font-size: .9rem; color: var(--text, #ddd); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
      '.eos-pdf-header .controls { display: flex; align-items: center; gap: 8px; }',
      '.eos-pdf-btn {',
      '  background: var(--border, #333); color: var(--text, #ddd); border: none;',
      '  padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: .85rem;',
      '}',
      '.eos-pdf-btn:hover { background: var(--accent, #4ea1f3); color: var(--accent-ink, #fff); }',
      '.eos-pdf-btn:disabled { opacity: .4; cursor: not-allowed; }',
      '.eos-pdf-page-input { width: 50px; padding: 4px 6px; border: 1px solid var(--border, #333); background: var(--bg, #141414); color: var(--text, #ddd); border-radius: 4px; text-align: center; font-size: .85rem; }',
      '.eos-pdf-body { flex: 1; overflow: auto; padding: 16px; background: #444; text-align: center; }',
      '.eos-pdf-body canvas { background: white; box-shadow: 0 2px 12px rgba(0,0,0,0.3); display: inline-block; }',
      '.eos-pdf-status { color: var(--muted, #888); padding: 40px; font-size: .9rem; }',
    ].join('\n');
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ─── Drawer state ─────────────────────────────────────────────────
  var _drawer = null;
  var _backdrop = null;
  var _pdfDoc = null;
  var _currentPage = 1;
  var _scale = 1.5;
  var _onHighlight = null;

  function ensureDrawer() {
    if (_drawer) return _drawer;
    injectStyles();
    _backdrop = document.createElement('div');
    _backdrop.className = 'eos-pdf-backdrop';
    _backdrop.onclick = close;
    document.body.appendChild(_backdrop);

    _drawer = document.createElement('div');
    _drawer.className = 'eos-pdf-drawer';
    _drawer.innerHTML = [
      '<div class="eos-pdf-header">',
      '  <div class="title" id="eos-pdf-title">PDF</div>',
      '  <div class="controls">',
      '    <button class="eos-pdf-btn" id="eos-pdf-highlight" style="display:none" title="Highlight this page">+ Highlight</button>',
      '    <button class="eos-pdf-btn" id="eos-pdf-prev" aria-label="Back">←</button>',
      '    <input class="eos-pdf-page-input" id="eos-pdf-page-input" type="number" min="1">',
      '    <span style="color:var(--muted,#888);font-size:.85rem">/ <span id="eos-pdf-total">?</span></span>',
      '    <button class="eos-pdf-btn" id="eos-pdf-next" aria-label="Forward">→</button>',
      '    <button class="eos-pdf-btn" id="eos-pdf-close" title="Close (Esc)">✕</button>',
      '  </div>',
      '</div>',
      '<div class="eos-pdf-body" id="eos-pdf-body"><div class="eos-pdf-status">Loading…</div></div>',
    ].join('');
    document.body.appendChild(_drawer);

    document.getElementById('eos-pdf-prev').onclick = function () { goPage(_currentPage - 1); };
    document.getElementById('eos-pdf-next').onclick = function () { goPage(_currentPage + 1); };
    document.getElementById('eos-pdf-close').onclick = close;
    document.getElementById('eos-pdf-highlight').onclick = function () {
      if (_onHighlight) _onHighlight(_currentPage);
    };
    document.getElementById('eos-pdf-page-input').onchange = function (e) {
      var n = parseInt(e.target.value, 10);
      if (!isNaN(n)) goPage(n);
    };
    document.addEventListener('keydown', function (e) {
      if (!_drawer || !_drawer.classList.contains('open')) return;
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowLeft') goPage(_currentPage - 1);
      else if (e.key === 'ArrowRight') goPage(_currentPage + 1);
    });
    return _drawer;
  }

  function setStatus(msg) {
    var body = document.getElementById('eos-pdf-body');
    if (body) body.innerHTML = '<div class="eos-pdf-status">' + msg + '</div>';
  }

  async function renderPage(n) {
    if (!_pdfDoc) return;
    n = Math.max(1, Math.min(_pdfDoc.numPages, n | 0));
    _currentPage = n;
    document.getElementById('eos-pdf-page-input').value = n;
    document.getElementById('eos-pdf-prev').disabled = (n <= 1);
    document.getElementById('eos-pdf-next').disabled = (n >= _pdfDoc.numPages);
    var page = await _pdfDoc.getPage(n);
    var viewport = page.getViewport({ scale: _scale });
    var canvas = document.createElement('canvas');
    var ctx = canvas.getContext('2d');
    canvas.height = viewport.height;
    canvas.width = viewport.width;
    var body = document.getElementById('eos-pdf-body');
    body.innerHTML = '';
    body.appendChild(canvas);
    body.scrollTop = 0;
    await page.render({ canvasContext: ctx, viewport: viewport }).promise;
  }

  function goPage(n) {
    if (!_pdfDoc) return;
    renderPage(n).catch(function (e) { setStatus('Render failed: ' + e.message); });
  }

  function open(opts) {
    opts = opts || {};
    if (!opts.path) {
      console.warn('EOS_PDF.open: path required');
      return;
    }
    var page = parseInt(opts.page || 1, 10) || 1;
    var serveBase = opts.serveBase || '/learn/api/pdf';
    var url = serveBase.replace(/\/$/, '') + '/' + opts.path.replace(/^\/+/, '');

    ensureDrawer();
    document.getElementById('eos-pdf-title').textContent = opts.title || opts.path.split('/').pop();
    _onHighlight = typeof opts.onHighlight === 'function' ? opts.onHighlight : null;
    document.getElementById('eos-pdf-highlight').style.display = _onHighlight ? '' : 'none';
    setStatus('Loading PDF.js…');
    _backdrop.classList.add('open');
    _drawer.classList.add('open');

    loadPdfJs()
      .then(function (pdfjs) {
        setStatus('Fetching PDF…');
        return fetch(url, { credentials: 'same-origin' }).then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.arrayBuffer();
        }).then(function (buf) { return pdfjs.getDocument({ data: buf }).promise; });
      })
      .then(function (doc) {
        _pdfDoc = doc;
        document.getElementById('eos-pdf-total').textContent = doc.numPages;
        document.getElementById('eos-pdf-page-input').max = doc.numPages;
        return renderPage(page);
      })
      .catch(function (e) {
        setStatus('Failed to load: ' + (e.message || e));
      });
  }

  function close() {
    if (!_drawer) return;
    _backdrop.classList.remove('open');
    _drawer.classList.remove('open');
  }

  window.EOS_PDF = { open: open, close: close };
})();
