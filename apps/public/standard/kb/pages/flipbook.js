var STATE = {
  page: null, parents: [], loading: false,
  edit: false, dirty: false,
  preferredMode: "svg",  // user's last chosen mode
  fast: false,           // when on, image renders use low-step path (~2-4s)
  selectedSvgEl: null,   // selected SVG element for symbol extraction
  embed: false,          // ?embed=1 — render without topbar/actions for iframe use
};

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function render() {
  var root = document.getElementById("root");

  if (!STATE.page && !STATE.loading) {
    root.innerHTML =
      '<div class="ex-shell">' +
        '<div class="ex-start">' +
          '<h2>Explore visually.</h2>' +
          '<p>Type a topic. Click a callout to dive deeper.</p>' +
          '<input id="ex-topic" type="text" autofocus ' +
            'placeholder="e.g. underground power cables" />' +
          '<div class="hint">Press Enter to begin. · <a href="/kb/?view=network" class="ex-graph-link">View vault graph →</a> · <span id="ex-model-pill"></span></div>' +
          '<div id="ex-saved" class="ex-saved-list">' +
            '<div class="ex-saved-loading">Loading saved explorations…</div>' +
          '</div>' +
          '<div id="ex-symbols" class="ex-saved-list"></div>' +
        '</div>' +
      '</div>';
    var input = document.getElementById("ex-topic");
    input.addEventListener("keydown", function(e) {
      if (e.key === "Enter" && input.value.trim()) {
        startExploration(input.value.trim());
      }
    });
    loadSavedList();
    loadSymbolLibrary();
    // Model pill — every generate/expand here spends the active think (+draw)
    // provider; make the cost class visible + switchable (FDL §6).
    if (window.EOS_UI && EOS_UI.modelPill) EOS_UI.modelPill({ app: 'kb', mount: '#ex-model-pill' });
    return;
  }

  var p = STATE.page || {};
  var crumbs = (p.breadcrumb || []).map(function(c, i, arr) {
    var cls = i === arr.length - 1 ? "crumb-active" : "";
    return '<span class="' + cls + '">' + esc(c) + '</span>';
  }).join('<span class="crumb-sep">/</span>');

  // Split callouts into left/right columns (shared geometry — eos-flipbook.js).
  var _cols = EOS_FLIP.splitCallouts(p.callouts);
  var leftItems = _cols.left, rightItems = _cols.right;

  function renderCol(items, side) {
    return items.map(function(it) {
      var c = it.c;
      var actions = STATE.edit ? '' :
        '<div class="ex-callout-actions">' +
          '<button class="ex-mini-btn" data-act="peek" data-idx="' + it.idx +
            '" title="Quick detail (no navigation)">+ peek</button>' +
          '<button class="ex-mini-btn" data-act="dive" data-idx="' + it.idx +
            '" title="Dive into a full page">→ dive</button>' +
        '</div>';
      return (
        '<div class="ex-callout" data-idx="' + it.idx + '" ' +
          'data-side="' + side + '" tabindex="0">' +
          '<div class="ex-callout-label" data-field="callout-label" ' +
            'data-callout-idx="' + it.idx + '" ' + editable + '>' +
            esc(c.label || "") + '</div>' +
          '<div class="ex-callout-body" data-field="callout-body" ' +
            'data-callout-idx="' + it.idx + '" ' + editable + '>' +
            esc(c.body || "") + '</div>' +
          actions +
        '</div>'
      );
    }).join("");
  }

  var statusBadge = '';
  if (STATE.dirty) {
    statusBadge = '<span class="ex-status dirty">unsaved edits</span>';
  } else if (p.verified) {
    statusBadge = '<span class="ex-status verified">✓ verified</span>';
  } else if (p.from_cache) {
    statusBadge = '<span class="ex-status cached">draft (cached)</span>';
  } else if (p.saved) {
    statusBadge = '<span class="ex-status draft">AI draft</span>';
  }

  var kbLink = "";
  if (p.verified && p._topic) {
    var slug = (p._topic || "").toLowerCase()
      .replace(/[^a-z0-9一-鿿]+/g, "-").replace(/^-+|-+$/g, "");
    kbLink = '<a class="ex-btn secondary" href="/kb/#' + encodeURIComponent(slug) +
      '" title="Open in Knowledge Base">KB ↗</a>';
  }

  var currentMode = (p.mode || "svg");
  var modeToggle = STATE.edit ? "" : (
    '<div class="ex-mode-toggle">' +
      '<button class="' + (currentMode === "svg" ? "active" : "") +
        '" onclick="switchMode(\'svg\')" ' +
        'title="LLM-drawn structural diagram with labeled callouts">SVG</button>' +
      '<button class="' + (currentMode === "image" ? "active" : "") +
        '" onclick="switchMode(\'image\')" ' +
        'title="Image-gen illustration + LLM callouts">Image</button>' +
    '</div>' +
    '<button class="ex-fast-toggle' + (STATE.fast ? " active" : "") +
      '" onclick="toggleFast()" ' +
      'title="Fast mode: low-step image render (~2-4s, lower quality). Affects next image regenerate.">' +
      '⚡ Fast</button>' +
    (currentMode === "image" && p.image_url
      ? '<button class="ex-fast-toggle" onclick="refineAnchors(\'local\')" ' +
          'title="Vision pass via local think provider (free if your active model is vision-capable).">' +
          '🎯 Refine (local)</button>' +
        (p._cloudAvailable
          ? '<button class="ex-fast-toggle active" ' +
              'onclick="refineAnchors(\'openai\')" ' +
              'title="Local vision failed. Use OpenAI ' +
              escAttr(p._cloudModel || "gpt-4o-mini") + ' (~$' +
              (p._cloudCost || 0.001).toFixed(3) + ' per refine).">' +
              '🎯 Refine via OpenAI · ~$' +
              (p._cloudCost || 0.001).toFixed(3) + '</button>'
          : "")
      : ""));

  var hasSelection = STATE.edit && STATE.selectedSvgEl;
  var symbolBtn = (STATE.edit && currentMode === "svg" && p.svg)
    ? '<button class="ex-btn secondary" onclick="saveAsSymbol()" ' +
        'title="' + (hasSelection
          ? "Save the selected element as a reusable symbol."
          : "Click any element in the diagram to select, or save the whole SVG.") +
        '">◇ Save ' + (hasSelection ? "selection" : "whole") +
        ' as symbol</button>'
    : '';

  var actionBtns =
    statusBadge + kbLink + modeToggle +
    (STATE.edit
      ? '<button class="ex-btn primary" onclick="saveEdits(true)">Save & verify</button>' +
        '<button class="ex-btn secondary" onclick="saveEdits(false)">Save draft</button>' +
        symbolBtn +
        '<button class="ex-btn secondary" onclick="toggleEdit()">Cancel</button>'
      : '<button class="ex-btn secondary" onclick="toggleEdit()" title="Edit page">✎ Edit</button>' +
        '<button class="ex-btn secondary" onclick="regenerate()" title="Re-generate from scratch">↻</button>') +
    '<button class="ex-clear" onclick="reset()">Clear</button>' +
    '<button class="ex-btn secondary" onclick="goExploreHome()" title="Back to Explore home">⌂ Home</button>';

  var editClass = STATE.edit ? ' ex-edit-mode' : '';
  var editable = STATE.edit ? 'contenteditable="true"' : '';
  var anchorHint = (STATE.edit && currentMode === "image")
    ? '<div class="ex-anchor-hint">Drag the green dots on the image to reposition each callout\'s anchor.</div>'
    : '';

  var topbar = STATE.embed ? '' :
    '<div class="ex-topbar">' +
      '<div class="ex-dots">' +
        '<button class="ex-dot ex-dot-close" onclick="goExploreHome()" title="Back to Explore home"></button>' +
        '<button class="ex-dot ex-dot-min" onclick="reset()" title="Clear this study"></button>' +
        '<span class="ex-dot ex-dot-zoom" title="Explore"></span>' +
      '</div>' +
      '<div class="ex-breadcrumb">' + crumbs + '</div>' +
      '<div class="ex-actions">' + actionBtns + '</div>' +
    '</div>';

  root.innerHTML =
    '<div class="ex-shell' + (STATE.embed ? ' ex-embed' : '') + '">' +
      '<div class="ex-frame' + editClass + '">' +
        topbar +
        '<div class="ex-stage">' +
          '<h1 data-field="title" ' + editable + '>' + esc(p.title || "") + '</h1>' +
          '<div class="subtitle" data-field="subtitle" ' + editable + '>' +
            esc(p.subtitle || "") + '</div>' +
          anchorHint +
          '<div class="ex-grid" id="ex-grid">' +
            '<div class="ex-col ex-col-left">' + renderCol(leftItems, 'left') + '</div>' +
            '<div class="ex-canvas-wrap">' +
              '<div class="diagram-host">' +
                (p.mode === "image"
                  ? (p.image_url
                      ? '<img class="diagram diagram-img" src="' +
                          escAttr(p.image_url) + '" alt="' + escAttr(p.title || "") + '" />'
                      : '<div class="ex-image-error">' +
                          '<strong>Image generation failed.</strong><br>' +
                          esc(p.image_error || "Unknown error.") +
                          (p.cloud_available
                            ? '<div style="margin-top:14px">' +
                                '<button class="ex-btn primary" ' +
                                  'onclick="useCloud()" ' +
                                  'title="Render via OpenAI gpt-image-1 (~$0.04 per image)">' +
                                  'Use cloud · OpenAI ($0.04)</button>' +
                              '</div>'
                            : '<div style="margin-top:10px;opacity:0.7;font-size:12px">' +
                                'No cloud fallback configured. Set OPENAI_API_KEY ' +
                                'to enable on-demand cloud rendering.</div>') +
                        '</div>')
                  : (p.svg || "")) +
              '</div>' +
              (STATE.loading ? '<div class="ex-loading">Generating…</div>' : '') +
            '</div>' +
            '<div class="ex-col ex-col-right">' + renderCol(rightItems, 'right') + '</div>' +
            '<svg class="ex-leaders" id="ex-leaders"></svg>' +
          '</div>' +
        '</div>' +
        '<div class="ex-caption" data-field="caption" ' + editable + '>' +
          esc(p.caption || "") + '</div>' +
      '</div>' +
    '</div>';

  // Style the inline diagram SVG to fill its container
  var diagSvg = document.querySelector(".diagram-host > svg");
  if (diagSvg) {
    diagSvg.classList.add("diagram");
    if (!diagSvg.getAttribute("preserveAspectRatio")) {
      diagSvg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    }
    // Edit mode + SVG mode: enable click-to-select for symbol extraction
    if (STATE.edit && (p.mode || "svg") === "svg") {
      diagSvg.style.cursor = "crosshair";
      diagSvg.addEventListener("click", onSvgElementClick);
    }
  }
  // Selection survives across re-renders: re-resolve by data-selectid
  if (STATE.selectedSvgEl) {
    var sid = STATE.selectedSvgEl.getAttribute &&
      STATE.selectedSvgEl.getAttribute("data-selectid");
    if (sid && diagSvg) {
      var found = diagSvg.querySelector('[data-selectid="' + sid + '"]');
      STATE.selectedSvgEl = found || null;
    }
  }

  if (!STATE.edit) {
    document.querySelectorAll(".ex-mini-btn").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        var idx = parseInt(btn.getAttribute("data-idx"), 10);
        var act = btn.getAttribute("data-act");
        var c = (STATE.page.callouts || [])[idx];
        if (!c) return;
        if (act === "dive") expand(c.label);
        else if (act === "peek") openPeek(c.label, idx, btn);
      });
    });
  } else {
    // In edit mode, capture inline edits
    document.querySelectorAll("[contenteditable]").forEach(function(el) {
      el.addEventListener("input", function() {
        var field = el.getAttribute("data-field");
        var val = el.innerText;
        if (field === "title") STATE.page.title = val;
        else if (field === "subtitle") STATE.page.subtitle = val;
        else if (field === "caption") STATE.page.caption = val;
        else if (field === "callout-label") {
          var i = parseInt(el.getAttribute("data-callout-idx"), 10);
          if (STATE.page.callouts[i]) STATE.page.callouts[i].label = val;
        } else if (field === "callout-body") {
          var i = parseInt(el.getAttribute("data-callout-idx"), 10);
          if (STATE.page.callouts[i]) STATE.page.callouts[i].body = val;
        }
        STATE.dirty = true;
        // Update only the status badge without re-render
        var statusEl = document.querySelector(".ex-status");
        if (statusEl) {
          statusEl.className = "ex-status dirty";
          statusEl.textContent = "unsaved edits";
        }
      });
    });
  }

  drawLeaders();
  // Re-draw once the image has loaded so we have natural dimensions.
  var imgEl = document.querySelector(".diagram-host > img");
  if (imgEl) {
    if (imgEl.complete && imgEl.naturalWidth) {
      drawLeaders();
    } else {
      imgEl.addEventListener("load", drawLeaders, { once: true });
    }
  }
  window.addEventListener("resize", drawLeaders);
}

