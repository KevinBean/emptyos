// kb — page logic, extracted verbatim from pages/index.html (P4 Atomic split,
// .claude/rules/multi-module-apps.md frontend pattern). Loaded at the same
// end-of-body position as the old inline <script>, so global scope and load
// order vs eos.js / eos-components.js are unchanged.
EOS.nav('kb');

var STATE = {
  domains:[], notes:[], filter:{domain:'',kind:'',q:''}, detail:null,
  bucket:'all',                                         // all | recent | linked | attention
  sort:'az',                                            // az | recent | linked
  visibleCount:40,                                      // windowing cap for the current view
  detailTabs:{ rendered:{}, can_flipbook:false },       // per-note lazy-render bookkeeping
  view:'list',                                          // active pane: list | network
  network:{ loaded:false, instance:null, nodesDS:null, edgesDS:null, raw:null, card:null },
  embedEnabled:false,                                   // viz-artifact embed feature flag
};
var WINDOW_STEP = 40;

function esc(s){ return String(s||'').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function loadAll(){
  var [d, n] = await Promise.all([
    fetch('/kb/api/domains').then(r=>r.json()),
    // enrich=1 → each note carries modified/updated/backlink_count/health_flag
    // for the Recent / Most-linked / Needs-attention buckets + sort.
    fetch('/kb/api/notes?enrich=1').then(r=>r.json()),
  ]);
  STATE.domains = d.domains || [];
  STATE.notes = n.notes || [];
  // Viz-artifact embed feature flag — gates the "Embed viz" affordance so a
  // disabled deployment shows no dead button. Fire-and-forget.
  fetch('/kb/api/embed/enabled').then(r=>r.json()).then(function(j){
    STATE.embedEnabled = !!(j && j.enabled);
  }).catch(function(){});
  document.getElementById('kb-desc').textContent =
    STATE.notes.length + ' notes across ' + STATE.domains.length + ' domain' + (STATE.domains.length===1?'':'s');
  renderDomainChips();
  // Browse-by-domain: open on desktop (summary hidden there), collapsed on mobile.
  var _dm = document.getElementById('kb-domains');
  if(_dm) _dm.open = window.innerWidth > 640;
  populateNetDomains();
  updateBucketCounts();
  renderNotes();
  _route.init();
  // If no detail hash, apply the initial view-mode (list/network/flipbook).
  // hideDetail() already calls applyView when the user closes a detail view.
  if(!window.location.hash || window.location.hash.length <= 1){
    applyView(STATE.view);
  }
}

function renderDomainChips(){
  var g = document.getElementById('domain-grid');
  var sum = document.getElementById('kb-domains-summary');
  if(!STATE.domains.length){
    g.innerHTML = '<div class="empty" style="padding:16px">No KB notes yet. Tag vault notes with <code>kb</code> + <code>domain:</code> + <code>kind:</code>.</div>';
    if(sum) sum.textContent = 'Domains';
    return;
  }
  if(sum) sum.textContent = 'Domains (' + STATE.domains.length + ')';
  g.innerHTML = STATE.domains.map(d => {
    var active = STATE.filter.domain === d.domain;
    return '<button class="kb-domain-chip'+(active?' active':'')+'" data-domain="'+escAttr(d.domain)+'" type="button" aria-pressed="'+(active?'true':'false')+'">' +
           esc(d.domain.replace(/-/g,' ')) +
           '<span class="kb-chip-n">'+d.total+'</span></button>';
  }).join('');
  g.querySelectorAll('.kb-domain-chip').forEach(el => {
    el.addEventListener('click', () => {
      var dm = el.dataset.domain;
      STATE.filter.domain = (STATE.filter.domain === dm) ? '' : dm;
      STATE.visibleCount = WINDOW_STEP;
      renderDomainChips(); renderNotes();
    });
  });
}

function populateNetDomains(){
  var sel = document.getElementById('net-domain');
  if(!sel) return;
  var cur = sel.value;
  sel.innerHTML = '<option value="">All domains</option>' +
    STATE.domains.map(d => '<option value="'+escAttr(d.domain)+'">'+esc(d.domain)+'</option>').join('');
  sel.value = cur;
}

// Filter + bucket + sort over the loaded corpus (all client-side over one fetch).
function _matchedNotes(){
  var f = STATE.filter;
  var q = (f.q||'').toLowerCase().trim();
  // Tokenized AND match: every whitespace-separated term must appear somewhere
  // in slug/topic/title/name. A whole-phrase substring match made natural
  // multi-word queries ("iec 60287 ampacity") miss notes whose title spreads
  // the words apart ("IEC 60287-1-1:2023 §1.4 — Ampacity master equation").
  var qTokens = q ? q.split(/\s+/).filter(Boolean) : [];
  var rows = STATE.notes.filter(n => {
    if(f.domain && n.domain !== f.domain) return false;
    if(f.kind && n.kind !== f.kind) return false;
    if(!qTokens.length) return true;
    var hay = (n.slug+' '+(n.topic||'')+' '+(n.title||'')+' '+(n.name||'')).toLowerCase();
    return qTokens.every(tok => hay.indexOf(tok) !== -1);
  });
  if(STATE.bucket === 'attention') rows = rows.filter(n => n.health_flag);
  // Recent / Most-linked buckets imply a sort; otherwise the sort select drives it.
  var sortKey = STATE.bucket === 'recent' ? 'recent'
              : STATE.bucket === 'linked' ? 'linked'
              : STATE.sort;
  rows = rows.slice();
  if(sortKey === 'recent'){
    rows.sort((a,b) => (b.modified||0) - (a.modified||0));
  } else if(sortKey === 'linked'){
    rows.sort((a,b) => (b.backlink_count||0) - (a.backlink_count||0)
      || (a.slug||'').localeCompare(b.slug||''));
  } else {
    rows.sort((a,b) => (a.title||a.name||a.slug||'').toLowerCase()
      .localeCompare((b.title||b.name||b.slug||'').toLowerCase()));
  }
  return rows;
}

function _noteRowHtml(n){
  var displayName = n.title || (n.name && n.name !== n.slug ? n.name : (n.slug||'').replace(/-/g,' '));
  var kindLabel = n.kind || '—';
  var domain = (n.domain || 'uncategorized').replace(/-/g,' ');
  var col = kindColor(n.kind);
  var flag = n.health_flag
    ? '<span class="note-flag '+escAttr(n.health_flag)+'" title="'+(n.health_flag==='error'?'Has an error — see Health':'Needs attention — see Health')+'"></span>'
    : '';
  var blc = (typeof n.backlink_count === 'number' && n.backlink_count > 0)
    ? '<span class="nr-meta" title="backlinks">↩ '+n.backlink_count+'</span>' : '';
  // Kind is the colored left edge + a small label; title leads (the scan target).
  return '<div class="note-row" data-slug="'+escAttr(n.slug)+'" style="border-left-color:'+col+'">' +
    '<div class="nr-main"><span class="nr-title">'+esc(displayName)+'</span>'+flag+'</div>' +
    '<div class="nr-sub">' +
      '<span class="nr-kind" style="color:'+col+'">'+esc(kindLabel)+'</span>' +
      '<span class="nr-meta">'+esc(domain)+'</span>' + blc +
    '</div>' +
  '</div>';
}

var _ioSentinel = null;
function renderNotes(){
  var list = document.getElementById('note-list');
  var rows = _matchedNotes();
  document.getElementById('count').textContent = rows.length + ' / ' + STATE.notes.length;
  // Name the active view in the result bar (search > domain > bucket > all).
  var rt = document.getElementById('kb-result-title');
  if(rt){
    rt.textContent = STATE.filter.q ? ('Search: “'+STATE.filter.q+'”')
      : STATE.filter.domain ? STATE.filter.domain.replace(/-/g,' ')
      : STATE.bucket === 'recent' ? 'Recently updated'
      : STATE.bucket === 'linked' ? 'Most linked'
      : STATE.bucket === 'attention' ? 'Needs attention'
      : 'All notes';
  }
  if(!rows.length){
    var hasFilter = !!(STATE.filter.q || STATE.filter.domain || STATE.filter.kind || STATE.bucket !== 'all');
    list.innerHTML = '<div class="empty">No notes match'
      + (STATE.bucket==='attention' ? ' — nothing needs attention 🎉' : (hasFilter ? ' the current filters.' : '.'))
      + (hasFilter ? '<div style="margin-top:10px"><button class="eos-btn eos-btn-sm" id="kb-clear-filters" type="button">Clear filters</button></div>' : '')
      + '</div>';
    var cf = document.getElementById('kb-clear-filters');
    if(cf) cf.addEventListener('click', function(){
      STATE.filter = {domain:'', kind:'', q:''};
      var qEl = document.getElementById('filter-q');
      if(qEl){ qEl.value = ''; document.getElementById('kb-search').classList.remove('has-q'); }
      var kEl = document.getElementById('filter-kind'); if(kEl) kEl.value = '';
      STATE.visibleCount = WINDOW_STEP;
      renderDomainChips();
      setBucket('all');
    });
    return;
  }
  var cap = Math.min(STATE.visibleCount, rows.length);
  var html = rows.slice(0, cap).map(_noteRowHtml).join('');
  if(cap < rows.length){
    html += '<div class="kb-load-more" id="kb-load-more">Showing '+cap+' of '+rows.length+' — scroll for more</div>';
  }
  list.innerHTML = html;
  list.querySelectorAll('.note-row').forEach(el => {
    el.addEventListener('click', () => _route.set(el.dataset.slug));
  });
  // Lazy windowing — bump the cap when the sentinel scrolls into view.
  if(_ioSentinel){ _ioSentinel.disconnect(); _ioSentinel = null; }
  var more = document.getElementById('kb-load-more');
  if(more && 'IntersectionObserver' in window){
    _ioSentinel = new IntersectionObserver(function(entries){
      if(entries.some(e => e.isIntersecting)){ STATE.visibleCount += WINDOW_STEP; renderNotes(); }
    }, {rootMargin:'400px'});
    _ioSentinel.observe(more);
  }
}

// Launchpad tiles — Recently updated / Most linked / Needs attention. Each is a
// quick view: clicking sets the bucket; clicking the active one toggles back to All.
function renderTiles(){
  var el = document.getElementById('kb-tiles');
  if(!el) return;
  var now = Date.now();
  function _ms(t){ t = +t || 0; return t ? (t < 1e12 ? t * 1000 : t) : 0; }  // epoch s|ms → ms
  // Tile counts are "notable" subsets, not saturated totals: edited this week,
  // and well-connected hubs (>=3 backlinks) — otherwise "most linked" ≈ all notes.
  var recent7 = STATE.notes.filter(function(n){ var m = _ms(n.modified); return m && (now - m) < 7*86400000; }).length;
  var hubsN   = STATE.notes.filter(function(n){ return (n.backlink_count || 0) >= 3; }).length;
  var attN    = STATE.notes.filter(function(n){ return n.health_flag; }).length;
  var tiles = [
    { bucket:'recent',    icon:'🕔', label:'Updated this week', n:recent7 },
    { bucket:'linked',    icon:'🔗', label:'Linked hubs',       n:hubsN },
    { bucket:'attention', icon:'⚠️', label:'Needs attention',   n:attN, warn:true },
  ];
  el.innerHTML = tiles.map(function(t){
    var active = STATE.bucket === t.bucket;
    return '<button class="kb-tile'+(t.warn?' warn':'')+(active?' active':'')+'" data-bucket="'+escAttr(t.bucket)+'" type="button" aria-pressed="'+(active?'true':'false')+'">' +
      '<span class="kb-tile-n">'+t.n+'</span>' +
      '<span class="kb-tile-label"><span class="kb-tile-icon">'+t.icon+'</span> '+esc(t.label)+'</span>' +
    '</button>';
  }).join('');
  el.querySelectorAll('.kb-tile').forEach(function(b){
    b.addEventListener('click', function(){
      var bk = b.dataset.bucket;
      setBucket(STATE.bucket === bk ? 'all' : bk);
    });
  });
}

function updateBucketCounts(){ renderTiles(); }

function setBucket(b){
  STATE.bucket = b;
  STATE.visibleCount = WINDOW_STEP;
  renderTiles();  // reflect the active tile
  // Keep the sort select honest about the bucket's implied ordering.
  var selEl = document.getElementById('filter-sort');
  if(selEl){
    if(b === 'recent') selEl.value = 'recent';
    else if(b === 'linked') selEl.value = 'linked';
    else selEl.value = STATE.sort;
  }
  renderNotes();
}

function kindColor(k){
  switch((k||'').toLowerCase()){
    case 'formula':   return 'var(--accent)';
    case 'concept':   return 'var(--text-secondary)';
    case 'reference': return 'var(--warning)';
    case 'clause':    return 'var(--text-muted)';
    case 'case':      return 'var(--success)';
    case 'lesson':    return 'var(--danger)';
    case 'doc':       return 'var(--accent)';
    case 'moc':       return 'var(--accent)';
    default:          return 'var(--text-muted)';
  }
}

function linkRow(slug, kind, opts){
  opts = opts || {};
  var color = kindColor(kind);
  var label = String(slug).replace(/-/g,' ');
  var miniKind = kind ? '<span class="mini-kind" style="background:color-mix(in srgb,'+color+' 18%,transparent);color:'+color+'">'+esc(kind)+'</span>' : '<span class="mini-kind" style="background:color-mix(in srgb,var(--text-muted) 12%,transparent);color:var(--text-muted)">·</span>';
  if(opts.external){
    return '<div class="item-link external" title="Not in KB">'+miniKind+'<span class="item-slug">'+esc(label)+'</span></div>';
  }
  return '<a class="item-link" href="#'+encodeURIComponent(slug)+'" data-wiki="'+escAttr(slug)+'">'+miniKind+'<span class="item-slug">'+esc(label)+'</span></a>';
}

function _slugifyHeading(text, used){
  var base = (text || '').toLowerCase().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-')
    .replace(/-+/g, '-').replace(/^-|-$/g, '') || 'section';
  var s = base, i = 2;
  while (used[s]) { s = base + '-' + i; i++; }
  used[s] = true;
  return s;
}

// TOC rail. For a reference note → a table of contents of the whole DOCUMENT:
// every clause note of THIS revision, grouped by chapter; clicking opens the
// clause note. For any other note → an "On this page" scroll-spy of its own
// headings. (The reference note's body headings are secondary on a landing page.)
function buildToc(data){
  var toc = document.getElementById('detail-toc');
  if (!toc) return;
  var p = (data && data.properties) || {};
  if (p.kind === 'reference' && Array.isArray(data.clauses) && data.clauses.length) {
    if (buildDocumentToc(toc, data, p)) return;
  }
  buildHeadingToc(toc);
}

function _clauseTocLabel(c){
  var t = c.clause_title || '';
  if (!t) {
    var parts = String(c.title || '').split(/\s[—–-]\s/);
    t = parts.length > 1 ? parts[parts.length - 1] : (c.title || c.slug || '');
  }
  return (c.clause ? '§' + c.clause + ' ' : '') + t;
}

// Document table of contents: every clause of THIS revision, as a collapsible
// chapter→clause tree — each chapter a real title from the standard's own TOC
// (data.chapters, falling back to bare "Chapter N"), each clause a link that
// opens the clause note. Returns false if too few to bother.
function buildDocumentToc(toc, data, p){
  var ed = String(p.edition || '').trim().toLowerCase();
  var clauses = data.clauses.filter(function(c){
    return !ed || !c.edition || String(c.edition).trim().toLowerCase() === ed;
  });
  if (clauses.length < 2) return false;
  // Chapter-number → title map from the reference payload (empty when the
  // standard has no parseable archive TOC → labels stay bare "Chapter N").
  var titleMap = {};
  (data.chapters || []).forEach(function(ch){ if (ch && ch.no != null) titleMap[String(ch.no)] = ch.title || ''; });
  var groups = {}, order = [];
  clauses.forEach(function(c){
    var m = String(c.clause || '').match(/^\s*(\d+)/);
    var ch = m ? m[1] : '?';
    if (!(ch in groups)) { groups[ch] = []; order.push(ch); }
    groups[ch].push(c);
  });
  order.sort(function(a, b){ return (parseInt(a, 10) || 999) - (parseInt(b, 10) || 999); });
  // Sort clauses within a chapter by numeric lower-bound ("1.8-1.22" → [1,8]),
  // so a range clause doesn't mis-sort ahead of §1.6.
  function _ckey(cl){ return String(cl || '').split(/[-–—]/)[0].split('.').map(function(x){ var n = parseInt(x, 10); return isNaN(n) ? 0 : n; }); }
  order.forEach(function(ch){
    groups[ch].sort(function(a, b){
      var ka = _ckey(a.clause), kb = _ckey(b.clause);
      for (var i = 0; i < Math.max(ka.length, kb.length); i++){ var d = (ka[i] || 0) - (kb[i] || 0); if (d) return d; }
      return 0;
    });
  });
  var html = '<div class="toc-title">Contents · ' + clauses.length + ' clauses</div><nav class="toc-nav">';
  order.forEach(function(ch, idx){
    var links = groups[ch].map(function(c){
      return '<a class="toc-link toc-clause" href="#' + encodeURIComponent(c.slug) +
        '" data-doc-slug="' + escAttr(c.slug) + '">' + esc(_clauseTocLabel(c)) + '</a>';
    }).join('');
    if (ch === '?') { html += links; return; }
    // Collapse deep standards for a browsable overview; keep the first chapter
    // and short standards (≤3 chapters) open so content is never fully hidden.
    var label = 'Chapter ' + esc(ch) + (titleMap[ch] ? ' — ' + esc(titleMap[ch]) : '');
    var open = (idx === 0 || order.length <= 3) ? ' open' : '';
    html += '<details class="toc-ch"' + open + '><summary class="toc-chapter">' + label + '</summary>' + links + '</details>';
  });
  toc.hidden = false;
  toc.innerHTML = html + '</nav>';
  toc.querySelectorAll('[data-doc-slug]').forEach(function(a){
    a.addEventListener('click', function(e){ e.preventDefault(); _route.set(a.getAttribute('data-doc-slug')); });
  });
  if (STATE._tocObserver) { STATE._tocObserver.disconnect(); STATE._tocObserver = null; }
  return true;
}

// "On this page" scroll-spy from the note body's own headings (h3/h4 — the
// renderer demotes one level so `##`→h3, `###`→h4; the lone h2 is the title).
function buildHeadingToc(toc){
  var body = document.getElementById('detail-body');
  if (!body || !toc) return;
  var used = {};
  var items = [];
  body.querySelectorAll('h3, h4').forEach(function(h){
    var text = (h.textContent || '').trim();
    if (!text) return;
    if (!h.id) h.id = _slugifyHeading(text, used); else used[h.id] = true;
    items.push({ id: h.id, text: text, level: h.tagName === 'H4' ? 3 : 2 });
  });
  if (items.length < 2) { toc.hidden = true; toc.innerHTML = ''; return; }
  toc.hidden = false;
  toc.innerHTML = '<div class="toc-title">On this page</div><nav class="toc-nav">' +
    items.map(function(it){
      return '<a class="toc-link toc-l' + it.level + '" href="#" data-toc="' + escAttr(it.id) + '">' +
        esc(it.text) + '</a>';
    }).join('') + '</nav>';
  var linkById = {};
  toc.querySelectorAll('[data-toc]').forEach(function(a){
    var id = a.getAttribute('data-toc');
    linkById[id] = a;
    a.addEventListener('click', function(e){
      e.preventDefault();
      var el = document.getElementById(id);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
  if (STATE._tocObserver) { STATE._tocObserver.disconnect(); }
  var visible = {};
  var order = items.map(function(i){ return i.id; });
  STATE._tocObserver = new IntersectionObserver(function(entries){
    entries.forEach(function(en){
      if (en.isIntersecting) visible[en.target.id] = true; else delete visible[en.target.id];
    });
    var topId = order.filter(function(id){ return visible[id]; })[0];
    Object.keys(linkById).forEach(function(id){ linkById[id].classList.toggle('active', id === topId); });
  }, { rootMargin: '0px 0px -70% 0px', threshold: 0 });
  items.forEach(function(it){ var el = document.getElementById(it.id); if (el) STATE._tocObserver.observe(el); });
}

function bindWikiLinks(scopeId){
  // Known-slug set (only trustworthy once STATE.notes has loaded). Used to mark
  // wikilinks whose target note doesn't exist yet — a planned "to be created"
  // stub. Without this they're styled identically to working cross-links, so a
  // reader only discovers the dead-end after clicking (lands on "Note not found").
  var known = null;
  if (STATE.notes && STATE.notes.length){
    known = new Set();
    STATE.notes.forEach(function(n){ if(n && n.slug) known.add(n.slug); });
  }
  document.querySelectorAll('#'+scopeId+' a[data-wiki]').forEach(a => {
    if (known){
      var base = String(a.dataset.wiki || '').split('#')[0];
      if (base && !known.has(base)){
        a.classList.add('kb-wiki-stub');
        if (!a.title) a.title = 'Planned note — not created yet';
      }
    }
    a.addEventListener('click', e => { e.preventDefault(); _route.set(a.dataset.wiki); });
  });
}

function toGraphNode(o){
  return {
    label: String(o.slug||'').replace(/-/g,' ').slice(0,28),
    color: kindColor(o.kind),
    clickable: o.in_kb !== false,
    data: o,
  };
}

function renderNeighborhood(centerSlug, centerKind, outgoing, backlinks){
  EOS_UI.radialGraph('neighborhood', {
    center: { label: centerSlug.replace(/-/g,' '), color: kindColor(centerKind) },
    outgoing: outgoing.map(toGraphNode),
    incoming: backlinks.map(toGraphNode),
    onNodeClick: function(n){ if(n.data && n.data.slug) _route.set(n.data.slug); },
  });
}

// Flipbook view of the same neighborhood. Two shapes:
//   • has_visual=true  → iframe to the kb-served flipbook page (scoped to this slug)
//   • has_visual=false → placeholder card listing the neighbor slugs as the
//                        callouts a generation would use, with a "Generate visual" CTA
function renderNeighborhoodFlipbook(slug, data){
  var host = document.getElementById('neighborhood-flipbook');
  if(!host) return;
  var p = data.properties || {};
  // Only show for kb concepts (and legacy entries without a typed kind).
  // For other kinds (formula / reference / clause / etc.) the flipbook
  // lens isn't meaningful — skip the section entirely.
  var kind = (p.kind || '').toLowerCase();
  var canFlipbook = kind === 'concept' || kind === 'lesson' || kind === '';
  if(!canFlipbook){ host.style.display = 'none'; return; }
  host.style.display = '';

  if(data.has_visual){
    var src = '/kb/pages/flipbook.html?slug=' + encodeURIComponent(slug) + '&embed=1';
    host.innerHTML =
      '<div class="nf-section-label">' +
        '<span>Flipbook</span>' +
        '<a class="nf-open-link" href="/kb/pages/flipbook.html?slug=' + encodeURIComponent(slug) + '" target="_blank">Open standalone ↗</a>' +
      '</div>' +
      '<iframe class="nf-frame" src="' + escAttr(src) + '" loading="lazy" title="Flipbook for ' + escAttr(slug) + '"></iframe>';
    return;
  }

  // No visual yet — show the neighborhood-as-callouts preview + Generate CTA.
  // Collect outgoing + backlink slugs (deduped) as the callout candidates the
  // generator will be asked to render.
  var calloutSlugs = [];
  var seen = {};
  function add(slugList){
    (slugList || []).forEach(function(o){
      var s = o && o.slug;
      if(s && !seen[s]){ seen[s] = true; calloutSlugs.push(s); }
    });
  }
  add(data.outgoing); add(data.backlinks);
  // Cap at 7 so the generator stays within the 4-7 callout sweet spot
  calloutSlugs = calloutSlugs.slice(0, 7);

  var chipsHtml = calloutSlugs.length
    ? '<div class="nf-callouts-preview">' +
        calloutSlugs.map(function(s){
          return '<span class="nf-callout-chip">' + esc(s.replace(/-/g,' ')) + '</span>';
        }).join('') +
      '</div>'
    : '<div class="nf-callouts-preview" style="color:var(--text-muted);font-size:11px">(no related concepts yet — add a few via related: in frontmatter to seed callouts)</div>';

  host.innerHTML =
    '<div class="nf-section-label"><span>Flipbook</span><span style="font-size:11px;color:var(--text-muted);text-transform:none;letter-spacing:0;font-weight:400">no visual yet</span></div>' +
    '<div class="nf-placeholder">' +
      '<div>Render this concept as a labeled diagram, using its connected concepts as callouts.</div>' +
      chipsHtml +
      '<button class="nf-cta" type="button" data-action="generate-flipbook">◊ Generate visual</button>' +
    '</div>';
  var btn = host.querySelector('[data-action="generate-flipbook"]');
  if(btn){
    btn.addEventListener('click', function(){
      // Hand off to the flipbook renderer page with the slug + callout
      // slugs as a comma-separated query param. The renderer's generate
      // flow reads these and threads them into /api/flipbook/start.
      var url = '/kb/pages/flipbook.html?slug=' + encodeURIComponent(slug) +
                '&generate=1' +
                (calloutSlugs.length ? '&callouts=' + encodeURIComponent(calloutSlugs.join(',')) : '');
      // Open in same tab so the post-verify redirect back to /kb/#<slug>
      // returns the user here with the visual now present.
      window.location.href = url;
    });
  }
}

// ── Detail tabs (Article / Graph / Flipbook / Links) ───────────────
// Article + Links render eagerly in showDetail; Graph + Flipbook render
// lazily on first activation (radialGraph layout + flipbook iframe are
// the expensive parts).
// ── Not-found recovery ─────────────────────────────────────────────
function renderNotFound(slug, errMsg){
  STATE.detail = null;
  var _rd = document.getElementById('kb-reading'); if (_rd) _rd.style.display = 'none';
  var _cn = document.getElementById('detail-connections'); if (_cn) _cn.style.display = 'none';
  document.getElementById('detail-head').innerHTML = '';
  // Fuzzy match over the already-loaded corpus — no extra fetch.
  var q = String(slug||'').toLowerCase();
  var qTokens = q.split(/[-_\s]+/).filter(Boolean);
  var scored = STATE.notes.map(function(n){
    var s = (n.slug||'').toLowerCase();
    var t = (n.title||n.name||'').toLowerCase();
    var score = 0;
    if(q && s.indexOf(q) !== -1) score += 3;
    if(q && t.indexOf(q) !== -1) score += 2;
    qTokens.forEach(function(tok){ if(s.indexOf(tok)!==-1 || t.indexOf(tok)!==-1) score += 1; });
    return {n:n, score:score};
  }).filter(function(x){ return x.score > 0; })
    .sort(function(a,b){ return b.score - a.score; })
    .slice(0,5);
  var suggHtml = scored.length
    ? '<div class="nf-suggest-head">Did you mean</div><div class="nf-suggest" id="nf-suggest">' +
        scored.map(function(x){ return linkRow(x.n.slug, x.n.kind, {}); }).join('') + '</div>'
    : '';
  var host = document.getElementById('detail-notfound');
  host.style.display = 'block';
  host.innerHTML =
    '<div class="kb-notfound">' +
      '<div class="nf-icon">🔍</div>' +
      '<h2>Note not found</h2>' +
      '<div class="nf-sub">No KB note with slug <code>'+esc(slug)+'</code>.</div>' +
      suggHtml +
      '<div class="nf-actions">' +
        '<button class="eos-btn" id="nf-back" type="button">← Back to list</button>' +
        '<button class="eos-btn eos-btn-primary" id="nf-create" type="button">+ Create this note</button>' +
      '</div>' +
    '</div>';
  bindWikiLinks('nf-suggest');
  document.getElementById('nf-back').addEventListener('click', function(){ _route.clear(); });
  document.getElementById('nf-create').addEventListener('click', function(){ openCreateNote(slug); });
}

function openCreateNote(slug){
  var prettyTitle = String(slug||'').replace(/-/g,' ').replace(/\b\w/g, function(c){ return c.toUpperCase(); });
  EOS_UI.formModal({
    title: 'Create KB note',
    fields: [
      {key:'title', label:'Title', type:'text', value:prettyTitle},
      // options are plain strings (EOS_UI.formHtml) — value === label === kind slug
      {key:'kind', label:'Kind', type:'select', value:'concept',
        options:['concept','formula','reference','case','lesson','pattern','moc']},
      {key:'domain', label:'Domain (optional)', type:'text', value:STATE.filter.domain||''},
    ],
    onSubmit: async function(vals){
      var res;
      try {
        res = await fetch('/kb/api/notes', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ kind: vals.kind, title: vals.title, domain: vals.domain || '', slug: slug }),
        }).then(function(r){ return r.json(); });
      } catch(e){ EOS_UI.toast('Create failed: '+e.message, false); return; }
      if(res.error){ EOS_UI.toast(res.error, false); return; }
      EOS_UI.toast('Created '+(res.slug||vals.title), true);
      await loadAll();
      _route.set(res.slug || slug);
    },
  });
}

// --- Source-PDF locator (files-domain consumer) -------------------------
// Bridges a standards note → the physical PDF anywhere on this computer via
// the `files` search domain (Everything/es.exe). Locate-on-demand; nothing
// stored, so a moved/renamed file is always re-found fresh.
async function findSourcePdf(){
  var slug = (STATE.detail || {}).slug;
  if(!slug) return;
  var data;
  try { data = await fetch('/kb/api/source-pdf/' + encodeURIComponent(slug)).then(function(r){ return r.json(); }); }
  catch(e){ data = { error: String(e) }; }
  EOS_UI.modal({ title: '📄 Source PDF', body: _sourcePdfBody(data), width: 640 });
}

function _sourcePdfBody(data){
  if(data && data.error){ return '<div class="empty-side">Error: ' + esc(data.error) + '</div>'; }
  var results = (data && data.results) || [];
  if(!results.length){
    var hint = (data && data.files_domain)
      ? ('No PDF found on this computer for "' + esc((data && data.query) || '') + '".')
      : 'The files search domain is off. Enable <code>[capabilities.search.files]</code> in emptyos.toml and restart to search your whole machine.';
    var why = (data && data.reason) ? ('<br><small style="color:var(--text-muted)">' + esc(data.reason) + '</small>') : '';
    return '<div class="empty-side">' + hint + why + '</div>';
  }
  var head = '<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px">Matched "' + esc(data.query) + '" · ' + results.length + ' file(s)</div>';
  return head + results.map(function(r){
    return '<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">' +
        '<div style="flex:1;min-width:0">' +
          '<div style="font-weight:600;font-size:13px">' + esc(r.name) + '</div>' +
          '<div style="font-size:11px;color:var(--text-muted);word-break:break-all">' + esc(r.path) + '</div>' +
        '</div>' +
        '<button onclick="openLocalFile(' + escAttr(JSON.stringify(r.path)) + ', this)"' +
          ' style="flex-shrink:0;font-size:12px;padding:5px 12px;border:1px solid var(--accent);border-radius:7px;background:transparent;color:var(--accent);cursor:pointer">Open</button>' +
      '</div>';
  }).join('');
}

async function openLocalFile(path, btn){
  if(btn){ btn.disabled = true; btn.textContent = 'Opening…'; }
  var res;
  try {
    res = await fetch('/kb/api/open-local', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path })
    }).then(function(r){ return r.json(); });
  } catch(e){ res = { ok: false, error: String(e) }; }
  if(btn){ btn.disabled = false; btn.textContent = res.ok ? 'Opened ✓' : 'Open'; }
  if(!res.ok){ EOS_UI.toast(res.error || 'Could not open file', false); }
}

