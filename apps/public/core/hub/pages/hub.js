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

// Pick the first non-null/non-empty field from an object. Tolerates field-name
// drift between panel contributors and renderer contracts (label vs name,
// title vs name, days vs days_remaining, etc.).
function pick(obj /*, ...names */) {
  for (var i = 1; i < arguments.length; i++) {
    var v = obj[arguments[i]];
    if (v != null && v !== '') return v;
  }
  return null;
}

// Flatten panel.data which may be a single object or a list of items, across
// all panels in a group. Used by group renderers (chips, countdown-tile).
function flatItems(items) {
  var all = [];
  items.forEach(function(p) {
    var d = p.data;
    if (d == null) return;
    if (!Array.isArray(d)) d = [d];
    all = all.concat(d);
  });
  return all;
}

// ── panel renderers (⑥ Explore) ───────────────────────────────────────────
var RENDERERS = {

  'quick-add': EOS_HUB_RENDERERS.quickAdd,

  // outcome-box — the outcome-first front door (docs/WORK-SURFACE-PIVOT.md
  // Phase 3). Same r-qa chrome as quick-add but NAVIGATES to the owning app
  // (d.href + ?ask=<text>) instead of POSTing — a deliverable run is
  // multi-minute and needs its plan preview, not a fire-and-forget add.
  // Data contract: {box_id?, icon?, title?, href, placeholder?, kinds?: [labels]}
  'outcome-box': function(items){
    var d = items[0].data;
    var kinds = (d.kinds || []).join(' · ');
    var hint = kinds ? '<div class="r-qa-hint">' + esc(kinds) + '</div>' : '';
    return '<form class="panel r-qa r-outcome" id="' + escAttr(d.box_id || 'hub-outcome-box') + '"' +
      ' onsubmit="return hubOutcomeGo(event,this,' + escAttr(JSON.stringify(d.href || '/work/')) + ')">' +
      '<div class="r-qa-head">' +
        '<span class="r-qa-title">' + esc((d.icon ? d.icon + ' ' : '') + (d.title || 'Produce')) + '</span>' +
        (d.href ? '<a class="r-qa-open"' + hrefAttr(d.href) + '>Open →</a>' : '') +
      '</div>' +
      '<div class="r-qa-row">' +
        '<input class="r-qa-input" name="ask" type="text" autocomplete="off" placeholder="' + escAttr(d.placeholder || '') + '">' +
        '<button class="r-qa-btn" type="submit" title="Open the work app with a plan for this outcome">Plan it</button>' +
      '</div>' +
      hint +
    '</form>';
  },

  'accent-card': function(items){
    var d = items[0].data;
    var btn = d.button_label ? '<span class="r-accent-card-btn">' + esc(d.button_label) + '</span>' : '';
    return '<a class="r-accent-card"' + hrefAttr(d.url) + '>' +
      '<div>' +
        '<div class="r-accent-card-label">' + esc(d.label || '') + '</div>' +
        '<div class="r-accent-card-text">' + esc(d.text || '') + '</div>' +
      '</div>' + btn +
    '</a>';
  },

  'hero-weather': function(items){
    var d = items[0].data || {};
    var hasData = d.emoji || d.temperature != null;
    var s = hasData ? ((d.emoji || '') + ' ' + (d.temperature != null ? d.temperature + '°' : '')).trim() : '';
    var chip = document.getElementById('hub-hero-weather');
    if (chip) chip.textContent = s;
    return '';
  },

  'tiles-row': function(items){
    var d = items[0].data;
    if (!Array.isArray(d)) d = [d];
    return '<div class="panel"><div class="r-tiles-row">' + d.map(function(t){
      return '<a class="r-tile"' + hrefAttr(t.href) + '>' +
        '<div class="r-tile-val ' + escAttr(t.tone || '') + '">' + esc(t.value) + '</div>' +
        '<div class="r-tile-label">' + esc(t.label) + '</div>' +
      '</a>';
    }).join('') + '</div></div>';
  },

  'stat-tile': function(items){
    /* When grouped (group="dashboard"), render all items as a tile grid.
       When standalone, render single tile. */
    var tiles = items.map(function(p){
      var d = p && p.data;
      if (d == null) return '';  // null/lazy contributor — skip its tile
      // `icon` is part of the documented contract (BaseApp.stat_tile) and the
      // personal hub renders it; this renderer never did, while 14 of the 40
      // live dashboard tiles were passing one (2026-09-03).
      return '<a class="r-stat-tile"' + hrefAttr(d.href) + '>' +
        '<div class="r-stat-val">' + esc(d.value) + '</div>' +
        '<div class="r-stat-label">' + esc((d.icon ? d.icon + ' ' : '') + (d.label || '')) + '</div>' +
        (d.sub ? '<div class="r-stat-sub">' + esc(d.sub) + '</div>' : '') +
      '</a>';
    }).join('');
    return '<div class="r-dashboard-grid">' + tiles + '</div>';
  },

  'plain-list': function(items, block){
    var d = items[0].data;
    if (d == null) return '';  // lazy placeholder before hydration
    if (!Array.isArray(d)) d = [d];
    var rows = d.filter(function(r){ return r != null; }).map(function(r){
      // Tolerate both `title/subtitle` and `label/sub` row shapes —
      // both appear across contributing apps.
      var title = pick(r, 'title', 'label', 'name') || '';
      var subtxt = pick(r, 'subtitle', 'sub', 'date');
      var sub = subtxt ? '<div class="r-list-sub">' + esc(subtxt) + '</div>' : '';
      return '<a class="r-list-row"' + hrefAttr(r.href) + '>' +
        (r.icon ? '<span class="r-list-icon">' + esc(r.icon) + '</span>' : '') +
        '<div class="r-list-body"><div class="r-list-title">' + esc(title) + '</div>' + sub + '</div>' +
      '</a>';
    }).join('');
    if (!rows) return '';
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    return '<div class="panel">' + hdr + '<div class="r-list">' + rows + '</div></div>';
  },

  'task-list': function(items, block){
    var d = items[0].data;
    if (!Array.isArray(d) || !d.length) return '';
    var rows = d.map(function(t){
      var doneCls = t.done ? ' done' : '';
      var badge = t.tone ? '<span class="r-list-badge ' + escAttr(t.tone) + '">' + esc(t.tone) + '</span>' : '';
      return '<a class="r-list-row' + doneCls + '"' + hrefAttr(t.href || '/task/') + '>' +
        '<div class="r-list-body"><div class="r-list-title">' + esc(t.text || t.title) + '</div></div>' + badge +
      '</a>';
    }).join('');
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    return '<div class="panel">' + hdr + '<div class="r-list">' + rows + '</div></div>';
  },

  'deadline-row': function(items, block){
    var d = items[0].data;
    if (!Array.isArray(d) || !d.length) return '';
    var rows = d.map(function(r){
      var title = pick(r, 'title', 'name');
      if (!title) return '';
      var days = pick(r, 'days_remaining', 'days');
      var badge = days != null
        ? '<span class="r-list-badge">' + esc(days) + 'd</span>'
        : '';
      var subtxt = pick(r, 'subtitle', 'date');
      var sub = subtxt ? '<div class="r-list-sub">' + esc(subtxt) + '</div>' : '';
      return '<a class="r-list-row"' + hrefAttr(r.href) + '>' +
        '<div class="r-list-body"><div class="r-list-title">' + esc(title) + '</div>' + sub + '</div>' + badge +
      '</a>';
    }).filter(function(s){ return s; }).join('');
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    return '<div class="panel">' + hdr + '<div class="r-list">' + rows + '</div></div>';
  },

  'app-grid': EOS_HUB_RENDERERS.appGrid,

  'chips': function(items, block){
    var all = flatItems(items);
    if (!all.length) return '';
    var chips = all.map(function(c){
      return '<a class="r-chip"' + hrefAttr(c.href) + '>' + esc(pick(c, 'title', 'label')) + '</a>';
    }).join('');
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    return '<div class="panel">' + hdr + '<div class="r-chips">' + chips + '</div></div>';
  },

  'countdown-tile': function(items){
    var tiles = flatItems(items).map(function(d){
      if (d == null || d.days == null) return '';
      var label = pick(d, 'label', 'name') || '';
      return '<a class="r-countdown"' + hrefAttr(d.href) + '>' +
        '<div class="r-countdown-days">' + esc(d.days) + 'd</div>' +
        '<div class="r-countdown-label">' + esc(label) + '</div>' +
        (d.date ? '<div class="r-countdown-date">' + esc(d.date) + '</div>' : '') +
      '</a>';
    }).join('');
    if (!tiles) return '';
    return '<div class="r-countdowns">' + tiles + '</div>';
  },

  'compare-tile': function(items){
    // Filter out items without usable data so the panel doesn't render
    // a husk (e.g. just "vs" with no numbers) when no source has data yet.
    items = items.filter(function(p){
      var d = p.data || {};
      return d.label && (d.now != null || d.prev != null);
    });
    if (!items.length) return '';
    var tiles = items.map(function(p){
      var d = p.data;
      var deltaCls = (d.delta_pct || 0) >= 0 ? 'up' : 'down';
      var deltaSign = (d.delta_pct || 0) >= 0 ? '+' : '';
      return '<div class="r-compare">' +
        '<div class="r-compare-label">' + esc(d.label) + '</div>' +
        '<div class="r-compare-vals">' +
          '<span class="r-compare-now">' + esc(d.now) + '</span>' +
          '<span class="r-compare-prev">vs ' + esc(d.prev) + '</span>' +
          (d.delta_pct != null ? '<span class="r-compare-delta ' + deltaCls + '">' + deltaSign + d.delta_pct + '%</span>' : '') +
        '</div>' +
      '</div>';
    }).join('');
    return '<div class="r-compare-grid">' + tiles + '</div>';
  },

  'text-card': function(items){
    var d = items[0].data;
    return '<div class="r-text-card">' +
      (d.title ? '<div class="r-text-card-title">' + esc(d.title) + '</div>' : '') +
      '<div class="r-text-card-body">' + esc(d.body || d.text || '') + '</div>' +
    '</div>';
  },

  'quote': function(items){
    var d = items[0].data;
    return '<div class="panel"><div class="r-quote">' + esc(d.text || d.quote || '') +
      (d.author ? '<span class="r-quote-attr">— ' + esc(d.author) + '</span>' : '') +
    '</div></div>';
  },

  'bar': function(items, block){
    /* Goals progress bars. Each panel returns dict {name,pct,detail} or list of same.
       When grouped (group="goals"), all bars render as one stacked panel. */
    var all = [];
    items.forEach(function(p){
      var d = p && p.data;
      if (d == null) return;  // null/lazy contributor — skip
      if (!Array.isArray(d)) d = [d];
      all = all.concat(d);
    });
    all = all.filter(function(b){ return b != null; });
    if (!all.length) return '';
    var rows = all.map(function(b){
      var pct = Math.max(0, Math.min(100, Number(b.pct) || 0));
      return '<div class="r-bar-row">' +
        '<div class="r-bar-head"><span class="r-bar-name">' + esc(b.name || '') + '</span>' +
          '<span class="r-bar-detail">' + esc(b.detail || (pct + '%')) + '</span></div>' +
        '<div class="r-bar-track"><div class="r-bar-fill" style="width:' + pct + '%"></div></div>' +
      '</div>';
    }).join('');
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    return '<div class="panel">' + hdr + '<div class="r-bars">' + rows + '</div></div>';
  },

  'checklist': function(items, block){
    var d = items[0].data;
    if (!Array.isArray(d) || !d.length) return '';
    var endpoint = (d[0] && d[0].toggle_endpoint) || '';
    var rows = d.map(function(it){
      var doneCls = it.done ? ' done' : '';
      // Coerce to a real integer before interpolating into the inline handler:
      // toggle_index is server-provided panel data, and a non-numeric value
      // would break out of the JS context (XSS). Number.isInteger rejects
      // NaN / strings / floats; endpoint is already escAttr(JSON)-safe.
      var idx = Number(it.toggle_index);
      var onclick = (endpoint && Number.isInteger(idx))
        ? ' onclick="hubToggleCheck(this,' + escAttr(JSON.stringify(endpoint)) + ',' + idx + ')"'
        : '';
      return '<div class="r-check-row' + doneCls + '"' + onclick + '>' +
        '<div class="r-check-box">' + (it.done ? '✓' : '') + '</div>' +
        '<div class="r-check-text">' + esc(it.text || '') + '</div>' +
      '</div>';
    }).join('');
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    return '<div class="panel">' + hdr + '<div class="r-checklist">' + rows + '</div></div>';
  },

  'next-up': function(items, block){
    var d = items[0].data;
    if (!d) return '';
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    return '<div class="panel">' + hdr + '<div class="r-nextup">' +
      '<div class="r-nextup-time">' + esc(d.time || '') + '</div>' +
      '<div class="r-nextup-body">' +
        '<div class="r-nextup-event">' + esc(d.event || '') + '</div>' +
        (d.tag ? '<div class="r-nextup-tag">' + esc(d.tag) + '</div>' : '') +
      '</div>' +
    '</div></div>';
  },

  'slot-list': function(items, block){
    var d = items[0].data;
    if (!Array.isArray(d) || !d.length) return '';
    var rows = d.map(function(r){
      var title = r.title || r.text || '';
      var sub = r.subtitle ? '<div class="r-list-sub">' + esc(r.subtitle) + '</div>' : '';
      var badge = (r.badge || r.tag)
        ? '<span class="r-list-badge">' + esc(r.badge || r.tag) + '</span>' : '';
      return '<a class="r-list-row"' + hrefAttr(r.href) + '>' +
        '<div class="r-list-body"><div class="r-list-title">' + esc(title) + '</div>' + sub + '</div>' + badge +
      '</a>';
    }).join('');
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    return '<div class="panel">' + hdr + '<div class="r-list">' + rows + '</div></div>';
  },

  'media-card': function(items){
    var d = items[0].data;
    if (!d) return '';
    return '<a class="r-media-card"' + hrefAttr(d.href) + '>' +
      (d.emoji ? '<div class="r-media-emoji">' + esc(d.emoji) + '</div>' : '') +
      '<div>' +
        '<div class="r-media-title">' + esc(d.title || '') + '</div>' +
        (d.subtitle ? '<div class="r-media-subtitle">' + esc(d.subtitle) + '</div>' : '') +
      '</div>' +
    '</a>';
  },

  'entity-card': EOS_HUB_RENDERERS.entityCard,

  // garden-mini — contributed by apps/garden/. Pre-rendered SVG.
  'garden-mini': EOS_HUB_RENDERERS.gardenMini,
};

