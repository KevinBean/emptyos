// eos-hub-renderers.js — everything a home surface needs to paint the hub's
// payloads: the SVG sanitizer + URL gate, the full RENDERERS map
// (/hub/api/panels blocks), the Explore visibility policy (`visibleBlocks`)
// and the companion lanes (/hub/api/digest: greeting, Next move, Now, Today,
// Continue). Styles: eos-hub-renderers.css.
//
// Three hosts: `apps/public/core/hub/pages/hub.js` (/hub/),
// `apps/public/standard/portal/pages/portal-home.js` (the landing surface's
// home board, since 2026-10-03) and `apps/personal/hub-life/pages/index.html`
// (the personal life dashboard, gitignored; it still carries its own forks of
// most renderers). Extracted 2026-08-24 at the 2nd consumer (CLAUDE.md rule
// 9) — hub-life's hand-copied `hrefAttr` had silently dropped the
// `javascript:`/`data:`/protocol-relative rejection, a gap across every
// renderer on that page. The map, policy and lanes followed on 2026-10-03
// when portal became the 2nd host of those too — one copy keeps the two homes
// agreeing. `scripts/check_hub_panels.py` reads the RENDERERS map here.
//
// Depends on the already-global `esc` / `escAttr` from eos.js. Load this
// script BEFORE any inline script or page-specific renderer file that
// references these names — every host loads it ahead of its own render logic.

