// worklog -- page logic, extracted verbatim from pages/index.html (P4 Atomic
// split, .claude/rules/multi-module-apps.md frontend pattern). Loaded at the
// same position as the old inline <script>, so global scope and load order
// vs eos.js / eos-components.js are unchanged.
var API = '/worklog/api';
var STATUS_EMOJI = {complete:'✅','in-progress':'🔄',todo:'⬜',next:'⏭️',waiting:'⏳',review:'👀',blocked:'⛔'};
var CYCLE = ['todo','in-progress','review','complete','blocked','waiting','next',''];
var STATE = { employer:'', view:'timeline', detailDate:null, day:null };
var _grid = null;

function esc(s){ return (s==null?'':String(s)).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }
function emp(){ return STATE.employer ? '&employer='+encodeURIComponent(STATE.employer) : ''; }
// Both helpers REJECT on a non-OK response. Without that check a 500 whose
// body is {"error": ...} resolves successfully and the caller renders nonsense,
// while a 401 (an HTML login page) throws an opaque JSON parse error far from
// the call site. One helper, every call site benefits.
function _readBody(r){
  return r.text().then(function(t){
    var data = null;
    try{ data = t ? JSON.parse(t) : null; }catch(e){ /* non-JSON body */ }
    if(r.ok) return data;
    var msg = (data && (data.error || data.detail)) || (r.status+' '+r.statusText);
    var err = new Error(String(msg)); err.status = r.status; throw err;
  });
}
function api(path){ return fetch(API+path).then(_readBody); }
function post(path, body){ return fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(_readBody); }
// Render a failed load in the failure vocabulary, never the empty-state one —
// "No results" and "the server did not answer" are different facts.
// .claude/rules/self-audit-loops.md § UI / failure honesty.
function renderLoadError(el, e, retryJs){
  if(!el) return;
  var msg = (e && e.message) ? String(e.message) : 'Could not load.';
  el.innerHTML = EOS_UI.errorState({ message: msg, onRetry: retryJs || '' });
}

// ── attachments — ![[vault/relative/path]] tokens riding inside item text ──
// Same idiom as the CPEng #cN tag (shared.py): extra meaning smuggled into the
// opaque item-text string rather than a new frontmatter field, so it survives
// the existing parser/render round trip, export and import unchanged.
function parseAttachments(text){
  var paths = [];
  var clean = (text||'').replace(/!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]/g, function(_, p){ paths.push(p.trim()); return ''; })
    .replace(/[ \t]{2,}/g, ' ').trim();
  return {clean: clean, paths: paths};
}
function _isImageExt(path){ return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(path||''); }
// Full chip strip — a small thumbnail (images) or a 📎 filename chip
// (everything else), used where a row already has room for one (detail view,
// by-project). Click opens the original file via /api/vault/file.
function renderAttachmentChips(paths){
  if(!paths || !paths.length) return '';
  return '<span class="wl-att-chips">' + paths.map(function(p){
    var url = '/api/vault/file?path='+encodeURIComponent(p);
    var open = 'event.stopPropagation();window.open('+EOS_UI.jsArg(url)+',\'_blank\',\'noopener\')';
    if(_isImageExt(p)) return '<img class="wl-att-thumb" src="'+url+'" loading="lazy" alt="" onclick="'+open+'">';
    return '<span class="wl-att-chip" title="'+escAttr(p.split('/').pop())+'" onclick="'+open+'">📎</span>';
  }).join('') + '</span>';
}
// Minimal marker — a bare 📎 with no image fetch, for dense secondary rows
// (search results, CPEng evidence list) where a full thumbnail would blow up
// row height (.claude/rules/list-card-density.md).
function attachmentMarker(paths){
  return (paths && paths.length)
    ? ' <span class="wl-att-dot" title="'+paths.length+' attachment'+(paths.length>1?'s':'')+'">📎</span>' : '';
}

// ── project deep-link ──────────────────────────────────────────────────
// {name.toLowerCase(): project_id} for the projects the projects app knows,
// filled by loadProjects() (which boot awaits before anything renders).
var PROJECT_IDS = {};

// A project label, linked into Projects when that project actually exists.
// Returns the LABEL ONLY so callers keep their own badge/title element and no
// layout changes — same degrade-to-plain-text contract as task.js's
// projectBadge. Most worklog headings are history-only labels with no project
// note ("General" alone carries ~1000 items), so the unmatched branch is the
// common one, not the edge case.
//
// URL is /projects/workspace/<id>: the projects app retired its hash detail
// view (projects/pages/app.js:696), which keeps #<id> only as a redirect.
function projectLabel(name){
  var id = PROJECT_IDS[String(name == null ? '' : name).toLowerCase()];
  if(!id) return esc(name);
  return '<a class="proj-link" href="/projects/workspace/' + encodeURIComponent(id) + '"'
    + ' title="Open ' + escAttr(name) + ' in Projects"'
    // Timeline badges sit inside a card whose onclick opens the day detail.
    + ' onclick="event.stopPropagation()">' + esc(name) + '</a>';
}

// ── routing: shared hash-route helper ──────────────────────────────────
// Two hash shapes: #YYYY-MM-DD opens a day's detail, #<tab> opens a tab.
// Tab routing exists because the CPEng hub panel links to /worklog/#competency
// (competency.py:158); without it that hash matched nothing and silently fell
// through to the Timeline, so a hub row promising "CPEng evidence still
// missing" landed the user on a different screen with no error.
// `competency` is accepted as an alias for the `cpeng` tab so an already-
// bookmarked hub row keeps working.
var TAB_VIEWS = ['timeline','calendar','project','heatmap','search','cpeng'];
var HASH_TAB_ALIASES = { competency: 'cpeng', cpeng: 'cpeng' };
function tabForHash(id){
  var key = String(id == null ? '' : id).toLowerCase();
  if(HASH_TAB_ALIASES[key]) return HASH_TAB_ALIASES[key];
  return TAB_VIEWS.indexOf(key) >= 0 ? key : '';
}
var _route = EOS_UI.hashRoute({
  onShow: function(id){
    if(/^\d{4}-\d{2}-\d{2}$/.test(id)) { showDetail(id); return; }
    var tab = tabForHash(id);
    if(tab){ STATE.view = tab; showTabView(tab); loadView(tab); return; }
    showTabView(STATE.view);
  },
  onHide: function(){ showTabView(STATE.view); },
});

// ── tabs ──
function showTabView(v){
  TAB_VIEWS.forEach(function(x){
    document.getElementById('view-'+x).classList.toggle('hidden', x!==v);
  });
  document.getElementById('view-detail').classList.add('hidden');
  document.querySelectorAll('.tab').forEach(function(t){ t.classList.toggle('active', t.dataset.view===v); });
}
function loadView(v){
  if(v==='calendar') loadCalendar();
  else if(v==='project') loadProjectPicker();
  else if(v==='heatmap') loadHeatmap();
  else if(v==='search') loadSearch();
  else if(v==='cpeng') loadCompetency();
  else loadTimeline();
}

// ── CPEng competency evidence ──
// Every one of the 16 elements renders, including those at zero — an element
// with no evidence IS the finding, so it must never be filtered out.
async function loadCompetency(){
  var win = document.getElementById('cp-window').value;
  var q = '/competency?window_days='+encodeURIComponent(win);
  if(STATE.employer) q += '&employer='+encodeURIComponent(STATE.employer);
  var box = document.getElementById('cp-results');
  box.innerHTML = '<div class="card" style="color:var(--text-muted)">Gathering evidence…</div>';
  var d;
  try { d = await api(q); }
  catch(e){ box.innerHTML = EOS_UI.errorState({title:'Could not read the work log', detail:String(e&&e.message||e), onRetry:'loadCompetency()'}); return; }
  if(!d || !d.areas){ box.innerHTML = EOS_UI.errorState({title:'Could not read the work log', detail:(d&&d.error)||'No response', onRetry:'loadCompetency()'}); return; }

  document.getElementById('cp-summary').textContent =
    d.covered+' of '+d.total_elements+' elements evidenced · '
    + d.tagged_items+' of '+d.total_items+' items tagged · since '+d.since;

  var html = '';
  if((d.open_focus||[]).length){
    html += '<div class="card" style="border-left:3px solid var(--red)">'
         +  '<b>Still unevidenced, and these are the two that can fail the application.</b><br>'
         +  '<span style="color:var(--text-muted);font-size:14px">'
         +  d.open_focus.map(function(n){ return 'Element '+n; }).join(' · ')
         +  ' — neither can be back-filled from a CV. Tag them the day they happen.</span></div>';
  }
  (d.areas||[]).forEach(function(a){
    html += '<div class="card"><h3 style="margin:0 0 10px">'+esc(a.area)+'</h3>';
    (a.elements||[]).forEach(function(el){
      var tone = el.count ? 'var(--green)' : (el.focus==='gap' ? 'var(--red)' : 'var(--text-muted)');
      var badge = el.focus==='gap' ? ' <span class="eos-badge eos-badge-status-blocked">gap</span>'
                : el.focus==='thin' ? ' <span class="eos-badge eos-badge-status-shelved">thin</span>' : '';
      html += '<div style="padding:8px 0;border-top:1px solid var(--border)">'
           +  '<div style="display:flex;gap:10px;align-items:baseline">'
           +  '<code style="color:'+tone+'">#c'+el.n+'</code>'
           +  '<b>'+esc(el.name)+'</b>'+badge
           +  '<span style="margin-left:auto;color:'+tone+';font-variant-numeric:tabular-nums">'+el.count+'</span></div>';
      (el.items||[]).forEach(function(it){
        var parsed = parseAttachments(it.text);
        html += '<div style="font-size:13px;color:var(--text-muted);margin:3px 0 0 22px">'
             +  '<a href="#'+escAttr(it.date)+'">'+esc(it.date)+'</a> · '
             +  esc(it.project)+' — '+esc(parsed.clean)+attachmentMarker(parsed.paths)+'</div>';
      });
      html += '</div>';
    });
    html += '</div>';
  });
  box.innerHTML = html;
}
function switchTab(v){
  STATE.view = v;
  if(_route.current()) _route.clear();   // leave an open detail (fires onHide)
  showTabView(v);
  loadView(v);
}

