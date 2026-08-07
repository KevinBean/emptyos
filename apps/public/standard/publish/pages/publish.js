// publish -- page logic, extracted verbatim from pages/index.html (P4 Atomic
// split, .claude/rules/multi-module-apps.md frontend pattern). Loaded at the
// same position as the old inline <script>, so global scope and load order
// vs eos.js / eos-components.js are unchanged.
var posts = [];
var drafts = [];
var currentFilter = 'published';
var config = {};
var podcastStatus = {};
var coverStatus = {};
var allSites = [];
var activeSiteId = 'default';

var _THEMES = [];
function applyThemeOptions(list) {
  _THEMES = (list && list.length) ? list : _THEMES;
  var sel = document.getElementById('sf-theme');
  if (sel && _THEMES.length) {
    sel.innerHTML = _THEMES.map(function(t) {
      return '<option value="' + escAttr(t.id) + '">' + esc(t.label || t.id) + '</option>';
    }).join('');
  }
}
function themeIdOptions() {
  // _THEMES comes from /publish/api/themes, which enumerates theme.css plus any
  // design-system KB notes. The fallback is only reached if that call failed, so
  // it stays deliberately minimal: a hardcoded list here was frozen at five ids
  // for long enough to hide digital-garden and everything after it, and a stale
  // list is worse than an obviously-degraded one — it looks authoritative.
  return _THEMES.length ? _THEMES.map(function(t){ return t.id; }) : ['eos'];
}

async function init() {
  try {
    var [sitesData, cfg, podStatus, covStatus, themesData] = await Promise.all([
      EOS.api('/publish/api/sites'),
      EOS.api('/publish/api/config'),
      EOS.api('/publish/api/podcast-status').catch(function() { return {podcasts:{}}; }),
      EOS.api('/publish/api/cover-status').catch(function() { return {covers:{}}; }),
      EOS.api('/publish/api/themes').catch(function() { return {themes:[]}; })
    ]);
    applyThemeOptions((themesData && themesData.themes) || []);

    allSites = (sitesData && sitesData.sites) || [];
    activeSiteId = (sitesData && sitesData.active) || 'default';
    podcastStatus = (podStatus && podStatus.podcasts) || {};
    coverStatus = (covStatus && covStatus.covers) || {};
    config = cfg || {};
    window.FRAMEWORK_EVAL_ON = !!config.framework_eval;  // dark flag → show Evaluate affordance
    posts = config.sources || [];
    drafts = config.drafts || [];

    renderSiteTabs();

    // Update header with mode
    var mode = config.site_mode || 'blog';
    var modeLabel = mode === 'project' ? 'Project Site' : 'Blog';
    document.getElementById('source-info').innerHTML =
      esc(config.source_folder || '?') + ' &bull; ' + esc(config.config?.site_name || 'My Site')
      + ' <span class="site-mode ' + mode + '">' + modeLabel + '</span>';

    // Stats — show pages + posts breakdown
    document.getElementById('s-posts').textContent = (config.page_count || 0) + (config.post_count || 0);
    document.getElementById('s-posts-lbl').textContent =
      (config.page_count || 0) + ' pages, ' + (config.post_count || 0) + ' posts';
    var allTags = new Set();
    posts.forEach(function(p) { (p.tags || []).forEach(function(t) { allTags.add(t); }); });
    document.getElementById('s-tags').textContent = allTags.size;

    var lb = config.last_build;
    document.getElementById('s-built').textContent = lb ? timeAgo(lb) : 'Never';

    var ld = config.last_deploy;
    document.getElementById('s-deployed').textContent = ld ? timeAgo(ld) : 'Never';
    // Show which platform this site was last deployed to, when known.
    var dt = config.deployed_target;
    document.getElementById('s-deployed-lbl').textContent =
      (ld && dt) ? ('Deployed · ' + (dt === 'firebase' ? 'Firebase' : 'GitHub')) : 'Deployed';

    // Enable preview + deploy if we have a built site
    if (config.last_build_stats && (config.last_build_stats.pages > 0 || config.last_build_stats.posts > 0)) {
      document.getElementById('btn-preview-site').disabled = false;
      document.getElementById('btn-deploy').disabled = false;
    }

    // Reflect the configured deploy target on the single Deploy button
    updateDeployButton();

    // Show visit button if site has been deployed
    updateVisitButton();

    // Show Chatbot Q&A button only when chatbot is enabled for this site.
    var activeSite = allSites.find(function(s) { return s.id === activeSiteId; });
    var cbEnabled = !!(activeSite && activeSite.chatbot && activeSite.chatbot.enabled);
    document.getElementById('btn-chatbot-qa').style.display = cbEnabled ? '' : 'none';

    renderPosts();
  } catch(e) {
    document.getElementById('source-info').textContent = 'Error loading: ' + e.message;
  }
}

function renderSiteTabs() {
  var el = document.getElementById('site-tabs');
  var mode = config.site_mode || 'blog';
  var html = allSites.map(function(s) {
    var isActive = s.id === activeSiteId;
    var cls = isActive ? 'site-tab active' : 'site-tab';
    var badge = isActive ? ' <span class="site-mode ' + mode + '">' + (mode === 'project' ? 'Project' : 'Blog') + '</span>' : '';
    return '<button class="' + cls + '" onclick="switchSite(' + escAttr(JSON.stringify(s.id)) + ')"'
      + ' oncontextmenu="event.preventDefault();siteContextMenu(event,' + escAttr(JSON.stringify(s.id)) + ')">'
      + esc(s.name || s.id) + badge + '</button>';
  }).join('');
  html += '<button class="site-tab-add" onclick="addSite()" title="Add site">+</button>';
  el.innerHTML = html;
}

async function switchSite(siteId) {
  if (siteId === activeSiteId) return;
  await EOS.post('/publish/api/sites/activate', { site_id: siteId });
  init();
}

async function addSite() {
  EOS_UI.formModal('New Site', [
    { key: 'name', label: 'Site Name', type: 'text', placeholder: 'My Blog' },
    { key: 'source_folder', label: 'Source Folder', type: 'text', placeholder: '30_Resources/Published' },
    { key: 'site_name', label: 'Title (shown on site)', type: 'text', placeholder: 'My Blog' },
    { key: 'theme', label: 'Theme', type: 'select', options: themeIdOptions(), value: 'eos' },
  ], async function(data) {
    var result = await EOS.post('/publish/api/sites', data);
    if (result.error) { EOS_UI.toast(result.error, false); return; }
    await EOS.post('/publish/api/sites/activate', { site_id: result.site.id });
    EOS_UI.toast('Site created: ' + data.name, true);
    init();
  });
}

function siteContextMenu(event, siteId) {
  // Right-click: delete option (only if more than 1 site)
  if (allSites.length <= 1) return;
  var site = allSites.find(function(s) { return s.id === siteId; });
  EOS_UI.confirm({message: 'Delete site "' + (site ? site.name : siteId) + '"?', action: 'Delete', danger: true}, async function() {
    await fetch('/publish/api/sites/' + encodeURIComponent(siteId), { method: 'DELETE' });
    EOS_UI.toast('Site deleted', true);
    init();
  });
}

