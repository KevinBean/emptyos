// learn -- page logic, extracted verbatim from pages/index.html (P4 Atomic
// split, .claude/rules/multi-module-apps.md frontend pattern). Loaded at the
// same position as the old inline <script>, so global scope and load order
// vs eos.js / eos-components.js are unchanged.
// ─── State + routing ─────────────────────────────────────────
var STATE = { route: null, catalog: [], course: null, lesson: null, quiz: null };

function navigate() {
  var h = (location.hash || '').replace(/^#/, '');
  if (!h) { showCatalog(); return; }
  var parts = h.split('/').filter(Boolean);
  // #review
  if (parts[0] === 'review') { showReview(); return; }
  // #course/<id>
  if (parts[0] === 'course' && parts.length === 2) { showCourse(parts[1]); return; }
  // #course/<id>/lesson/<idx>
  if (parts[0] === 'course' && parts[2] === 'lesson' && parts.length === 4) { showLesson(parts[1], parseInt(parts[3], 10)); return; }
  // #course/<id>/lesson/<idx>/quiz
  if (parts[0] === 'course' && parts[2] === 'lesson' && parts[4] === 'quiz') { showQuiz(parts[1], parseInt(parts[3], 10)); return; }
  // #course/<id>/diagnostic
  if (parts[0] === 'course' && parts[2] === 'diagnostic' && parts.length === 3) { showDiagnostic(parts[1]); return; }
  showCatalog();
}
window.addEventListener('hashchange', navigate);
window.addEventListener('DOMContentLoaded', navigate);

function showView(id) {
  ['catalog-view','course-view','lesson-view','quiz-view','review-view','diagnostic-view'].forEach(function(v) {
    document.getElementById(v).style.display = (v === id ? 'block' : 'none');
  });
}

// ─── Tutorial helpers (Lathe borrow) ─────────────────────────
var VERIFY_BADGES = {
  verified:  { icon: '✓', label: 'verified',   cls: 'eos-badge-status-pass' },
  verifying: { icon: '⏳', label: 'verifying…', cls: 'eos-badge-status-running' },
  skipped:   { icon: '⚠', label: 'skipped',    cls: 'eos-badge-status-shelved' },
  failed:    { icon: '✗', label: 'failed',     cls: 'eos-badge-status-fail' },
};
function verifyBadgeHtml(status, error) {
  var b = VERIFY_BADGES[status];
  if (!b) return '';
  var tip = status === 'failed' && error ? escAttr(error) : 'Tutorial ' + b.label;
  return '<span class="eos-badge ' + b.cls + '" title="' + tip + '">' + b.icon + ' ' + b.label + '</span>';
}
// ─── Catalog ─────────────────────────────────────────────────
async function showCatalog() {
  showView('catalog-view');
  var res = await fetch('/learn/api/courses').then(function(r) { return r.json(); });
  STATE.catalog = res.courses || [];
  // Review-queue chip in catalog header
  try {
    var stats = await fetch('/learn/api/review/stats').then(function(r) { return r.json(); });
    var chipEl = document.getElementById('catalog-review-chip');
    // The chip counts the whole unified queue, not just learn's own cards.
    var dueTotal = (stats.unified && stats.unified.total_due) || stats.due_today;
    if (dueTotal > 0) {
      chipEl.innerHTML = '<button class="review-chip" onclick="location.hash=\'#review\'">' +
        '🔁 Review queue <span class="pill">' + dueTotal + ' due</span>' +
        '</button>';
      chipEl.style.display = 'block';
    } else if (stats.total_cards > 0) {
      chipEl.innerHTML = '<button class="review-chip" style="background:var(--border);color:var(--muted)" disabled>' +
        '✓ All caught up · ' + stats.total_cards + ' cards' +
        '</button>';
      chipEl.style.display = 'block';
    } else {
      chipEl.style.display = 'none';
    }
  } catch (e) { /* SRS may not be ready */ }
  var grid = document.getElementById('catalog-grid');
  var empty = document.getElementById('catalog-empty');
  if (!STATE.catalog.length) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  grid.innerHTML = STATE.catalog.map(function(c) {
    var pct = c.lesson_count ? Math.round(100 * c.completed_count / c.lesson_count) : 0;
    var done = c.completed_at ? '✓ ' : '';
    return '<div class="course-card" data-id="' + escAttr(c.id) + '">' +
      '<div class="meta">' + (c.domain ? '<span>' + esc(c.domain) + '</span>' : '') + (c.level ? '<span>' + esc(c.level) + '</span>' : '') + (c.duration_min ? '<span>~' + c.duration_min + ' min</span>' : '') + verifyBadgeHtml(c.verify_status) + '</div>' +
      '<h3>' + done + esc(c.title) + '</h3>' +
      '<div class="desc">' + esc(c.description || '') + '</div>' +
      '<div class="progress-bar"><div style="width:' + pct + '%"></div></div>' +
      '<div class="progress-label"><span>' + c.completed_count + ' / ' + c.lesson_count + ' lessons</span><span>' + pct + '%</span></div>' +
      '<div class="card-actions" style="display:flex;justify-content:flex-end"><button class="edit-btn" onclick="event.stopPropagation();openWizard(\'' + escAttr(c.id) + '\')">✎ Edit</button></div>' +
      '</div>';
  }).join('');
  // Card click → open course (avoid Edit button propagation handled inline above).
  grid.querySelectorAll('.course-card').forEach(function(card) {
    card.onclick = function() { location.hash = '#course/' + encodeURIComponent(card.dataset.id); };
  });
}

// ─── Course detail ───────────────────────────────────────────
async function showCourse(id) {
  showView('course-view');
  var res = await fetch('/learn/api/courses/' + encodeURIComponent(id)).then(function(r) { return r.json(); });
  if (res.error) { document.getElementById('course-view').innerHTML = '<p>Error: ' + esc(res.error) + '</p>'; return; }
  STATE.course = res;
  var pct = res.lessons.length ? Math.round(100 * res.progress.completed_count / res.lessons.length) : 0;
  var resumeIdx = res.progress.last_opened || 0;
  var startBtn = res.progress.started_at
    ? '<button class="eos-btn eos-btn-primary" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/lesson/' + resumeIdx + '\'">Resume — Lesson ' + (resumeIdx+1) + '</button>'
    : '<button class="eos-btn eos-btn-primary" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/lesson/0\'">Start course</button>';
  var resetBtn = res.progress.started_at
    ? '<button class="eos-btn" onclick="resetCourse(\'' + id + '\')">Reset progress</button>'
    : '';
  var html =
    '<div class="back-link" onclick="location.hash=\'\'">← All courses</div>' +
    '<div class="course-header">' +
      '<div>' +
        '<h1>' + esc(res.title) + '</h1>' +
        '<div class="meta-row">' +
          (res.domain ? '<span>' + esc(res.domain) + '</span>' : '') +
          (res.level ? '<span>· ' + esc(res.level) + '</span>' : '') +
          (res.duration_min ? '<span>· ~' + res.duration_min + ' min</span>' : '') +
          '<span>· ' + res.lessons.length + ' lessons</span>' +
          (res.verify && res.verify.status ? '<span style="margin-left:6px">' + verifyBadgeHtml(res.verify.status, res.verify.error) + '</span>' : '') +
        '</div>' +
        '<p style="margin-top:14px;max-width:680px;line-height:1.5">' + esc(res.description) + '</p>' +
      '</div>' +
      '<div class="course-actions">' + startBtn + ' ' + resetBtn + ' <span id="diag-btn-slot"></span> <span id="video-queue-slot"></span></div>' +
    '</div>' +
    '<div class="progress-bar"><div style="width:' + pct + '%"></div></div>' +
    '<div class="progress-label" style="margin-top:6px"><span>' + res.progress.completed_count + ' / ' + res.lessons.length + ' lessons complete</span><span>' + pct + '%</span></div>' +
    '<h3 style="margin-top:24px;margin-bottom:8px">Syllabus</h3>' +
    '<div class="lesson-list">' +
      res.lessons.map(function(l) {
        return '<div class="lesson-row ' + (l.completed ? 'done' : '') + '" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/lesson/' + l.index + '\'">' +
          '<div class="check">' + (l.completed ? '✓' : '') + '</div>' +
          '<div class="lesson-meta"><div class="title">' + (l.index+1) + '. ' + esc(l.title) + '</div>' +
          (l.slug ? '<div class="sub">' + esc(l.slug) + '</div>' : '') + '</div>' +
          '<div class="kind-tag ' + escAttr(l.kind) + '">' + esc(l.kind) + '</div>' +
        '</div>';
      }).join('') +
    '</div>';
  document.getElementById('course-view').innerHTML = html;
  // Feature-detected diagnostic affordance (dark flag → endpoint returns
  // {enabled:false} and we render nothing).
  hydrateDiagnosticButton(id);
  if (res.video_enabled && window.LessonVideo) {
    LessonVideo.mountCourse(document.getElementById('video-queue-slot'), id);
  }
}

async function hydrateDiagnosticButton(id) {
  var slot = document.getElementById('diag-btn-slot');
  if (!slot) return;
  var st;
  try {
    st = await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/diagnostic').then(function(r) { return r.json(); });
  } catch (e) { return; }
  if (!st || !st.enabled || st.error) return;
  var label = st.taken_at ? '🎯 Re-take diagnostic' : '🎯 Take diagnostic';
  var tip = st.personalized ? ' · personalized' : '';
  slot.innerHTML = '<button class="eos-btn" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/diagnostic\'">' + label + '</button>' +
    (st.personalized ? '<span class="eos-badge eos-badge-status-pass" style="margin-left:6px" title="Syllabus reshaped around your diagnostic">personalized</span>' : '');
}

function resetCourse(id) {
  EOS_UI.confirm({
    message: 'Reset all progress for this course?',
    action: 'Reset', danger: true,
    onYes: async function() {
      await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/reset', { method: 'POST' });
      showCourse(id);
    },
  });
}

// ─── Lesson player ───────────────────────────────────────────
async function showLesson(id, idx) {
  showView('lesson-view');
  var res = await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/lessons/' + idx).then(function(r) { return r.json(); });
  if (res.error) { document.getElementById('lesson-view').innerHTML = '<p>Error: ' + esc(res.error) + '</p>'; return; }
  STATE.lesson = res;
  var course = await fetch('/learn/api/courses/' + encodeURIComponent(id)).then(function(r) { return r.json(); });
  var bodyHtml = res.source && res.source.body_md
    ? (window.marked ? EOS_UI.upgradeCallouts(marked.parse(res.source.body_md)) : '<pre>' + esc(res.source.body_md) + '</pre>')
    : '<p style="color:var(--muted)">No source content for this lesson.</p>';

  var prevBtn = (res.prev !== null) ? '<button class="eos-btn" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/lesson/' + res.prev + '\'">← Previous</button>' : '<button class="eos-btn" disabled>← Previous</button>';
  var nextBtn = (res.next !== null) ? '<button class="eos-btn eos-btn-primary" onclick="markCompleteAndNext()">Mark complete · Next →</button>' : '<button class="eos-btn eos-btn-primary" onclick="markCompleteAndFinish()">Mark complete · Finish</button>';
  var quizBtn = res.source && res.source.body_md ? '<button class="eos-btn" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/lesson/' + idx + '/quiz\'">📝 Quiz me on this</button>' : '';

  var miniList = (course.lessons || []).map(function(l) {
    var cls = 'mini-row' + (l.index === idx ? ' active' : '') + (l.completed ? ' done' : '');
    return '<div class="' + cls + '" style="cursor:pointer" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/lesson/' + l.index + '\'">' +
      (l.completed ? '✓' : '○') + ' ' + (l.index+1) + '. ' + esc(l.title) +
      '</div>';
  }).join('');

  document.getElementById('lesson-view').innerHTML =
    '<div class="back-link" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '\'">← ' + esc(course.title) + '</div>' +
    '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px">' +
      '<h1 style="margin:0;font-size:1.4rem">' + esc(res.title) + '</h1>' +
      '<span style="color:var(--muted);font-size:.85rem">Lesson ' + (idx+1) + ' of ' + res.total + '</span>' +
    '</div>' +
    '<div class="player-shell">' +
      '<div>' +
        '<div id="lesson-video-slot"></div>' +
        '<div class="player-content" id="lesson-body">' + bodyHtml + '</div>' +
        '<div class="player-nav">' + prevBtn + quizBtn + nextBtn + '</div>' +
      '</div>' +
      '<div class="player-sidebar">' +
        '<div class="sidebar-panel">' +
          '<h4>Lessons</h4>' +
          '<div class="lesson-list-mini">' + miniList + '</div>' +
        '</div>' +
        '<div class="sidebar-panel" id="reader-notes-panel" style="display:none">' +
          '<h4 id="reader-notes-header">📝 Reader notes</h4>' +
          '<div class="reader-notes-list" id="reader-notes-list"></div>' +
        '</div>' +
        (res.source && res.source.path ? '<div class="sidebar-panel"><h4>Source</h4><div style="font-size:.85rem"><a href="/kb/#' + encodeURIComponent(res.source.slug) + '" target="_blank">' + esc(res.source.slug) + '</a><div style="color:var(--muted);font-size:.75rem;margin-top:4px">' + esc(res.source.kind) + ' · ' + esc(res.source.path) + '</div></div></div>' : '') +
      '</div>' +
    '</div>';
  bindPdfAnchors(document.getElementById('lesson-body'));
  bindSelectionNoteButton(document.getElementById('lesson-body'), {
    slug: res.source && res.source.slug,
    course_id: id,
    lesson_index: idx,
  });
  loadReaderNotes(res.source && res.source.slug);
  // Lesson video (dark flag): the sibling lesson-video.js owns the whole panel.
  // Reading lessons only — the backend refuses a video for any other kind.
  if (res.video_enabled && res.kind === 'read' && res.slug && window.LessonVideo) {
    LessonVideo.mountLesson(document.getElementById('lesson-video-slot'), id, idx);
  }
  // If a reader-note click stashed a quote for this lesson, scroll to + flash it.
  var pendingQuote = sessionStorage.getItem('learn:scrollToQuote');
  if (pendingQuote) {
    sessionStorage.removeItem('learn:scrollToQuote');
    setTimeout(function() {
      EOS_UI.flashText(document.getElementById('lesson-body'), pendingQuote);
    }, 50);
  }
}

// ─── Reader notes sidebar ────────────────────────────────────
async function loadReaderNotes(slug) {
  if (!slug) return;
  var panel = document.getElementById('reader-notes-panel');
  var listEl = document.getElementById('reader-notes-list');
  var hdr = document.getElementById('reader-notes-header');
  try {
    var res = await fetch('/learn/api/lessons/notes/' + encodeURIComponent(slug)).then(function(r) { return r.json(); });
    var notes = res.notes || [];
    if (!notes.length) { panel.style.display = 'none'; return; }
    panel.style.display = '';
    hdr.textContent = '📝 Reader notes (' + notes.length + ')';
    listEl.innerHTML = notes.map(function(n, i) {
      if (n.raw) {
        return '<div class="reader-note-card"><div class="body">' + esc(n.raw) + '</div></div>';
      }
      var bits = [];
      if (n.quote) bits.push('<div class="quote" title="' + escAttr(n.quote) + '">' + esc(n.quote) + '</div>');
      if (n.note) bits.push('<div class="body">' + esc(n.note) + '</div>');
      var metaParts = [];
      if (n.date) metaParts.push(n.date);
      if (typeof n.lesson_index === 'number') metaParts.push('lesson ' + (n.lesson_index + 1));
      if (metaParts.length) bits.push('<div class="meta">' + esc(metaParts.join(' · ')) + '</div>');
      // Clickable when we have a course_id + lesson_index → jumps back to that lesson
      // and scrolls to the quoted text within the lesson body.
      var navTarget = '';
      if (n.course_id && typeof n.lesson_index === 'number') {
        navTarget = '#course/' + encodeURIComponent(n.course_id) + '/lesson/' + n.lesson_index;
      }
      var cls = 'reader-note-card' + (navTarget ? ' clickable' : '');
      var quoteAttr = n.quote ? ' data-quote="' + escAttr(n.quote) + '"' : '';
      var attrs = navTarget ? ' data-nav="' + escAttr(navTarget) + '" title="Jump to this quote in the lesson"' + quoteAttr : '';
      return '<div class="' + cls + '"' + attrs + '>' + bits.join('') + '</div>';
    }).join('');
    // Wire click handlers (delegated to each card with data-nav).
    listEl.querySelectorAll('.reader-note-card.clickable').forEach(function(card) {
      card.onclick = function() {
        var target = card.getAttribute('data-nav');
        var quote = card.getAttribute('data-quote') || '';
        if (quote) sessionStorage.setItem('learn:scrollToQuote', quote);
        if (target && location.hash === target) {
          // Already on this lesson — trigger highlight directly without nav.
          sessionStorage.removeItem('learn:scrollToQuote');
          setTimeout(function() {
            EOS_UI.flashText(document.getElementById('lesson-body'), quote);
          }, 10);
        } else if (target) {
          location.hash = target;
        }
      };
    });
  } catch (e) { panel.style.display = 'none'; }
}

// ─── Selection note pill ─────────────────────────────────────
var _notePill = null;
var _noteCtx = null;     // {slug, course_id, lesson_index}
var _noteSelection = ''; // captured text — selection is lost when modal opens

function bindSelectionNoteButton(root, ctx) {
  if (!root) return;
  _noteCtx = ctx || {};
  function hidePill() {
    if (_notePill) { _notePill.classList.add('fade'); setTimeout(function() {
      if (_notePill && _notePill.classList.contains('fade')) _notePill.remove(), _notePill = null;
    }, 150); }
  }
  function selectionInside(sel) {
    if (!sel || !sel.rangeCount) return false;
    var node = sel.getRangeAt(0).commonAncestorContainer;
    return root.contains(node) || root === node;
  }
  root.addEventListener('mouseup', function(e) {
    setTimeout(function() {
      var sel = window.getSelection();
      var txt = sel ? sel.toString().trim() : '';
      if (!txt || !selectionInside(sel)) { hidePill(); return; }
      _noteSelection = txt;
      var rect = sel.getRangeAt(0).getBoundingClientRect();
      if (!_notePill) {
        _notePill = document.createElement('button');
        _notePill.className = 'eos-note-pill';
        _notePill.innerHTML = '📝 Note';
        // mousedown.preventDefault() keeps the selection alive — without this,
        // clicking the pill collapses the selection → selectionchange fires →
        // hidePill() runs before click registers → pill un-clickable.
        _notePill.addEventListener('mousedown', function(ev) { ev.preventDefault(); });
        _notePill.addEventListener('click', function(ev) {
          ev.stopPropagation();
          ev.preventDefault();
          openNoteComposer();
        });
        document.body.appendChild(_notePill);
      } else {
        _notePill.classList.remove('fade');
      }
      // Position above the selection, accounting for scroll.
      var top = rect.top + window.scrollY - 38;
      var left = rect.left + window.scrollX + (rect.width / 2) - 30;
      if (top < window.scrollY + 4) top = rect.bottom + window.scrollY + 8;  // flip below if too close to top
      _notePill.style.top = top + 'px';
      _notePill.style.left = Math.max(8, left) + 'px';
    }, 10);  // wait a tick — selection finalises after mouseup
  });
  document.addEventListener('selectionchange', function() {
    var sel = window.getSelection();
    if (!sel || !sel.toString().trim()) hidePill();
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') hidePill();
  });
}

function openNoteComposer() {
  if (!_noteSelection || !_noteCtx.slug) return;
  var quoteCaptured = _noteSelection;
  var slugCaptured = _noteCtx.slug;
  var courseCaptured = _noteCtx.course_id;
  var idxCaptured = _noteCtx.lesson_index;
  EOS_UI.formModal({
    title: '📝 Add reader note',
    fields: [
      { key: 'quote', label: 'Quoted text', type: 'textarea', value: quoteCaptured, autofocus: false },
      { key: 'note', label: 'Your note', type: 'textarea', autofocus: true,
        placeholder: "What's the insight, question, or doubt?" },
    ],
    onSubmit: async function(values) {
      // Use the captured quote, not the form value — the field is read-only by design.
      values.quote = quoteCaptured;
      var body = {
        slug: slugCaptured,
        quote: quoteCaptured,
        note: values.note || '',
        course_id: courseCaptured,
        lesson_index: idxCaptured,
      };
      try {
        var res = await fetch('/learn/api/lessons/note', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }).then(function(r) { return r.json(); });
        if (res.error) {
          EOS_UI.toast('Note save failed: ' + res.error, false);
          return;
        }
        EOS_UI.toast('Note saved', true);
        if (_notePill) { _notePill.remove(); _notePill = null; }
        loadReaderNotes(slugCaptured);
      } catch (e) {
        EOS_UI.toast('Note save failed: ' + e.message, false);
      }
    },
  });
  // Make the quote textarea read-only — formModal doesn't support the flag natively.
  setTimeout(function() {
    var quoteEl = document.getElementById('eos-form-quote');
    if (quoteEl) {
      quoteEl.readOnly = true;
      quoteEl.style.opacity = '0.7';
      quoteEl.style.cursor = 'default';
      quoteEl.style.fontStyle = 'italic';
      quoteEl.rows = 3;
    }
    var noteEl = document.getElementById('eos-form-note');
    if (noteEl) { noteEl.rows = 4; noteEl.focus(); }
  }, 0);
}