// ── employer filter ──
function onEmployerChange(){ STATE.employer = document.getElementById('emp-filter').value; switchTab(STATE.view); }
async function loadEmployers(){
  var d = await api('/employers');
  var sel = document.getElementById('emp-filter');
  sel.innerHTML = '<option value="">All employers</option>';
  (d.employers||[]).forEach(function(e){
    var o = document.createElement('option'); o.value = e.name==='—'?'':e.name;
    o.textContent = e.name+' ('+e.days+')'; sel.appendChild(o);
  });
  // Rebuilt options drop the selection — restore it so a reload after an
  // import doesn't show "All employers" while STATE is still filtering.
  sel.value = STATE.employer || '';
  if(sel.value !== (STATE.employer||'')){ STATE.employer = ''; sel.value = ''; }
}

// Local calendar date. `toISOString()` is UTC, so east of Greenwich it names
// yesterday for the first hours of every day — the server sends local dates
// (`date.today()`), so formatting must be local too or "today" disagrees.
function isoDate(d){
  d = d || new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0')
       + '-' + String(d.getDate()).padStart(2,'0');
}

// ── timeline stats (client-side, from the recent payload) ──
// Streak = consecutive logged workdays; weekends never break it, and an
// unlogged *today* doesn't either (the day isn't over yet).
function calcStreak(dates){
  var set = {}; dates.forEach(function(x){ set[x] = true; });
  var cur = new Date(); cur.setHours(0,0,0,0);
  if(!set[isoDate(cur)]) cur.setDate(cur.getDate()-1);
  var streak = 0;
  for(var guard = 0; guard < 400; guard++){
    var iso = isoDate(cur);
    var dow = cur.getDay();
    if(set[iso]) streak++;
    else if(dow !== 0 && dow !== 6) break;
    cur.setDate(cur.getDate()-1);
  }
  return streak;
}
function renderWlStats(days){
  var thisMonth = isoDate().slice(0,7);
  var weekAgo = isoDate(new Date(Date.now() - 7*86400000));
  var monthDays = 0, weekItems = 0, weekBlocked = 0, weekHours = 0;
  (days||[]).forEach(function(s){
    if(s.date.slice(0,7) === thisMonth) monthDays++;
    if(s.date >= weekAgo){
      weekHours += (s.hours || 0);
      Object.keys(s.status_counts||{}).forEach(function(k){
        weekItems += s.status_counts[k];
        if(k === 'blocked') weekBlocked += s.status_counts[k];
      });
    }
  });
  var streak = calcStreak((days||[]).map(function(s){ return s.date; }));
  var cards = [
    {value: monthDays,   label: 'Days this month', variant: 'accent'},
    {value: weekItems,   label: 'Items this week', variant: 'accent'},
    {value: weekBlocked, label: 'Blocked (7d)',    variant: weekBlocked ? 'danger' : 'success'},
    {value: streak + (streak >= 5 ? ' 🔥' : ''), label: 'Workday streak', variant: 'success'},
  ];
  // Only when hours are actually recorded — an always-visible "0 h" would read
  // as a broken stat on a vault that uses neither time convention.
  if(weekHours > 0) cards.push({value: (Math.round(weekHours*10)/10)+' h', label: 'Hours (7d)', variant: 'accent'});
  EOS_UI.statCards('wl-stats', cards);
}

// ── AI rollup + timesheet export + smart parse + carry-over ──
var _rollupText = '';
var _rollupDays = 7;
function openRollup(){
  EOS_UI.modal({
    title:'✨ Work rollup', width:'640px',
    body:'<div class="form-row" style="margin-bottom:10px">'
       +   '<span class="muted">Window</span>'
       +   '<select id="rollup-days" class="sel" onchange="loadRollup()" title="How far back to summarise">'
       +     [7,14,30,60,90].map(function(n){
              return '<option value="'+n+'"'+(n===_rollupDays?' selected':'')+'>Last '+n+' days</option>'; }).join('')
       +   '</select>'
       + '</div><div id="rollup-out" class="muted">Thinking…</div>',
  });
  loadRollup();
}
async function loadRollup(){
  var sel = document.getElementById('rollup-days');
  if(sel) _rollupDays = parseInt(sel.value, 10) || 7;
  var out = document.getElementById('rollup-out');
  if(out){ out.className='muted'; out.textContent='Thinking…'; }
  try{
    var d = await api('/rollup?days='+_rollupDays+emp());
    out = document.getElementById('rollup-out');
    if(!out) return;                       // modal closed while thinking
    if(d.error){ out.className='muted'; out.textContent = d.error; return; }
    _rollupText = d.rollup || '';
    out.className = '';
    out.innerHTML = '<div class="prose-view" style="white-space:pre-wrap">'+esc(d.rollup)+'</div>'
      + '<div style="display:flex;gap:8px;margin-top:10px;align-items:center;flex-wrap:wrap">'
      + '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="copyRollup()" title="Copy this rollup">Copy</button>'
      + (d.provenance && d.provenance.mode ? EOS_UI.provenance({
            mode:d.provenance.mode, provider:d.provenance.provider, model:d.provenance.model,
            title:'Summarised from '+d.items+' work item(s) over '+esc(d.window||'')}) : '')
      + '<span class="muted" style="font-size:12px">'+d.items+' items'
      // The rollup caps how much it feeds the model; say so rather than
      // presenting a summary of a subset as a summary of everything.
      + (d.truncated ? ' (most recent of '+(d.items+d.truncated)+')' : '')
      + (d.saved ? ' · saved to vault' : '')+'</span></div>';
  }catch(e){ var o=document.getElementById('rollup-out');
    if(o){ o.className=''; renderLoadError(o, e, 'loadRollup()'); } }
}
function copyRollup(){
  if(navigator.clipboard && _rollupText){
    navigator.clipboard.writeText(_rollupText).then(function(){ EOS.toast&&EOS.toast('Copied'); });
  }
}
// Range-picker modal, shared by the PDF timesheet and CSV export. A trailing
// "last N days" cannot express "FY2024", which is the shape an employment
// record is actually asked for — and the 92-day cap put almost all recorded
// hours out of reach.
function exportTimesheet(){ return openExportRange('pdf'); }
function exportCsv(){ return openExportRange('csv'); }
async function openExportRange(fmt){
  var isCsv = fmt === 'csv';
  EOS_UI.modal({ title: isCsv ? 'Export CSV' : 'Export timesheet', width:'520px',
    body:'<div id="ts-range" class="muted">Loading…</div>' });
  var host = document.getElementById('ts-range');
  var years = [];
  try{ years = (await api('/years'+(STATE.employer?'?employer='+encodeURIComponent(STATE.employer):''))).years||[]; }
  catch(e){}
  // Only offer years that hold data — a gap year renders an empty export.
  var yearBtns = years.map(function(y){
    return '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="runExport(\''+fmt+'\',\''
      + y.year+'-01-01\',\''+y.year+'-12-31\')" title="Export the '+y.year+' calendar year">'
      + y.year+' <span class="muted">('+y.days+'d'+(y.hours?' · '+y.hours+'h':'')+')</span></button>';
  }).join(' ');
  host.className = '';
  host.innerHTML =
      '<div class="section-label">Quick ranges</div>'
    + '<div class="ts-btns">'
    +   '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="runExport(\''+fmt+'\',\'\',\'\',7)" title="Export the last 7 days">Last 7 days</button> '
    +   '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="runExport(\''+fmt+'\',\'\',\'\',30)" title="Export the last 30 days">Last 30 days</button> '
    +   '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="runExport(\''+fmt+'\',\'1970-01-01\',\'\')" title="Export every recorded day">All time</button>'
    + '</div>'
    + (yearBtns ? '<div class="section-label">By year</div><div class="ts-btns">'+yearBtns+'</div>' : '')
    + '<div class="section-label">Custom range</div>'
    + '<div class="form-row">'
    +   '<input type="date" id="ts-from" class="sel" title="Start date (inclusive)">'
    +   '<span class="muted">→</span>'
    +   '<input type="date" id="ts-to" class="sel" title="End date (inclusive)">'
    +   '<button class="eos-btn eos-btn-sm eos-btn-primary" onclick="runExportCustom(\''+fmt+'\')" title="Export this date range">Export</button>'
    + '</div>'
    + (years.length ? '' : '<div class="muted" style="margin-top:8px">No worklog days found yet.</div>');
}
function runExport(fmt, from, to, days){
  var q = days ? 'days='+days : 'from='+encodeURIComponent(from||'')+'&to='+encodeURIComponent(to||'');
  var path = fmt === 'csv' ? '/export.csv' : '/timesheet.pdf';
  window.open(API+path+'?'+q+emp(), '_blank', 'noopener');
  EOS_UI.closeModal();
}
function runExportCustom(fmt){
  var f = document.getElementById('ts-from').value, t = document.getElementById('ts-to').value;
  if(!f && !t){ EOS.toast&&EOS.toast('Pick at least one date'); return; }
  runExport(fmt, f, t);
}

async function smartParse(btn){
  var text = document.getElementById('wl-add-text').value.trim();
  if(!text){ EOS.toast&&EOS.toast('Type a sentence first'); return; }
  if(btn){ btn.disabled=true; btn.textContent='…'; }
  try{
    var d = await post('/smart-parse', {text:text, employer:STATE.employer});
    if(d.error){ EOS.toast&&EOS.toast(d.error); return; }
    document.getElementById('wl-proj').value = d.project||'';
    document.getElementById('wl-add-text').value = d.text||text;
    if(d.status) document.getElementById('wl-status').value = d.status;
    if(d.date) document.getElementById('wl-date').value = d.date;
    // The parse is AI-authored and the user is about to commit it — say which
    // model produced it. The endpoint has always returned this; the page was
    // throwing it away.
    var prov = document.getElementById('sp-prov');
    if(prov && d.provenance && d.provenance.mode){
      prov.innerHTML = EOS_UI.provenance({
        mode:d.provenance.mode, provider:d.provenance.provider, model:d.provenance.model,
        title:'Project / status / date proposed by the model — review before logging'});
    }
    EOS.toast&&EOS.toast('Parsed — review and press Log');
  }catch(e){ EOS.toast&&EOS.toast('Parse failed'); }
  finally{ if(btn){ btn.disabled=false; btn.textContent='✨'; } }
}