// ── quick-add / checklist handlers (used by renderers above) ───────────────
function hubOutcomeGo(ev, formEl, href){
  ev.preventDefault();
  var val = (formEl.querySelector('.r-qa-input').value || '').trim();
  var sep = href.indexOf('?') >= 0 ? '&' : '?';
  window.location.href = val ? href + sep + 'ask=' + encodeURIComponent(val) : href;
  return false;
}

async function hubToggleCheck(rowEl, endpoint, index){
  rowEl.classList.toggle('done');
  var box = rowEl.querySelector('.r-check-box');
  if (box) box.textContent = rowEl.classList.contains('done') ? '✓' : '';
  try {
    await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({index: index})});
  } catch (e) { /* leave optimistic toggle */ }
}

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

// ── ② Next move (the companion) ────────────────────────────────────────────
// Snooze/Dismiss persist in localStorage keyed by today + the move title, so
// the same suggestion doesn't re-nag. (Phase 1 client-side; Phase 2 moves the
// memory server-side alongside the Aura synthesis.)
function _moveKey(d){
  var day = new Date().toISOString().slice(0, 10);
  return day + '|' + ((d.next_move && d.next_move.title) || '');
}
function _moveSuppressed(d){
  try {
    var raw = localStorage.getItem('hub.companion.suppress');
    if (!raw) return false;
    var o = JSON.parse(raw);
    if (!o || o.key !== _moveKey(d)) return false;
    return o.mode === 'dismiss' || (o.until && o.until > Date.now());
  } catch (e) { return false; }
}
function _suppressMove(d, mode){
  var o = { key: _moveKey(d), mode: mode, until: mode === 'snooze' ? Date.now() + 1000 * 60 * 180 : 0 };
  try { localStorage.setItem('hub.companion.suppress', JSON.stringify(o)); } catch (e) {}
}

