/* Sound Check — the play loop.
 *
 * Extracted from index.html so the page stays a scaffold and this file stays
 * the behaviour. Loads at the same position the inline block occupied, so load
 * order and global scope are unchanged.
 *
 * The shape mirrors the backend split: RENDERERS is a map keyed by shape name,
 * and nothing outside it knows what a shape is. Adding a seventh round form is
 * one entry here and one row in shapes.py — no change to the loop.
 */

var STATE = {
  view: 'home',
  sid: null,
  round: null,
  plan: null,
  answered: false,
  startedAt: 0,
  replays: 0,
  deadline: null,
  audio: {},          // url -> preloaded Audio, capped
  audioOrder: [],
};

var AUDIO_CACHE_MAX = 8;

/* ── view swap ─────────────────────────────────────────────── */

function showView(name) {
  STATE.view = name;
  ['home', 'play', 'summary', 'history', 'review'].forEach(function (v) {
    var el = document.getElementById('view-' + v);
    if (el) el.hidden = (v !== name);
  });
  // A tab bar during a timed round is an invitation to lose the round.
  document.getElementById('sc-tabs').hidden = (name === 'play' || name === 'summary');
  // aria-selected is stamped once at boot by EOS_UI._initTabA11y; it goes stale
  // on every switch unless we move it with the class.
  document.querySelectorAll('#sc-tabs .eos-tab').forEach(function (b) {
    var on = b.dataset.view === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  if (name === 'history') loadHistory();
  if (name === 'review') loadReview();
}

/* ── home ──────────────────────────────────────────────────── */

async function loadHome() {
  var bank = await EOS.apiSafe('/soundcheck/api/bank');
  var note = document.getElementById('sc-bank-note');
  if (!bank || bank.error) {
    note.innerHTML = EOS_UI.errorState({
      message: 'Could not read the sound bank — ' +
        ((bank && bank.error) || 'the app may not have finished loading.'),
      onRetry: 'loadHome()',
    });
    return;
  }
  if (!bank.count) {
    note.innerHTML = EOS_UI.emptyState({
      icon: '👂',
      message: 'The sound bank is empty. ' + (bank.pending_review
        ? bank.pending_review + ' items are generated and waiting for review — ' +
          'approve some in bank/curation.json, then rebuild.'
        : 'Run scripts/build_soundcheck_bank.py to generate it.'),
    });
    document.getElementById('sc-go').disabled = true;
    return;
  }
  note.innerHTML = '';

  var d = await EOS.apiSafe('/soundcheck/api/dimensions');
  var dims = (d && d.dimensions) || [];
  document.getElementById('sc-dimensions').innerHTML = dims.map(function (x) {
    var acc = x.accuracy === null || x.accuracy === undefined
      ? '<span class="sc-dim-none">not tested</span>'
      : Math.round(x.accuracy * 100) + '% right';
    var playable = x.items > 0;
    var tip = playable ? 'Drill this sound family — ' + x.items + ' items'
                       : 'Nothing in the bank for this one yet';
    // escAttr AFTER JSON.stringify, not instead of it: stringify emits real
    // double quotes, which close the onclick="" attribute early and leave the
    // handler unbound — a card that silently does nothing when clicked.
    return '<button class="sc-dim' + (playable ? '' : ' sc-dim-empty') + '" title="' + escAttr(tip) + '"' +
      (playable
        ? ' onclick="startSet(\'quick\', ' + escAttr(JSON.stringify(x.id)) + ')"'
        : ' disabled') +
      '><span class="sc-dim-icon">' + esc(x.icon) + '</span>' +
      '<span class="sc-dim-label">' + esc(x.label) + '</span>' +
      '<span class="sc-dim-blurb">' + esc(x.blurb) + '</span>' +
      '<span class="sc-dim-stat">' + (playable ? x.items + ' items · ' + acc : 'none yet') +
      '</span></button>';
  }).join('');
}

/* ── starting + the loop ───────────────────────────────────── */

async function startSet(mode, dimension) {
  var body = { mode: mode || 'quick' };
  if (dimension) body.dimensions = [dimension];
  // The diagnostic asks for the eye-then-ear pair. The backend ignores this
  // unless the feature is switched on, and falls back to a single pass.
  if (mode === 'diagnostic') body.dual = true;

  var res = await EOS.apiSafe('/soundcheck/api/session/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res || !res.ok) {
    EOS_UI.toast((res && res.error) || 'Could not start a set', false);
    return;
  }
  if (!res.round) {
    EOS_UI.toast('No items match that filter yet', false);
    return;
  }
  STATE.sid = res.sid;
  STATE.plan = res.plan;
  if (res.note) EOS_UI.toast(res.note, true, { timeout: 6000 });
  showView('play');
  renderRound(res.round, res.progress);
}

function renderRound(round, progress) {
  STATE.round = round;
  STATE.answered = false;
  STATE.replays = 0;
  clearDeadline();

  document.getElementById('sc-verdict').hidden = true;
  document.getElementById('sc-prompt').textContent = round.prompt || '';
  renderHud(progress);

  var mount = document.getElementById('sc-round');
  var render = RENDERERS[round.shape];
  if (!render) {
    mount.innerHTML = EOS_UI.errorState({
      message: 'No renderer for round type "' + round.shape + '". ' +
        'The backend served a shape this page does not know how to draw.',
    });
    return;
  }
  mount.innerHTML = render(round);
  mount.focus();

  primeAudio(round);
  // On a listening round the clock starts when the clip FINISHES, not when the
  // round appears — otherwise a long word costs the learner thinking time.
  if (round.stimulus && round.stimulus.mode === 'audio') {
    playStimulus(function () { armDeadline(round.deadline_ms); });
  } else {
    armDeadline(round.deadline_ms);
  }
}

function renderHud(p) {
  if (!p) return;
  var lives = STATE.plan && STATE.plan.lives;
  document.getElementById('sc-lives').innerHTML = lives
    ? '♥'.repeat(Math.max(0, p.lives_left)) +
      '<span class="sc-lost">' + '♡'.repeat(Math.max(0, lives - p.lives_left)) + '</span>'
    : '';
  document.getElementById('sc-streak').textContent =
    p.streak > 1 ? '🔥 ' + p.streak : '';
  document.getElementById('sc-count').textContent = p.asked + ' / ' + p.length;
}

/* ── the renderers — one per shape, nothing else knows shapes ─ */

function optionButtons(round, cls) {
  return (round.options || []).map(function (o, i) {
    var special = o.special ? ' sc-opt-special' : '';
    var key = o.special ? '0' : (i + 1);
    // A real button element, not a div+role: the number keys are the fast path,
    // but a Tab-navigating or screen-reader user has to reach an answer too.
    return '<button type="button" class="sc-opt ' + (cls || '') + special +
      '" data-choice="' + escAttr(o.id) + '" title="Answer ' + key +
      ' — or press ' + key + '">' +
      '<span class="sc-key">' + key + '</span>' +
      '<span class="sc-opt-label" data-i18n="skip">' + esc(o.label || o.id) +
      '</span></button>';
  }).join('');
}

function playButton(audio, label) {
  if (!audio) return '<span class="sc-noaudio">no clip</span>';
  return '<button class="sc-play" data-audio="' + escAttr(audio) +
    '" title="Play the clip — or press space to hear it again">▶ ' +
    esc(label || 'Play') + '</button>';
}

var RENDERERS = {
  minimal_pair: function (r) {
    var s = r.stimulus.items[0] || {};
    return '<div class="sc-stim">' + playButton(s.audio, 'Play') + '</div>' +
      '<div class="sc-opts sc-opts-2">' + optionButtons(r, 'sc-opt-wide') + '</div>';
  },

  sort: function (r) {
    var s = r.stimulus.items[0] || {};
    var stim = r.stimulus.mode === 'text'
      ? '<div class="sc-word" data-i18n="skip">' + esc(s.text || '') + '</div>'
      : '<div class="sc-stim">' + playButton(s.audio) + '</div>';
    return stim + '<div class="sc-opts">' + optionButtons(r, 'sc-opt-wide') + '</div>';
  },

  odd_one_out: function (r) {
    var tiles = (r.stimulus.items || []).map(function (it, i) {
      var inner = r.stimulus.mode === 'text'
        ? esc(it.text || '')
        : playButton(it.audio, String(i + 1));
      return '<div class="sc-tile" data-i18n="skip">' + inner + '</div>';
    }).join('');
    var opts = (r.options || []).filter(function (o) { return !o.special; });
    var same = (r.options || []).filter(function (o) { return o.special; });
    return '<div class="sc-tiles">' + tiles + '</div>' +
      '<div class="sc-opts sc-opts-row">' +
        optionButtons({ options: opts }, 'sc-opt-num') + '</div>' +
      (same.length ? '<hr class="sc-rule">' +
        '<div class="sc-opts">' + optionButtons({ options: same }, 'sc-opt-wide') +
        '</div>' : '');
  },

  stress: function (r) {
    var s = r.stimulus.items[0] || {};
    var audio = s.audio ? '<div class="sc-stim">' + playButton(s.audio) + '</div>' : '';
    return '<div class="sc-word" data-i18n="skip">' + esc(s.text || '') +
      '</div>' + audio +
      '<div class="sc-opts sc-opts-row">' + optionButtons(r, 'sc-opt-num') + '</div>';
  },

  count: function (r) {
    return RENDERERS.sort(r);
  },

  presence: function (r) {
    return RENDERERS.sort(r);
  },

  produce: function (r) {
    var s = r.stimulus.items[0] || {};
    return '<div class="sc-word" data-i18n="skip">' + esc(s.text || '') + '</div>' +
      '<div class="sc-stim">' + playButton(s.audio, 'Hear it') + '</div>' +
      '<div id="sc-rec"></div>';
  },
};

/* ── audio ─────────────────────────────────────────────────── */

function primeAudio(round) {
  var urls = [];
  ((round.stimulus && round.stimulus.items) || []).forEach(function (i) {
    if (i.audio) urls.push(i.audio);
  });
  urls.forEach(cacheAudio);
}

function cacheAudio(url) {
  if (!url || STATE.audio[url]) return STATE.audio[url];
  var a = new Audio(url);
  a.preload = 'auto';
  try { a.load(); } catch (e) { /* a failed preload is not worth a message */ }
  STATE.audio[url] = a;
  STATE.audioOrder.push(url);
  while (STATE.audioOrder.length > AUDIO_CACHE_MAX) {
    delete STATE.audio[STATE.audioOrder.shift()];
  }
  return a;
}

function playUrl(url, onEnd) {
  if (!url) { if (onEnd) onEnd(); return; }
  var a = cacheAudio(url);
  try {
    a.currentTime = 0;
    if (onEnd) a.onended = onEnd;
    var p = a.play();
    if (p && p.catch) p.catch(function () { if (onEnd) onEnd(); });
  } catch (e) { if (onEnd) onEnd(); }
}

function playStimulus(onEnd) {
  var items = (STATE.round && STATE.round.stimulus && STATE.round.stimulus.items) || [];
  var first = items.find(function (i) { return i.audio; });
  playUrl(first && first.audio, onEnd);
}

/* ── the clock ─────────────────────────────────────────────── */

function armDeadline(ms) {
  clearDeadline();
  var bar = document.getElementById('sc-clock-bar');
  if (!ms) { bar.style.transform = 'scaleX(0)'; return; }
  bar.style.transition = 'none';
  bar.style.transform = 'scaleX(1)';
  // Commit the full bar before the transition starts: without this read the
  // browser coalesces scaleX(1) and scaleX(0) into one style pass and the bar
  // never moves (measured: the width version animated, the transform one sat
  // at 0 until this flush was added).
  void getComputedStyle(bar).transform;
  // Let the browser animate the bar (a transform, per §5 — never width);
  // one timer fires the real expiry.
  requestAnimationFrame(function () {
    bar.style.transition = 'transform ' + ms + 'ms linear';
    bar.style.transform = 'scaleX(0)';
  });
  STATE.deadline = setTimeout(function () { submit([], true); }, ms);
}

function clearDeadline() {
  if (STATE.deadline) clearTimeout(STATE.deadline);
  STATE.deadline = null;
  var bar = document.getElementById('sc-clock-bar');
  if (bar) { bar.style.transition = 'none'; bar.style.transform = 'scaleX(0)'; }
  STATE.startedAt = Date.now();
}

/* ── answering ─────────────────────────────────────────────── */

async function submit(choice, timedOut) {
  if (STATE.answered || !STATE.sid) return;
  STATE.answered = true;
  clearDeadline();

  var res = await EOS.apiSafe(
    '/soundcheck/api/session/' + encodeURIComponent(STATE.sid) + '/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        rid: STATE.round.rid,
        choice: choice,
        elapsed_ms: Math.max(0, Date.now() - STATE.startedAt),
        replays: STATE.replays,
        timeout: !!timedOut,
      }),
    });
  if (!res || !res.ok) {
    EOS_UI.toast((res && res.error) || 'Could not record that answer', false);
    STATE.answered = false;
    return;
  }
  showVerdict(res);
}