var _carryover = null;
async function maybeCarryover(days){
  var host = document.getElementById('carryover-banner');
  if(!host) return;
  var todayIso = isoDate();
  var todayLogged = days.length && days[0].date === todayIso && days[0].item_count > 0;
  if(todayLogged){ host.innerHTML=''; return; }
  try{
    var d = await api('/carryover'+(STATE.employer?'?employer='+encodeURIComponent(STATE.employer):''));
    if(!d.items || !d.items.length){ host.innerHTML=''; return; }
    _carryover = d;
    host.innerHTML = '<div class="card" style="border-left:3px solid var(--warning)">'
      + '<b>'+d.items.length+' open item'+(d.items.length>1?'s':'')+'</b> from '+esc(d.weekday||'')+' '+esc(d.from)
      + ' <button class="eos-btn eos-btn-sm eos-btn-secondary" style="margin-left:8px" onclick="applyCarryover(this)" title="Copy these open items into today">Carry over to today</button>'
      + '<div class="muted" style="font-size:12px;margin-top:6px">'
      + d.items.slice(0,5).map(function(it){ return (STATUS_EMOJI[it.status]||'·')+' '+esc(it.project)+': '+esc(parseAttachments(it.text).clean); }).join('<br>')
      + (d.items.length>5?'<br>… +'+(d.items.length-5)+' more':'')
      + '</div></div>';
  // error-state: intentional — the carry-over banner is an optional nudge,
  // not a pane; a failure here must not push an error at the user.
  }catch(e){ host.innerHTML=''; }
}
async function applyCarryover(btn){
  if(!_carryover) return;
  if(btn) btn.disabled=true;
  try{
    for(var i=0;i<_carryover.items.length;i++){
      var it=_carryover.items[i];
      await post('/log', {date:'', project:it.project, text:it.text,
        status:it.status||'in-progress', employer:STATE.employer});
    }
    EOS.toast&&EOS.toast('Carried over '+_carryover.items.length+' item(s)');
    _carryover=null; loadTimeline();
  }catch(e){ EOS.toast&&EOS.toast('Carry-over failed'); if(btn) btn.disabled=false; }
}

// ── timer (start/stop → writes the existing `Logged time:` convention) ──
var _timerState = {running:false};
var _timerTickHandle = null;
function fmtElapsed(ms){
  var s = Math.max(0, Math.floor(ms/1000));
  var h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = s%60;
  return (h?String(h).padStart(2,'0')+':':'') + String(m).padStart(2,'0')+':'+String(sec).padStart(2,'0');
}
function renderTimerWidget(){
  var host = document.getElementById('timer-widget');
  if(!host) return;
  if(_timerState.running){
    var elapsed = Date.now() - new Date(_timerState.started_at).getTime();
    host.innerHTML = '<span class="timer-chip" title="'+escAttr(_timerState.project||'')+' — started '
      + escAttr((_timerState.started_at||'').slice(11,16))
      + (_timerState.unknown ? ' (last known — daemon unreachable)' : '')+'">⏱ '
      + fmtElapsed(elapsed)+' · '+esc(_timerState.project||'')+'</span>'
      + '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="stopTimer()" title="Stop the timer and log the time to today\'s worklog">⏹ Stop</button>'
      + '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="cancelTimer()" title="Discard this timer — nothing gets logged">✕</button>';
  } else if(_timerState.unknown){
    // The server did not answer. "Not running" would be a claim we cannot
    // make — a timer may well be running, and offering Start here produces
    // "timer already running for X" (timer.py) on click. Say so instead.
    host.innerHTML = '<span class="timer-chip" title="Could not reach the daemon to read timer state">⏱ ?</span>'
      + '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="loadTimerStatus()" title="Re-check whether a timer is running">Retry</button>';
  } else {
    host.innerHTML = '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="startTimer()" title="Start a timer for the project in the Log work field">▶ Start</button>';
  }
}
async function loadTimerStatus(){
  // On failure keep the last known state and mark it unknown — never fall back
  // to {running:false}, which renders a *false* idle widget rather than a
  // degraded one (.claude/rules/self-audit-loops.md § UI / failure honesty).
  try{ _timerState = await api('/timer'); }
  catch(e){ _timerState = Object.assign({}, _timerState, {unknown:true}); }
  renderTimerWidget();
  if(_timerTickHandle){ clearInterval(_timerTickHandle); _timerTickHandle=null; }
  if(_timerState.running){
    _timerTickHandle = setInterval(function(){
      var host = document.getElementById('timer-widget'); if(!host) return;
      var chip = host.querySelector('.timer-chip'); if(!chip) return;
      var elapsed = Date.now() - new Date(_timerState.started_at).getTime();
      chip.textContent = '⏱ '+fmtElapsed(elapsed)+' · '+(_timerState.project||'');
    }, 1000);
  }
}
async function startTimer(){
  var project = (document.getElementById('wl-proj').value||'').trim() || 'General';
  var r = await post('/timer/start', {project:project, employer:STATE.employer});
  if(r.error){ EOS.toast&&EOS.toast(r.error); return; }
  EOS.toast&&EOS.toast('Timer started — '+project);
  loadTimerStatus();
}
async function stopTimer(){
  var r = await post('/timer/stop', {});
  if(r.error){ EOS.toast&&EOS.toast(r.error); return; }
  EOS.toast&&EOS.toast('Logged '+r.start+'–'+r.end);
  loadTimerStatus();
  if(STATE.detailDate === r.date) refreshDetail();
  loadTimeline();
}
async function cancelTimer(){
  if(!await EOS_UI.confirm({message:'Discard this timer? Nothing will be logged.', action:'Discard'})) return;
  var r = await post('/timer/cancel', {});
  if(r.error){ EOS.toast&&EOS.toast(r.error); return; }
  EOS.toast&&EOS.toast('Timer discarded');
  loadTimerStatus();
}

// ── capture-ingest lane (worklog-capture's pending drafts, surfaced inline) ──
// Not a merge — worklog-capture stays the capture source, worklog stays the
// write endpoint. Degrades to nothing when worklog-capture isn't installed
// or has zero pending drafts (see apps/public/standard/worklog/capture_ingest.py).
var _capturePending = [];
async function loadCapturePending(){
  var host = document.getElementById('capture-banner');
  if(!host) return;
  try{
    var d = await api('/capture/pending');
    var items = (d && d.available) ? (d.flagged||[]) : [];
    _capturePending = items;
    if(!items.length){ host.innerHTML=''; return; }
    host.innerHTML = '<div class="card" style="border-left:3px solid var(--accent)">'
      + '<div class="section-label" style="margin:0 0 8px">📸 '+items.length
      + ' capture'+(items.length>1?'s':'')+' awaiting review</div>'
      + items.slice(0,4).map(renderCaptureCard).join('')
      + (items.length>4
          ? '<div class="muted" style="font-size:12px;margin-top:4px">… +'+(items.length-4)
            +' more — open <a href="/worklog-capture/" target="_blank" rel="noopener">Worklog Capture</a></div>'
          : '')
      + '</div>';
  }catch(e){ host.innerHTML=''; }
}
function renderCaptureCard(c){
  var d = c.draft||{};
  var thumb = c.has_image
    ? '<img src="/worklog-capture/api/thumb/'+encodeURIComponent(c.id)+'" loading="lazy" '
      + 'style="width:64px;height:48px;object-fit:cover;border-radius:6px" alt="">'
    : '';
  var badges = [];
  if(d.project) badges.push({label:d.project, variant:'neutral'});
  // Same eos-badge-status-<status> vocabulary the CPEng tab already uses
  // (entityCard prefixes variant with "eos-badge-", so "status-blocked" ->
  // the exact class this app renders status pills with elsewhere).
  if(d.status) badges.push({label:d.status, variant:'status-'+d.status});
  return EOS_UI.entityCard({
    title: d.text || c.ocr_excerpt || '(no draft text)',
    badges: badges,
    body: thumb,
    actions: '<button class="eos-btn eos-btn-sm eos-btn-primary" onclick="applyCapture('
        + EOS_UI.jsArg(c.id) + ',this)" title="Log this into today\'s worklog">Apply</button>'
      + '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="dismissCapture('
        + EOS_UI.jsArg(c.id) + ',this)" title="Discard this capture">Dismiss</button>',
  });
}
async function applyCapture(id, btn){
  if(btn) btn.disabled=true;
  try{
    var r = await post('/capture/apply/'+encodeURIComponent(id), {});
    if(r.error){ EOS.toast&&EOS.toast(r.error); if(btn) btn.disabled=false; return; }
    EOS.toast&&EOS.toast('Logged');
    loadCapturePending(); loadTimeline();
  }catch(e){ EOS.toast&&EOS.toast('Apply failed'); if(btn) btn.disabled=false; }
}
async function dismissCapture(id, btn){
  if(btn) btn.disabled=true;
  try{
    await post('/capture/dismiss/'+encodeURIComponent(id), {});
    loadCapturePending();
  }catch(e){ EOS.toast&&EOS.toast('Dismiss failed'); if(btn) btn.disabled=false; }
}

// ── board view (kanban-by-status over recent items — see boards.py) ──
// Feature-detected so an uninstalled boards app leaves no dead UI
// (.claude/rules/boards-as-view-layer.md).
async function loadBoardLink(){
  var host = document.getElementById('board-link');
  if(!host) return;
  try{
    var r = await fetch('/boards/api/boards/worklog-boards');
    if(!r.ok){ host.innerHTML=''; return; }
    host.innerHTML = '<a href="/boards/?id=worklog-boards" class="eos-btn eos-btn-sm eos-btn-secondary" '
      + 'style="text-decoration:none" target="_blank" rel="noopener" '
      + 'title="Kanban board over recent work items, grouped by status">⊞ Board</a>';
  // error-state: intentional — feature detection; an uninstalled boards app
  // must leave no dead UI, not an error banner.
  }catch(e){ host.innerHTML=''; }
}