var _filterBar = null;
function renderFilterPills() {
  var cats = [
    { key: 'published', label: 'Published', count: posts.length },
    { key: 'drafts', label: 'Drafts', count: drafts.length },
    { key: 'all', label: 'All', count: posts.length + drafts.length }
  ];
  if (!_filterBar) {
    _filterBar = EOS_UI.filterBar('filter-pills', cats, function(key) {
      currentFilter = key;
      renderPosts();
    }, { includeAll: false, initial: currentFilter });
  } else {
    _filterBar.update(cats);
    _filterBar.setActive(currentFilter);
  }
}

function setFilter(key) {
  currentFilter = key;
  if (_filterBar) _filterBar.setActive(key);
  renderPosts();
}

function visibleItems() {
  if (currentFilter === 'drafts') return drafts;
  if (currentFilter === 'all') return drafts.concat(posts);
  return posts;
}

function renderPosts() {
  renderFilterPills();
  var list = document.getElementById('post-list');
  var empty = document.getElementById('empty');
  var items = visibleItems();

  if (!items.length) {
    list.innerHTML = '';
    empty.style.display = 'block';
    if (currentFilter === 'drafts') {
      document.getElementById('empty-msg').textContent = 'No drafts';
      document.getElementById('empty-hint').innerHTML = 'Save a post with <code>publish: false</code> to keep it here until it\u2019s ready';
    } else {
      document.getElementById('empty-msg').textContent = 'No publishable notes found';
      document.getElementById('empty-hint').innerHTML = 'Create notes in your source folder with <code>publish: true</code> in frontmatter';
    }
    return;
  }
  empty.style.display = 'none';

  list.innerHTML = items.map(function(p) {
    var badges = [];
    if (p.draft) {
      badges.push({label: 'Draft', variant: 'status-draft'});
    } else if (p.type === 'page') {
      badges.push({label: p.layout === 'landing' ? 'Landing' : 'Page', variant: 'neutral'});
    }
    (p.tags || []).forEach(function(t) { badges.push({label: t, variant: 'neutral'}); });

    var publishBtn = p.draft
      ? '<button class="pi-btn" onclick="publishDraft(' + escAttr(JSON.stringify(p.path.replace(/\\/g,'/'))) + ')" title="Publish now">&#10004;</button>'
      : '';

    var evalBtn = (p.draft && window.FRAMEWORK_EVAL_ON)
      ? '<button class="pi-btn" onclick="evaluateDraft(' + escAttr(JSON.stringify(p.path.replace(/\\/g,'/'))) + ')" title="Evaluate against this site’s branding framework">&#9878;</button>'
      : '';

    var actions =
        (p.draft ? '' : '<button class="pi-btn" onclick="previewPost(' + escAttr(JSON.stringify(p.slug)) + ')" title="Preview">&#128065;</button>')
      + '<a class="pi-btn" href="/publish/pages/writer.html?edit=' + encodeURIComponent(p.slug) + '" title="Edit">&#9998;</a>'
      + evalBtn
      + publishBtn
      + (p.type === 'post' && !p.draft ? coverButton(p.slug) : '')
      + (p.type === 'post' && !p.draft ? podcastButton(p.slug) : '')
      + (window.ASSET_STUDIO_ON ? '<button class="pi-btn" onclick="openAssets(' + escAttr(JSON.stringify((p.path || '').replace(/\\/g,'/').split('/').pop())) + ',' + escAttr(JSON.stringify(p.title)) + ')" title="Asset studio (diagrams + screenshots)">&#128202;</button>' : '')
      + '<button class="pi-btn" onclick="EOS.openInViewer(\'' + EOS.escPath(p.relative || p.path) + '\')" title="Open external">&#8599;</button>';

    return EOS_UI.entityCard({
      title: p.title,
      badges: badges,
      body: p.summary ? '<div class="pi-summary">' + esc(p.summary) + '</div>' : undefined,
      meta: '<span>' + esc(p.date) + ' \u2022 ' + p.reading_time + ' min read</span>',
      actions: actions,
      className: p.draft ? 'is-draft' : '',
    });
  }).join('');
}

function publishDraft(path) {
  EOS_UI.confirm('Publish this draft? It will appear on the site after the next build.', async function() {
    try {
      var r = await EOS.post('/publish/api/toggle-publish', { path: path, publish: true });
      if (r && r.error) { EOS_UI.toast(r.error, false); return; }
      EOS_UI.toast('Published \u2014 run Build to update site', true);
      init();
    } catch(e) {
      EOS_UI.toast('Failed: ' + e.message, false);
    }
  });
}

// ── Branding-framework draft evaluator (dark flag: config.framework_eval) ──
function _scoreChip(n) {
  if (n === null || n === undefined) return '<span style="color:var(--text-muted)">–</span>';
  var c = n <= 2 ? 'var(--danger)' : (n === 3 ? 'var(--warning,#c90)' : 'var(--success)');
  return '<span style="display:inline-block;min-width:34px;text-align:center;padding:1px 6px;border-radius:6px;font-weight:600;color:#fff;background:' + c + '">' + n + '/5</span>';
}
function _verdictChip(v) {
  var map = {'ready': ['Ready', 'var(--success)'], 'needs-polish': ['Needs polish', 'var(--warning,#c90)'], 'off-brand': ['Off-brand', 'var(--danger)']};
  var m = map[v] || [v || '?', 'var(--text-muted)'];
  return '<span style="display:inline-block;padding:2px 10px;border-radius:8px;font-weight:600;color:#fff;background:' + m[1] + '">' + esc(m[0]) + '</span>';
}
async function evaluateDraft(path) {
  EOS_UI.toast('Evaluating against branding framework…', true);
  try {
    var card = await EOS.post('/publish/api/evaluate', { path: path, site: activeSiteId });
    if (!card || card.ok === false) { EOS_UI.toast((card && card.error) || 'Evaluation failed', false); return; }
    renderScorecard(card);
  } catch (e) { EOS_UI.toast('Failed: ' + e.message, false); }
}
function renderScorecard(card) {
  var rows = (card.dimensions || []).map(function(d) {
    return '<tr><td style="padding:6px 10px 6px 0;vertical-align:top">' + esc(d.name) + '</td>'
      + '<td style="padding:6px 10px 6px 0;vertical-align:top;white-space:nowrap">' + _scoreChip(d.score) + '</td>'
      + '<td style="padding:6px 0;color:var(--text-secondary)">' + esc(d.notes || '') + '</td></tr>';
  }).join('');
  var guard = (card.guardrail_hits || []).map(function(g) {
    var c = g.severity === 'high' ? 'var(--danger)' : (g.severity === 'medium' ? 'var(--warning,#c90)' : 'var(--text-muted)');
    return '<li style="color:' + c + '">[' + esc(g.kind) + '] ' + esc(g.detail) + '</li>';
  }).join('');
  var fixes = (card.fixes || []).map(function(f) { return '<li>' + esc(f) + '</li>'; }).join('');
  var prov = card.provenance ? EOS_UI.provenance(card.provenance) : '';
  var body = ''
    + '<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap">'
    +   _verdictChip(card.verdict)
    +   '<span style="font-size:15px">Overall ' + _scoreChip(card.overall) + '</span>'
    +   (card.framework_present === false ? '<span style="color:var(--warning,#c90);font-size:12px">⚠ no _framework.md — scored on defaults</span>' : '')
    +   '<span style="margin-left:auto">' + prov + '</span>'
    + '</div>'
    + '<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:14px">' + rows + '</table>'
    + (guard ? '<div style="margin-bottom:12px"><strong>Guardrail hits</strong><ul style="margin:6px 0 0;padding-left:20px;font-size:13px">' + guard + '</ul></div>' : '')
    + (fixes ? '<div><strong>Top fixes</strong><ol style="margin:6px 0 0;padding-left:20px;font-size:13px;color:var(--text-secondary)">' + fixes + '</ol></div>' : '')
    + (card.error ? '<div style="color:var(--danger);margin-top:10px;font-size:12px">' + esc(card.error) + '</div>' : '');
  EOS_UI.modal({ title: 'Branding scorecard · ' + esc(card.site || activeSiteId), body: body, width: 640 });
}
async function refreshFrameworkPanel(siteId) {
  var grp = document.getElementById('sf-framework-group');
  var st = document.getElementById('sf-framework-state');
  var acts = document.getElementById('sf-framework-actions');
  if (!grp) return;
  if (!window.FRAMEWORK_EVAL_ON) { grp.style.display = 'none'; return; }
  grp.style.display = '';
  st.textContent = '…'; acts.innerHTML = '';
  try {
    var r = await EOS.api('/publish/api/framework/' + encodeURIComponent(siteId));
    if (!r || r.ok === false) { st.textContent = '(unavailable)'; return; }
    if (r.present) {
      st.innerHTML = '<span style="color:var(--success)">● configured</span> · ' + (r.dimensions || []).length + ' dimensions';
      acts.innerHTML = '<button class="pi-btn" onclick="EOS.openInViewer(\'' + EOS.escPath(r.path) + '\')">Open framework note</button>';
    } else {
      st.innerHTML = '<span style="color:var(--text-muted)">not configured</span>';
      acts.innerHTML = '<button class="pi-btn" onclick="seedFramework(' + escAttr(JSON.stringify(siteId)) + ')">Create from template</button>';
    }
  } catch (e) { st.textContent = '(error)'; }
}
async function seedFramework(siteId) {
  try {
    var r = await EOS.post('/publish/api/framework/' + encodeURIComponent(siteId) + '/seed', {});
    if (!r || r.ok === false) { EOS_UI.toast((r && r.error) || 'Could not create', false); return; }
    EOS_UI.toast('Framework note created', true);
    refreshFrameworkPanel(siteId);
  } catch (e) { EOS_UI.toast('Failed: ' + e.message, false); }
}