// ─── Course authoring wizard ─────────────────────────────────
var WIZARD = null;  // {step, course_id|null, fields, lessons, kbNotes, picker:{kind, q}}

async function openWizard(courseId) {
  // Initialize state — courseId truthy → edit mode, else create.
  WIZARD = {
    step: 1,
    course_id: courseId || null,
    fields: { title: '', description: '', level: 'intermediate', domain: '', topic: '', duration_min: 30 },
    lessons: [],
    kbNotes: [],
    picker: { kind: '', q: '' },
    dirty: false,
  };
  if (courseId) {
    try {
      var existing = await fetch('/learn/api/courses/' + encodeURIComponent(courseId)).then(function(r) { return r.json(); });
      if (!existing.error) {
        WIZARD.fields = {
          title: existing.title || '',
          description: existing.description || '',
          level: existing.level || 'intermediate',
          domain: existing.domain || '',
          topic: existing.topic || '',
          duration_min: existing.duration_min || 30,
        };
        WIZARD.lessons = (existing.lessons || []).map(function(l) {
          return { slug: l.slug, title: l.title, kind: l.kind || 'read', duration_min: l.duration_min || 0 };
        });
      }
    } catch (e) { /* fall through with empty fields */ }
  }
  EOS_UI.modal({
    title: courseId ? '✎ Edit course' : '+ New course',
    body: renderWizard(),
    width: '800px',
  });
  setTimeout(bindWizardStep1, 0);
}