// ── timeline ──
async function loadTimeline(){
  var el = document.getElementById('timeline-list');
  var d;
  try{ d = await api('/recent?days=120'+emp()); }
  catch(e){ renderLoadError(el, e, 'loadTimeline()'); return; }
  renderWlStats(d.days||[]);
  maybeCarryover(d.days||[]);
  loadCapturePending();
  if(!d.days || !d.days.length){ el.innerHTML = '<div class="muted card">No work logged yet. Use the form above to log your first item.</div>'; return; }
  el.innerHTML = d.days.map(function(s){
    var statusPills = Object.keys(s.status_counts||{}).map(function(k){ return STATUS_EMOJI[k]+' '+s.status_counts[k]; }).join(' · ');
    var countTxt = s.item_count ? (s.item_count + (s.item_count===1?' item':' items')) : '';
    var pills = [countTxt, statusPills].filter(Boolean).join(' · ');
    var badges = (s.projects||[]).slice(0,5).map(function(p){ return '<span class="proj-badge">'+projectLabel(p)+'</span>'; }).join('');
    var snippet = s.snippet ? '<div class="day-snippet">'+esc(s.snippet)+'</div>' : '';
    return '<div class="card day-card" onclick="openDetail(\''+s.date+'\')">'
      + '<div class="day-head"><span class="day-date">'+s.date+'</span><span class="day-week">'+esc(s.weekday)+'</span>'
      + (s.employer?'<span class="emp-chip">'+esc(s.employer)+'</span>':'')
      + (s.hours?'<span class="hours-chip">'+s.hours+'h</span>':'')
      + '<span class="pills">'+pills+'</span></div>'
      + snippet
      + '<div class="proj-badges">'+badges+'</div></div>';
  }).join('');
}

// ── detail ──
function openDetail(dateS){ _route.set(dateS); }   // → onShow → showDetail
function closeDetail(){ _route.clear(); }           // → onHide → restore tab view
async function showDetail(dateS){
  STATE.detailDate = dateS;
  TAB_VIEWS.forEach(function(x){ document.getElementById('view-'+x).classList.add('hidden'); });
  document.getElementById('view-detail').classList.remove('hidden');
  try{ renderDetail(await api('/day?date='+encodeURIComponent(dateS))); }
  catch(e){ renderLoadError(document.getElementById('view-detail'), e,
                            'showDetail(' + EOS_UI.jsArg(dateS) + ')'); }
}
function renderDetail(d){
  STATE.day = d;   // cache focus day for the companion sidebar (context + fuzzy match)
  var h = '<div class="day-head"><span class="day-date">'+d.date+'</span><span class="day-week">'+esc(d.weekday)+'</span>'
    + (d.employer?'<span class="emp-chip">'+esc(d.employer)+'</span>':'')+'</div>';
  h += proseSection('Plan', d.plan||'', 'plan');
  h += '<div class="section-label">Work</div>';
  (d.projects||[]).forEach(function(g){
    if(!g.items||!g.items.length) return;
    h += '<div class="grp-title">'+projectLabel(g.project)+'</div>';
    g.items.forEach(function(it){
      var em = STATUS_EMOJI[it.status]||'·';
      var parsed = parseAttachments(it.text);
      // data-text stays the RAW string (incl. ![[...]] tokens) — cycleStatus
      // posts it back to /status, which matches verbatim against the stored
      // item text server-side.
      // text_clean / competencies / focus come from the server (reads.py) — the
      // page must not re-derive them — see the sibling competency-chips.js.
      var shown = parseAttachments(it.text_clean != null ? it.text_clean : it.text).clean;
      h += '<div class="item" onclick="cycleStatus(' + escAttr(JSON.stringify(g.project)) + ',this)" data-text="'+escAttr(it.text)+'">'
        + '<span class="em">'+em+'</span><span class="tx">'+esc(shown)+renderCompetencyTags(it.competencies, it.focus)+'</span>'
        + renderAttachmentChips(parsed.paths)+'</div>';
    });
  });
  var itemCount = (d.projects||[]).reduce(function(n,g){ return n + ((g.items||[]).length); }, 0);
  var untagged = (d.projects||[]).reduce(function(n,g){
    return n + (g.items||[]).filter(function(it){ return !(it.competencies||[]).length; }).length;
  }, 0);
  if(untagged > 0){
    h += '<div id="cp-suggest-box"><button class="eos-btn eos-btn-sm eos-btn-ghost" '
      + 'onclick="suggestCompetency()" title="Propose Engineers Australia Stage 2 elements '
      + 'for this day&#39;s untagged items. Nothing is written until you accept.">'
      + '&#10024; Suggest CPEng tags ('+untagged+')</button></div>';
  }
  h += proseSection('Update', d.update||'', 'update', itemCount > 0);
  // Hours come from either convention — an itemised ## Timesheet, or the
  // hand-written `Logged time:` range that 136 of 213 day notes actually use.
  if((d.timesheet && d.timesheet.length) || (d.logged_time && d.logged_time.length)){
    h += '<div class="section-label">Hours'
       + (d.hours ? ' <span class="hours-chip">'+d.hours+'h</span>' : '')+'</div>';
    h += (d.timesheet||[]).map(function(t){
      return '<div class="muted">'+esc(t.project)+': '+t.hours+'h '+(t.note?'— '+esc(t.note):'')+'</div>'; }).join('');
    if(!(d.timesheet||[]).length){
      h += (d.logged_time||[]).map(function(t){
        var span = t.start ? esc(t.start)+'–'+esc(t.end)+' · ' : '';
        return '<div class="muted">'+span+t.hours+'h'+(t.note?' — '+esc(t.note):'')+'</div>'; }).join('');
    }
  }
  if(d.notes && d.notes.length){
    h += '<div class="section-label">Notes</div>';
    h += d.notes.map(function(n){return '<div class="muted">• '+esc(n)+'</div>';}).join('');
  }
  // The add-item form goes LAST: everything above is what the day contains,
  // this is the one action. With Hours empty it never mattered where this sat;
  // now that Hours carries content, day content reading after an input form
  // was the wrong order.
  h += '<div class="section-label">Add to this day</div>';
  h += '<div class="form-row"><input id="dt-proj" class="proj-in" list="proj-list" placeholder="Project">'
    + '<input id="dt-text" class="tx-in" placeholder="Work item" onkeydown="if(event.key===\'Enter\')addToDay()">'
    + '<select id="dt-status" class="sel">'
    + Object.keys(STATUS_EMOJI).map(function(k){return '<option value="'+k+'">'+STATUS_EMOJI[k]+' '+k+'</option>';}).join('')
    + '</select><button class="eos-btn eos-btn-sm eos-btn-primary" onclick="addToDay()" title="Add this item to the selected day">Add</button></div>';
  document.getElementById('detail-body').innerHTML = '<div class="card">'+h+'</div>';
  renderCompetencyProposals();
}
async function cycleStatus(project, el){
  var text = el.getAttribute('data-text');
  var cur = el.querySelector('.em').textContent;
  var curStatus = Object.keys(STATUS_EMOJI).find(function(k){return STATUS_EMOJI[k]===cur;}) || '';
  var next = CYCLE[(CYCLE.indexOf(curStatus)+1) % CYCLE.length];
  await post('/status', {date:STATE.detailDate, project:project, item:text, status:next});
  el.querySelector('.em').textContent = STATUS_EMOJI[next]||'·';
}
// ── CPEng competency tags: propose (LLM) → accept (writes) ─────────────
// Follows the propose→accept split of .claude/rules/proposed-action.md: the
// suggester never writes, and every tag in the markdown was clicked. It does
// NOT adopt that rule's on-disk proposal store — a batch is cheap to regenerate
// and lives only in `_cpProposals`, so a reload discards it.
var _cpProposals = null;   // {date, items[], done:[]} — the last suggester reply
// renderCompetencyTags lives in the sibling competency-chips.js so tests/js can
// load it without this file's boot IIFE — see that file's header.
async function suggestCompetency(){
  var box = document.getElementById('cp-suggest-box');
  if(!box || box.dataset.busy) return;      // explicit, not the accidental guard
  box.dataset.busy = '1';
  var day = STATE.detailDate;               // pin the day across the await
  box.innerHTML = '<div class="muted">Reading the day&#39;s untagged items…</div>';
  var d;
  // errorState reads `message` only — passing title/detail renders the generic
  // "Something went wrong." and throws the diagnosis away.
  try { d = await post('/competency/suggest', {date: day, employer: STATE.employer||''}); }
  catch(e){
    box.innerHTML = EOS_UI.errorState({message:'Could not reach the suggester — '
      + String(e&&e.message||e), onRetry:'suggestCompetency()'});
    return;
  }
  if(!d || d.ok === false){
    box.innerHTML = EOS_UI.errorState({message:(d&&d.error)||'No response from the suggester',
      onRetry:'suggestCompetency()'});
    return;
  }
  var props = d.proposals || [];
  if(!props.length){
    // Two different outcomes: `reason` means there was nothing to consider (no
    // model ran); otherwise the model ran and declined — which the prompt asks
    // for, so it is a legitimate answer, not a failure.
    var msg = d.reason
      ? esc(d.reason) + ' — nothing to suggest.'
      : 'Nothing confidently maps to an element (considered ' + (d.considered||0)
        + '). Tag by hand with #cN.';
    box.innerHTML = '<div class="muted">'+msg+'</div>'
      + EOS_UI.provenanceLine(d.provenance, {suffix:' · proposed, not written'});
    return;
  }
  // The batch survives the accept-and-re-read below, so tagging five items
  // costs ONE think() call rather than five.
  _cpProposals = {date: day, items: props, provenance: d.provenance, done: []};
  renderCompetencyProposals();
}
// Called on every detail render, so re-reading the day after an accept restores
// the remaining cards instead of silently discarding them.
function renderCompetencyProposals(){
  var box = document.getElementById('cp-suggest-box');
  if(!box || !_cpProposals || _cpProposals.date !== STATE.detailDate) return;
  var left = _cpProposals.items.filter(function(_, i){ return _cpProposals.done.indexOf(i) < 0; });
  if(!left.length) return;
  var html = '<div class="section-label">Proposed CPEng evidence — nothing is written until you accept</div>';
  _cpProposals.items.forEach(function(pr, i){
    if(_cpProposals.done.indexOf(i) >= 0) return;
    html += '<div class="cp-prop" id="cp-prop-'+i+'"><div class="row">'
      + '<span class="tx">'+esc(pr.display||pr.text)+'</span>'
      + renderCompetencyTags(pr.elements, pr.focus)
      + '<button class="eos-btn eos-btn-sm eos-btn-primary" style="margin-left:auto" '
      + 'onclick="acceptCompetency('+i+')">Accept</button>'
      + '<button class="eos-btn eos-btn-sm eos-btn-ghost" onclick="dismissCompetency('+i+')">Dismiss</button>'
      + '</div>' + (pr.why ? '<div class="why">'+esc(pr.why)+'</div>' : '') + '</div>';
  });
  // provenanceLine() returns '' when think() carried no provenance — the honest
  // signal, so it is appended unconditionally rather than guarded here.
  box.innerHTML = html + EOS_UI.provenanceLine(_cpProposals.provenance,
                                               {suffix:' · proposed, not written'});
}
function dismissCompetency(i){
  if(_cpProposals) _cpProposals.done.push(i);
  var row = document.getElementById('cp-prop-'+i);
  if(row) row.remove();
}
async function acceptCompetency(i){
  if(!_cpProposals) return;
  var pr = _cpProposals.items[i];
  var row = document.getElementById('cp-prop-'+i);
  var btn = row && row.querySelector('.eos-btn-primary');
  if(!pr || (btn && btn.disabled)) return;
  if(btn) btn.disabled = true;
  try{
    // The proposal's OWN date, not STATE.detailDate — identical (project, text)
    // pairs across days are a designed-for case (applyCarryover copies text
    // verbatim), so a stale date would tag the wrong day's item.
    var r = await post('/competency/tag', {date: _cpProposals.date, project: pr.project,
                                           item: pr.text, elements: pr.elements});
    if(r && r.error){
      // The server matches on item text, so this is the staleness gate firing:
      // the item was edited or re-tagged since the proposal was made.
      EOS.toast&&EOS.toast(r.error === 'item not found'
        ? 'That item changed since it was proposed — run Suggest again'
        : r.error);
      if(btn) btn.disabled = false;
      return;
    }
    EOS.toast&&EOS.toast(r && r.unchanged ? 'Already tagged'
      : 'Tagged '+(pr.elements||[]).map(function(n){return '#c'+n;}).join(' '));
    _cpProposals.done.push(i);
    // Re-read so the item above shows its new chips from the file, not from
    // this reply — the markdown is the source of truth.
    showDetail(_cpProposals.date);
  }catch(e){
    EOS.toast&&EOS.toast('Tagging failed');
    if(btn) btn.disabled = false;
  }
}