async function previewPost(slug) {
  document.getElementById('preview-title').textContent = 'Loading...';
  document.getElementById('preview-body').innerHTML = '<div style="color:var(--text-muted)">Rendering...</div>';
  document.getElementById('overlay').classList.add('open');
  document.getElementById('preview-panel').classList.add('open');

  try {
    var data = await EOS.api('/publish/api/preview?slug=' + encodeURIComponent(slug));
    if (data.error) {
      document.getElementById('preview-body').innerHTML = '<div style="color:var(--danger)">' + esc(data.error) + '</div>';
      return;
    }
    document.getElementById('preview-title').textContent = data.title;
    var tagsHtml = (data.tags || []).map(function(t) { return '<span class="eos-badge eos-badge-neutral">' + esc(t) + '</span>'; }).join(' ');
    document.getElementById('preview-body').innerHTML =
      '<div style="color:var(--text-muted);font-size:12px;margin-bottom:16px">' + esc(data.date) + ' ' + tagsHtml + '</div>'
      + '<div class="article">' + data.html + '</div>';
  } catch(e) {
    document.getElementById('preview-body').innerHTML = '<div style="color:var(--danger)">Preview failed: ' + esc(e.message) + '</div>';
  }
}

function closePreview() {
  document.getElementById('overlay').classList.remove('open');
  document.getElementById('preview-panel').classList.remove('open');
}

function closeAll() {
  closePreview();
  closeSettings();
}

async function doRasterizeDiagrams() {
  var status = await EOS.api('/publish/api/diagrams' + (activeSiteId ? '?site_id=' + encodeURIComponent(activeSiteId) : ''));
  if (status.error) { EOS_UI.toast(status.error, 'error'); return; }
  if (!status.stale_count) { EOS_UI.toast('All ' + status.diagrams.length + ' diagram pair(s) are fresh', 'success'); return; }
  var result = await EOS.post('/publish/api/diagrams/rasterize', {site_id: activeSiteId});
  if (result.error || result.skipped) { EOS_UI.toast(result.error || result.skipped, 'error'); return; }
  EOS_UI.toast('Re-rasterized ' + result.rasterized.length + ' diagram(s): ' + result.rasterized.join(', '), 'success');
}

async function doBuild() {
  var logEl = document.getElementById('log');
  logEl.style.display = 'block';
  logEl.textContent = 'Building site...\n';
  document.getElementById('toolbar-info').textContent = 'Building...';
  var job = EOS_UI.jobProgress({id: 'pub-build'});
  job.update({stage: 'Building site', detail: 'rendering pages'});

  try {
    var result = await EOS.post('/publish/api/build', {site_id: activeSiteId});
    if (result.error) {
      logEl.textContent += 'ERROR: ' + result.error + '\n';
      document.getElementById('toolbar-info').textContent = 'Build failed';
      job.hide();
      return;
    }
    if (result.diagrams_rasterized && result.diagrams_rasterized.length) {
      logEl.textContent += 'Re-rasterized ' + result.diagrams_rasterized.length + ' stale diagram(s): ' + result.diagrams_rasterized.join(', ') + '\n';
    }
    logEl.textContent += 'Built ' + result.pages + ' pages, ' + result.tags + ' tags, ' + result.images + ' images\n';
    logEl.textContent += 'Output: ' + result.output + '\n';
    document.getElementById('toolbar-info').textContent = 'Built ' + result.pages + ' pages';
    document.getElementById('s-built').textContent = 'Just now';
    document.getElementById('btn-preview-site').disabled = false;
    document.getElementById('btn-deploy').disabled = false;

    if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) {
      EOS_UI.toast('Site built: ' + result.pages + ' pages', true);
    }
    job.done({stage: 'Built', detail: result.pages + ' pages'});
  } catch(e) {
    logEl.textContent += 'ERROR: ' + e.message + '\n';
    document.getElementById('toolbar-info').textContent = 'Build failed';
    job.hide();
  }
}

// Which host the single Deploy button targets, from Site settings.
function deployTarget() {
  return (config.config && config.config.deploy_target) || 'github';
}

// Sync the Deploy button label/title with the configured target, and note
// where the site was last deployed (so you can see which platform it's on).
function updateDeployButton() {
  var btn = document.getElementById('btn-deploy');
  if (!btn) return;
  var target = deployTarget();
  var name = target === 'firebase' ? 'Firebase' : 'GitHub';
  btn.textContent = 'Deploy → ' + name;
  var deployedOn = config.deployed_target;
  var title = 'Deploy the built site to ' + name + ' (change in Site settings)';
  if (deployedOn) {
    title += '\nLast deployed on: ' + (deployedOn === 'firebase' ? 'Firebase' : 'GitHub');
  }
  btn.title = title;
}

