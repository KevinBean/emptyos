// designer — page UI logic.
// Loaded as an external sibling at end-of-body (same position the inline block
// would occupy), so global load order vs eos.js / eos-components.js is preserved
// and onclick= handlers resolve at click time. See .claude/rules/multi-module-apps.md
// (frontend counterpart) + memory feedback_app_page_sibling_assets (absolute path).

var STATE = { items: [], currentId: null, editMode: false, annotateMode: false, pickedEl: null, editToken: null };
var META = { styles: [], active_ability: 'standard', min_ability: 'standard', provider: '', model: '', embed_default: true, edit_enabled: false, annotate_enabled: false };

function escapeHtml(s) {
  if (typeof esc === 'function') return esc(String(s == null ? '' : s));
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Settings panel ──────────────────────────────────────────────────
var _appSettings = EOS_UI.settingsPanel({
  id: 'designer-settings-panel',
  title: 'Designer Settings',
  fields: [
    {key: 'designer.embed_viz', label: 'Embed viz elements', type: 'boolean', default: true,
     hint: 'Fill data-viz placeholders with real viz artifacts, baked inline.'},
    {key: 'designer.max_embeds', label: 'Max embedded elements', type: 'number', default: 4,
     hint: 'Cap on viz artifacts per page (each adds an LLM call + size).'},
    {key: 'designer.max_html_kb', label: 'Max HTML size (KB)', type: 'number', default: 384,
     hint: 'Refuse to save designs larger than this.'},
    {key: 'designer.think_domain', label: 'Think domain', type: 'select',
     options: [
       {value: 'code', label: 'code (preferred for HTML/CSS)'},
       {value: 'text', label: 'text'},
       {value: 'reason', label: 'reason'},
     ], default: 'code'},
  ],
});
function openAppSettings() { _appSettings.open(); }

// ── Hash routing ────────────────────────────────────────────────────
var _route = EOS_UI.hashRoute({
  onShow: function(id) { showArtifact(id); },
  onHide: function() { /* keep current view */ },
});

// ── Rendering ───────────────────────────────────────────────────────
function renderStyleOptions() {
  var sel = document.getElementById('style');
  var keep = sel.value;
  var opts = ['<option value="">— No design system —</option>'];
  META.styles.forEach(function(s) {
    var label = (s.domain ? '[' + s.domain + '] ' : '') + s.title;
    opts.push('<option value="' + escapeHtml(s.slug) + '">' + escapeHtml(label) + '</option>');
  });
  sel.innerHTML = opts.join('');
  if (keep) sel.value = keep;
  var hint = document.getElementById('style-hint');
  if (hint) {
    hint.textContent = META.styles.length
      ? 'Pick a style to match its colours, type, spacing & components.'
      : 'No design systems found — add a KB note (kind: pattern, topic: ui-design).';
  }
}

function renderAbilityBanner() {
  var el = document.getElementById('ability-gate');
  if (!el) return;
  if (EOS_UI.abilityMeets(META.active_ability, META.min_ability)) { el.innerHTML = ''; return; }
  el.innerHTML = EOS_UI.abilityBannerHtml({
    minAbility: META.min_ability, ability: META.active_ability,
    model: META.model, provider: META.provider,
  });
}

function renderList() {
  var el = document.getElementById('list');
  if (!STATE.items.length) {
    el.innerHTML = '<div class="hint">No designs yet.</div>';
    return;
  }
  el.innerHTML = STATE.items.map(function(r) {
    var active = r.id === STATE.currentId ? ' active' : '';
    var when = (r.updated || r.created || '').slice(0, 10);
    var nEmbed = (r.embeds && r.embeds.length) ? (' · ' + r.embeds.length + ' viz') : '';
    var style = r.style ? escapeHtml(r.style.replace(/^design-system-/, '')) : 'freeform';
    return '<div class="row-item' + active + '" onclick="_route.set(\'' + r.id + '\')">' +
      '<div>' + escapeHtml(r.prompt.slice(0, 80)) + (r.prompt.length > 80 ? '…' : '') + '</div>' +
      '<div class="meta"><span>' + style + '</span><span>' + when + ' · ' + r.size_kb + ' KB' + nEmbed + '</span></div>' +
    '</div>';
  }).join('');
}

function showArtifact(id) {
  id = String(id || '').replace(/^\/+/, '');
  STATE.currentId = id;
  var iframe = document.getElementById('preview');
  document.getElementById('empty').style.display = 'none';
  // edit + annotate are mutually exclusive overlays (clicks would conflict).
  var modeParam = STATE.editMode ? '&edit=1' : (STATE.annotateMode ? '&annotate=1' : '');
  iframe.src = '/designer/api/html/' + encodeURIComponent(id) + '?t=' + Date.now() + modeParam;
  iframe.style.display = 'block';
  iframe.onload = function() {
    fetch('/designer/api/rendered', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: id}),
    }).catch(function(){});
  };
  document.getElementById('gen-btn').style.display = 'none';
  document.getElementById('iter-btn').style.display = '';
  document.getElementById('cancel-iter').style.display = '';
  document.getElementById('prompt-label').textContent = 'Change request — say what to tweak';
  document.getElementById('prompt').placeholder = 'tighten the hero spacing and make the primary CTA larger';
  document.getElementById('prompt').value = '';
  var rb = document.getElementById('reannotate-btn');
  if (rb) rb.style.display = STATE.annotateMode ? '' : 'none';
  renderList();
}