function showVerdict(res) {
  markChoices(res);
  var box = document.getElementById('sc-verdict');
  var accent = res.accent
    ? '<p class="sc-accent">This one is an accent difference, not a mistake.</p>' : '';
  box.className = 'sc-verdict ' + (res.correct ? 'ok' : 'no');
  box.innerHTML =
    '<strong>' + (res.correct ? 'Yes' : (res.timeout ? 'Out of time' : 'Not quite')) +
    '</strong>' +
    (res.explain ? '<p>' + esc(res.explain) + '</p>' : '') + accent +
    '<button class="eos-btn eos-btn-primary sc-next" title="' +
      (res.done ? 'See how the whole set went' : 'Go to the next round') +
      ' — or press Enter">' +
      (res.done ? 'See the summary' : 'Next') + ' <kbd>⏎</kbd></button>';
  box.hidden = false;
  box.querySelector('.sc-next').onclick = function () { advance(res); };

  if (res.celebrate) celebrate();
  // Warm the next round's clips while the verdict is being read.
  if (res.next) primeAudio(res.next);
  renderHud(res.progress);
}

function markChoices(res) {
  var answer = res.answer || [];
  document.querySelectorAll('#sc-round [data-choice]').forEach(function (el) {
    var id = el.dataset.choice;
    if (answer.indexOf(id) >= 0) el.classList.add('sc-right');
    else if ((res.picked || []).indexOf(id) >= 0) el.classList.add('sc-wrong');
    el.classList.add('sc-locked');
  });
}