function renderWizard() {
  return '<div class="wizard-shell">' +
    '<div class="wizard-steps">' +
      '<span class="step ' + (WIZARD.step === 1 ? 'active' : '') + '" id="ws-1">1. Details</span>' +
      '<span class="step ' + (WIZARD.step === 2 ? 'active' : '') + '" id="ws-2">2. Lessons</span>' +
    '</div>' +
    (WIZARD.step === 1 ? renderWizardStep1() : renderWizardStep2()) +
    '</div>';
}

function renderWizardStep1() {
  var f = WIZARD.fields;
  return '<div id="wizard-step1">' +
    '<div class="field"><label>Title</label><input id="wiz-title" value="' + escAttr(f.title) + '" placeholder="e.g. AS/NZS 60364 — Earthing Foundations" autofocus></div>' +
    '<div class="field"><label>Description</label><textarea id="wiz-description" rows="3" placeholder="One-paragraph hook explaining what learners get out of this course">' + esc(f.description) + '</textarea></div>' +
    '<div class="field-row">' +
      '<div class="field"><label>Domain</label><input id="wiz-domain" value="' + escAttr(f.domain) + '" placeholder="electrical-engineering"></div>' +
      '<div class="field"><label>Topic</label><input id="wiz-topic" value="' + escAttr(f.topic) + '" placeholder="lv-switchgear"></div>' +
    '</div>' +
    '<div class="field-row">' +
      '<div class="field"><label>Level</label><select id="wiz-level"><option value="beginner"' + (f.level === 'beginner' ? ' selected' : '') + '>Beginner</option><option value="intermediate"' + (f.level === 'intermediate' ? ' selected' : '') + '>Intermediate</option><option value="advanced"' + (f.level === 'advanced' ? ' selected' : '') + '>Advanced</option></select></div>' +
      '<div class="field"><label>Duration (min)</label><input id="wiz-duration" type="number" min="1" value="' + (f.duration_min || 30) + '"></div>' +
    '</div>' +
    '<div class="wizard-actions"><button class="eos-btn" onclick="cancelWizard()">Cancel</button><div class="spacer"></div><button class="eos-btn eos-btn-primary" onclick="wizardNext()">Next →</button></div>' +
    '</div>';
}