async function toggleEdit() {
  if (STATE.edit && STATE.dirty) {
    var ok = await EOS_UI.confirm({
      message: "Discard unsaved edits?",
      action: "Discard", danger: true,
    });
    if (!ok) return;
    STATE.dirty = false;
  }
  STATE.edit = !STATE.edit;
  render();
}

async function saveEdits(verify) {
  if (!STATE.page) return;
  try {
    var res = await fetch("/kb/api/flipbook/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({page: STATE.page, verify: !!verify}),
    });
    var data = await res.json();
    if (data.error) { EOS_UI.toast(data.error, false); return; }
    STATE.page.verified = !!verify;
    STATE.page.saved = true;
    STATE.dirty = false;
    STATE.edit = false;
    render();
    EOS_UI.toast(verify ? "Verified" : "Draft saved");
    // Wave 3: after verify, if we're NOT in embed mode and the page has
    // a topic slug, return to /kb/#<slug> so the kb detail view picks up
    // the freshly-saved visual. Skip for drafts (user may iterate) and
    // for embed mode (the host iframe should re-render itself).
    if (verify && !STATE.embed) {
      var topic = STATE.page._topic ||
        (STATE.page.breadcrumb || [STATE.page.title]).slice(-1)[0];
      if (topic) {
        var slugOut = _slugify(String(topic).trim());
        if (slugOut) {
          setTimeout(function() {
            window.location.href = "/kb/#" + encodeURIComponent(slugOut);
          }, 600);
        }
      }
    }
  } catch (e) { EOS_UI.toast("Save failed: " + e.message, false); }
}

