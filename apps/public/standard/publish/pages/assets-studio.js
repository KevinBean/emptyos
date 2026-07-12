/* Publish asset studio — in-app audit + diagram/screenshot generation.
 *
 * Loaded at end-of-body from index.html (same global scope: EOS, EOS_UI, esc).
 * Exposes window.openAssets(postFile, title) and ASSET_STUDIO_ON (gates the
 * per-row 🎨 button, set from GET /publish/api/assets/status on load).
 * Every generate/capture is propose -> preview -> Apply/Reject; nothing is
 * written to images/ or media/ until the author clicks Apply.
 */
(function () {
  window.ASSET_STUDIO_ON = false;

  function api(path, body) {
    return EOS.post(path, body);
  }

  // Feature-detect, then re-render the post list so the 🎨 button appears.
  EOS.api('/publish/api/assets/status').then(function (s) {
    window.ASSET_STUDIO_ON = !!(s && s.enabled);
    if (window.ASSET_STUDIO_ON && typeof renderPosts === 'function') renderPosts();
  }).catch(function () {});

  var _pending = null;   // {kind, id, name}

  window.openAssets = function (postFile, title) {
    if (!window.ASSET_STUDIO_ON) {
      EOS_UI.toast('Asset studio is off — set feature.asset-studio.enabled', false);
      return;
    }
    var body =
      '<div class="as-wrap" style="display:flex;flex-direction:column;gap:14px;max-width:720px">' +
        '<div><div style="font-weight:600;margin-bottom:6px">Referenced media</div>' +
          '<div id="as-audit" style="font-size:13px;color:var(--text-muted)">Loading…</div></div>' +
        '<hr style="border:none;border-top:1px solid var(--border)">' +
        '<div><div style="font-weight:600;margin-bottom:6px">🖼 Generate diagram</div>' +
          '<textarea id="as-dg-prompt" rows="3" placeholder="Describe the diagram (the missing image\'s alt text is a great seed)…" style="width:100%;box-sizing:border-box"></textarea>' +
          '<div style="display:flex;gap:8px;margin-top:6px">' +
            '<input id="as-dg-name" placeholder="filename.svg" style="flex:1">' +
            '<button class="pi-btn" id="as-dg-go">Generate</button></div></div>' +
        '<div><div style="font-weight:600;margin-bottom:6px">📸 Capture screenshot</div>' +
          '<input id="as-sh-url" placeholder="URL (your own pages auto-auth)" style="width:100%;box-sizing:border-box;margin-bottom:6px">' +
          '<div style="display:flex;gap:8px;margin-bottom:6px">' +
            '<input id="as-sh-blur" placeholder="blur selectors (csv, optional)" style="flex:1">' +
            '<label style="font-size:12px;display:flex;align-items:center;gap:4px"><input type="checkbox" id="as-sh-full">full page</label></div>' +
          '<div style="display:flex;gap:8px">' +
            '<input id="as-sh-name" placeholder="filename.png" style="flex:1">' +
            '<button class="pi-btn" id="as-sh-go">Capture</button></div></div>' +
        '<div id="as-preview"></div>' +
      '</div>';
    EOS_UI.modal({ title: 'Asset studio — ' + (title || postFile), body: body });

    _loadAudit(postFile);
    document.getElementById('as-dg-go').onclick = _generateDiagram;
    document.getElementById('as-sh-go').onclick = _captureShot;
  };

  function _loadAudit(postFile) {
    var el = document.getElementById('as-audit');
    EOS.api('/publish/api/assets/audit?post=' + encodeURIComponent(postFile)).then(function (d) {
      if (!el) return;
      if (d.error) { el.textContent = d.error; return; }
      if (!d.items.length) { el.textContent = 'No image references in this post.'; return; }
      var items = d.items;   // closure — wired by index, no data-in-markup
      el.innerHTML = items.map(function (i, idx) {
        var ok = i.status === 'ok';
        var dot = ok ? '<span style="color:var(--success,#34d399)">●</span>'
                     : '<span style="color:var(--danger,#f87171)">○ missing</span>';
        var fix = ok ? '' :
          ' <a href="#" data-idx="' + idx + '" style="font-size:12px">use ' + esc(i.generator) + ' →</a>';
        return '<div style="display:flex;gap:8px;align-items:center;padding:2px 0">' +
          dot + ' <code style="font-size:12px">' + esc(i.ref) + '</code>' + fix + '</div>';
      }).join('');
      // Wire the "use diagram/screenshot" prefill links by index into `items`.
      Array.prototype.forEach.call(el.querySelectorAll('a[data-idx]'), function (a) {
        a.onclick = function (e) {
          e.preventDefault();
          var i = items[parseInt(a.getAttribute('data-idx'), 10)];
          if (!i) return;
          if (i.generator === 'diagram') {
            document.getElementById('as-dg-name').value = i.name;
            document.getElementById('as-dg-prompt').focus();
          } else {
            document.getElementById('as-sh-name').value = i.name;
            document.getElementById('as-sh-url').focus();
          }
        };
      });
    }).catch(function (e) { if (el) el.textContent = 'Audit failed: ' + (e.message || e); });
  }

  function _previewCard(html) {
    document.getElementById('as-preview').innerHTML =
      '<hr style="border:none;border-top:1px solid var(--border)">' + html;
  }

  async function _generateDiagram() {
    var prompt = document.getElementById('as-dg-prompt').value.trim();
    var name = document.getElementById('as-dg-name').value.trim();
    if (!prompt) { EOS_UI.toast('Describe the diagram first', false); return; }
    _previewCard('<div style="color:var(--text-muted)">Generating diagram…</div>');
    try {
      var r = await api('/publish/api/assets/diagram/propose', { prompt: prompt, name: name });
      if (r.error) { _previewCard('<div style="color:var(--danger)">' + esc(r.error) + '</div>'); return; }
      _pending = { kind: 'diagram', id: r.pending_id, name: r.name };
      _renderPreview(r.preview, r.name, false, []);
    } catch (e) { _previewCard('<div style="color:var(--danger)">' + esc(e.message || e) + '</div>'); }
  }

  async function _captureShot() {
    var url = document.getElementById('as-sh-url').value.trim();
    var name = document.getElementById('as-sh-name').value.trim();
    var blur = document.getElementById('as-sh-blur').value.trim();
    var full = document.getElementById('as-sh-full').checked;
    if (!url) { EOS_UI.toast('URL required', false); return; }
    _previewCard('<div style="color:var(--text-muted)">Capturing…</div>');
    try {
      var r = await api('/publish/api/assets/shot/propose', { url: url, blur: blur, full_page: full });
      if (r.error) { _previewCard('<div style="color:var(--danger)">' + esc(r.error) + '</div>'); return; }
      _pending = { kind: 'screenshot', id: r.pending_id, name: name || 'screenshot.png' };
      _renderPreview(r.preview, _pending.name, true, r.redaction_hits || []);
    } catch (e) { _previewCard('<div style="color:var(--danger)">' + esc(e.message || e) + '</div>'); }
  }

  function _renderPreview(previewUrl, name, isShot, hits) {
    var warn = '';
    if (hits && hits.length) {
      warn = '<div style="background:color-mix(in srgb,var(--danger) 12%,transparent);border-radius:8px;padding:8px;margin:8px 0;font-size:12px">' +
        '⚠ Redaction gate: ' + hits.length + ' protected pattern(s) visible — ' +
        esc(hits.slice(0, 4).map(function (h) { return h.match; }).join(', ')) +
        '. Blur those regions and recapture, or tick “apply anyway”.' +
        '<label style="display:block;margin-top:6px"><input type="checkbox" id="as-force"> apply anyway (I reviewed it)</label></div>';
    }
    var altRow = isShot
      ? '<input id="as-alt" placeholder="alt text (optional, for accessibility)" style="width:100%;box-sizing:border-box;margin:6px 0">'
      : '';
    _previewCard(
      '<div style="font-weight:600;margin:6px 0">Preview — ' + esc(name) + '</div>' +
      '<img src="' + previewUrl + '" style="max-width:100%;border:1px solid var(--border);border-radius:8px;background:#fff">' +
      warn + altRow +
      '<div style="display:flex;gap:8px;margin-top:8px">' +
        '<button class="pi-btn" id="as-apply">Apply → write file</button>' +
        '<button class="pi-btn" id="as-reject">Reject</button></div>'
    );
    document.getElementById('as-apply').onclick = _apply;
    document.getElementById('as-reject').onclick = _reject;
  }

  async function _apply() {
    if (!_pending) return;
    var payload = { pending_id: _pending.id, name: _pending.name };
    var path = _pending.kind === 'diagram'
      ? '/publish/api/assets/diagram/apply'
      : '/publish/api/assets/shot/apply';
    if (_pending.kind === 'screenshot') {
      var altEl = document.getElementById('as-alt');
      if (altEl && altEl.value.trim()) payload.alt = altEl.value.trim();
      var force = document.getElementById('as-force');
      if (force && force.checked) payload.force = true;
    }
    try {
      var r = await api(path, payload);
      if (r.error) { EOS_UI.toast(r.error, false); return; }
      EOS_UI.toast('Wrote ' + (r.written || []).join(', ') + ' — run Build to publish', true);
      _pending = null;
      document.getElementById('as-preview').innerHTML = '';
    } catch (e) { EOS_UI.toast('Apply failed: ' + (e.message || e), false); }
  }

  async function _reject() {
    if (_pending) { try { await api('/publish/api/assets/reject', { pending_id: _pending.id }); } catch (e) {} }
    _pending = null;
    document.getElementById('as-preview').innerHTML = '';
  }
})();