function advance(res) {
  if (res.next) {
    renderRound(res.next, res.progress);
  } else {
    renderSummary(res.summary);
  }
}

function celebrate() {
  var host = document.getElementById('sc-round');
  if (!host) return;
  for (var i = 0; i < 14; i++) {
    var bit = document.createElement('span');
    bit.className = 'sc-confetti';
    bit.style.left = (10 + Math.random() * 80) + '%';
    bit.style.animationDelay = (Math.random() * 0.25) + 's';
    host.appendChild(bit);
    setTimeout(function (b) { return function () { b.remove(); }; }(bit), 1400);
  }
}

/* ── summary ───────────────────────────────────────────────── */

function renderSummary(s) {
  showView('summary');
  if (!s) { document.getElementById('sc-summary').innerHTML = ''; return; }

  var m = s.matrix || {};
  document.getElementById('sc-summary').innerHTML =
    '<h2>' + s.right + ' of ' + s.asked + '</h2>' +
    (s.headline ? '<p class="sc-headline" data-i18n="skip">' + esc(s.headline) +
      '</p>' : '') +
    (s.cleared && s.cleared.length
      ? '<p class="sc-cleared">Cleared: ' + esc(s.cleared.join(', ')) + '</p>' : '') +
    matrixHtml(m) +
    (s.eye_ear && s.eye_ear.compared
      ? '<p class="sc-eyeear">' + esc(s.eye_ear.headline) + '</p>' : '') +
    secondPassOffer(s) +
    '<div class="sc-start">' +
      '<button class="eos-btn eos-btn-primary" onclick="startSet(\'quick\')"' +
        ' title="A fresh short set">Another set</button>' +
      '<button class="eos-btn" onclick="showView(\'home\'); loadHome();"' +
        ' title="Back to the sound list">Home</button>' +
    '</div>';
}

