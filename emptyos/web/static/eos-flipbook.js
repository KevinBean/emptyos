/* eos-flipbook.js — shared Flipbook overlay (EOS_FLIP).
 *
 * The pure render/geometry core behind annotated-SVG explainer pages: draw
 * leader lines from callout cards to data-anchor="N" SVG elements, and the
 * "peek" detail popover. Extracted from kb/pages/flipbook.js once a second
 * consumer (condition-map's Illustrated view) appeared — per CLAUDE.md rule 9.
 *
 * Consumers own DATA + TRANSPORT (STATE, fetches, routing); this module owns
 * GEOMETRY + DOM. It reaches into no caller STATE — edit-mode drag is wired via
 * an onAnchorPointerDown callback, and peek "open full page" via onExpand.
 *
 * Markup contract (shared via eos-flipbook.css, .ex- namespace):
 *   grid (.ex-grid)
 *     .ex-canvas-wrap  →  .diagram-host > svg|img
 *     .ex-callout[data-idx][data-side=left|right]   (callout cards)
 *   leaders: an <svg> overlay sized to the grid.
 */
(function (global) {
  "use strict";

  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── Callout column split ───────────────────────────────────────────
  // Split callouts into left/right columns by anchor x (≤8 shown), rebalance an
  // empty column, and sort each top-to-bottom by anchor y so leaders don't
  // cross. Returns {left, right} of {c, idx} entries (idx = original index).
  // The consumer renders the card HTML for each side (kb has edit/dive cards,
  // condition-map has peek-only) — only this geometry is shared.
  function splitCallouts(callouts) {
    var left = [], right = [];
    (callouts || []).slice(0, 8).forEach(function (c, i) {
      var ax = (c.x == null) ? 50 : c.x;
      (ax < 50 ? left : right).push({ c: c, idx: i });
    });
    if (!left.length && right.length > 1) left = right.splice(0, Math.floor(right.length / 2));
    else if (!right.length && left.length > 1) right = left.splice(Math.floor(left.length / 2));
    function byY(a, b) { return ((a.c.y == null) ? 50 : a.c.y) - ((b.c.y == null) ? 50 : b.c.y); }
    left.sort(byY); right.sort(byY);
    return { left: left, right: right };
  }

  // ── Leader lines ───────────────────────────────────────────────────
  // Draw a curved leader from each .ex-callout to its anchor — a tagged
  // data-anchor="N" SVG element in SVG mode, else the (x,y) percentage.
  // opts: { grid, leaders, callouts, mode, selectedEl, draggable,
  //         onAnchorPointerDown }
  function drawLeaders(opts) {
    var grid = opts.grid;
    var leaders = opts.leaders;
    if (!grid || !leaders) return;
    var callouts = opts.callouts || [];
    var canvasWrap = grid.querySelector(".ex-canvas-wrap");
    if (!canvasWrap) return;
    var gridRect = grid.getBoundingClientRect();
    var canvasRect = canvasWrap.getBoundingClientRect();
    leaders.innerHTML = "";
    leaders.setAttribute("viewBox", "0 0 " + gridRect.width + " " + gridRect.height);
    leaders.setAttribute("width", gridRect.width);
    leaders.setAttribute("height", gridRect.height);

    var diagSvg = grid.querySelector(".diagram-host > svg");
    var diagImg = grid.querySelector(".diagram-host > img");
    var isImageMode = opts.mode === "image";

    // Image mode: compute the *visible* image rect after object-fit:contain.
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

    // Highlight the selected SVG element (edit mode + SVG mode).
    if (opts.selectedEl && diagSvg) {
      try {
        var sRect = opts.selectedEl.getBoundingClientRect();
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

    grid.querySelectorAll(".ex-callout").forEach(function (el) {
      var idx = parseInt(el.getAttribute("data-idx"), 10);
      var side = el.getAttribute("data-side");
      var c = callouts[idx];
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
      // Callout edge nearest the diagram, vertical clamped to anchor height.
      var calloutX = (side === 'left')
        ? elRect.right - gridRect.left
        : elRect.left - gridRect.left;
      var boxTop = elRect.top - gridRect.top;
      var boxBottom = elRect.bottom - gridRect.top;
      var pad = 8;
      var calloutY = Math.min(Math.max(anchorY, boxTop + pad), boxBottom - pad);
      var ctrlX = calloutX + (anchorX - calloutX) * 0.55;
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
      // Anchor dot — draggable in edit mode for image pages.
      var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", anchorX);
      dot.setAttribute("cy", anchorY);
      var draggable = opts.draggable && isImageMode && imageRect;
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

    // Wire drag listeners once the dots exist (idempotent).
    if (opts.draggable && isImageMode && imageRect && opts.onAnchorPointerDown) {
      leaders.querySelectorAll("circle.anchor-draggable").forEach(function (dot) {
        dot.addEventListener("pointerdown", opts.onAnchorPointerDown);
      });
    }
  }

  // Map the visible image rect for an <img> after object-fit:contain — the
  // coordinate frame anchor-drag write-back needs. Consumers reuse this so the
  // drag math matches drawLeaders exactly.
  function imageContentRect(diagImg) {
    if (!diagImg || !diagImg.naturalWidth) return null;
    var box = diagImg.getBoundingClientRect();
    var scale = Math.min(box.width / diagImg.naturalWidth,
                         box.height / diagImg.naturalHeight);
    var dw = diagImg.naturalWidth * scale;
    var dh = diagImg.naturalHeight * scale;
    return {
      left: box.left + (box.width - dw) / 2,
      top: box.top + (box.height - dh) / 2,
      width: dw, height: dh,
    };
  }

  // ── Peek popover ───────────────────────────────────────────────────
  var POP_ID = "ex-popover";

  function _peekBodyHtml(label, summary, facts, opts) {
    opts = opts || {};
    var factsHtml = (facts || []).map(function (f) {
      return '<li>' + _esc(f) + '</li>';
    }).join("");
    var foot = opts.onExpand
      ? '<div class="ex-popover-foot">' +
          '<button class="ex-mini-btn" data-flip-act="expand">→ Open full page</button>' +
        '</div>'
      : '';
    return (
      '<div class="ex-popover-head">' +
        '<div class="ex-popover-title">' + _esc(label) + '</div>' +
        '<button class="ex-popover-close" data-flip-act="close" ' +
          'aria-label="Close">×</button>' +
      '</div>' +
      (summary
        ? '<div class="ex-popover-summary">' + _esc(summary) + '</div>'
        : '') +
      (factsHtml ? '<ul class="ex-popover-facts">' + factsHtml + '</ul>' : '') +
      foot
    );
  }

  function _loadingHtml(label) {
    return (
      '<div class="ex-popover-head">' +
        '<div class="ex-popover-title">' + _esc(label) + '</div>' +
        '<button class="ex-popover-close" data-flip-act="close" ' +
          'aria-label="Close">×</button>' +
      '</div>' +
      '<div class="ex-popover-loading">Loading…</div>'
    );
  }

  function _wirePop(pop, label, onExpand) {
    pop.querySelectorAll('[data-flip-act="close"]').forEach(function (b) {
      b.addEventListener("click", closePeek);
    });
    if (onExpand) {
      pop.querySelectorAll('[data-flip-act="expand"]').forEach(function (b) {
        b.addEventListener("click", function () { closePeek(); onExpand(label); });
      });
    }
  }

  function positionPeek(pop, anchorEl) {
    var rect = anchorEl.getBoundingClientRect();
    var popW = 320, popH = 220;
    var pad = 10;
    var vw = window.innerWidth, vh = window.innerHeight;
    var left = rect.right + pad;
    if (left + popW > vw - pad) left = rect.left - popW - pad;
    if (left < pad) left = Math.max(pad, rect.left);
    var top = rect.top;
    if (top + popH > vh - pad) top = vh - popH - pad;
    if (top < pad) top = pad;
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }

  function _peekOutsideClick(e) {
    var pop = document.getElementById(POP_ID);
    if (!pop) return;
    if (!pop.contains(e.target) &&
        !(e.target.closest && e.target.closest('[data-act="peek"]'))) {
      closePeek();
    }
  }

  function _peekKeydown(e) {
    if (e.key === "Escape") closePeek();
  }

  function closePeek() {
    var pop = document.getElementById(POP_ID);
    if (pop) pop.remove();
    document.removeEventListener("click", _peekOutsideClick, true);
    document.removeEventListener("keydown", _peekKeydown, true);
  }

  // Open (or replace) the peek popover near anchorEl. Pass loading:true for the
  // pre-fetch state, then call updatePeek() once content arrives. onExpand(label)
  // (optional) renders the "Open full page" footer.
  function showPeek(o) {
    closePeek();
    var pop = document.createElement("div");
    pop.className = "ex-popover";
    pop.id = POP_ID;
    pop._flipLabel = o.label;
    pop._flipExpand = o.onExpand || null;
    pop.innerHTML = o.loading
      ? _loadingHtml(o.label)
      : _peekBodyHtml(o.label, o.summary, o.facts, { onExpand: o.onExpand });
    document.body.appendChild(pop);
    _wirePop(pop, o.label, o.onExpand);
    positionPeek(pop, o.anchorEl);
    setTimeout(function () {
      document.addEventListener("click", _peekOutsideClick, true);
      document.addEventListener("keydown", _peekKeydown, true);
    }, 0);
    return pop;
  }

  // Replace the open popover's content (after an async fetch). No-op if closed.
  function updatePeek(o) {
    var pop = document.getElementById(POP_ID);
    if (!pop) return;
    var label = o.label != null ? o.label : pop._flipLabel;
    var onExpand = o.onExpand !== undefined ? o.onExpand : pop._flipExpand;
    pop.innerHTML = _peekBodyHtml(label, o.summary, o.facts, { onExpand: onExpand });
    _wirePop(pop, label, onExpand);
  }

  global.EOS_FLIP = {
    splitCallouts: splitCallouts,
    drawLeaders: drawLeaders,
    imageContentRect: imageContentRect,
    peek: {
      show: showPeek,
      update: updatePeek,
      close: closePeek,
      position: positionPeek,
      bodyHtml: _peekBodyHtml,
    },
  };
})(window);
