/* Picture packs — compose a pack from a description (authoring surface).
 *
 * Loaded after pictures.js, at the same position, so `S`, `esc`, `escAttr` and
 * `EOS_UI` are already bound. Owns its own state (`CS`) and its own mount
 * (#pd-compose), deliberately NOT a branch inside pictures.js render(): the two
 * surfaces answer different questions and a learner who never authors a pack
 * should never pay for this file's DOM.
 *
 * Exposes `window.EOS_PACK_COMPOSE` only when the feature is on, which is what
 * pictures.js checks before drawing the affordance. Off => this file adds
 * nothing to the page at all.
 *
 * The preview here is the whole point: the word list, verified against the photo
 * service, BEFORE a single image is fetched. Nothing is written until Apply.
 */

(function () {
  'use strict';

  var CS = {
    open: false,
    proposal: null,     // the server's proposal record
    busy: false,
    err: '',
    poll: null,
    resumable: [],      // proposals on disk the page can reopen
    resumableSig: null, // content signature of the above, to avoid blind repaints
    desc: '',           // the description input, preserved across re-renders
    packs: [],          // existing packs, as extend targets
    packsSig: null,     // content signature of the above
    target: '',         // '' = new pack, else the pack id being extended
  };

  function host() { return document.getElementById('pd-compose'); }
  function view() { return document.getElementById('pd-view'); }

  function get(u) { return EOS.apiSafe(u); }
  function post(u, b) {
    return EOS.apiSafe(u, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(b || {}),
    });
  }

  /* ── verdict vocabulary ──────────────────────────────────────────
   * Every row the user sees carries one of these. `no-photo` and
   * `duplicate-photo` are the two that must never be applied silently: the
   * first renders an emoji tile forever (indistinguishable from a designed
   * absence), the second makes a photo-to-name question unanswerable. */
  var VERDICT = {
    'new':             { mark: '+',  label: 'new',        tone: 'ok' },
    'reuse':           { mark: '=',  label: 'reused',     tone: 'ok' },
    'reuse-conflict':  { mark: '!',  label: 'conflict',   tone: 'warn' },
    'duplicate-photo': { mark: '!!', label: 'same photo', tone: 'bad' },
    'no-photo':        { mark: '-',  label: 'no photo',   tone: 'bad' },
    // Extending only: the pack already holds this slug, so the row writes
    // nothing. Left as `reuse` it counted toward the four-item gate and was
    // then dropped by the write, shipping a group below the floor.
    'in-pack':         { mark: '=',  label: 'already in this pack', tone: 'warn' },
    // A second name for an object the store already has (courgette beside
    // zucchini). It would take the same photo, so a quiz round would show one
    // picture matching two of its four options.
    'same-article':    { mark: '!!', label: 'same article', tone: 'bad' },
  };

  function rowHtml(r) {
    var v = VERDICT[r.verdict] || { mark: '?', label: r.verdict || '?', tone: 'warn' };
    var wiki = r.wiki_tried && r.wiki_tried !== r.wiki
      ? '<s class="pd-muted">' + esc(r.wiki_tried) + '</s> &rarr; ' + esc(r.wiki)
      : esc(r.wiki || '');
    var note = '';
    if (r.verdict === 'no-photo') {
      note = 'tried ' + esc((r.tried || []).join(', '));
    } else if (r.verdict === 'duplicate-photo') {
      note = 'shares its photo with ' + esc((r.shares_with || []).join(', '));
    } else if (r.verdict === 'reuse-conflict') {
      note = 'already in the store as ' + esc(r.shipped_wiki || '');
    } else if (r.verdict === 'same-article') {
      note = 'same source article as ' + esc(r.same_as || '') +
             ' — a second name for the same thing';
    } else if (r.warning) {
      note = esc(r.warning);
    }
    return '<tr class="pc-' + v.tone + '">' +
      '<td class="pc-mark" title="' + escAttr(v.label) + '">' + v.mark + '</td>' +
      '<td>' + esc(r.emoji || '') + ' <b>' + esc(r.name) + '</b>' +
        '<div class="pd-muted">' + esc(r.chinese || '') + ' ' + esc(r.pinyin || '') + '</div></td>' +
      '<td>' + esc(r.group || '') + '</td>' +
      '<td>' + wiki + (note ? '<div class="pd-muted">' + note + '</div>' : '') + '</td>' +
      '<td class="pc-act">' +
        '<input class="pd-search pc-fix" placeholder="article title" ' +
          'onkeydown="if(event.key===\'Enter\')EOS_PACK_COMPOSE.setTitle(' +
          EOS_UI.jsArg(r.slug) + ',this.value)">' +
        '<button class="eos-btn eos-btn-sm" onclick="EOS_PACK_COMPOSE.drop(' +
          EOS_UI.jsArg(r.slug) + ')">Drop</button>' +
      '</td></tr>';
  }

  function summaryHtml(p) {
    var s = p.summary || {}, v = s.verdicts || {};
    var bits = [];
    if (v['new']) bits.push(v['new'] + ' new');
    if (v['reuse']) bits.push(v['reuse'] + ' reused');
    var bad = (p.rows || []).filter(function (r) {
      return r.verdict && ['new', 'reuse'].indexOf(r.verdict) < 0;
    }).length;
    if (bad) bits.push(bad + ' rejected');
    var groups = Object.keys(s.groups || {}).length;
    if (groups) bits.push(groups + ' groups');

    var warn = '';
    if (p.capped) {
      warn += '<div class="pc-note">The model returned ' + (p.rows.length + p.capped) +
        ' items; kept the first ' + p.rows.length + '.</div>';
    }
    if ((p.dropped || []).length) {
      warn += '<div class="pc-note">Dropped before checking: ' +
        p.dropped.map(function (d) {
          return esc(d.slug || '?') + ' (' + esc(d.reason) + ')';
        }).join(', ') + '</div>';
    }
    /* The apply gate names two repairs. Drop is a per-row button above; merge
     * had no affordance at all, so a three-item group's only reachable repair
     * was deleting three verified, photo-checked rows to clear a warning that
     * is about arrangement, not content. One control per thin group, because
     * the gate blocks per group. */
    // Existing groups carry their label on the proposal, not in pack.labels —
    // the prompt tells the model not to re-declare them — so fold both in or an
    // extend's merge picker offers a bare `fruit` where the app shows `Fruit`.
    var labels = Object.assign({}, (p.pack && p.pack.labels) || {});
    Object.keys(p.existing_groups || {}).forEach(function (g) {
      if (!labels[g] && p.existing_groups[g].label) labels[g] = p.existing_groups[g].label;
    });
    var gname = function (g) { return labels[g] || g; };
    var thin = s.thin_groups || {};
    var thinKeys = Object.keys(thin);
    if (thinKeys.length) {
      var counts = s.groups || {};
      // The server sends its own floor; hardcoding 4 here would be a second
      // literal drifting from picture_quiz.OPTIONS_PER_ROUND.
      var floor = s.min_group || 0;
      warn += '<div class="pc-note pc-bad">Too small to build a four-option quiz round: ' +
        thinKeys.map(function (g) {
          return esc(gname(g)) + ' (' + thin[g] + ')';
        }).join(', ') + '. Merge or drop them before applying.' +
        thinKeys.map(function (g, i) {
          // Only targets that would actually clear this group's block. A thin
          // target is fine when the two sum past the floor (2+2), and useless
          // when they do not (1+1) — offering the useless one spends the repair
          // and leaves the same red warning on screen.
          var others = Object.keys(counts).filter(function (o) {
            return o !== g && thin[g] + counts[o] >= floor;
          });
          // Nothing big enough to merge into: the honest answer is that the
          // pack needs more items, not rearranging. Drop stays available above.
          if (!others.length) return '';
          // Positional id: a group id is never validated to be url-safe, and a
          // space in one would make this an invalid HTML5 id (unaddressable by
          // any CSS or Playwright selector, even though getElementById copes).
          var selId = 'pc-into-' + i;
          return '<div class="pc-merge">Move the ' + thin[g] + ' in <b>' +
            esc(gname(g)) + '</b> into ' +
            '<select id="' + selId + '">' +
            others.map(function (o) {
              return '<option value="' + escAttr(o) + '">' + esc(gname(o)) +
                ' (' + (counts[o] || 0) + ')</option>';
            }).join('') +
            '</select> <button class="eos-btn eos-btn-sm" onclick="EOS_PACK_COMPOSE.mergeGroup(' +
            EOS_UI.jsArg(g) + ',' + EOS_UI.jsArg(selId) + ')">Merge</button></div>';
        }).join('') + '</div>';
    }
    return '<div class="pc-summary"><b>' + bits.join(' · ') + '</b>' + warn + '</div>';
  }

  /* A proposal outlives the tab that made it, but `CS.proposal` did not: it was
   * set only by propose(), so a reload orphaned a verified list on disk with no
   * way back to it. That mattered most for exactly the proposals the apply gate
   * had blocked — reaching the repair would have cost a whole re-propose. */
  /* Fetched when the panel opens, not on page load: a learner who never authors
   * a pack should not pay a request for this file's surface. Failure is silent —
   * an unreachable list means no resume row, never a broken compose form. */
  async function loadResumable() {
    // `no-store` is load-bearing, not belt-and-braces. The daemon sends no
    // Cache-Control on API responses, so the browser caches this GET
    // heuristically and re-serves it: measured showing "ready to apply" for a
    // proposal the server was reporting as thin in the same tick. The list's
    // whole job is to report current state.
    var r = await EOS.apiSafe('/dictionary/api/picture/packs/proposals',
                              { cache: 'no-store' });
    var next = (r && !r.error && r.proposals) || [];
    // Signature over content, not length: a count is blind to a row finishing
    // composition (running -> ready, 0 -> 38 words) and to a simultaneous
    // add+remove, so the list would keep claiming a finished pack is still
    // composing for the rest of the session.
    // The signature must cover every field resumeHtml() renders, or the list
    // shows a state the server has already corrected. Missing thin_groups here
    // left a row reading "1 group too small" straight after the merge that
    // cleared it — the one action most likely to be taken from this list.
    var sig = next.map(function (p) {
      return p.id + ':' + p.status + ':' + p.row_count + ':' +
        Object.keys(p.thin_groups || {}).length;
    }).join('|');
    CS.resumable = next;
    if (sig === CS.resumableSig) return;
    // Advance the signature ONLY when we actually paint. Advancing it on a
    // skipped paint (a proposal was open, so the list is not on screen) told
    // the next load "nothing changed" and left the stale row up permanently —
    // it took a second, redundant open to correct.
    if (CS.proposal) return;
    CS.resumableSig = sig;
    // render() replaces the panel's innerHTML, which destroys #pc-desc. The
    // input mirrors into CS.desc on every keystroke and is re-rendered from it,
    // so a repaint mid-word keeps the text. This fetch resolves *after* the
    // form is on screen, and only when there is something to list — i.e.
    // exactly when someone is most likely mid-word.
    render();
  }

  /* New pack, or add to one that exists. Extending is opt-in on the wire too —
   * the server refuses an existing id without it — because a description like
   * "food in the fridge" derives the id `food`, and inferring intent from that
   * collision would write into the shipped Food pack. */
  /* The extend targets. Read from the picture status the page already serves,
   * so this adds no endpoint. Failure is silent: no list simply means the
   * picker does not render and every proposal is a new pack, which is the
   * behaviour that existed before extending did. */
  async function loadPacks() {
    var st = await EOS.apiSafe('/dictionary/api/picture/status', { cache: 'no-store' });
    var packs = (st && !st.error && st.packs) || [];
    // Signature over content — a count is blind to a pack that GREW, which is
    // exactly what extending does, so the picker would read "Food (49)" for the
    // rest of the session after adding to it.
    var sig = packs.map(function (p) { return p.id + ':' + p.title + ':' + p.count; }).join('|');
    CS.packs = packs;
    if (sig === CS.packsSig) return;
    if (CS.proposal) return;
    CS.packsSig = sig;
    render();
  }

  function targetHtml() {
    var packs = CS.packs || [];
    if (!packs.length) return '';
    return '<p class="pc-target">Add to ' +
      '<select id="pc-target" onchange="EOS_PACK_COMPOSE.noteTarget(this.value)">' +
      '<option value=""' + (CS.target ? '' : ' selected') + '>a new pack</option>' +
      packs.map(function (p) {
        return '<option value="' + escAttr(p.id) + '"' +
          (CS.target === p.id ? ' selected' : '') + '>' +
          esc(p.title || p.id) + ' (' + (p.count || 0) + ')</option>';
      }).join('') +
      '</select></p>';
  }

  function resumeHtml() {
    if (!CS.resumable.length) return '';
    return '<div class="pc-resume"><b>Unfinished</b>' +
      CS.resumable.map(function (r) {
        var thin = Object.keys(r.thin_groups || {}).length;
        var bits = [r.row_count + ' words'];
        // Every state gets a word. A row whose status showed only by the
        // ABSENCE of a note is exactly what list-card-density.md forbids.
        if (r.status === 'running') bits.push('still composing');
        else if (r.status === 'error') bits.push('failed to finish');
        else if (thin) bits.push(thin + (thin === 1 ? ' group' : ' groups') + ' too small');
        else bits.push('ready to apply');
        return '<div class="pc-resume-row">' +
          '<span>' + esc(r.description || r.pack_id) + ' <span class="pd-muted">(' +
          esc(bits.join(' · ')) + ')</span></span> ' +
          '<button class="eos-btn eos-btn-sm" onclick="EOS_PACK_COMPOSE.resume(' +
          EOS_UI.jsArg(r.id) + ')">Open</button></div>';
      }).join('') + '</div>';
  }

  function render() {
    var h = host();
    if (!h) return;
    if (!CS.open) { h.innerHTML = ''; h.hidden = true; if (view()) view().hidden = false; return; }
    h.hidden = false;
    if (view()) view().hidden = true;

    var p = CS.proposal;
    // An extend and a new pack are the same screen otherwise, and the single
    // most important fact — which pack you are about to change, and how big it
    // already is — appeared nowhere. proposed-action.md wants the preview to
    // state what will change.
    var ext = (p && p.extends) || (!p && CS.target) || '';
    var extPack = ext ? (CS.packs.filter(function (x) { return x.id === ext; })[0] || {}) : {};
    var head = '<div class="pc-head"><h3>' +
      (ext ? 'Add to ' + esc(extPack.title || ext) +
             (extPack.count ? ' <span class="pd-muted">(' + extPack.count + ' now)</span>' : '')
           : 'New pack from a description') + '</h3>' +
      '<span id="pc-model"></span>' +
      '<button class="eos-btn eos-btn-sm" onclick="EOS_PACK_COMPOSE.close()">Close</button></div>';

    if (CS.err) {
      h.innerHTML = head + EOS_UI.errorState({ message: CS.err,
        onRetry: 'EOS_PACK_COMPOSE.reset()' });
      return;
    }

    if (!p) {
      h.innerHTML = head +
        '<p class="pd-muted">Describe a theme. A model proposes the words; every ' +
        'article title is checked against the photo service before anything is ' +
        'written, and you approve the list before a single image is fetched.</p>' +
        targetHtml() +
        '<p><input class="pd-search" id="pc-desc" style="max-width:420px" ' +
        'value="' + escAttr(CS.desc || '') + '" ' +
        'oninput="EOS_PACK_COMPOSE.noteDesc(this.value)" ' +
        'placeholder="things you see at an airport" ' +
        'onkeydown="if(event.key===\'Enter\')EOS_PACK_COMPOSE.propose()"></p>' +
        '<button class="eos-btn eos-btn-primary" ' +
        (CS.busy ? 'disabled' : '') +
        ' onclick="EOS_PACK_COMPOSE.propose()">' +
        (CS.busy ? 'Working…' : 'Propose a pack') + '</button>' +
        resumeHtml();
      mountPill();
      return;
    }

    if (p.status === 'running') {
      var stage = { queued: 'starting', thinking: 'the model is proposing words',
                    verifying: 'checking every article against the photo service'
                  }[p.stage] || p.stage || 'working';
      h.innerHTML = head + '<div class="pd-center"><p>' + esc(stage) + '…</p>' +
        '<p class="pd-muted">Nothing has been written and no image has been fetched.</p>' +
        '</div>';
      return;
    }

    if (p.status === 'error') {
      h.innerHTML = head + EOS_UI.errorState({
        message: 'The proposal failed: ' + (p.error || 'unknown error'),
        onRetry: 'EOS_PACK_COMPOSE.reset()' });
      return;
    }

    if (p.status === 'applied') {
      h.innerHTML = head + '<div class="pd-center">' +
        '<h4>' + esc(p.pack_id) + (p.extends ? ' extended' : ' created') + '</h4>' +
        '<p class="pd-muted">The photos are downloading now. Nothing has been ' +
        'reviewed by eye — a clean fetch proves the bytes arrived, not that each ' +
        'picture shows the right thing.</p>' +
        '<p><code>/eos-picture-pack-review ' + esc(p.pack_id) + '</code></p>' +
        '<button class="eos-btn" onclick="EOS_PACK_COMPOSE.close()">Done</button></div>';
      return;
    }

    var applicable = (p.rows || []).filter(function (r) {
      return r.verdict === 'new' || r.verdict === 'reuse';
    }).length;
    h.innerHTML = head +
      '<div class="pc-desc pd-muted">' + esc(p.description) + ' &rarr; <b>' +
        esc(p.pack_id) + '</b></div>' +
      summaryHtml(p) +
      '<table class="pc-table"><tbody>' + (p.rows || []).map(rowHtml).join('') +
      '</tbody></table>' +
      '<div class="pc-actions">' +
      '<button class="eos-btn eos-btn-primary" ' + (applicable ? '' : 'disabled') +
        ' onclick="EOS_PACK_COMPOSE.apply()">Approve &amp; fetch ' + applicable + '</button> ' +
      '<button class="eos-btn" onclick="EOS_PACK_COMPOSE.reject()">Discard</button>' +
      '</div>';
    mountPill();
  }

  /* Compose has its own pill because it renders in a slot the lookup surface
   * never draws. The lookup tab carries one too (index.html) — its definition
   * is also model output, so both surfaces name the provider that pays. */
  function mountPill() {
    var slot = document.getElementById('pc-model');
    if (slot && !slot.dataset.mounted && window.EOS_UI && EOS_UI.modelPill) {
      slot.dataset.mounted = '1';
      try { EOS_UI.modelPill({ app: 'dictionary', mount: slot, domain: 'text' }); }
      catch (e) { /* the pill is chrome; never let it break authoring */ }
    }
  }

  function stopPoll() { if (CS.poll) { clearInterval(CS.poll); CS.poll = null; } }

  function startPoll(pid) {
    stopPoll();
    CS.poll = setInterval(async function () {
      var p = await get('/dictionary/api/picture/packs/proposal/' + encodeURIComponent(pid));
      if (p && p.error) { stopPoll(); CS.err = p.error; render(); return; }
      CS.proposal = p;
      if (p && p.status !== 'running') stopPoll();
      render();
    }, 1500);
  }

  /* Installed only when the daemon says the flag is on. pictures.js checks for
   * this object before drawing anything, so flag-off is not a hidden button — it
   * is an absent one. */
  function install() {
    window.EOS_PACK_COMPOSE = API;
    if (typeof S !== 'undefined' && S.render) S.render();
  }

  var API = {
    affordance: function () {
      return '<button class="pd-chip pc-open" onclick="EOS_PACK_COMPOSE.open()" ' +
             'title="Describe a theme and get a verified pack">+ New pack</button>';
    },
    open: function () { CS.open = true; render(); loadResumable(); loadPacks(); },

    noteDesc: function (v) { CS.desc = v; },
    noteTarget: function (v) { CS.target = v || ''; },

    /* Reopen a proposal left on disk. Polls if it is still composing, so a
     * reload mid-run rejoins the run instead of watching a frozen card. */
    resume: async function (id) {
      var p = await get('/dictionary/api/picture/packs/proposal/' + encodeURIComponent(id));
      // An errored proposal CARRIES an `error` field and is still a record
      // worth opening — apply sets it when write_pack raises, keeping every
      // verified row. Only a response with no `status` is an envelope error
      // ({"error": "no such proposal"}); testing `p.error` alone would make
      // exactly those proposals permanently unopenable.
      if (!p || (p.error && !p.status)) {
        EOS_UI.toast((p && p.error) || 'could not reopen that proposal', false);
        loadResumable();   // it is probably gone; do not leave a ghost row
        return;
      }
      CS.proposal = p;
      CS.err = '';
      render();
      if (p.status === 'running') startPoll(id);
    },
    /* Closing drops the loaded proposal. It is safe — the proposal lives on
     * disk and the Unfinished list reopens it — and it is what makes the list
     * reachable a second time: resumeHtml() renders only in the no-proposal
     * branch, so without this, opening the wrong one of two proposals left a
     * reload as the only non-destructive way back. */
    close: function () { stopPoll(); CS.open = false; CS.proposal = null; CS.target = ''; render(); },
    reset: function () { CS.proposal = null; CS.err = ''; CS.busy = false; CS.target = ''; render(); },

    propose: async function () {
      var el = document.getElementById('pc-desc');
      var desc = (el && el.value || '').trim();
      if (!desc) return;
      CS.busy = true; CS.err = ''; render();
      var body = { description: desc };
      if (CS.target) { body.pack_id = CS.target; body.extend = true; }
      var r = await post('/dictionary/api/picture/packs/propose', body);
      CS.busy = false;
      if (!r || r.error) { CS.err = (r && r.error) || 'could not start'; render(); return; }
      CS.proposal = { id: r.id, pack_id: r.pack_id, description: desc,
                      status: 'running', stage: 'queued', rows: [] };
      render();
      startPoll(r.id);
    },

    drop: async function (slug) {
      if (!CS.proposal) return;
      var p = await post('/dictionary/api/picture/packs/proposal/' +
                         encodeURIComponent(CS.proposal.id) + '/edit', { slug: slug, drop: true });
      if (p && !p.error) { CS.proposal = p; render(); }
    },

    /* Reports failure, but through a toast rather than `CS.err`: that flag makes
     * render() replace the whole surface with an error card whose only button is
     * reset(), which nulls CS.proposal — so one hiccup mid-merge would throw away
     * a verified 38-row proposal the page has no way to reopen. Silence is wrong
     * too (the row verbs get away with it; a no-op merge looks like a successful
     * one and sends the user back to a still-blocked Apply). */
    mergeGroup: async function (group, selId) {
      if (!CS.proposal) return;
      var sel = document.getElementById(selId);
      var into = sel && sel.value;
      if (!into) return;
      var p = await post('/dictionary/api/picture/packs/proposal/' +
                         encodeURIComponent(CS.proposal.id) + '/edit',
                         { merge_group: group, into: into });
      if (!p || p.error) {
        EOS_UI.toast((p && p.error) || 'could not merge that group', false);
        return;
      }
      CS.proposal = p;
      render();
    },

    setTitle: async function (slug, wiki) {
      if (!CS.proposal || !(wiki || '').trim()) return;
      var p = await post('/dictionary/api/picture/packs/proposal/' +
                         encodeURIComponent(CS.proposal.id) + '/edit',
                         { slug: slug, wiki: wiki.trim() });
      if (p && !p.error) { CS.proposal = p; render(); }
    },

    apply: async function () {
      if (!CS.proposal) return;
      var r = await post('/dictionary/api/picture/packs/proposal/' +
                         encodeURIComponent(CS.proposal.id) + '/apply', {});
      if (!r || r.error) { CS.err = (r && r.error) || 'apply failed'; render(); return; }
      CS.proposal = Object.assign({}, CS.proposal, { status: 'applied' });
      render();
      if (typeof loadStatus === 'function') loadStatus();
    },

    reject: async function () {
      if (!CS.proposal) return;
      await post('/dictionary/api/picture/packs/proposal/' +
                 encodeURIComponent(CS.proposal.id) + '/reject', {});
      this.reset();
      // reset() repaints the list from CS.resumable, which still holds the row
      // just deleted — clicking it would toast "no such proposal".
      loadResumable();
    },
  };

  /* One extra status read rather than threading a flag through pictures.js —
   * the page has already fetched this by the time anyone clicks, and a wrong
   * guess here would either hide a live feature or show a dead button. */
  EOS.apiSafe('/dictionary/api/picture/status').then(function (st) {
    if (st && !st.error && st.composer) install();
  });
})();