function _normTitle(x){ return String(x||'').replace(/\s+/g,' ').trim().toLowerCase(); }

// Strip a leading H1 that duplicates the page title (header owns the title).
function _prepBody(md, title){
  var s = (md || '').replace(/^﻿?\s*/, '');
  var m = s.match(/^#\s+(.+?)\s*\r?\n/);
  if (m && _normTitle(m[1]) === _normTitle(title)) s = s.slice(m[0].length);
  return s.replace(/^\s+/, '');
}

async function showDetail(slug){
  document.getElementById('list-wrap').classList.add('hide');
  document.getElementById('list-wrap').style.display = '';
  document.getElementById('network-wrap').classList.remove('show');
  document.getElementById('detail-wrap').classList.add('show');
  var _rdShow = document.getElementById('kb-reading'); if (_rdShow) _rdShow.style.display = '';
  var _cnShow = document.getElementById('detail-connections'); if (_cnShow) _cnShow.style.display = '';
  if (window.KBFull) KBFull.teardown();
  if (STATE._tocObserver) { STATE._tocObserver.disconnect(); STATE._tocObserver = null; }
  STATE._connGraphRendered = false;
  // Reset surfaces
  document.getElementById('detail-notfound').style.display = 'none';
  document.getElementById('detail-notfound').innerHTML = '';
  document.getElementById('detail-head').innerHTML = '';
  document.getElementById('detail-body').innerHTML = '<div class="empty">Loading…</div>';
  var ftMount = document.getElementById('kb-fulltext-mount'); if (ftMount) ftMount.innerHTML = '';
  var docnav = document.getElementById('kb-docnav'); if (docnav) { docnav.hidden = true; docnav.innerHTML = ''; }
  var onpage = document.getElementById('detail-toc'); if (onpage) { onpage.hidden = true; onpage.innerHTML = ''; }
  var nfEl = document.getElementById('neighborhood-flipbook'); nfEl.innerHTML = ''; nfEl.style.display = 'none';
  document.getElementById('neighborhood').innerHTML = '';
  ['detail-refs','detail-outgoing','detail-impl','detail-backlinks','detail-clauses'].forEach(function(id){
    var e = document.getElementById(id); if (e) e.innerHTML = '';
  });
  document.getElementById('detail-clauses-card').style.display = 'none';
  document.getElementById('cl-count').textContent = '';
  document.getElementById('bl-count').textContent = '';
  document.getElementById('kb-conn-n').textContent = '';
  var conn = document.getElementById('detail-connections'); if (conn) conn.open = false;

  var data;
  try {
    data = await fetch('/kb/api/notes/'+encodeURIComponent(slug)).then(r=>r.json());
  } catch(e){ data = {error:String(e)}; }
  if(data.error){ renderNotFound(slug, data.error); return; }
  STATE.detail = data;
  var p = data.properties || {};
  var hasFlipbook = !!data.has_visual;

  // Title — frontmatter, then body H1, then humanized slug
  var bodyH1 = (data.body||'').match(/^#\s+(.+?)\s*$/m);
  var headerTitle = p.title
    || (bodyH1 ? bodyH1[1] : null)
    || (data.name && data.name !== slug ? data.name : slug.replace(/-/g,' '));

  renderDetailHeader(slug, data, p, headerTitle);

  // Body (overview for documents, full article for leaf notes). Strip the
  // duplicate leading H1; drop the flipbook raw dump when a visual exists.
  var bodyMd = _prepBody(data.body || '', headerTitle);
  if (hasFlipbook) {
    bodyMd = bodyMd
      .replace(/##\s+Diagram[\s\S]*?(?=\n##\s|\n#\s|$)/i, '')
      .replace(/##\s+Callouts[\s\S]*?(?=\n##\s|\n#\s|$)/i, '');
  }
  var _vizEmbeds = EOS_UI.decodeVaultJson(p.viz_embeds, []);
  document.getElementById('detail-body').innerHTML = EOS_UI.renderMarkdownWithEmbeds(bodyMd, _vizEmbeds, {
    wikiLink: function(target, label){
      return '<a href="#'+encodeURIComponent(target)+'" data-wiki="'+escAttr(target)+'" class="kb-wiki">'+esc(label)+'</a>';
    },
  });
  bindWikiLinks('detail-body');
  // Formula notes are a first-class kind here, so typeset their math. Lazy,
  // idempotent, and a no-op on a note that has none.
  if (EOS_UI.typesetMath) EOS_UI.typesetMath(document.getElementById('detail-body'));
  renderNeighborhoodFlipbook(slug, data);  // concept/lesson visual above the body

  // Reading surface: a reference with stored full text → 3-column docs reader
  // (doc-nav + overview→full text + on-this-page); everything else → 2-column
  // article (body + on-this-page).
  var builtReader = false;
  if (p.kind === 'reference' && window.KBFull) {
    builtReader = await KBFull.mountReader(slug, data);
  }
  // No full-text reader → reference gets the chapter→clause document tree
  // (buildToc dispatches: reference w/ clauses → buildDocumentToc, else → headings).
  if (!builtReader) buildToc(data);

  renderConnections(slug, data, p);
}

// Compact header: breadcrumb · title (once) · meta · revision controls · ⋯ actions.
function renderDetailHeader(slug, data, p, headerTitle){
  // Supersession → one slim chip (not a banner + a separate line).
  var supChip = '';
  if (data.superseded_by && data.superseded_by.slug){
    var sb = data.superseded_by;
    var curSlug = data.current_slug || sb.slug;
    var jump = '<a href="#'+encodeURIComponent(curSlug)+'" data-wiki="'+escAttr(curSlug)+'" class="kb-wiki">'+esc(sb.title || sb.slug)+'</a>';
    var lead = (sb.via === 'document')
      ? ('⤴ superseded revision'+(p.edition?(' '+esc(p.edition)):'')+' → ')
      : '⤴ superseded by ';
    supChip = '<span class="kb-sup-chip">'+lead+jump+'</span>';
  }
  var crumbDomain = p.domain ? (' · '+esc(p.domain.replace(/-/g,' '))) : '';
  var html =
    '<div class="kb-crumb"><a id="kb-crumb-back">← Knowledge base</a>'+crumbDomain+'</div>' +
    '<div class="kb-head-row">' +
      '<h1>'+esc(headerTitle)+'</h1>' +
      '<div class="kb-head-tools" id="kb-head-tools"></div>' +
    '</div>' +
    supChip +
    '<div class="detail-meta">' +
      (p.kind ? '<span class="note-kind kind-'+escAttr(p.kind)+'">'+esc(p.kind)+'</span>' : '') +
      (p.topic ? '<span>'+esc(p.topic)+'</span>' : '') +
    '</div>';
  document.getElementById('detail-head').innerHTML = html;
  bindWikiLinks('detail-head');
  var crumb = document.getElementById('kb-crumb-back');
  if (crumb) crumb.addEventListener('click', function(){ if (window._route) _route.clear(); else hideDetail(); });

  // Tools: revision selector + Compare (references) via KBRev; ⋯ actions menu.
  var tools = document.getElementById('kb-head-tools');
  if (window.KBRev && KBRev.mountHeader) KBRev.mountHeader(tools, data);
  _mountActionsMenu(tools, slug, p);
  // 4D timeline contract — the platform auto-injects a 📅 button into any
  // element carrying data-entity-path (see [provides.timeline] in manifest).
  if (data.path) tools.setAttribute('data-entity-path', String(data.path).replace(/\\/g, '/'));
}

function _mountActionsMenu(tools, slug, p){
  var items = [];
  if (p.standard_id || p.standard) items.push('<button onclick="findSourcePdf()">&#128196; Source PDF</button>');
  if (STATE.embedEnabled) items.push('<button onclick="openVizEmbedPicker(\''+escAttr(slug)+'\')">&#10010; Embed viz</button>');
  // The raw verbatim archive (the standard is read as composed clause notes; the
  // .txt is the immutable source, demoted to this link).
  var arc = p.local_text || p.source_file || '';
  if (arc) items.push('<div class="kb-arc-row">&#128196; Source archive ' + EOS.noteActions(arc) + '</div>');
  items.push('<button onclick="document.getElementById(\'detail-connections\').open=true;document.getElementById(\'detail-connections\').scrollIntoView({behavior:\'smooth\'})">&#128279; Connections &amp; graph</button>');
  var wrap = document.createElement('div');
  wrap.className = 'kb-actions-menu';
  wrap.innerHTML = '<button class="kb-mini-btn" id="kb-actions-btn" title="More">⋯</button>' +
    '<div class="kb-actions-pop" id="kb-actions-pop" hidden>'+items.join('')+'</div>';
  tools.appendChild(wrap);
  var btn = wrap.querySelector('#kb-actions-btn');
  var pop = wrap.querySelector('#kb-actions-pop');
  btn.addEventListener('click', function(e){ e.stopPropagation(); pop.hidden = !pop.hidden; });
  document.addEventListener('click', function(){ if (pop) pop.hidden = true; });
}

// Connections — link cards (+ lazy graph on first expand). Replaces Graph/Links tabs.
function renderConnections(slug, data, p){
  // References
  var refs = p.references || [];
  document.getElementById('detail-refs').innerHTML = (Array.isArray(refs) && refs.length)
    ? refs.map(function(r){
        if (typeof r === 'string') return '<div class="refs-text">'+esc(r)+'</div>';
        if (r && r.target_slug) return '<div class="refs-text"><a href="#'+encodeURIComponent(r.target_slug)+'" data-wiki="'+escAttr(r.target_slug)+'" class="kb-wiki">'+esc(r.text||'')+'</a></div>';
        return '<div class="refs-text">'+esc((r && r.text) || '')+'</div>';
      }).join('')
    : '<div class="empty-side">No external citations.</div>';
  bindWikiLinks('detail-refs');

  // Clauses in this standard
  var clauses = data.clauses || [];
  if (clauses.length){
    document.getElementById('detail-clauses-card').style.display = 'block';
    document.getElementById('cl-count').textContent = clauses.length;
    var groups = {}, order = [];
    clauses.forEach(function(c){ var k = c.standard + (c.edition ? ':'+c.edition : ''); if (!(k in groups)){ groups[k]=[]; order.push(k);} groups[k].push(c); });
    document.getElementById('detail-clauses').innerHTML = order.map(function(k){
      var rows = groups[k].map(function(c){
        var label = (c.clause ? '§'+esc(c.clause) : '(whole standard)') + (c.clause_title ? ' — '+esc(c.clause_title) : '');
        return '<a class="item-link" href="#'+encodeURIComponent(c.slug)+'" data-wiki="'+escAttr(c.slug)+'"><span class="item-slug">'+label+'</span></a>';
      }).join('');
      return (order.length > 1 ? '<div class="clauses-group-header">'+esc(k)+'</div>' : '') + rows;
    }).join('');
    bindWikiLinks('detail-clauses');
  }

  // Outgoing
  var out = data.outgoing || [];
  document.getElementById('detail-outgoing').innerHTML = out.length
    ? out.map(function(o){ return linkRow(o.slug, o.kind, {external: !o.in_kb}); }).join('')
    : '<div class="empty-side">No outgoing links.</div>';
  bindWikiLinks('detail-outgoing');

  // Implemented in
  var impl = data.implemented_in_status || [];
  document.getElementById('detail-impl').innerHTML = impl.length
    ? impl.map(function(r){
        var isMethod = r.kind === 'method';
        var symbol = isMethod ? esc(r.symbol||'') : (r.symbol ? esc(r.symbol)+'()' : esc((r.path||'').split('/').pop()));
        var status = isMethod ? '<span style="color:var(--text-muted)" title="Method reference">·</span>'
          : (r.exists ? '<span class="impl-ok" title="Path exists">✓</span>' : '<span class="impl-bad" title="Path missing">✗</span>');
        var pathRow = isMethod ? '' : '<div class="impl-card-path">'+esc(r.path||'(no path)')+'</div>';
        return '<div class="impl-card"><div class="impl-card-row">'+status+' <span class="impl-card-sym">'+symbol+'</span></div>'+pathRow+'</div>';
      }).join('')
    : '<div class="empty-side">No code references.</div>';

  // Backlinks
  var bl = data.backlinks || [];
  document.getElementById('bl-count').textContent = bl.length ? '('+bl.length+')' : '';
  document.getElementById('detail-backlinks').innerHTML = bl.length
    ? bl.map(function(b){ return linkRow(b.slug, b.kind, {}); }).join('')
    : '<div class="empty-side">No backlinks found.</div>';
  bindWikiLinks('detail-backlinks');

  // Connections count + lazy graph on first expand
  var n = (data.backlinks||[]).length + (data.outgoing||[]).length + (data.clauses||[]).length;
  document.getElementById('kb-conn-n').textContent = n ? ('· '+n) : '';
  var conn = document.getElementById('detail-connections');
  if (conn && !conn._wired){
    conn._wired = true;
    conn.addEventListener('toggle', function(){
      if (conn.open && !STATE._connGraphRendered && STATE.detail){
        STATE._connGraphRendered = true;
        var d = STATE.detail;
        renderNeighborhood(d.slug, (d.properties||{}).kind, d.outgoing||[], d.backlinks||[]);
      }
    });
  }
}


function hideDetail(){
  document.getElementById('list-wrap').classList.remove('hide');
  document.getElementById('detail-wrap').classList.remove('show');
  // Re-show whichever view-mode pane was active before detail opened
  applyView(STATE.view);
}

// ── View-mode toggle (list / network) ──────────────────────────────
// Flipbook is NOT a top-level view-mode — it's a per-note renderer
// stacked inside the detail view, next to the radial neighborhood.
function applyView(mode){
  document.getElementById('list-wrap').classList.remove('hide');
  document.getElementById('list-wrap').style.display = '';
  document.getElementById('network-wrap').classList.remove('show');
  if(mode === 'network'){
    document.getElementById('list-wrap').style.display = 'none';
    document.getElementById('network-wrap').classList.add('show');
    ensureNetwork();
  }
  document.querySelectorAll('#view-toggle button').forEach(b => {
    var active = b.dataset.view === mode;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', active ? 'true' : 'false');
  });
}

function setView(mode, opts){
  opts = opts || {};
  if(!['list','network'].includes(mode)) mode = 'list';
  STATE.view = mode;
  var url = new URL(window.location.href);
  if(mode === 'list') url.searchParams.delete('view');
  else url.searchParams.set('view', mode);
  if(!opts.silent) window.history.replaceState({}, '', url.toString());
  if(window.location.hash && window.location.hash.length > 1) return;
  applyView(mode);
}

// ── Network view (vis-network + inline-expand card) ────────────────
function loadVisNetwork(){
  return new Promise(function(resolve, reject){
    if(window.vis && window.vis.Network) return resolve();
    // Vendored locally (vis-network 9.1.9) — works offline / behind CSP / on
    // the demo VPS, no unpkg dependency. Served at {prefix}/pages.
    var s = document.createElement('script');
    s.src = '/kb/pages/vis-network.min.js';
    s.onload = resolve;
    s.onerror = function(){ reject(new Error('Failed to load vis-network (bundled)')); };
    document.head.appendChild(s);
  });
}

function netThemeColor(name, fallback){
  var v = (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim();
  return v || fallback;
}

function netKindColor(k){
  var theme = {
    accent: netThemeColor('--accent', '#6c5ce7'),
    success: netThemeColor('--success', '#27ae76'),
    warning: netThemeColor('--warning', '#d4a017'),
    danger: netThemeColor('--danger', '#d44040'),
    muted: netThemeColor('--text-muted', '#888'),
    secondary: netThemeColor('--text-secondary', '#aaa'),
  };
  switch((k||'').toLowerCase()){
    case 'kb':        return theme.accent;
    case 'formula':   return theme.accent;
    case 'concept':   return theme.secondary;
    case 'reference': return theme.warning;
    case 'clause':    return theme.muted;
    case 'case':      return theme.success;
    case 'lesson':    return theme.danger;
    case 'doc':       case 'kb-doc': return theme.accent;
    case 'moc':       return theme.accent;
    case 'note':      return theme.success;
    case 'guideline': return theme.warning;
    case 'explore':   return theme.accent;
    default:          return theme.muted;
  }
}

// Typed-edge styling — wikilink/tag kept byte-for-byte (muted, .5 opacity);
// the four semantic kinds surface the KB's frontmatter relationships so the
// reference→clause→formula→case ladder is visible. Mirrors the vault-graph
// edgeStyle() precedent. Colors come from theme tokens so themes restyle them.
function netEdgeStyle(kind){
  var muted = netThemeColor('--text-muted', '#888');
  switch(kind){
    case 'wikilink':         return { color: muted, opacity: 0.5, dashes: false, arrows: 'to', width: 1 };
    case 'tag':              return { color: muted, opacity: 0.5, dashes: true,  arrows: '',   width: 1 };
    case 'verified_against': return { color: netThemeColor('--success', '#27ae76'),        opacity: 0.8,  dashes: false, arrows: 'to', width: 1.5 };
    case 'cites':            return { color: netThemeColor('--accent', '#6c5ce7'),         opacity: 0.7,  dashes: false, arrows: 'to', width: 1 };
    case 'superseded_by':    return { color: netThemeColor('--warning', '#d4a017'),        opacity: 0.8,  dashes: true,  arrows: 'to', width: 1.5 };
    case 'related':          return { color: netThemeColor('--text-secondary', '#aaa'),    opacity: 0.55, dashes: false, arrows: '',   width: 1 };
    default:                 return { color: muted, opacity: 0.5, dashes: false, arrows: '', width: 1 };
  }
}

function _netEdgeKindOn(id){
  var el = document.getElementById(id);
  return !el || el.checked;
}

async function fetchNetworkGraph(){
  var status = document.getElementById('network-status');
  status.style.display = '';
  status.textContent = 'Fetching graph…';
  var kindsSel = document.getElementById('net-kinds');
  // Distinguish "kb" (default) from "" (whole vault) — the API treats them differently.
  // Lower cap (250) keeps the layout legible; domain/kind filters narrow further
  // client-side via the new node `domain` field.
  var url = '/kb/api/graph?limit=250';
  if(kindsSel.value === '') url += '&kinds=';
  else url += '&kinds=' + encodeURIComponent(kindsSel.value);
  if(document.getElementById('net-tag-edges').checked) url += '&shared_tags=1';
  var semEl = document.getElementById('net-semantic-edges');
  if(semEl && semEl.checked) url += '&semantic=1';
  if(STATE.detail && STATE.detail.path) url += '&center=' + encodeURIComponent(STATE.detail.path);
  try {
    var r = await fetch(url);
    var data = await r.json();
    // Honest cap: "250 of 1359 notes" when the slice is capped — never imply
    // the graph shows everything when it doesn't (no silent caps).
    var st = data.stats || null;
    document.getElementById('net-stat-line').textContent = st
      ? (st.in_slice + (st.pool > st.in_slice ? ' of ' + st.pool : '') + ' notes · ' + st.wikilink_edges + ' links')
      : '';
    return data;
  } catch(e){
    status.textContent = 'Failed: ' + e.message;
    throw e;
  }
}

function renderNetwork(data){
  STATE.network.raw = data;
  if(!data.nodes || !data.nodes.length){
    var st = document.getElementById('network-status');
    st.style.display = '';
    st.textContent = 'No notes in this slice — switch scope or add KB notes.';
    return;
  }
  var textColor = netThemeColor('--text', '#eee');
  var accentColor = netThemeColor('--accent', '#6c5ce7');
  var nodes = data.nodes.map(function(n){
    var color = netKindColor(n.kind);
    // Wave 3: notes with flipbook visuals render as diamonds (vis-network
    // shape) so they read as distinct nodes in the graph at a glance.
    // Center node (focused via detail) keeps its bigger circle.
    var shape = (n.has_visual && !n.is_center) ? 'diamond' : 'dot';
    var node = {
      id: n.id,
      label: n.label || '(untitled)',
      color: { background: color, border: color },
      font: { color: textColor, size: 12 },
      shape: shape,
      size: n.is_center ? 18 : (n.has_visual ? 12 : 10),
      _kind: n.kind,
      _domain: n.domain || '',
      _has_visual: !!n.has_visual,
    };
    if(n.is_center){
      node.borderWidth = 3;
      node.color.border = accentColor;
    } else if(n.has_visual){
      // Subtle accent edge so diamonds also read as "marked" against the muted graph
      node.borderWidth = 2;
      node.color.border = accentColor;
    }
    return node;
  });
  var edges = data.edges.map(function(e){
    var s = netEdgeStyle(e.kind);
    return {
      from: e.from, to: e.to,
      arrows: s.arrows,
      dashes: s.dashes,
      color: { color: s.color, opacity: s.opacity },
      width: s.width,
      _kind: e.kind,
    };
  });
  STATE.network.nodesDS = new vis.DataSet(nodes);
  STATE.network.edgesDS = new vis.DataSet(edges);
  var container = document.getElementById('network-canvas');
  // Tear down prior instance if re-rendering
  if(STATE.network.instance){ try { STATE.network.instance.destroy(); } catch(e){} }
  STATE.network.instance = new vis.Network(
    container,
    { nodes: STATE.network.nodesDS, edges: STATE.network.edgesDS },
    {
      layout: { improvedLayout: false },   // silences the >100-node hierarchical-layout warning + speeds boot
      physics: { stabilization: { iterations: 120 }, barnesHut: { gravitationalConstant: -3000 } },
      interaction: { hover: true, tooltipDelay: 200 },
      nodes: { borderWidth: 1 },
    }
  );
  STATE.network.instance.on('click', function(params){
    if(params.nodes && params.nodes.length){
      expandNodeCard(params.nodes[0]);
    } else {
      closeNodeCard();
    }
  });
  STATE.network.instance.on('dragStart', closeNodeCard);
  STATE.network.instance.on('zoom', closeNodeCard);
  document.getElementById('network-status').style.display = 'none';
  // If we have a center node, focus + auto-expand its card
  if(STATE.detail && STATE.detail.path){
    var hasCenter = data.nodes.some(function(n){ return n.id === STATE.detail.path && n.is_center; });
    if(hasCenter){
      setTimeout(function(){
        try {
          STATE.network.instance.focus(STATE.detail.path, { scale: 1.2, animation: true });
          setTimeout(function(){ expandNodeCard(STATE.detail.path); }, 350);
        } catch(e){}
      }, 600);
    }
  }
}

async function ensureNetwork(){
  try {
    document.getElementById('network-status').style.display = '';
    await loadVisNetwork();
    var data = await fetchNetworkGraph();
    renderNetwork(data);
    // Default domain-scoped: carry over the list's active domain filter.
    var nd = document.getElementById('net-domain');
    if(nd && STATE.filter.domain && !nd.value){ nd.value = STATE.filter.domain; }
    applyNetworkFilter();
    STATE.network.loaded = true;
  } catch(e){
    document.getElementById('network-status').textContent = e.message;
  }
}

async function reloadNetwork(){
  closeNodeCard();
  try {
    document.getElementById('network-status').style.display = '';
    var data = await fetchNetworkGraph();
    renderNetwork(data);
    applyNetworkFilter();
  } catch(e){
    document.getElementById('network-status').textContent = e.message;
  }
}

function applyNetworkFilter(){
  if(!STATE.network.nodesDS) return;
  var q = (document.getElementById('net-filter-q').value || '').toLowerCase().trim();
  var dom = (document.getElementById('net-domain') || {}).value || '';
  var knd = (document.getElementById('net-kind') || {}).value || '';
  var textColor = netThemeColor('--text', '#eee');
  var updates = [];
  STATE.network.nodesDS.forEach(function(n){
    var labelMatch = !q || (n.label || '').toLowerCase().includes(q);
    var domMatch = !dom || n._domain === dom;
    var kindMatch = !knd || n._kind === knd;
    var hidden = !(labelMatch && domMatch && kindMatch);
    // Emphasise labels that match an active text query (labels-on-search).
    var emph = q && labelMatch && !hidden;
    updates.push({ id: n.id, hidden: !!hidden, font: { color: textColor, size: emph ? 18 : 12 } });
  });
  STATE.network.nodesDS.update(updates);

  // Per-kind semantic-edge visibility (client-side; no refetch). wikilink/tag
  // are always shown — tag is already gated at fetch via shared_tags.
  if(STATE.network.edgesDS){
    var ev = {
      verified_against: _netEdgeKindOn('net-edge-validates'),
      cites: _netEdgeKindOn('net-edge-cites'),
      superseded_by: _netEdgeKindOn('net-edge-supersedes'),
      related: _netEdgeKindOn('net-edge-related'),
    };
    var eUpdates = [];
    STATE.network.edgesDS.forEach(function(ed){
      if(ed._kind in ev){ eUpdates.push({ id: ed.id, hidden: !ev[ed._kind] }); }
    });
    if(eUpdates.length) STATE.network.edgesDS.update(eUpdates);
  }
}

// Inline-expand card — pinned to the clicked node, re-centered on chip click
function closeNodeCard(){
  if(STATE.network.card){
    STATE.network.card.remove();
    STATE.network.card = null;
  }
}

async function expandNodeCard(nodePath){
  closeNodeCard();
  if(!STATE.network.instance) return;
  // Resolve path → slug
  var resolve;
  try {
    resolve = await fetch('/kb/api/notes-by-path/' + encodeURIComponent(nodePath)).then(r => r.json());
  } catch(e){ return; }
  if(!resolve || !resolve.slug){
    // Node isn't a kb-tagged note — fall back to a minimal card
    var nodeMeta = STATE.network.raw.nodes.find(function(n){ return n.id === nodePath; });
    showNodeCard(nodePath, {
      slug: (nodePath.split('/').pop() || '').replace(/\.md$/, ''),
      title: (nodeMeta && nodeMeta.label) || nodePath,
      kind: (nodeMeta && nodeMeta.kind) || '',
      excerpt: '(not in KB — open detail to add to corpus)',
      outgoing: [],
      external: true,
    });
    return;
  }
  var slug = resolve.slug;
  var data;
  try {
    data = await fetch('/kb/api/notes/' + encodeURIComponent(slug)).then(r => r.json());
  } catch(e){ return; }
  if(data.error) return;
  var p = data.properties || {};
  var bodyMd = (data.body || '').replace(/^#\s+.+?\n/, '').trim();
  // Strip ## headings + code fences for excerpt cleanliness
  var excerpt = bodyMd
    .replace(/```[\s\S]*?```/g, '')
    .replace(/^##\s+.+?\n/gm, '')
    .replace(/\n{2,}/g, ' ')
    .slice(0, 280);
  // Pull has_visual from the API response — server computes it from
  // svg_callouts/image_url/image_callouts presence; client doesn't
  // re-derive. Fall back to the node-meta flag from /api/graph so the
  // diamond chip + flipbook button render correctly even if the
  // detail-fetch race hasn't filled `data.has_visual` yet.
  var nodeMeta2 = STATE.network.raw && STATE.network.raw.nodes
    ? STATE.network.raw.nodes.find(function(n){ return n.id === nodePath; })
    : null;
  showNodeCard(nodePath, {
    slug: slug,
    title: p.title || data.name || slug.replace(/-/g,' '),
    kind: p.kind || '',
    domain: p.domain || '',
    excerpt: excerpt || '(empty body)',
    outgoing: (data.outgoing || []).slice(0, 8),
    external: false,
    has_visual: !!(data.has_visual || (nodeMeta2 && nodeMeta2.has_visual)),
  });
}

function showNodeCard(nodePath, info){
  var positions = STATE.network.instance.getPositions([nodePath]);
  var pos = positions[nodePath];
  if(!pos) return;
  var canvas = document.getElementById('network-canvas');
  var domPos = STATE.network.instance.canvasToDOM(pos);
  var card = document.createElement('div');
  card.className = 'node-card';
  var kindChip = info.kind
    ? '<span class="note-kind kind-' + escAttr(info.kind) + '" style="font-size:10px">' + esc(info.kind) + '</span>'
    : '';
  var domainChip = info.domain
    ? '<span style="font-size:11px;color:var(--text-muted)">' + esc(info.domain.replace(/-/g,' ')) + '</span>'
    : '';
  var outgoingHtml = '';
  if(info.outgoing && info.outgoing.length){
    outgoingHtml = '<div class="nc-out-label">Outgoing</div><div class="nc-out-chips">' +
      info.outgoing.map(function(o){
        var cls = o.in_kb === false ? 'nc-chip external' : 'nc-chip';
        return '<a class="' + cls + '" data-chip-slug="' + escAttr(o.slug) + '" title="' + escAttr(o.slug) + '">' +
               esc(o.slug.replace(/-/g,' ')) + '</a>';
      }).join('') + '</div>';
  }
  // Visual marker — small ◊ chip when the node has flipbook visual data
  var visualChip = info.has_visual
    ? '<span style="font-size:11px;color:var(--accent)" title="Has flipbook visual">◊</span>'
    : '';
  // Flipbook action — "Open as flipbook" when visuals exist; otherwise
  // omit (Wave 3: every concept could be flipbooked, but the network
  // card stays focused — generation flow lives in the detail view).
  var flipbookBtn = info.has_visual
    ? '<button class="nc-btn" data-action="flipbook" title="Open this node as a flipbook">◊ Flipbook</button>'
    : '';
  card.innerHTML =
    '<button class="nc-close" aria-label="Close">×</button>' +
    '<div class="nc-title">' + esc(info.title) + ' ' + visualChip + '</div>' +
    '<div class="nc-meta">' + kindChip + domainChip + '</div>' +
    '<div class="nc-excerpt">' + esc(info.excerpt) + '</div>' +
    outgoingHtml +
    '<div class="nc-actions">' +
      (info.external ? '' :
        '<button class="nc-btn primary" data-action="detail">Open detail</button>' +
        flipbookBtn +
        '<button class="nc-btn" data-action="pin">Pin to side</button>'
      ) +
    '</div>';
  // Position the card: top-right of node by default, flip if it'd overflow
  var wrap = canvas.getBoundingClientRect();
  var cardWidth = 260;
  var cardHeight = 200;
  var left = domPos.x + 20;
  var top = domPos.y - 80;
  if(left + cardWidth > wrap.width) left = domPos.x - cardWidth - 20;
  if(top < 8) top = 8;
  if(top + cardHeight > wrap.height - 8) top = Math.max(8, wrap.height - cardHeight - 8);
  card.style.left = left + 'px';
  card.style.top = top + 'px';
  canvas.appendChild(card);
  STATE.network.card = card;
  card.querySelector('.nc-close').addEventListener('click', closeNodeCard);
  card.querySelectorAll('[data-chip-slug]').forEach(function(a){
    a.addEventListener('click', function(e){
      e.preventDefault();
      var targetSlug = a.dataset.chipSlug;
      // Find a node whose id ends with /{slug}.md and re-center there
      var match = STATE.network.raw.nodes.find(function(n){
        return n.id.endsWith('/' + targetSlug + '.md') || n.id === targetSlug + '.md';
      });
      if(match){
        try {
          STATE.network.instance.focus(match.id, { scale: 1.2, animation: true });
          setTimeout(function(){ expandNodeCard(match.id); }, 350);
        } catch(err){}
      } else {
        // Not in current slice — jump to detail view
        _route.set(targetSlug);
      }
    });
  });
  var detailBtn = card.querySelector('[data-action="detail"]');
  if(detailBtn) detailBtn.addEventListener('click', function(){ _route.set(info.slug); });
  var flipbookBtnEl = card.querySelector('[data-action="flipbook"]');
  if(flipbookBtnEl) flipbookBtnEl.addEventListener('click', function(){
    // Open the kb detail view, then scroll to the inline flipbook visual.
    _route.set(info.slug);
    setTimeout(function(){
      var fb = document.getElementById('neighborhood-flipbook');
      if (fb && fb.style.display !== 'none') fb.scrollIntoView({ behavior:'smooth', block:'start' });
    }, 450);
  });
  var pinBtn = card.querySelector('[data-action="pin"]');
  if(pinBtn) pinBtn.addEventListener('click', function(){
    // V1: pinning just keeps the card open while clicking elsewhere doesn't close it
    pinBtn.textContent = 'Pinned';
    pinBtn.disabled = true;
  });
}

var _route = EOS_UI.hashRoute({
  onShow: function(slug){ showDetail(slug); },
  onHide: function(){ hideDetail(); },
});

// Back is the header breadcrumb (#kb-crumb-back); the old detail-back button is gone.
// Health triage — categories grouped by severity, each row links to its note.
var HEALTH_CATEGORIES = [
  {key:'duplicate_slugs',             sev:'error', label:'Duplicate slugs',                  detail:function(b){ return '('+(b.paths||[]).length+' notes)'; }},
  {key:'invalid_kind',                sev:'error', label:'Invalid kind',                     detail:function(b){ return '→ '+(b.kind||'(none)'); }},
  {key:'broken_implemented_in',       sev:'error', label:'Broken implemented_in',            detail:function(b){ return '→ '+(b.ref||''); }},
  {key:'unresolved_verified_against', sev:'error', label:'Unresolved verified_against',       detail:function(b){ return '→ '+(b.target||''); }},
  {key:'formulas_missing_verification',sev:'warn', label:'Formulas missing verification',     detail:function(){ return ''; }},
  {key:'orphans',                     sev:'warn',  label:'Orphans (no backlinks / related)',  detail:function(){ return ''; }},
  {key:'uncited_references',          sev:'warn',  label:'Uncited clauses',                   detail:function(){ return ''; }},
  {key:'verification_target_not_case',sev:'warn',  label:'Verify target not a case',          detail:function(b){ return '→ '+(b.target||'')+' ('+(b.target_kind||'?')+')'; }},
  {key:'stale_reference',             sev:'warn',  label:'References past review date',        detail:function(b){
    if(b.state === 'malformed') return 'unparseable review_due: '+(b.review_due||'');
    return (b.days_overdue||0)+'d overdue (due '+(b.review_due||'?')+')';
  }},
];

function _healthBody(r){
  var distinct = {}, totalRows = 0;
  HEALTH_CATEGORIES.forEach(function(c){
    (r[c.key]||[]).forEach(function(b){ if(b.slug){ distinct[b.slug]=true; totalRows++; } });
  });
  var distinctN = Object.keys(distinct).length;
  if(totalRows === 0){
    return '<div class="kb-health-clean">✓ All clear — no issues found across the corpus.</div>';
  }
  var html = '<div class="kb-health-summary"><strong>'+distinctN+'</strong> note'+(distinctN===1?'':'s')+
    ' need attention across <strong>'+totalRows+'</strong> finding'+(totalRows===1?'':'s')+'.</div>';
  ['error','warn'].forEach(function(sev){
    var cats = HEALTH_CATEGORIES.filter(function(c){ return c.sev===sev && (r[c.key]||[]).length; });
    if(!cats.length) return;
    html += '<div class="kb-health-sev-head '+(sev==='error'?'err':'warn')+'">'+(sev==='error'?'Errors':'Warnings')+'</div>';
    cats.forEach(function(c){
      var rows = r[c.key]||[];
      html += '<details class="kb-health-group"'+(sev==='error'?' open':'')+'>' +
        '<summary>'+esc(c.label)+'<span class="kb-health-cat-n '+(sev==='error'?'err':'warn')+'">'+rows.length+'</span></summary>' +
        '<div class="kb-health-rows">' +
          rows.map(function(b){
            var det = c.detail(b);
            return '<a class="kb-health-row" href="#'+encodeURIComponent(b.slug||'')+'" data-hslug="'+escAttr(b.slug||'')+'">'+
              (sev==='error'?'✗ ':'· ')+esc(b.slug||'(unknown)')+
              (det?' <span class="kb-health-detail">'+esc(det)+'</span>':'')+'</a>';
          }).join('') +
        '</div></details>';
    });
  });
  html += '<div class="kb-health-foot"><button class="eos-btn eos-btn-sm" id="kb-health-viewlist" type="button">View in list →</button></div>';
  return html;
}

// ── Digest doc → proposed KB notes (engineering pipeline stage 3) ──
var COVERAGE_LABELS = {
  'digested': 'Digested',
  'undigested': 'Undigested',
  'clauses-only': 'Clauses only',
  'metadata-only': 'Metadata only',
  'source-missing': 'Source missing',
  'missing-reference': 'Missing reference',
};

function _coverageRow(row, linked){
  var name = row.standard_id || row.standard || row.title || 'Untitled reference';
  var details = [];
  if(row.edition) details.push(row.edition);
  if(row.domain) details.push(row.domain);
  if(row.source_status === 'available') details.push('archive available');
  else if(row.source_status === 'missing') details.push('archive path missing');
  else if(linked) details.push('no local archive');
  var status = row.status || 'metadata-only';
  var count = Number(row.clause_count || 0);
  var inner = '<div><div class="kb-coverage-name">'+esc(name)+'</div>'+
    '<div class="kb-coverage-meta">'+esc(details.join(' · '))+'</div></div>'+
    '<div class="kb-coverage-side"><span class="kb-coverage-count">'+count+' clause note'+(count===1?'':'s')+'</span>'+
    '<span class="kb-coverage-status '+escAttr(status)+'">'+esc(COVERAGE_LABELS[status]||status)+'</span></div>';
  if(linked && row.slug){
    return '<a class="kb-coverage-row" href="#'+encodeURIComponent(row.slug)+'" data-coverage-slug="'+escAttr(row.slug)+'">'+inner+'</a>';
  }
  return '<div class="kb-coverage-row">'+inner+'</div>';
}

function _referenceCoverageBody(data){
  var s = data.summary || {};
  var refs = data.references || [];
  var missing = data.missing_references || [];
  var html = '<p class="kb-coverage-intro">A source archive makes a reference atomizable; clause notes make it searchable and composable. Counts show corpus coverage, not compliance with the standard.</p>'+
    '<div class="kb-coverage-stats">'+
      '<div class="kb-coverage-stat"><strong>'+Number(s.references||0)+'</strong><span>Reference notes</span></div>'+
      '<div class="kb-coverage-stat"><strong>'+Number(s.clause_notes||0)+'</strong><span>Clause notes</span></div>'+
      '<div class="kb-coverage-stat"><strong>'+Number(s.digested||0)+'</strong><span>Archived + digested</span></div>'+
      '<div class="kb-coverage-stat"><strong>'+Number(s.missing_reference_groups||0)+'</strong><span>Missing parent groups</span></div>'+
    '</div>';
  if(missing.length){
    html += '<section class="kb-coverage-section"><h3>Missing reference notes</h3><div class="kb-coverage-list">'+
      missing.map(function(row){ return _coverageRow(row, false); }).join('')+'</div></section>';
  }
  if(refs.length){
    html += '<section class="kb-coverage-section"><h3>Reference inventory</h3><div class="kb-coverage-list">'+
      refs.map(function(row){ return _coverageRow(row, true); }).join('')+'</div></section>';
  } else {
    html += EOS_UI.emptyState({message:'No kind: reference notes are indexed yet.'});
  }
  return html;
}

function _digestBody(){
  var domains = (STATE.domains||[]).map(function(d){
    return '<option value="'+escAttr(d.domain)+'">'+esc(d.domain)+'</option>';
  }).join('');
  return ''
    + '<p style="color:var(--text-muted);font-size:13px;margin:0 0 10px">Paste an algorithm / standard excerpt. Each extracted formula / concept / reference / case becomes a <b>review-gated</b> proposal — nothing is written until you Apply it in the pending dashboard.</p>'
    + '<textarea id="dg-text" rows="10" placeholder="Paste the equation + its definition + cited clauses (e.g. IEC 60287-1-1:2023 §2.1)…" style="width:100%;padding:10px;background:var(--bg-card);border:1px solid var(--border);border-radius:10px;color:var(--text);font-family:inherit;font-size:13px;resize:vertical"></textarea>'
    + '<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">'
    +   '<input id="dg-domain" list="dg-domain-list" placeholder="Domain (optional)" style="flex:1;min-width:140px;padding:8px 10px;background:var(--bg-card);border:1px solid var(--border);border-radius:10px;color:var(--text);font-family:inherit;font-size:13px">'
    +   '<datalist id="dg-domain-list">'+domains+'</datalist>'
    +   '<input id="dg-source" placeholder="Source (optional)" style="flex:1;min-width:140px;padding:8px 10px;background:var(--bg-card);border:1px solid var(--border);border-radius:10px;color:var(--text);font-family:inherit;font-size:13px">'
    + '</div>'
    + '<div style="display:flex;gap:8px;align-items:center;margin-top:12px">'
    +   '<button id="dg-go" class="eos-btn eos-btn-primary">Propose notes</button>'
    +   '<span id="dg-status" style="font-size:13px;color:var(--text-muted)"></span>'
    + '</div>'
    + '<div id="dg-result" style="margin-top:12px"></div>';
}

async function _runDigest(){
  var text = (document.getElementById('dg-text').value||'').trim();
  var statusEl = document.getElementById('dg-status');
  var resEl = document.getElementById('dg-result');
  if(!text){ statusEl.textContent = 'Paste some text first.'; return; }
  var goBtn = document.getElementById('dg-go');
  goBtn.disabled = true; statusEl.textContent = 'Extracting…';
  var res;
  try {
    res = await fetch('/kb/api/digest-doc', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        text: text,
        domain: (document.getElementById('dg-domain').value||'').trim(),
        source: (document.getElementById('dg-source').value||'').trim(),
      }),
    }).then(function(r){ return r.json(); });
  } catch(e){ statusEl.textContent = 'Digest failed: '+e.message; goBtn.disabled=false; return; }
  goBtn.disabled = false;
  if(res.error){ statusEl.textContent = res.error; return; }
  var proposed = res.proposed||[], skipped = res.skipped||[];
  statusEl.textContent = proposed.length + ' proposed' + (skipped.length? (', '+skipped.length+' skipped'):'');
  // Provenance chip — the proposals are AI-drafted; show who produced them (FDL §6).
  if(res.provenance && res.provenance.mode && EOS_UI.provenance){
    statusEl.innerHTML += ' ' + EOS_UI.provenance(res.provenance);
  }
  var rows = proposed.map(function(p){
    var refs = (p.references||[]).map(function(r){ return esc(r); }).join(' · ');
    return '<div class="note-row" style="cursor:default"><span class="note-kind kind-'+escAttr(p.kind)+'">'+esc(p.kind)+'</span>'
      + '<span class="item-slug">'+esc(p.title)+'</span>'
      + (refs? '<span style="color:var(--text-muted);font-size:12px;margin-left:8px">'+refs+'</span>':'')
      + '</div>';
  }).join('');
  resEl.innerHTML = (proposed.length
      ? '<div style="margin-bottom:8px">'+rows+'</div><p style="font-size:13px;color:var(--text-muted)">Review + Apply each in the <a href="/rooms/" style="color:var(--accent)">pending dashboard</a>.</p>'
      : '<p style="font-size:13px;color:var(--text-muted)">No extractable notes found — try a sharper excerpt with the equation + definitions.</p>')
    + (skipped.length? '<p style="font-size:12px;color:var(--text-muted)">Skipped: '+skipped.map(function(s){return esc(s.title)+' ('+esc(s.reason)+')';}).join('; ')+'</p>':'');
}

// Settings — mirrors [provides.settings] in manifest.toml. The keys are
// guideline-scoped today (guidelines absorbed from the retired guideline app).
var _kbSettings = EOS_UI.settingsPanel({
  id: 'kb-settings-panel',
  title: 'Knowledge Base Settings',
  fields: [
    { key: 'kb.guideline_default_category', label: 'Default category for new guidelines', type: 'text', default: 'general' },
    { key: 'kb.guideline_hub_panel_enabled', label: 'Show daily guideline clause on hub', type: 'boolean', default: true },
  ],
});
document.getElementById('kb-settings-btn').addEventListener('click', function(){ _kbSettings.open(); });

// Model pill — the page's AI paths (Digest doc, note-gen, flipbook generate)
// all spend the active think provider; the pill makes cost class visible +
// switchable (FDL §6, .claude/rules/model-pill.md).
if (EOS_UI.modelPill) EOS_UI.modelPill({ app: 'kb', mount: '#kb-model-pill' });

document.getElementById('digest-btn').addEventListener('click', function(){
  EOS_UI.modal({ title: 'Digest algorithm doc → KB notes', body: _digestBody(), width: 620 });
  setTimeout(function(){
    var go = document.getElementById('dg-go');
    if(go) go.addEventListener('click', _runDigest);
  }, 0);
});

document.getElementById('health-btn').addEventListener('click', async () => {
  var r;
  try { r = await fetch('/kb/api/health').then(r=>r.json()); }
  catch(e){ EOS_UI.toast('Health check failed', false); return; }
  EOS_UI.modal({ title: 'KB Health', body: _healthBody(r), width: 560 });
  // Wire row links + "view in list" after the modal mounts.
  setTimeout(function(){
    document.querySelectorAll('.kb-health-row[data-hslug]').forEach(function(a){
      a.addEventListener('click', function(e){
        e.preventDefault();
        if(EOS_UI.closeModal) EOS_UI.closeModal();
        _route.set(a.dataset.hslug);
      });
    });
    var vil = document.getElementById('kb-health-viewlist');
    if(vil) vil.addEventListener('click', function(){
      if(EOS_UI.closeModal) EOS_UI.closeModal();
      _route.clear();
      setBucket('attention');
    });
  }, 30);
});

document.getElementById('reference-coverage-btn').addEventListener('click', async function(){
  EOS_UI.modal({ title:'Reference coverage', body:'<div id="kb-reference-coverage-body"><div class="empty">Inspecting the corpus…</div></div>', width:760 });
  var mount = document.getElementById('kb-reference-coverage-body');
  try {
    var response = await fetch('/kb/api/reference-coverage');
    var data = await response.json();
    if(!response.ok || data.error) throw new Error(data.error || 'Request failed');
    if(mount) mount.innerHTML = _referenceCoverageBody(data);
    document.querySelectorAll('[data-coverage-slug]').forEach(function(link){
      link.addEventListener('click', function(event){
        event.preventDefault();
        if(EOS_UI.closeModal) EOS_UI.closeModal();
        _route.set(link.dataset.coverageSlug);
      });
    });
  } catch(error) {
    if(mount) mount.innerHTML = EOS_UI.errorState({message:'Could not load reference coverage: '+error.message});
  }
});

// List filters
document.getElementById('filter-kind').addEventListener('change', e => { STATE.filter.kind = e.target.value; STATE.visibleCount = WINDOW_STEP; renderNotes(); });
var _searchEl = document.getElementById('filter-q');
var _searchWrap = document.getElementById('kb-search');
_searchEl.addEventListener('input', e => {
  STATE.filter.q = e.target.value;
  STATE.visibleCount = WINDOW_STEP;
  _searchWrap.classList.toggle('has-q', !!e.target.value);
  renderNotes();
});
document.getElementById('kb-search-clear').addEventListener('click', () => {
  _searchEl.value = ''; STATE.filter.q = ''; _searchWrap.classList.remove('has-q');
  STATE.visibleCount = WINDOW_STEP; renderNotes(); _searchEl.focus();
});
document.getElementById('filter-sort').addEventListener('change', e => {
  STATE.sort = e.target.value;
  // Recent/Linked buckets are sort presets; a manual sort falls back to All.
  if(STATE.bucket === 'recent' || STATE.bucket === 'linked') setBucket('all');
  else { STATE.visibleCount = WINDOW_STEP; renderNotes(); }
});
// Bucket selection now lives on the launchpad tiles (wired in renderTiles()).
// Domain chips stay open on desktop across viewport changes.
var _mqDesktop = window.matchMedia('(min-width:641px)');
function _syncDomainsOpen(){ if(_mqDesktop.matches){ var d=document.getElementById('kb-domains'); if(d) d.open = true; } }
if(_mqDesktop.addEventListener) _mqDesktop.addEventListener('change', _syncDomainsOpen);
else if(_mqDesktop.addListener) _mqDesktop.addListener(_syncDomainsOpen);
_syncDomainsOpen();

// Detail tabs removed — the page is one docs surface (no tab bar).

// View-mode toggle
document.querySelectorAll('#view-toggle button').forEach(b => {
  b.addEventListener('click', () => setView(b.dataset.view));
});

// Network toolbar
document.getElementById('net-kinds').addEventListener('change', reloadNetwork);
document.getElementById('net-tag-edges').addEventListener('change', reloadNetwork);
document.getElementById('net-filter-q').addEventListener('input', applyNetworkFilter);
document.getElementById('net-domain').addEventListener('change', applyNetworkFilter);
document.getElementById('net-kind').addEventListener('change', applyNetworkFilter);
// Semantic edges: master toggle refetches (server emits them); per-kind
// checkboxes only show/hide client-side via applyNetworkFilter.
(function(){
  var sem = document.getElementById('net-semantic-edges');
  if(sem) sem.addEventListener('change', function(){
    var on = sem.checked;
    var leg = document.getElementById('net-semantic-legend');
    if(leg) leg.style.display = on ? '' : 'none';
    reloadNetwork();
  });
  ['net-edge-validates','net-edge-cites','net-edge-supersedes','net-edge-related'].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.addEventListener('change', applyNetworkFilter);
  });
})();

// Esc closes inline-expand card
document.addEventListener('keydown', e => {
  if(e.key === 'Escape' && STATE.network.card) closeNodeCard();
});

// Initial view-mode from ?view= query
(function initView(){
  var params = new URLSearchParams(window.location.search);
  var v = params.get('view');
  if(v === 'network') STATE.view = 'network';
})();

// ── Viz-artifact embed picker (note side of the artifact-embed feature) ──
// "From note" direction: pick an existing viz artifact + a durability mode and
// embed it into the open note. "From viz" direction lives in the viz app's
// "Send to note" button. Both POST /kb/api/embed → BaseApp.embed_viz_into_note.
async function openVizEmbedPicker(slug){
  var items;
  try { items = await fetch('/viz/api/list').then(r=>r.json()); }
  catch(e){ EOS_UI.toast('Could not reach the viz app', false); return; }
  if(!Array.isArray(items) || !items.length){
    EOS_UI.modal({ title:'Embed a viz artifact',
      body:'<div class="empty-side">No viz artifacts yet. Create one in <a href="/viz/" target="_blank">the viz app</a> first.</div>',
      width:520 });
    return;
  }
  window._vizEmbedTarget = slug;
  var rows = items.map(function(it){
    var label = it.prompt || '(untitled)';
    if(label.length > 80) label = label.slice(0,80) + '…';
    return '<label style="display:flex;gap:8px;align-items:flex-start;padding:8px;border:1px solid var(--border);border-radius:8px;margin-bottom:6px;cursor:pointer">'
      + '<input type="radio" name="vizpick" value="'+escAttr(it.id)+'" style="margin-top:3px">'
      + '<span><span class="note-kind" style="font-size:11px">'+esc(it.shape||'')+'</span> '
      + '<span style="font-size:13px">'+esc(label)+'</span></span></label>';
  }).join('');
  var body = '<div style="max-height:46vh;overflow:auto">'+rows+'</div>'
    + '<div style="margin-top:12px;display:flex;flex-direction:column;gap:6px">'
    + '<label style="font-size:13px"><input type="radio" name="vizmode" value="snapshot" checked> '
    + 'Snapshot <span style="color:var(--muted)">— frozen copy; source artifact stays freely deletable</span></label>'
    + '<label style="font-size:13px"><input type="radio" name="vizmode" value="reference"> '
    + 'Live reference <span style="color:var(--muted)">— updates with the source; its deletion is gated</span></label>'
    + '</div>'
    + '<div style="margin-top:14px;text-align:right">'
    + '<button onclick="submitVizEmbed()" style="padding:6px 16px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color: var(--accent-ink, #fff);cursor:pointer">Embed</button>'
    + '</div>';
  EOS_UI.modal({ title:'Embed a viz artifact', body:body, width:560 });
}

async function submitVizEmbed(){
  var slug = window._vizEmbedTarget;
  var pick = document.querySelector('input[name="vizpick"]:checked');
  if(!pick){ EOS_UI.toast('Pick an artifact first', false); return; }
  var modeEl = document.querySelector('input[name="vizmode"]:checked');
  var mode = modeEl ? modeEl.value : 'snapshot';
  var res;
  try {
    res = await fetch('/kb/api/embed', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ slug:slug, viz_id:pick.value, mode:mode }),
    }).then(r=>r.json());
  } catch(e){ EOS_UI.toast('Embed failed', false); return; }
  if(!res || !res.ok){ EOS_UI.toast((res && res.error) || 'Embed failed', false); return; }
  EOS_UI.closeModal();
  EOS_UI.toast('Embedded into note', true);
  showDetail(slug);   // reload detail so the iframe renders
}

loadAll();
