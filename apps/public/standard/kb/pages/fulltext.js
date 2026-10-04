/* kb — full-text reader (KBFull), embedded in the no-tab docs page.
 *
 * For a reference note with stored full text, mountReader() builds the reading
 * surface IN PLACE (no tabs):
 *   LEFT   #kb-docnav         — document TOC: "Overview" + every section, by chapter
 *   CENTER #kb-fulltext-mount — verbatim text, lazy per chapter, BELOW the overview body
 *   RIGHT  #detail-toc        — "on this page": the current chapter's sections (scroll-spy)
 * Returns true when a full text exists (else the caller uses the leaf layout).
 *
 * Backed by /kb/api/notes/{slug}/fulltext + /fulltext/section?chapter=N.
 */
var KBFull = (function () {
  var NAV = 'kb-docnav', MOUNT = 'kb-fulltext-mount', ONPAGE = 'detail-toc';
  var S = { slug: null, contents: [], byChapter: {}, chapters: [], chapterTitles: {}, archive: '',
            loaded: {}, curChapter: null, lazyObs: null, spyObs: null, _scrollHandler: null, _jumping: false };

  function _get(url) { return fetch(url).then(function (r) { return r.json(); }); }
  function _chOf(no) { return String(no || '').split('.')[0]; }
  function _chTitle(ch) { return (S.chapterTitles && S.chapterTitles[ch]) || ('Chapter ' + ch); }

  // Build the reader in place from the standard's COMPOSED atomic clause notes
  // (not a flat-text slice). Returns false when there are no clause notes.
  async function mountReader(slug, data) {
    teardown();
    S.slug = slug; S.loaded = {}; S.curChapter = null;
    var idx;
    try { idx = await _get('/kb/api/notes/' + encodeURIComponent(slug) + '/compose'); }
    catch (e) { return false; }
    if (!idx || !idx.has_clauses || !(idx.contents || []).length) return false;
    S.contents = idx.contents;
    S.archive = idx.archive || '';
    S.chapterTitles = {};
    (idx.chapters || []).forEach(function (c) { S.chapterTitles[c.no] = c.title; });
    S.byChapter = {}; S.chapters = [];
    S.contents.forEach(function (c) {
      var ch = c.chapter;
      if (!(ch in S.byChapter)) { S.byChapter[ch] = []; S.chapters.push(ch); }
      S.byChapter[ch].push(c);
    });
    S.chapters.sort(function (a, b) { return (parseInt(a, 10) || 999) - (parseInt(b, 10) || 999); });
    var nav = document.getElementById(NAV); if (nav) nav.hidden = false;
    _renderDocNav();
    _renderChapterShells();
    _setupLazy();
    return true;
  }

  function _renderDocNav() {
    var left = document.getElementById(NAV);
    if (!left) return;
    var html = '<div class="ft-toc-title">Contents · ' + S.contents.length + ' sections</div>' +
      '<a class="ft-toc-link ft-overview" data-overview="1" href="#">Overview</a>';
    S.chapters.forEach(function (ch) {
      html += '<details class="ft-ch" open><summary>' + esc(ch) + '. ' + esc(_chTitle(ch)) + '</summary>';
      S.byChapter[ch].forEach(function (c) {
        if (c.level === 1) return;
        html += '<a class="ft-toc-link ft-l' + c.level + '" data-no="' + escAttr(c.no) + '" href="#">' +
          '§' + esc(c.no) + ' ' + esc(c.title) + '</a>';
      });
      html += '</details>';
    });
    left.innerHTML = html;
    var ov = left.querySelector('[data-overview]');
    if (ov) ov.addEventListener('click', function (e) {
      e.preventDefault();
      var b = document.getElementById('detail-body');
      if (b) b.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    left.querySelectorAll('[data-no]').forEach(function (a) {
      a.addEventListener('click', function (e) { e.preventDefault(); jumpTo(a.getAttribute('data-no')); });
    });
  }

  function _renderChapterShells() {
    var mount = document.getElementById(MOUNT);
    if (!mount) return;
    mount.innerHTML = '<div class="ft-divider">Full text</div>' + S.chapters.map(function (ch) {
      return '<section class="ft-chapter" data-chapter="' + escAttr(ch) + '" id="ftc-' + escAttr(ch) + '">' +
        '<h3 class="ft-ch-head">' + esc(ch) + '. ' + esc(_chTitle(ch)) + '</h3>' +
        '<div class="ft-ch-body"><div class="ft-loading">Loading…</div></div></section>';
    }).join('');
  }

  function _setupLazy() {
    if (S.lazyObs) S.lazyObs.disconnect();
    S.lazyObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) _loadChapter(en.target.getAttribute('data-chapter')); });
    }, { rootMargin: '500px 0px 0px 0px' });
    document.querySelectorAll('#' + MOUNT + ' .ft-chapter').forEach(function (s) { S.lazyObs.observe(s); });
    if (S._scrollHandler) window.removeEventListener('scroll', S._scrollHandler);
    S._scrollHandler = _throttle(_updateCurrentChapter, 150);
    window.addEventListener('scroll', S._scrollHandler, { passive: true });
    if (S.chapters.length) { _loadChapter(S.chapters[0]); _setCurrentChapter(S.chapters[0]); }
  }

  function _throttle(fn, ms) {
    var last = 0, timer = null;
    return function () {
      var now = (window.performance && performance.now) ? performance.now() : last + ms + 1;
      var wait = ms - (now - last);
      if (wait <= 0) { last = now; fn(); }
      else if (!timer) { timer = setTimeout(function () { timer = null; last = (performance.now ? performance.now() : 0); fn(); }, wait); }
    };
  }

  function _updateCurrentChapter() {
    if (S._jumping) return;
    var secs = document.querySelectorAll('#' + MOUNT + ' .ft-chapter');
    var cur = null;
    for (var i = 0; i < secs.length; i++) {
      if (secs[i].getBoundingClientRect().top <= 140) cur = secs[i].getAttribute('data-chapter');
      else break;
    }
    if (cur) _setCurrentChapter(cur);
  }

  async function _loadChapter(ch) {
    if (S.loaded[ch]) return;
    S.loaded[ch] = true;
    var sec = document.getElementById('ftc-' + ch);
    if (!sec) { S.loaded[ch] = false; return; }
    var bodyEl = sec.querySelector('.ft-ch-body');
    try {
      var res = await _get('/kb/api/notes/' + encodeURIComponent(S.slug) +
        '/compose/chapter?chapter=' + encodeURIComponent(ch));
      bodyEl.innerHTML = (res.sections || []).map(_renderClause).join('') ||
        '<p class="ft-p ft-muted">(no sections)</p>';
      if (window.bindWikiLinks) bindWikiLinks(MOUNT);
      _refreshSpy();
    } catch (e) {
      // No onRetry: _loadChapter is private to this module. S.loaded[ch] resets
      // below, so the lazy observer or a contents jump reloads it.
      bodyEl.innerHTML = EOS_UI.errorState({ message: 'Could not load chapter ' + ch + '. Jump to it from the contents to try again.' });
      S.loaded[ch] = false;
    }
  }

  // One composed clause = its note's markdown body, anchored by clause number,
  // with a link to open the standalone clause note. The body already carries its
  // own "# §X.Y — title" heading + "## §X.Y.Z" subsections.
  function _renderClause(s) {
    var md = (s.body || '');
    var html;
    if (window.EOS_UI && EOS_UI.renderMarkdownWithEmbeds) {
      html = EOS_UI.renderMarkdownWithEmbeds(md, [], {
        wikiLink: function (t, l) {
          return '<a href="#' + encodeURIComponent(t) + '" data-wiki="' + escAttr(t) + '" class="kb-wiki">' + esc(l) + '</a>';
        },
      });
    } else {
      html = '<pre class="ft-p">' + esc(md) + '</pre>';
    }
    var open = '<a class="ft-open-note" href="#' + encodeURIComponent(s.slug) +
      '" data-wiki="' + escAttr(s.slug) + '" title="Open §' + escAttr(s.clause) + ' as its own note">open note ↗</a>';
    return '<section class="ft-sec ft-clause" id="ftsec-' + escAttr(s.clause) + '" data-no="' + escAttr(s.clause) + '">' +
      '<div class="ft-clause-bar">' + open + '</div>' + html + '</section>';
  }

  async function jumpTo(no) {
    var ch = _chOf(no);
    S._jumping = true;
    for (var i = 0; i < S.chapters.length; i++) {
      await _loadChapter(S.chapters[i]);
      if (S.chapters[i] === ch) break;
    }
    function go() {
      var el = document.getElementById('ftsec-' + no) || document.getElementById('ftc-' + ch);
      if (el) el.scrollIntoView({ behavior: 'auto', block: 'start' });
    }
    go();
    setTimeout(function () { go(); _setCurrentChapter(ch); S._jumping = false; }, 140);
  }

  // RIGHT rail (#detail-toc) = the current chapter's sections.
  function _setCurrentChapter(ch) {
    if (ch === S.curChapter) return;
    S.curChapter = ch;
    var rail = document.getElementById(ONPAGE);
    if (!rail) return;
    var rows = (S.byChapter[ch] || []).filter(function (c) { return c.level >= 2; });
    if (!rows.length) { rail.hidden = true; rail.innerHTML = ''; return; }
    rail.hidden = false;
    rail.innerHTML = '<div class="ft-toc-title">On this page</div><div class="ft-onpage-ch">Ch ' + esc(ch) + ' · ' + esc(_chTitle(ch)) + '</div>' +
      rows.map(function (c) {
        return '<a class="ft-op-link ft-l' + c.level + '" data-no="' + escAttr(c.no) + '" href="#">§' + esc(c.no) + ' ' + esc(c.title) + '</a>';
      }).join('');
    rail.querySelectorAll('[data-no]').forEach(function (a) {
      a.addEventListener('click', function (e) { e.preventDefault(); jumpTo(a.getAttribute('data-no')); });
    });
    _refreshSpy();
  }

  function _refreshSpy() {
    if (S.spyObs) S.spyObs.disconnect();
    var secs = document.querySelectorAll('#' + MOUNT + ' .ft-sec');
    if (!secs.length) return;
    var visible = {};
    S.spyObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) visible[en.target.id] = true; else delete visible[en.target.id];
      });
      var ids = [].map.call(secs, function (s) { return s.id; }).filter(function (id) { return visible[id]; });
      var topId = ids[0];
      document.querySelectorAll('#' + ONPAGE + ' .ft-op-link').forEach(function (a) {
        a.classList.toggle('active', topId === 'ftsec-' + a.getAttribute('data-no'));
      });
    }, { rootMargin: '0px 0px -75% 0px', threshold: 0 });
    [].forEach.call(secs, function (s) { S.spyObs.observe(s); });
  }

  function teardown() {
    if (S.lazyObs) { S.lazyObs.disconnect(); S.lazyObs = null; }
    if (S.spyObs) { S.spyObs.disconnect(); S.spyObs = null; }
    if (S._scrollHandler) { window.removeEventListener('scroll', S._scrollHandler); S._scrollHandler = null; }
    S.loaded = {}; S.curChapter = null; S._jumping = false;
  }

  return { mountReader: mountReader, teardown: teardown };
})();