function renderNext(d){
  var el = document.getElementById('hub-next');
  if (!el) return;
  var greet = '<div class="hub-greeting">' + esc(d.greeting || 'Hello') + '</div>';
  var nm = d.next_move || {};
  var moveHtml = '';
  if (nm.title && !_moveSuppressed(d)) {
    // Provenance chip only when the move was AI-synthesized (Aura). The
    // deterministic template carries no chip — it isn't AI-authored.
    var prov = '';
    if (nm.source === 'aura' && nm.provenance && nm.provenance.mode && typeof EOS_UI !== 'undefined' && EOS_UI.provenance) {
      prov = '<span class="hub-next-prov">' + EOS_UI.provenance({
        mode: nm.provenance.mode,
        provider: nm.provenance.provider,
        model: nm.provenance.model,
        title: 'Suggested by Aura',
      }) + '</span>';
    }
    moveHtml =
      '<div class="hub-next-card tone-' + escAttr(nm.tone || 'calm') + '">' +
        '<div class="hub-next-eyebrow">Next' + prov + '</div>' +
        '<div class="hub-next-title">' + esc(nm.title) + '</div>' +
        (nm.why ? '<div class="hub-next-why">Suggested because: ' + esc(nm.why) + '</div>' : '') +
        '<div class="hub-next-actions">' +
          '<a class="eos-btn eos-btn-sm hub-next-go"' + hrefAttr(nm.action_href) + '>' + esc(nm.action_label || 'Open') + '</a>' +
          '<button type="button" class="hub-next-defer" data-defer="snooze" title="Hide this suggestion until later today">Snooze</button>' +
          '<button type="button" class="hub-next-defer" data-defer="dismiss" title="Dismiss this suggestion">Dismiss</button>' +
        '</div>' +
      '</div>';
  }
  el.innerHTML = greet + moveHtml;
  el.querySelectorAll('.hub-next-defer').forEach(function(b){
    b.addEventListener('click', function(){
      _suppressMove(d, b.getAttribute('data-defer'));
      var card = el.querySelector('.hub-next-card');
      if (card) card.remove();
    });
  });
}

