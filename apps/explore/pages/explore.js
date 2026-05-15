var STATE = {
  page: null, parents: [], loading: false,
  edit: false, dirty: false,
  preferredMode: "svg",  // user's last chosen mode
  fast: false,           // when on, image renders use low-step path (~2-4s)
  selectedSvgEl: null,   // selected SVG element for symbol extraction
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
          '<div class="hint">Press Enter to begin. · <a href="/explore/pages/graph.html" class="ex-graph-link">View vault graph →</a></div>' +
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
    return;
  }

  var p = STATE.page || {};
  var crumbs = (p.breadcrumb || []).map(function(c, i, arr) {
    var cls = i === arr.length - 1 ? "crumb-active" : "";
    return '<span class="' + cls + '">' + esc(c) + '</span>';
  }).join('<span class="crumb-sep">/</span>');

  // Split callouts into left/right columns by anchor X
  var callouts = (p.callouts || []).slice(0, 8);
  var leftItems = [], rightItems = [];
  callouts.forEach(function(c, i) {
    var ax = (c.x == null) ? 50 : c.x;
    if (ax < 50) leftItems.push({c: c, idx: i});
    else rightItems.push({c: c, idx: i});
  });
  // Rebalance if a column ends up empty
  if (leftItems.length === 0 && rightItems.length > 1) {
    leftItems = rightItems.splice(0, Math.floor(rightItems.length / 2));
  } else if (rightItems.length === 0 && leftItems.length > 1) {
    rightItems = leftItems.splice(Math.floor(leftItems.length / 2));
  }
  // Sort each column top-to-bottom by anchor Y so leader lines don't cross
  function byAnchorY(a, b) {
    var ay = (a.c.y == null) ? 50 : a.c.y;
    var by = (b.c.y == null) ? 50 : b.c.y;
    return ay - by;
  }
  leftItems.sort(byAnchorY);
  rightItems.sort(byAnchorY);

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
              esc(p._cloudModel || "gpt-4o-mini") + ' (~$' +
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

  root.innerHTML =
    '<div class="ex-shell">' +
      '<div class="ex-frame' + editClass + '">' +
        '<div class="ex-topbar">' +
          '<div class="ex-dots">' +
            '<button class="ex-dot ex-dot-close" onclick="goExploreHome()" title="Back to Explore home"></button>' +
            '<button class="ex-dot ex-dot-min" onclick="reset()" title="Clear this study"></button>' +
            '<span class="ex-dot ex-dot-zoom" title="Explore"></span>' +
          '</div>' +
          '<div class="ex-breadcrumb">' + crumbs + '</div>' +
          '<div class="ex-actions">' + actionBtns + '</div>' +
        '</div>' +
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
                          esc(p.image_url) + '" alt="' + esc(p.title || "") + '" />'
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
    var res = await fetch("/explore/api/save", {
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
    var res = await fetch("/explore/api/refine_anchors", {
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
    var endpoint = parents.length === 0 ? "/explore/api/start" : "/explore/api/expand";
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
    var endpoint = parents.length === 0 ? "/explore/api/start" : "/explore/api/expand";
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
    var endpoint = parents.length === 0 ? "/explore/api/start" : "/explore/api/expand";
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

function drawLeaders() {
  var grid = document.getElementById("ex-grid");
  var leaders = document.getElementById("ex-leaders");
  if (!grid || !leaders) return;
  var canvasWrap = grid.querySelector(".ex-canvas-wrap");
  if (!canvasWrap) return;
  var gridRect = grid.getBoundingClientRect();
  var canvasRect = canvasWrap.getBoundingClientRect();
  leaders.innerHTML = "";
  leaders.setAttribute("viewBox", "0 0 " + gridRect.width + " " + gridRect.height);
  leaders.setAttribute("width", gridRect.width);
  leaders.setAttribute("height", gridRect.height);

  // SVG mode can use semantic data-anchor lookups; image mode falls back to (x,y)
  var diagSvg = document.querySelector(".diagram-host > svg");
  var diagImg = document.querySelector(".diagram-host > img");
  var isImageMode = STATE.page && STATE.page.mode === "image";

  // For image mode: compute the actual *visible* image rect after object-fit:contain.
  // The <img> element fills the 8:5 canvas, but the picture inside is letterboxed.
  // Anchor coords (0-100%) are relative to the picture, not the canvas.
  var imageRect = null;
  if (isImageMode && diagImg && diagImg.naturalWidth && diagImg.naturalHeight) {
    var box = diagImg.getBoundingClientRect();
    var scale = Math.min(box.width / diagImg.naturalWidth,
                         box.height / diagImg.naturalHeight);
    var dw = diagImg.naturalWidth * scale;
    var dh = diagImg.naturalHeight * scale;
    imageRect = {
      left: box.left + (box.width - dw) / 2,
      top: box.top + (box.height - dh) / 2,
      width: dw, height: dh,
    };
  }

  // Highlight the selected SVG element (edit mode + SVG mode)
  if (STATE.selectedSvgEl && diagSvg) {
    try {
      var sRect = STATE.selectedSvgEl.getBoundingClientRect();
      var hRect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      hRect.setAttribute("x", sRect.left - gridRect.left - 2);
      hRect.setAttribute("y", sRect.top - gridRect.top - 2);
      hRect.setAttribute("width", sRect.width + 4);
      hRect.setAttribute("height", sRect.height + 4);
      hRect.setAttribute("fill", "none");
      hRect.setAttribute("stroke", "#2f6f3f");
      hRect.setAttribute("stroke-width", "2");
      hRect.setAttribute("stroke-dasharray", "5 3");
      hRect.setAttribute("rx", "3");
      hRect.setAttribute("pointer-events", "none");
      leaders.appendChild(hRect);
    } catch (e) { /* element may have been replaced */ }
  }

  document.querySelectorAll(".ex-callout").forEach(function(el) {
    var idx = parseInt(el.getAttribute("data-idx"), 10);
    var side = el.getAttribute("data-side");
    var c = (STATE.page.callouts || [])[idx];
    if (!c) return;
    var elRect = el.getBoundingClientRect();

    var anchorX, anchorY;
    var tagged = !isImageMode && diagSvg &&
      diagSvg.querySelector('[data-anchor="' + idx + '"]');
    if (tagged) {
      try {
        var tRect = tagged.getBoundingClientRect();
        anchorX = tRect.left + tRect.width / 2 - gridRect.left;
        anchorY = tRect.top + tRect.height / 2 - gridRect.top;
      } catch (e) { tagged = null; }
    }
    if (!tagged) {
      var ax = (c.x == null ? 50 : c.x) / 100;
      var ay = (c.y == null ? 50 : c.y) / 100;
      var anchorTarget = imageRect || canvasRect;
      anchorX = anchorTarget.left + ax * anchorTarget.width - gridRect.left;
      anchorY = anchorTarget.top + ay * anchorTarget.height - gridRect.top;
    }
    // Callout edge nearest the diagram, with vertical clamped to anchor height
    // so lines start from the corner closest to the anchor (no long diagonals).
    var calloutX = (side === 'left')
      ? elRect.right - gridRect.left
      : elRect.left - gridRect.left;
    var boxTop = elRect.top - gridRect.top;
    var boxBottom = elRect.bottom - gridRect.top;
    var pad = 8;
    var calloutY = Math.min(Math.max(anchorY, boxTop + pad), boxBottom - pad);
    // Smooth curve from callout edge to anchor (control point pulled
    // horizontally toward the diagram so the line eases in).
    var ctrlX = (side === 'left')
      ? calloutX + (anchorX - calloutX) * 0.55
      : calloutX + (anchorX - calloutX) * 0.55;
    var ctrlY = calloutY;
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute(
      "d",
      "M " + calloutX + " " + calloutY +
      " Q " + ctrlX + " " + ctrlY + " " + anchorX + " " + anchorY
    );
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#6f5d3f");
    path.setAttribute("stroke-width", "1.2");
    path.setAttribute("stroke-dasharray", "3 3");
    path.setAttribute("opacity", "0.7");
    leaders.appendChild(path);
    // Small dot at the anchor — draggable in edit mode for image pages
    var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", anchorX);
    dot.setAttribute("cy", anchorY);
    var draggable = STATE.edit && isImageMode && imageRect;
    dot.setAttribute("r", draggable ? 8 : 3);
    dot.setAttribute("fill", "#6f5d3f");
    if (draggable) {
      dot.classList.add("anchor-draggable");
      dot.setAttribute("data-idx", idx);
      dot.setAttribute("stroke", "#fff");
      dot.setAttribute("stroke-width", "2");
    }
    leaders.appendChild(dot);
  });

  // Wire drag listeners once the dots exist (idempotent — safe to call each draw)
  if (STATE.edit && isImageMode && imageRect) {
    leaders.querySelectorAll("circle.anchor-draggable").forEach(function(dot) {
      dot.addEventListener("pointerdown", anchorDragStart);
    });
  }
}

// ── Anchor drag in edit + image mode ──
var _drag = null;

function anchorDragStart(e) {
  e.preventDefault();
  e.stopPropagation();
  var idx = parseInt(e.currentTarget.getAttribute("data-idx"), 10);
  var diagImg = document.querySelector(".diagram-host > img");
  if (!diagImg || !diagImg.naturalWidth) return;
  var box = diagImg.getBoundingClientRect();
  var scale = Math.min(box.width / diagImg.naturalWidth,
                       box.height / diagImg.naturalHeight);
  var dw = diagImg.naturalWidth * scale;
  var dh = diagImg.naturalHeight * scale;
  _drag = {
    idx: idx,
    rect: {
      left: box.left + (box.width - dw) / 2,
      top: box.top + (box.height - dh) / 2,
      width: dw, height: dh,
    },
  };
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

async function startExploration(topic) {
  STATE.loading = true; STATE.parents = []; render();
  try {
    var res = await fetch("/explore/api/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({topic: topic, fast: STATE.fast}),
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
    var res = await fetch("/explore/api/page/" + encodeURIComponent(slug));
    if (!res.ok) {
      // No saved page — fall through to a fresh start with the slug as topic.
      STATE.loading = false;
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
    var res = await fetch("/explore/api/expand", {
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
// URL hash present at page load (e.g. /explore/#guitar -> open guitar).
window.addEventListener("DOMContentLoaded", function () { _route.init(); });

async function loadSymbolLibrary() {
  var host = document.getElementById("ex-symbols");
  if (!host) return;
  try {
    var res = await fetch("/explore/api/symbols");
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
          return '<div class="ex-symbol-item" data-symbol-id="' + esc(s.id) +
            '" title="Click to preview, × to delete" style="cursor:pointer">' +
            '<div class="ex-symbol-row">' +
              '<span class="ex-symbol-name">◇ ' + esc(s.name) + '</span>' +
              '<button class="ex-symbol-del" data-id="' + esc(s.id) +
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
    var res = await fetch("/explore/api/symbols/" + encodeURIComponent(id));
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
    await fetch("/explore/api/symbols/" + encodeURIComponent(id),
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
        var res = await fetch("/explore/api/symbols", {
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
    var res = await fetch("/explore/api/list");
    var data = await res.json();
    var items = (data && data.items) || [];
    if (!host.isConnected) return;
    if (!items.length) {
      host.innerHTML =
        '<div class="ex-saved-empty">No saved explorations yet.</div>';
      return;
    }
    items.sort(function(a, b) {
      return (b.updated || "").localeCompare(a.updated || "");
    });
    var verified = items.filter(function(i) { return i.verified; });
    var drafts = items.filter(function(i) { return !i.verified; });
    var sections = [];
    function renderItems(list) {
      return list.map(function(it) {
        var name = (it.title || it.slug || "").trim();
        var mark = it.verified
          ? '<span class="ex-saved-mark verified">verified</span>'
          : '<span class="ex-saved-mark draft">draft</span>';
        var modeIcon = it.mode === "image" ? " 🖼" : " ◇";
        // Prefer the saved `topic` (cache key) over the title for reload.
        var topicKey = it.topic || it.title || it.slug;
        return '<button class="ex-saved-item" data-topic="' +
          esc(topicKey) + '">' +
          '<span class="ex-saved-name">' + esc(name) + modeIcon + '</span>' +
          mark + '</button>';
      }).join("");
    }
    if (verified.length) {
      sections.push(
        '<h3>Verified (' + verified.length + ')</h3>' +
        '<div class="ex-saved-grid">' + renderItems(verified) + '</div>'
      );
    }
    if (drafts.length) {
      sections.push(
        '<h3>Drafts (' + drafts.length + ')</h3>' +
        '<div class="ex-saved-grid">' + renderItems(drafts) + '</div>'
      );
    }
    host.innerHTML = sections.join("");
    host.querySelectorAll(".ex-saved-item").forEach(function(btn) {
      btn.addEventListener("click", function() {
        var topic = btn.getAttribute("data-topic");
        if (topic) startExploration(topic);
      });
    });
  } catch (e) {
    if (host.isConnected) {
      host.innerHTML =
        '<div class="ex-saved-empty">Could not load saved list.</div>';
    }
  }
}

function renderPeekBody(label, summary, facts) {
  var factsHtml = (facts || []).map(function(f) {
    return '<li>' + esc(f) + '</li>';
  }).join("");
  return (
    '<div class="ex-popover-head">' +
      '<div class="ex-popover-title">' + esc(label) + '</div>' +
      '<button class="ex-popover-close" onclick="closePeek()" ' +
        'aria-label="Close">×</button>' +
    '</div>' +
    (summary
      ? '<div class="ex-popover-summary">' + esc(summary) + '</div>'
      : '') +
    (factsHtml ? '<ul class="ex-popover-facts">' + factsHtml + '</ul>' : '') +
    '<div class="ex-popover-foot">' +
      '<button class="ex-mini-btn" onclick="closePeek();expand(' +
        JSON.stringify(label).replace(/"/g, '&quot;') +
      ')">→ Open full page</button>' +
    '</div>'
  );
}

// ── Inline detail popover ("peek") ──
async function openPeek(label, idx, anchorEl) {
  closePeek();
  var pop = document.createElement("div");
  pop.className = "ex-popover";
  pop.id = "ex-popover";

  // Hit in-memory cache first — no network, no flash of "Loading…"
  var cached = (STATE.page && STATE.page.callouts &&
    STATE.page.callouts[idx] && STATE.page.callouts[idx].peek) || null;
  if (cached && cached.summary) {
    pop.innerHTML = renderPeekBody(label, cached.summary, cached.facts);
  } else {
    pop.innerHTML =
      '<div class="ex-popover-head">' +
        '<div class="ex-popover-title">' + esc(label) + '</div>' +
        '<button class="ex-popover-close" onclick="closePeek()" ' +
          'aria-label="Close">×</button>' +
      '</div>' +
      '<div class="ex-popover-loading">Loading…</div>';
  }
  document.body.appendChild(pop);
  positionPeek(pop, anchorEl);
  // Close on outside click / Escape
  setTimeout(function() {
    document.addEventListener("click", peekOutsideClick, true);
    document.addEventListener("keydown", peekKeydown, true);
  }, 0);

  // If we already had a cache hit, we're done — no network call.
  if (cached && cached.summary) return;

  try {
    var pageTitle = (STATE.page && STATE.page.title) || "";
    var pageTopic = (STATE.page && (STATE.page._topic ||
      (STATE.page.breadcrumb || []).slice(-1)[0])) || "";
    var res = await fetch("/explore/api/detail", {
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
    if (!document.getElementById("ex-popover")) return;
    pop.innerHTML = renderPeekBody(label, data.summary, data.facts);
  } catch (e) {
    if (document.getElementById("ex-popover")) {
      pop.querySelector(".ex-popover-loading").textContent =
        "Failed: " + e.message;
    }
  }
}

function positionPeek(pop, anchorEl) {
  var rect = anchorEl.getBoundingClientRect();
  var popW = 320, popH = 220;
  var pad = 10;
  var vw = window.innerWidth, vh = window.innerHeight;
  // Prefer right of the callout, fall back to left, then below
  var left = rect.right + pad;
  if (left + popW > vw - pad) left = rect.left - popW - pad;
  if (left < pad) left = Math.max(pad, rect.left);
  var top = rect.top;
  if (top + popH > vh - pad) top = vh - popH - pad;
  if (top < pad) top = pad;
  pop.style.left = left + "px";
  pop.style.top = top + "px";
}

function closePeek() {
  var pop = document.getElementById("ex-popover");
  if (pop) pop.remove();
  document.removeEventListener("click", peekOutsideClick, true);
  document.removeEventListener("keydown", peekKeydown, true);
}

function peekOutsideClick(e) {
  var pop = document.getElementById("ex-popover");
  if (!pop) return;
  if (!pop.contains(e.target) &&
      !(e.target.closest && e.target.closest('[data-act="peek"]'))) {
    closePeek();
  }
}

function peekKeydown(e) {
  if (e.key === "Escape") closePeek();
}

// Deeplink + initial render — wait for the deferred EOS_UI scripts to load
// (defer scripts complete before DOMContentLoaded). Without the wait, the
// hashRoute boot path tries to use EOS_UI before it's defined.
function _bootExplore() {
  var params = new URLSearchParams(window.location.search);
  var topic = params.get("topic");
  var hash = (window.location.hash || "").replace(/^#/, "");
  var r = _ensureRoute();
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