function cancelIterate() {
  STATE.currentId = null;
  document.getElementById('gen-btn').style.display = '';
  document.getElementById('iter-btn').style.display = 'none';
  document.getElementById('cancel-iter').style.display = 'none';
  document.getElementById('prompt-label').textContent = 'Brief — describe the page';
  document.getElementById('prompt').placeholder = 'A pricing page for a developer tool…';
  _route.clear();
  renderList();
}

function reloadPreview() { if (STATE.currentId) showArtifact(STATE.currentId); }

function downloadCurrent() {
  if (!STATE.currentId) { EOS_UI.toast && EOS_UI.toast('No design loaded', 'warn'); return; }
  var a = document.createElement('a');
  a.href = '/designer/api/html/' + encodeURIComponent(STATE.currentId);
  a.download = 'designer-' + STATE.currentId + '.html';
  document.body.appendChild(a); a.click(); a.remove();
}

// ── API actions ─────────────────────────────────────────────────────
function setWorking(on, label) {
  var w = document.getElementById('working');
  w.style.display = on ? '' : 'none';
  if (label) w.textContent = label;
  document.getElementById('gen-btn').disabled = on;
  document.getElementById('iter-btn').disabled = on;
}

async function generate() {
  var prompt = document.getElementById('prompt').value.trim();
  var style = document.getElementById('style').value;
  var embed = document.getElementById('embed').checked;
  if (!prompt) { EOS_UI.toast && EOS_UI.toast('Brief is empty', 'warn'); return; }
  setWorking(true, embed ? 'designing + embedding…' : 'designing…');
  try {
    var res = await fetch('/designer/api/generate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt: prompt, style: style, embed: embed}),
    });
    var data = await res.json();
    if (!data.ok) {
      EOS_UI.toast && EOS_UI.toast('Generate failed: ' + (data.error || 'unknown'), 'err');
      return;
    }
    await loadList();
    _route.set(data.id);
    var msg = 'Design ready' + (data.embeds && data.embeds.length ? ' · ' + data.embeds.length + ' viz element(s)' : '');
    EOS_UI.toast && EOS_UI.toast(msg, 'ok');
  } catch (e) {
    EOS_UI.toast && EOS_UI.toast('Generate error: ' + e.message, 'err');
  } finally { setWorking(false, 'generating…'); }
}

async function iterate() {
  var prompt = document.getElementById('prompt').value.trim();
  if (!prompt || !STATE.currentId) return;
  setWorking(true, 'revising…');
  try {
    var res = await fetch('/designer/api/iterate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: STATE.currentId, prompt: prompt}),
    });
    var data = await res.json();
    if (!data.ok) {
      EOS_UI.toast && EOS_UI.toast('Iterate failed: ' + (data.error || 'unknown'), 'err');
      return;
    }
    await loadList();
    showArtifact(data.id);
    EOS_UI.toast && EOS_UI.toast('Updated', 'ok');
  } catch (e) {
    EOS_UI.toast && EOS_UI.toast('Iterate error: ' + e.message, 'err');
  } finally { setWorking(false, 'generating…'); }
}