(function () {
  'use strict';

  // ── URL / SVG safety (was hub.js-only; hub-life now shares it) ─────────

  // Reject javascript:/data:/vbscript: hrefs from panel contributors — escAttr
  // escapes quotes but does NOT neuter a dangerous scheme. Allow relative URLs
  // and explicit http(s) only. Used by hrefAttr + sanitizeSvg's href check.
  function safeUrl(url) {
    if (!url) return '';
    var s = String(url);
    if (/^(\.\/|#|\?)/.test(s)) return s;               // ./relative, fragment, query
    // Root-relative — but NOT protocol-relative (//host) or backslash-tricked
    // (/\host), which leave the origin while masquerading as a local path.
    if (s.charAt(0) === '/' && s.charAt(1) !== '/' && s.charAt(1) !== '\\') return s;
    try {
      var u = new URL(s, location.origin);
      // Only bless an *explicitly* http(s)-schemed absolute URL. The literal
      // scheme check rejects //host and /\host (which new URL would otherwise
      // resolve to the origin's https scheme) and every non-web scheme.
      if ((u.protocol === 'http:' || u.protocol === 'https:') && /^https?:\/\//i.test(s)) return s;
    } catch (e) { /* unparseable, non-relative → reject */ }
    return '';
  }

  function hrefAttr(url) {
    var safe = safeUrl(url);
    return safe ? ' href="' + escAttr(safe) + '"' : '';
  }

  // Scrub app-contributed SVG before innerHTML insertion. Panels can come from
  // third-party marketplace apps (.claude/rules/store.md), so treat their SVG as
  // semi-trusted. A regex scrub is bypassable (entity-encoded `&#x6a;avascript:`,
  // whitespace-split `java&#9;script:`) because the browser decodes those when
  // the markup hits innerHTML — so parse instead: DOMParser entity-decodes
  // attribute values for us, we drop disallowed elements + on*= handlers, and
  // run every href/xlink:href through the same safeUrl() gate as panel links.
  // No DOMPurify dependency. Anything malformed or not <svg>-rooted is dropped.
  var _SVG_BAD_EL = {
    script: 1, foreignobject: 1, style: 1, set: 1, handler: 1, listener: 1,
    animate: 1, animatetransform: 1, animatemotion: 1, animatecolor: 1,
  };

  function sanitizeSvg(svg) {
    if (!svg) return '';
    try {
      var doc = new DOMParser().parseFromString(String(svg), 'image/svg+xml');
      var root = doc.documentElement;
      // Malformed XML yields a <parsererror> root; non-<svg> root → reject whole.
      if (!root || root.nodeName.toLowerCase().replace(/^.*:/, '') !== 'svg') return '';
      var walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null);
      var drop = [];
      var node = root;
      do {
        var tag = node.nodeName.toLowerCase().replace(/^.*:/, '');
        if (_SVG_BAD_EL[tag]) { drop.push(node); continue; }
        for (var i = node.attributes.length - 1; i >= 0; i--) {
          var attr = node.attributes[i];
          var nm = attr.name.toLowerCase();
          var local = nm.replace(/^.*:/, '');
          if (local.indexOf('on') === 0) { node.removeAttribute(attr.name); continue; }
          if (local === 'href') {
            // attr.value is already entity-decoded by the parser.
            if (!safeUrl(attr.value)) node.removeAttribute(attr.name);
          }
        }
      } while ((node = walker.nextNode()));
      drop.forEach(function(n) { if (n.parentNode) n.parentNode.removeChild(n); });
      return new XMLSerializer().serializeToString(root);
    } catch (e) {
      return '';  // anything unexpected → drop rather than risk it
    }
  }

  // ── app-grid collapse state (localStorage-persisted, size-defaulted) ───

  // Collapse/expand a store_category section in the app-grid launcher panel.
  // Large sections default to collapsed so the launcher stays scannable (the
  // full app set is 180+ cards); the user's per-section choice persists so a
  // reload doesn't re-collapse what they opened.
  var _LAUNCHER_COLLAPSE_THRESHOLD = 15;  // sections with more apps start collapsed

  function _launcherCollapsePrefs() {
    try { return JSON.parse(localStorage.getItem('eos.hub.launcher.collapsed') || '{}') || {}; }
    catch (e) { return {}; }
  }

  function _launcherIsCollapsed(key, count) {
    var prefs = _launcherCollapsePrefs();
    if (key && Object.prototype.hasOwnProperty.call(prefs, key)) return !!prefs[key];
    return count > _LAUNCHER_COLLAPSE_THRESHOLD;  // default: collapse large sections
  }

  function hubToggleSection(hdr) {
    var sec = hdr.closest('.r-app-section');
    if (!sec) return;
    var collapsed = sec.classList.toggle('collapsed');
    var key = sec.getAttribute('data-sec-key');
    if (!key) return;
    try {
      var prefs = _launcherCollapsePrefs();
      prefs[key] = collapsed;
      localStorage.setItem('eos.hub.launcher.collapsed', JSON.stringify(prefs));
    } catch (e) {}
  }

  // ── quick-add submit (resolves whichever page-level refresh fn exists) ──

  function _refreshAfterAdd() {
    if (typeof window.refreshAll === 'function') { window.refreshAll(); return; }
    if (typeof window.loadPanels === 'function') { window.loadPanels(); return; }
  }

  async function hubQuickAdd(ev, formEl, endpoint, fieldName) {
    ev.preventDefault();
    var input = formEl.querySelector('.r-qa-input');
    var btn = formEl.querySelector('.r-qa-btn');
    var val = (input.value || '').trim();
    if (!val) { input.focus(); return false; }
    var payload = {}; payload[fieldName] = val;
    btn.disabled = true; btn.textContent = '…';
    try {
      var res = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      var json = await res.json();
      if (json && json.error) {
        EOS_UI.toast(json.error, false);
      } else {
        EOS_UI.toast('Added', true);
        input.value = '';
        _refreshAfterAdd();
      }
    } catch(e) {
      EOS_UI.toast('Failed to add', false);
    } finally {
      btn.disabled = false; btn.textContent = 'Add';
    }
    return false;
  }

  // ── renderers ────────────────────────────────────────────────────────

  function renderQuickAdd(items) {
    var d = items[0].data;
    var hint = d.hint ? '<div class="r-qa-hint">' + esc(d.hint) + '</div>' : '';
    var head = (d.icon || d.title || d.href)
      ? '<div class="r-qa-head">' +
          '<span class="r-qa-title">' + esc((d.icon ? d.icon + ' ' : '') + (d.title || '')) + '</span>' +
          (d.href ? '<a class="r-qa-open"' + hrefAttr(d.href) + '>Open →</a>' : '') +
        '</div>'
      : '';
    return '<form class="panel r-qa" onsubmit="return hubQuickAdd(event,this,' + escAttr(JSON.stringify(d.endpoint)) + ',' + escAttr(JSON.stringify(d.field || 'text')) + ')">' +
      head +
      '<div class="r-qa-row">' +
        '<input class="r-qa-input" name="' + escAttr(d.field || 'text') + '" type="text" autocomplete="off" placeholder="' + escAttr(d.placeholder || '') + '">' +
        '<button class="r-qa-btn" type="submit" title="Add this entry">Add</button>' +
      '</div>' +
      hint +
    '</form>';
  }

  function renderAppGrid(items, block) {
    var d = items[0].data;
    if (!Array.isArray(d) || !d.length) return '';
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    var renderCard = function(c){
      var desc = c.description ? '<div class="r-app-card-desc">' + esc((c.description || '').slice(0, 90)) + '</div>' : '';
      return '<a class="r-app-card"' + hrefAttr(c.href) + '>' +
        '<span class="r-app-card-icon">' + EOS.appIcon(c.icon_id, c.icon, c.title || c.id) + '</span>' +
        '<span class="r-app-card-copy"><span class="r-app-card-name">' + esc(c.title || c.id) + '</span>' +
        desc + '</span>' +
      '</a>';
    };
    // panel_launcher now returns store_category-grouped sections:
    // [{key,label,icon,count,apps:[{id,title,href,description,icon,icon_id}]}]. Each renders
    // as a collapsible block. (Defensive: a flat [{id,...}] list — older shape —
    // renders as one unlabelled grid.)
    var grouped = d[0] && Array.isArray(d[0].apps);
    var sections = grouped ? d : [{label: '', icon: '', apps: d, count: d.length}];
    if (EOS.registerAppIconIds) {
      EOS.registerAppIconIds([].concat.apply([], sections.map(function(s) {
        return (s.apps || []).map(function(app) { return app.icon_id; });
      })));
    }
    var body = sections.map(function(s){
      var count = Number(s.count) || (s.apps || []).length;
      var cards = (s.apps || []).map(renderCard).join('');
      // Only grouped (labelled) sections collapse; the flat fallback stays open.
      var collapsed = s.label ? _launcherIsCollapsed(s.key, count) : false;
      var head = s.label
        ? '<div class="r-app-sec-hdr" onclick="hubToggleSection(this)">' +
            '<span class="r-app-sec-icon">' + esc(s.icon || '') + '</span>' +
            '<span class="r-app-sec-name">' + esc(s.label) + '</span>' +
            '<span class="r-app-sec-count">' + count + '</span>' +
            '<span class="r-app-sec-toggle">&#9654;</span>' +
          '</div>'
        : '';
      return '<div class="r-app-section' + (collapsed ? ' collapsed' : '') + '"' +
        (s.key ? ' data-sec-key="' + escAttr(s.key) + '"' : '') + '>' +
        head + '<div class="r-app-grid">' + cards + '</div></div>';
    }).join('');
    return '<div class="panel hub-app-launcher">' + hdr + body + '</div>';
  }

  function renderEntityCard(items, block) {
    var d = items[0].data;
    if (!d) return '';
    var fields = (Array.isArray(d.fields) ? d.fields : []).map(function(f){
      if (!f || (f.value == null || f.value === '')) return '';
      return '<div class="r-entity-card-field">' +
        '<div class="r-entity-card-field-label">' + esc(f.label || '') + '</div>' +
        '<div class="r-entity-card-field-value">' + esc(f.value) + '</div>' +
      '</div>';
    }).filter(function(s){ return s; }).join('');
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    var href = d.link || d.href;
    return hdr + '<a class="r-entity-card"' + hrefAttr(href) + '>' +
      '<div class="r-entity-card-title">' + esc(d.title || '') + '</div>' +
      (d.subtitle ? '<div class="r-entity-card-subtitle">' + esc(d.subtitle) + '</div>' : '') +
      (fields ? '<div class="r-entity-card-fields">' + fields + '</div>' : '') +
    '</a>';
  }

  // garden-mini — contributed by apps/garden/. Pre-rendered SVG.
  function renderGardenMini(items, block) {
    var d = items[0].data;
    if (!d || !d.svg) return '';
    var hdr = block.title ? '<div class="panel-title">' + esc(block.title) + '</div>' : '';
    var sub = (d.total_plants != null) ? '<div class="panel-sub" style="font-size:11px;opacity:0.6;margin-top:4px;">' + esc(String(d.total_plants)) + ' growing</div>' : '';
    return '<a class="panel"' + hrefAttr(d.href || '/garden/') + ' style="display:block;text-decoration:none;color:inherit;">' +
      hdr + sanitizeSvg(d.svg) + sub +
    '</a>';
  }

  // ── The renderer map (⑥ Explore) ───────────────────────────────────────
  // Moved here from hub.js on 2026-10-03 at the second consumer (portal's
  // home board). `pick` / `flatItems` tolerate field-name drift between panel
  // contributors and renderer contracts (label vs name, days vs days_remaining).

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

var RENDERERS = {

  'quick-add': renderQuickAdd,

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
    // The weather panel sends "" for an unknown temperature — absent, not 0.
    var hasTemp = d.temperature != null && d.temperature !== '';
    var s = (d.emoji || hasTemp) ? ((d.emoji || '') + ' ' + (hasTemp ? d.temperature + '°' : '')).trim() : '';
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

  'app-grid': renderAppGrid,

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

  'entity-card': renderEntityCard,

  // garden-mini — contributed by apps/garden/. Pre-rendered SVG.
  'garden-mini': renderGardenMini,
};

  // quick-add / checklist handlers (used by the renderers above)
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

  // ── Companion lanes: ② Next move · ③ Now · ④ Today · ⑤ Continue ─────────
  // Moved here from hub.js on 2026-10-03 at the second consumer — portal's
  // home board paints the same /hub/api/digest + /hub/api/next-move payloads
  // under its composer. Each renderer takes the HOST ELEMENT; the two hosts
  // differ only in where a lane lands, never in what it shows.

  // Snooze/Dismiss persist in localStorage keyed by today + the move title, so
  // the same suggestion doesn't re-nag. One key for every host: a move snoozed
  // on the portal stays snoozed on /hub/, which is the point of a snooze.
  function moveKey(d){
    // The LOCAL day: a UTC date rolls over mid-morning in the eastern
    // hemisphere, bringing a move dismissed at 9am back at 10.
    var n = new Date();
    var day = n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0') + '-' + String(n.getDate()).padStart(2, '0');
    return day + '|' + ((d && d.next_move && d.next_move.title) || '');
  }
  function moveSuppressed(d){
    try {
      var raw = localStorage.getItem('hub.companion.suppress');
      if (!raw) return false;
      var o = JSON.parse(raw);
      if (!o || o.key !== moveKey(d)) return false;
      return o.mode === 'dismiss' || (o.until && o.until > Date.now());
    } catch (e) { return false; }
  }
  function suppressMove(d, mode){
    var o = { key: moveKey(d), mode: mode, until: mode === 'snooze' ? Date.now() + 1000 * 60 * 180 : 0 };
    try { localStorage.setItem('hub.companion.suppress', JSON.stringify(o)); } catch (e) {}
  }

  // The time-of-day greeting on its own, for a host whose title already
  // carries the hero copy (portal) and wants the greeting as an eyebrow.
  function renderGreeting(el, d){
    if (!el) return;
    el.textContent = (d && d.greeting) || 'Hello';
  }

  // ② greeting + Next move card. opts.greeting === false leaves the greeting
  // to renderGreeting (portal); the default is hub.js's original shape.
  function renderNext(el, d, opts){
    if (!el) return;
    d = d || {};
    opts = opts || {};
    var greet = opts.greeting === false ? '' : '<div class="hub-greeting">' + esc(d.greeting || 'Hello') + '</div>';
    var nm = d.next_move || {};
    var moveHtml = '';
    if (nm.title && !moveSuppressed(d)) {
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
        suppressMove(d, b.getAttribute('data-defer'));
        var card = el.querySelector('.hub-next-card');
        if (card) card.remove();
      });
    });
  }

  // ③ Now strip — the calendar agenda as one pill.
  function renderNow(el, items){
    if (!el) return;
    if (!items || !items.length) { el.innerHTML = ''; return; }
    var parts = items.map(function(it){
      var time = it.time ? '<span class="hub-now-time">' + esc(it.time) + '</span>' : '';
      return '<span class="hub-now-item">' + time + '<span class="hub-now-title">' + esc(it.title) + '</span></span>';
    }).join('<span class="hub-now-dot">·</span>');
    el.innerHTML = '<a class="hub-now" href="/calendar/"><span class="hub-now-label">Now</span>' + parts + '</a>';
  }

  // ④ Today lane — task + journal chips, then today's task titles.
  function renderToday(el, t){
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

  // ⑤ Continue lane — recently touched vault notes (/app-analytics/api/vault/recent).
  // Takes the fetched rows; the host owns the fetch so it can fail soft its own way.
  function renderContinueNotes(el, items){
    if (!el) return;
    if (!Array.isArray(items) || !items.length) { el.innerHTML = ''; return; }
    var rows = items.slice(0, 6).map(function(it){
      var path = (it.path || '').replace(/\\/g, '/');
      var base = path.split('/').pop().replace(/\.md$/i, '');
      var actions = (typeof EOS !== 'undefined' && EOS.noteActions) ? EOS.noteActions(path) : '';
      return '<span class="hub-cont-item"><span class="hub-cont-name">' + esc(base) + '</span>' + actions + '</span>';
    }).join('');
    el.innerHTML = '<div class="hub-lane"><div class="hub-lane-head">Continue</div>' +
      '<div class="hub-cont-row">' + rows + '</div></div>';
  }

  // ── ⑥ Explore — which /hub/api/panels blocks a home surface shows ────────
  // The filter that used to live inline in hub.js loadExplore. It IS the
  // rendering policy of .claude/rules/hub-panels.md ("what /hub/ actually
  // renders"), so both hosts must apply the same one: ambient (≥150) dropped,
  // pinned-refs always shown, task/calendar sources left to the lanes, the
  // welcome card and the weather chip consumed elsewhere. Quick-add forms are
  // actions, not catalog panels — they go to the host's `.hub-quick-zone`.
  var SUPPRESS_SOURCE = { task: 1, calendar: 1 };
  var ALWAYS_SHOW = { 'pinned-refs': 1 };
  var QUICK_ZONE = { 'outcome-box': 0, 'quick-add': 1 };   // outcome box leads the zone

  function visibleBlocks(blocks){
    blocks = Array.isArray(blocks) ? blocks : [];
    var visible = blocks.filter(function(b){
      if (!b) return false;
      if (b.id in ALWAYS_SHOW) return true;              // honour the pin-to-home promise
      if ((b.priority || 100) >= 150) return false;      // ambient dropped entirely (decision 3)
      if (b.id === 'hub-welcome') return false;          // greeting replaces it
      if (b.renderer === 'hero-weather') return false;   // consumed by header chip
      var src = (b.items && b.items[0] && b.items[0].source) || '';
      if (SUPPRESS_SOURCE[src]) return false;            // surfaced in Now / Today lanes
      return true;
    });
    var quick = visible.filter(function(b){ return b.renderer in QUICK_ZONE; })
                       .sort(function(a, b2){ return QUICK_ZONE[a.renderer] - QUICK_ZONE[b2.renderer]; });
    var rest = visible.filter(function(b){ return !(b.renderer in QUICK_ZONE); });
    // hero-weather is a side effect (it fills #hub-hero-weather), never a
    // visible block — hand it back so the host can still run it.
    var weather = blocks.filter(function(b){ return b && b.renderer === 'hero-weather'; });
    return { quick: quick, rest: rest, weather: weather };
  }

  function renderBlock(b){
    var fn = RENDERERS[b.renderer];
    if (!fn) {
      return '<div class="panel err">Unknown renderer: <code>' + esc(b.renderer) + '</code> (panel <code>' + esc(b.id) + '</code>)</div>';
    }
    try { return fn(b.items, b); }
    catch (e) { return '<div class="panel err">Render error in ' + esc(b.id) + ': ' + esc(String(e)) + '</div>'; }
  }

  // Global names match what the generated HTML's onclick=/onsubmit= strings
  // call by bare identifier — these MUST stay on window under these exact
  // names for every host's existing markup to keep resolving them.
  window.safeUrl = safeUrl;
  window.hrefAttr = hrefAttr;
  window.sanitizeSvg = sanitizeSvg;
  window.hubToggleSection = hubToggleSection;
  window.hubQuickAdd = hubQuickAdd;
  window.hubOutcomeGo = hubOutcomeGo;
  window.hubToggleCheck = hubToggleCheck;

  window.EOS_HUB_RENDERERS = {
    safeUrl: safeUrl,
    hrefAttr: hrefAttr,
    sanitizeSvg: sanitizeSvg,
    quickAdd: renderQuickAdd,
    appGrid: renderAppGrid,
    entityCard: renderEntityCard,
    gardenMini: renderGardenMini,
    // The full renderer map + the Explore policy.
    map: RENDERERS,
    visibleBlocks: visibleBlocks,
    renderBlock: renderBlock,
    // Companion lanes; each takes the host element.
    lanes: {
      greeting: renderGreeting,
      next: renderNext,
      now: renderNow,
      today: renderToday,
      continueNotes: renderContinueNotes,
    },
    moveSuppressed: moveSuppressed,
    suppressMove: suppressMove,
  };
})();
