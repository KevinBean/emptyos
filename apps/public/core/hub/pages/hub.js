// hub.js — EmptyOS home page logic.
//
// Extracted from index.html (Phase 0, .claude/rules/multi-module-apps.md §
// frontend) and extended into the companion command-surface (Phase 1,
// docs/HOME-COMPANION-REDESIGN.md). Loads at end of <body>, same scope +
// order as the old inline block, so onclick= handlers and globals resolve.
//
// Layout it drives, top→bottom: ① command bar · ② Next move · ③ Now strip ·
// ④ Today lane · ⑤ Continue lane · ⑥ Explore (the panel aggregator, ambient
// dropped). Only ② is "the companion"; ③④⑤ are pure deterministic digest.

// ── helpers ──────────────────────────────────────────────────────────────
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

// safeUrl / hrefAttr / sanitizeSvg / the app-grid collapse-state helpers /
// hubToggleSection / hubQuickAdd now live in the shared
// /static/eos-hub-renderers.js (loaded before this file in index.html) —
// extracted 2026-08-24 at the 2nd consumer (hub-life) so a security fix to
// the SVG sanitizer or the URL scheme gate can't miss one copy. They're
// still plain globals (`safeUrl`, `hrefAttr`, `sanitizeSvg`,
// `hubToggleSection`, `hubQuickAdd`) — the generated HTML's onclick=/
// onsubmit= strings call them by bare name, same as before.

// `pick` / `flatItems` (field-name tolerance for renderers) and the whole
// RENDERERS map live in the shared bundle too since 2026-10-03 — portal's
// home board renders the same panels. `RENDERERS` stays a page global so
// the markup and tests/js/hub_stat_tile.test.mjs keep resolving it.
var RENDERERS = EOS_HUB_RENDERERS.map;

// Delegated: Show all / Show less on the apps launcher panel.
document.addEventListener('click', function(e){
  var t = e.target;
  if (!t || !t.closest) return;
  var more = t.closest('.hub-app-launcher .r-app-more');
  if (!more) return;
  e.preventDefault();
  var panel = more.closest('.hub-app-launcher');
  if (!panel) return;
  var rest = panel.querySelector('.r-app-grid-rest');
  if (!rest) return;
  var collapsed = more.getAttribute('data-state') === 'collapsed';
  if (collapsed) {
    rest.hidden = false;
    more.setAttribute('data-state', 'expanded');
    more.textContent = 'Show less';
  } else {
    rest.hidden = true;
    more.setAttribute('data-state', 'collapsed');
    var n = more.getAttribute('data-rest-count') || rest.querySelectorAll('.r-app-card').length;
    more.textContent = 'Show all (' + n + ' more)';
  }
});

// ── ① command bar — mode chips ─────────────────────────────────────────────
// Text-aware: with text in the bar, a chip FORCES that route for the text
// (no LLM); with an empty bar it navigates as before.
document.addEventListener('click', function(e){
  var chip = e.target.closest ? e.target.closest('.hub-mode') : null;
  if (!chip) return;
  var mode = chip.getAttribute('data-mode');
  var barText = _searchBar && _searchBar.value ? _searchBar.value() : '';
  if (mode === 'capture') {
    if (barText) { hubForcedRoute(barText, 'capture'); }
    else { location.href = '/quick-action/'; }
  }
  else if (mode === 'ask') {
    location.href = barText ? '/assistant/?q=' + encodeURIComponent(barText) : '/assistant/';
  }
  else if (mode === 'command') {
    try {
      document.dispatchEvent(new KeyboardEvent('keydown', {key:'k', ctrlKey:true, bubbles:true}));
    } catch (_) {
      var inp = document.querySelector('#hub-search-mount input');
      if (inp) inp.focus();
    }
  } else if (mode === 'search') {
    var s = document.querySelector('#hub-search-mount input');
    if (s) s.focus();
  }
});

// ── ② Next move · ③ Now · ④ Today · ⑤ Continue · ⑥ Explore ─────────────────
// The renderers live in /static/eos-hub-renderers.js (`lanes`, `visibleBlocks`,
// `renderBlock`), shared with portal's home board since 2026-10-03 — both
// hosts paint the same /hub/api/* payloads one way. This file keeps only the
// fetches and the element ids.
var _lanes = EOS_HUB_RENDERERS.lanes;
function renderNext(d){ _lanes.next(document.getElementById('hub-next'), d); }
function renderNow(items){ _lanes.now(document.getElementById('hub-now'), items); }
function renderToday(t){ _lanes.today(document.getElementById('hub-today'), t); }