function renderWizardStep2() {
  return '<div id="wizard-step2">' +
    '<div class="wizard-split">' +
      '<div class="wizard-panel">' +
        '<div class="wizard-panel-header">KB notes</div>' +
        '<div class="picker-filters" id="picker-filters"></div>' +
        '<div class="picker-search"><input id="picker-search" type="search" placeholder="Search title or slug…"></div>' +
        '<div class="wizard-panel-body" id="picker-list"><div class="picker-empty">Loading…</div></div>' +
      '</div>' +
      '<div class="wizard-panel">' +
        '<div class="wizard-panel-header">Lessons (drag to reorder)<span id="lesson-count" style="font-weight:400;color:var(--muted);text-transform:none;letter-spacing:0">' + WIZARD.lessons.length + '</span></div>' +
        '<div class="wizard-panel-body" id="lessons-list"></div>' +
      '</div>' +
    '</div>' +
    '<div class="wizard-actions"><button class="eos-btn" onclick="wizardBack()">← Back</button><button class="eos-btn" onclick="cancelWizard()">Cancel</button><div class="spacer"></div><button class="eos-btn eos-btn-primary" onclick="saveCourse()">Save course</button></div>' +
    '</div>';
}

function bindWizardStep1() {
  var t = document.getElementById('wiz-title');
  if (t) { t.focus(); t.oninput = function() { WIZARD.dirty = true; }; }
}

function wizardNext() {
  // Capture step-1 fields.
  WIZARD.fields.title = (document.getElementById('wiz-title') || {}).value || '';
  WIZARD.fields.description = (document.getElementById('wiz-description') || {}).value || '';
  WIZARD.fields.domain = (document.getElementById('wiz-domain') || {}).value || '';
  WIZARD.fields.topic = (document.getElementById('wiz-topic') || {}).value || '';
  WIZARD.fields.level = (document.getElementById('wiz-level') || {}).value || 'intermediate';
  WIZARD.fields.duration_min = parseInt((document.getElementById('wiz-duration') || {}).value || '30', 10);
  if (!WIZARD.fields.title.trim()) { EOS_UI.toast('Title is required.', false); return; }
  WIZARD.dirty = true;
  WIZARD.step = 2;
  // Re-render modal body.
  var body = document.querySelector('.eos-modal-body') || document.querySelector('.eos-modal .body');
  if (body) body.innerHTML = renderWizard();
  loadKbNotes();
  renderLessonList();
  setTimeout(bindStep2Handlers, 0);
}

function wizardBack() {
  WIZARD.step = 1;
  var body = document.querySelector('.eos-modal-body') || document.querySelector('.eos-modal .body');
  if (body) body.innerHTML = renderWizard();
  setTimeout(bindWizardStep1, 0);
}

function cancelWizard() {
  if (WIZARD && WIZARD.dirty) {
    EOS_UI.confirm({
      message: 'Discard unsaved changes?',
      action: 'Discard', danger: true,
      onYes: function() { EOS_UI.closeModal(); WIZARD = null; },
    });
    return;
  }
  EOS_UI.closeModal();
  WIZARD = null;
}

// ─── Step 2 — KB picker + lesson list ──────────────────────
async function loadKbNotes() {
  var p = WIZARD.picker;
  var qs = new URLSearchParams();
  if (p.kind) qs.set('kind', p.kind);
  if (p.q) qs.set('q', p.q);
  try {
    var res = await fetch('/learn/api/authoring/kb-notes?' + qs.toString()).then(function(r) { return r.json(); });
    WIZARD.kbNotes = res.notes || [];
  } catch (e) { WIZARD.kbNotes = []; }
  renderPickerList();
  renderPickerFilters();
}

function renderPickerFilters() {
  var kinds = ['', 'concept', 'clause', 'case', 'reference', 'lesson', 'formula'];
  var html = kinds.map(function(k) {
    var label = k || 'all';
    var active = WIZARD.picker.kind === k ? ' active' : '';
    return '<span class="picker-chip' + active + '" data-kind="' + k + '">' + esc(label) + '</span>';
  }).join('');
  var el = document.getElementById('picker-filters');
  if (!el) return;
  el.innerHTML = html;
  el.querySelectorAll('.picker-chip').forEach(function(c) {
    c.onclick = function() { WIZARD.picker.kind = c.dataset.kind; loadKbNotes(); };
  });
}