// ── ③ Now strip ────────────────────────────────────────────────────────────
function renderNow(items){
  var el = document.getElementById('hub-now');
  if (!el) return;
  if (!items || !items.length) { el.innerHTML = ''; return; }
  var parts = items.map(function(it){
    var time = it.time ? '<span class="hub-now-time">' + esc(it.time) + '</span>' : '';
    return '<span class="hub-now-item">' + time + '<span class="hub-now-title">' + esc(it.title) + '</span></span>';
  }).join('<span class="hub-now-dot">·</span>');
  el.innerHTML = '<a class="hub-now" href="/calendar/"><span class="hub-now-label">Now</span>' + parts + '</a>';
}

// ── ④ Today lane ───────────────────────────────────────────────────────────
function renderToday(t){
  var el = document.getElementById('hub-today');
  if (!el) return;
  if (!t) { el.innerHTML = ''; return; }
  var chips = [];
  if (t.overdue > 0) chips.push('<a class="hub-chip-stat overdue" href="/task/">' + t.overdue + ' overdue</a>');
  if (t.due_today > 0) chips.push('<a class="hub-chip-stat today" href="/task/">' + t.due_today + ' due today</a>');
  chips.push('<a class="hub-chip-stat ' + (t.journaled ? 'ok' : '') + '" href="/journal/">' + (t.journaled ? 'journaled ✓' : 'not journaled') + '</a>');
  if (t.streak > 0) chips.push('<span class="hub-chip-stat">' + t.streak + '-day streak</span>');
  var rows = '';
  if (t.tasks && t.tasks.length) {
    rows = '<div class="hub-today-tasks">' + t.tasks.map(function(x){
      return '<a class="hub-today-task" href="/task/">' + esc(x) + '</a>';
    }).join('') + '</div>';
  }
  el.innerHTML = '<div class="hub-lane"><div class="hub-lane-head">Today</div>' +
    '<div class="hub-chip-row">' + chips.join('') + '</div>' + rows + '</div>';
}