// ⑤ Continue lane — recently touched vault notes.
async function loadContinue(){
  var el = document.getElementById('hub-continue');
  if (!el) return;
  try {
    var res = await fetch('/app-analytics/api/vault/recent?limit=6');
    _lanes.continueNotes(el, res.ok ? await res.json() : []);
  } catch (e) { el.innerHTML = ''; }
}

// ⑥ Explore — the panel aggregator; the visibility policy is the shared one.
async function loadExplore(){
  var el = document.getElementById('hub-explore');
  if (!el) return;
  try {
    var res = await fetch('/hub/api/panels');
    var json = await res.json();
    var split = EOS_HUB_RENDERERS.visibleBlocks(json.blocks || []);
    // hero-weather is a side effect (it fills the header chip), never a
    // visible block — run it even though the filter leaves it out.
    split.weather.forEach(function(b){ try { RENDERERS['hero-weather'](b.items, b); } catch (e) {} });
    var qEl = document.getElementById('hub-quick');
    if (qEl) qEl.innerHTML = split.quick.map(EOS_HUB_RENDERERS.renderBlock).join('');
    el.innerHTML = split.rest.length
      ? ('<div class="hub-lane-head hub-explore-head">Explore</div>' + split.rest.map(EOS_HUB_RENDERERS.renderBlock).join(''))
      : '';
  } catch (e) {
    el.innerHTML = '<div class="err">Failed to load apps: ' + esc(String(e)) + '</div>';
  }
}

// ── digest (③④ + ② template) ───────────────────────────────────────────────
async function loadDigest(){
  try {
    var res = await fetch('/hub/api/digest');
    var d = await res.json();
    _smartRouteOn = d.smart_route !== false;
    renderNext(d); renderNow(d.now); renderToday(d.today);  // instant: template move
    loadNextMove(d);                                        // hydrate ② with Aura (may be slow)
  } catch (e) {
    // Fail-soft: hide the companion zones; Explore still loads.
    var n = document.getElementById('hub-next'); if (n) n.innerHTML = '';
    var w = document.getElementById('hub-now'); if (w) w.innerHTML = '';
    var t = document.getElementById('hub-today'); if (t) t.innerHTML = '';
  }
}

// Swap the template Next move for the Aura-synthesized one once it lands.
// Cached server-side per cadence, so this is near-instant after the first
// hourly call; on a cold call claude-cli can take ~20s — the template move
// is useful in the meantime, and the lanes already painted.
async function loadNextMove(d){
  try {
    var res = await fetch('/hub/api/next-move');
    var j = await res.json();
    if (j && j.move && j.move.title) {
      d.next_move = j.move;
      renderNext(d);  // re-render ② with the Aura move + provenance chip
    }
  } catch (e) { /* keep the template move */ }
}

function refreshAll(){ loadDigest(); loadExplore(); }

// ── chrome: header, search, settings, lucky, tour ──────────────────────────
EOS_UI.pageHeader({
  mount: 'hub-header-mount',
  title: 'EmptyOS',
  actions:
    '<span class="hub-hero-weather" id="hub-hero-weather"></span>'
    + '<button class="btn-settings" onclick="openHubSettings()" title="Hub settings" aria-label="Hub settings">&#9881;</button>',
});

// ── ① the smart bar — ONE typed entry point, routed by intent shape ────────
// Single-token queries keep the instant app-launch behavior (type `exp`,
// Enter opens Expense). Multi-word / question input gets a first-class
// "route this" item at slot 0; Enter classifies via POST /hub/api/route.
// (The old Feeling Lucky button is folded in — its backend is the `open`
// branch of the router.)
var _smartRouteOn = true;
var _searchBar = EOS_UI.searchBar({
  mount: '#hub-search-mount',
  placeholder: 'Type anything — a task, a question, an outcome, an app…',
  smart: {
    when: function(q){ return _smartRouteOn && (q.split(/\s+/).length >= 2 || /[?？]$/.test(q)); },
    label: function(q){ return 'Do this: “' + q + '”'; },
    hint: 'capture · ask · produce · find · open',
    run: hubSmartRoute,
  },
});

function hubSmartRoute(q, ctl) {
  if (ctl && ctl.setBusy) ctl.setBusy('⟳ Routing…');
  EOS.post('/hub/api/route', { text: q }).then(function(resp){
    if (!resp || resp.ok === false) {
      location.href = '/search/?q=' + encodeURIComponent(q);
      return;
    }
    executeRoute(q, resp, ctl);
  }).catch(function(){
    // Route endpoint unreachable — mirror the server's degrade-to-find policy.
    location.href = '/search/?q=' + encodeURIComponent(q);
  });
}

