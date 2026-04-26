var _scenarios = [];
var _historyFilter = '';
var _agentScenarios = [];
var _agentSubjects = [];

// Two independent benchmarks. `switchMode` picks which one (text-bench
// or agent-bench); `switchTab` picks the sub-view inside text-bench.
// Agent-bench has no sub-tabs — it's a single rich view.
var _currentMode = 'text';  // 'text' | 'agent'

function switchMode(mode) {
  _currentMode = mode;
  document.getElementById('mode-text').classList.toggle('active', mode === 'text');
  document.getElementById('mode-agent').classList.toggle('active', mode === 'agent');
  var subtabs = document.getElementById('text-subtabs');
  if (subtabs) subtabs.classList.toggle('hidden', mode !== 'text');

  var textTabs = ['run', 'ranking', 'history'];
  textTabs.forEach(function(t) {
    var el = document.getElementById('tab-' + t);
    if (el) el.classList.toggle('active', mode === 'text' && _activeTextTab === t);
  });
  var agentEl = document.getElementById('tab-agent');
  if (agentEl) agentEl.classList.toggle('active', mode === 'agent');

  var headerActions = document.getElementById('text-bench-actions');
  if (headerActions) headerActions.style.display = (mode === 'agent') ? 'none' : '';

  if (mode === 'agent') loadAgent();
  else if (_activeTextTab === 'ranking') loadRanking();
  else if (_activeTextTab === 'history') loadHistory();
}

var _activeTextTab = 'run';
function switchTab(name) {
  // Only meaningful in text-bench mode
  _activeTextTab = name;
  ['run', 'ranking', 'history'].forEach(function(t, i) {
    document.querySelectorAll('#text-subtabs .eos-tab')[i].classList.toggle('active', t === name);
    document.getElementById('tab-' + t).classList.toggle('active', t === name);
  });
  // Ensure the agent pane is hidden while we're on text sub-tabs
  var agentEl = document.getElementById('tab-agent');
  if (agentEl) agentEl.classList.remove('active');
  if (name === 'history') loadHistory();
  if (name === 'ranking') loadRanking();
  _currentMode = 'text';
  document.getElementById('mode-text').classList.add('active');
  document.getElementById('mode-agent').classList.remove('active');
  var subtabs = document.getElementById('text-subtabs');
  if (subtabs) subtabs.classList.remove('hidden');
  var headerActions = document.getElementById('text-bench-actions');
  if (headerActions) headerActions.style.display = '';
}

function okClass(rate) {
  if (rate >= 0.9) return 'ok-high';
  if (rate >= 0.5) return 'ok-mid';
  return 'ok-low';
}

function toggleResponse(el) { el.classList.toggle('expanded'); }
function toggleSnippet(el) { el.classList.toggle('expanded'); }

function markClampedResponses(root) {
  (root || document).querySelectorAll('.bench-table .response').forEach(function(el) {
    if (el.scrollHeight > el.clientHeight + 2) el.classList.add('clamped');
  });
}

async function applyBucketChain(bucket, variantsJson) {
  try {
    var variants = JSON.parse(variantsJson);
    if (!variants.length) { EOS_UI.toast('No variants with successful runs'); return; }
    var resp = await fetch('/model-bench/api/apply-chain', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({bucket: bucket, variants: variants}),
    });
    var data = await resp.json();
    if (data.error) { EOS_UI.toast('Error: ' + data.error); return; }
    var msg = 'Applied ' + data.variants.length + ' variants to ' + bucket;
    if (data.unknown && data.unknown.length) msg += ' (' + data.unknown.length + ' unknown skipped)';
    EOS_UI.toast(msg);
    loadRanking();
  } catch (e) { EOS_UI.toast('Error: ' + e.message); }
}

async function clearBucketChain(bucket) {
  try {
    var resp = await fetch('/model-bench/api/apply-chain?bucket=' + encodeURIComponent(bucket), {method: 'DELETE'});
    var data = await resp.json();
    if (data.error) { EOS_UI.toast('Error: ' + data.error); return; }
    EOS_UI.toast('Cleared ' + bucket);
    loadRanking();
  } catch (e) { EOS_UI.toast('Error: ' + e.message); }
}

