// eos-edit-shim — element-edit pick shim (shared: designer, viz, …).
// Injected into a generated artifact ONLY when served with ?edit=1 (and the
// owning app's feature.element-edit.enabled flag is on). Runs inside the preview
// iframe, which is sandbox="allow-scripts" WITHOUT allow-same-origin — i.e. an
// opaque origin: scripts run, but the parent cannot read this document. So the
// shim resolves the clicked element here (it has a real DOM) and posts the
// anchor out; the parent never touches the iframe DOM. See the host app's
// editing.py + page JS (onEditMessage).
//
// No dependencies. postMessage target is '*' because an opaque-origin frame
// can't know the parent origin, and the payload carries no secret (an anchor id
// + short display text). The parent validates by e.source identity, not origin.
(function () {
  'use strict';
  if (window.__eosEditShim) return;
  window.__eosEditShim = true;

  var HL_ID = '__eos-edit-hl';
  var hl = null;

  function ensureHighlight() {
    if (hl) return hl;
    hl = document.createElement('div');
    hl.id = HL_ID;
    hl.style.cssText = [
      'position:fixed', 'pointer-events:none', 'z-index:2147483646',
      'border:2px solid #3b82f6', 'background:rgba(59,130,246,.10)',
      'border-radius:4px', 'transition:all .04s linear', 'display:none',
      'box-shadow:0 0 0 1px rgba(255,255,255,.6)'
    ].join(';');
    document.documentElement.appendChild(hl);
    return hl;
  }

  function nearestAnchor(node) {
    while (node && node.nodeType === 1) {
      if (node.hasAttribute && node.hasAttribute('data-eos-el')) return node;
      node = node.parentElement;
    }
    return null;
  }

  function paint(el) {
    var box = ensureHighlight();
    if (!el) { box.style.display = 'none'; return; }
    var r = el.getBoundingClientRect();
    box.style.left = r.left + 'px';
    box.style.top = r.top + 'px';
    box.style.width = r.width + 'px';
    box.style.height = r.height + 'px';
    box.style.display = 'block';
  }

  document.addEventListener('mousemove', function (e) {
    paint(nearestAnchor(e.target));
  }, true);

  document.addEventListener('mouseleave', function () { paint(null); }, true);

  // Capture-phase click: intercept BEFORE the page's own handlers/navigation,
  // so editing a link/button doesn't navigate or fire app logic.
  document.addEventListener('click', function (e) {
    var el = nearestAnchor(e.target);
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    var r = el.getBoundingClientRect();
    var payload = {
      type: 'eos-edit-pick',
      el: el.getAttribute('data-eos-el'),
      tag: (el.tagName || '').toLowerCase(),
      text: (el.textContent || '').trim().slice(0, 200),
      rect: { left: r.left, top: r.top, width: r.width, height: r.height }
    };
    try { window.parent.postMessage(payload, '*'); } catch (_) {}
  }, true);

  // Suppress in-page navigation while in edit mode (links shouldn't leave).
  document.addEventListener('submit', function (e) { e.preventDefault(); }, true);

  // Let the parent clear the highlight (e.g. after a pick panel closes).
  window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'eos-edit-clear') paint(null);
  });
})();