function hubForcedRoute(q, shape) {
  EOS.post('/hub/api/route', { text: q, force: shape }).then(function(resp){
    if (!resp || resp.ok === false) { EOS_UI.toast((resp && resp.error) || 'failed', 'error'); return; }
    executeRoute(q, resp, null);
  }).catch(function(e){ EOS_UI.toast('failed: ' + e, 'error'); });
}

function executeRoute(q, resp, ctl) {
  var a = resp.action || {};
  if (a.kind === 'navigate' && a.href) { location.href = a.href; return; }
  if (a.kind === 'choose') {
    if (ctl && ctl.close) ctl.close();
    openAppChooser(q, a.matches || []);
    return;
  }
  if (a.kind === 'captured') {
    if (ctl && ctl.close) ctl.close();
    if (_searchBar && _searchBar.clear) _searchBar.clear();
    renderRouteResult(resp);
    return;
  }
  location.href = '/search/?q=' + encodeURIComponent(q);
}

// Result strip under the command bar: confirmation + undo + "not it?"
// alternatives (from the route response — no re-classification round trip).
var _routeStripTimer = null;
function renderRouteResult(resp) {
  var el = document.getElementById('hub-route-result');
  if (!el) return;
  var a = resp.action || {};
  var entry = a.entry || {};
  var altsHtml = (resp.alternatives || []).map(function(alt, i){
    if (alt.href) return '<a class="hub-route-alt"' + hrefAttr(alt.href) + '>' + esc(alt.label) + '</a>';
    return '<button class="hub-route-alt" data-alt="' + i + '" title="Re-route this entry: ' + escAttr(alt.label) + '">' + esc(alt.label) + '</button>';
  }).join('');
  var routed = a.routed_to ? ' → ' + esc(a.routed_to) : '';
  var undoBtn = a.undo ? '<button class="hub-route-alt" id="hub-route-undo" title="Undo this capture">Undo</button>' : '';
  el.innerHTML =
    '<div class="hub-route-strip">' +
    '  <span>✓ Captured' + (entry.tag ? ' <span class="eos-badge">' + esc(entry.tag) + '</span>' : '') + routed +
    '  <span class="hub-route-text">' + esc(entry.text || '') + '</span></span>' +
    undoBtn +
    '  <span class="hub-route-not">Not it?</span>' + altsHtml +
    '</div>';
  var undoEl = el.querySelector('#hub-route-undo');
  if (undoEl) undoEl.onclick = function(){
    EOS.post(a.undo.url, a.undo.body).then(function(){
      el.querySelector('.hub-route-strip').firstElementChild.innerHTML = 'Removed.';
      EOS_UI.toast('Capture removed');
    }).catch(function(){ EOS_UI.toast('undo failed', 'error'); });
  };
  el.querySelectorAll('button[data-alt]').forEach(function(btn){
    btn.onclick = function(){
      var alt = (resp.alternatives || [])[+btn.dataset.alt];
      if (alt && alt.post) EOS.post(alt.post.url, alt.post.body).then(function(r){ executeRoute('', r, null); });
    };
  });
  clearTimeout(_routeStripTimer);
  _routeStripTimer = setTimeout(function(){
    if (!el.matches(':hover')) el.innerHTML = '';
    else el.addEventListener('mouseleave', function(){ el.innerHTML = ''; }, { once: true });
  }, 12000);
}

// Settings panel (mandatory — manifest declares [provides.settings]).
var _hubSettings = EOS_UI.settingsPanel({
  id: 'hub-settings-panel',
  title: 'Hub Settings',
  fields: [
    {key: 'hub.show_welcome', label: 'Show welcome card when no panels', type: 'boolean', default: true,
     hint: 'Hides the EmptyOS welcome accent-card on the home page.'},
    {key: 'hub.smart_route', label: 'Smart-route the command bar', type: 'boolean', default: true,
     hint: 'Enter on free text classifies it (capture / ask / produce / find / open) and routes. Off = plain search.'},
  ],
});
function openHubSettings() { _hubSettings.open(); }