function renderPickerList() {
  var addedSlugs = new Set(WIZARD.lessons.map(function(l) { return l.slug; }));
  var notes = WIZARD.kbNotes;
  var html = notes.length
    ? notes.map(function(n) {
        var added = addedSlugs.has(n.slug);
        return '<div class="picker-row' + (added ? ' added' : '') + '" data-slug="' + escAttr(n.slug) + '">' +
          '<span class="kind-tag">' + esc(n.kind) + '</span>' +
          '<span class="title">' + esc(n.title) + '</span>' +
          (added ? '<span class="check">✓</span>' : '') +
          '</div>';
      }).join('')
    : '<div class="picker-empty">No KB notes match. Adjust filters.</div>';
  var el = document.getElementById('picker-list');
  if (!el) return;
  el.innerHTML = html;
  el.querySelectorAll('.picker-row:not(.added)').forEach(function(row) {
    row.onclick = function() { addLesson(row.dataset.slug); };
  });
}

function bindStep2Handlers() {
  var search = document.getElementById('picker-search');
  if (search) {
    var t = null;
    search.addEventListener('input', function() {
      clearTimeout(t);
      t = setTimeout(function() { WIZARD.picker.q = search.value; loadKbNotes(); }, 200);
    });
  }
}

function addLesson(slug) {
  var note = WIZARD.kbNotes.find(function(n) { return n.slug === slug; });
  if (!note) return;
  WIZARD.lessons.push({
    slug: slug,
    title: note.title,
    kind: 'read',
    duration_min: 5,
  });
  WIZARD.dirty = true;
  renderLessonList();
  renderPickerList();  // refresh greyed-out state
}

function removeLesson(idx) {
  WIZARD.lessons.splice(idx, 1);
  WIZARD.dirty = true;
  renderLessonList();
  renderPickerList();
}

function addQuizAfter(idx) {
  var src = WIZARD.lessons[idx];
  if (!src) return;
  WIZARD.lessons.splice(idx + 1, 0, {
    slug: src.slug,
    title: 'Self-check: ' + src.title,
    kind: 'quiz',
    duration_min: 3,
  });
  WIZARD.dirty = true;
  renderLessonList();
}

function renderLessonList() {
  var ls = WIZARD.lessons;
  var countEl = document.getElementById('lesson-count');
  if (countEl) countEl.textContent = ' · ' + ls.length;
  var el = document.getElementById('lessons-list');
  if (!el) return;
  if (!ls.length) {
    el.innerHTML = '<div class="lessons-empty">Pick KB notes from the left to add lessons.</div>';
    return;
  }
  el.innerHTML = ls.map(function(l, i) {
    var kinds = ['read', 'quiz', 'case'];
    var opts = kinds.map(function(k) {
      return '<option value="' + k + '"' + (l.kind === k ? ' selected' : '') + '>' + k + '</option>';
    }).join('');
    return '<div class="lesson-row-edit" draggable="true" data-idx="' + i + '">' +
      '<span class="handle" title="Drag to reorder">⋮⋮</span>' +
      '<span class="idx">' + (i + 1) + '</span>' +
      '<input class="title-input" data-idx="' + i + '" value="' + escAttr(l.title) + '">' +
      '<select class="kind-select" data-idx="' + i + '">' + opts + '</select>' +
      '<input class="dur-input" data-idx="' + i + '" type="number" min="0" value="' + (l.duration_min || 0) + '" title="Duration (min)">' +
      '<div class="row-actions">' +
        '<button class="icon-btn" onclick="addQuizAfter(' + i + ')" title="Add quiz after this">＋?</button>' +
        '<button class="icon-btn danger" onclick="removeLesson(' + i + ')" title="Remove">✕</button>' +
      '</div>' +
      '</div>';
  }).join('');
  // Wire input change handlers.
  el.querySelectorAll('.title-input').forEach(function(inp) {
    inp.oninput = function() { WIZARD.lessons[+inp.dataset.idx].title = inp.value; WIZARD.dirty = true; };
  });
  el.querySelectorAll('.kind-select').forEach(function(sel) {
    sel.onchange = function() { WIZARD.lessons[+sel.dataset.idx].kind = sel.value; WIZARD.dirty = true; };
  });
  el.querySelectorAll('.dur-input').forEach(function(inp) {
    inp.onchange = function() { WIZARD.lessons[+inp.dataset.idx].duration_min = parseInt(inp.value || '0', 10); WIZARD.dirty = true; };
  });
  // Wire drag-and-drop.
  bindLessonDragDrop(el);
}

var _dragSrcIdx = null;
function bindLessonDragDrop(root) {
  root.querySelectorAll('.lesson-row-edit').forEach(function(row) {
    row.ondragstart = function(e) {
      _dragSrcIdx = parseInt(row.dataset.idx, 10);
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(_dragSrcIdx));
    };
    row.ondragend = function() {
      row.classList.remove('dragging');
      root.querySelectorAll('.lesson-row-edit').forEach(function(r) {
        r.classList.remove('drop-above', 'drop-below');
      });
    };
    row.ondragover = function(e) {
      e.preventDefault();
      var rect = row.getBoundingClientRect();
      var below = (e.clientY - rect.top) > rect.height / 2;
      row.classList.toggle('drop-above', !below);
      row.classList.toggle('drop-below', below);
    };
    row.ondragleave = function() { row.classList.remove('drop-above', 'drop-below'); };
    row.ondrop = function(e) {
      e.preventDefault();
      var dstIdx = parseInt(row.dataset.idx, 10);
      if (_dragSrcIdx === null || _dragSrcIdx === dstIdx) return;
      var rect = row.getBoundingClientRect();
      var below = (e.clientY - rect.top) > rect.height / 2;
      var insertAt = below ? dstIdx + 1 : dstIdx;
      if (insertAt > _dragSrcIdx) insertAt--;  // account for removal shift
      var item = WIZARD.lessons.splice(_dragSrcIdx, 1)[0];
      WIZARD.lessons.splice(insertAt, 0, item);
      WIZARD.dirty = true;
      _dragSrcIdx = null;
      renderLessonList();
    };
  });
}