/* The backend sets `dual_ready` on a completed first pass with a frozen item
   order: the same questions can be run again by ear. Without this offer the
   whole second-pass path is unreachable — one pass alone says how you did, but
   never where eye and ear disagree, which is the point of the diagnostic. */
function secondPassOffer(s) {
  if (!s.dual_ready || !s.sid) return '';
  return '<div class="sc-second">' +
    '<p>That was the reading pass. The same questions by ear is what shows ' +
    'which sounds you know on paper but cannot hear.</p>' +
    '<button class="eos-btn eos-btn-primary" onclick="secondPass(' +
      escAttr(JSON.stringify(s.sid)) + ')" title="Run the same questions again, ' +
      'listening this time">Now do it by ear</button></div>';
}

function matrixHtml(m) {
  var labels = m.labels || [];
  if (!labels.length) return '';
  var head = '<tr><th></th>' + labels.map(function (l) {
    return '<th>' + esc(l) + '</th>';
  }).join('') + '</tr>';
  var rows = labels.map(function (want) {
    return '<tr><th>' + esc(want) + '</th>' + labels.map(function (got) {
      var n = (m.cells || {})[want + '|' + got] || 0;
      var cls = want === got ? 'sc-diag' : (n ? 'sc-miss' : '');
      return '<td class="' + cls + '">' + (n || '') + '</td>';
    }).join('') + '</tr>';
  }).join('');
  return '<p class="sc-matrix-cap">Rows are the sound that was there; ' +
    'columns are the one you picked.</p>' +
    '<div class="sc-matrix-wrap"><table class="sc-matrix" data-i18n="skip">' +
    head + rows + '</table></div>';
}