function toggleFast() {
  STATE.fast = !STATE.fast;
  render();
}

async function refineAnchors(provider) {
  if (!STATE.page) return;
  provider = provider || "local";
  var topic = STATE.page._topic ||
    (STATE.page.breadcrumb || [STATE.page.title]).slice(-1)[0];

  // Cloud path: show the cost upfront and require explicit OK.
  if (provider === "openai") {
    var cost = (STATE.page._cloudCost || 0.001).toFixed(3);
    var model = STATE.page._cloudModel || "gpt-4o-mini";
    var ok = await EOS_UI.confirm({
      message: "Run vision refine via " + model + "? Estimated cost: ~$" +
        cost + " per image.",
      action: "Spend ~$" + cost, danger: false,
    });
    if (!ok) return;
  }

  STATE.loading = true; render();
  try {
    var res = await fetch("/kb/api/flipbook/refine_anchors", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({topic: topic, provider: provider}),
    });
    var data = await res.json();
    if (data.error) {
      // If local failed and cloud is available, stash the cost info and
      // show the opt-in button on the next render.
      if (provider === "local" && data.cloud_available) {
        STATE.page._refineError = data.error;
        STATE.page._cloudAvailable = true;
        STATE.page._cloudCost = data.cloud_cost_usd || 0.001;
        STATE.page._cloudModel = data.cloud_model || "gpt-4o-mini";
      } else {
        EOS_UI.toast("Refine failed: " + data.error, false);
      }
    } else {
      STATE.page.callouts = data.callouts || STATE.page.callouts;
      STATE.page._refineError = "";
      STATE.page._cloudAvailable = false;
      var costStr = (data.cost_usd || 0) > 0
        ? " (cost: $" + (data.cost_usd).toFixed(3) + ")"
        : " (free, local)";
      EOS_UI.toast("Repositioned " + (data.moved || 0) + " anchor" +
        (data.moved === 1 ? "" : "s") + costStr);
    }
  } catch (e) { EOS_UI.toast("Failed: " + e.message, false); }
  STATE.loading = false; render();
}