async function loadRanking() {
  var el = document.getElementById('ranking-list');
  el.innerHTML = '<div class="empty">Loading...</div>';
  try {
    var data = await EOS.api('/model-bench/api/ranking');
    var applied = {};
    try {
      var chainsData = await EOS.api('/model-bench/api/chains');
      applied = (chainsData && chainsData.chains) || {};
    } catch (e) { /* ok */ }
    var buckets = (data && data.buckets) || [];
    if (!buckets.length) { el.innerHTML = '<div class="empty">No buckets</div>'; return; }
    el.innerHTML = buckets.map(function(b) {
      var variants = b.variants || [];
      var empty = !variants.length;
      var rows = variants.map(function(v, i) {
        var okPct = Math.round(v.ok_rate * 100);
        var modelBadge = v.model
          ? '<span class="variant-meta" title="model">' + EOS_UI.esc(v.model) + '</span>'
          : '';
        var modeBadge = v.mode
          ? '<span class="variant-meta mode" title="mode/effort">' + EOS_UI.esc(v.mode) + '</span>'
          : '';
        var full = v.latest_response || v.latest_error || '';
        var snippetCell = full
          ? '<td class="snippet" onclick="toggleSnippet(this)" title="Click to expand / collapse">' + EOS_UI.esc(full) + '</td>'
          : '<td class="snippet"></td>';
        return '<tr>'
          + '<td class="rank-n">' + (v.ok_count ? (i + 1) : '—') + '</td>'
          + '<td class="provider">'
            + '<div>' + EOS_UI.esc(v.provider) + '</div>'
            + (modelBadge || modeBadge ? '<div class="variant-badges">' + modelBadge + modeBadge + '</div>' : '')
          + '</td>'
          + '<td class="stat">' + v.runs
            + (v.skipped ? ' <span class="eos-badge eos-badge-status-shelved" title="skipped due to cloud consent — approve in /providers">+' + v.skipped + ' skipped</span>' : '')
          + '</td>'
          + '<td class="stat ' + okClass(v.ok_rate) + '">' + (v.runs ? okPct + '%' : '—') + '</td>'
          + '<td class="stat">' + (v.median_latency_ms ? v.median_latency_ms + 'ms' : '—') + '</td>'
          + snippetCell
          + '</tr>';
      }).join('');
      var order = b.suggested_order || [];
      var orderJson = EOS_UI.esc(JSON.stringify(order)).replace(/\'/g, '&#39;');
      var bucketEsc = EOS_UI.esc(b.id).replace(/\'/g, '\\\'');
      var activeChain = applied[b.id];
      var appliedBadge = activeChain
        ? '<span class="eos-badge eos-badge-status-active" title="currently routing this bucket through ' + EOS_UI.esc((activeChain || []).join(', ')) + '">applied</span>'
        : '';
      var chainHtml = b.suggested_chain
        ? '<span class="val">' + EOS_UI.esc(b.suggested_chain) + '</span>'
          + '<button class="eos-btn eos-btn-xs" onclick="navigator.clipboard.writeText(\'' + EOS_UI.esc(b.suggested_chain).replace(/\'/g, '\\\'') + '\');EOS_UI.toast(\'Copied\')">Copy</button>'
          + '<button class="eos-btn eos-btn-xs eos-btn-primary" title="Route this bucket through the suggested chain (persists + hot-applies)" onclick="applyBucketChain(\'' + bucketEsc + '\', \'' + orderJson + '\')">Apply</button>'
          + (activeChain ? '<button class="eos-btn eos-btn-xs" title="Remove override" onclick="clearBucketChain(\'' + bucketEsc + '\')">Clear</button>' : '')
        : '<span class="val empty-chain">no successful runs yet</span>';
      return '<div class="rank-bucket' + (empty ? ' empty-bucket' : '') + '">'
        + '<div class="hdr">'
          + '<span class="bucket-id">' + EOS_UI.esc(b.id) + '</span>'
          + appliedBadge
          + '<span class="bucket-desc">' + EOS_UI.esc(b.description) + '</span>'
          + '<span class="n-runs">' + b.n_runs + ' run' + (b.n_runs === 1 ? '' : 's') + '</span>'
        + '</div>'
        + '<div class="chain"><span class="lbl">Suggested chain</span>' + chainHtml + '</div>'
        + (empty
            ? '<div class="empty" style="padding:12px">No runs yet. <button class="eos-btn eos-btn-xs" onclick="runOne(\'' + EOS_UI.esc(b.id).replace(/\'/g, '\\\'') + '\');switchTab(\'run\')">Run this bucket</button></div>'
            : '<table class="rank-table"><tr><th></th><th>Provider</th><th style="text-align:right">Runs</th><th style="text-align:right">OK %</th><th style="text-align:right">Median lat.</th><th>Latest response / error</th></tr>' + rows + '</table>')
        + '</div>';
    }).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty">Error: ' + EOS_UI.esc(e.message) + '</div>';
  }
}

function scenarioIdFromHash() {
  var h = (location.hash || '').replace(/^#/, '');
  return h && _scenarios.some(function(s) { return s.id === h; }) ? h : '';
}

function markActiveScenario(id) {
  document.querySelectorAll('.scenario-card').forEach(function(el) {
    el.classList.toggle('active', el.dataset.id === id);
  });
}

async function loadScenarios() {
  try {
    var scenarios = await EOS.api('/model-bench/api/scenarios');
    _scenarios = scenarios || [];
    var el = document.getElementById('scenarios');
    if (!_scenarios.length) { el.innerHTML = '<div class="empty" style="padding:12px;grid-column:1/-1">No scenarios</div>'; return; }
    el.innerHTML = _scenarios.map(function(s, i) {
      var preview = (s.prompt_preview || '').replace(/\n/g, ' ').slice(0, 90);
      return '<div class="scenario-card" data-id="' + EOS_UI.esc(s.id) + '" style="animation-delay:' + (i * 0.03) + 's">'
        + '<div class="row1">'
          + '<span class="bucket">' + EOS_UI.esc(s.id) + '</span>'
          + '<span class="shape">' + EOS_UI.esc(s.task_shape || '') + '</span>'
        + '</div>'
        + '<div class="desc">' + EOS_UI.esc(s.description || '') + '</div>'
        + '<div class="preview" title="' + EOS_UI.esc(s.prompt_preview || '') + '">' + EOS_UI.esc(preview) + '…</div>'
        + '<div class="actions">'
          + '<button class="eos-btn eos-btn-xs" onclick="runOne(\'' + EOS_UI.esc(s.id).replace(/\'/g, '\\\'') + '\')">Run</button>'
          + '<button class="eos-btn eos-btn-xs" onclick="filterHistory(\'' + EOS_UI.esc(s.id).replace(/\'/g, '\\\'') + '\')">History</button>'
        + '</div>'
        + '</div>';
    }).join('');
    var hashId = scenarioIdFromHash();
    if (hashId) { markActiveScenario(hashId); }
  } catch (e) {
    document.getElementById('scenarios').innerHTML = '<div class="empty" style="padding:12px;grid-column:1/-1">Error loading</div>';
  }
}

function isConsentSkip(err) {
  return typeof err === 'string' && err.indexOf('cloud provider not pre-approved') !== -1;
}

async function approveAllCloudAndRun() {
  try {
    var live = await EOS.api('/providers/api/providers/live');
    var names = {};
    ((live && live.providers) || []).forEach(function(p) { if (p.is_cloud && p.name) names[p.name] = true; });
    var unique = Object.keys(names);
    if (!unique.length) { EOS_UI.toast('No cloud providers configured'); return runAll(); }
    await Promise.all(unique.map(function(name) { return EOS.post('/api/cloud/approve', {provider: name}); }));
    EOS_UI.toast('Approved ' + unique.length + ' cloud provider' + (unique.length === 1 ? '' : 's'));
    await runAll();
  } catch (e) { EOS_UI.toast('Error: ' + e.message); }
}

async function approveAndRetry(provider, scenarioId) {
  try {
    await EOS.post('/api/cloud/approve', {provider: provider});
    EOS_UI.toast('Approved ' + provider + ' — re-running');
    if (scenarioId) { await runOne(scenarioId); } else { await runAll(); }
  } catch (e) { EOS_UI.toast('Error: ' + e.message); }
}

function renderResults(entries) {
  return entries.map(function(entry, idx) {
    var results = entry.results || [];
    var fastest = results.filter(function(r) { return !r.error; })
      .sort(function(a, b) { return a.latency_ms - b.latency_ms; })[0];
    var scenarioId = entry.scenario || '';
    var rows = results.map(function(r) {
      var variantId = r.variant || r.provider;
      var isBest = fastest && (fastest.variant || fastest.provider) === variantId && !r.error;
      var label = EOS_UI.esc(r.provider || variantId);
      if (r.model) label += ' <span class="variant-meta">' + EOS_UI.esc(r.model) + '</span>';
      if (r.mode)  label += ' <span class="variant-meta mode">' + EOS_UI.esc(r.mode) + '</span>';
      var consentSkipped = isConsentSkip(r.error);
      var latencyCell = r.error ? '<span class="error">' + (consentSkipped ? 'SKIP' : 'ERR') + '</span>' : r.latency_ms + 'ms';
      var responseCell;
      if (consentSkipped) {
        var escProvider = EOS_UI.esc(r.provider || '').replace(/\'/g, '\\\'');
        var escScenario = EOS_UI.esc(scenarioId).replace(/\'/g, '\\\'');
        responseCell = '<span class="error">cloud provider not pre-approved</span> '
          + '<button class="eos-btn eos-btn-xs" onclick="approveAndRetry(\'' + escProvider + '\',\'' + escScenario + '\')">Approve &amp; retry</button>';
      } else if (r.error) {
        responseCell = '<span class="error">' + EOS_UI.esc(r.error || '') + '</span>';
      } else {
        responseCell = EOS_UI.esc(r.response || '');
      }
      var respAttrs = consentSkipped
        ? ''
        : ' onclick="toggleResponse(this)" title="Click to expand / collapse"';
      return '<tr>'
        + '<td class="provider">' + label + (isBest ? '<span class="best-tag">fastest</span>' : '') + '</td>'
        + '<td class="latency">' + latencyCell + '</td>'
        + '<td class="response"' + respAttrs + '>' + responseCell + '</td></tr>';
    }).join('');
    return '<div class="bench-result" style="animation-delay:' + (idx * 0.06) + 's">'
      + '<div class="bench-scenario">' + EOS_UI.esc(entry.scenario || '')
      + '<span class="result-time">' + (entry.timestamp || '').slice(11, 19) + '</span></div>'
      + '<div class="bench-desc">' + EOS_UI.esc(entry.description || '') + '</div>'
      + '<table class="bench-table"><tr><th>Provider</th><th style="text-align:right">Latency</th><th>Response</th></tr>'
      + rows + '</table></div>';
  }).join('');
}

async function _run(url, label) {
  var btn = document.getElementById('run-all-btn');
  var statusEl = document.getElementById('run-status');
  btn.disabled = true; btn.textContent = '...';
  statusEl.innerHTML = '<div class="run-status running">' + label + '</div>';
  document.getElementById('run-results').innerHTML = '';
  try {
    var results = await EOS.api(url);
    if (results && results.error) {
      statusEl.innerHTML = '<div class="run-status">' + EOS_UI.esc(results.error) + '</div>';
      return;
    }
    statusEl.innerHTML = '';
    document.getElementById('run-results').innerHTML = renderResults(results);
    markClampedResponses(document.getElementById('run-results'));
    EOS_UI.toast((results.length || 0) + ' scenarios completed');
  } catch (e) {
    statusEl.innerHTML = '<div class="run-status">Error: ' + EOS_UI.esc(e.message) + '</div>';
  }
  btn.disabled = false; btn.textContent = 'Run all';
}

async function runAll() { await _run('/model-bench/api/run', 'Running all buckets across providers...'); }
async function runOne(id) {
  location.hash = id;
  markActiveScenario(id);
  await _run('/model-bench/api/run?scenario=' + encodeURIComponent(id), 'Running ' + id + '...');
}

function filterHistory(id) {
  _historyFilter = id;
  switchTab('history');
}

async function loadHistory() {
  try {
    var url = '/model-bench/api/results?limit=30' + (_historyFilter ? '&scenario=' + encodeURIComponent(_historyFilter) : '');
    var results = await EOS.api(url);
    var filterEl = document.getElementById('history-filter');
    var chips = [''].concat(_scenarios.map(function(s) { return s.id; })).map(function(id) {
      var label = id || 'All';
      var active = (id === _historyFilter);
      return '<button class="eos-btn eos-btn-xs' + (active ? ' eos-btn-primary' : '') + '" onclick="_historyFilter=\'' + EOS_UI.esc(id).replace(/\'/g, '\\\'') + '\';loadHistory()">' + EOS_UI.esc(label) + '</button>';
    }).join('');
    filterEl.innerHTML = chips;
    var el = document.getElementById('history-list');
    if (!results.length) { el.innerHTML = '<div class="empty">No results yet. Run a benchmark.</div>'; return; }
    el.innerHTML = renderResults(results.reverse());
    markClampedResponses(el);
  } catch (e) { document.getElementById('history-list').innerHTML = '<div class="empty">Error</div>'; }
}

window.addEventListener('hashchange', function() {
  var id = scenarioIdFromHash();
  if (id) markActiveScenario(id);
});

loadScenarios();
EOS.on('model-bench:completed', function() {
  if (document.getElementById('tab-ranking').classList.contains('active')) loadRanking();
  if (document.getElementById('tab-history').classList.contains('active')) loadHistory();
});
EOS.on('model-bench:agent_run_completed', function() {
  if (document.getElementById('tab-agent').classList.contains('active')) {
    loadAgentResultsGrid();
    loadAgentLeaderboard();
  }
});

// ── Agent-bench tab ────────────────────────────────────────────────

// Selected subjects state — persisted in sessionStorage so the pick
// survives tab switches.
var _agentLatest = {};   // { "scenarioId::subjectId": result }

function getSelectedSubjects() {
  try {
    var raw = sessionStorage.getItem('agent-bench-selected-subjects');
    if (raw) return JSON.parse(raw);
  } catch (e) {}
  // Default: only the available ones
  return _agentSubjects.filter(function(s) { return s.available; }).map(function(s) { return s.id; });
}

function setSelectedSubjects(ids) {
  try { sessionStorage.setItem('agent-bench-selected-subjects', JSON.stringify(ids)); } catch (e) {}
}

function toggleSubject(id) {
  var s = (_agentSubjects.find(function(x) { return x.id === id; }) || {});
  if (!s.available) return;
  var sel = getSelectedSubjects();
  var i = sel.indexOf(id);
  if (i >= 0) sel.splice(i, 1); else sel.push(id);
  setSelectedSubjects(sel);
  renderAgentSubjects();
  renderAgentScenarios();  // inline results filter by selection too
}

async function loadAgent() {
  await Promise.all([loadAgentSubjects(), loadAgentScenarios()]);
  await loadAgentLatestIndex();
  renderAgentScenarios();
  renderAgentResultsGrid();
  loadAgentLeaderboard();
}

async function loadAgentSubjects() {
  try {
    var r = await fetch('/model-bench/api/agent-subjects');
    _agentSubjects = await r.json();
  } catch (e) { _agentSubjects = []; }
  // Initialize selection if empty
  var sel = getSelectedSubjects();
  if (!sel.length) {
    setSelectedSubjects(_agentSubjects.filter(function(s) { return s.available; }).map(function(s) { return s.id; }));
  }
  renderAgentSubjects();
}

function renderAgentSubjects() {
  var host = document.getElementById('agent-subjects');
  if (!host) return;
  if (!_agentSubjects.length) { host.innerHTML = '<span class="empty" style="padding:0">(no subjects)</span>'; return; }
  var sel = getSelectedSubjects();
  host.innerHTML = _agentSubjects.map(function(s) {
    var classes = ['subject-chip', s.available ? 'available' : 'unavailable'];
    if (sel.indexOf(s.id) >= 0) classes.push('selected');
    var modelSuffix = s.model ? ' <span style="color:var(--text-muted);font-size:10px">· ' + EOS_UI.esc(s.model) + '</span>' : '';
    var title = s.available
      ? ('Click to toggle' + (s.model ? ' — model: ' + s.model : ''))
      : ('Unavailable: ' + s.reason);
    var safe = EOS_UI.esc(s.id);
    var onclick = s.available ? (' onclick="toggleSubject(\'' + safe + '\')"') : '';
    return '<span class="' + classes.join(' ') + '"' + onclick + ' title="' + EOS_UI.esc(title) + '"><span class="dot"></span>' + safe + modelSuffix + '</span>';
  }).join('');
}

async function loadAgentScenarios() {
  try {
    var r = await fetch('/model-bench/api/agent-scenarios');
    _agentScenarios = await r.json();
  } catch (e) { _agentScenarios = []; }
}

async function loadAgentLatestIndex() {
  _agentLatest = {};
  try {
    var r = await fetch('/model-bench/api/agent-results?limit=500');
    var results = await r.json();
    (results || []).forEach(function(row) {
      var key = row.scenario_id + '::' + row.subject_id;
      if (!_agentLatest[key] || _agentLatest[key].timestamp < row.timestamp) _agentLatest[key] = row;
    });
  } catch (e) {}
}

function renderAgentScenarios() {
  var host = document.getElementById('agent-scenarios-wrap');
  if (!host) return;
  if (!_agentScenarios.length) { host.innerHTML = '<div class="empty" style="padding:14px">No scenarios registered.</div>'; return; }
  var sel = getSelectedSubjects();
  // Always show all subjects in the inline strip — selection only affects which ones get *run*.
  // Subjects that are currently selected get a subtle highlight so the user sees what
  // "Run with selected" will target.
  var allSubjects = _agentSubjects.length ? _agentSubjects.map(function(s) { return s.id; })
                                          : ['claude-external','eos+claude','eos+openai','eos+ollama'];
  host.innerHTML = _agentScenarios.map(function(s) {
    var tagHtml = (s.tags || []).map(function(t) { return '<span class="tag">' + EOS_UI.esc(t) + '</span>'; }).join('');
    var inline = allSubjects.map(function(subj) {
      var row = _agentLatest[s.id + '::' + subj];
      var isSelected = sel.indexOf(subj) >= 0;
      var cls, badge, meta;
      if (!row) { cls = 'none'; badge = '&mdash;'; meta = 'no run'; }
      else if (row.ok) { cls = 'ok'; badge = '&check;'; meta = (row.tool_calls || 0) + 't · ' + (Math.round((row.wall_ms || 0) / 100) / 10) + 's'; }
      else { cls = 'fail'; badge = '&times;'; meta = (row.tool_calls || 0) + 't · ' + (Math.round((row.wall_ms || 0) / 100) / 10) + 's'; }
      var onclick = row ? ' onclick="showAgentTranscript(\'' + EOS_UI.esc(row.run_id) + '\')"' : '';
      var title = row ? EOS_UI.esc(row.notes || '') : 'Never run on this subject';
      var wrap = isSelected ? ' in-selection' : ' off-selection';
      return '<div class="inline-result ' + cls + wrap + '" title="' + title + '"' + onclick + '>' +
        '<span class="subj-lbl">' + EOS_UI.esc(subj) + '</span>' +
        '<span class="badge">' + badge + '</span>' +
        '<span class="meta">' + meta + '</span>' +
        '</div>';
    }).join('');

    var selCount = sel.length;
    var runBtnLabel = selCount
      ? 'Run with selected (' + selCount + ')'
      : 'Select a subject above to run';
    var runBtnDisabled = selCount ? '' : ' disabled';
    var safeId = EOS_UI.esc(s.id);
    return (
      '<div class="agent-card" id="scn-' + safeId + '">' +
        '<div class="card-head">' +
          '<span class="title">' + safeId + '</span>' +
          '<span class="floor" title="Minimum tool calls a competent agent needs">floor ' + s.expected_tool_floor + '</span>' +
          '<div class="tags" style="margin-left:auto">' + tagHtml + '</div>' +
        '</div>' +
        '<div class="desc">' + EOS_UI.esc(s.description) + '</div>' +
        '<div class="task-preview" title="' + EOS_UI.esc(s.task_preview) + '">' + EOS_UI.esc(s.task_preview) + '</div>' +
        '<div class="inline-results">' + inline + '</div>' +
        '<div class="actions">' +
          '<button class="eos-btn eos-btn-xs eos-btn-primary"' + runBtnDisabled + ' onclick="runAgentScenario(\'' + safeId + '\', null)">' + runBtnLabel + '</button>' +
        '</div>' +
      '</div>'
    );
  }).join('');
}

async function runAgentScenario(scenarioId, subjectIds) {
  var sel = subjectIds || getSelectedSubjects();
  if (!sel.length) { EOS_UI.toast('Pick at least one subject above.'); return; }
  var status = document.getElementById('agent-run-status');
  status.className = 'agent-run-status running';
  status.textContent = 'Running ' + scenarioId + ' × ' + sel.join(', ') + '...';
  try {
    var r = await fetch('/model-bench/api/agent-run', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        scenario_id: scenarioId, subject_ids: sel,
        variant_id: _agentVariantId || '',
        reps: _agentReps || 1,
        apply_overlay: _agentApplyOverlay !== false,
      }),
    });
    var data = await r.json();
    if (data.error) {
      status.className = 'agent-run-status error';
      status.textContent = 'Error: ' + data.error;
      return;
    }
    var nOK = (data.results || []).filter(function(x) { return x.ok; }).length;
    status.className = 'agent-run-status';
    var variantTag = data.variant_id ? ' · variant=' + data.variant_id : '';
    status.textContent = 'Done: ' + nOK + '/' + data.results.length + ' passed. ' +
      '(' + (data.run_group_id || '?') + variantTag + ')';
    await loadAgentLatestIndex();
    renderAgentScenarios();
    renderAgentResultsGrid();
    await loadAgentLeaderboard();
  } catch (e) {
    status.className = 'agent-run-status error';
    status.textContent = 'Request failed: ' + String(e);
  }
}