async function doDeploy() {
  var target = deployTarget();
  var isFb = target === 'firebase';
  var name = isFb ? 'Firebase' : 'GitHub';

  // Guard: the target's required config must be set, else open settings.
  if (isFb && !config.config?.firebase_project) {
    openSettings();
    if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) EOS_UI.toast('Set a Firebase project ID first', false);
    return;
  }
  if (!isFb && !config.config?.repo) {
    openSettings();
    if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) EOS_UI.toast('Set a GitHub repo first', false);
    return;
  }

  var endpoint = isFb ? '/publish/api/deploy/firebase' : '/publish/api/deploy';
  var btn = document.getElementById('btn-deploy');
  var logEl = document.getElementById('log');
  logEl.style.display = 'block';
  logEl.textContent += '\nDeploying to ' + name + '...\n';
  document.getElementById('toolbar-info').textContent = 'Deploying to ' + name + '...';
  btn.disabled = true;
  var job = EOS_UI.jobProgress({id: 'pub-deploy'});
  job.update({stage: 'Deploying to ' + name, detail: isFb ? 'uploading' : 'pushing to host'});

  try {
    var result = await EOS.post(endpoint, {site_id: activeSiteId});
    if (result.error) {
      logEl.textContent += 'ERROR: ' + result.error + '\n';
      document.getElementById('toolbar-info').textContent = name + ' deploy failed';
      if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) EOS_UI.toast(result.error, false);
      btn.disabled = false;
      job.hide();
      return;
    }
    if (result.status === 'nothing_changed') {
      logEl.textContent += (result.message || 'Already up to date.') + '\n';
      document.getElementById('toolbar-info').textContent = 'Already up to date';
      btn.disabled = false;
      job.done({stage: 'Up to date', detail: 'nothing changed'});
      return;
    }
    logEl.textContent += 'Deployed! ' + result.url + '\n';
    document.getElementById('toolbar-info').innerHTML = '<a href="' + result.url + '" target="_blank" style="color:var(--accent)">' + result.url + '</a>';
    document.getElementById('s-deployed').textContent = 'Just now';
    btn.disabled = false;
    // Remember which platform we just deployed to so the button title reflects it.
    config.deployed_target = target;
    updateDeployButton();
    updateVisitButton(result.url);

    if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) {
      EOS_UI.toast('Deployed to ' + result.url, true);
    }
    job.done({stage: 'Deployed', detail: result.url || ''});
  } catch(e) {
    logEl.textContent += 'ERROR: ' + e.message + '\n';
    document.getElementById('toolbar-info').textContent = name + ' deploy failed';
    btn.disabled = false;
    job.hide();
  }
}