async function useCloud() {
  if (!STATE.page) return;
  var ok = await EOS_UI.confirm({
    message: "Render this image via OpenAI gpt-image-1? Approx cost: $0.04 per image.",
    action: "Spend ~$0.04", danger: false,
  });
  if (!ok) return;
  STATE.loading = true; render();
  var topic = STATE.page._topic ||
    (STATE.page.breadcrumb || [STATE.page.title]).slice(-1)[0];
  var parents = (STATE.page.breadcrumb || []).slice(0, -1);
  try {
    var endpoint = parents.length === 0 ? "/kb/api/flipbook/start" : "/kb/api/flipbook/expand";
    var bodyData = parents.length === 0
      ? {topic: topic, mode: "image", provider: "openai", force: true}
      : {label: topic, parents: parents, mode: "image", provider: "openai", force: true};
    var res = await fetch(endpoint, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(bodyData),
    });
    var data = await res.json();
    if (data.error) { EOS_UI.toast(data.error, false); STATE.loading = false; render(); return; }
    STATE.page = data;
  } catch (e) { EOS_UI.toast("Failed: " + e.message, false); }
  STATE.loading = false; render();
}

async function switchMode(mode) {
  if (!STATE.page) return;
  if ((STATE.page.mode || "svg") === mode) return;
  STATE.preferredMode = mode;

  // Fast path: both modes already loaded on the current page → flip locally.
  var hasMode =
    (mode === "svg" && STATE.page.svg && STATE.page.svg.indexOf("(illustration unavailable") === -1) ||
    (mode === "image" && STATE.page.image_url);
  if (hasMode) {
    STATE.page.mode = mode;
    var altCallouts = mode === "image" ? STATE.page.image_callouts : STATE.page.svg_callouts;
    if (altCallouts && altCallouts.length) STATE.page.callouts = altCallouts;
    render();
    return;
  }

  if (STATE.dirty) {
    var ok1 = await EOS_UI.confirm({
      message: "Switching mode generates the missing version. Discard unsaved edits?",
      action: "Discard", danger: true,
    });
    if (!ok1) return;
    STATE.dirty = false;
  }
  STATE.loading = true; render();
  var topic = STATE.page._topic ||
    (STATE.page.breadcrumb || [STATE.page.title]).slice(-1)[0];
  var parents = (STATE.page.breadcrumb || []).slice(0, -1);
  try {
    var endpoint = parents.length === 0 ? "/kb/api/flipbook/start" : "/kb/api/flipbook/expand";
    // No `force` — backend returns cached if both modes are stored, else
    // generates only the missing mode and merges it in.
    var bodyData = parents.length === 0
      ? {topic: topic, mode: mode, fast: STATE.fast}
      : {label: topic, parents: parents, mode: mode, fast: STATE.fast};
    var res = await fetch(endpoint, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(bodyData),
    });
    var data = await res.json();
    if (data.error) { EOS_UI.toast(data.error, false); STATE.loading = false; render(); return; }
    STATE.page = data;
  } catch (e) { EOS_UI.toast("Failed: " + e.message, false); }
  STATE.loading = false; render();
}

async function regenerate() {
  if (!STATE.page) return;
  var ok = await EOS_UI.confirm({
    message: "Regenerate this page from scratch? Current content will be replaced.",
    action: "Regenerate", danger: true,
  });
  if (!ok) return;
  STATE.loading = true; render();
  var topic = STATE.page._topic ||
    (STATE.page.breadcrumb || [STATE.page.title]).slice(-1)[0];
  var parents = (STATE.page.breadcrumb || []).slice(0, -1);
  var mode = STATE.page.mode || "svg";
  try {
    var endpoint = parents.length === 0 ? "/kb/api/flipbook/start" : "/kb/api/flipbook/expand";
    var bodyData = parents.length === 0
      ? {topic: topic, mode: mode, force: true, fast: STATE.fast}
      : {label: topic, parents: parents, mode: mode, force: true, fast: STATE.fast};
    var res = await fetch(endpoint, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(bodyData),
    });
    var data = await res.json();
    if (data.error) { EOS_UI.toast(data.error, false); STATE.loading = false; render(); return; }
    STATE.page = data;
    STATE.dirty = false;
  } catch (e) { EOS_UI.toast("Failed: " + e.message, false); }
  STATE.loading = false; render();
}

// Thin delegator over the shared EOS_FLIP overlay (emptyos/web/static/eos-flipbook.js).
// kb owns STATE/edit/drag; the geometry lives in the shared module so condition-map
// (and future consumers) render the same leader lines.
function drawLeaders() {
  var grid = document.getElementById("ex-grid");
  var leaders = document.getElementById("ex-leaders");
  if (!grid || !leaders) return;
  EOS_FLIP.drawLeaders({
    grid: grid,
    leaders: leaders,
    callouts: (STATE.page && STATE.page.callouts) || [],
    mode: (STATE.page && STATE.page.mode) || "svg",
    selectedEl: STATE.selectedSvgEl,
    draggable: STATE.edit,
    onAnchorPointerDown: anchorDragStart,
  });
}

