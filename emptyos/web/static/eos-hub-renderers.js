// eos-hub-renderers.js — shared hub-panel renderer functions + the SVG
// sanitizer, used by both `apps/public/core/hub/pages/hub.js` (the core
// hub) and `apps/personal/hub-life/pages/index.html` (the personal life
// dashboard, gitignored). Extracted 2026-08-24 at the 2nd consumer
// (CLAUDE.md rule 9) — hub-life had grown a verbatim, hand-copied fork of
// four renderers + the sanitizer, and its copy of `hrefAttr` had silently
// dropped hub.js's scheme/host validation (no `javascript:`/`data:`/
// protocol-relative rejection) — a real gap across EVERY renderer on that
// page, not just the four that were being ported. One copy closes both.
//
// Depends on the already-global `esc` / `escAttr` from eos.js. Load this
// script BEFORE any inline script or page-specific renderer file that
// references these names — both consumers load it near the top of
// <body>, ahead of their own render logic.

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

  // Global names match what the generated HTML's onclick=/onsubmit= strings
  // call by bare identifier — these MUST stay on window under these exact
  // names for both consumers' existing markup to keep resolving them.
  window.safeUrl = safeUrl;
  window.hrefAttr = hrefAttr;
  window.sanitizeSvg = sanitizeSvg;
  window.hubToggleSection = hubToggleSection;
  window.hubQuickAdd = hubQuickAdd;

  window.EOS_HUB_RENDERERS = {
    safeUrl: safeUrl,
    hrefAttr: hrefAttr,
    sanitizeSvg: sanitizeSvg,
    quickAdd: renderQuickAdd,
    appGrid: renderAppGrid,
    entityCard: renderEntityCard,
    gardenMini: renderGardenMini,
  };
})();