function timeAgo(isoStr) {
  var d = new Date(isoStr);
  var diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'Just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

// ── Live generation banner ────────────────────────────────────────────
// Shows active cover-image / podcast jobs as a sticky banner above the post
// list, with a spinner + running elapsed timer. Supports concurrent jobs.
// Replaces the old behaviour of appending status to the bottom-of-page log.
var genJobs = {};   // id -> { title, sub, startedAt, done, ok, timer }
var _genSeq = 0;

function _genFmt(j) {
  var s = Math.max(0, Math.floor((Date.now() - j.startedAt) / 1000));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

function _genRender() {
  var banner = document.getElementById('gen-banner');
  if (!banner) return;
  var ids = Object.keys(genJobs);
  if (!ids.length) { banner.className = 'gen-banner'; banner.innerHTML = ''; return; }
  banner.className = 'gen-banner active';
  banner.innerHTML = ids.map(function(id) {
    var j = genJobs[id];
    var cls = j.done ? (j.ok ? 'gen-job is-done' : 'gen-job is-error') : 'gen-job';
    var lead = j.done
      ? '<span class="gen-icon">' + (j.ok ? '&#10003;' : '&#10005;') + '</span>'
      : '<span class="gen-spinner"></span>';
    var right = j.done
      ? '<span class="gen-elapsed">' + _genFmt(j) + '</span>'
        + '<button class="gen-close" onclick="genDismiss(' + escAttr(JSON.stringify(id)) + ')" title="Dismiss">&times;</button>'
      : '<span class="gen-elapsed" id="gen-el-' + id + '">' + _genFmt(j) + '</span>';
    return '<div class="' + cls + '">' + lead
      + '<div class="gen-body"><div class="gen-title">' + esc(j.title) + '</div>'
      + (j.sub ? '<div class="gen-sub">' + esc(j.sub) + '</div>' : '') + '</div>'
      + right + '</div>';
  }).join('');
}

function genStart(opts) {
  var id = 'g' + (++_genSeq);
  var j = { title: opts.title, sub: opts.sub || '', startedAt: Date.now(), done: false, ok: false };
  genJobs[id] = j;
  j.timer = setInterval(function() {
    var el = document.getElementById('gen-el-' + id);
    if (el && genJobs[id] && !genJobs[id].done) el.textContent = _genFmt(genJobs[id]);
  }, 1000);
  _genRender();
  return id;
}

function genFinish(id, ok, message) {
  var j = genJobs[id];
  if (!j) return;
  if (j.timer) { clearInterval(j.timer); j.timer = null; }
  j.done = true; j.ok = ok;
  if (message) j.sub = message;
  _genRender();
  if (ok) setTimeout(function() { genDismiss(id); }, 8000);  // errors stay until dismissed
}

function genDismiss(id) {
  if (genJobs[id] && genJobs[id].timer) clearInterval(genJobs[id].timer);
  delete genJobs[id];
  _genRender();
}

function podcastButton(slug) {
  var has = podcastStatus[slug];
  if (has) {
    return '<button class="pi-btn" onclick="event.stopPropagation();viewPodcast(' + escAttr(JSON.stringify(slug)) + ')" title="View Podcast" style="color:var(--success)">&#127911;</button>'
      + '<button class="pi-btn" onclick="event.stopPropagation();generatePodcast(' + escAttr(JSON.stringify(slug)) + ')" title="Regenerate Podcast" style="font-size:10px">&#8635;</button>';
  }
  return '<button class="pi-btn" onclick="event.stopPropagation();generatePodcast(' + escAttr(JSON.stringify(slug)) + ')" title="Generate Podcast">&#127911;</button>';
}

function coverButton(slug) {
  var has = coverStatus[slug];
  if (has && has.embedded) {
    return '<button class="pi-btn" onclick="event.stopPropagation();viewCover(' + escAttr(JSON.stringify(slug)) + ')" title="View Cover (embedded)" style="color:var(--success)">&#127912;</button>'
      + '<button class="pi-btn" onclick="event.stopPropagation();generateCover(' + escAttr(JSON.stringify(slug)) + ')" title="Regenerate Cover" style="font-size:10px">&#8635;</button>';
  }
  if (has) {
    // Image exists but not yet approved — amber dot
    return '<button class="pi-btn" onclick="event.stopPropagation();previewCover(' + escAttr(JSON.stringify(slug)) + ')" title="Review pending cover" style="color:var(--warning)">&#127912;</button>';
  }
  return '<button class="pi-btn" onclick="event.stopPropagation();generateCover(' + escAttr(JSON.stringify(slug)) + ')" title="Generate Cover Image">&#127912;</button>';
}

function viewCover(slug) {
  var info = coverStatus[slug];
  if (!info) return;
  window.open('/publish/api/site-file?path=media/' + info.file, '_blank');
}

var lastCoverMeta = {};  // slug -> { summary, image_prompt }

async function generateCover(slug, opts) {
  opts = opts || {};
  var gid = genStart({
    title: 'Generating cover — ' + slug,
    sub: opts.rewrite_brief
      ? 'Rewriting visual brief via art-director agent…'
      : 'Rendering cover image (30–90s)…',
  });
  try {
    var result = await EOS.post('/publish/api/generate-cover', {
      slug: slug,
      rewrite_brief: !!opts.rewrite_brief,
    });
    if (result.error) {
      genFinish(gid, false, result.error);
      if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) EOS_UI.toast(result.error, false);
      return;
    }
    lastCoverMeta[slug] = {
      summary: result.summary || '',
      image_prompt: result.image_prompt || '',
    };
    genFinish(gid, true, 'Cover ready — review to approve or reject.');
    coverStatus[slug] = { file: result.cover_file, size_kb: 0, embedded: false };
    renderPosts();
    previewCover(slug);
  } catch(e) {
    genFinish(gid, false, e.message);
  }
}

function previewCover(slug) {
  var info = coverStatus[slug];
  if (!info) return;
  var src = '/publish/api/source-media?file=' + encodeURIComponent(info.file) + '&t=' + Date.now();
  var meta = lastCoverMeta[slug] || {};
  var metaHtml = '';
  if (meta.summary || meta.image_prompt) {
    metaHtml =
      '<details style="margin:10px 0 0;font-size:0.85rem;color:var(--text-muted)">' +
        '<summary style="cursor:pointer">Article metadata used (from frontmatter)</summary>' +
        '<div style="margin-top:6px;padding:8px;background:var(--bg-surface);border-radius:6px">' +
          (meta.summary ? '<div><strong>summary:</strong> ' + esc(meta.summary) + '</div>' : '') +
          (meta.image_prompt ? '<div style="margin-top:6px"><strong>image_prompt:</strong> ' + esc(meta.image_prompt) + '</div>' : '') +
          '<div style="margin-top:8px;font-size:0.8rem;opacity:0.7">Edit these fields in the note itself to guide future regenerations.</div>' +
        '</div>' +
      '</details>';
  }
  var body =
    '<div style="text-align:center">' +
      '<img src="' + src + '" style="max-width:100%;max-height:60vh;border:1px solid var(--border);border-radius:8px;background:var(--bg-card)" alt="Cover preview" ' +
        'onerror="this.insertAdjacentHTML(\'afterend\',\'<p style=color:var(--text-muted)>Preview unavailable — check the file directly.</p>\');this.style.display=\'none\'">' +
      '<p style="color:var(--text-muted);font-size:0.85rem;margin:12px 0 0">' +
        'AI-generated. <strong>Regenerate</strong> reuses the visual brief (new rendering, same concept). ' +
        '<strong>Rewrite brief</strong> asks the art-director for a new concept.' +
      '</p>' +
      metaHtml +
    '</div>' +
    '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px;flex-wrap:wrap">' +
      '<button class="eos-btn" onclick="rejectCover(' + escAttr(JSON.stringify(slug)) + ')">&#10005; Reject</button>' +
      '<button class="eos-btn" onclick="rewriteBrief(' + escAttr(JSON.stringify(slug)) + ')" title="Ask the art-director for a new visual concept">&#9998; Rewrite brief</button>' +
      '<button class="eos-btn" onclick="regenerateCover(' + escAttr(JSON.stringify(slug)) + ')" title="Same concept, new rendering">&#8635; Regenerate</button>' +
      '<button class="eos-btn eos-btn-primary" onclick="approveCover(' + escAttr(JSON.stringify(slug)) + ')">&#10003; Approve &amp; Embed</button>' +
    '</div>';
  EOS_UI.modal({ title: 'Cover preview — ' + slug, body: body, width: '680px' });
}

async function rewriteBrief(slug) {
  EOS_UI.closeModal();
  await generateCover(slug, { rewrite_brief: true });
}

async function approveCover(slug) {
  try {
    var result = await EOS.post('/publish/api/approve-cover', { slug: slug });
    if (result.error) {
      if (EOS_UI.toast) EOS_UI.toast(result.error, false);
      return;
    }
    coverStatus[slug] = { file: result.cover_file, size_kb: 0, embedded: true };
    EOS_UI.closeModal();
    renderPosts();
    if (EOS_UI.toast) EOS_UI.toast('Cover embedded. Click Build to publish.', true);
  } catch(e) {
    if (EOS_UI.toast) EOS_UI.toast('Failed: ' + e.message, false);
  }
}

async function rejectCover(slug) {
  try {
    var result = await EOS.post('/publish/api/reject-cover', { slug: slug });
    if (result.error) {
      if (EOS_UI.toast) EOS_UI.toast(result.error, false);
      return;
    }
    delete coverStatus[slug];
    EOS_UI.closeModal();
    renderPosts();
    if (EOS_UI.toast) EOS_UI.toast('Cover discarded.', true);
  } catch(e) {
    if (EOS_UI.toast) EOS_UI.toast('Failed: ' + e.message, false);
  }
}

async function regenerateCover(slug) {
  EOS_UI.closeModal();
  await generateCover(slug);
}

function viewPodcast(slug) {
  var info = podcastStatus[slug];
  if (!info) return;
  // Open the post page which has the embedded podcast
  window.open('/publish/api/site-file?path=posts/' + slug + '.html', '_blank');
}

function generatePodcast(slug) {
  function _doGenerate() {
    var gid = genStart({
      title: 'Generating podcast — ' + slug,
      sub: 'script → voice → scene images → slideshow · 3–8 min',
    });
    EOS.post('/publish/api/generate-podcast', {
      slug: slug, language: 'en', duration: 'auto', video: true
    }).then(function(result) {
      if (result.error) { genFinish(gid, false, result.error); return; }
      var parts = [result.segments + ' segments'];
      if (result.has_slideshow) parts.push(result.scene_images + ' scenes');
      if (result.auto_embedded) parts.push('auto-embedded');
      genFinish(gid, true, 'Podcast ready — ' + parts.join(' · ') + '. Click Build to publish.');
      if (EOS_UI.toast) EOS_UI.toast('Podcast generated! Click Build to publish.', true);
    }).catch(function(e) { genFinish(gid, false, e.message); });
  }
  if (podcastStatus[slug]) {
    EOS_UI.confirm('A podcast already exists for this post. Regenerate?', _doGenerate);
  } else {
    _doGenerate();
  }
}

async function loadSuggestions() {
  var list = document.getElementById('suggestions-list');
  list.innerHTML = '<div style="padding:12px;color:var(--text-muted);font-size:12px">Scanning vault...</div>';

  try {
    var result = await EOS.post('/publish/api/suggest-topics', {});
    var topics = result.topics || [];
    if (!topics.length) {
      list.innerHTML = EOS_UI.emptyState({message:'No suggestions found. Click Refresh to try again.'});
      return;
    }

    var chipHtml = (result.provenance && result.provenance.mode) ? '<div style="grid-column:1/-1;margin-bottom:4px">' + EOS_UI.provenance(result.provenance) + '</div>' : '';
    list.innerHTML = chipHtml + topics.map(function(t) {
      var path = encodeURIComponent(t.path || '');
      return '<a class="suggest-card" href="/publish/pages/writer.html?load=' + path + '">'
        + '<div class="suggest-title">' + esc(t.title) + '</div>'
        + '<div class="suggest-pitch">' + esc(t.pitch || '') + '</div>'
        + '<span class="suggest-type">' + esc(t.type || 'post') + '</span>'
        + '</a>';
    }).join('');
  } catch(e) {
    list.innerHTML = '<div style="padding:12px;color:var(--text-muted);font-size:12px">Could not load suggestions. Click Refresh to try again.</div>';
  }
}

function previewSite() {
  window.open('/publish/api/site-file?path=index.html', '_blank');
}

function updateVisitButton(url) {
  var btn = document.getElementById('btn-visit');
  // Use provided URL, or derive from config
  if (!url) {
    var c = config.config || {};
    var domain = c.domain;
    var repo = c.repo;
    if (domain && config.last_deploy) {
      url = 'https://' + domain;
    } else if (repo && config.last_deploy) {
      var parts = repo.split('/');
      url = 'https://' + parts[0] + '.github.io/' + parts[1];
    }
  }
  if (url) {
    btn.href = url;
    btn.style.display = '';
  } else {
    btn.style.display = 'none';
  }
}

function esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

// --- Settings (now edits active site profile) ---

function closeMore() {
  var m = document.getElementById('tb-more');
  if (m) m.removeAttribute('open');
}

async function openSettings() {
  document.getElementById('overlay').classList.add('open');
  document.getElementById('settings-panel').classList.add('open');

  // Load current values from active site profile
  var site = allSites.find(function(s) { return s.id === activeSiteId; }) || {};
  document.getElementById('sf-site-label').textContent =
    (site.name || site.id || 'Site') + (site.id ? '  ·  id: ' + site.id : '');
  document.getElementById('sf-name').value = site.name || '';
  document.getElementById('sf-site-name').value = site.site_name || '';
  document.getElementById('sf-site-desc').value = site.site_description || '';
  document.getElementById('sf-author').value = site.author || '';
  document.getElementById('sf-source').value = site.source_folder || '30_Resources/Published';
  (function(){
    var sel = document.getElementById('sf-theme');
    var want = site.theme || 'eos';
    sel.value = want;
    if (sel.value !== want) {  // theme not in the loaded option list — add it
      var o = document.createElement('option'); o.value = want; o.textContent = want;
      sel.appendChild(o); sel.value = want;
    }
  })();
  document.getElementById('sf-repo').value = site.repo || '';
  document.getElementById('sf-deploy-target').value = site.deploy_target || 'github';
  document.getElementById('sf-firebase-project').value = site.firebase_project || '';
  toggleDeployFields();
  document.getElementById('sf-bio').value = site.author_bio || '';
  document.getElementById('sf-social').value = site.social_links || '';
  document.getElementById('sf-domain').value = site.domain || '';
  document.getElementById('sf-favicon').value = site.favicon || '';
  document.getElementById('sf-search-engines').checked = site.search_engines !== false;
  var analytics = site.analytics || {};
  document.getElementById('sf-analytics-enabled').checked = !!analytics.enabled;
  document.getElementById('sf-analytics-url').value = analytics.collector_url || '';
  document.getElementById('sf-analytics-link').href = '/web-analytics/#' + encodeURIComponent(site.id || '');
  refreshFrameworkPanel(activeSiteId);

  // Chatbot fields
  var cb = site.chatbot || {};
  document.getElementById('sf-chatbot-enabled').checked = !!cb.enabled;
  document.getElementById('sf-chatbot-endpoint').value = cb.endpoint || '';
  document.getElementById('sf-chatbot-model').value = cb.model || 'gpt-5-nano';
  document.getElementById('sf-chatbot-cap').value =
    (cb.daily_cap_usd != null ? cb.daily_cap_usd : 2.0);
  document.getElementById('sf-chatbot-persona').value = cb.persona || '';
  document.getElementById('sf-chatbot-starters').value =
    Array.isArray(cb.starter_questions) ? cb.starter_questions.join(' · ') : '';
  document.getElementById('sf-chatbot-id-echo').textContent = site.id || '<id>';
  // Build the exact sites.toml block this site needs
  var domain = (site.domain || '').trim();
  var hosts = domain ? [`https://${domain}`] : ['https://your-domain.com'];
  // Add www variant if domain is a bare apex (no subdomain)
  if (domain && domain.split('.').length === 2) {
    hosts.push(`https://www.${domain}`);
  }
  var corpusUrl = domain ? `https://${domain}/corpus.json` : 'https://your-domain.com/corpus.json';
  var snippet =
    `[sites.${site.id || 'YOUR_SITE_ID'}]\n` +
    `name = ${JSON.stringify(site.name || site.id || 'Site')}\n` +
    `allowed_origins = ${JSON.stringify(hosts).replace(/,/g, ', ')}\n` +
    `corpus_url = ${JSON.stringify(corpusUrl)}`;
  document.getElementById('sf-chatbot-toml-snippet').textContent = snippet;
  document.getElementById('sf-chatbot-fields').style.display =
    document.getElementById('sf-chatbot-enabled').checked ? '' : 'none';
  // Toggle visibility on change
  document.getElementById('sf-chatbot-enabled').onchange = function(e) {
    document.getElementById('sf-chatbot-fields').style.display = e.target.checked ? '' : 'none';
  };

  // Show mode hint
  var mode = config.site_mode || 'blog';
  var hint = document.getElementById('sf-mode-hint');
  if (mode === 'project') {
    hint.innerHTML = '<strong>Mode: Project Site</strong> &#8212; A page with <code>layout: landing</code> in frontmatter was detected. The homepage shows a hero with feature cards. Other pages get a docs sidebar.<br><br>To switch to blog mode, remove <code>layout: landing</code> from the page frontmatter.';
  } else {
    hint.innerHTML = '<strong>Mode: Blog</strong> &#8212; The homepage shows featured posts in a card grid.<br><br>To switch to project mode, create a page with <code>layout: landing</code> in its frontmatter. Sections become feature cards, links become CTA buttons.';
  }
}

function closeSettings() {
  document.getElementById('overlay').classList.remove('open');
  document.getElementById('settings-panel').classList.remove('open');
}

// Show the Firebase Project field only when the deploy target is Firebase.
function toggleDeployFields() {
  var target = document.getElementById('sf-deploy-target').value;
  document.getElementById('sf-firebase-group').style.display =
    target === 'firebase' ? '' : 'none';
}

async function saveSettings() {
  var data = {
    name: document.getElementById('sf-name').value.trim() || 'Untitled',
    site_name: document.getElementById('sf-site-name').value.trim(),
    site_description: document.getElementById('sf-site-desc').value.trim(),
    author: document.getElementById('sf-author').value.trim(),
    source_folder: document.getElementById('sf-source').value.trim() || '30_Resources/Published',
    theme: document.getElementById('sf-theme').value,
    repo: document.getElementById('sf-repo').value.trim(),
    deploy_target: document.getElementById('sf-deploy-target').value,
    firebase_project: document.getElementById('sf-firebase-project').value.trim(),
    author_bio: document.getElementById('sf-bio').value.trim(),
    social_links: document.getElementById('sf-social').value.trim(),
    domain: document.getElementById('sf-domain').value.trim(),
    favicon: document.getElementById('sf-favicon').value.trim(),
    search_engines: document.getElementById('sf-search-engines').checked,
    analytics: {
      enabled: document.getElementById('sf-analytics-enabled').checked,
      collector_url: document.getElementById('sf-analytics-url').value.trim(),
    },
    chatbot: {
      enabled: document.getElementById('sf-chatbot-enabled').checked,
      endpoint: document.getElementById('sf-chatbot-endpoint').value.trim(),
      model: document.getElementById('sf-chatbot-model').value,
      daily_cap_usd: parseFloat(document.getElementById('sf-chatbot-cap').value) || 2.0,
      persona: document.getElementById('sf-chatbot-persona').value.trim(),
      starter_questions: document.getElementById('sf-chatbot-starters').value
        .split('·')
        .map(function(s) { return s.trim(); })
        .filter(function(s) { return s.length > 0; }),
    },
  };

  // Refuse to save a Firebase deploy target with no project id — the Deploy
  // button would just bounce the user back to settings.
  if (data.deploy_target === 'firebase' && !data.firebase_project) {
    if (EOS_UI && EOS_UI.toast) {
      EOS_UI.toast('Firebase Project ID is required when the deploy target is Firebase', false);
    }
    document.getElementById('sf-firebase-project').focus();
    return;
  }

  // Refuse to save chatbot enabled with no endpoint — would silently skip
  // widget injection at build time, which is confusing.
  if (data.chatbot.enabled && !data.chatbot.endpoint) {
    if (EOS_UI && EOS_UI.toast) {
      EOS_UI.toast('Chatbot endpoint URL is required when chatbot is enabled', false);
    }
    document.getElementById('sf-chatbot-endpoint').focus();
    return;
  }

  try {
    var resp = await fetch('/publish/api/sites/' + encodeURIComponent(activeSiteId), {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    });
    var result = await resp.json();
    if (result && result.error) {
      EOS_UI.toast(result.error, false);
      return;
    }
    closeSettings();

    // If chatbot is enabled, push synced fields (model/persona/cap/starters)
    // to the chatbot service. Silent best-effort — errors toast but don't
    // block the save.
    if (data.chatbot && data.chatbot.enabled) {
      try {
        var syncResp = await fetch('/publish/api/chatbot/sync-site/' + encodeURIComponent(activeSiteId), {
          method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'
        });
        var syncResult = await syncResp.json();
        if (syncResult && syncResult.error) {
          if (EOS_UI && EOS_UI.toast) {
            EOS_UI.toast('Saved locally. Service sync failed: ' + syncResult.error, false);
          }
        } else if (EOS_UI && EOS_UI.toast) {
          EOS_UI.toast('Site settings saved + synced to chatbot service', true);
        }
      } catch (e) {
        if (EOS_UI && EOS_UI.toast) {
          EOS_UI.toast('Saved locally. Service sync failed: ' + e.message, false);
        }
      }
    } else if (typeof EOS_UI !== 'undefined' && EOS_UI.toast) {
      EOS_UI.toast('Site settings saved', true);
    }

    init();
  } catch(e) {
    EOS_UI.toast('Failed to save: ' + e.message, false);
  }
}

// App Settings slide-out (shared helper) — global schema from manifest
// Dark feature flag is managed from global Settings, not this compact panel:
// settings-panel-drift: ignore publish.feature.publish-scheduled-posts.enabled
var _appSettings = EOS_UI.settingsPanel({
    id: 'app-settings-panel',
    title: 'Publish — App Settings',
    fields: [
        {key: 'publish.active_site', label: 'Active Site Profile', type: 'text', default: 'default',
         hint: 'The site profile ID shown by default. Usually switched by clicking site tabs.'},
    ],
});
function openAppSettings() { _appSettings.open(); }

// Keyboard shortcut
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeAll();
});