// ── Anchor drag in edit + image mode ──
var _drag = null;

function anchorDragStart(e) {
  e.preventDefault();
  e.stopPropagation();
  var idx = parseInt(e.currentTarget.getAttribute("data-idx"), 10);
  var diagImg = document.querySelector(".diagram-host > img");
  var rect = EOS_FLIP.imageContentRect(diagImg);
  if (!rect) return;
  _drag = { idx: idx, rect: rect };
  e.currentTarget.setPointerCapture(e.pointerId);
  document.addEventListener("pointermove", anchorDragMove);
  document.addEventListener("pointerup", anchorDragEnd, { once: true });
}

function anchorDragMove(e) {
  if (!_drag || !STATE.page || !STATE.page.callouts) return;
  var c = STATE.page.callouts[_drag.idx];
  if (!c) return;
  var px = ((e.clientX - _drag.rect.left) / _drag.rect.width) * 100;
  var py = ((e.clientY - _drag.rect.top) / _drag.rect.height) * 100;
  c.x = Math.max(0, Math.min(100, Math.round(px * 10) / 10));
  c.y = Math.max(0, Math.min(100, Math.round(py * 10) / 10));
  STATE.dirty = true;
  // Update only the leader overlay — full re-render would steal focus from edit fields
  drawLeaders();
  var statusEl = document.querySelector(".ex-status");
  if (statusEl) {
    statusEl.className = "ex-status dirty";
    statusEl.textContent = "unsaved edits";
  }
}

function anchorDragEnd() {
  document.removeEventListener("pointermove", anchorDragMove);
  _drag = null;
}

function _slugify(topic) {
  return (topic || "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function _hierarchicalSlug(page, fallbackTopic) {
  // Build `parent/child` slug from page breadcrumb so sub-topics get a
  // bookmarkable URL distinct from a top-level study with the same name.
  var topic = (page && (page._topic || (page.breadcrumb || []).slice(-1)[0])) || fallbackTopic || "";
  var parents = (page && page.breadcrumb) ? page.breadcrumb.slice(0, -1) : [];
  if (!parents.length) return _slugify(topic);
  var leaf = parents[parents.length - 1];
  return _slugify(leaf) + "/" + _slugify(topic);
}

// EOS_UI.hashRoute drives URL <-> page state. onShow fires for any
// non-empty hash (including page-load and browser back/forward via
// popstate); onHide fires when the hash clears (Home / explicit clear).
// Lazy init: this inline script parses BEFORE the deferred eos-components.js
// loads, so EOS_UI isn't defined yet at parse time. Initialise on first use.
var _route = null;
function _ensureRoute() {
  if (_route) return _route;
  if (typeof EOS_UI === "undefined" || !EOS_UI.hashRoute) return null;
  _route = EOS_UI.hashRoute({
    onShow: function (slug) {
      var current = STATE.page ? _hierarchicalSlug(STATE.page) : "";
      if (slug !== current) loadPageBySlug(slug);
    },
    onHide: function () { reset(); },
  });
  return _route;
}

function _setHash(slug) {
  var r = _ensureRoute();
  if (!r) return;
  if (slug) r.set(slug);
  else r.clear();
}

function goExploreHome() {
  var r = _ensureRoute();
  if (r) r.clear();
}

async function startExploration(topic, opts) {
  // opts.callout_slugs (optional) — list of kb slugs that MUST become the
  // callouts. Used by the neighborhood-driven generation flow from kb's
  // detail view (?slug=<>&generate=1&callouts=a,b,c).
  // opts.slug (optional) — kb note slug to write the result back to. When
  // present, the save lands on THAT note's path even if its title slugifies
  // to something different (e.g. slug=snr-social-perception with title
  // "Signal-to-Noise Theory of Social Perception").
  opts = opts || {};
  STATE.loading = true; STATE.parents = []; render();
  try {
    var payload = {topic: topic, fast: STATE.fast};
    if (opts.callout_slugs && opts.callout_slugs.length) {
      payload.callout_slugs = opts.callout_slugs;
    }
    if (opts.slug) {
      payload.slug = opts.slug;
    }
    var res = await fetch("/kb/api/flipbook/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    var data = await res.json();
    if (data.error) { EOS_UI.toast(data.error, false); STATE.loading = false; render(); return; }
    STATE.page = data;
    STATE.parents = (data.breadcrumb || []).slice(0, -1);
    _setHash(_hierarchicalSlug(data, topic));
  } catch (e) { EOS_UI.toast("Failed: " + e.message, false); }
  STATE.loading = false; render();
}

async function loadPageBySlug(slug) {
  if (!slug) return false;
  STATE.loading = true; render();
  try {
    var res = await fetch("/kb/api/flipbook/page/" + encodeURIComponent(slug));
    if (!res.ok) {
      STATE.loading = false;
      // Embed mode: never auto-generate. Show an empty state instead.
      if (STATE.embed) {
        var root = document.getElementById("root");
        root.innerHTML = '<div class="ex-shell ex-embed"><div class="ex-frame">' +
          '<div class="ex-stage"><div class="empty">No saved visualization for <code>' +
          esc(slug) + '</code>.</div></div></div></div>';
        return false;
      }
      await startExploration(slug.replace(/-/g, " "));
      return true;
    }
    var data = await res.json();
    STATE.page = data;
    STATE.parents = (data.breadcrumb || []).slice(0, -1);
  } catch (e) { /* swallow — fall back to home */ }
  STATE.loading = false; render();
  return true;
}

async function expand(label) {
  STATE.loading = true;
  var parents = (STATE.page && STATE.page.breadcrumb) || [];
  var mode = (STATE.page && STATE.page.mode) || STATE.preferredMode || "svg";
  render();
  try {
    var res = await fetch("/kb/api/flipbook/expand", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        label: label, parents: parents, mode: mode, fast: STATE.fast,
      }),
    });
    var data = await res.json();
    if (data.error) { EOS_UI.toast(data.error, false); STATE.loading = false; render(); return; }
    STATE.page = data;
    STATE.parents = (data.breadcrumb || []).slice(0, -1);
    _setHash(_hierarchicalSlug(data, label));
  } catch (e) { EOS_UI.toast("Failed: " + e.message, false); }
  STATE.loading = false; render();
}