async function loadList() {
  var res = await fetch('/designer/api/list');
  STATE.items = await res.json();
  renderList();
}

async function loadMeta() {
  try {
    var res = await fetch('/designer/api/meta');
    META = await res.json();
  } catch (e) { /* leave defaults */ }
  renderStyleOptions();
  renderAbilityBanner();
  var embedBox = document.getElementById('embed');
  if (embedBox && typeof META.embed_default === 'boolean') embedBox.checked = META.embed_default;
  var editToggle = document.getElementById('edit-toggle');
  if (editToggle) editToggle.style.display = META.edit_enabled ? '' : 'none';
  var annToggle = document.getElementById('annotate-toggle');
  if (annToggle) annToggle.style.display = META.annotate_enabled ? '' : 'none';
}

// ── Element-anchored edit loop ──────────────────────────────────────
function toggleEditMode() {
  STATE.editMode = !STATE.editMode;
  if (STATE.editMode && STATE.annotateMode) _setAnnotateUI(false);  // mutually exclusive
  var btn = document.getElementById('edit-toggle');
  if (btn) {
    btn.classList.toggle('btn-edit-on', STATE.editMode);
    btn.textContent = STATE.editMode ? '✎ Editing — click an element' : '✎ Edit elements';
  }
  if (!STATE.editMode) closeEditPanel();
  if (STATE.currentId) showArtifact(STATE.currentId);  // reload with/without ?edit=1
}

// ── Annotation overlay (墨刀-style spec pins) ────────────────────────
function _setAnnotateUI(on) {
  STATE.annotateMode = on;
  var btn = document.getElementById('annotate-toggle');
  if (btn) {
    btn.classList.toggle('btn-edit-on', on);
    btn.textContent = on ? '🏷 Annotating' : '🏷 Annotate';
  }
  var rb = document.getElementById('reannotate-btn');
  if (rb) rb.style.display = on ? '' : 'none';
}

async function toggleAnnotateMode() {
  if (!STATE.currentId) { EOS_UI.toast && EOS_UI.toast('Load a design first', 'warn'); return; }
  var turningOn = !STATE.annotateMode;
  if (turningOn && STATE.editMode) {
    STATE.editMode = false;  // mutually exclusive
    var eb = document.getElementById('edit-toggle');
    if (eb) { eb.classList.remove('btn-edit-on'); eb.textContent = '✎ Edit elements'; }
    closeEditPanel();
  }
  _setAnnotateUI(turningOn);
  if (turningOn) await _ensureAnnotations();  // generate on first entry
  showArtifact(STATE.currentId);              // reload with/without &annotate=1
}

async function _ensureAnnotations() {
  try {
    var res = await fetch('/designer/api/annotations/' + encodeURIComponent(STATE.currentId));
    var data = await res.json();
    if (data.ok && data.items && data.items.length) return;  // already annotated
  } catch (_) {}
  await _runAnnotate();
}

async function _runAnnotate() {
  setWorking(true, 'annotating…');
  try {
    var res = await fetch('/designer/api/annotate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: STATE.currentId }),
    });
    var data = await res.json();
    if (!data.ok) {
      EOS_UI.toast && EOS_UI.toast('Annotate failed: ' + (data.error || 'unknown'), 'err');
      return false;
    }
    EOS_UI.toast && EOS_UI.toast(data.count + ' annotation' + (data.count === 1 ? '' : 's'), 'ok');
    return true;
  } catch (e) {
    EOS_UI.toast && EOS_UI.toast('Annotate error: ' + e.message, 'err');
    return false;
  } finally { setWorking(false, 'generating…'); }
}

async function reAnnotate() {
  if (!STATE.currentId) return;
  var ok = await _runAnnotate();
  if (ok && STATE.annotateMode) showArtifact(STATE.currentId);  // reload overlay
}

function onEditMessage(e) {
  var iframe = document.getElementById('preview');
  if (!iframe || e.source !== iframe.contentWindow) return;  // identity, not origin ("null")
  var d = e.data;
  if (!d || d.type !== 'eos-edit-pick') return;
  if (typeof d.el !== 'string' || !/^e\d+$/.test(d.el)) return;
  openEditPanel(d.el, String(d.tag || ''), String(d.text || '').slice(0, 120));
}