// Hands-Free gesture override — ILoveYou triggers a build. "I love this draft
// enough to ship it." The binding only applies while on /publish/.
if (EOS.handsFree) {
    EOS.handsFree.registerGesture('ILoveYou', function() { doBuild(); }, 'Build site');
}

// ── Chatbot Q&A modal ──────────────────────────────────────────────
//
// Shown when site.chatbot.enabled. Three sections (pending / curated / FAQ)
// proxied through publish app handlers — see apps/publish/app.py
// `api_chatbot_qa_*`. Service-side admin auth handled there; this UI just
// fetches + renders + dispatches actions.

var _qaModal = null;
var _qaState = { pending: [], curated: [], faqs: [], loading: false };

async function openChatbotQA() {
  if (_qaModal) { _qaModal.close(); _qaModal = null; }
  _qaModal = EOS_UI.modal({
    title: 'Chatbot Q&A — ' + esc(activeSiteId),
    body: '<div id="qa-modal-body"><div class="eos-empty-state">Loading…</div></div>',
    width: '720px',
  });
  await reloadChatbotQA();
}

async function reloadChatbotQA() {
  _qaState.loading = true;
  renderQAModal();
  function jget(p) {
    return fetch(p).then(function(r) { return r.json(); }).catch(function(e) {
      return { error: 'Network: ' + (e && e.message) };
    });
  }
  try {
    var res = await Promise.all([
      jget('/publish/api/chatbot/qa-log/' + encodeURIComponent(activeSiteId) + '?status=pending'),
      jget('/publish/api/chatbot/qa-log/' + encodeURIComponent(activeSiteId) + '?status=curated'),
      jget('/publish/api/chatbot/faqs/' + encodeURIComponent(activeSiteId)),
    ]);
    _qaState.pending = (res[0] && res[0].rows) || [];
    _qaState.curated = (res[1] && res[1].rows) || [];
    _qaState.faqs = (res[2] && res[2].faqs) || [];
    _qaState.faqsPath = (res[2] && res[2].path) || '';
    _qaState.error = (res[0] && res[0].error) || (res[1] && res[1].error) || (res[2] && res[2].error) || '';
  } catch (e) {
    _qaState.error = 'Network error: ' + (e && e.message);
  }
  _qaState.loading = false;
  renderQAModal();
}