function reset() {
  STATE.page = null; STATE.parents = []; STATE.loading = false; render();
}

// Hash-route is wired via EOS_UI.hashRoute above; .init() applies the
// URL hash present at page load (e.g. /kb/pages/flipbook.html#guitar opens 'guitar').
window.addEventListener("DOMContentLoaded", function () { _route.init(); });

async function loadSymbolLibrary() {
  var host = document.getElementById("ex-symbols");
  if (!host) return;
  try {
    var res = await fetch("/kb/api/flipbook/symbols");
    var data = await res.json();
    var items = (data && data.symbols) || [];
    if (!host.isConnected) return;
    if (!items.length) {
      host.innerHTML =
        '<h3>Symbol library</h3>' +
        '<div class="ex-saved-empty">' +
          'Empty. In edit mode on any SVG page, ' +
          'click "Save as symbol" to add one.' +
        '</div>';
      return;
    }
    host.innerHTML =
      '<h3>Symbol library (' + items.length + ')</h3>' +
      '<div class="ex-saved-grid">' +
        items.map(function(s) {
          return '<div class="ex-symbol-item" data-symbol-id="' + escAttr(s.id) +
            '" title="Click to preview, × to delete" style="cursor:pointer">' +
            '<div class="ex-symbol-row">' +
              '<span class="ex-symbol-name">◇ ' + esc(s.name) + '</span>' +
              '<button class="ex-symbol-del" data-id="' + escAttr(s.id) +
                '" title="Delete symbol">×</button>' +
            '</div>' +
            (s.description
              ? '<div class="ex-symbol-desc">' + esc(s.description) + '</div>'
              : '') +
          '</div>';
        }).join("") +
      '</div>';
    host.querySelectorAll(".ex-symbol-del").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        deleteSymbol(btn.getAttribute("data-id"));
      });
    });
    host.querySelectorAll(".ex-symbol-item").forEach(function(card) {
      card.addEventListener("click", function() {
        previewSymbol(card.getAttribute("data-symbol-id"));
      });
    });
  } catch (e) {
    if (host.isConnected) {
      host.innerHTML = '<h3>Symbol library</h3>' +
        '<div class="ex-saved-empty">Could not load.</div>';
    }
  }
}

async function previewSymbol(id) {
  if (!id) return;
  try {
    var res = await fetch("/kb/api/flipbook/symbols/" + encodeURIComponent(id));
    if (!res.ok) { EOS_UI.toast("Could not load symbol.", false); return; }
    var svg = await res.text();
    EOS_UI.modal({
      title: "◇ " + id.replace(/-/g, " "),
      body: '<div style="background:#fff; border-radius:var(--radius); padding:16px; max-width:600px;">' + svg + '</div>',
    });
  } catch (e) {
    EOS_UI.toast("Failed: " + e.message, false);
  }
}

async function deleteSymbol(id) {
  var ok = await EOS_UI.confirm({
    message: "Delete symbol '" + id + "' from library?",
    action: "Delete", danger: true,
  });
  if (!ok) return;
  try {
    await fetch("/kb/api/flipbook/symbols/" + encodeURIComponent(id),
      { method: "DELETE" });
    loadSymbolLibrary();
    EOS_UI.toast("Deleted: " + id);
  } catch (e) { EOS_UI.toast("Failed: " + e.message, false); }
}

function onSvgElementClick(e) {
  e.stopPropagation();
  var diagSvg = e.currentTarget;
  var t = e.target;
  // Walk up to a meaningful ancestor (skip plain root <svg> and <defs>)
  while (t && t !== diagSvg) {
    var tag = t.tagName && t.tagName.toLowerCase();
    if (tag && tag !== "defs" && tag !== "symbol") break;
    t = t.parentElement;
  }
  if (!t || t === diagSvg || t.tagName.toLowerCase() === "svg") {
    STATE.selectedSvgEl = null;
  } else {
    if (!t.getAttribute("data-selectid")) {
      t.setAttribute("data-selectid",
        "sel-" + Math.random().toString(36).slice(2, 9));
    }
    STATE.selectedSvgEl = t;
  }
  // Re-render the topbar (button label changes), keep current SVG markup
  // and just redraw the leader/highlight overlay.
  render();
}

function buildSelectionSvg() {
  var el = STATE.selectedSvgEl;
  if (!el) return "";
  var bbox;
  try {
    bbox = el.getBBox();
  } catch (e) { return ""; }
  if (!bbox || !bbox.width || !bbox.height) return "";
  var clone = el.cloneNode(true);
  // Drop any selection-related attrs from the clone
  clone.removeAttribute("data-selectid");
  return (
    '<svg viewBox="' + bbox.x + ' ' + bbox.y + ' ' +
      bbox.width + ' ' + bbox.height + '" ' +
      'xmlns="http://www.w3.org/2000/svg">' +
    clone.outerHTML +
    '</svg>'
  );
}