// ── prose sections (Plan / Update): render-with-links + edit toggle ──
function renderProse(text){
  if(!(text||'').trim()) return '<div class="muted">—</div>';
  var html = esc(text);
  // [[target|alias]] / [[target]] → clickable KB link (/kb/#slug)
  html = html.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, function(_, tgt, alias){
    var slug = tgt.trim().split('#')[0];
    var label = (alias||tgt).trim();
    return '<a href="/kb/#'+encodeURIComponent(slug)+'" target="_blank" rel="noopener">'+esc(label)+'</a>';
  });
  html = html.replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br>');
  return '<p>'+html+'</p>';
}
function proseSection(label, text, field, canDraft){
  var has = !!(text||'').trim();
  var id = 'ps-'+field;
  // The Update is assembled from the day's items rather than typed from
  // scratch — that's the whole point of the ✨ button (see draftUpdate). With
  // no items there is nothing to summarise, so don't offer an action that can
  // only return an error.
  var draftable = field === 'update' && canDraft !== false;
  var draftBtn = draftable
    ? ' <button class="ps-edit" data-online-only onclick="draftUpdate(this)"'
      + ' title="Draft an end-of-day update from this day\'s work items">✨</button>'
    : '';
  var head = '<div class="section-label">'+label
    + (has ? ' <button class="ps-edit" onclick="editProse(\''+field+'\')" title="Edit '+label+'">✎</button>' : '')
    + draftBtn
    + '<span id="'+id+'-prov" class="ps-prov"></span>'
    + '</div>';
  var view = '<div id="'+id+'-view" class="prose-view'+(has?'':' hidden')+'">'+renderProse(text)+'</div>';
  var add  = '<button id="'+id+'-add" class="ps-add'+(has?' hidden':'')+'" onclick="editProse(\''+field+'\')" title="Add '+label+'">＋ Add '+label.toLowerCase()+'</button>'
    + (draftable && !has
        ? ' <button id="'+id+'-draftadd" class="ps-add" data-online-only onclick="draftUpdate(this)"'
          + ' title="Draft an end-of-day update from this day\'s work items">✨ Draft from today\'s work</button>'
        : '');
  var edit = '<div id="'+id+'-edit" class="hidden">'
    + '<textarea class="prose" id="'+id+'-ta" placeholder="'+label+'…" oninput="autoGrow(this)">'+esc(text||'')+'</textarea>'
    + '<div style="text-align:right;margin-top:6px">'
    +   '<button class="eos-btn eos-btn-sm eos-btn-secondary" onclick="cancelProse(\''+field+'\')" title="Discard these '+label+' edits">Cancel</button> '
    +   '<button class="eos-btn eos-btn-sm eos-btn-primary" onclick="saveProse(\''+field+'\')" title="Save '+label+'">Save</button></div>'
    + '</div>';
  return head + view + add + edit;
}
// Draft the Update from the day's items → open the editor prefilled. The user
// still presses Save: propose, never autofill.
async function draftUpdate(btn){
  var ta = document.getElementById('ps-update-ta');
  var existing = ((ta && ta.value) || (STATE.day && STATE.day.update) || '').trim();
  if(existing && !await EOS_UI.confirm({
      message:'Replace the current Update with a fresh draft from today\'s work items?',
      action:'Draft again'})) return;
  var label = btn ? btn.textContent : '';
  if(btn){ btn.disabled=true; btn.textContent='…'; }
  try{
    var d = await post('/update/draft', {date: STATE.detailDate});
    if(d.error){ EOS.toast&&EOS.toast(d.error); return; }
    editProse('update');
    ta = document.getElementById('ps-update-ta');
    if(ta){ ta.value = d.draft||''; ta.focus(); autoGrow(ta); }
    var prov = document.getElementById('ps-update-prov');
    if(prov && d.provenance && d.provenance.mode){
      prov.innerHTML = EOS_UI.provenance({
        mode:d.provenance.mode, provider:d.provenance.provider, model:d.provenance.model,
        title:'Drafted from '+d.items+' work item(s) — review before saving'});
    }
    EOS.toast&&EOS.toast('Drafted from '+d.items+' item(s) — review, then Save');
  }catch(e){ EOS.toast&&EOS.toast('Draft failed'); }
  finally{ if(btn){ btn.disabled=false; btn.textContent=label; } }
}

function editProse(field){
  var id = 'ps-'+field;
  // '-draftadd' too, or the empty-state ✨ button hangs above the open editor.
  ['-view','-add','-draftadd'].forEach(function(s){ var e=document.getElementById(id+s); if(e) e.classList.add('hidden'); });
  document.getElementById(id+'-edit').classList.remove('hidden');
  var ta = document.getElementById(id+'-ta'); if(ta) { ta.focus(); autoGrow(ta); }
}
// A drafted Update runs 3-4 sentences — at the 70px min-height it lands
// scrolled off the bottom, which reads as "the draft is truncated".
function autoGrow(ta){
  if(!ta) return;
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight + 2, 420) + 'px';
}
function cancelProse(field){ refreshDetail(); }
async function saveProse(field){
  await post('/'+field, {date:STATE.detailDate, text:document.getElementById('ps-'+field+'-ta').value});
  EOS.toast && EOS.toast((field==='plan'?'Plan':'Update')+' saved');
  refreshDetail();
}
async function refreshDetail(){
  try{ renderDetail(await api('/day?date='+encodeURIComponent(STATE.detailDate))); }
  catch(e){ renderLoadError(document.getElementById('view-detail'), e, 'refreshDetail()'); }
}
async function addToDay(){
  var text = document.getElementById('dt-text').value.trim(); if(!text) return;
  await post('/log', {date:STATE.detailDate, project:document.getElementById('dt-proj').value||'General',
    text:text, status:document.getElementById('dt-status').value, employer:STATE.employer});
  var d = await api('/day?date='+encodeURIComponent(STATE.detailDate)); renderDetail(d);
}

// ── attach a file (paste / file-picker) → the vault → a pending chip ──
// First browser-side consumer of /api/vault/write-bytes (routes_vault.py) —
// hash-verified, atomic, immutable-by-default, so pasting the same
// screenshot twice reuses the same file instead of duplicating it.
var _pendingAtts = [];   // [{path, name, isImage}] — not yet attached to a logged item