function _newClientGroupId() {
  // Client-side mint, so every POST in the batch carries the same id.
  // Server also generates one if omitted, but threading it here means the
  // UI can show "this batch produced N runs" before waiting on the last POST.
  var ts = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14);
  var rnd = Math.random().toString(36).slice(2, 8);
  return 'grp_' + ts + '_' + rnd;
}

async function runAllAgentScenarios() {
  var sel = getSelectedSubjects();
  if (!sel.length) { EOS_UI.toast('Pick at least one subject above.'); return; }
  if (!_agentScenarios.length) { EOS_UI.toast('No scenarios loaded.'); return; }
  var btn = document.getElementById('agent-run-all-btn');
  btn.disabled = true;
  var status = document.getElementById('agent-run-status');
  var groupId = _newClientGroupId();
  var variantId = _agentVariantId || '';
  for (var i = 0; i < _agentScenarios.length; i++) {
    var s = _agentScenarios[i];
    status.className = 'agent-run-status running';
    status.textContent = '[' + (i + 1) + '/' + _agentScenarios.length + '] ' + groupId + ' — ' + s.id + ' × ' + sel.join(', ') + '...';
    try {
      await fetch('/model-bench/api/agent-run', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          scenario_id: s.id, subject_ids: sel,
          run_group_id: groupId, variant_id: variantId,
          reps: _agentReps || 1,
          apply_overlay: _agentApplyOverlay !== false,
        }),
      });
    } catch (e) {}
    await loadAgentLatestIndex();
    renderAgentScenarios();
    renderAgentResultsGrid();
  }
  btn.disabled = false;
  status.className = 'agent-run-status';
  status.textContent = 'Batch ' + groupId + ' complete.';
  await loadAgentLeaderboard();
}