async function saveAsSymbol() {
  if (!STATE.page) return;
  var topic = STATE.page._topic ||
    (STATE.page.breadcrumb || [STATE.page.title]).slice(-1)[0];
  var hasSelection = !!STATE.selectedSvgEl;
  var selectionSvg = hasSelection ? buildSelectionSvg() : "";
  if (hasSelection && !selectionSvg) {
    EOS_UI.toast("Couldn't extract that element (zero bounding box). " +
      "Try clicking on a different shape or group.", false);
    return;
  }
  var defaultName = hasSelection
    ? ""
    : (STATE.page.title || topic || "")
        .toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "")
        .slice(0, 60);
  EOS_UI.formModal(
    hasSelection ? "Save selection as symbol" : "Save SVG as symbol",
    [
      {key: "name", label: "Symbol id",
       hint: "lowercase + hyphens; referenced as <use href='#id'>",
       default: defaultName, type: "text"},
      {key: "description", label: "Short description (optional)",
       default: hasSelection ? "" : (STATE.page.subtitle || ""),
       type: "text"},
    ],
    async function(vals) {
      var name = (vals.name || "").trim();
      if (!name) { EOS_UI.toast("name required", false); return; }
      try {
        var bodyData = {name: name, description: vals.description || ""};
        if (hasSelection) bodyData.svg = selectionSvg;
        else bodyData.topic = topic;
        var res = await fetch("/kb/api/flipbook/symbols", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(bodyData),
        });
        var data = await res.json();
        if (data.error) { EOS_UI.toast("Save failed: " + data.error, false); return; }
        STATE.selectedSvgEl = null;
        EOS_UI.toast("Saved symbol: " + data.id);
        render();
      } catch (e) { EOS_UI.toast("Failed: " + e.message, false); }
    }
  );
}

async function loadSavedList() {
  var host = document.getElementById("ex-saved");
  if (!host) return;
  try {
    var res = await fetch("/kb/api/flipbook/list");
    var data = await res.json();
    var items = (data && data.items) || [];
    if (!host.isConnected) return;
    if (!items.length) {
      host.innerHTML =
        '<div class="ex-saved-empty">No kb concepts yet.</div>';
      return;
    }
    // Wave 3: the list now returns ALL kb concepts (with + without
    // visuals). Split by has_visual so existing flipbooks stay
    // prominent and unvisualized concepts surface as "click to generate"
    // affordances rather than getting buried.
    var withVisuals = items.filter(function(i) { return i.has_visual; });
    var withoutVisuals = items.filter(function(i) { return !i.has_visual; });
    var sections = [];
    function renderItems(list, opts) {
      opts = opts || {};
      return list.map(function(it) {
        var name = (it.title || it.slug || "").trim();
        var mark = "";
        if (opts.showStatus) {
          mark = it.verified
            ? '<span class="ex-saved-mark verified">verified</span>'
            : '<span class="ex-saved-mark draft">draft</span>';
        } else {
          mark = '<span class="ex-saved-mark draft" title="No visual yet — click to generate">+ visual</span>';
        }
        var modeIcon = "";
        if (it.has_visual) modeIcon = it.mode === "image" ? " 🖼" : " ◇";
        // Slug is the canonical lookup key post-Wave-3; topic is the
        // human-typed fallback for legacy notes that didn't carry a
        // distinct slug at save time.
        return '<button class="ex-saved-item" data-slug="' + escAttr(it.slug) +
          '" data-topic="' + escAttr(it.topic || it.title || it.slug) + '">' +
          '<span class="ex-saved-name">' + esc(name) + modeIcon + '</span>' +
          mark + '</button>';
      }).join("");
    }
    if (withVisuals.length) {
      sections.push(
        '<h3>With visuals (' + withVisuals.length + ')</h3>' +
        '<div class="ex-saved-grid">' +
          renderItems(withVisuals, { showStatus: true }) +
        '</div>'
      );
    }
    if (withoutVisuals.length) {
      sections.push(
        '<h3>Concepts without visuals (' + withoutVisuals.length + ') — click to generate</h3>' +
        '<div class="ex-saved-grid">' +
          renderItems(withoutVisuals, { showStatus: false }) +
        '</div>'
      );
    }
    host.innerHTML = sections.join("");
    host.querySelectorAll(".ex-saved-item").forEach(function(btn) {
      btn.addEventListener("click", function() {
        var slug = btn.getAttribute("data-slug");
        var topic = btn.getAttribute("data-topic");
        // Prefer slug-keyed load (Wave-3 unified path) — falls back to
        // topic-string for legacy entries that lack a stable slug.
        if (slug) loadBySlug(slug);
        else if (topic) startExploration(topic);
      });
    });
  } catch (e) {
    if (host.isConnected) {
      host.innerHTML =
        '<div class="ex-saved-empty">Could not load saved list.</div>';
    }
  }
}

