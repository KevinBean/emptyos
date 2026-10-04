/* Picture Dictionary — browse / quiz / review / speak.
 *
 * Extracted from index.html per .claude/rules/multi-module-apps.md. Loads at the
 * same position the inline block occupied, so global load order is unchanged.
 *
 * render() is a pure function of S — the state/render split in
 * .claude/rules/app-conventions-for-export.md, so the view can be re-seeded.
 */
'use strict';

var S = EOS_UI.store({
  tab: 'browse',
  status: null,
  items: null,          // null = not fetched yet (distinct from an empty result)
  pack: '',            // '' = every pack
  category: '',
  starred: false,
  q: '',
  detail: null,
  quiz: null,           // {session, rounds, i, score, answered, done, missed}
  review: null,         // {cards, i, revealed}
  speak: null,          // {slug, result, busy}
  speed: null,          // {scope, cards, i, phase, results} — see speed round
  talk: null,           // {slug, name, pack, hints, phase, result} — see talk about it
  err: '',
}, render);

var _route = null, _pollTimer = null, _recorder = null;

/* `esc` (HTML text) and `escAttr` (attribute values) are the globals from
 * eos.js. Deliberately NOT shadowed here: a local esc() that also escaped
 * quotes would silently mean something different from the same name in every
 * other page, and the next reader would have no way to know which contract
 * applies. Text contexts use esc, attribute contexts escAttr, JS-in-onclick
 * uses EOS_UI.jsArg. */

/* EOS.apiSafe hands `options` straight to fetch(), so a plain-object body would
 * be sent as the literal string "[object Object]". Serialise here; let FormData
 * through untouched so the browser can set its own multipart boundary. */
function get(path) { return EOS.apiSafe(path); }

