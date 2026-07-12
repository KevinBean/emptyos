/* eos-parallel.js — shared left/right corresponding-text reader.
 *
 * EOS_UI.parallelText(opts) renders paired text (left | right) for
 * follow-along reading: 原文|注音, 原文|译文, bilingual lyrics, interlinear,
 * KB clause|注释 — anything "corresponding". Data shape matches the backend
 * parser emptyos/sdk/parallel_text.py (parse_parallel_md):
 *
 *   { title, sections: [ { title, rows: [{left, right}], blocks: [str] } ] }
 *
 * Usage:
 *   <link rel="stylesheet" href="/static/eos-parallel.css">
 *   <script src="/static/eos-parallel.js"></script>
 *   var pt = EOS_UI.parallelText({ mount: '#app', data: data });
 *   pt.refresh(newData);  pt.setFont(22);  pt.setShowRight(false);
 *
 * Options: { mount, data, fontSize=20, showRight=true, toolbar=true,
 *            title=true, toggleLabel='注音', sectionLabel=fn, onPick=fn }
 * Returns: { refresh(data), setFont(px), setShowRight(bool), root }
 */
(function () {
  window.EOS_UI = window.EOS_UI || {};
  if (EOS_UI.parallelText) return;

  function esc(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
    });
  }

  function defaultSectionLabel(t) {
    return String(t || '')
      .replace(/【[^】]*】/g, '')
      .replace(/（[^）]*）/g, '')
      .replace(/\([^)]*\)/g, '')
      .trim() || t;
  }

  function tagOf(title) {
    var m = String(title || '').match(/【([^】]*)】/);
    return m ? m[1] : '';
  }

  EOS_UI.parallelText = function (opts) {
    opts = opts || {};
    var mount = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
    if (!mount) throw new Error('EOS_UI.parallelText: mount not found');

    var st = {
      data: opts.data || null,
      fs: opts.fontSize || 20,
      showRight: opts.showRight !== false,
      toolbar: opts.toolbar !== false,
      showTitle: opts.title !== false,
      toggleLabel: opts.toggleLabel || '注音',
      sectionLabel: opts.sectionLabel || defaultSectionLabel,
      onPick: opts.onPick || null,
      activeKey: null,
    };

    // scaffold
    var root = document.createElement('div');
    root.className = 'eos-pt';
    mount.innerHTML = '';
    mount.appendChild(root);

    var bar = null, jumpSel = null, titleEl = null;
    if (st.toolbar) {
      bar = document.createElement('div');
      bar.className = 'eos-pt-bar';
      bar.innerHTML =
        '<span class="eos-pt-title"></span>' +
        '<select class="eos-pt-jump" title="跳到段落"></select>' +
        '<span class="eos-pt-spacer"></span>' +
        '<button class="eos-pt-btn" data-act="fsdown" title="减小字号">A−</button>' +
        '<button class="eos-pt-btn" data-act="fsup" title="增大字号">A+</button>' +
        '<button class="eos-pt-btn eos-pt-toggle" data-act="toggle" title="显示/隐藏对照列"></button>';
      root.appendChild(bar);
      titleEl = bar.querySelector('.eos-pt-title');
      jumpSel = bar.querySelector('.eos-pt-jump');
      bar.querySelector('[data-act="fsdown"]').onclick = function () { setFont(st.fs - 2); };
      bar.querySelector('[data-act="fsup"]').onclick = function () { setFont(st.fs + 2); };
      bar.querySelector('[data-act="toggle"]').onclick = function () { setShowRight(!st.showRight); };
      jumpSel.onchange = function () {
        var el = this.value && wrap.querySelector('#' + this.value);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      };
    }

    var wrap = document.createElement('div');
    wrap.className = 'eos-pt-wrap';
    root.appendChild(wrap);

    function applyFont() { root.style.setProperty('--eos-pt-fs', st.fs + 'px'); }
    function applyShowRight() {
      root.classList.toggle('hide-right', !st.showRight);
      var t = bar && bar.querySelector('.eos-pt-toggle');
      if (t) { t.textContent = st.toggleLabel; t.classList.toggle('on', st.showRight); }
    }

    function render() {
      var d = st.data;
      if (!d) { wrap.innerHTML = '<div class="eos-pt-empty">加载中…</div>'; return; }
      if (d.error) {
        wrap.innerHTML = '<div class="eos-pt-empty">' + esc(d.error) +
          (d.path ? '<br><small>' + esc(d.path) + '</small>' : '') + '</div>';
        return;
      }
      if (titleEl) titleEl.textContent = st.showTitle ? (d.title || '') : '';
      if (jumpSel) jumpSel.innerHTML = '<option value="">跳到段落…</option>';

      var html = '';
      (d.sections || []).forEach(function (sec, si) {
        var sid = 'epts-' + si;
        var tag = tagOf(sec.title);
        var label = st.sectionLabel(sec.title);
        if (jumpSel) jumpSel.insertAdjacentHTML('beforeend',
          '<option value="' + sid + '">' + esc(label) + '</option>');
        html += '<div class="eos-pt-sec" id="' + sid + '"><div class="eos-pt-sec-title">' +
          esc(label) + (tag ? '<span class="eos-pt-tag">' + esc(tag) + '</span>' : '') + '</div>';
        (sec.rows || []).forEach(function (r, ri) {
          var key = si + '-' + ri;
          var on = st.activeKey === key ? ' active' : '';
          html += '<div class="eos-pt-line' + on + '" data-key="' + key + '">' +
            '<div class="eos-pt-left">' + esc(r.left) + '</div>' +
            '<div class="eos-pt-right">' + esc(r.right) + '</div></div>';
        });
        (sec.blocks || []).forEach(function (b) {
          html += '<div class="eos-pt-block">' + esc(b) + '</div>';
        });
        html += '</div>';
      });
      if (!html) html = '<div class="eos-pt-empty">没有可对照的内容（需 <code>| 左 | 右 |</code> 表格）。' +
        (d.path ? '<br><small>' + esc(d.path) + '</small>' : '') + '</div>';
      wrap.innerHTML = html;

      Array.prototype.forEach.call(wrap.querySelectorAll('.eos-pt-line'), function (el) {
        el.onclick = function () { pick(el.getAttribute('data-key')); };
      });
    }

    function pick(key) {
      st.activeKey = (st.activeKey === key) ? null : key;
      Array.prototype.forEach.call(wrap.querySelectorAll('.eos-pt-line'), function (el) {
        el.classList.toggle('active', el.getAttribute('data-key') === st.activeKey);
      });
      if (st.onPick) try { st.onPick(st.activeKey); } catch (e) {}
    }

    function setFont(px) { st.fs = Math.max(12, Math.min(44, px)); applyFont(); }
    function setShowRight(b) { st.showRight = !!b; applyShowRight(); }
    function refresh(data) { st.data = data; st.activeKey = null; render(); }

    applyFont(); applyShowRight(); render();
    return { refresh: refresh, setFont: setFont, setShowRight: setShowRight, root: root };
  };
})();
