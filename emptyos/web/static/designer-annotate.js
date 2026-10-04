/* designer-annotate.js — self-contained 墨刀-style annotation overlay.
 *
 * Injected into a designer page by ?annotate=1 (apps/public/standard/designer/
 * routes.py). Reads window.__EOS_ANNOTATIONS__ (one item per documented element,
 * each keyed to a data-eos-el anchor) and overlays a numbered pin on each live
 * element; clicking a pin opens a spec popover (logic / validation / exceptions).
 *
 * Deliberately self-contained: its OWN scoped .dz-annot-* styles + popover, no
 * dependency on theme.css or eos-flipbook.css (whose page-owning body{} / :root
 * rules would repaint the generated prototype). Safe to bake onto any page.
 *
 * Reuse note: this is the HTML-element sibling of EOS_FLIP.drawLeaders (which
 * targets SVG inside a .diagram-host) — see .claude/rules/artifact-element-edit.md
 * and project_designer_app. Pins, not leaders, for v1.
 */
(function () {
  "use strict";

  var DATA = (window.__EOS_ANNOTATIONS__ || []).filter(function (a) {
    return a && typeof a.el === "string" && /^e\d+$/.test(a.el) &&
      (a.logic || a.validation || a.exceptions);
  });
  if (!DATA.length) return;

  var visible = true;
  var layer = null, toggleBtn = null, pop = null;
  var pins = [];   // { a, target, btn }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function injectStyle() {
    if (document.getElementById("dz-annot-style")) return;
    var css = [
      ".dz-annot-layer{position:fixed;inset:0;pointer-events:none;z-index:2147483000;}",
      ".dz-annot-pin{position:fixed;pointer-events:auto;width:24px;height:24px;",
        "margin:0;border-radius:50%;border:2px solid #fff;background:#d9480f;color:#fff;",
        "font:600 12px/20px system-ui,-apple-system,sans-serif;text-align:center;",
        "cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.35);transition:transform .1s;}",
      ".dz-annot-pin:hover{transform:scale(1.18);}",
      ".dz-annot-pin.sel{background:#1d4ed8;}",
      ".dz-annot-toggle{position:fixed;left:16px;bottom:16px;pointer-events:auto;",
        "z-index:2147483001;background:#1a1a1a;color:#fff;border:0;border-radius:999px;",
        "padding:8px 14px;font:600 12px system-ui,-apple-system,sans-serif;cursor:pointer;",
        "box-shadow:0 4px 16px rgba(0,0,0,.4);display:flex;gap:7px;align-items:center;}",
      ".dz-annot-toggle .dot{width:9px;height:9px;border-radius:50%;background:#d9480f;}",
      ".dz-annot-toggle.off .dot{background:#777;}",
      ".dz-annot-pop{position:fixed;z-index:2147483002;width:300px;max-width:86vw;",
        "background:#fff;color:#1a1a1a;border:1px solid #e5e5e5;border-radius:12px;",
        "box-shadow:0 12px 48px rgba(0,0,0,.3);overflow:hidden;",
        "font:13px/1.5 system-ui,-apple-system,sans-serif;}",
      ".dz-annot-pop-head{display:flex;align-items:center;gap:8px;padding:11px 14px;",
        "background:#d9480f;color:#fff;}",
      ".dz-annot-pop-num{width:20px;height:20px;border-radius:50%;flex:0 0 auto;",
        "background:rgba(255,255,255,.25);text-align:center;font:600 11px/20px system-ui,sans-serif;}",
      ".dz-annot-pop-title{flex:1;font-weight:600;font-size:13px;}",
      ".dz-annot-pop-x{background:transparent;border:0;color:#fff;font-size:18px;",
        "line-height:1;cursor:pointer;padding:0 2px;}",
      ".dz-annot-pop-body{padding:12px 14px;}",
      ".dz-annot-logic{margin:0;}",
      ".dz-annot-row{display:flex;gap:8px;margin-top:9px;font-size:12px;}",
      ".dz-annot-row .k{flex:0 0 42px;font-weight:600;color:#666;}",
      ".dz-annot-row.warn .k{color:#b54708;}",
    ].join("");
    var s = document.createElement("style");
    s.id = "dz-annot-style";
    s.textContent = css;
    (document.head || document.documentElement).appendChild(s);
  }

  function targetFor(a) {
    return document.querySelector('[data-eos-el="' + a.el + '"]');
  }

  function build() {
    injectStyle();
    layer = document.createElement("div");
    layer.className = "dz-annot-layer";

    DATA.forEach(function (a) {
      var t = targetFor(a);
      if (!t) return;   // orphaned (element removed by a later iterate) — skip
      var btn = document.createElement("button");
      btn.className = "dz-annot-pin";
      btn.type = "button";
      btn.textContent = a.n;
      btn.setAttribute("aria-label", "Annotation " + a.n + ": " + (a.label || a.el));
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        openPop(a, t, btn);
      });
      layer.appendChild(btn);
      pins.push({ a: a, target: t, btn: btn });
    });
    document.body.appendChild(layer);

    toggleBtn = document.createElement("button");
    toggleBtn.className = "dz-annot-toggle";
    toggleBtn.type = "button";
    var n = pins.length, m = DATA.length;
    var lbl = (n === m) ? ("Annotations · " + n)
                        : ("Annotations · " + n + " of " + m);
    toggleBtn.innerHTML = '<span class="dot"></span><span>' + esc(lbl) + "</span>";
    toggleBtn.addEventListener("click", function () { setVisible(!visible); });
    document.body.appendChild(toggleBtn);

    reposition();
  }

  var raf = 0;
  function scheduleReposition() {
    if (raf) return;
    raf = requestAnimationFrame(function () { raf = 0; reposition(); });
  }

  function reposition() {
    pins.forEach(function (p) {
      var r = p.target.getBoundingClientRect();
      // Fixed pins use viewport coords directly; nudge inward off the corner.
      p.btn.style.left = (r.left + 1) + "px";
      p.btn.style.top = (r.top + 1) + "px";
      p.btn.style.display = (r.width === 0 && r.height === 0) ? "none" : "";
    });
    if (pop && pop._btn) positionPop(pop, pop._btn);
  }

  function setVisible(on) {
    visible = on;
    layer.style.display = on ? "" : "none";
    toggleBtn.classList.toggle("off", !on);
    if (!on) closePop();
  }

  function closePop() {
    if (pop) { pop.remove(); pop = null; }
    pins.forEach(function (p) { p.btn.classList.remove("sel"); });
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("click", onOutside, true);
  }

  function onKey(e) { if (e.key === "Escape") closePop(); }
  function onOutside(e) {
    if (!pop) return;
    var onPin = e.target.closest && e.target.closest(".dz-annot-pin");
    if (!pop.contains(e.target) && !onPin) closePop();
  }

  function row(kind, label, val) {
    if (!val) return "";
    return '<div class="dz-annot-row ' + kind + '"><span class="k">' + label +
      '</span><span class="v">' + esc(val) + "</span></div>";
  }

  function openPop(a, target, btn) {
    closePop();
    btn.classList.add("sel");
    pop = document.createElement("div");
    pop.className = "dz-annot-pop";
    pop._btn = btn;
    pop.innerHTML =
      '<div class="dz-annot-pop-head">' +
        '<span class="dz-annot-pop-num">' + esc(a.n) + "</span>" +
        '<span class="dz-annot-pop-title">' + esc(a.label || a.el) + "</span>" +
        '<button class="dz-annot-pop-x" type="button" aria-label="Close">&times;</button>' +
      "</div>" +
      '<div class="dz-annot-pop-body">' +
        (a.logic ? '<p class="dz-annot-logic">' + esc(a.logic) + "</p>" : "") +
        row("ok", "Rules", a.validation) +
        row("warn", "Edge", a.exceptions) +
      "</div>";
    document.body.appendChild(pop);
    pop.querySelector(".dz-annot-pop-x").addEventListener("click", closePop);
    positionPop(pop, btn);
    setTimeout(function () {
      document.addEventListener("keydown", onKey, true);
      document.addEventListener("click", onOutside, true);
    }, 0);
  }

  function positionPop(el, btn) {
    var r = btn.getBoundingClientRect();
    var w = el.offsetWidth || 300, h = el.offsetHeight || 160, pad = 10;
    var left = r.right + pad;
    if (left + w > window.innerWidth - pad) left = r.left - w - pad;
    if (left < pad) left = pad;
    var top = r.top;
    if (top + h > window.innerHeight - pad) top = window.innerHeight - h - pad;
    if (top < pad) top = pad;
    el.style.left = left + "px";
    el.style.top = top + "px";
  }

  function init() {
    build();
    window.addEventListener("scroll", scheduleReposition, true);
    window.addEventListener("resize", scheduleReposition);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