function post(path, body) {
  if (body instanceof FormData) return EOS.apiSafe(path, { method: 'POST', body: body });
  return EOS.apiSafe(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

/* The "Show 中文" setting. Declared in the manifest, so it has to actually do
 * something — a schema key the UI never reads ships a toggle that silently does
 * nothing, which is the trap .claude/rules/app-ui-patterns.md documents. Off
 * turns the gallery into a self-test: photo and English only. */
function zh(text) {
  var st = S.get().status;
  if (st && st.show_chinese === false) return '';
  return esc(text || '');
}

/* ── data ─────────────────────────────────────────────────────────── */

async function loadStatus() {
  var st = await get('/dictionary/api/picture/status');
  if (st && st.error) { S.set({ err: st.error }); return; }
  S.set({ status: st });
  if (st.prefetch && st.prefetch.running) startPoll();
}

async function loadCatalog() {
  var qs = [];
  if (S.get().category) qs.push('category=' + encodeURIComponent(S.get().category));
  else if (S.get().pack) qs.push('pack=' + encodeURIComponent(S.get().pack));
  if (S.get().q) qs.push('q=' + encodeURIComponent(S.get().q));
  if (S.get().starred) qs.push('starred=1');
  var res = await get('/dictionary/api/picture/catalog' + (qs.length ? '?' + qs.join('&') : ''));
  if (res && res.error) { S.set({ err: res.error }); return; }
  S.set({ items: (res && res.items) || [] });
}

function startPoll() {
  if (_pollTimer) return;
  _pollTimer = setInterval(async function () {
    var p = await get('/dictionary/api/picture/images/status');
    if (!p || p.error) { stopPoll(); return; }
    var st = S.get().status || {};
    st.prefetch = p;
    if (p.coverage) st.coverage = p.coverage;   // nested: p.total is the JOB total
    S.set({ status: st });
    if (!p.running) {
      stopPoll();
      var cov = p.coverage || {};
      EOS_UI.toast('Photos ready — ' + (cov.with_photo || 0) + ' of ' +
                   (cov.total || 0) + ' animals', 'success');
      loadCatalog();
    }
  }, 1500);
}
function stopPoll() { if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; } }

async function startFetch() {
  var res = await post('/dictionary/api/picture/images/prefetch', {});
  if (res && res.error) { EOS_UI.toast(res.error, 'error'); return; }
  if (res && !res.started) { EOS_UI.toast(res.reason || 'Nothing to fetch', 'info'); return; }
  EOS_UI.toast('Fetching ' + res.total + ' photos in the background…', 'info');
  loadStatus(); startPoll();
}

/* ── render ───────────────────────────────────────────────────────── */

function render(s) {
  var sub = document.getElementById('pd-sub');
  if (sub) {
    if (s.err) sub.textContent = s.err;
    else if (!s.status) sub.textContent = 'Loading…';
    else {
      var c = s.status.coverage || {}, p = s.status.progress || {};
      sub.textContent = c.total + ' pictures · ' + c.with_photo + ' with photos · ' +
                        p.learning + ' in review · ' + p.due + ' due today' +
                        (p.said_in_scenes ? ' · ' + p.said_in_scenes + ' said in a conversation' : '');
    }
  }
  ['browse', 'quiz', 'review', 'speak'].forEach(function (t) {
    var el = document.getElementById('tab-' + t);
    if (el) el.setAttribute('aria-selected', String(s.tab === t));
  });
  renderBanner(s);

  var host = document.getElementById('pd-view');
  if (!host) return;
  if (s.err && !s.status) { host.innerHTML = EOS_UI.errorState({ message: s.err }); return; }
  if (s.tab === 'browse') host.innerHTML = s.detail ? viewDetail(s) : viewBrowse(s);
  else if (s.tab === 'quiz') host.innerHTML = viewQuiz(s);
  else if (s.tab === 'review') host.innerHTML = viewReview(s);
  else host.innerHTML = s.speed ? viewSpeedRound(s) : s.talk ? viewTalkAbout(s) : viewSpeak(s);

  if (s.tab === 'speak' && !s.speed && !s.talk && s.speak && s.speak.slug) mountRecorder();
}

/* First-run 中文 offer. The edition defaults to English-only (A4), so the first
 * time a learner opens the picture cards we offer to turn Chinese glosses on.
 * The "already offered" mark is per-browser (localStorage) rather than a server
 * setting: the internal flag is not a declared schema key, so in a hosted
 * (user-posture) build the settings write allowlist would refuse it. The actual
 * CHOICE writes the real schema-declared `picture-dict.show_chinese` setting,
 * which a learner IS allowed to set. */
function pdZhPrompted() {
  try { return localStorage.getItem('pd_zh_prompted') === '1'; } catch (e) { return false; }
}
function pdMarkZhPrompted() {
  try { localStorage.setItem('pd_zh_prompted', '1'); } catch (e) { /* private mode */ }
}
function _shouldOfferChinese(s) {
  return !!(s.status && s.status.show_chinese === false) && !pdZhPrompted();
}
async function pdEnableChinese() {
  pdMarkZhPrompted();
  await post('/settings/api/set', { key: 'picture-dict.show_chinese', value: true });
  await loadStatus();  // re-render cards with 中文
}
function pdDismissChinese() {
  pdMarkZhPrompted();
  S.render();  // fall through to the normal banner
}

function renderBanner(s) {
  var slot = document.getElementById('pd-banner-slot');
  if (!slot) return;
  if (_shouldOfferChinese(s)) {
    slot.innerHTML =
      '<div class="pd-banner"><span>中</span>' +
      '<span class="pd-b-txt">Show Chinese (中文) glosses on the cards? ' +
      'You can change this any time in Settings.</span>' +
      '<button class="eos-btn eos-btn-sm" onclick="pdEnableChinese()">Show 中文</button>' +
      '<button class="eos-btn eos-btn-sm" onclick="pdDismissChinese()">Keep English only</button></div>';
    return;
  }
  var p = (s.status && s.status.prefetch) || {};
  var gate = (s.status && s.status.network) || {};
  if (gate.enabled === false) {
    slot.innerHTML = '<div class="pd-banner"><span>📷</span><span class="pd-b-txt">' +
      esc(gate.reason || 'Photo lookup is unavailable here.') +
      ' The gallery still works — every animal shows its emoji tile.</span></div>';
    return;
  }
  if (!p.running) { slot.innerHTML = ''; return; }
  var pct = p.total ? Math.round(100 * p.done / p.total) : 0;
  slot.innerHTML =
    '<div class="pd-banner"><span>📷</span>' +
    '<progress max="100" value="' + pct + '"></progress>' +
    '<span class="pd-b-txt">' + p.done + ' / ' + p.total +
    (p.current ? ' · ' + esc(p.current) : '') +
    (p.eta_s ? ' · ~' + p.eta_s + 's left' : '') + '</span>' +
    '<button class="eos-btn eos-btn-sm" onclick="cancelFetch()">Stop</button></div>';
}

async function cancelFetch() {
  await post('/dictionary/api/picture/images/cancel', {});
  stopPoll(); loadStatus();
}

/* ── browse ───────────────────────────────────────────────────────── */

/* A cached photo can be deleted under us, so a tile must survive a 404 without
 * ever showing a broken-image glyph. The fallback tile is rendered alongside and
 * revealed by a *named* handler — building the replacement markup inside the
 * onerror attribute would need nested quotes and HTML entities, which is both
 * fragile and outside the eos-csp-bridge grammar. */
function pdImgFail(img) {
  img.style.display = 'none';
  var fb = img.nextElementSibling;
  if (fb) fb.style.display = 'flex';
}

function tile(it) {
  var img = it.image
    ? '<img class="pd-thumb" src="' + escAttr(it.image) + '" alt="" loading="lazy" onerror="pdImgFail(this)">' +
      '<div class="pd-thumb-fallback" style="display:none">' + esc(it.emoji) + '</div>'
    : '<div class="pd-thumb-fallback">' + esc(it.emoji) + '</div>';
  return '<button class="pd-card" onclick="openDetail(' + EOS_UI.jsArg(it.slug) + ')">' + img +
    '<div class="pd-card-body"><div class="pd-name">' + esc(it.name) +
    (it.starred ? ' <span class="pd-star">★</span>' : '') + '</div>' +
    '<div class="pd-zh">' + zh(it.chinese) + '</div></div></button>';
}

function viewBrowse(s) {
  /* Group chips are qualified (`animals:sea`), so with more than one pack the
   * flat list stops being readable — narrow it to the chosen pack first. The id
   * still round-trips to the API untouched. */
  var packs = (s.status && s.status.packs) || [];
  var packRow = (packs.length > 1 || window.EOS_PACK_COMPOSE) ? packRowHtml(s, packs) : '';
  var cats = ((s.status && s.status.categories) || [])
    .filter(function (c) { return !s.pack || c.pack === s.pack; });
  var chips = '<button class="pd-chip" aria-pressed="' + (!s.category) +
    '" onclick="pickCategory(\'\')">All</button>';
  cats.forEach(function (c) {
    chips += '<button class="pd-chip" aria-pressed="' + (s.category === c.id) +
      '" onclick="pickCategory(' + EOS_UI.jsArg(c.id) + ')">' + esc(c.label) +
      ' <span class="pd-muted">' + c.count + '</span></button>';
  });
  chips += '<button class="pd-chip" aria-pressed="' + s.starred +
    '" onclick="toggleStarredOnly()" title="Show only the animals you saved">★ Starred</button>';
  chips += '<input class="pd-search" id="pd-q" placeholder="Search name, 中文 or pinyin…" ' +
           'value="' + escAttr(s.q) + '" oninput="onSearch(this.value)">';

  var body;
  if (s.items === null) body = '<div class="pd-center pd-muted">Loading…</div>';
  else if (!s.items.length) body = EOS_UI.emptyState({
    icon: '🔍', title: 'Nothing matches',
    message: 'Try another category, or clear the search box.',
  });
  else body = '<div class="pd-grid">' + s.items.map(tile).join('') + '</div>';

  return packRow + '<div class="pd-chips">' + chips + '</div>' + body;
}

function packRowHtml(s, packs) {
  var row = '<button class="pd-chip" aria-pressed="' + (!s.pack) +
    '" onclick="clearPack()">All packs</button>';
  packs.forEach(function (pk) {
    row += '<button class="pd-chip" aria-pressed="' + (s.pack === pk.id) +
      '" onclick="pickPack(' + EOS_UI.jsArg(pk.id) + ')">' +
      (pk.emoji ? esc(pk.emoji) + ' ' : '') + esc(pk.title) +
      ' <span class="pd-muted">' + pk.count + '</span></button>';
  });
  if (window.EOS_PACK_COMPOSE) row += window.EOS_PACK_COMPOSE.affordance();
  return '<div class="pd-chips pd-packs">' + row + '</div>';
}

/* "Said in a conversation 3× · last 2026-09-23" — producing a word in an
 * Improv scene, recorded by the dictionary's improv_result. Empty until then. */
function pictureSceneLine(scene) {
  var n = (scene && scene.said) || 0;
  if (!n) return '';
  return '<div class="pd-muted" style="font-size:.82rem">💬 Said in a conversation ' + n + '×' +
    (scene.last ? ' · last ' + esc(scene.last) : '') + '</div>';
}

function viewDetail(s) {
  var it = s.detail;
  var media = it.image
    ? '<img src="' + escAttr(it.image) + '" alt="' + escAttr(it.name) + '">'
    : '<div class="pd-big-emoji">' + esc(it.emoji) + '</div>';
  var cr = it.credit || {};
  var credit = '';
  if (cr.artist || cr.license) {
    credit = '<div class="pd-credit">Photo: ' + esc(cr.artist || 'unknown') +
      (cr.license ? ' · ' + esc(cr.license) : '') +
      (cr.source ? ' · <a href="' + escAttr(cr.source) + '" target="_blank" rel="noopener">source</a>' : '') +
      '</div>';
  }
  return '<button class="eos-btn eos-btn-sm" onclick="closeDetail()">&larr; Back</button>' +
    '<div class="pd-detail" style="margin-top:12px">' +
    '<div>' + media + credit + '</div>' +
    '<div><h2>' + esc(it.name) + '</h2>' +
    '<div class="pd-zh">' + zh(it.chinese) + (it.pinyin ? ' · ' + esc(it.pinyin) : '') + '</div>' +
    (it.hint ? '<p>' + esc(it.hint) + '</p>' : '') +
    '<div class="pd-muted" style="font-size:.82rem">Group: ' + esc(it.category) + '</div>' +
    pictureSceneLine(it.scene) +
    '<div class="pd-actions">' +
    '<button class="eos-btn eos-btn-sm" onclick="say(' + EOS_UI.jsArg(it.slug) + ')">🔊 Hear it</button>' +
    '<button class="eos-btn eos-btn-sm" onclick="practise(' + EOS_UI.jsArg(it.slug) + ')">🎤 Say it</button>' +
    '<button class="eos-btn eos-btn-sm" onclick="toggleStar(' + EOS_UI.jsArg(it.slug) + ',' + (!it.starred) + ')">' +
      (it.starred ? '★ Saved' : '☆ Save to my vocabulary') + '</button>' +
    (it.enrolled ? '' : '<button class="eos-btn eos-btn-sm" onclick="enroll(' +
      EOS_UI.jsArg(it.slug) + ')">➕ Add to review</button>') +
    '<button class="eos-btn eos-btn-sm" onclick="refetch(' + EOS_UI.jsArg(it.slug) + ')">Wrong photo?</button>' +
    '</div></div></div>';
}

function pickCategory(c) { S.set({ category: c }); loadCatalog(); }
function clearPack() { pickPack(''); }

/* Choosing a pack clears the group filter: a group id is scoped to its pack, so
 * carrying `animals:sea` into Food would filter to nothing and look broken. */
function pickPack(p) { S.set({ pack: p, category: '' }); loadCatalog(); }
function toggleStarredOnly() { S.set({ starred: !S.get().starred }); loadCatalog(); }
var _searchT = null;
function onSearch(v) {
  S.set({ q: v });
  if (_searchT) clearTimeout(_searchT);
  _searchT = setTimeout(loadCatalog, 220);
}

async function openDetail(slug) {
  var it = await get('/dictionary/api/picture/item/' + encodeURIComponent(slug));
  if (!it || it.error) { EOS_UI.toast((it && it.error) || 'Not found', 'error'); return; }
  S.set({ tab: 'browse', detail: it });
  if (_route) _route.set(slug, { silent: true });   // silent: we already rendered
}
function closeDetail() { S.set({ detail: null }); if (_route) _route.clear(); }

async function say(slug) {
  var r = await get('/dictionary/api/picture/say/' + encodeURIComponent(slug));
  if (!r || r.error) { EOS_UI.toast((r && r.error) || 'No voice engine', 'error'); return; }
  new Audio(r.audio_url).play().catch(function () {});
}

async function toggleStar(slug, on) {
  var url = on ? '/dictionary/api/picture/save-word' : '/dictionary/api/picture/unsave';
  var r = await post(url, { slug: slug });
  if (!r || r.error) { EOS_UI.toast((r && r.error) || 'Could not save', 'error'); return; }
  EOS_UI.toast(on ? 'Saved to your vocabulary' : 'Removed the star', 'success');
  openDetail(slug); loadStatus();
}

async function enroll(slug) {
  await post('/dictionary/api/picture/srs/enroll', { slug: slug });
  EOS_UI.toast('Added to your review queue', 'success');
  openDetail(slug); loadStatus();
}

async function refetch(slug) {
  EOS_UI.toast('Looking for another photo…', 'info');
  var r = await get('/dictionary/api/picture/images/refetch/' + encodeURIComponent(slug),
                            { method: 'POST', body: {} });
  if (!r || r.error) { EOS_UI.toast((r && r.error) || 'No other photo found', 'error'); return; }
  openDetail(slug); loadCatalog();
}

/* ── quiz ─────────────────────────────────────────────────────────── */

function viewQuiz(s) {
  var q = s.quiz;
  if (!q) {
    var cats = (s.status && s.status.categories) || [];
    var opts = '<option value="">Every animal</option>' + cats.map(function (c) {
      return '<option value="' + escAttr(c.id) + '">' + esc(c.label) + ' (' + c.count + ')</option>';
    }).join('');
    return '<div class="pd-panel pd-center"><h3>Photo &rarr; name</h3>' +
      '<p class="pd-muted">Pick the right English name for the animal in the photo.</p>' +
      '<p><select id="pd-quiz-cat" class="pd-search" style="max-width:280px">' + opts + '</select></p>' +
      '<button class="eos-btn eos-btn-primary" onclick="quizStart()">Start quiz</button></div>';
  }
  if (q.done) {
    var missed = q.missed && q.missed.length
      ? '<p class="pd-muted">Added to your review queue:</p><div class="pd-grid">' +
        q.missed.map(tile).join('') + '</div>'
      : '<p class="pd-muted">Nothing missed. </p>';
    return '<div class="pd-panel pd-center"><div class="pd-score">' + q.score + ' / ' + q.total + '</div>' +
      missed + '<div style="margin-top:14px"><button class="eos-btn eos-btn-primary" ' +
      'onclick="quizReset()">Another quiz</button></div></div>';
  }
  var r = q.rounds[q.i];
  var media = r.answer_image
    ? '<img class="pd-q-photo" src="' + escAttr(r.answer_image) + '" alt="">'
    : '<div class="pd-q-emoji">' + esc(r.answer_emoji) + '</div>';
  var opts = r.options.map(function (o) {
    var cls = 'pd-opt';
    if (q.picked) {
      if (o.slug === q.answer) cls += ' is-right';
      else if (o.slug === q.picked) cls += ' is-wrong';
    }
    return '<button class="' + cls + '"' + (q.picked ? ' disabled' : '') +
      ' onclick="quizAnswer(' + EOS_UI.jsArg(o.slug) + ')">' + esc(o.name) + '</button>';
  }).join('');
  return '<div class="pd-panel"><div class="pd-muted pd-center">Question ' + (q.i + 1) +
    ' of ' + q.total + ' · score ' + q.score + '</div>' +
    '<div style="margin:12px 0">' + media + '</div>' +
    '<div class="pd-opts">' + opts + '</div>' +
    '<div class="pd-q-feedback">' + esc(q.feedback || '') + '</div>' +
    (q.picked ? '<div class="pd-center"><button class="eos-btn eos-btn-primary" onclick="quizNext()">' +
      (q.i + 1 >= q.total ? 'See results' : 'Next') + '</button></div>' : '') + '</div>';
}

async function quizStart() {
  var el = document.getElementById('pd-quiz-cat');
  var res = await post('/dictionary/api/picture/quiz/start', { category: el ? el.value : '' });
  if (!res || res.error) { EOS_UI.toast((res && res.error) || 'Could not start', 'error'); return; }
  S.set({ quiz: { session: res.session, rounds: res.rounds, total: res.total,
                  i: 0, score: 0, picked: '', answer: '', feedback: '', done: false } });
}

async function quizAnswer(slug) {
  var q = S.get().quiz;
  if (!q || q.picked) return;
  var res = await post('/dictionary/api/picture/quiz/answer', { session: q.session, round: q.rounds[q.i].index, choice: slug });
  if (!res || res.error) { EOS_UI.toast((res && res.error) || 'Could not answer', 'error'); return; }
  q.picked = slug; q.answer = res.answer; q.score = res.score;
  q.feedback = res.correct
    ? '✓ ' + res.answer_name + ' — ' + res.answer_chinese
    : '✗ It was ' + res.answer_name + ' — ' + res.answer_chinese +
      (res.answer_hint ? '. ' + res.answer_hint : '');
  S.set({ quiz: q });
}

async function quizNext() {
  var q = S.get().quiz;
  if (q.i + 1 >= q.total) {
    var res = await post('/dictionary/api/picture/quiz/finish', { session: q.session });
    q.done = true;
    q.missed = (res && res.missed) || [];
    S.set({ quiz: q }); loadStatus(); loadCatalog();
    return;
  }
  q.i += 1; q.picked = ''; q.answer = ''; q.feedback = '';
  S.set({ quiz: q });
}
function quizReset() { S.set({ quiz: null }); }

/* ── review ───────────────────────────────────────────────────────── */

function viewReview(s) {
  var r = s.review;
  if (!r) return '<div class="pd-center pd-muted">Loading…</div>';
  if (!r.cards.length) {
    return EOS_UI.emptyState({
      icon: '✅', title: 'Nothing due right now',
      message: 'Cards arrive here when you miss one in a quiz, star an animal, or press "Add to review". ' +
               'They also show up in your daily Review.',
    }) + '<div class="pd-center"><a class="eos-btn eos-btn-sm" href="/learn/">Open daily Review</a></div>';
  }
  if (r.i >= r.cards.length) {
    return '<div class="pd-panel pd-center"><h3>Done for now</h3>' +
      '<p class="pd-muted">Reviewed ' + r.cards.length + ' card' + (r.cards.length === 1 ? '' : 's') + '.</p>' +
      '<button class="eos-btn eos-btn-primary" onclick="loadPictureReview()">Check again</button></div>';
  }
  var c = r.cards[r.i];
  var media = c.image ? '<img class="pd-q-photo" src="' + escAttr(c.image) + '" alt="">'
                      : '<div class="pd-q-emoji">' + esc(c.emoji) + '</div>';
  var back = r.revealed
    ? '<div class="pd-center" style="margin-top:12px"><div class="pd-name" style="font-size:1.3rem">' +
      esc(c.name) + '</div><div class="pd-zh">' + zh(c.chinese) +
      (c.pinyin ? ' · ' + esc(c.pinyin) : '') + '</div>' +
      (c.hint ? '<p class="pd-muted">' + esc(c.hint) + '</p>' : '') +
      '<button class="eos-btn eos-btn-sm" onclick="say(' + EOS_UI.jsArg(c.slug) + ')">🔊 Hear it</button></div>' +
      '<div class="pd-rev-grades">' +
      ['again', 'hard', 'good', 'easy'].map(function (g) {
        return '<button class="eos-btn" onclick="grade(' + EOS_UI.jsArg(g) + ')">' +
               g.charAt(0).toUpperCase() + g.slice(1) + '</button>';
      }).join('') + '</div>'
    : '<div class="pd-center" style="margin-top:14px">' +
      '<button class="eos-btn eos-btn-primary" onclick="reveal()">Show answer</button></div>';
  return '<div class="pd-panel"><div class="pd-muted pd-center">Card ' + (r.i + 1) +
    ' of ' + r.cards.length + '</div><div style="margin:12px 0">' + media + '</div>' + back + '</div>';
}

// NOT `loadReview` — index.html's inline script declares a `loadReview()` of its
// own for the WORD flashcard deck, this file is loaded after it, and both land in
// the same global scope ('use strict', no module, no IIFE). The later declaration
// silently won, so opening Practice -> Flashcards called the picture loader, which
// never touches #review-area: the word review rendered nothing at all, with no
// error anywhere. Keep every global in this file namespaced to pictures.
async function loadPictureReview() {
  var res = await get('/dictionary/api/picture/srs/due?limit=30');
  S.set({ review: { cards: (res && res.cards) || [], i: 0, revealed: false } });
}
function reveal() { var r = S.get().review; r.revealed = true; S.set({ review: r }); }
async function grade(g) {
  var r = S.get().review, c = r.cards[r.i];
  await post('/dictionary/api/picture/srs/grade', { slug: c.slug, rating: g });
  r.i += 1; r.revealed = false;
  S.set({ review: r }); loadStatus();
}

/* ── speak ────────────────────────────────────────────────────────── */

function viewSpeak(s) {
  var sp = s.speak;
  if (!sp || !sp.slug) {
    return '<div class="pd-panel pd-center"><h3>Say the word</h3>' +
      '<p class="pd-muted">Open any animal in Browse and press “🎤 Say it”, ' +
      'or start with one that is due for review.</p>' +
      '<button class="eos-btn eos-btn-primary" onclick="speakPickDue()">Pick one for me</button> ' +
      '<button class="eos-btn" onclick="speedRoundSetup()">⚡ Speed round</button> ' +
      '<button class="eos-btn" onclick="talkAboutPick()">🗣 Talk about it</button> ' +
      improvSceneButton('🎭 Improv scene') + '</div>';
  }
  var res = sp.result;
  var out = '';
  if (res) {
    var pct = Math.round((res.score || 0) * 100);
    out = '<div class="pd-center" style="margin-top:14px">' +
      '<div class="pd-score">' + pct + '%' + (res.passed ? ' ✅' : '') + '</div>' +
      (res.heard ? '<div class="pd-muted">Heard: “' + esc(res.heard) + '”</div>' : '') +
      (res.weak_phones && res.weak_phones.length
        ? '<div style="margin-top:8px">' + res.weak_phones.map(function (p) {
            return '<span class="pd-phone">' + esc(p) + '</span>'; }).join('') + '</div>'
        : '') +
      (res.scorer_note ? '<div class="pd-note">' + esc(res.scorer_note) + '</div>' : '') +
      '</div>';
  }
  return '<div class="pd-panel pd-center">' +
    (sp.image ? '<img class="pd-q-photo" src="' + escAttr(sp.image) + '" alt="">'
              : '<div class="pd-q-emoji">' + esc(sp.emoji || '🐾') + '</div>') +
    '<h3 style="margin:10px 0 2px">' + esc(sp.name) + '</h3>' +
    '<div class="pd-zh">' + zh(sp.chinese) + '</div>' +
    '<div style="margin:12px 0"><button class="eos-btn eos-btn-sm" onclick="say(' +
      EOS_UI.jsArg(sp.slug) + ')">🔊 Hear it first</button></div>' +
    '<div id="pd-rec"></div><div id="pd-rec-hint" class="pd-note"></div>' +
    (sp.busy ? '<div class="pd-muted" style="margin-top:10px">Scoring…</div>' : '') + out +
    '<div style="margin-top:14px"><button class="eos-btn eos-btn-sm" onclick="speakPickDue()">' +
    'Another animal</button> <button class="eos-btn eos-btn-sm" onclick="speedRoundSetup()">' +
    '⚡ Speed round</button> <button class="eos-btn eos-btn-sm" onclick="talkAboutPick(' +
    EOS_UI.jsArg(sp.slug) + ')">🗣 Talk about it</button></div></div>';
}

async function practise(slug) {
  var it = await get('/dictionary/api/picture/item/' + encodeURIComponent(slug));
  if (!it || it.error) return;
  S.set({ tab: 'speak', detail: null, speak: { slug: it.slug, name: it.name,
          chinese: it.chinese, image: it.image, emoji: it.emoji, result: null, busy: false } });
  if (_route) _route.clear();
}

async function speakPickDue() {
  var res = await get('/dictionary/api/picture/srs/due?limit=1');
  if (res && res.cards && res.cards.length) { practise(res.cards[0].slug); return; }
  var cat = await get('/dictionary/api/picture/catalog');
  var pool = (cat && cat.items) || [];
  if (!pool.length) { EOS_UI.toast('No animals loaded', 'error'); return; }
  practise(pool[Math.floor(Math.random() * pool.length)].slug);
}

function mountRecorder() {
  var mount = document.getElementById('pd-rec');
  if (!mount || mount.dataset.mounted === '1') return;
  mount.dataset.mounted = '1';
  _recorder = EOS_UI.recorder({
    mount: mount, label: '● Say the word', stopLabel: '■ Stop',
    maxMs: 8000, minBytes: 1200,
    onBlob: onSpoken,
    onError: function (msg) {
      var h = document.getElementById('pd-rec-hint');
      if (h) h.textContent = msg;
    },
  });
}

async function onSpoken(blob) {
  var sp = S.get().speak;
  if (!sp) return;
  S.set({ speak: Object.assign({}, sp, { busy: true, result: null }) });

  var fd = new FormData();
  fd.append('audio', blob, 'say.webm');
  var up = await post('/dictionary/api/picture/speak/upload', fd);
  if (!up || up.error) {
    S.set({ speak: Object.assign({}, sp, { busy: false }) });
    EOS_UI.toast((up && up.error) || 'Upload failed', 'error');
    return;
  }
  var res = await post('/dictionary/api/picture/speak/attempt', { slug: sp.slug, audio_path: up.audio_path });
  if (!res || res.error) {
    S.set({ speak: Object.assign({}, sp, { busy: false }) });
    EOS_UI.toast((res && res.error) || 'Could not score that', 'error');
    return;
  }
  S.set({ speak: Object.assign({}, sp, { busy: false, result: res }) });
  loadStatus();
}

/* ── speed round ──────────────────────────────────────────────────────
 *
 * Ten pictures from whatever the learner is browsing. Each picture appears
 * only once the microphone is recording, so the silence before the first
 * sound IS the reaction time; the server measures it from the audio.
 *
 * The recorder lives in a hidden element outside #pd-view: render() replaces
 * that view on every state change, and one recorder must survive the round.
 * Scoring runs in the background so the next picture never waits on it.
 * Pure helpers (labels, pick, summary) are in speed-round.js.
 */

var _speedRec = null;

function speedRoundScope(s) {
  var packs = (s.status && s.status.packs) || [];
  var cats = (s.status && s.status.categories) || [];
  if (s.category) {
    var c = cats.filter(function (x) { return x.id === s.category; })[0];
    if (c) return c.pack_title + ' · ' + c.label;
  }
  if (s.pack) {
    var p = packs.filter(function (x) { return x.id === s.pack; })[0];
    if (p) return p.title;
  }
  return s.starred ? 'Starred' : 'All packs';
}

var _speedRoundSeq = 0;

async function speedRoundSetup() {
  if (S.get().items === null) await loadCatalog();
  var s = S.get();
  var cards = speedRoundPick(s.items || [], SPEED_ROUND_SIZE);
  if (cards.length < 4) {
    EOS_UI.toast('Pick a pack with at least 4 pictures in Browse first.', 'info');
    return;
  }
  // `id` separates rounds: a late score from an earlier round must never
  // land in this one, even when a small pack reshuffles the same words.
  if (S.get().talk) talkAboutQuit();
  S.set({ tab: 'speak', detail: null, speed: {
    id: ++_speedRoundSeq, scope: speedRoundScope(s), cards: cards, i: 0,
    phase: 'ready', results: [] } });
}

function speedRoundQuit() {
  var rec = _speedRec;
  S.set({ speed: null });          // first, so the stop's blob is ignored
  if (rec && rec.isRecording()) rec.stop();
}

/* Cap per card: the longest time limit on offer (3 s) plus ~2 s to say the
 * word, so a slow-but-right answer is heard in full and reads as slow, not
 * as cut off. The learner can move on sooner with Next. */
var SPEED_ROUND_MAX_MS = 5000;

function speedRoundRecorder() {
  if (_speedRec) return _speedRec;
  var host = document.createElement('div');
  host.hidden = true;
  document.body.appendChild(host);
  _speedRec = EOS_UI.recorder({
    mount: host, maxMs: SPEED_ROUND_MAX_MS, minBytes: 600,
    onStart: function () {
      // The microphone takes a moment to open. If the round was stopped or
      // hidden meanwhile, close it again straight away.
      var sp = S.get().speed;
      if (!sp || sp.phase !== 'arming') { _speedRec.stop(); return; }
      S.set({ speed: Object.assign({}, sp, { phase: 'live' }) });
    },
    onBlob: speedRoundOnBlob,
    onError: function (msg) {
      var sp = S.get().speed;
      if (!sp) return;
      EOS_UI.toast(msg, 'error');
      S.set({ speed: Object.assign({}, sp, { phase: 'ready' }) });
    },
  });
  return _speedRec;
}

/* Resolve once the picture is ready to paint (or after 3 s regardless), so
 * download time is never counted as the learner's reaction time. */
function speedRoundPreload(src) {
  return new Promise(function (done) {
    if (!src) { done(); return; }
    var img = new Image();
    var timer = setTimeout(done, 3000);
    var finish = function () { clearTimeout(timer); done(); };
    img.onload = function () { if (img.decode) img.decode().then(finish, finish); else finish(); };
    img.onerror = finish;
    img.src = src;
  });
}

/* Get the current card on screen: preload its picture, then open the mic.
 * The picture appears only once recording has started (onStart). */
async function speedRoundArm() {
  var sp = S.get().speed;
  if (!sp || sp.i >= sp.cards.length) return;
  var id = sp.id, i = sp.i;
  S.set({ speed: Object.assign({}, sp, { phase: 'arming' }) });
  await speedRoundPreload(sp.cards[i].image);
  var cur = S.get().speed;
  if (!cur || cur.id !== id || cur.i !== i || cur.phase !== 'arming') return;
  // Never switch the microphone on for a round nobody can see: the learner
  // may have moved to another part of the dictionary. Wait for Start.
  if (!speedRoundVisible()) { S.set({ speed: Object.assign({}, cur, { phase: 'ready' }) }); return; }
  speedRoundRecorder().start();
}

function speedRoundGo() { speedRoundArm(); }

function speedRoundNext() {
  if (_speedRec && _speedRec.isRecording()) _speedRec.stop();
}

function speedRoundOnBlob(blob) {
  var sp = S.get().speed;
  if (!sp || sp.phase !== 'live') return;   // a recording the round no longer wants
  var card = sp.cards[sp.i];
  var idx = sp.results.length;
  var results = sp.results.concat([{ slug: card.slug, name: card.name,
                                     chinese: card.chinese, verdict: 'pending' }]);
  var next = sp.i + 1;
  var more = next < sp.cards.length;
  S.set({ speed: Object.assign({}, sp, { results: results, i: next,
                                          phase: more ? 'arming' : 'done' }) });
  speedRoundScore(sp.id, idx, card.slug, blob);
  if (more) setTimeout(function () {
    var cur = S.get().speed;
    if (cur && cur.id === sp.id && cur.i === next && cur.phase === 'arming') speedRoundArm();
  }, 700);
}

function speedRoundVisible() {
  var view = document.getElementById('pd-view');
  return S.get().tab === 'speak' && !document.hidden && !!view && view.offsetParent !== null;
}

async function speedRoundScore(id, idx, slug, blob) {
  var fd = new FormData();
  fd.append('audio', blob, 'fast.webm');
  var up = await post('/dictionary/api/picture/speak/upload', fd);
  var res = (up && !up.error)
    ? await post('/dictionary/api/picture/speak/fast', { slug: slug, audio_path: up.audio_path })
    : up;
  var sp = S.get().speed;
  if (!sp || sp.id !== id || !sp.results[idx]) return;
  var results = sp.results.slice();
  results[idx] = Object.assign({}, results[idx], (res && !res.error)
    ? { verdict: res.verdict, onset_s: res.onset_s, heard: res.heard || '',
        enrolled: !!res.enrolled, pron: res.pronunciation_weak ? res.score : null }
    : { verdict: 'error', note: (res && res.error) || 'Could not score that' });
  S.set({ speed: Object.assign({}, sp, { results: results }) });
  if (sp.phase === 'done' && speedRoundSummary(results).pending === 0) loadStatus();
}

function viewSpeedRound(s) {
  var sp = s.speed;
  var head = '<div class="pd-panel pd-center"><h3 style="margin-top:0">⚡ Speed round · ' +
    esc(sp.scope) + '</h3>';
  if (sp.phase === 'ready') {
    return head + '<p class="pd-muted">' + sp.cards.length + ' pictures. Each one appears ' +
      'while the microphone is listening — say its English name out loud as soon as you ' +
      'know it. Wrong or slow words are added to Review.</p>' +
      '<button class="eos-btn eos-btn-primary" onclick="speedRoundGo()">Start</button> ' +
      '<button class="eos-btn" onclick="speedRoundQuit()">Cancel</button></div>';
  }
  if (sp.phase === 'done') return head + viewSpeedRoundResults(sp) + '</div>';

  var card = sp.cards[sp.i];
  var live = sp.phase === 'live';
  var pic = !live ? '<div class="pd-q-emoji" aria-label="Get ready">…</div>'
    : card.image ? '<img class="pd-q-photo" src="' + escAttr(card.image) + '" alt="">'
    : '<div class="pd-q-emoji">' + esc(card.emoji || '🐾') + '</div>';
  return head + '<div class="pd-muted">' + (sp.i + 1) + ' of ' + sp.cards.length + '</div>' +
    pic +
    '<div class="pd-note">' + (live ? '🎙 Listening — say it now' : 'Get ready…') + '</div>' +
    '<div style="margin-top:12px"><button class="eos-btn eos-btn-sm" onclick="speedRoundNext()"' +
    (live ? '' : ' disabled') + '>Next ⏭</button> ' +
    '<button class="eos-btn eos-btn-sm" onclick="speedRoundQuit()">Stop round</button></div></div>';
}

function viewSpeedRoundResults(sp) {
  var sum = speedRoundSummary(sp.results);
  var line = sum.remembered + ' of ' + sum.total + ' remembered' +
    (sum.avg_s !== null ? ' · average ' + sum.avg_s.toFixed(1) + ' s' : '') +
    (sum.pending ? ' · scoring ' + sum.pending + '…' : '');
  var rows = sp.results.map(function (r) {
    var time = typeof r.onset_s === 'number' ? r.onset_s.toFixed(1) + ' s' : '—';
    return '<tr><td>' + esc(r.name) + '</td><td>' + zh(r.chinese) + '</td><td>' + time +
      '</td><td>' + EOS_UI.statusBadge(speedRoundLabel(r.verdict), r.verdict, SPEED_ROUND_BADGE) +
      (r.heard ? ' <span class="pd-muted">heard “' + esc(r.heard) + '”</span>' : '') +
      (typeof r.pron === 'number'
        ? ' <span class="pd-muted">· say it more clearly (' + Math.round(r.pron * 100) + '%)</span>'
        : '') +
      (r.note ? ' <span class="pd-muted">' + esc(r.note) + '</span>' : '') + '</td></tr>';
  }).join('');
  var queued = sp.results.filter(function (r) { return r.enrolled; }).length;
  return '<div class="pd-score" style="font-size:1.4rem">' + esc(line) + '</div>' +
    '<table class="pc-table" style="margin:12px 0;text-align:left">' +
    '<thead><tr><th>Word</th><th>中文</th><th>Time</th><th>Result</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>' +
    (queued ? '<p class="pd-note">' + queued + (queued === 1 ? ' new word' : ' new words') +
      ' added to Review.</p>' : '') +
    '<button class="eos-btn eos-btn-primary" onclick="speedRoundSetup()">Another round</button> ' +
    '<button class="eos-btn" onclick="speedRoundQuit(); showTab(\'review\')">Go to Review</button> ' +
    '<button class="eos-btn" onclick="speedRoundQuit()">Done</button>';
}

/* ── talk about it ────────────────────────────────────────────────────
 *
 * One picture, 30–60 seconds of free speech. The server transcribes it and
 * scores fluency with the same scorer Speaking Practice uses (pace, fillers,
 * repeats, long pauses), plus whether the name and the pack's other words
 * were used. Like the speed round, the recorder lives outside #pd-view so a
 * re-render never destroys it; the running clock is written straight into
 * its element instead of through state, so the picture does not repaint
 * every 250 ms. Pure helpers are in talk-about.js.
 */

var _talkRec = null;
var _talkSeq = 0;

async function talkAboutPick(slug) {
  if (S.get().items === null) await loadCatalog();
  var s = S.get();
  var pool = s.items || [];
  var it = slug ? pool.filter(function (x) { return x.slug === slug; })[0] : null;
  if (!it && slug) it = await get('/dictionary/api/picture/item/' + encodeURIComponent(slug));
  if (!it || it.error) it = pool[Math.floor(Math.random() * pool.length)];
  if (!it) { EOS_UI.toast('Pick a pack in Browse first.', 'info'); return; }
  if (S.get().talk) talkAboutQuit();
  var pack = talkAboutScenePack(it, s.pack);
  var scene = pack ? await get('/dictionary/api/picture/catalog?pack=' + encodeURIComponent(pack)) : null;
  S.set({ tab: 'speak', detail: null, speed: null, talk: {
    id: ++_talkSeq, slug: it.slug, name: it.name, chinese: it.chinese, image: it.image,
    emoji: it.emoji, pack: pack, hints: talkAboutHints((scene && scene.items) || [], it.slug),
    phase: 'ready', result: null } });
}

function talkAboutQuit() {
  var rec = _talkRec;
  S.set({ talk: null });           // first, so the stop's blob is ignored
  if (rec && rec.isRecording()) rec.stop();
}

function talkAboutRecorder() {
  if (_talkRec) return _talkRec;
  var host = document.createElement('div');
  host.hidden = true;
  document.body.appendChild(host);
  _talkRec = EOS_UI.recorder({
    mount: host, maxMs: TALK_ABOUT_MAX_MS,
    onStart: function () {
      var t = S.get().talk;
      if (!t || t.phase !== 'arming') { _talkRec.stop(); return; }
      S.set({ talk: Object.assign({}, t, { phase: 'recording' }) });
    },
    onTick: function (ms) {
      var el = document.getElementById('pd-talk-clock');
      if (el) el.textContent = talkAboutTimerLabel(ms);
    },
    onBlob: talkAboutOnBlob,
    onError: function (msg) {
      var t = S.get().talk;
      if (!t) return;
      EOS_UI.toast(msg, 'error');
      S.set({ talk: Object.assign({}, t, { phase: 'ready' }) });
    },
  });
  return _talkRec;
}

function talkAboutStart() {
  var t = S.get().talk;
  if (!t || t.phase === 'arming' || t.phase === 'recording' || t.phase === 'scoring') return;
  // An earlier recording still closing (quit, then straight back in): the
  // recorder would refuse this start and leave us stuck at "arming". Let it
  // finish first; its blob is ignored because the phase is not 'recording'.
  if (_talkRec && _talkRec.isRecording()) {
    _talkRec.stop();
    setTimeout(talkAboutStart, 300);
    return;
  }
  // A new id per attempt: a slow result from an earlier try must not land here.
  S.set({ talk: Object.assign({}, t, { id: ++_talkSeq, phase: 'arming', result: null }) });
  talkAboutRecorder().start();
}

function talkAboutStop() {
  if (_talkRec && _talkRec.isRecording()) _talkRec.stop();
}

/* Stop listening and go back to the prompt without scoring anything. */
function talkAboutCancel() {
  var t = S.get().talk;
  if (!t) return;
  S.set({ talk: Object.assign({}, t, { id: ++_talkSeq, phase: 'ready' }) });
  if (_talkRec && _talkRec.isRecording()) _talkRec.stop();
}

async function talkAboutOnBlob(blob) {
  var t = S.get().talk;
  if (!t || t.phase !== 'recording') return;
  var id = t.id;
  S.set({ talk: Object.assign({}, t, { phase: 'scoring' }) });
  var fd = new FormData();
  fd.append('audio', blob, 'talk.webm');
  var up = await post('/dictionary/api/picture/speak/upload', fd);
  var res = (up && !up.error)
    ? await post('/dictionary/api/picture/talk', { slug: t.slug, audio_path: up.audio_path, pack: t.pack })
    : up;
  var cur = S.get().talk;
  if (!cur || cur.id !== id || cur.phase !== 'scoring') return;
  if (!res || res.error) {
    EOS_UI.toast((res && res.error) || 'Could not score that', 'error');
    S.set({ talk: Object.assign({}, cur, { phase: 'ready' }) });
    return;
  }
  S.set({ talk: Object.assign({}, cur, { phase: 'done', result: res }) });
}

/* Open Improv to practise a pack's words. The link carries only the source
 * key (plus the object just talked about, so the scene includes it); Improv
 * asks this app for the words, and lists every other source it knows
 * (including "due today") when none is named. */
function improvSceneOpen() {
  var s = S.get();
  var pack = (s.talk && s.talk.pack) || s.pack;
  var key = pack ? 'dictionary:pack:' + pack + (s.talk ? '/' + s.talk.slug : '') : '';
  if (s.talk) talkAboutQuit();
  location.href = key ? '/improv/?source=' + encodeURIComponent(key) : '/improv/';
}

function viewTalkAbout(s) {
  var t = s.talk;
  var pic = t.image ? '<img class="pd-q-photo" src="' + escAttr(t.image) + '" alt="">'
                    : '<div class="pd-q-emoji">' + esc(t.emoji || '🐾') + '</div>';
  var head = '<div class="pd-panel"><div class="pd-center"><h3 style="margin-top:0">🗣 Talk about it</h3>' +
    pic + '<h3 style="margin:10px 0 2px">' + esc(t.name) + '</h3>' +
    '<div class="pd-zh">' + zh(t.chinese) + '</div></div>';
  if (t.phase === 'done') return head + viewTalkAboutResult(t.result) + '</div>';

  var qs = '<ul class="pd-note" style="margin:12px 0 4px;padding-left:18px">' +
    TALK_ABOUT_QUESTIONS.map(function (q) { return '<li>' + esc(q) + '</li>'; }).join('') + '</ul>';
  var hints = t.hints && t.hints.length
    ? '<div class="pd-note">Try to use: ' + t.hints.map(function (h) {
        return '<span class="pd-phone">' + esc(h) + '</span>'; }).join(' ') + '</div>'
    : '';
  var ctl;
  if (t.phase === 'ready') {
    ctl = '<button class="eos-btn eos-btn-primary" onclick="talkAboutStart()">🎙 Start talking</button> ' +
          '<button class="eos-btn" onclick="talkAboutPick()">Another picture</button> ' +
          '<button class="eos-btn" onclick="talkAboutQuit()">Done</button>';
  } else if (t.phase === 'scoring') {
    ctl = '<div class="pd-muted">Listening back and scoring…</div>';
  } else {
    ctl = '<div id="pd-talk-clock" class="pd-score" style="font-size:1.2rem">' +
          esc(talkAboutTimerLabel(0)) + '</div>' +
          '<button class="eos-btn eos-btn-primary" onclick="talkAboutStop()"' +
          (t.phase === 'recording' ? '' : ' disabled') + '>■ Stop and score</button> ' +
          '<button class="eos-btn" onclick="talkAboutCancel()">Cancel</button>';
  }
  return head + '<p class="pd-muted" style="margin:12px 0 0">Talk about this for 30–60 seconds. ' +
    'Keep going even if you are not sure of a word — describe around it.</p>' + qs + hints +
    '<div class="pd-center" style="margin-top:12px">' + ctl + '</div></div>';
}

function viewTalkAboutResult(r) {
  var f = r.fluency || {}, m = f.metrics || {};
  var secs = Math.round(r.seconds || 0);
  var target = r.target_seconds || TALK_ABOUT_TARGET_S;
  var rows = [
    ['Spoke for', secs + ' s' + (secs < target ? ' — aim for ' + target + ' s' : '')],
    ['Pace', (m.wpm || 0) + ' words per minute'],
    ['Filler words', m.filler_count ? m.filler_count + ' (' + (m.filler_words || []).join(', ') + ')' : 'none'],
    ['Long pauses', r.pauses_measured
      ? String(f.long_pause_count || 0)
      : 'not measured — background noise hid the gaps'],
    ['Repeats / false starts', String(f.repetitions_false_starts || 0)],
    ['Said “' + r.name + '”', r.said_name ? 'yes' : 'no'],
    ['Words from this pack', (r.scene_words_used || []).length
      ? r.scene_words_used.join(', ') : 'none this time'],
  ];
  var table = '<table class="pc-table" style="margin:10px 0">' + rows.map(function (row) {
    return '<tr><td>' + esc(row[0]) + '</td><td>' + esc(row[1]) + '</td></tr>'; }).join('') + '</table>';
  var tips = (f.missed || []).length
    ? '<div class="pd-note">To work on: ' + f.missed.map(esc).join(' · ') + '</div>' : '';
  return '<div class="pd-center" style="margin-top:12px"><div class="pd-score">Fluency ' +
    esc(String(f.score)) + ' / 5</div></div>' + table + tips +
    '<details style="margin-top:10px"><summary>What you said</summary><p>' + esc(r.transcript) + '</p></details>' +
    '<div class="pd-center" style="margin-top:12px">' +
    '<button class="eos-btn eos-btn-primary" onclick="talkAboutStart()">Try again</button> ' +
    '<button class="eos-btn" onclick="talkAboutPick()">Another picture</button> ' +
    improvSceneButton('🎭 Use these words in a scene') +
    '<button class="eos-btn" onclick="talkAboutQuit()">Done</button></div>';
}

/* ── tabs + boot ──────────────────────────────────────────────────── */

function showTab(t) {
  if (t !== 'speak' && S.get().speed) speedRoundQuit();   // don't record off-screen
  if (t !== 'speak' && S.get().talk) talkAboutQuit();
  S.set({ tab: t, detail: t === 'browse' ? S.get().detail : null });
  // Always refetch: what is due changes as you grade, so a cached queue would
  // show cards you already cleared. One call — an earlier guarded-plus-
  // unguarded pair fired two requests and raced their renders.
  if (t === 'review') loadPictureReview();
}

// `openAppSettings` is deliberately NOT defined here. index.html already owns
// one, pointed at the dictionary settings panel, and that panel derives its
// fields from the manifest schema — which carries the picture-dict.* keys since
// the merge. Two definitions in one global scope would mean the later script
// silently wins.

var _picturesBooted = false;

/* Booted lazily by switchTab('pictures'), not on DOMContentLoaded.
 *
 * loadStatus() is what triggers the photo prefetch, so an eager boot would
 * download a 130-photo pack for someone who only ever uses the dictionary.
 * Idempotent because switchTab fires on every visit to the tab. */
function initPictures() {
  if (_picturesBooted) return;
  _picturesBooted = true;

  if (EOS.registerActions) {
    EOS.registerActions({}, [], {
      description: 'A visual English vocabulary trainer: browse animals with real ' +
                   'photographs, quiz yourself, review with spaced repetition, and ' +
                   'practise saying the names aloud.',
      quickActions: [
        { label: 'Start a picture quiz', run: function () { showTab('quiz'); } },
        { label: 'Review due pictures', run: function () { showTab('review'); } },
      ],
    });
  }
  // A detail view still deep-links, but under `#pictures/<slug>` rather than the
  // bare `#slug` the standalone page used — bare slugs would collide with the
  // dictionary's own `#practice` / `#review` / `#quiz` routes.
  _route = {
    set: function (slug) {
      try { history.replaceState(null, '', '#pictures/' + encodeURIComponent(slug)); }
      catch (e) { location.hash = 'pictures/' + encodeURIComponent(slug); }
    },
    clear: function () {
      try { history.replaceState(null, '', '#pictures'); }
      catch (e) { location.hash = 'pictures'; }
    },
  };
  loadStatus();
  // hasApp answers from a cache that may still be empty on a first visit;
  // re-render once the loaded-app list is known so the Improv buttons appear.
  if (EOS._whenAppsKnown) EOS._whenAppsKnown(function () { render(S.get()); });
  // The hash is owned by index.html (see openPictureDetail) — the standalone
  // page used EOS_UI.hashRoute on a bare `#slug`, which would now collide with
  // the dictionary's own `#practice` / `#review` routes.
  return loadCatalog().then(function () {
    if (window._pendingPictureSlug) {
      var slug = window._pendingPictureSlug;
      window._pendingPictureSlug = null;
      openDetail(slug);
    }
  });
}

/* Called by index.html's hash router for `#pictures/<slug>`. Queues the slug
 * when the catalog has not loaded yet, because a deep link arrives before the
 * first fetch resolves. */
function openPictureDetail(slug) {
  if (!slug) return;
  if (!_picturesBooted) {
    window._pendingPictureSlug = slug;
    initPictures();
    return;
  }
  openDetail(slug);
}