// Hide the "Take the tour" button for returning / tour-app-less users.
setTimeout(function(){
  var btn = document.getElementById('hub-tour-btn');
  if (!btn) return;
  if (!window.EOS || !window.EOS.tour) { btn.style.display = 'none'; return; }
  try {
    var raw = localStorage.getItem('eos.tour.v1');
    if (raw) {
      var st = JSON.parse(raw);
      if (st && (st.dismissed || st.completed || st.last_step)) btn.style.display = 'none';
    }
  } catch (e) { /* leave visible on parse failure */ }
}, 200);

// ── App chooser — the `open` route's ambiguous-match modal ─────────────────
// (Heir to the Feeling Lucky button: same cards, but matches arrive pre-fetched
// inside the /api/route response — this never calls the backend itself.)
function openAppChooser(q, matches) {
  var modal = EOS_UI.modal({
    title: '✨ Which app?',
    body: '<div id="lucky-results" style="display:flex;flex-direction:column;gap:8px"></div>',
    width: 540,
  });
  var results = modal.querySelector('#lucky-results');
  var allWeak = matches.length === 0 || matches.every(function(m){ return m.score < 0.4; });
  matches.forEach(function(m){
    var card = document.createElement('div');
    card.style.cssText = 'padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg-card);display:flex;justify-content:space-between;align-items:center;gap:12px';
    var conf = m.score >= 0.7 ? 'strong' : (m.score >= 0.5 ? 'plausible' : 'weak');
    var badgeClass = m.score >= 0.7 ? 'eos-badge-age-fresh' : (m.score >= 0.5 ? 'eos-badge-age-aging' : 'eos-badge-age-stale');
    card.innerHTML = ''
      + '<div style="flex:1;min-width:0">'
      + '  <div style="font-weight:600;display:flex;align-items:center;gap:8px">'
      +     esc(m.name)
      + '    <span class="eos-badge ' + badgeClass + '">' + conf + ' · ' + Math.round(m.score * 100) + '%</span>'
      + '  </div>'
      + '  <div style="font-size:13px;color:var(--text-muted);margin-top:4px">' + esc(m.reason) + '</div>'
      + '</div>'
      + '<a class="eos-btn eos-btn-sm"' + hrefAttr(m.href || ('/' + encodeURIComponent(m.id) + '/')) + ' style="text-decoration:none;flex-shrink:0">Open</a>';
    results.appendChild(card);
  });
  if (allWeak) {
    var fallback = document.createElement('div');
    fallback.style.cssText = 'padding:12px;border:1px dashed var(--border);border-radius:8px;font-size:13px;color:var(--text-muted);display:flex;justify-content:space-between;align-items:center;gap:12px';
    fallback.innerHTML = ''
      + '<div>None of these fit. Want to describe a new app for this?</div>'
      + '<a class="eos-btn eos-btn-sm" href="/app-builder/?intent=' + encodeURIComponent(q) + '" style="text-decoration:none;flex-shrink:0">Build a new app</a>';
    results.appendChild(fallback);
  }
}

// ── launcher mode (?launcher=1) — strip to command bar + apps ──────────────
(function applyLauncherMode() {
  var params = new URLSearchParams(window.location.search);
  if (params.get('launcher') !== '1') return;
  document.body.classList.add('hub-launcher-mode');
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') { try { window.close(); } catch (_) {} }
  });
  // 'body > nav.nav' is eos.js's own globally auto-injected nav bar — was
  // previously hidden for free by the generic body.launcher-mode CSS rule;
  // now that hub opts out of that generic hide (data-own-launcher-mode, see
  // eos-keys.js), this list must hide it explicitly. It's injected via a
  // dynamically-created <script> (async by default), so it can still be
  // absent on the first pass — a short MutationObserver window catches it
  // whenever it actually lands.
  var hide = ['body > nav.nav', '#eos-nav', '#hub-tour-btn', '.eos-page-header', '.hub-modes',
              '#hub-quick', '#hub-next', '#hub-now', '#hub-today', '#hub-continue'];
  function hideLauncherChrome() {
    hide.forEach(function(sel) {
      var el = document.querySelector(sel);
      if (el) el.style.display = 'none';
    });
  }
  hideLauncherChrome();
  var launcherChromeObserver = new MutationObserver(hideLauncherChrome);
  launcherChromeObserver.observe(document.body, {childList: true});
  setTimeout(function() { launcherChromeObserver.disconnect(); }, 3000);
  setTimeout(function() {
    var inp = document.querySelector('#hub-search-mount input');
    if (inp) inp.focus();
  }, 100);
})();

// ── boot ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function(){
  loadDigest();
  loadContinue();
  loadExplore();
});