// ── ⑤ Continue lane — recently touched vault notes ─────────────────────────
async function loadContinue(){
  var el = document.getElementById('hub-continue');
  if (!el) return;
  try {
    var res = await fetch('/app-analytics/api/vault/recent?limit=6');
    if (!res.ok) { el.innerHTML = ''; return; }
    var items = await res.json();
    if (!Array.isArray(items) || !items.length) { el.innerHTML = ''; return; }
    var rows = items.slice(0, 6).map(function(it){
      var path = (it.path || '').replace(/\\/g, '/');
      var base = path.split('/').pop().replace(/\.md$/i, '');
      var actions = (typeof EOS !== 'undefined' && EOS.noteActions) ? EOS.noteActions(path) : '';
      return '<span class="hub-cont-item"><span class="hub-cont-name">' + esc(base) + '</span>' + actions + '</span>';
    }).join('');
    el.innerHTML = '<div class="hub-lane"><div class="hub-lane-head">Continue</div>' +
      '<div class="hub-cont-row">' + rows + '</div></div>';
  } catch (e) { el.innerHTML = ''; }
}

// ── ⑥ Explore — the panel aggregator, ambient dropped ──────────────────────
function renderExploreBlock(b){
  var fn = RENDERERS[b.renderer];
  if (!fn) {
    return '<div class="panel err">Unknown renderer: <code>' + esc(b.renderer) + '</code> (panel <code>' + esc(b.id) + '</code>)</div>';
  }
  try { return fn(b.items, b); }
  catch (e) { return '<div class="panel err">Render error in ' + esc(b.id) + ': ' + esc(String(e)) + '</div>'; }
}