async function loadBySlug(slug) {
  // Wave 3: slug-keyed load that handles both existing flipbooks AND
  // unvisualized kb concepts. The server returns either a full flipbook
  // page (has_visual=true) or a needs_generation shape with the kb body
  // as context. For unvisualized concepts we drop the user into the
  // start screen pre-filled with the topic.
  try {
    var r = await fetch("/kb/api/flipbook/page/" + encodeURIComponent(slug));
    if (!r.ok) {
      EOS_UI.toast("Couldn't load: " + slug, false);
      return;
    }
    var page = await r.json();
    if (page.needs_generation) {
      // Prefill the topic input and let the user kick off generation.
      // The generator writes back to the same slug because _save_page
      // resolves storage path from topic → slug (now matches the kb
      // note's existing path under notes_dir).
      var input = document.getElementById("ex-topic");
      if (input) {
        input.value = page.topic || page.title || slug.replace(/-/g, " ");
        input.focus();
      }
      EOS_UI.toast(
        "Ready to generate a visual for '" + (page.title || slug) +
          "'. Press Enter to start.",
        true
      );
      return;
    }
    // Existing flipbook — route through normal exploration flow
    startExploration(page.topic || page.title || slug);
  } catch (e) {
    EOS_UI.toast("Failed: " + e.message, false);
  }
}

// closePeek is kept as a global so legacy callers / inline handlers resolve;
// it delegates to the shared overlay popover.
function closePeek() { EOS_FLIP.peek.close(); }

// ── Inline detail popover ("peek") ──
// Lifecycle (create/position/outside-click/Esc) lives in EOS_FLIP.peek; kb owns
// the in-memory cache + the /kb/api/flipbook/detail fetch + the dive callback.
async function openPeek(label, idx, anchorEl) {
  function doExpand(lbl) { expand(lbl); }

  // Hit in-memory cache first — no network, no flash of "Loading…"
  var cached = (STATE.page && STATE.page.callouts &&
    STATE.page.callouts[idx] && STATE.page.callouts[idx].peek) || null;
  if (cached && cached.summary) {
    EOS_FLIP.peek.show({
      anchorEl: anchorEl, label: label,
      summary: cached.summary, facts: cached.facts, onExpand: doExpand,
    });
    return;
  }
  EOS_FLIP.peek.show({ anchorEl: anchorEl, label: label, loading: true, onExpand: doExpand });

  try {
    var pageTitle = (STATE.page && STATE.page.title) || "";
    var pageTopic = (STATE.page && (STATE.page._topic ||
      (STATE.page.breadcrumb || []).slice(-1)[0])) || "";
    var res = await fetch("/kb/api/flipbook/detail", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        label: label,
        idx: idx,
        page_topic: pageTopic,
        page_title: pageTitle,
      }),
    });
    var data = await res.json();
    if (data && STATE.page && STATE.page.callouts && STATE.page.callouts[idx]) {
      STATE.page.callouts[idx].peek = {
        summary: data.summary || "",
        facts: data.facts || [],
      };
    }
    EOS_FLIP.peek.update({ label: label, summary: data.summary, facts: data.facts, onExpand: doExpand });
  } catch (e) {
    var pop = document.getElementById("ex-popover");
    if (pop) {
      var loading = pop.querySelector(".ex-popover-loading");
      if (loading) loading.textContent = "Failed: " + e.message;
    }
  }
}

// Deeplink + initial render — wait for the deferred EOS_UI scripts to load
// (defer scripts complete before DOMContentLoaded). Without the wait, the
// hashRoute boot path tries to use EOS_UI before it's defined.
function _bootExplore() {
  var params = new URLSearchParams(window.location.search);
  var topic = params.get("topic");
  var slug = params.get("slug");        // Wave-3 canonical entry point
  var generate = params.get("generate") === "1";
  var calloutsRaw = params.get("callouts") || "";
  var calloutSlugs = calloutsRaw
    ? calloutsRaw.split(",").map(function(s){ return s.trim(); }).filter(Boolean)
    : [];
  var hash = (window.location.hash || "").replace(/^#/, "");
  STATE.embed = params.get("embed") === "1";
  if (STATE.embed) document.body.classList.add("ex-embed");
  var r = _ensureRoute();
  // Embed mode: never auto-generate a fresh page (would burn LLM calls
  // inside an iframe the host didn't expect). Always load by slug.
  if (STATE.embed && slug) { loadBySlug(slug); return; }
  if (STATE.embed && topic) {
    loadPageBySlug(_slugify(topic.trim()));
    return;
  }
  // Wave-3 generate-from-neighborhood: ?slug=<>&generate=1&callouts=a,b,c
  // arrives from the kb detail view's "Generate visual" button. Kick off
  // generation immediately using the kb body's title as the topic and the
  // listed slugs as required callouts. The result writes back to the
  // existing kb note's frontmatter (matched by slug via _save_page).
  if (slug && generate) {
    (async function(){
      try {
        var resp = await fetch("/kb/api/flipbook/page/" + encodeURIComponent(slug));
        var note = await resp.json();
        var topicForGen = (note && (note.topic || note.title)) || slug.replace(/-/g, " ");
        startExploration(topicForGen, {callout_slugs: calloutSlugs, slug: slug});
      } catch (e) {
        // Fallback: generate using the slug-as-topic if the lookup fails
        startExploration(slug.replace(/-/g, " "), {callout_slugs: calloutSlugs, slug: slug});
      }
    })();
    return;
  }
  // Wave-3 slug-keyed open — drives the unified detail-or-generate flow.
  // Comes from the kb-detail "Open standalone ↗" header link and the
  // saved-list "load by slug" path.
  if (slug) { loadBySlug(slug); return; }
  // If a hash is present, hashRoute will fire onShow → loadPageBySlug
  if (hash && r && r.init) { r.init(); return; }
  if (topic) startExploration(topic.trim());
  else render();
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _bootExplore);
} else {
  _bootExplore();
}