/* ── history + review ──────────────────────────────────────── */

async function secondPass(sid) {
  var res = await EOS.apiSafe('/soundcheck/api/session/' + encodeURIComponent(sid) +
                              '/second-pass', { method: 'POST' });
  if (!res || !res.ok) {
    EOS_UI.toast((res && res.error) || 'Could not start the second pass', false);
    return;
  }
  STATE.sid = res.sid;
  STATE.plan = res.plan;
  showView('play');
  renderRound(res.round, res.progress);
}


async function loadHistory() {
  var h = await EOS.apiSafe('/soundcheck/api/history');
  var host = document.getElementById('sc-sessions');
  if (!h || h.error) {
    host.innerHTML = EOS_UI.errorState({ message: 'Could not load history.',
                                        onRetry: 'loadHistory()' });
    return;
  }
  if (!h.sessions.length) {
    host.innerHTML = EOS_UI.emptyState({
      icon: '👂', message: 'No sets yet — play one and it will show up here.',
    });
    return;
  }
  if (EOS_UI.yearHeatmap) {
    EOS_UI.yearHeatmap({ mount: '#sc-heatmap', data: h.heatmap || {}, months: 6 });
  }
  host.innerHTML = h.sessions.map(function (s) {
    var state = s.abandoned ? 'abandoned' : (s.completed ? 'done' : 'unfinished');
    return '<div class="sc-row"><span>' + esc((s.started || '').slice(0, 16).replace('T', ' ')) +
      '</span><span>' + esc(s.mode) + '</span><span>' + s.right + '/' + s.asked +
      '</span><span class="sc-state">' + state + '</span></div>';
  }).join('');
}

async function loadReview() {
  var r = await EOS.apiSafe('/soundcheck/api/review');
  var host = document.getElementById('sc-review');
  if (!r || r.error) {
    host.innerHTML = EOS_UI.errorState({ message: 'Could not read your slips.',
                                        onRetry: 'loadReview()' });
    return;
  }
  if (r.telemetry_available === false) {
    host.innerHTML = EOS_UI.errorState({
      message: 'Could not read your pronunciation history — ' +
        (r.telemetry_error || 'the dictionary app did not answer.') +
        ' This page is empty because the read failed, not because you have no slips.',
      onRetry: 'loadReview()',
    });
    return;
  }
  var total = (r.occurrences || {});
  if (!total.error && !total['accent-split'] && !total.artifact) {
    host.innerHTML = EOS_UI.emptyState({
      icon: '📋',
      message: 'No pronunciation history yet. Once shadowing or voice review has ' +
               'scored you, this page separates real slips from accent differences.',
    });
    return;
  }
  host.innerHTML =
    (r.headline ? '<p class="sc-headline" data-i18n="skip">' + esc(r.headline) +
      '</p>' : '') +
    ['artifact', 'accent-split', 'error'].map(function (kind) {
      var rows = (r.rows || {})[kind] || [];
      if (!rows.length) return '';
      return '<h3>' + esc(LABELS[kind]) + ' <span class="sc-dim-none">' +
        rows.length + '</span></h3>' + '<p class="sc-kindnote">' +
        esc(KIND_NOTE[kind]) + '</p>' +
        rows.slice(0, 12).map(function (row) {
          return '<div class="sc-row"><span data-i18n="skip">' + esc(row.pair || '') +
            '</span><span>' + esc(row.op || '') + '</span><span>×' +
            (row.count || 0) + '</span><span class="sc-state">' +
            esc(row._split_id || '') + '</span></div>';
        }).join('');
    }).join('');
}

