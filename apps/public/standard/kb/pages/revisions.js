/* kb — revision-diff compare UI (KBRev).
 *
 * Loaded after kb.js (same global scope; reuses esc/escAttr/EOS_UI). Mounts a
 * "Revisions" panel on a kind:reference detail view when the document has >=2
 * revisions, and opens a compare modal that renders the content-aligned clause
 * diff (status badges, expandable verbatim diff, lazy "what changed" digests,
 * publish-summary). Backed by /kb/api/revisions + /kb/api/revision-diff[/digest|/publish].
 * Feature-gated server-side (feature.revision-diff.enabled) — the routes 200 with
 * {error} when off, so the panel simply doesn't appear.
 */
var KBRev = (function () {
  var _cur = null;          // last computed diff result
  var _ctx = null;          // {standard_id, rev_a, rev_b}

  var STATUS = {
    unchanged: { label: 'unchanged', color: 'var(--text-muted)' },
    modified:  { label: 'modified',  color: 'var(--warning)' },
    renumbered:{ label: 'renumbered',color: 'var(--accent)' },
    added:     { label: 'added',     color: 'var(--success, #2e7d32)' },
    removed:   { label: 'removed',   color: 'var(--danger, #c0392b)' },
    split:     { label: 'split',     color: '#8e44ad' },
    merged:    { label: 'merged',    color: '#8e44ad' }
  };

  function _post(url, body) {
    return fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  // Mount the revisions panel into #detail-head for a reference note.
  async function mountPanel(data) {
    try {
      var p = (data && data.properties) || {};
      if (p.kind !== 'reference' || !p.standard_id) return;
      var res = await fetch('/kb/api/revisions/' + encodeURIComponent(p.standard_id))
        .then(function (r) { return r.json(); });
      if (res.error || !res.revisions || res.revisions.length < 2) return;
      var revs = res.revisions;  // newest first
      var chips = revs.map(function (r) {
        var cur = r.current ? ' · current' : '';
        return '<span class="kbrev-chip' + (r.current ? ' kbrev-chip-cur' : '') + '">' +
          esc(r.edition || '?') + cur + '</span>';
      }).join('');
      var newest = revs[0].edition, prev = revs[1].edition;
      var panel = '<div class="kbrev-panel">' +
        '<span class="kbrev-panel-label">Revisions of this document:</span> ' + chips +
        ' <button class="eos-btn-sm" onclick="KBRev.openCompare(' +
          escAttr(JSON.stringify(p.standard_id)) + ',' +
          escAttr(JSON.stringify(prev)) + ',' +
          escAttr(JSON.stringify(newest)) + ')">⇄ Compare ' +
          esc(prev) + ' → ' + esc(newest) + '</button>' +
        '</div>';
      var head = document.getElementById('detail-head');
      if (head) head.insertAdjacentHTML('beforeend', panel);
    } catch (e) { /* non-fatal */ }
  }

  // Compact header control: a revision <select> (switch revision) + Compare,
  // mounted into the header tools. Replaces the big "Revisions of this document" panel.
  async function mountHeader(tools, data) {
    try {
      var p = (data && data.properties) || {};
      if (!tools || p.kind !== 'reference' || !p.standard_id) return;
      var res = await fetch('/kb/api/revisions/' + encodeURIComponent(p.standard_id))
        .then(function (r) { return r.json(); });
      if (res.error || !res.revisions || res.revisions.length < 2) return;
      var revs = res.revisions;  // newest first
      var cur = p.edition || revs[0].edition;
      var opts = revs.map(function (r) {
        return '<option value="' + escAttr(r.slug) + '"' + (r.edition === cur ? ' selected' : '') + '>' +
          esc(r.edition) + (r.current ? ' · current' : '') + '</option>';
      }).join('');
      var newest = revs[0].edition, prev = revs[1].edition;
      var wrap = document.createElement('span');
      wrap.style.cssText = 'display:inline-flex;gap:6px;align-items:center';
      wrap.innerHTML =
        '<select class="kb-rev-select" id="kb-rev-select" title="Switch revision">' + opts + '</select>' +
        '<button class="kb-mini-btn" id="kb-rev-compare">⇄ Compare</button>';
      tools.appendChild(wrap);
      var sel = wrap.querySelector('#kb-rev-select');
      sel.addEventListener('change', function () {
        if (sel.value && window._route) _route.set(sel.value);
        else if (sel.value) location.hash = '#' + encodeURIComponent(sel.value);
      });
      wrap.querySelector('#kb-rev-compare').addEventListener('click', function () {
        openCompare(p.standard_id, prev, newest);
      });
    } catch (e) { /* non-fatal */ }
  }

  function _badge(status) {
    var s = STATUS[status] || { label: status, color: 'var(--text-muted)' };
    // Tint-don't-flood: 16% tint background + full-strength text, matching
    // the .kind-* badge language (works across light + dark themes).
    return '<span class="kbrev-badge" style="background:color-mix(in srgb,' + s.color + ' 16%,transparent);color:' + s.color + '">' + esc(s.label) + '</span>';
  }

  function _diffHtml(diff) {
    if (!diff || !diff.diff) return '<div class="kbrev-nodiff">No verbatim text difference (or text not located).</div>';
    var rows = diff.diff.split('\n').map(function (ln) {
      var cls = 'kbrev-ctx';
      if (ln.startsWith('+') && !ln.startsWith('+++')) cls = 'kbrev-add';
      else if (ln.startsWith('-') && !ln.startsWith('---')) cls = 'kbrev-del';
      else if (ln.startsWith('@@')) cls = 'kbrev-hunk';
      else if (ln.startsWith('+++') || ln.startsWith('---')) return '';
      return '<div class="' + cls + '">' + esc(ln) + '</div>';
    }).join('');
    return '<pre class="kbrev-diff">' + rows + '</pre>';
  }

  function _rowHtml(e, i) {
    var oldRef = e.old_no ? '§' + esc(e.old_no) : '—';
    var newRef = e.new_no ? '§' + esc(e.new_no) : '—';
    var title = esc(e.new_title || e.old_title || '');
    var conf = '';
    if (e.method === 'embedding' || e.method === 'llm') {
      conf = '<span class="kbrev-conf" title="content-matched (' + escAttr(e.method) +
        '), verify">~' + (e.similarity ? Math.round(e.similarity * 100) + '%' : 'AI') + '</span>';
    }
    var dim = e.status === 'unchanged' ? ' kbrev-row-dim' : '';
    var canDiff = (e.old_no || e.new_no);
    return '<div class="kbrev-row' + dim + '" id="kbrev-row-' + i + '">' +
      '<div class="kbrev-row-head">' +
        _badge(e.status) +
        '<span class="kbrev-refs">' + oldRef + ' → ' + newRef + '</span>' +
        '<span class="kbrev-title">' + title + '</span>' + conf +
        (canDiff ? '<button class="eos-btn-sm kbrev-mini" onclick="KBRev.toggleDiff(' + i + ')">diff</button>' : '') +
        (e.status !== 'unchanged' ? '<button class="eos-btn-sm kbrev-mini" onclick="KBRev.explain(' + i + ')">✨ explain</button>' : '') +
      '</div>' +
      '<div class="kbrev-digest" id="kbrev-digest-' + i + '">' + (e.digest ? esc(e.digest) : '') + '</div>' +
      '<div class="kbrev-diffwrap" id="kbrev-diff-' + i + '" style="display:none"></div>' +
    '</div>';
  }

  function _renderBody(d) {
    if (d.error) return '<div class="empty">' + esc(d.error) + (d.have ? ' — have: ' + esc((d.have || []).join(', ')) : '') + '</div>';
    var counts = Object.keys(d.counts || {}).filter(function (k) { return k !== 'unchanged'; })
      .map(function (k) { return d.counts[k] + ' ' + k; }).join(' · ') || 'no changes';
    var byChapter = d.chapters || {};
    // index edges by key for chapter grouping
    var keyOf = function (e) { return (e.old_slug || '∅') + '|' + (e.new_slug || '∅'); };
    var idxByKey = {};
    d.edges.forEach(function (e, i) { idxByKey[keyOf(e)] = i; });
    var chapters = Object.keys(byChapter).sort(function (a, b) { return (parseInt(a) || 99) - (parseInt(b) || 99); });
    var body = '<div class="kbrev-summary"><b>' + esc(d.rev_a) + ' → ' + esc(d.rev_b) + '</b> · ' + esc(counts) +
      ' <button class="eos-btn-sm" style="float:right" onclick="KBRev.publish()">⬇ Publish change summary</button></div>';
    body += chapters.map(function (ch) {
      var keys = byChapter[ch];
      var rows = keys.map(function (k) { var i = idxByKey[k]; return i == null ? '' : _rowHtml(d.edges[i], i); }).join('');
      return '<div class="kbrev-chapter"><div class="kbrev-chapter-h">Chapter ' + esc(ch) + '</div>' + rows + '</div>';
    }).join('');
    return body;
  }

  async function openCompare(standardId, revA, revB) {
    _ctx = { standard_id: standardId, rev_a: revA, rev_b: revB };
    EOS_UI.modal({ title: '⇄ Revision compare', body: '<div class="empty">Aligning clauses…</div>', width: 860 });
    var d = await _post('/kb/api/revision-diff', _ctx);
    _cur = d;
    var box = document.querySelector('.eos-modal-body') || document.querySelector('.modal-body');
    if (box) box.innerHTML = _renderBody(d);
  }

  function toggleDiff(i) {
    var wrap = document.getElementById('kbrev-diff-' + i);
    if (!wrap || !_cur) return;
    if (wrap.style.display === 'none') {
      wrap.innerHTML = _diffHtml(_cur.edges[i].verbatim_diff);
      wrap.style.display = 'block';
    } else { wrap.style.display = 'none'; }
  }

  async function explain(i) {
    if (!_cur || !_ctx) return;
    var e = _cur.edges[i];
    var box = document.getElementById('kbrev-digest-' + i);
    if (e.digest) { if (box) box.textContent = e.digest; return; }
    if (box) box.textContent = '…';
    var r = await _post('/kb/api/revision-diff/digest', Object.assign({}, _ctx, {
      old_slug: e.old_slug, new_slug: e.new_slug
    }));
    e.digest = r.digest || r.error || '(no digest)';
    if (box) box.textContent = e.digest;
  }

  async function publish() {
    if (!_ctx) return;
    var r = await _post('/kb/api/revision-diff/publish', _ctx);
    if (r.ok) EOS_UI.toast ? EOS_UI.toast('Saved change summary: ' + r.slug) : alert('Saved: ' + r.slug);
    else alert(r.error || 'publish failed');
  }

  return { mountPanel: mountPanel, mountHeader: mountHeader, openCompare: openCompare, toggleDiff: toggleDiff, explain: explain, publish: publish };
})();