async function saveCourse() {
  if (!WIZARD || !WIZARD.lessons.length) {
    EOS_UI.toast('Add at least one lesson before saving.', false);
    return;
  }
  var payload = Object.assign({}, WIZARD.fields, {
    course_id: WIZARD.course_id || '',
    lessons: WIZARD.lessons,
  });
  try {
    var res = await fetch('/learn/api/courses/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function(r) { return r.json(); });
    if (res.error) {
      EOS_UI.toast('Save failed: ' + res.error, false);
      return;
    }
    EOS_UI.toast(res.is_update ? 'Course updated' : 'Course created', true);
    EOS_UI.closeModal();
    WIZARD = null;
    showCatalog();  // refresh
  } catch (e) {
    EOS_UI.toast('Save failed: ' + e.message, false);
  }
}

// Bind PDF citation buttons inside a rendered lesson body to EOS_PDF.open()
function bindPdfAnchors(root) {
  if (!root || !window.EOS_PDF) return;
  var btns = root.querySelectorAll('button.eos-pdf-anchor');
  btns.forEach(function(b) {
    b.onclick = function(e) {
      e.preventDefault();
      EOS_PDF.open({
        path: b.getAttribute('data-pdf-path'),
        page: parseInt(b.getAttribute('data-pdf-page'), 10) || 1,
        title: b.getAttribute('data-pdf-title') || '',
        serveBase: '/learn/api/pdf',
      });
    };
  });
}

async function markCompleteAndNext() {
  if (!STATE.lesson) return;
  var id = STATE.lesson.course_id, idx = STATE.lesson.index;
  await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/lessons/' + idx + '/complete', { method: 'POST' });
  if (STATE.lesson.next !== null) {
    location.hash = '#course/' + encodeURIComponent(id) + '/lesson/' + STATE.lesson.next;
  } else {
    location.hash = '#course/' + encodeURIComponent(id);
  }
}
async function markCompleteAndFinish() {
  if (!STATE.lesson) return;
  var id = STATE.lesson.course_id, idx = STATE.lesson.index;
  await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/lessons/' + idx + '/complete', { method: 'POST' });
  if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast('Course complete!', 'success');
  location.hash = '#course/' + encodeURIComponent(id);
}

// ─── Quiz ────────────────────────────────────────────────────
async function showQuiz(id, idx) {
  showView('quiz-view');
  document.getElementById('quiz-view').innerHTML = '<div class="quiz-shell"><p style="text-align:center;color:var(--muted)">Generating quiz…</p></div>';
  var res = await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/lessons/' + idx + '/quiz', { method: 'POST' }).then(function(r) { return r.json(); });
  if (res.error) {
    // A spent monthly AI limit is not a failure — show its own message.
    var lead = (res.limit_reached || res.needs_opt_in) ? '' : 'Quiz generation failed: ';
    document.getElementById('quiz-view').innerHTML = '<div class="quiz-shell"><p>' + lead + esc(res.error) + '</p></div>';
    return;
  }
  STATE.quiz = res;
  renderQuiz(id, idx, res, {}, false);
}

function renderQuiz(id, idx, quiz, answers, graded, gradeResult) {
  var html = '<div class="quiz-shell">' +
    '<div class="back-link" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/lesson/' + idx + '\'">← Back to lesson</div>' +
    '<h2 style="margin:0 0 6px 0">Self-check</h2>' +
    '<p style="color:var(--muted);margin:0 0 20px 0">' + esc(quiz.title) + '</p>';
  if (graded && gradeResult) {
    html += '<div class="quiz-score">' + gradeResult.score + '%</div>' +
            '<p style="text-align:center;color:var(--muted);margin-bottom:24px">' + gradeResult.correct + ' of ' + gradeResult.total + ' correct</p>';
  }
  quiz.questions.forEach(function(q, i) {
    html += '<div class="quiz-question">' +
      '<div class="q-text">' + (i+1) + '. ' + esc(q.q) + '</div>';
    Object.keys(q.options).forEach(function(opt) {
      var picked = answers[i] === opt;
      var cls = 'quiz-option';
      if (graded && gradeResult) {
        var detail = gradeResult.details[i];
        if (opt === detail.correct_answer) cls += ' correct';
        else if (picked && !detail.ok) cls += ' wrong';
      }
      // `opt` is a key from model output — escape it, and let the handler read
      // the value back off the input rather than interpolating it into JS.
      html += '<label class="' + cls + '">' +
        '<input type="radio" name="q' + i + '" value="' + escAttr(opt) + '" ' + (picked ? 'checked' : '') + (graded ? ' disabled' : '') + ' onchange="QUIZ_ANSWERS[' + i + ']=this.value">' +
        '<strong>' + esc(opt) + '.</strong> ' + esc(q.options[opt]) +
      '</label>';
    });
    if (graded && gradeResult) {
      html += '<div class="quiz-explanation">' + esc(gradeResult.details[i].explanation || q.explanation || '') + '</div>';
    }
    html += '</div>';
  });
  if (!graded) {
    html += '<button class="eos-btn eos-btn-primary" style="width:100%;padding:12px" onclick="submitQuiz(\'' + id + '\',' + idx + ')">Submit answers</button>';
  } else {
    html += '<div style="display:flex;gap:8px;margin-top:16px">' +
      '<button class="eos-btn" style="flex:1" onclick="showQuiz(\'' + id + '\',' + idx + ')">Try again</button>' +
      '<button class="eos-btn eos-btn-primary" style="flex:1" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '/lesson/' + idx + '\'">Back to lesson</button>' +
    '</div>';
  }
  html += '</div>';
  document.getElementById('quiz-view').innerHTML = html;
}

window.QUIZ_ANSWERS = {};
async function submitQuiz(id, idx) {
  if (!STATE.quiz) return;
  var body = { answers: window.QUIZ_ANSWERS, questions: STATE.quiz.questions };
  var grade = await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/lessons/' + idx + '/quiz/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(function(r) { return r.json(); });
  if (grade.error) { EOS_UI.toast('Submit failed: ' + grade.error, false); return; }
  renderQuiz(id, idx, STATE.quiz, window.QUIZ_ANSWERS, true, grade);
  window.QUIZ_ANSWERS = {};
}

// ─── Diagnostic (placement test → personalized plan) ─────────
window.DIAG_ANSWERS = {};
window.DIAG = null;

async function showDiagnostic(id) {
  showView('diagnostic-view');
  var el = document.getElementById('diagnostic-view');
  el.innerHTML = '<div class="quiz-shell"><div class="back-link" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '\'">← Back to course</div>' +
    '<p style="text-align:center;color:var(--muted);padding:30px">Building your diagnostic across this course\'s concepts…</p></div>';
  var res = await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/diagnostic', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(function(r) { return r.json(); });
  if (res.error) {
    el.innerHTML = '<div class="quiz-shell"><div class="back-link" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '\'">← Back to course</div><p>Diagnostic unavailable: ' + esc(res.error) + '</p></div>';
    return;
  }
  window.DIAG = res;
  window.DIAG_ANSWERS = {};
  renderDiagnostic(id, res, {}, false, null);
}

function renderDiagnostic(id, diag, answers, graded, result) {
  var html = '<div class="quiz-shell">' +
    '<div class="back-link" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '\'">← Back to course</div>' +
    '<h2 style="margin:0 0 6px 0">🎯 Diagnostic</h2>' +
    '<p style="color:var(--muted);margin:0 0 20px 0">' + diag.question_count + ' questions across ' + diag.concept_count + ' concepts — find what to skip and what to focus on.</p>';
  if (graded && result) {
    var s = result.summary || {};
    html += '<div class="diag-summary" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px">' +
      diagChip('Weak', (s.weak||[]).length, 'eos-badge-status-fail') +
      diagChip('Developing', (s.developing||[]).length, 'eos-badge-status-running') +
      diagChip('Mastered', (s.mastered||[]).length, 'eos-badge-status-pass') +
      '</div>' +
      '<p style="color:var(--muted);margin:0 0 18px 0">Proposed plan: <strong>' + result.plan_lesson_count + '</strong> lessons (was ' + result.original_lesson_count + ') — mastered concepts dropped, weak ones first.</p>';
  }
  diag.questions.forEach(function(q, i) {
    html += '<div class="quiz-question">' +
      '<div class="q-text"><span style="color:var(--muted);font-size:.8em">' + esc(q.concept || q.slug) + '</span><br>' + (i+1) + '. ' + esc(q.q) + '</div>';
    Object.keys(q.options).forEach(function(opt) {
      var picked = answers[i] === opt;
      var cls = 'quiz-option';
      if (graded && result) {
        var detail = result.details[i];
        if (detail && opt === detail.correct_answer) cls += ' correct';
        else if (picked && detail && !detail.ok) cls += ' wrong';
      }
      html += '<label class="' + cls + '">' +
        '<input type="radio" name="dq' + i + '" value="' + opt + '" ' + (picked ? 'checked' : '') + (graded ? ' disabled' : '') + ' onchange="DIAG_ANSWERS[' + i + ']=\'' + opt + '\'">' +
        '<strong>' + opt + '.</strong> ' + esc(q.options[opt]) +
      '</label>';
    });
    if (graded && result && result.details[i]) {
      html += '<div class="quiz-explanation">' + esc(result.details[i].explanation || q.explanation || '') + '</div>';
    }
    html += '</div>';
  });
  if (!graded) {
    html += '<button class="eos-btn eos-btn-primary" style="width:100%;padding:12px" onclick="submitDiagnostic(\'' + id + '\')">Score my diagnostic</button>';
  } else {
    var applyBtn = result.plan_lesson_count > 0
      ? '<button class="eos-btn eos-btn-primary" style="flex:1" onclick="applyDiagnostic(\'' + id + '\')">Personalize this course</button>'
      : '';
    html += '<div style="display:flex;gap:8px;margin-top:16px">' +
      '<button class="eos-btn" style="flex:1" onclick="location.hash=\'#course/' + encodeURIComponent(id) + '\'">Keep as is</button>' +
      applyBtn +
    '</div>';
  }
  html += '</div>';
  document.getElementById('diagnostic-view').innerHTML = html;
}

function diagChip(label, n, cls) {
  return '<span class="eos-badge ' + cls + '">' + label + ': ' + n + '</span>';
}

async function submitDiagnostic(id) {
  if (!window.DIAG) return;
  var body = { answers: window.DIAG_ANSWERS, questions: window.DIAG.questions };
  var res = await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/diagnostic/submit', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(function(r) { return r.json(); });
  if (res.error) { EOS_UI.toast('Scoring failed: ' + res.error, false); return; }
  renderDiagnostic(id, window.DIAG, window.DIAG_ANSWERS, true, res);
}

function applyDiagnostic(id) {
  EOS_UI.confirm({
    message: 'Reshape this course around your diagnostic? Mastered concepts are dropped, weak ones move first. Progress resets since the lesson order changes.',
    action: 'Personalize',
    onYes: async function() {
      var res = await fetch('/learn/api/courses/' + encodeURIComponent(id) + '/diagnostic/apply', { method: 'POST' }).then(function(r) { return r.json(); });
      if (res.error) { EOS_UI.toast('Apply failed: ' + res.error, false); return; }
      EOS_UI.toast('Course personalized — ' + res.lesson_count + ' lessons', true);
      location.hash = '#course/' + encodeURIComponent(id);
    },
  });
}

// ─── Review mode — unified queue across every SRS silo ───────
// One session over learn (KB quizzes), dictionary (words) and media
// (highlights/flashcards). Two interaction styles: MCQ for `quiz` cards,
// front→reveal→rate for everything else. When the optional apps are absent
// the payload is learn-only and this behaves exactly as it always did.
window.REVIEW_ANSWERS = {};

var SOURCE_LABELS = {
  learn: { icon: '📚', name: 'Learn' },
  dictionary: { icon: '📖', name: 'Words' },
  media: { icon: '✨', name: 'Highlights' },
};

async function showReview() {
  showView('review-view');
  var el = document.getElementById('review-view');
  el.innerHTML = '<div class="review-shell"><p style="text-align:center;color:var(--muted);padding:48px">Loading review queue…</p></div>';

  var stats = await fetch('/learn/api/review/stats').then(function(r) { return r.json(); });
  var queue = await fetch('/learn/api/review/all?limit=30').then(function(r) { return r.json(); });

  STATE.review = {
    queue: queue.cards || [],
    idx: 0,
    counts: queue.counts || {},
    due: queue.total_due || 0,
    done: 0,
    stats: stats,
  };

  if (!STATE.review.queue.length) {
    el.innerHTML = renderReviewHeader() +
      '<div class="review-shell"><div class="review-empty">' +
      '<div class="icon">✓</div>' +
      '<h2 style="margin:0 0 8px 0">All caught up</h2>' +
      '<p>No cards due right now. Come back tomorrow — SRS will resurface what needs review.</p>' +
      '<button class="eos-btn" style="margin-top:20px" onclick="location.hash=\'\'">Back to catalog</button>' +
      '</div></div>';
    return;
  }
  renderUnifiedCard();
}

function renderReviewHeader() {
  var R = STATE.review || { due: 0, done: 0, counts: {} };
  var chips = Object.keys(SOURCE_LABELS).filter(function(k) {
    return (R.counts[k] || 0) > 0;
  }).map(function(k) {
    var s = SOURCE_LABELS[k];
    return '<span>' + s.icon + ' ' + s.name + ' <strong>' + R.counts[k] + '</strong></span>';
  }).join('');
  return '<div class="review-shell">' +
    '<div class="back-link" onclick="location.hash=\'\'">← Back to catalog</div>' +
    '<div class="review-header">' +
      '<h2>🔁 Review</h2>' +
      '<div class="stats">' +
        '<span><strong>' + Math.max(0, R.due - R.done) + '</strong> due</span>' +
        '<span><strong>' + R.done + '</strong> done this session</span>' +
      '</div>' +
    '</div>' +
    (chips ? '<div class="review-header"><div class="source-chips">' + chips + '</div></div>' : '') +
    '</div>';
}

function reviewSourceMeta(card, extra) {
  var s = SOURCE_LABELS[card.app] || { icon: '•', name: card.app };
  var R = STATE.review;
  var remaining = R.queue.length - R.idx;
  return '<div class="review-source-meta">' +
    s.icon + ' <strong>' + esc(s.name) + '</strong>' +
    (card.meta && card.meta.kind ? ' · ' + esc(card.meta.kind) : '') +
    (card.meta && card.meta.source_title ? ' · ' + esc(card.meta.source_title) : '') +
    (extra ? ' · ' + extra : '') +
    ' · ' + remaining + ' card' + (remaining === 1 ? '' : 's') + ' remaining' +
    '</div>';
}

// Shell for a non-quiz card: header + meta + body, wrapped in .review-shell.
function reviewShell(card, bodyHtml, extra) {
  var html = renderReviewHeader().replace(/<\/div>$/, '');
  html += reviewSourceMeta(card, extra) + bodyHtml + '</div>';
  document.getElementById('review-view').innerHTML = html;
}

function renderUnifiedCard() {
  var R = STATE.review;
  if (R.idx >= R.queue.length) { renderReviewDone(); return; }
  var card = R.queue[R.idx];
  if (card.kind === 'quiz') { renderQuizCard(card); }
  else { renderRevealCard(card, false); }
}

function renderReviewDone() {
  var R = STATE.review;
  document.getElementById('review-view').innerHTML =
    renderReviewHeader().replace(/<\/div>$/, '') +
    '<div class="review-empty">' +
      '<div class="icon">✓</div>' +
      '<h2 style="margin:0 0 8px 0">Session done</h2>' +
      '<p>' + R.done + ' card' + (R.done === 1 ? '' : 's') + ' reviewed.</p>' +
      '<button class="eos-btn" style="margin-top:20px" onclick="location.hash=\'\'">Back to catalog</button>' +
    '</div></div>';
}

// Move to the next card without counting it as reviewed (skip, or a card
// whose grade was already recorded by submitReview).
function nextCard() {
  STATE.review.idx++;
  renderUnifiedCard();
}

// Graded → count it, then move on.
function advanceReview() {
  STATE.review.done++;
  nextCard();
}

// ── Reveal-style cards (dictionary words, media highlights/flashcards) ──

function renderRevealCard(card, revealed) {
  var m = card.meta || {};
  // A picture card is answered FROM the photograph, so the image is the prompt
  // and the name must stay hidden until reveal — printing card.front here would
  // put the answer right next to the question.
  var isPicture = card.kind === 'picture' && m.image;
  var body = '';
  if (isPicture) {
    body += '<img class="card-image" src="' + escAttr(m.image) + '" alt="" loading="lazy">';
  }
  var front = '<div class="card-front">' + esc(card.front) +
    (m.phonetic ? '<span class="phonetic">' + esc(m.phonetic) + '</span>' : '') + '</div>';
  if (!isPicture || revealed) body += front;
  if (!revealed) {
    body += '<button class="eos-btn eos-btn-primary" style="width:100%;padding:12px" onclick="revealReviewCard()">Show answer</button>';
  } else {
    var back = card.back || '';
    if (m.chinese) back = back ? back + '\n\n' + m.chinese : m.chinese;
    body += '<div class="card-back">' + (back ? esc(back) : '<span class="empty">No note — rate from memory.</span>') + '</div>' +
      '<div class="rating-row">' +
        '<button class="eos-btn rating-again" onclick="gradeReviewCard(\'again\')">Again</button>' +
        '<button class="eos-btn" onclick="gradeReviewCard(\'hard\')">Hard</button>' +
        '<button class="eos-btn eos-btn-primary" onclick="gradeReviewCard(\'good\')">Good</button>' +
        '<button class="eos-btn rating-easy" onclick="gradeReviewCard(\'easy\')">Easy</button>' +
      '</div>';
  }
  reviewShell(card, body, m.level !== undefined ? 'level ' + m.level : '');
}

function revealReviewCard() {
  var R = STATE.review;
  renderRevealCard(R.queue[R.idx], true);
}

async function gradeReviewCard(rating) {
  var R = STATE.review;
  var card = R.queue[R.idx];
  // Double-grade guard: the buttons stay disabled until we advance.
  var buttons = document.querySelectorAll('.rating-row button');
  for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = true; }

  var res = await fetch('/learn/api/review/grade-item', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app: card.app, id: card.id, rating: rating }),
  }).then(function(r) { return r.json(); }).catch(function(e) { return { error: String(e) }; });

  // A card that vanished between listing and grading shouldn't strand the queue.
  if (res.error) { EOS_UI.toast('Card unavailable — skipped', false); }
  advanceReview();
}

