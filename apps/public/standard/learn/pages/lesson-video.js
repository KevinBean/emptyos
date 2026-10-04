// learn — lesson video panel (🎬 Make video). A sibling of learn.js, loaded
// after it. Everything lives under the one LessonVideo global so no top-level
// name can collide with learn.js (.claude/rules/multi-module-apps.md §
// frontend counterpart). Backend: apps/public/standard/learn/video.py.
//
// Flow: Make video → the run writes a script and pauses → the user edits and
// clicks Render → narration, slides, animations, assembly, quality check →
// the finished video plays above the lesson body.
var LessonVideo = (function () {
  'use strict';

  // Polling cadence while a run is active. Stages report progress every few
  // seconds at most (one TTS call, one screenshot), so 2 s shows every step
  // without hammering the daemon; a failed poll backs off to 6 s.
  var POLL_MS = 2000;
  var RETRY_MS = 6000;
  var ACTIVE = { running: 1, starting: 1, queued: 1, resuming: 1 };
  // Run status → shared badge variant. The badge always carries the status
  // word too; colour is never the only signal.
  var STATUS_MAP = {
    running: 'running', starting: 'running', resuming: 'running', queued: 'shelved',
    paused: 'shelved', interrupted: 'fail', error: 'fail', stale: 'fail',
  };
  var STATUS_WORDS = {
    running: 'running', starting: 'starting', resuming: 'resuming', queued: 'queued',
    paused: 'waiting for your review', interrupted: 'interrupted', error: 'failed',
  };
  var STAGE_LABELS = {
    starting: 'Starting', queued: 'Queued behind earlier lessons', resuming: 'Resuming',
    script: 'Writing the script', narration: 'Narration', visuals: 'Slides and animations',
    assemble: 'Assembly', review: 'Quality check',
  };
  var CHOICE_LABELS = {
    render: '▶ Render video', regenerate: '↻ New script', retry: '↻ Retry', discard: 'Discard run',
  };

  var S = { slot: null, courseId: null, idx: null, timer: null, videoSig: '', lastRun: null, busy: false };

  function esc(v) { return EOS_UI.esc(v == null ? '' : String(v)); }
  function attr(v) { return EOS_UI.escAttr(v == null ? '' : String(v)); }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function lessonBase() {
    return '/learn/api/courses/' + encodeURIComponent(S.courseId) + '/lessons/' + S.idx;
  }
  function post(path, body) {
    return EOS.apiSafe(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }
  function stopPolling() {
    if (S.timer) { clearTimeout(S.timer); S.timer = null; }
  }
  function schedule(ms) {
    stopPolling();
    S.timer = setTimeout(function () { S.timer = null; refresh(); }, ms);
  }
  function fmtSeconds(s) {
    var n = Math.round(num(s));
    return Math.floor(n / 60) + ':' + ('0' + (n % 60)).slice(-2);
  }

  // What the Progress row says. A stopped run's `stage` is the last one that
  // ran — naming it as if it were still in progress misreads the state.
  function stageText(status, stage) {
    var name = STAGE_LABELS[stage] || stage || '';
    if (ACTIVE[status]) return name;
    if (status === 'paused') return 'Script ready — review it below';
    return name ? 'Stopped at: ' + name : '';
  }

  // One request at a time from this panel: a double click must not send the
  // second POST (which the server would refuse with a confusing toast).
  async function once(fn) {
    if (S.busy) return;
    S.busy = true;
    if (S.slot) S.slot.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
    try { await fn(); } finally {
      S.busy = false;
      if (S.slot) S.slot.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
    }
  }

  // ─── Lesson panel ────────────────────────────────────────────

  function mountLesson(slot, courseId, idx) {
    stopPolling();
    S.slot = slot; S.courseId = courseId; S.idx = idx; S.videoSig = ''; S.lastRun = null; S.busy = false;
    if (!slot) return;
    slot.innerHTML =
      '<section class="lv-panel" aria-label="Lesson video">' +
        '<div class="lv-head"><strong>🎬 Lesson video</strong>' +
          '<span class="lv-pill"></span><span class="lv-actions"></span></div>' +
        '<div class="lv-video"></div>' +
        '<div class="lv-run"></div>' +
      '</section>';
    EOS_UI.modelPill({ app: 'learn', mount: slot.querySelector('.lv-pill'), domain: 'text' });
    refresh();
  }

  async function refresh() {
    var slot = S.slot;
    if (!slot) return;
    var res = await EOS.apiSafe(lessonBase() + '/video');
    if (slot !== S.slot || !document.body.contains(slot)) return;  // navigated away
    if (res.enabled === false) { slot.innerHTML = ''; return; }
    if (res.error) {
      // Keep watching a run that was active: a daemon restart or a dropped
      // connection must not freeze the panel on a stale error forever.
      var wasActive = S.lastRun && ACTIVE[S.lastRun.status];
      slot.querySelector('.lv-run').innerHTML = EOS_UI.errorState({
        message: 'Could not load the lesson video: ' + res.error + (wasActive ? ' — retrying.' : ''),
        onRetry: 'LessonVideo.refresh()',
      });
      if (wasActive) schedule(RETRY_MS);
      return;
    }
    renderVideo(res);
    renderActions(res);
    renderRun(res.run);
    if (res.run && ACTIVE[res.run.status]) schedule(POLL_MS);
  }

  function renderVideo(res) {
    var box = S.slot.querySelector('.lv-video');
    // Re-render only when the video itself changed — polling a running remake
    // must not restart the one that is playing.
    var sig = res.exists ? [res.video_url, res.created, res.stale].join('|') : '';
    if (sig === S.videoSig) return;
    S.videoSig = sig;
    if (!res.exists) { box.innerHTML = ''; return; }
    var meta = [];
    if (res.duration_s) meta.push(fmtSeconds(res.duration_s));
    if (res.created) meta.push('made ' + esc(String(res.created).replace('T', ' ')));
    var stale = res.stale
      ? ' ' + EOS_UI.statusBadge('out of date', 'stale', STATUS_MAP) +
        ' <span class="lv-muted">the lesson note changed after this video was made</span>'
      : '';
    var warn = (res.warnings || []).length
      ? '<ul class="lv-warnings">' + res.warnings.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join('') + '</ul>'
      : '';
    box.innerHTML =
      '<video class="lv-player" controls preload="metadata" src="' + attr(res.video_url + '?v=' + encodeURIComponent(res.created || '')) + '"></video>' +
      '<div class="lv-meta"><span class="lv-ai-chip" title="The script and narration were written by AI from this lesson\'s note">✨ AI-written</span> ' +
        esc(res.title || '') + (meta.length ? ' · ' + meta.join(' · ') : '') + stale + '</div>' +
      warn;
  }

  function renderActions(res) {
    var box = S.slot.querySelector('.lv-actions');
    if (res.run) { box.innerHTML = ''; return; }
    box.innerHTML = '<button type="button" class="eos-btn eos-btn-sm">' +
      (res.exists ? '↻ Remake video' : '🎬 Make video') + '</button>';
    box.querySelector('button').addEventListener('click', function () { once(start); });
  }

  async function start() {
    var res = await post(lessonBase() + '/video');
    if (res.error) { EOS_UI.toast(res.error, false); return; }
    refresh();
  }

  // ─── Run: progress · settings · choices ──────────────────────

  function renderRun(run) {
    var box = S.slot.querySelector('.lv-run');
    S.lastRun = run || null;
    if (!run) { box.innerHTML = ''; return; }
    var status = run.status || '';
    var pct = Math.max(0, Math.min(100, num(run.progress)));
    var total = num(run.total);
    var settings = run.settings || {};
    // Only what the run actually persisted — never an invented fallback.
    var settingRows = [];
    if (settings.lesson_title) settingRows.push(['Lesson', settings.lesson_title]);
    if ('voice' in settings) settingRows.push(['Voice', settings.voice || 'provider default']);
    if (settings.tts_providers) settingRows.push(['Speech', settings.tts_providers.join(' → ') || 'system speech chain']);
    if (settings.resolution) settingRows.push(['Frame', settings.resolution]);
    var settingsHtml = settingRows.map(function (r) {
      return '<div><span class="lv-muted">' + esc(r[0]) + '</span> ' + esc(r[1]) + '</div>';
    }).join('') || '<span class="lv-muted">recorded when the run starts</span>';
    var choices = (run.choices || []).filter(function (c) { return CHOICE_LABELS[c]; }).map(function (c) {
      var primary = (c === 'render' || c === 'retry') ? ' eos-btn-primary' : '';
      return '<button type="button" class="eos-btn eos-btn-sm' + primary + '" data-lv-choice="' + attr(c) + '">' +
        esc(CHOICE_LABELS[c]) + '</button>';
    }).join(' ');
    var stageNo = Math.min(num(run.completed) + (ACTIVE[status] ? 1 : 0), total);

    var html =
      '<table class="lv-table"><tbody>' +
        '<tr><th scope="row">Progress</th><td>' +
          EOS_UI.statusBadge(STATUS_WORDS[status] || status, status, STATUS_MAP) +
          ' <span>' + esc(stageText(status, run.stage)) + '</span>' +
          ' <span class="lv-muted">· stage ' + stageNo + ' of ' + total + ' · ' + pct + '%</span>' +
          (run.detail ? '<div class="lv-muted">' + esc(run.detail) + '</div>' : '') +
          '<div class="eos-bar lv-bar" role="progressbar" aria-label="Video progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '">' +
            '<div class="eos-bar-fill" style="width:' + pct + '%"></div></div>' +
        '</td></tr>' +
        '<tr><th scope="row">Settings</th><td class="lv-settings">' + settingsHtml + '</td></tr>' +
        '<tr><th scope="row">Choices</th><td>' + (choices || '<span class="lv-muted">none while it runs</span>') + '</td></tr>' +
      '</tbody></table>';

    if (run.error) html += EOS_UI.errorState({ message: run.error });
    if ((run.warnings || []).length) {
      html += '<ul class="lv-warnings">' + run.warnings.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join('') + '</ul>';
    }
    if (run.script && (run.choices || []).indexOf('render') !== -1) html += scriptEditorHtml(run);
    box.innerHTML = html;

    box.querySelectorAll('[data-lv-choice]').forEach(function (btn) {
      btn.addEventListener('click', function () { choose(btn.getAttribute('data-lv-choice'), run); });
    });
    box.querySelectorAll('.lv-kind').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var wrap = sel.closest('.lv-scene').querySelector('.lv-brief-wrap');
        wrap.hidden = sel.value !== 'anim';
        if (!wrap.hidden) wrap.querySelector('.lv-brief').focus();
      });
    });
  }

  function scriptEditorHtml(run) {
    var script = run.script;
    var scenes = script.scenes || [];
    var maxAnim = run.limits && run.limits.max_anim_scenes;
    var rows = scenes.map(function (s, i) {
      var id = 'lv-scene-' + i;
      return '<fieldset class="lv-scene">' +
        '<legend>Scene ' + (i + 1) + '</legend>' +
        '<div class="lv-scene-top">' +
          '<label for="' + id + '-k">Kind</label>' +
          '<select id="' + id + '-k" class="lv-kind">' +
            '<option value="slide"' + (s.kind === 'slide' ? ' selected' : '') + '>slide</option>' +
            '<option value="anim"' + (s.kind === 'anim' ? ' selected' : '') + '>animation</option>' +
          '</select>' +
          '<label for="' + id + '-h" class="lv-grow-label">Heading</label>' +
          '<input id="' + id + '-h" class="lv-heading lv-grow" value="' + attr(s.heading) + '">' +
        '</div>' +
        '<label for="' + id + '-n">Narration (spoken)</label>' +
        '<textarea id="' + id + '-n" class="lv-narration" rows="3">' + esc(s.narration) + '</textarea>' +
        '<label for="' + id + '-b">Bullets (one per line)</label>' +
        '<textarea id="' + id + '-b" class="lv-bullets" rows="2">' + esc((s.bullets || []).join('\n')) + '</textarea>' +
        '<div class="lv-brief-wrap"' + (s.kind === 'anim' ? '' : ' hidden') + '>' +
          '<label for="' + id + '-a">What the animation shows (required — without it the scene stays a slide)</label>' +
          '<textarea id="' + id + '-a" class="lv-brief" rows="2">' + esc(s.anim_brief) + '</textarea>' +
        '</div>' +
      '</fieldset>';
    }).join('');
    return '<div class="lv-script">' +
      '<div class="lv-script-head"><h4>' + esc(script.title || 'Script') + ' · ' + scenes.length + ' scenes</h4>' +
        '<span class="lv-ai-chip">✨ draft</span></div>' +
      '<p class="lv-muted">Written by AI from the lesson note. Nothing is narrated or rendered until you ' +
      'choose Render video — edit anything first. A scene with no narration is dropped' +
      (maxAnim ? '; at most ' + num(maxAnim) + ' scenes can be animations, and later ones become slides.' : '.') +
      '</p>' + rows +
      '</div>';
  }

  function collectScript(run) {
    var scenes = [];
    S.slot.querySelectorAll('.lv-scene').forEach(function (row) {
      scenes.push({
        kind: row.querySelector('.lv-kind').value,
        heading: row.querySelector('.lv-heading').value,
        narration: row.querySelector('.lv-narration').value,
        bullets: row.querySelector('.lv-bullets').value.split('\n')
          .map(function (b) { return b.trim(); }).filter(Boolean),
        anim_brief: row.querySelector('.lv-brief').value,
      });
    });
    return { title: (run.script && run.script.title) || '', scenes: scenes };
  }

  function choose(choice, run) {
    var runBase = '/learn/api/video-runs/' + encodeURIComponent(run.run_id);
    if (choice === 'render' || choice === 'retry') {
      once(async function () {
        var res = await post(runBase + '/resume', choice === 'render' ? { script: collectScript(run) } : {});
        if (res.error) { EOS_UI.toast(res.error, false); return; }
        refresh();
      });
      return;
    }
    if (choice !== 'discard' && choice !== 'regenerate') return;
    var remake = choice === 'regenerate';
    EOS_UI.confirm({
      title: remake ? 'Write a new script?' : 'Discard this run?',
      message: remake
        ? 'This run and its script are deleted, and a new script is written from the lesson note.'
        : 'This run and everything it produced are deleted. A finished lesson video is kept.',
      action: remake ? 'New script' : 'Discard', danger: !remake,
      onYes: function () {
        once(async function () {
          var r = await post(runBase + '/discard');
          if (r.error) { EOS_UI.toast(r.error, false); return; }
          if (remake) { await start(); } else { refresh(); }
        });
      },
    });
  }

  // ─── Course: queue every lesson ──────────────────────────────

  function mountCourse(slot, courseId) {
    if (!slot) return;
    slot.innerHTML = '<button type="button" class="eos-btn">🎬 Queue lesson videos</button>';
    var btn = slot.querySelector('button');
    btn.addEventListener('click', function () {
      EOS_UI.confirm({
        title: 'Queue lesson videos?',
        message: 'A script is written for every reading lesson that has no video yet, one lesson at a time. ' +
          'Each lesson\'s note text is sent to the model shown in the model pill. ' +
          'Nothing is narrated or rendered until you review each script from its lesson.',
        action: 'Queue scripts',
        onYes: async function () {
          if (btn.disabled) return;
          btn.disabled = true;
          try {
            var res = await post('/learn/api/courses/' + encodeURIComponent(courseId) + '/videos');
            if (res.error) { EOS_UI.toast(res.error, false); return; }
            EOS_UI.toast(res.queued
              ? 'Writing scripts for ' + res.queued + ' lesson' + (res.queued === 1 ? '' : 's') + ' — open each lesson to review'
              : 'Every reading lesson already has a video or a run in progress');
          } finally { btn.disabled = false; }
        },
      });
    });
  }

  return {
    mountLesson: mountLesson,
    mountCourse: mountCourse,
    refresh: refresh,
    // Pure pieces, exposed for tests/js/learn_lesson_video.test.mjs.
    _stageText: stageText,
    _STATUS_MAP: STATUS_MAP,
    _STAGE_LABELS: STAGE_LABELS,
  };
})();