async function _readFileBytes(file){ return await file.arrayBuffer(); }
async function _sha256Hex(buf){
  var digest = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(digest)).map(function(b){ return b.toString(16).padStart(2,'0'); }).join('');
}
// Chunked to avoid "Maximum call stack size exceeded" from String.fromCharCode.apply
// over a multi-MB screenshot buffer in one call.
function _b64FromBuf(buf){
  var bytes = new Uint8Array(buf), chunk = 0x8000, binary = '';
  for(var i=0; i<bytes.length; i+=chunk) binary += String.fromCharCode.apply(null, bytes.subarray(i, i+chunk));
  return btoa(binary);
}
async function uploadAttachment(file){
  var buf = await _readFileBytes(file);
  var hex = await _sha256Hex(buf);
  var ext = (file.name.split('.').pop()||'bin').toLowerCase().replace(/[^a-z0-9]/g,'') || 'bin';
  // Content-hash filename — same bytes always resolve to the same path, so a
  // repeated paste/upload is a free no-op dedup rather than a new file.
  var path = '60_Worklogs/assets/'+new Date().getFullYear()+'/'+hex.slice(0,16)+'.'+ext;
  var d = await EOS.apiSafe('/api/vault/write-bytes', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path:path, content_base64:_b64FromBuf(buf), content_sha256:hex})});
  if(d.error) throw new Error(d.error);
  return {path: d.relative || path, name: file.name,
          isImage: /^image\//.test(file.type||'') || _isImageExt(file.name)};
}
function renderPendingAtts(){
  var host = document.getElementById('wl-pending-atts');
  if(!host) return;
  host.innerHTML = _pendingAtts.map(function(a, i){
    var thumb = a.isImage
      ? '<img src="/api/vault/file?path='+encodeURIComponent(a.path)+'" loading="lazy" alt="">' : '📎';
    return '<span class="wl-pend-chip">'+thumb+' '+esc(a.name)
      + '<button type="button" class="wl-pend-x" onclick="removePendingAtt('+i+')" title="Remove attachment">✕</button></span>';
  }).join('');
}
function removePendingAtt(i){ _pendingAtts.splice(i, 1); renderPendingAtts(); }
async function addPendingAttachment(file){
  if(!file) return;
  try{
    var att = await uploadAttachment(file);
    _pendingAtts.push(att);
    renderPendingAtts();
  }catch(e){ EOS.toast && EOS.toast('Attach failed: '+(e&&e.message||e)); }
}
function onAttachFilePicked(input){
  var files = Array.prototype.slice.call(input.files||[]);
  input.value = '';   // allow re-picking the same file
  files.forEach(addPendingAttachment);
}
// Fast path for the common "log this screenshot" flow — paste directly into
// the text field, no round trip through the file picker.
function _bindAttachPaste(){
  var ta = document.getElementById('wl-add-text');
  if(!ta) return;
  ta.addEventListener('paste', function(ev){
    var items = (ev.clipboardData && ev.clipboardData.items) || [];
    var handled = false;
    for(var i=0; i<items.length; i++){
      if(items[i].type && items[i].type.indexOf('image/') === 0){
        var f = items[i].getAsFile();
        if(f){ handled = true; addPendingAttachment(f); }
      }
    }
    if(handled) ev.preventDefault();   // don't also paste raw image bytes as text
  });
}

// ── add from timeline form ──
async function submitLog(){
  var text = document.getElementById('wl-add-text').value.trim();
  var attRefs = _pendingAtts.map(function(a){ return '![['+a.path+']]'; }).join(' ');
  var fullText = attRefs ? (text ? text+' '+attRefs : attRefs) : text;
  if(!fullText) return;
  var r = await post('/log', {date:document.getElementById('wl-date').value||'',
    project:document.getElementById('wl-proj').value||'General', text:fullText,
    status:document.getElementById('wl-status').value, employer:STATE.employer});
  if(r.error){ EOS.toast && EOS.toast(r.error); return; }
  document.getElementById('wl-add-text').value='';
  _pendingAtts = []; renderPendingAtts();
  EOS.toast && EOS.toast('Logged');
  loadProjects(); loadTimeline();
}
async function loadProjects(){
  var d = await api('/projects'+emp());
  var dl = document.getElementById('proj-list'); dl.innerHTML = '';
  PROJECT_IDS = {};
  (d.projects||[]).forEach(function(p){
    var o=document.createElement('option'); o.value=p.name; dl.appendChild(o);
    if(p.project_id) PROJECT_IDS[String(p.name||'').toLowerCase()] = p.project_id;
  });
}

// ── calendar ──
// monthGrid's DEFAULT renderCell draws tone-coloured dots and nothing else —
// it ignores `label` and sets no title — so this tab used to communicate every
// day's status by colour alone, which .claude/rules/list-card-density.md
// forbids ("Status must never be color-only"). The tone vocabulary also reads
// backwards here: a *complete* day paints muted grey and a *blocked* day paints
// red, with no word anywhere on the page saying so and no legend. Render the
// status word and the project names instead; the colour stays as reinforcement.
function calCellHtml(cell){
  var items = (cell && cell.items) || [];
  if(!items.length) return '';
  var status = items[0].status || '';
  var tone   = items[0].tone || '';
  var names  = items.map(function(it){ return it.label || ''; }).filter(Boolean);
  var tip = (status ? status.charAt(0).toUpperCase()+status.slice(1) : 'Logged')
    + (names.length ? ' — ' + names.join(', ') : '');
  var word = status
    ? '<span class="cal-status'+(tone?' tone-'+tone:'')+'">'+esc(status)+'</span>' : '';
  var projs = names.length
    ? '<span class="cal-projs">'+esc(names.slice(0,2).join(', '))
      + (names.length>2 ? ' +'+(names.length-2) : '')+'</span>' : '';
  return '<div class="cal-cell" title="'+escAttr(tip)+'">'+word+projs+'</div>';
}
async function loadCalendar(){
  var ym = (STATE.detailDate||isoDate()).slice(0,7);
  if(!_grid){
    _grid = EOS_UI.monthGrid({ mount:'#cal-grid', month:ym, cells:[],
      renderCell: calCellHtml,
      onDayClick:function(date){ openDetail(date); },
      onMonthChange:function(newYm){ loadMonthCells(newYm); } });
  }
  await loadMonthCells(ym);
}
async function loadMonthCells(ym){
  try{
    var d = await api('/month?month='+ym+emp());
    if(_grid) _grid.refresh(d.cells||[]);
  }catch(e){ renderLoadError(document.getElementById('cal-grid'), e, 'loadCalendar()'); }
}

// ── by project ──
async function loadProjectPicker(){
  var d;
  try{ d = await api('/projects'+emp()); }
  catch(e){ renderLoadError(document.getElementById('project-rollup'), e,
                            'loadProjectPicker()'); return; }
  var sel = document.getElementById('proj-pick');
  sel.innerHTML = '<option value="">Pick a project…</option>' + (d.projects||[]).map(function(p){
    return '<option value="'+escAttr(p.name)+'">'+esc(p.name)+' ('+p.items+')</option>'; }).join('');
}
async function loadByProject(){
  var name = document.getElementById('proj-pick').value;
  var el = document.getElementById('project-rollup');
  if(!name){ el.innerHTML=''; return; }
  var d;
  try{ d = await api('/by-project?name='+encodeURIComponent(name)+emp()); }
  catch(e){ renderLoadError(el, e, 'loadByProject()'); return; }
  if(!d.days || !d.days.length){ el.innerHTML='<div class="muted card">No entries.</div>'; return; }
  el.innerHTML = '<div class="muted" style="margin-bottom:8px">'+d.days.length+' days · '+esc(name)+'</div>' +
    d.days.map(function(day){
      var items = day.items.map(function(it){ var parsed=parseAttachments(it.text); return '<div class="item"><span class="em">'+(STATUS_EMOJI[it.status]||'·')+'</span><span class="tx">'+esc(parsed.clean)+'</span>'+renderAttachmentChips(parsed.paths)+'</div>'; }).join('');
      return '<div class="card"><div class="day-head"><span class="day-date" style="cursor:pointer" onclick="openDetail(\''+day.date+'\')">'+day.date+'</span><span class="day-week">'+esc(day.weekday)+'</span>'+(day.employer?'<span class="emp-chip">'+esc(day.employer)+'</span>':'')+'</div>'+items+'</div>';
    }).join('');
}

