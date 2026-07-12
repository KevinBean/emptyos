/* Reader — interactive-fiction player (story mode).
 *
 * Loads AFTER the inline reader script, so it reuses its globals:
 *   esc / escAttr / escWithWords, EOS_UI, and the #main / #rail / #back-btn DOM.
 *
 * Self-contained: owns its own state (STORY_MODE), CSS, library section, and a
 * full-#main player. Dark-flagged — when [apps.reader] feature.story-mode.enabled
 * is off, /api/story-mode reports disabled and every entry point no-ops, so the
 * reader library and pages are unchanged.
 *
 * Walk is server-driven: each choice POSTs to /api/story/{slug}/choose and the
 * server returns the next View (decision_graph DecisionRun). No story logic here.
 */
(function () {
  var STORY_MODE = { enabled: null, default_lang: 'English', default_cefr: 'C1', current: null, view: null, scenes: {} };
  window.STORY_MODE = STORY_MODE;

  // ── one-time CSS ───────────────────────────────────────────────
  (function injectCss() {
    var css = [
      '.story-section { margin-top: 28px; }',
      '.story-section h2 { display:flex; align-items:center; gap:10px; }',
      '.story-badge { font-size:11px; padding:2px 8px; border-radius:999px; background:var(--eos-chip,rgba(255,255,255,0.08)); opacity:0.85; }',
      '.story-card-meta { display:flex; gap:6px; flex-wrap:wrap; margin-top:4px; }',
      '.story-pane { max-width: 760px; margin: 0 auto; display:flex; flex-direction:column; gap:16px; }',
      '.story-crumbs { font-size:12px; opacity:0.55; }',
      '.story-beat-title { margin:0; }',
      '.story-beat { font-size: var(--reader-fs,19px); line-height: var(--reader-lh,1.75); padding:18px 20px; border-radius:10px; background:var(--eos-surface,rgba(255,255,255,0.02)); border:1px solid var(--eos-border,rgba(255,255,255,0.08)); }',
      '.story-choices { display:flex; flex-direction:column; gap:10px; }',
      '.story-choice { text-align:left; padding:14px 16px; border-radius:8px; border:1px solid var(--eos-border,rgba(255,255,255,0.12)); background:var(--eos-bg-elev,#26262b); color:var(--eos-text,#f2f2f2); cursor:pointer; font-size:15px; line-height:1.4; transition:background 0.08s ease,border-color 0.08s ease; }',
      '.story-choice:hover { background:var(--eos-accent,#5aa9ff); color:var(--accent-ink,#fff); border-color:var(--eos-accent,#5aa9ff); }',
      '.story-choice .num { opacity:0.5; margin-right:8px; }',
      '.story-toolbar { display:flex; gap:8px; flex-wrap:wrap; }',
      '.story-scene-img { width:100%; border-radius:10px; display:block; }',
      '.story-end { text-align:center; padding:24px; }',
      '.story-end .big { font-size:24px; font-weight:700; margin-bottom:12px; }',
    ].join('\n');
    var el = document.createElement('style');
    el.textContent = css;
    document.head.appendChild(el);
  })();

  function api(url, opts) {
    return fetch(url, opts).then(function (r) { return r.json(); }).catch(function (e) { return { error: String(e) }; });
  }

  async function initStoryMode() {
    var res = await api('/reader/api/story-mode');
    STORY_MODE.enabled = !!(res && res.enabled);
    if (res && res.default_lang) STORY_MODE.default_lang = res.default_lang;
    if (res && res.default_cefr) STORY_MODE.default_cefr = res.default_cefr;
    return STORY_MODE.enabled;
  }

  // ── library section (appended under the book grid by renderLibrary) ──
  // renderLibrary fires twice at boot (loadLibrary + _route.init→closeBook), so
  // guard with a render-sequence token (last call wins) + remove any stale section.
  var _storyRenderSeq = 0;
  async function renderStoryLibrary() {
    if (STORY_MODE.enabled === null) await initStoryMode();
    if (!STORY_MODE.enabled || STORY_MODE.current) return;
    var seq = ++_storyRenderSeq;
    var res = await api('/reader/api/stories');
    if (seq !== _storyRenderSeq) return; // a newer render superseded this one
    var main = document.getElementById('main');
    if (!main) return;
    var prev = document.getElementById('story-section');
    if (prev) prev.remove();
    var stories = (res && res.stories) || [];
    var cards = stories.map(function (s) {
      var meta = '<div class="story-card-meta">' +
        '<span class="story-badge">Interactive</span>' +
        (s.cefr ? '<span class="story-badge">' + esc(s.cefr) + '</span>' : '') +
        (s.lang ? '<span class="story-badge">' + esc(s.lang) + '</span>' : '') +
        (s.in_progress ? '<span class="story-badge">▶ in progress</span>' : '') +
        '</div>';
      return EOS_UI.entityCard({
        title: s.title,
        meta: s.premise || 'Interactive story',
        onClick: "STORY_MODE_open(" + escAttr(JSON.stringify(s.slug)) + ")",
        extraHtml: meta,
      });
    }).join('');
    var section = document.createElement('div');
    section.className = 'story-section';
    section.id = 'story-section';
    section.innerHTML =
      '<h2>📖 Interactive stories ' +
        '<button class="eos-btn-sm" style="font-size:13px;padding:6px 12px;" onclick="STORY_MODE_weave()">✨ Weave a story</button>' +
      '</h2>' +
      (cards
        ? '<div class="book-list">' + cards + '</div>'
        : '<div class="empty-hint" style="padding:16px;">No stories yet — press <strong>Weave a story</strong> to generate a graded, branching tale.</div>');
    main.appendChild(section);
  }
  window.renderStoryLibrary = renderStoryLibrary;

  // EOS_UI.entityCard may not support extraHtml in all builds; degrade safely by
  // falling back to a plain card if the helper ignores it.
  if (!EOS_UI.entityCard) {
    EOS_UI.entityCard = function (o) {
      return '<div class="eos-entity-card" onclick="' + (o.onClick || '') + '"><strong>' + esc(o.title) + '</strong>' +
        '<div style="opacity:.7;font-size:12px;">' + esc(o.meta || '') + '</div>' + (o.extraHtml || '') + '</div>';
    };
  }

  // ── weave ──────────────────────────────────────────────────────
  function weave() {
    if (!STORY_MODE.enabled) return;
    EOS_UI.formModal('Weave a story', [
      { key: 'premise', label: 'Premise', type: 'textarea', placeholder: 'A lighthouse keeper finds a door that was not there yesterday…',
        // ✨ shared field-suggest, bespoke-grounding (book titles) via endpoint override.
        suggest: { app: 'reader', field: 'premise', endpoint: '/reader/api/story/suggest-premise', label: '💡 Suggest from my library' } },
      // formModal selects take plain-string options; it has no boolean type, so
      // comprehension-checks is a No/Yes select mapped to a bool on submit.
      { key: 'cefr', label: 'CEFR level', type: 'select', value: STORY_MODE.default_cefr,
        options: ['A2', 'B1', 'B2', 'C1', 'C2'] },
      { key: 'lang', label: 'Language', type: 'text', value: STORY_MODE.default_lang },
      { key: 'target_words', label: 'Target vocabulary (optional, comma-separated)', type: 'text', placeholder: 'ephemeral, brackish, foreboding' },
      { key: 'with_checks', label: 'Comprehension checks', type: 'select', value: 'No', options: ['No', 'Yes'] },
    ], async function (v) {
      if (!v.premise || !v.premise.trim()) { EOS_UI.toast('A premise is required', false); return; }
      var job = EOS_UI.jobProgress ? EOS_UI.jobProgress({ id: 'story-weave' }) : null;
      if (job) job.update({ stage: 'Weaving', detail: 'generating a branching ' + (v.cefr || 'C1') + ' story…' });
      var res = await api('/reader/api/story/weave', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          premise: v.premise, cefr: v.cefr || 'C1', lang: v.lang || 'English',
          target_words: v.target_words || '', with_checks: v.with_checks === 'Yes',
        }),
      });
      if (res && res.ok) {
        if (job) job.done({ stage: 'Woven', detail: res.nodes + ' beats' });
        EOS_UI.toast('Story woven: ' + res.title);
        openStory(res.slug);
      } else {
        if (job) job.hide();
        var msg = (res && res.error) || 'Weave failed';
        if (res && res.details && res.details.length) msg += ' — ' + res.details.join('; ');
        EOS_UI.toast(msg, false);
      }
    });
    // Premise field-suggest is now declarative (suggest: {...} on the field
    // above) — the shared EOS_UI.fieldSuggest auto-mounter injects the button.
  }
  window.STORY_MODE_weave = weave;

  // ── open / play ────────────────────────────────────────────────
  async function openStory(slug) {
    if (!STORY_MODE.enabled) return;
    var res = await api('/reader/api/story/' + encodeURIComponent(slug));
    if (!res || res.error) { EOS_UI.toast((res && res.error) || 'Story not found', false); return; }
    STORY_MODE.current = res;
    STORY_MODE.scenes = {};
    // Reuse the existing back button to leave the story.
    var back = document.getElementById('back-btn');
    if (back) { back.style.display = ''; back.onclick = closeStory; }
    document.getElementById('reader-shell').classList.remove('has-detail');
    document.getElementById('rail').style.display = 'none';
    if (res.view) { STORY_MODE.view = res.view; renderPlayer(); }
    else renderBegin();
  }
  window.STORY_MODE_open = openStory;

  function renderBegin() {
    var s = STORY_MODE.current;
    document.getElementById('main').innerHTML =
      '<div class="story-pane">' +
        '<h2 class="story-beat-title">' + esc(s.title) + '</h2>' +
        '<div class="story-crumbs">' + (s.cefr ? esc(s.cefr) + ' · ' : '') + esc(s.lang || 'English') + '</div>' +
        (s.premise ? '<div class="story-beat"><em>' + esc(s.premise) + '</em></div>' : '') +
        '<div class="story-toolbar"><button class="eos-btn" onclick="STORY_MODE_begin()">▶ Begin</button></div>' +
      '</div>';
  }
  window.STORY_MODE_begin = async function () {
    var slug = STORY_MODE.current && STORY_MODE.current.slug;
    if (!slug) return;
    var v = await api('/reader/api/story/' + encodeURIComponent(slug) + '/start', { method: 'POST' });
    if (v && !v.error) { STORY_MODE.view = v; renderPlayer(); }
    else EOS_UI.toast((v && v.error) || 'Could not start', false);
  };

  function renderPlayer() {
    var v = STORY_MODE.view, s = STORY_MODE.current;
    if (!v) return;
    var crumbs = (v.path || []).join(' › ');
    var sceneUrl = STORY_MODE.scenes[v.node_id] || v.scene || '';
    var html = '<div class="story-pane">' +
      '<div class="story-crumbs">' + (s.cefr ? esc(s.cefr) + ' · ' : '') + esc(crumbs || s.title) + '</div>' +
      (v.title ? '<h2 class="story-beat-title">' + esc(v.title) + '</h2>' : '') +
      (sceneUrl ? '<img class="story-scene-img" src="' + escAttr(sceneUrl) + '" alt="scene">' : '') +
      '<div class="story-beat">' + escWithWords(v.text || '') + '</div>';

    if (v.status === 'ended' || !(v.choices && v.choices.length)) {
      html += '<div class="story-end">' +
        '<div class="big">— The End —</div>' +
        '<div class="story-toolbar" style="justify-content:center;">' +
          (v.can_back ? '<button class="eos-btn-sm" onclick="STORY_MODE_back()">← Back</button>' : '') +
          ((v.steps || 0) > 1 ? '<button class="eos-btn-sm" onclick="STORY_MODE_soFar()">📖 Story so far</button>' : '') +
          '<button class="eos-btn" onclick="STORY_MODE_restart()">↻ Play again</button>' +
          '<button class="eos-btn-sm" onclick="STORY_MODE_toGame()">🎮 Make a game of this</button>' +
          '<button class="eos-btn-sm" onclick="STORY_MODE_close()">← Library</button>' +
        '</div></div>';
    } else {
      html += '<div class="story-choices">' +
        v.choices.map(function (c) {
          return '<button class="story-choice" onclick="STORY_MODE_choose(' + c.index + ')">' +
            '<span class="num">' + (c.index + 1) + '.</span>' + esc(c.label) + '</button>';
        }).join('') +
        '</div>' +
        '<div class="story-toolbar">' +
          (v.can_back ? '<button class="eos-btn-sm" onclick="STORY_MODE_back()">← Back</button>' : '') +
          '<button class="eos-btn-sm" id="story-illustrate" onclick="STORY_MODE_illustrate()">🎨 Illustrate</button>' +
          '<button class="eos-btn-sm" id="story-speak" onclick="STORY_MODE_speak()">🔊 Read aloud</button>' +
          ((v.steps || 0) > 1 ? '<button class="eos-btn-sm" onclick="STORY_MODE_soFar()">📖 Story so far</button>' : '') +
          '<button class="eos-btn-sm" onclick="STORY_MODE_restart()">↻ Restart</button>' +
        '</div>';
    }
    html += '</div>';
    document.getElementById('main').innerHTML = html;
  }

  window.STORY_MODE_choose = async function (index) {
    var slug = STORY_MODE.current && STORY_MODE.current.slug;
    if (!slug) return;
    var v = await api('/reader/api/story/' + encodeURIComponent(slug) + '/choose', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ choice: index }),
    });
    if (v && !v.error) { STORY_MODE.view = v; renderPlayer(); var p = document.getElementById('main'); if (p) p.scrollTop = 0; }
    else EOS_UI.toast((v && v.error) || 'Choice failed', false);
  };

  window.STORY_MODE_restart = async function () {
    var slug = STORY_MODE.current && STORY_MODE.current.slug;
    if (!slug) return;
    var v = await api('/reader/api/story/' + encodeURIComponent(slug) + '/restart', { method: 'POST' });
    if (v && !v.error) { STORY_MODE.view = v; STORY_MODE.scenes = {}; renderPlayer(); }
  };

  window.STORY_MODE_back = async function () {
    var slug = STORY_MODE.current && STORY_MODE.current.slug;
    if (!slug) return;
    var v = await api('/reader/api/story/' + encodeURIComponent(slug) + '/back', { method: 'POST' });
    if (v && !v.error) { STORY_MODE.view = v; renderPlayer(); var p = document.getElementById('main'); if (p) p.scrollTop = 0; }
    else EOS_UI.toast((v && v.error) || 'Cannot go back', false);
  };

  window.STORY_MODE_toGame = async function () {
    var slug = STORY_MODE.current && STORY_MODE.current.slug;
    if (!slug) return;
    var job = EOS_UI.jobProgress ? EOS_UI.jobProgress({ id: 'story-to-game' }) : null;
    if (job) job.update({ stage: 'Building a game', detail: 'turning the story into a playable top-down game…' });
    var res = await api('/reader/api/story/' + encodeURIComponent(slug) + '/to-game', { method: 'POST' });
    if (res && res.ok && res.url) {
      if (job) job.done({ stage: 'Game ready', detail: '' });
      EOS_UI.toast('Game built — opening it');
      window.open(res.url, '_blank', 'noopener');
    } else {
      if (job) job.hide();
      EOS_UI.toast((res && res.error) || 'Could not build a game', false);
    }
  };

  window.STORY_MODE_soFar = async function () {
    var slug = STORY_MODE.current && STORY_MODE.current.slug;
    if (!slug) return;
    var res = await api('/reader/api/story/' + encodeURIComponent(slug) + '/history');
    var beats = (res && res.beats) || [];
    if (!beats.length) { EOS_UI.toast('No beats yet', false); return; }
    var body = beats.map(function (b) {
      return '<div style="margin-bottom:16px;">' +
        (b.title ? '<h4 style="margin:0 0 4px;">' + esc(b.title) + '</h4>' : '') +
        '<div style="opacity:0.9;line-height:1.6;">' + escWithWords(b.text || '') + '</div></div>';
    }).join('');
    EOS_UI.modal({ title: 'Story so far', body: body, width: 680 });
  };

  window.STORY_MODE_illustrate = async function () {
    var v = STORY_MODE.view, slug = STORY_MODE.current && STORY_MODE.current.slug;
    if (!v || !slug) return;
    var btn = document.getElementById('story-illustrate');
    if (btn) { btn.disabled = true; btn.textContent = 'Illustrating…'; }
    var job = EOS_UI.jobProgress ? EOS_UI.jobProgress({ id: 'story-scene' }) : null;
    if (job) job.update({ stage: 'Illustrating', detail: 'GPU image' });
    var res = await api('/reader/api/story/' + encodeURIComponent(slug) + '/scene', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node: v.node_id, text: v.text }),
    });
    if (res && res.url) {
      STORY_MODE.scenes[v.node_id] = res.url;
      if (job) job.done({ stage: 'Illustrated', detail: '' });
      renderPlayer();
    } else {
      if (job) job.hide();
      EOS_UI.toast((res && res.error) || 'Illustration failed', false);
      if (btn) { btn.disabled = false; btn.textContent = '🎨 Illustrate'; }
    }
  };

  var _storyAudio = null;
  var _storyPlaying = false;
  function _stopStoryAudio() {
    _storyPlaying = false;
    if (_storyAudio) { try { _storyAudio.pause(); } catch (e) {} _storyAudio = null; }
  }
  // Multi-voice: /narrate splits the beat into narrator vs dialogue segments,
  // each with its own voice; play them in sequence. Click again to stop.
  window.STORY_MODE_speak = async function () {
    var v = STORY_MODE.view, slug = STORY_MODE.current && STORY_MODE.current.slug;
    if (!v || !slug) return;
    var btn = document.getElementById('story-speak');
    if (_storyPlaying) { _stopStoryAudio(); if (btn) btn.textContent = '🔊 Read aloud'; return; }
    if (btn) { btn.disabled = true; btn.textContent = 'Synthesizing…'; }
    var res = await api('/reader/api/story/' + encodeURIComponent(slug) + '/narrate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: v.text }),
    });
    if (btn) btn.disabled = false;
    var segs = (res && res.segments) || [];
    if (!segs.length) {
      if (btn) btn.textContent = '🔊 Read aloud';
      EOS_UI.toast((res && res.error) || 'No local TTS available', false);
      return;
    }
    _storyPlaying = true;
    if (btn) btn.textContent = '⏹ Stop';
    for (var i = 0; i < segs.length; i++) {
      if (!_storyPlaying) break;
      if (!segs[i].url) continue;
      _storyAudio = new Audio(segs[i].url);
      try { await _storyAudio.play(); }
      catch (e) { EOS_UI.toast('Browser blocked playback — click again', false); break; }
      await new Promise(function (resolve) {
        if (!_storyAudio) return resolve();
        _storyAudio.addEventListener('ended', resolve, { once: true });
        _storyAudio.addEventListener('error', resolve, { once: true });
      });
    }
    _storyPlaying = false;
    if (btn) btn.textContent = '🔊 Read aloud';
  };

  function closeStory() {
    _stopStoryAudio();
    STORY_MODE.current = null;
    STORY_MODE.view = null;
    var back = document.getElementById('back-btn');
    if (back) { back.style.display = 'none'; back.onclick = window.closeBook || null; }
    if (typeof renderLibrary === 'function') renderLibrary();
  }
  window.STORY_MODE_close = closeStory;

  // Deep-link: /reader/?story=<slug> (the hub "Continue your story" panel links
  // here). Query param, not hash — the book reader owns the # router. Defer until
  // the reader's own boot render has settled, then take over #main.
  window.addEventListener('load', function () {
    var slug;
    try { slug = new URLSearchParams(location.search).get('story'); } catch (e) { slug = null; }
    if (!slug) return;
    setTimeout(async function () {
      if (STORY_MODE.enabled === null) await initStoryMode();
      if (STORY_MODE.enabled) openStory(slug);
    }, 500);
  });
})();