function renderQAModal() {
  var el = document.getElementById('qa-modal-body');
  if (!el) return;
  if (_qaState.loading) {
    el.innerHTML = '<div class="eos-empty-state">Loading…</div>';
    return;
  }
  if (_qaState.error) {
    el.innerHTML = '<div class="eos-error-state"><strong>Error:</strong> ' + esc(_qaState.error) + '</div>';
    return;
  }
  var html = '';
  html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">';
  html += '<div style="font-size:12px;color:var(--text-muted)">Manage what the site chatbot serves before going to the LLM.</div>';
  html += '<button class="pi-btn" onclick="reloadChatbotQA()" title="Refresh">&#8635; Refresh</button>';
  html += '</div>';

  html += renderQASection('pending', 'Pending', _qaState.pending,
    'Visitor questions answered by the LLM. Approve good ones to cache them, reject bad ones.');
  html += renderQASection('curated', 'Curated (serving)', _qaState.curated,
    'These reply free + instant when visitors ask similar questions.');
  html += renderFAQSection(_qaState.faqs, _qaState.faqsPath);

  el.innerHTML = html;
}

function renderQASection(key, label, rows, hint) {
  var html = '<details ' + (rows.length ? 'open' : '') + ' style="margin-bottom:14px;border:1px solid var(--border);border-radius:8px">';
  html += '<summary style="padding:10px 12px;cursor:pointer;font-weight:600;font-size:13px">'
       + esc(label) + ' <span style="color:var(--text-muted);font-weight:400">('
       + rows.length + ')</span></summary>';
  html += '<div style="padding:8px 12px 12px"><div style="font-size:11px;color:var(--text-muted);margin-bottom:8px">'
       + esc(hint) + '</div>';
  if (!rows.length) {
    html += '<div class="eos-empty-state" style="padding:14px"><p class="eos-empty-state-message">Nothing here yet</p></div>';
  } else {
    rows.forEach(function(r) {
      html += renderQARow(r, key);
    });
  }
  html += '</div></details>';
  return html;
}