// ── Quiz cards (learn) — MCQ generated lazily for this specific slug ──

async function renderQuizCard(card) {
  reviewShell(card, '<p style="text-align:center;color:var(--muted);padding:48px">Generating questions…</p>');
  var quiz = await fetch('/learn/api/review/quiz/' + encodeURIComponent(card.id))
    .then(function(r) { return r.json(); })
    .catch(function(e) { return { error: String(e) }; });

  if (quiz.error) {
    // A spent monthly AI limit is not a failure: plain text, not the danger colour.
    var quizNote = (quiz.limit_reached || quiz.needs_opt_in)
      ? '<p style="padding:20px">' + esc(quiz.error) + '</p>'
      : '<p style="color:var(--danger);padding:20px">Couldn\'t build a quiz for this card: ' + esc(quiz.error) + '</p>';
    reviewShell(card,
      quizNote +
      '<div style="display:flex;gap:8px">' +
        '<button class="eos-btn" style="flex:1" onclick="nextCard()">Skip</button>' +
        '<button class="eos-btn eos-btn-primary" style="flex:1" onclick="renderUnifiedCard()">Try again</button>' +
      '</div>');
    return;
  }
  STATE.review.quiz = quiz;
  window.REVIEW_ANSWERS = {};
  renderReviewCard(card, quiz, {}, false, null);
}