var LABELS = {
  artifact: 'Not from your mouth',
  'accent-split': 'Australian English being correct',
  error: 'Real slips',
};
var KIND_NOTE = {
  artifact: 'The scoring pipeline produced these — a symbol it could not map. ' +
            'They are not evidence about your pronunciation.',
  'accent-split': 'Correct in one accent and absent in the other. Worth knowing ' +
                  'if you are aiming at an American accent; not a mistake here.',
  error: 'Genuine confusions in both accents. These are what the drill selects from.',
};

/* ── keyboard ──────────────────────────────────────────────── */

document.getElementById('sc-round').addEventListener('keydown', function (e) {
  if (STATE.view !== 'play') return;
  var key = e.key;

  if (key === 'Enter') {
    var next = document.querySelector('#sc-verdict .sc-next');
    if (next) { e.preventDefault(); e.stopPropagation(); next.click(); }
    return;
  }
  if (key === ' ') {
    // Space is "hear it again" — but the options are real buttons now, so when
    // one of them is focused Space belongs to it. Swallowing it there would
    // make the keyboard path replay audio instead of answering.
    if (e.target.closest && e.target.closest('[data-choice],[data-audio]')) return;
    e.preventDefault(); e.stopPropagation();
    STATE.replays++;
    playStimulus();
    return;
  }
  if (STATE.answered) return;

  if (key >= '0' && key <= '9') {
    var opts = Array.from(document.querySelectorAll('#sc-round [data-choice]'));
    var target = key === '0'
      ? opts.find(function (o) { return o.classList.contains('sc-opt-special'); })
      : opts.filter(function (o) { return !o.classList.contains('sc-opt-special'); })
            [parseInt(key, 10) - 1];
    if (target) {
      e.preventDefault(); e.stopPropagation();
      submit([target.dataset.choice]);
    }
  }
  // Escape is deliberately NOT intercepted — it bubbles so the global
  // overlay-close still works. Quitting a set is a button.
});

document.getElementById('sc-round').addEventListener('click', function (e) {
  var play = e.target.closest('[data-audio]');
  if (play) {
    STATE.replays++;
    playUrl(play.dataset.audio);
    return;
  }
  var opt = e.target.closest('[data-choice]');
  if (opt && !STATE.answered) submit([opt.dataset.choice]);
});

async function quitSet() {
  if (!STATE.sid) { showView('home'); return; }
  var ok = await EOS_UI.confirm({
    title: 'Quit this set?',
    body: 'An unfinished set does not count toward your streak.',
  });
  if (!ok) return;
  clearDeadline();
  await EOS.apiSafe('/soundcheck/api/session/' + encodeURIComponent(STATE.sid) +
                    '/abandon', { method: 'POST' });
  STATE.sid = null;
  showView('home');
  loadHome();
}

/* ── boot ──────────────────────────────────────────────────── */

var _settings;
function openSettings() {
  if (!_settings) {
    _settings = EOS_UI.settingsPanel({
      id: 'soundcheck-settings', title: 'Sound Check', app: 'soundcheck',
    });
  }
  _settings.open();
}

/* No model pill: it names the `think` provider about to spend the user's budget,
   and this app never calls think() — it spends `speak`. A chip here would
   advertise a cost centre that is never charged. See rules/model-pill.md. */
(function boot() {
  if (window.EOS && EOS.keys) {
    EOS.keys.register('p', 'Sound Check — start a set', function () {
      if (STATE.view !== 'play') startSet('quick');
    });
  }
  loadHome();
})();
