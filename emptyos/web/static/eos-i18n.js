/* eos-i18n.js — runtime UI translation (English source → user language).
 *
 * EmptyOS authors every string in English only. This walks the rendered DOM,
 * collects visible text + key attributes, asks the server to translate them
 * (server-side cache + the `translate` capability → local NLLB / LLM fallback),
 * and swaps them in place. New nodes (modals, async-rendered lists) are caught
 * by a MutationObserver.
 *
 * Self-gating + fail-silent: when ui.language === "en" (the default) this is a
 * complete no-op, so it's safe to load on every page. Loaded by eos.js.
 *
 * Opt out of translating a subtree with  data-i18n="skip"  (use it on code,
 * identifiers, proper nouns the model shouldn't touch).
 */
(function () {
  "use strict";

  var ATTRS = ["placeholder", "title", "aria-label", "alt"];
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, CODE: 1, PRE: 1, TEXTAREA: 1, NOSCRIPT: 1, KBD: 1, SAMP: 1 };
  var LANG = "en";
  var doneText = new WeakSet(); // text nodes already translated
  var localCache = {}; // src -> dst, this page session
  var queue = [];      // [{apply: fn, src: str}]
  var flushTimer = null;
  var _busy = false;   // at most ONE batch request in flight at a time
  var DEBOUNCE_MS = 250;
  var MAX_BATCH = 200; // cap strings per request; remainder stays queued

  function hasLetters(s) {
    return /[A-Za-z]/.test(s);
  }

  function skipSubtree(node) {
    var p = node.nodeType === 1 ? node : node.parentNode;
    while (p && p.nodeType === 1) {
      if (SKIP_TAGS[p.tagName]) return true;
      if (p.getAttribute && p.getAttribute("data-i18n") === "skip") return true;
      p = p.parentNode;
    }
    return false;
  }

  function enqueue(src, applyFn) {
    var dst = localCache[src];
    if (dst !== undefined) {
      applyFn(dst);
      return;
    }
    queue.push({ src: src, apply: applyFn });
    schedule();
  }

  // Debounced + serialized: never more than one in-flight batch per tab. A
  // burst of enqueues (initial page, MutationObserver storm on a live
  // dashboard) coalesces into sequential batches instead of flooding the
  // daemon with concurrent requests (which would stampede the NLLB model load
  // and wedge the event loop).
  function schedule() {
    if (_busy || flushTimer || !queue.length) return;
    flushTimer = setTimeout(function () {
      flushTimer = null;
      flush();
    }, DEBOUNCE_MS);
  }

  function collect(root) {
    if (root.nodeType === 1 && skipSubtree(root)) return;

    // Text nodes
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = walker.nextNode())) {
      if (doneText.has(n)) continue;
      var raw = n.nodeValue;
      if (!raw) continue;
      var trimmed = raw.trim();
      if (trimmed.length < 2 || !hasLetters(trimmed)) continue;
      if (skipSubtree(n)) continue;
      doneText.add(n);
      (function (node, original, core) {
        var lead = original.slice(0, original.indexOf(core));
        var tail = original.slice(original.indexOf(core) + core.length);
        enqueue(core, function (dst) {
          node.nodeValue = lead + dst + tail;
        });
      })(n, raw, trimmed);
    }

    // Attributes
    var scope = root.nodeType === 1 ? root : document.body;
    ATTRS.forEach(function (attr) {
      var els;
      try {
        els = scope.querySelectorAll("[" + attr + "]");
      } catch (e) {
        return;
      }
      els.forEach(function (el) {
        var marker = "__i18n_" + attr;
        if (el[marker]) return;
        if (skipSubtree(el)) return;
        var val = el.getAttribute(attr);
        if (!val) return;
        var core = val.trim();
        if (core.length < 2 || !hasLetters(core)) return;
        el[marker] = true;
        (function (element, a, c) {
          enqueue(c, function (dst) {
            element.setAttribute(a, dst);
          });
        })(el, attr, core);
      });
    });
  }

  // --- loading indicator (shown while a batch is in flight) ----------------
  var _inflight = 0;
  var _spinnerEl = null;

  function _ensureSpinnerStyle() {
    if (document.getElementById("eos-i18n-style")) return;
    var st = document.createElement("style");
    st.id = "eos-i18n-style";
    st.textContent =
      "@keyframes eos-i18n-spin{to{transform:rotate(360deg)}}" +
      ".eos-i18n-indicator{position:fixed;left:14px;bottom:14px;z-index:99999;" +
      "display:flex;align-items:center;gap:7px;padding:7px 12px;border-radius:999px;" +
      "background:var(--bg-card,#1f2430);color:var(--text,#e8e8e8);" +
      "border:1px solid var(--border,rgba(127,127,127,.25));" +
      "font:500 12px/1 system-ui,-apple-system,sans-serif;" +
      "box-shadow:0 4px 14px rgba(0,0,0,.28);opacity:0;transition:opacity .2s;" +
      "pointer-events:none}" +
      ".eos-i18n-indicator .dot{width:12px;height:12px;border-radius:50%;" +
      "border:2px solid var(--border,rgba(127,127,127,.3));" +
      "border-top-color:var(--accent,#6ea8fe);display:inline-block;" +
      "animation:eos-i18n-spin .7s linear infinite}" +
      "@media (prefers-reduced-motion:reduce){.eos-i18n-indicator .dot{animation:none}}";
    document.head.appendChild(st);
  }

  function showSpinner() {
    _inflight++;
    if (_spinnerEl || !document.body) return;
    _ensureSpinnerStyle();
    var el = document.createElement("div");
    el.className = "eos-i18n-indicator";
    el.setAttribute("data-i18n", "skip"); // never translate the indicator itself
    el.setAttribute("aria-live", "polite");
    el.innerHTML = '<span class="dot"></span><span>🌐</span>';
    document.body.appendChild(el);
    _spinnerEl = el;
    requestAnimationFrame(function () {
      if (_spinnerEl) _spinnerEl.style.opacity = "1";
    });
  }

  function hideSpinner() {
    _inflight = Math.max(0, _inflight - 1);
    if (_inflight > 0 || !_spinnerEl) return;
    var el = _spinnerEl;
    _spinnerEl = null;
    el.style.opacity = "0";
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 250);
  }

  function flush() {
    if (_busy || !queue.length) return;

    // Take up to MAX_BATCH unique source strings; leave the rest queued so a
    // big initial page still goes out as bounded, sequential requests.
    var seen = {};
    var strings = [];
    var batch = [];
    var rest = [];
    queue.forEach(function (item) {
      if (item.src in seen) {
        batch.push(item);
      } else if (strings.length < MAX_BATCH) {
        seen[item.src] = 1;
        strings.push(item.src);
        batch.push(item);
      } else {
        rest.push(item);
      }
    });
    queue = rest;

    _busy = true;
    showSpinner();
    fetch("/api/i18n/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang: LANG, strings: strings }),
    })
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        var map = (data && data.translations) || {};
        batch.forEach(function (item) {
          var dst = map[item.src];
          if (dst && dst !== item.src) {
            localCache[item.src] = dst;
            try {
              item.apply(dst);
            } catch (e) {}
          }
        });
      })
      .catch(function () {})
      .finally(function () {
        _busy = false;
        hideSpinner();
        if (queue.length) schedule(); // drain remainder, still one-at-a-time
      });
  }

  function observe() {
    var obs = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        m.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) collect(node);
          else if (node.nodeType === 3 && !doneText.has(node)) {
            // bare text node added directly — re-scan its parent element
            if (node.parentNode && node.parentNode.nodeType === 1) collect(node.parentNode);
          }
        });
      });
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  function start() {
    try {
      collect(document.body);
      observe();
    } catch (e) {
      /* fail-silent — never break a page over i18n */
    }
  }

  function init() {
    fetch("/api/i18n/lang")
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        if (!data || !data.lang || data.lang === "en") return; // no-op default
        LANG = data.lang;
        var html = document.documentElement;
        html.setAttribute("lang", LANG);
        html.setAttribute("dir", data.rtl ? "rtl" : "ltr");
        if (document.readyState === "loading") {
          document.addEventListener("DOMContentLoaded", start);
        } else {
          start();
        }
      })
      .catch(function () {});
  }

  init();
})();