function openEditPanel(el, tag, text) {
  STATE.pickedEl = el;
  STATE.editToken = null;
  var label = '<code>&lt;' + escapeHtml(tag || '?') + '&gt;</code>';
  if (text) label += ' — ' + escapeHtml(text) + (text.length >= 120 ? '…' : '');
  document.getElementById('edit-picked').innerHTML = label;
  document.getElementById('edit-instruction').value = '';
  document.getElementById('edit-form').style.display = '';
  document.getElementById('edit-preview').style.display = 'none';
  ['knob-size', 'knob-pad'].forEach(function (id) { var s = document.getElementById(id); if (s) s.value = ''; });
  document.getElementById('edit-panel').classList.add('open');
}

function closeEditPanel() {
  document.getElementById('edit-panel').classList.remove('open');
  STATE.pickedEl = null;
  STATE.editToken = null;
  var iframe = document.getElementById('preview');
  if (iframe && iframe.contentWindow) {
    try { iframe.contentWindow.postMessage({ type: 'eos-edit-clear' }, '*'); } catch (_) {}
  }
}

function setEditWorking(on) {
  document.getElementById('edit-working').style.display = on ? '' : 'none';
}

function proposeInstruction() {
  var instr = document.getElementById('edit-instruction').value.trim();
  if (!instr) { EOS_UI.toast && EOS_UI.toast('Describe the change first', 'warn'); return; }
  _propose({ instruction: instr });
}

function proposeKnob(prop, value) {
  if (!value) return;
  _propose({ knob: { prop: prop, value: value } });
}

async function _propose(payload) {
  if (!STATE.currentId || !STATE.pickedEl) return;
  setEditWorking(true);
  try {
    var body = Object.assign({ id: STATE.currentId, el: STATE.pickedEl }, payload);
    var res = await fetch('/designer/api/edit/propose', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    var data = await res.json();
    if (!data.ok) {
      if (data.fallback === 'iterate') {
        EOS_UI.toast && EOS_UI.toast("Can't isolate that element — use whole-page Iterate instead", 'warn');
        closeEditPanel();
        var p = document.getElementById('prompt');
        if (p && payload.instruction) { p.value = payload.instruction; p.focus(); }
      } else {
        EOS_UI.toast && EOS_UI.toast('Propose failed: ' + (data.error || 'unknown'), 'err');
      }
      return;
    }
    STATE.editToken = data.token;
    document.getElementById('edit-diff').innerHTML = EOS_UI.diffLinesHtml(data.diff_lines || []);
    document.getElementById('edit-form').style.display = 'none';
    document.getElementById('edit-preview').style.display = '';
  } catch (e) {
    EOS_UI.toast && EOS_UI.toast('Propose error: ' + e.message, 'err');
  } finally { setEditWorking(false); }
}

async function applyEdit() {
  if (!STATE.currentId || !STATE.editToken) return;
  setEditWorking(true);
  try {
    var res = await fetch('/designer/api/edit/apply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: STATE.currentId, token: STATE.editToken }),
    });
    var data = await res.json();
    if (!data.ok) {
      EOS_UI.toast && EOS_UI.toast(data.message || data.error || 'Apply failed', 'err');
      closeEditPanel();
      if (data.error === 'stale') reloadPreview();
      return;
    }
    EOS_UI.toast && EOS_UI.toast('Applied', 'ok');
    closeEditPanel();
    await loadList();
    showArtifact(STATE.currentId);  // reload (keeps ?edit=1 while in edit mode)
  } catch (e) {
    EOS_UI.toast && EOS_UI.toast('Apply error: ' + e.message, 'err');
  } finally { setEditWorking(false); }
}

async function rejectEdit() {
  var token = STATE.editToken;
  closeEditPanel();
  if (!token) return;
  try {
    await fetch('/designer/api/edit/reject', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token }),
    });
  } catch (_) {}
}

// ── Boot ────────────────────────────────────────────────────────────
async function boot() {
  EOS_UI.modelPill({app: 'designer', mount: '#model-pill', domain: 'code',
                    onSwitch: function(){ loadMeta(); }});
  window.addEventListener('message', onEditMessage);
  await Promise.all([loadMeta(), loadList()]);
  _route.init();
}
boot();