function renderQARow(r, section) {
  var ts = r.ts ? timeAgo(r.ts) : '';
  var srcChips = '';
  (r.sources || []).forEach(function(s) {
    var lbl = (s.title || s.id) + (s.section ? ' → ' + s.section : '');
    srcChips += '<span class="eos-badge" style="margin-right:4px;font-size:10px">📄 ' + esc(lbl) + '</span>';
  });
  var actions = '';
  if (section === 'pending') {
    actions += '<button class="pi-btn" onclick="qaAction(' + r.id + ',\'curate\')">Approve</button>';
    actions += '<button class="pi-btn" onclick="qaEdit(' + r.id + ')">Edit</button>';
    actions += '<button class="pi-btn" onclick="qaAction(' + r.id + ',\'reject\')">Reject</button>';
    actions += '<button class="pi-btn" onclick="qaPromote(' + r.id + ')" title="Append to faqs.toml">Promote → FAQ</button>';
  } else if (section === 'curated') {
    actions += '<button class="pi-btn" onclick="qaAction(' + r.id + ',\'uncurate\')">Un-cache</button>';
    actions += '<button class="pi-btn" onclick="qaEdit(' + r.id + ')">Edit</button>';
    actions += '<button class="pi-btn" onclick="qaPromote(' + r.id + ')">Promote → FAQ</button>';
  }
  return '<div style="border-top:1px solid var(--border);padding:10px 0">'
    + '<div style="font-size:13px;font-weight:500;margin-bottom:4px">' + esc(r.query) + '</div>'
    + '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;white-space:pre-wrap">' + esc(r.reply) + '</div>'
    + '<div style="margin-bottom:8px">' + srcChips + '</div>'
    + '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px">'
      + '<span style="font-size:10px;color:var(--text-muted)">' + esc(ts) + '</span>'
      + '<div style="display:flex;gap:4px;flex-wrap:wrap">' + actions + '</div>'
    + '</div>'
  + '</div>';
}

function renderFAQSection(faqs, path) {
  var html = '<details style="margin-bottom:14px;border:1px solid var(--border);border-radius:8px">';
  html += '<summary style="padding:10px 12px;cursor:pointer;font-weight:600;font-size:13px">'
       + 'FAQs (canon) <span style="color:var(--text-muted);font-weight:400">(' + faqs.length + ')</span></summary>';
  html += '<div style="padding:8px 12px 12px">';
  html += '<div style="font-size:11px;color:var(--text-muted);margin-bottom:8px">Hand-written canon committed in vault. Edit the file directly: <code>' + esc(path || 'faqs.toml') + '</code></div>';
  if (!faqs.length) {
    html += '<div class="eos-empty-state" style="padding:14px"><p class="eos-empty-state-message">No FAQs yet — promote a curated reply to seed this.</p></div>';
  } else {
    faqs.forEach(function(f) {
      html += '<div style="border-top:1px solid var(--border);padding:8px 0">'
           + '<div style="font-size:12px;font-weight:500">' + esc(f.q) + '</div>'
           + '<div style="font-size:12px;color:var(--text-secondary);white-space:pre-wrap">' + esc(f.a) + '</div>'
           + '</div>';
    });
  }
  html += '</div></details>';
  return html;
}

async function qaAction(qaId, action) {
  var url = '/publish/api/chatbot/qa-log/' + encodeURIComponent(activeSiteId) + '/' + qaId;
  var res = await EOS.post(url, { action: action });
  if (res && res.error) {
    if (EOS_UI && EOS_UI.toast) EOS_UI.toast(res.error, false);
    return;
  }
  if (EOS_UI && EOS_UI.toast) EOS_UI.toast(action + 'd', true);
  reloadChatbotQA();
}

async function qaEdit(qaId) {
  var row = (_qaState.pending.concat(_qaState.curated)).find(function(r) { return r.id === qaId; });
  if (!row) return;
  var current = row.reply || '';
  // EOS_UI.formModal takes positional args: (title, fields, onSubmit)
  EOS_UI.formModal(
    'Edit reply',
    [{ name: 'reply', label: 'Reply', type: 'textarea', value: current, required: true }],
    async function(values) {
      var url = '/publish/api/chatbot/qa-log/' + encodeURIComponent(activeSiteId) + '/' + qaId;
      var res = await EOS.post(url, { action: 'edit', reply: values.reply });
      if (res && res.error) {
        if (EOS_UI && EOS_UI.toast) EOS_UI.toast(res.error, false);
        return;
      }
      if (EOS_UI && EOS_UI.toast) EOS_UI.toast('Saved', true);
      reloadChatbotQA();
    }
  );
}

function copyChatbotToml() {
  var pre = document.getElementById('sf-chatbot-toml-snippet');
  if (!pre) return;
  var text = pre.textContent || '';
  navigator.clipboard.writeText(text).then(function() {
    if (EOS_UI && EOS_UI.toast) EOS_UI.toast('Copied — paste into sites.toml on the VPS', true);
  }).catch(function() {
    // Fallback: select for manual copy
    var range = document.createRange();
    range.selectNode(pre);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    if (EOS_UI && EOS_UI.toast) EOS_UI.toast('Selected — Ctrl+C to copy', true);
  });
}

async function qaPromote(qaId) {
  var ok = await EOS_UI.confirm({
    message: 'Promote this Q&A to FAQ? Appends to faqs.toml in the vault.',
    action: 'Promote',
    danger: false,
  });
  if (!ok) return;
  var url = '/publish/api/chatbot/qa-log/' + encodeURIComponent(activeSiteId) + '/' + qaId + '/promote';
  var res = await EOS.post(url, {});
  if (res && res.error) {
    if (EOS_UI && EOS_UI.toast) EOS_UI.toast(res.error, false);
    return;
  }
  if (EOS_UI && EOS_UI.toast) EOS_UI.toast('Added to FAQs — rebuild to ship', true);
  reloadChatbotQA();
}

init();