// ── search ──
// Escape FIRST, then wrap matches: highlighting before escaping would let
// item text inject markup through the <mark> we add.
function highlight(text, terms){
  var out = esc(text||'');
  (terms||[]).forEach(function(t){
    if(!t) return;
    var re = new RegExp('('+t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi');
    out = out.replace(re, '<mark class="sq-hit">$1</mark>');
  });
  return out;
}
// Cycle an item's status from a search row, without leaving the results — a
// backlog of 1,265 untagged items is only workable if tagging one costs a
// click, not a round trip through the day view.
async function cycleSearchStatus(ev, el){
  ev.stopPropagation();                       // the card itself opens the day
  var cur = (el.textContent || '').trim();
  var curStatus = Object.keys(STATUS_EMOJI).find(function(k){ return STATUS_EMOJI[k]===cur; }) || '';
  var next = CYCLE[(CYCLE.indexOf(curStatus)+1) % CYCLE.length];
  el.disabled = true;
  try{
    var r = await post('/status', {date:el.dataset.date, project:el.dataset.project,
                                   item:el.dataset.text, status:next});
    if(r && r.error){ EOS.toast&&EOS.toast(r.error); return; }
    el.textContent = STATUS_EMOJI[next] || '·';
    el.title = next ? ('Status: '+next+' — click to change') : 'No status — click to set one';
  }catch(e){ EOS.toast&&EOS.toast('Could not update status'); }
  finally{ el.disabled = false; }
}

var _searchSeq = 0;
async function runSearch(){
  var q = document.getElementById('sq').value.trim();
  var st = document.getElementById('sq-status').value;
  var el = document.getElementById('search-results');
  // A status filter alone is a valid search — that IS the untagged backlog pass.
  if(!q && !st){ el.innerHTML = '<div class="muted card">Type something to search '
    + 'your work items, plans and updates.</div>'; return; }
  var seq = ++_searchSeq;
  el.innerHTML = '<div class="muted card">Searching…</div>';
  var d;
  try{
    var clean = document.getElementById('sq-clean').checked;
    d = await api('/search?q='+encodeURIComponent(q)+(st?'&status='+encodeURIComponent(st):'')
                  +(clean?'&hide_debris=1':'')+emp());
  }catch(e){ if(seq===_searchSeq) renderLoadError(el, e, 'loadSearch()'); return; }
  if(seq !== _searchSeq) return;   // a newer query already answered
  if(d.error){ el.innerHTML = '<div class="muted card">'+esc(d.error)+'</div>'; return; }
  if(!d.count){
    el.innerHTML = '<div class="muted card">'
      + (st==='none' ? 'Every item has a status. Nothing left to tag.'
                     : 'No matches for “'+esc(q)+'”.') + '</div>';
    return;
  }
  var head = '<div class="muted" style="margin-bottom:8px">'
    + (st==='none' ? d.count+' item'+(d.count===1?'':'s')+' with no status — click the · to tag one'
                   : d.count+' match'+(d.count===1?'':'es'))
    + (d.truncated ? ' · showing the first '+d.results.length+', narrow the query to see the rest' : '')
    // Say what the filter removed — a quietly shortened list reads as
    // "that's all there is", and the toggle is how you get them back.
    + (d.hidden_debris ? ' · '+d.hidden_debris+' import fragment'
        +(d.hidden_debris===1?'':'s')+' hidden' : '')
    + '</div>';
  el.innerHTML = head + d.results.map(function(r){
    var badge = r.kind==='item'
      ? '<button class="em sq-cycle" onclick="cycleSearchStatus(event,this)"'
        + ' data-date="'+escAttr(r.date)+'" data-project="'+escAttr(r.project)+'"'
        // RAW text (incl. any ![[...]] tokens) — cycleSearchStatus posts it
        // back to /status, which matches verbatim against the stored item.
        + ' data-text="'+escAttr(r.text)+'"'
        + ' title="'+(r.status ? 'Status: '+escAttr(r.status)+' — click to change'
                               : 'No status — click to set one')+'">'
        + (STATUS_EMOJI[r.status]||'·')+'</button>'
      : '<span class="sq-kind">'+esc(r.kind)+'</span>';
    var meta = '<span class="day-date">'+r.date+'</span> <span class="day-week">'+esc(r.weekday)+'</span>'
      + (r.project?' <span class="proj-badge">'+esc(r.project)+'</span>':'')
      + (r.employer?' <span class="emp-chip">'+esc(r.employer)+'</span>':'');
    var parsed = parseAttachments(r.text);
    return '<div class="card day-card" onclick="openDetail(\''+r.date+'\')" title="Open '+r.date+'">'
      + '<div class="day-head">'+meta+'</div>'
      + '<div class="item" style="cursor:pointer">'+badge
      + '<span class="tx">'+highlight(parsed.clean, d.terms)+'</span>'+attachmentMarker(parsed.paths)+'</div></div>';
  }).join('');
}
function loadSearch(){
  var sel = document.getElementById('sq-status');
  if(sel.options.length <= 1){
    Object.keys(STATUS_EMOJI).forEach(function(k){
      var o=document.createElement('option'); o.value=k; o.textContent=STATUS_EMOJI[k]+' '+k; sel.appendChild(o);
    });
  }
  // Opening the tab left a blank pane until the first query — say what this
  // searches, so an empty result area never reads as "nothing found".
  var el = document.getElementById('search-results');
  if(!el.innerHTML.trim()){
    el.innerHTML = '<div class="muted card">Search every work item, plan and update. '
      + 'All terms must match — try a project name, a number like <b>12345</b>, or a person.'
      + '<div style="margin-top:8px">Or pick <b>No status yet</b> to work through items '
      + 'that were never tagged — they stay invisible to the blocked and in-review views.</div></div>';
  }
  document.getElementById('sq').focus();
}

// ── heatmap + rollup ──
async function loadHeatmap(){
  try{
    var d = await api('/heatmap'+emp());
    EOS_UI.yearHeatmap({ mount:'#heatmap', data:d.data||{}, months:14, showStats:true, showMonthLabels:true,
      intensity:function(c){ return c===0?0:c<=2?1:c<=5?2:c<=10?3:4; },
      tooltipFor:function(date,count){ return date+': '+count+' items'; },
      onCellClick:function(date,count){ if(count) openDetail(date); } });
  }catch(e){ renderLoadError(document.getElementById('heatmap'), e, 'loadHeatmap()'); }
  var r = await api('/status-rollup?days=21'+emp());
  var el = document.getElementById('status-rollup');
  var totals = Object.keys(r.totals||{}).map(function(k){ return STATUS_EMOJI[k]+' '+k+': '+r.totals[k]; }).join(' &nbsp; ');
  var h = '<div class="section-label">Last 21 days</div><div class="muted" style="margin-bottom:10px">'+(totals||'No items')+'</div>';
  if((r.blocked||[]).length){ h += '<div class="section-label">⛔ Blocked</div>'+r.blocked.map(function(b){return '<div class="item" onclick="openDetail(\''+b.date+'\')"><span class="tx">'+esc(b.project)+' — '+esc(parseAttachments(b.text).clean)+' <span class="day-week">'+b.date+'</span></span></div>';}).join(''); }
  if((r.review||[]).length){ h += '<div class="section-label">👀 In review</div>'+r.review.map(function(b){return '<div class="item" onclick="openDetail(\''+b.date+'\')"><span class="tx">'+esc(b.project)+' — '+esc(parseAttachments(b.text).clean)+' <span class="day-week">'+b.date+'</span></span></div>';}).join(''); }
  el.innerHTML = h;
}

// -- portable data transfer (live <-> standalone) --
function openDataTools(){
  EOS_UI.modal({
    title:'Work Log data', width:'560px',
    body:'<p class="muted" style="margin-bottom:12px">Move work between EmptyOS and the standalone HTML. Importing shows what would change first — nothing is written until you press Apply.</p>'
      + '<div style="display:flex;gap:8px;flex-wrap:wrap">'
      + '<button class="eos-btn eos-btn-primary" onclick="exportPortableData()" title="Download all Work Log days as portable JSON">Export JSON</button>'
      + '<button class="eos-btn eos-btn-secondary" onclick="choosePortableImport()" title="Merge a portable or standalone backup JSON file">Import JSON</button>'
      + '</div><div id="wl-import-receipt" style="margin-top:12px"></div>'
  });
}
async function exportPortableData(){
  try{
    var doc = await api('/portable');
    if(doc.error) throw new Error(doc.error);
    var blob = new Blob([JSON.stringify(doc,null,2)], {type:'application/json'});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'worklog-'+isoDate()+'.json';
    document.body.appendChild(a); a.click();
    setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 500);
    EOS.toast&&EOS.toast('Work Log data exported');
  }catch(e){ EOS.toast&&EOS.toast('Export failed: '+e.message); }
}
var _pendingImport = null;
function choosePortableImport(){
  var input=document.createElement('input');
  input.type='file'; input.accept='.json,application/json'; input.style.display='none';
  input.onchange=async function(){
    var file=input.files&&input.files[0]; input.remove(); if(!file)return;
    if(file.size>20*1024*1024){ EOS.toast&&EOS.toast('Import file is larger than 20 MB'); return; }
    try{
      var doc=JSON.parse(await file.text());
      var r=await post('/import/preview',{document:doc});
      if(r.error) throw new Error(r.error);
      _pendingImport=doc;
      renderImportReceipt(r);
    }catch(e){ _pendingImport=null; EOS.toast&&EOS.toast('Import failed: '+e.message); }
  };
  document.body.appendChild(input); input.click();
}
async function applyPortableImport(btn){
  if(!_pendingImport) return;
  if(btn){ btn.disabled=true; btn.textContent='Applying…'; }
  try{
    var r=await post('/import',{document:_pendingImport});
    if(r.error) throw new Error(r.error);
    _pendingImport=null;
    renderImportReceipt(r);
    await refreshAfterImport();
    EOS.toast&&EOS.toast('Work Log import complete');
  }catch(e){
    if(btn){ btn.disabled=false; btn.textContent='Apply import'; }
    EOS.toast&&EOS.toast('Import failed: '+e.message);
  }
}
function renderImportReceipt(r){
  var el=document.getElementById('wl-import-receipt'); if(!el)return;
  var changed=(r.days_created||0)+(r.days_updated||0);
  var preview=!!r.preview;
  var conflicts=(r.conflicts||[]).map(function(c){return esc(c.date)+': '+esc((c.fields||[]).join(', '));});
  var rows=(r.changes||[]).slice(0,12).map(function(c){
    var bits=[];
    if(c.items_added) bits.push(c.items_added+' item'+(c.items_added===1?'':'s'));
    if(c.statuses_updated) bits.push(c.statuses_updated+' status'+(c.statuses_updated===1?'':'es'));
    if(c.timesheet_added) bits.push(c.timesheet_added+' timesheet');
    if(c.notes_added) bits.push(c.notes_added+' note'+(c.notes_added===1?'':'s'));
    if(c.prose_added) bits.push(c.prose_added+' prose');
    return '<div class="muted">'+(c.created?'＋ ':'· ')+esc(c.date)+' — '+esc(bits.join(', ')||'no change')+'</div>';
  });
  var more=(r.changes||[]).length-rows.length;
  el.innerHTML='<div class="prose-view"><b>'+changed+' day'+(changed===1?'':'s')+(preview?' will change':' changed')+'</b>'
    +'<div class="muted" style="margin-top:4px">'+(r.items_added||0)+' item'+((r.items_added||0)===1?'':'s')+' &middot; '+(r.statuses_updated||0)+' status change'+((r.statuses_updated||0)===1?'':'s')+' &middot; '+(r.days_unchanged||0)+' unchanged</div>'
    +(rows.length?'<div style="margin-top:8px">'+rows.join('')+(more>0?'<div class="muted">… +'+more+' more</div>':'')+'</div>':'')
    +(conflicts.length?'<div style="color:var(--warning);margin-top:8px"><b>Will keep live values for:</b><br>'+conflicts.join('<br>')+'</div>':'')
    +(preview&&changed?'<div style="margin-top:12px"><button class="eos-btn eos-btn-primary" onclick="applyPortableImport(this)" title="Merge these changes into the vault">Apply import</button> <button class="eos-btn eos-btn-secondary" onclick="cancelPortableImport()" title="Discard this import">Cancel</button></div>':'')
    +(preview&&!changed?'<div class="muted" style="margin-top:8px">Nothing to apply.</div>':'')
    +'</div>';
}
function cancelPortableImport(){
  _pendingImport=null;
  var el=document.getElementById('wl-import-receipt'); if(el) el.innerHTML='';
}
async function refreshAfterImport(){
  STATE.day=null;
  await loadEmployers(); await loadProjects();
  if(STATE.detailDate) await refreshDetail(); else loadView(STATE.view);
}