async function loadExplore(){
  var el = document.getElementById('hub-explore');
  if (!el) return;
  try {
    var res = await fetch('/hub/api/panels');
    var json = await res.json();
    var blocks = json.blocks || [];
    // Always run hero-weather (side-effect: populates the header chip), even
    // though it's filtered out of the visible Explore list below.
    blocks.forEach(function(b){
      if (b.renderer === 'hero-weather') { try { RENDERERS['hero-weather'](b.items, b); } catch (e) {} }
    });
    // Explore = cognitive panels only, minus what the lanes already surface.
    var SUPPRESS_SOURCE = { task: 1, calendar: 1 };
    // User-pinned content is never "ambient" — the 📌 Pin affordance promises it
    // lands on the home, so it surfaces regardless of its (ambient-band) priority.
    var ALWAYS_SHOW = { 'pinned-refs': 1 };
    var visible = blocks.filter(function(b){
      if (b.id in ALWAYS_SHOW) return true;              // honour the pin-to-home promise
      if ((b.priority || 100) >= 150) return false;      // ambient dropped entirely (decision 3)
      if (b.id === 'hub-welcome') return false;          // greeting replaces it
      if (b.renderer === 'hero-weather') return false;   // consumed by header chip
      var src = (b.items && b.items[0] && b.items[0].source) || '';
      if (SUPPRESS_SOURCE[src]) return false;            // surfaced in Now / Today lanes
      return true;
    });
    // Quick-add forms (expense log, etc.) are actions, not catalog panels —
    // pin them to the dedicated #hub-quick zone near the top. The rest render
    // under the Explore head below.
    var qEl = document.getElementById('hub-quick');
    var QUICK_ZONE = { 'outcome-box': 0, 'quick-add': 1 };   // outcome box leads the zone
    var quick = visible.filter(function(b){ return b.renderer in QUICK_ZONE; })
                       .sort(function(a, b2){ return QUICK_ZONE[a.renderer] - QUICK_ZONE[b2.renderer]; });
    var rest = visible.filter(function(b){ return !(b.renderer in QUICK_ZONE); });
    if (qEl) qEl.innerHTML = quick.map(renderExploreBlock).join('');
    el.innerHTML = rest.length
      ? ('<div class="hub-lane-head hub-explore-head">Explore</div>' + rest.map(renderExploreBlock).join(''))
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