function renderReviewCard(card, quiz, answers, graded, gradeResult) {
  var html = renderReviewHeader().replace(/<\/div>$/, '');
  html += reviewSourceMeta(card, esc(quiz.title || card.front) + (quiz.cached ? '' : ' · <em>fresh</em>'));
  (quiz.questions || []).forEach(function(q, i) {
    html += '<div class="quiz-question">' +
      '<div class="q-text">' + (i+1) + '. ' + esc(q.q) + '</div>';
    Object.keys(q.options).forEach(function(opt) {
      var picked = answers[i] === opt;
      var cls = 'quiz-option';
      if (graded && gradeResult) {
        var detail = gradeResult.details[i];
        if (opt === detail.correct_answer) cls += ' correct';
        else if (picked && !detail.ok) cls += ' wrong';
      }
      html += '<label class="' + cls + '">' +
        '<input type="radio" name="rq' + i + '" value="' + escAttr(opt) + '" ' + (picked ? 'checked' : '') + (graded ? ' disabled' : '') + ' onchange="REVIEW_ANSWERS[' + i + ']=this.value">' +
        '<strong>' + esc(opt) + '.</strong> ' + esc(q.options[opt]) +
      '</label>';
    });
    if (graded && gradeResult) {
      html += '<div class="quiz-explanation">' + esc(gradeResult.details[i].explanation || q.explanation || '') + '</div>';
    }
    html += '</div>';
  });
  if (!graded) {
    html += '<button class="eos-btn eos-btn-primary" style="width:100%;padding:12px" onclick="submitReview()">Submit answers</button>';
  } else {
    html += '<div class="quiz-score">' + gradeResult.score + '%</div>' +
            '<p style="text-align:center;color:var(--muted);margin-bottom:24px">' +
            gradeResult.correct + ' of ' + gradeResult.total + ' correct · scheduled for ' +
            (gradeResult.next_review || 'soon') + '</p>' +
            '<div style="display:flex;gap:8px">' +
              '<button class="eos-btn" style="flex:1" onclick="location.hash=\'\'">Stop reviewing</button>' +
              '<button class="eos-btn eos-btn-primary" style="flex:2" onclick="nextCard()">Next card →</button>' +
            '</div>';
  }
  html += '</div>';  // close review-shell
  document.getElementById('review-view').innerHTML = html;
}

async function submitReview() {
  var R = STATE.review;
  if (!R || !R.quiz) return;
  // Auto-score the MCQ answers locally (same model as lesson quiz).
  var card = R.queue[R.idx];
  var quiz = R.quiz;
  var correct = 0;
  var details = [];
  quiz.questions.forEach(function(q, i) {
    var picked = window.REVIEW_ANSWERS[i];
    var right = (q.answer || '').toUpperCase();
    var ok = (picked || '').toUpperCase() === right;
    if (ok) correct++;
    details.push({ index: i, picked: picked, correct_answer: right, ok: ok, explanation: q.explanation || '' });
  });
  var score = Math.round(100 * correct / Math.max(1, quiz.questions.length));
  var grade = await fetch('/learn/api/review/grade-item', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app: 'learn', id: card.id, score: score }),
  }).then(function(r) { return r.json(); }).catch(function(e) { return { error: String(e) }; });

  // Counted here so the header reflects it while the score panel is open;
  // the panel's "Next card" button only moves the index (nextCard).
  R.done++;
  renderReviewCard(card, quiz, window.REVIEW_ANSWERS, true, {
    score: score, correct: correct, total: quiz.questions.length,
    details: details,
    next_review: grade.next_review || null,
  });
}