// -- settings --
// Fields come from the manifest's [provides.settings] schema — one declaration,
// so a setting can't reach /settings and miss this panel (.claude/rules/app-ui-patterns.md).
var _appSettings = EOS_UI.settingsPanel({
  id:'worklog-settings', title:'Work Log Settings', app:'worklog',
});
function openAppSettings(){ _appSettings.open(); }

// ── companion sidebar (page-assistant.js): drive the page by chat ──
function _focusDate(){ return STATE.detailDate || (STATE.day && STATE.day.date) || isoDate(); }
async function _ensureDay(){
  if(STATE.day && STATE.day.date===_focusDate()) return STATE.day;
  STATE.day = await api('/day?date='+encodeURIComponent(_focusDate())); return STATE.day;
}
function _showAfterWrite(dt){ if(STATE.detailDate!==dt) openDetail(dt); else refreshDetail(); }
// page context for the model — page-assistant concatenates this as a STRING
EOS.getPageMetrics = function(){
  var d = STATE.day; if(!d) return 'No day loaded yet.';
  var lines = ['Date: '+d.date+(d.employer?' ('+d.employer+')':'')];
  var any=false;
  (d.projects||[]).forEach(function(g){ (g.items||[]).forEach(function(it){
    any=true; lines.push('- ['+(it.status||'note')+'] '+g.project+': '+it.text); }); });
  if(!any) lines.push('(no work items logged yet)');
  if((d.plan||'').trim()) lines.push('Plan: '+d.plan.trim());
  if((d.update||'').trim()) lines.push('Update: '+d.update.trim());
  return lines.join('\n');
};
// extract the model's DRAFTED prose from a fenced ``` block in a recent reply
function _extractDraft(){
  var msgs=document.querySelectorAll('.pa-msg.assistant');
  for(var i=msgs.length-1; i>=0 && i>=msgs.length-3; i--){
    var t=(msgs[i].innerHTML||'').replace(/<br\s*\/?>/gi,'\n').replace(/<[^>]+>/g,'');
    var ta=document.createElement('textarea'); ta.innerHTML=t; t=ta.value;
    var m=t.match(/```[^\n]*\n([\s\S]*?)```/g);
    if(m&&m.length) return m[m.length-1].replace(/^```[^\n]*\n/,'').replace(/```\s*$/,'').trim();
    var inl=t.match(/```([^`]+)```/g);
    if(inl&&inl.length) return inl[inl.length-1].replace(/```/g,'').trim();
  }
  return '';
}
// client action: mark a work item by fuzzy text + status  (auto-applies)
async function wlSetStatus(param){
  var parts=String(param||'').split('|');
  var query=(parts[0]||'').trim().toLowerCase();
  var status=(parts[1]||'complete').trim().toLowerCase();
  if(!query){ EOS.toast&&EOS.toast('No item specified'); return; }
  var d=await _ensureDay(); var hit=null;
  (d.projects||[]).forEach(function(g){ (g.items||[]).forEach(function(it){
    if(!hit && it.text.toLowerCase().indexOf(query)>=0) hit={project:g.project,text:it.text}; }); });
  if(!hit){ EOS.toast&&EOS.toast('No item matching “'+query+'”'); return; }
  await post('/status', {date:d.date, project:hit.project, item:hit.text, status:status});
  EOS.toast&&EOS.toast('Marked '+status+': '+hit.text.slice(0,40));
  _showAfterWrite(d.date);
}
// client action: save the model's drafted Plan/Update prose  (confirm via button)
async function applyProse(field){
  field=(field==='update')?'update':'plan';
  var text=_extractDraft();
  if(!text){
    var dt=_focusDate();
    if(STATE.detailDate===dt) editProse(field);
    else { openDetail(dt); setTimeout(function(){ try{ editProse(field); }catch(e){} }, 350); }
    EOS.toast&&EOS.toast('No draft found — opening the '+field+' editor');
    return;
  }
  var dt=_focusDate(); await post('/'+field, {date:dt, text:text});
  EOS.toast&&EOS.toast((field==='plan'?'Plan':'Update')+' saved');
  _showAfterWrite(dt);
}
// client action: log a new item from the model's drafted text  (confirm via button)
async function wlLogFromDraft(project){
  var text=_extractDraft();
  if(!text){ EOS.toast&&EOS.toast('No draft item found'); return; }
  var dt=_focusDate(); await post('/log', {date:dt, project:(project||'General'),
    text:text, status:'in-progress', employer:STATE.employer});
  EOS.toast&&EOS.toast('Logged: '+text.slice(0,40));
  _showAfterWrite(dt);
}
function wlSwitchTab(view){ if(TAB_VIEWS.indexOf(view)>=0) switchTab(view); }
function wlOpenDay(dateS){ if(/^\d{4}-\d{2}-\d{2}$/.test(String(dateS||'').trim())) openDetail(dateS.trim()); }

if(window.EOS && EOS.registerActions){
  EOS.registerActions(
    {
      wlSetStatus:  function(p){ wlSetStatus(p); },
      applyProse:   function(f){ applyProse(f); },
      wlLogFromDraft:function(p){ wlLogFromDraft(p); },
      wlSwitchTab:  function(v){ wlSwitchTab(v); },
      wlOpenDay:    function(d){ wlOpenDay(d); },
    },
    [
      {name:'wlSetStatus', description:'Mark a work item by status (applies immediately). Param is "query|status" where query is a few distinctive words from the item text and status is one of: complete, blocked, in-progress, review, waiting, todo, next. Example: [ACTION:wlSetStatus(incomer|complete)]', params:['query|status']},
      {name:'applyProse', description:'Save DRAFTED Plan or Update prose. STEP 1: write the prose inside a fenced ``` code block ```. STEP 2: emit the button EXACTLY as [BUTTON:Save as today\'s plan|applyProse(plan)] (or applyProse(update) for the Update) — the action name goes directly after the | with NO action(...) wrapper. The page reads your fenced draft and saves it.', params:['plan|update']},
      {name:'wlLogFromDraft', description:'Log a new work item from a draft. STEP 1: write the item text inside a fenced ``` code block ```. STEP 2: emit EXACTLY [BUTTON:Add to <Project>|wlLogFromDraft(<Project>)] — action name directly after the |, no action(...) wrapper. Param is the project name.', params:['project']},
      {name:'wlSwitchTab', description:'Switch the worklog view tab', params:['timeline|calendar|project|heatmap']},
      {name:'wlOpenDay', description:'Open a specific day in detail (YYYY-MM-DD)', params:['date']},
    ],
    {
      description:'Your professional work log. Items are grouped by project and tagged with a status (🔄 in-progress, ✅ complete, ⛔ blocked, 👀 review, ⏳ waiting). You can mark items, log new items, and draft today\'s Plan/Update. Use the live_data in context to see today\'s items before acting.',
      quickActions:[
        {label:'What\'s open today?', msg:'Which work items are still open today? List them by project.'},
        {label:'Summarise today', msg:'Summarise what I did today based on the work items.'},
        {label:'Draft today\'s plan', msg:'Draft a short Plan for today from my open items. Put the plan in a fenced code block and give me a button to save it.'},
      ],
    }
  );
}

// ── boot ──
(async function(){
  if(window.EOS_IS_EXPORT){
    document.querySelectorAll('[data-online-only]').forEach(function(el){
      el.disabled=true;
      el.title='Requires the EmptyOS daemon';
      el.setAttribute('aria-disabled','true');
    });
    window.addEventListener('eos-export:data-loaded', function(){ refreshAfterImport(); });
  }
  document.getElementById('wl-date').value = isoDate();
  // Last-resort honesty net. The panes above surface their own errorState;
  // this catches the remaining action-shaped awaits (log, status cycle, prose
  // save, timer, import) so a failure is never silent — the failure mode this
  // replaces was a click that appeared to do nothing at all.
  window.addEventListener('unhandledrejection', function(ev){
    var msg = (ev && ev.reason && ev.reason.message) ? String(ev.reason.message) : '';
    if(EOS.toast) EOS.toast(msg ? ('Failed: ' + msg) : 'That action failed.');
  });
  // Which model is about to spend the budget, and click-to-switch. Mount
  // criteria met: two user-initiated think paths (Rollup, ✨ smart-parse) plus
  // the Update draft. See .claude/rules/model-pill.md.
  if(!window.EOS_IS_EXPORT && EOS_UI.modelPill){
    try{ EOS_UI.modelPill({app:'worklog', mount:'#model-pill', domain:'text'}); }catch(e){}
  }
  // Both are awaited because later renders depend on them (loadProjects fills
  // PROJECT_IDS for the deep-link), but neither may be FATAL: an unguarded
  // throw here aborts the whole boot IIFE, so one failing chrome fetch used to
  // take out the timeline, the timer and the board link with it.
  try{ await loadEmployers(); }catch(e){ console.warn('worklog: employers unavailable', e); }
  try{ await loadProjects(); }catch(e){ console.warn('worklog: project list unavailable', e); }
  loadTimeline();                 // populate timeline (behind detail if deep-linked)
  if(_route.current()) _route.init();   // deep link → onShow → showDetail
  else { try{ STATE.day = await api('/day?date='+isoDate()); }catch(e){} }  // prime companion context
  if(!window.EOS_IS_EXPORT){
    loadTimerStatus();
    loadBoardLink();
    // Server resync every 25s (clock-in on another tab/session, etc.) — the
    // 1s tick inside loadTimerStatus() only re-renders locally in between.
    setInterval(loadTimerStatus, 25000);
    // /api/vault/write-bytes needs the daemon — no offline/exported-bundle
    // equivalent, matching the other data-online-only affordances above.
    _bindAttachPaste();
  }
})();