var _agentVariantId = '';   // surfaces in a tiny UI input; blank = baseline
var _agentReps = 1;         // >=2 exposes stochastic variance
var _agentApplyOverlay = true;  // provider-tier prompt overlay, default on

function renderAgentResultsGrid() {
  var host = document.getElementById('agent-results-grid');
  if (!host) return;
  if (!Object.keys(_agentLatest).length) {
    host.innerHTML = '<div class="empty" style="padding:8px 0">No runs yet.</div>';
    return;
  }
  var subjects = _agentSubjects.map(function(s) { return s.id; });
  var scenarioIds = _agentScenarios.map(function(s) { return s.id; });
  if (!scenarioIds.length) {
    var seen = {};
    Object.keys(_agentLatest).forEach(function(k) {
      var sid = k.split('::')[0]; if (!seen[sid]) { seen[sid] = 1; scenarioIds.push(sid); }
    });
  }
  // Build column headers: subject_id on top line, model on second line.
  // Model shown is the LATEST model we've actually seen for each subject
  // across any scenario — matches the subject-chip metadata. Falls back
  // to the agent-subjects probe if no run data yet.
  var modelBySubject = {};
  _agentSubjects.forEach(function(s) { if (s.model) modelBySubject[s.id] = s.model; });
  Object.values(_agentLatest).forEach(function(r) {
    if (r.subject_model) modelBySubject[r.subject_id] = r.subject_model;
  });
  var html = '<table><thead><tr><th class="scen">Scenario</th>';
  subjects.forEach(function(s) {
    var model = modelBySubject[s] || '';
    var sub = model ? '<div style="font-size:9px;color:var(--text-muted);font-weight:400;margin-top:2px">' + EOS_UI.esc(model) + '</div>' : '';
    html += '<th>' + EOS_UI.esc(s) + sub + '</th>';
  });
  html += '</tr></thead><tbody>';
  scenarioIds.forEach(function(sid) {
    html += '<tr><td class="scen">' + EOS_UI.esc(sid) + '</td>';
    subjects.forEach(function(subj) {
      var row = _agentLatest[sid + '::' + subj];
      if (!row) { html += '<td class="cell empty">&mdash;</td>'; return; }
      var cls = row.ok ? 'ok' : 'fail';
      var mark = row.ok ? '&check;' : '&times;';
      var secs = Math.round((row.wall_ms || 0) / 100) / 10;
      var cell = mark + ' ' + (row.tool_calls || 0) + 't · ' + secs + 's';
      // Title shows the model that actually ran — useful when subjects change models
      var title = (row.notes || '') + (row.subject_model ? ' · model=' + row.subject_model : '');
      html += '<td class="cell ' + cls + '" title="' + EOS_UI.esc(title) + '" onclick="showAgentTranscript(\'' + EOS_UI.esc(row.run_id) + '\')">' + cell + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  host.innerHTML = html;
}

async function loadAgentLeaderboard() {
  var host = document.getElementById('agent-leaderboard');
  if (!host) return;
  try {
    var r = await fetch('/model-bench/api/agent-leaderboard');
    var rows = await r.json();
    var withRuns = (rows || []).filter(function(r) { return r.runs > 0; });
    if (!withRuns.length) {
      host.innerHTML = '<div class="empty" style="padding:4px 0">No runs yet.</div>'; return;
    }
    // Rank: ok_rate DESC, avg_wall ASC
    withRuns.sort(function(a, b) {
      if (b.ok_rate !== a.ok_rate) return b.ok_rate - a.ok_rate;
      return (a.avg_wall_ms || 0) - (b.avg_wall_ms || 0);
    });
    var html = '<table class="agent-leaderboard-table"><thead><tr>' +
      '<th>Subject</th><th class="num">N</th><th class="num">OK%</th>' +
      '<th class="num">tools</th><th class="num">wall</th>' +
      '<th class="num" title="Total USD across all runs (passes + fails)">$ total</th>' +
      '<th class="num" title="Average USD per passing run — main cost metric">$/pass</th>' +
      '</tr></thead><tbody>';
    withRuns.forEach(function(r, i) {
      var pct = Math.round((r.ok_rate || 0) * 100);
      var rowClass = (i === 0 && r.ok_rate > 0) ? ' class="winner"' : '';
      var totalCost = r.total_cost_usd || 0;
      var perPass = r.avg_cost_per_pass_usd || 0;
      html += '<tr' + rowClass + '>' +
        '<td class="subj">' + EOS_UI.esc(r.subject_id) + '</td>' +
        '<td class="num">' + r.runs + '</td>' +
        '<td class="num">' + pct + '%</td>' +
        '<td class="num">' + (r.avg_tool_calls || 0) + '</td>' +
        '<td class="num">' + (Math.round((r.avg_wall_ms || 0) / 100) / 10) + 's</td>' +
        '<td class="num">' + (totalCost === 0 ? 'free' : '$' + totalCost.toFixed(4)) + '</td>' +
        '<td class="num">' + (perPass === 0 ? 'free' : '$' + perPass.toFixed(4)) + '</td>' +
      '</tr>';
    });
    html += '</tbody></table>';
    host.innerHTML = html;
  } catch (e) {
    host.innerHTML = '<div class="empty" style="padding:4px 0">Error loading leaderboard</div>';
  }
}

async function showAgentTranscript(runId) {
  try {
    // Fetch transcript and the run summary row (for histogram/error stats)
    var [tResp, aResp] = await Promise.all([
      fetch('/model-bench/api/agent-results/' + encodeURIComponent(runId) + '/transcript'),
      fetch('/model-bench/api/agent-results?limit=500'),
    ]);
    var data = await tResp.json();
    if (data.error) { EOS_UI.toast(data.error); return; }
    var allResults = await aResp.json();
    var row = (allResults || []).find(function(r) { return r.run_id === runId; }) || {};
    var events = data.events || [];
    var evHtml = events.map(function(e) { return formatTranscriptEvent(e); }).join('');
    if (!evHtml) evHtml = '<div class="empty" style="padding:8px">Empty transcript</div>';

    var header = renderTranscriptHeader(row);
    EOS_UI.modal({
      title: runId,
      body: header + '<div style="max-height:60vh;overflow-y:auto">' + evHtml + '</div>',
    });
  } catch (e) { EOS_UI.toast('Transcript failed: ' + e); }
}

function renderTranscriptHeader(r) {
  if (!r || !r.run_id) return '';
  function pill(label, value, tone) {
    if (value === undefined || value === null || value === '') return '';
    var bg = tone === 'ok' ? 'color-mix(in srgb,var(--accent) 14%,transparent)'
           : tone === 'fail' ? 'color-mix(in srgb,#ef4444 14%,transparent)'
           : 'var(--bg)';
    return '<span style="display:inline-block;padding:2px 8px;margin:2px 4px 2px 0;background:' + bg +
           ';border:1px solid var(--border);border-radius:4px;font-family:var(--mono);font-size:11px">' +
           '<span style="color:var(--text-muted)">' + EOS_UI.esc(label) + ':</span> ' +
           EOS_UI.esc(String(value)) + '</span>';
  }
  var hist = r.tool_histogram || {};
  var errs = r.error_categories || {};
  var histHtml = Object.keys(hist).map(function(k) {
    return pill(k, hist[k]);
  }).join('');
  var errsHtml = Object.keys(errs).map(function(k) {
    return pill(k, errs[k], 'fail');
  }).join('');

  return (
    '<div style="margin-bottom:12px;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:8px">' +
      '<div style="margin-bottom:6px">' +
        pill('subject', r.subject_id) +
        pill('model', r.subject_model || '(unknown)') +
        pill(r.ok ? 'passed' : 'failed', (r.tool_calls || 0) + ' tools · ' + (Math.round((r.wall_ms || 0)/100)/10) + 's',
             r.ok ? 'ok' : 'fail') +
        pill('cost', (r.cost_usd && r.cost_usd > 0) ? ('$' + r.cost_usd.toFixed(6)) : 'free') +
        pill('rep', (r.rep_index || 0).toString()) +
        pill('group', r.run_group_id || '—') +
        pill('variant', r.variant_id || 'baseline') +
        pill('overlay', r.overlay_applied ? 'on' : 'off') +
        pill('sha', (r.eos_git_sha || '').slice(0, 10) || '—') +
        pill('prompt#', r.system_prompt_hash || '—') +
      '</div>' +
      (histHtml ? '<div style="margin-bottom:4px;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px">TOOL HISTOGRAM</div><div>' + histHtml + '</div>' : '') +
      (errsHtml ? '<div style="margin:6px 0 4px 0;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px">ERROR CATEGORIES</div><div>' + errsHtml + '</div>' : '') +
      (r.notes ? '<div style="margin-top:6px;font-size:12px;color:var(--text-secondary)">' + EOS_UI.esc(r.notes) + '</div>' : '') +
    '</div>'
  );
}

function formatTranscriptEvent(e) {
  var t = e.type || '';
  if (t === 'agent:text') {
    var delta = (e.delta || '').trim();
    if (!delta) return '';
    return '<div class="transcript-evt text-block">' + EOS_UI.esc(delta) + '</div>';
  }
  if (t === 'agent:tool_call') {
    var inp = e.input ? JSON.stringify(e.input) : '{}';
    if (inp.length > 280) inp = inp.slice(0, 277) + '...';
    return '<div class="transcript-evt">' +
      '<span class="etype">▶ ' + EOS_UI.esc(e.name || '?') + '</span>' +
      '<span class="payload">' + EOS_UI.esc(inp) + '</span>' +
      '</div>';
  }
  if (t === 'agent:tool_result') {
    var d = e.display || {};
    var cls = e.is_error ? 'err' : '';
    var mark = e.is_error ? '✗' : '✓';
    var name = d.name || '?';
    var extra = Object.keys(d).filter(function(k) { return k !== 'name'; }).map(function(k) { return k + '=' + JSON.stringify(d[k]); }).join(' ');
    if (extra.length > 200) extra = extra.slice(0, 197) + '...';
    return '<div class="transcript-evt">' +
      '<span class="etype ' + cls + '">' + mark + ' ' + EOS_UI.esc(name) + '</span>' +
      '<span class="payload">' + EOS_UI.esc(extra) + '</span>' +
      '</div>';
  }
  if (t === 'agent:done' || t === 'agent:max_iters' || t === 'agent:cancelled' || t === 'agent:error') {
    var payload = JSON.stringify({stop_reason: e.stop_reason, usage: e.usage, error: e.error});
    if (payload.length > 200) payload = payload.slice(0, 197) + '...';
    return '<div class="transcript-evt">' +
      '<span class="etype">' + EOS_UI.esc(t) + '</span>' +
      '<span class="payload">' + EOS_UI.esc(payload) + '</span>' +
      '</div>';
  }
  // agent:turn_start, agent:iter_start — show sparingly
  return '';
}