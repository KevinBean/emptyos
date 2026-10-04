// dictionary -- page logic, extracted verbatim from pages/index.html (P4 Atomic
// split, .claude/rules/multi-module-apps.md frontend pattern). Loaded at the
// same position as the old inline <script>, so global scope and load order
// vs eos.js / eos-components.js are unchanged.
var esc = EOS_UI.esc;
// Order MUST match the .eos-tab button order in the markup — switchTab
// pairs them by index.
var TABS = ['today','lookup','words','pictures','practice'];
var currentResult = null;
var acTimer = null;
var acIdx = -1;
var _pronounceCache = {};
var _currentAudio = null;

// ── Pronunciation ──
function pronounce(word, btn) {
    if (!word) return;
    if (btn && btn.classList.contains('disabled')) return;
    // Stop any currently playing audio
    if (_currentAudio) { try { _currentAudio.pause(); } catch(e){} _currentAudio = null; }
    document.querySelectorAll('.pronounce-btn.playing').forEach(function(b){ b.classList.remove('playing'); });

    function play(url) {
        var audio = new Audio(url);
        _currentAudio = audio;
        if (btn) btn.classList.add('playing');
        audio.onended = function() { if (btn) btn.classList.remove('playing'); _currentAudio = null; };
        audio.onerror = function() {
            if (btn) btn.classList.remove('playing');
            _currentAudio = null;
            EOS_UI.toast('Playback failed');
        };
        audio.play().catch(function() {
            if (btn) btn.classList.remove('playing');
            _currentAudio = null;
            EOS_UI.toast('Playback blocked');
        });
    }

    if (_pronounceCache[word]) { play(_pronounceCache[word]); return; }

    if (btn) btn.classList.add('playing');
    EOS.api('/dictionary/api/pronounce/' + encodeURIComponent(word)).then(function(d) {
        if (btn) btn.classList.remove('playing');
        if (!d || d.status !== 'ok' || !d.audio_url) {
            if (btn) btn.classList.add('disabled');
            EOS_UI.toast(d && d.status === 'tts_local_only' && d.error
                ? d.error : 'Pronunciation unavailable — voice service offline');
            return;
        }
        _pronounceCache[word] = d.audio_url;
        play(d.audio_url);
    }).catch(function() {
        if (btn) btn.classList.remove('playing');
        EOS_UI.toast('Pronunciation failed');
    });
}

// ── Tabs ──
function switchTab(name) {
    TABS.forEach(function(t, i) {
        var btn = document.querySelectorAll('.eos-tab')[i];
        var sel = t === name;
        btn.classList.toggle('active', sel);
        btn.setAttribute('aria-selected', sel ? 'true' : 'false');
        btn.setAttribute('tabindex', sel ? '0' : '-1');   // roving tabindex (WAI-ARIA tabs)
        document.getElementById('tab-' + t).classList.toggle('active', sel);
    });
    if (name === 'today') loadLoop();
    if (name === 'words') loadVocab();
    if (name === 'practice') loadPractice();
    // Lazy: the pictures module's first call is what starts the photo prefetch,
    // so booting it eagerly would download a pack for someone who never opens
    // the tab. initPictures() is idempotent (pictures.js guards it).
    if (name === 'pictures' && window.initPictures) initPictures();
}

// Keyboard support for the tablist (WAI-ARIA tabs pattern): Enter/Space
// activates the focused tab; arrows/Home/End move + activate, skipping the
// hidden Today tab.
function tabKey(event, name) {
    var key = event.key;
    if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
        event.preventDefault();
        switchTab(name);
        return;
    }
    var dir = (key === 'ArrowRight' || key === 'ArrowDown') ? 1
            : (key === 'ArrowLeft' || key === 'ArrowUp') ? -1 : 0;
    if (!dir && key !== 'Home' && key !== 'End') return;
    event.preventDefault();
    var visible = TABS.filter(function(t) {
        var b = document.getElementById('tab-btn-' + t);
        return b && b.style.display !== 'none';
    });
    var cur = visible.indexOf(name);
    if (cur === -1) return;
    var next = key === 'Home' ? 0
             : key === 'End' ? visible.length - 1
             : (cur + dir + visible.length) % visible.length;
    var target = visible[next];
    switchTab(target);
    var btn = document.getElementById('tab-btn-' + target);
    if (btn) btn.focus();
}

// ── Practice tab: sub-modes (flashcards / quiz / writing rep) ──
var _practiceMode = 'flashcards';
function practiceMode(mode) {
    _practiceMode = mode;
    ['flashcards','quiz','writing'].forEach(function(m) {
        var panel = document.getElementById('practice-' + m);
        if (panel) panel.style.display = (m === mode) ? '' : 'none';
    });
    document.querySelectorAll('.practice-mode').forEach(function(b) {
        b.classList.toggle('active', b.getAttribute('data-mode') === mode);
    });
    if (mode === 'flashcards') loadReview();
    if (mode === 'quiz') showQuizStart();
    if (mode === 'writing') loadWriting();
}
function loadPractice() { practiceMode(_practiceMode); }

// ── Word of the Day ──
function loadWotd() {
    EOS.api('/dictionary/api/word-of-day').then(function(d) {
        if (!d || !d.word) return;
        var el = document.getElementById('wotd');
        el.innerHTML = '<div class="wotd-banner" onclick="lookupWord(' + escAttr(JSON.stringify(d.word)) + ')">' +
            '<div><div class="wotd-label">Word of the Day</div><div class="wotd-word">' + esc(d.word) + '</div></div>' +
            '<div class="wotd-def">' + esc(d.definition || '') + '</div>' +
            '</div>';
    });
}

// ── Autocomplete ──
var inputEl = document.getElementById('lookup-input');
var acEl = document.getElementById('ac-dropdown');

inputEl.addEventListener('input', function() {
    clearTimeout(acTimer);
    var q = inputEl.value.trim();
    if (q.length < 2) { hideAc(); return; }
    acTimer = setTimeout(function() { fetchSuggestions(q); }, 250);
});

inputEl.addEventListener('keydown', function(e) {
    var items = acEl.querySelectorAll('.ac-item');
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        acIdx = Math.min(acIdx + 1, items.length - 1);
        updateAcHighlight(items);
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        acIdx = Math.max(acIdx - 1, 0);
        updateAcHighlight(items);
    } else if (e.key === 'Enter') {
        e.preventDefault();
        if (acIdx >= 0 && items[acIdx]) {
            inputEl.value = items[acIdx].textContent;
            hideAc();
        }
        doLookup();
    } else if (e.key === 'Escape') {
        hideAc();
    }
});

function fetchSuggestions(q) {
    EOS.api('/dictionary/api/suggest?q=' + encodeURIComponent(q)).then(function(d) {
        if (!d || !d.suggestions || !d.suggestions.length) { hideAc(); return; }
        acIdx = -1;
        acEl.innerHTML = d.suggestions.slice(0, 8).map(function(s) {
            return '<div class="ac-item" onmousedown="pickAc(' + escAttr(JSON.stringify(s)) + ')">' + esc(s) + '</div>';
        }).join('');
        acEl.classList.add('show');
    });
}

function pickAc(word) {
    inputEl.value = word;
    hideAc();
    doLookup();
}

function updateAcHighlight(items) {
    for (var i = 0; i < items.length; i++) {
        items[i].classList.toggle('active', i === acIdx);
    }
}

function hideAc() { acEl.classList.remove('show'); acIdx = -1; }

document.addEventListener('click', function(e) {
    if (!acEl.contains(e.target) && e.target !== inputEl) hideAc();
});

// ── Lookup ──
function lookupWord(w) {
    inputEl.value = w;
    switchTab('lookup');
    doLookup();
}

function doLookup(fresh) {
    var word = (fresh && currentResult) ? currentResult.word : inputEl.value.trim();
    if (!word) return;
    var lookupHash = '#lookup/' + encodeURIComponent(word);
    if (location.hash !== lookupHash) history.replaceState(null, '', lookupHash);
    hideAc();
    var container = document.getElementById('lookup-result');
    container.innerHTML = '<div class="loading-pulse">' + (fresh ? 'Refreshing "' : 'Looking up "') + esc(word) + '"...</div>';
    document.getElementById('lookup-btn').disabled = true;

    var url = '/dictionary/api/lookup?word=' + encodeURIComponent(word) + (fresh ? '&fresh=1' : '');
    EOS.api(url).then(function(d) {
        document.getElementById('lookup-btn').disabled = false;
        if (d && d.limit_reached) {
            container.innerHTML = EOS_UI.errorState({message: d.message});
            return;
        }
        if (!d || !d.word) {
            container.innerHTML = '<div class="loading-pulse" style="animation:none">No result found. Check spelling?</div>';
            return;
        }
        currentResult = d;
        renderResult(d, container);
    }).catch(function() {
        document.getElementById('lookup-btn').disabled = false;
        container.innerHTML = '<div class="loading-pulse" style="animation:none">Lookup failed. Try again.</div>';
    });
}

function renderResult(d, container) {
    var syns = (d.synonyms || []);
    var ants = (d.antonyms || []);
    var wordSafe = esc(d.word).replace(/'/g, "\\'");
    var actionHtml = d.from_vault
        ? '<span class="saved-badge" id="save-btn">&#10003; Saved</span>' +
          '<button class="examples-btn" onclick="doLookup(true)" title="Force a fresh AI lookup">&#8635; Refresh with AI</button>' +
          '<button class="examples-btn" onclick="loadExamples(\'' + wordSafe + '\')">More Examples</button>'
        : '<button class="save-btn" id="save-btn" onclick="saveWord()">&#128190; Save to Vault</button>' +
          '<button class="examples-btn" onclick="loadExamples(\'' + wordSafe + '\')">More Examples</button>';

    var provChip = (d.provenance && d.provenance.mode && window.EOS_UI && EOS_UI.provenance)
        ? ' <span style="vertical-align:middle;margin-left:6px">' + EOS_UI.provenance(d.provenance) + '</span>'
        : '';
    var html = '<div class="result-card">' +
        '<div class="result-word">' + esc(d.word) +
        ' <button class="pronounce-btn" onclick="pronounce(\'' + wordSafe + '\',this)" title="Play pronunciation" aria-label="Play pronunciation">&#128266;</button>' +
        (d.phonetic ? ' <span class="result-phonetic">' + esc(d.phonetic) + '</span>' : '') +
        provChip +
        '</div>' +
        (d.part_of_speech ? '<div class="result-pos">' + esc(d.part_of_speech) + '</div>' : '') +
        '<div id="word-picture"></div>' +
        (d.chinese ? '<div class="result-section"><div class="result-label">Chinese</div><div class="result-chinese">' + esc(d.chinese) + '</div></div>' : '') +
        '<div class="result-section"><div class="result-label">Definition</div><div class="result-text">' + esc(d.definition || '') + '</div></div>' +
        (d.example ? '<div class="result-section"><div class="result-label">Example</div><div class="result-example">' + esc(d.example) + '</div></div>' : '') +
        (syns.length ? '<div class="result-section"><div class="result-label">Synonyms</div><div class="result-tags">' + syns.map(function(s){ return '<span class="result-tag">' + esc(s) + '</span>'; }).join('') + '</div></div>' : '') +
        (ants.length ? '<div class="result-section"><div class="result-label">Antonyms</div><div class="result-tags">' + ants.map(function(s){ return '<span class="result-tag">' + esc(s) + '</span>'; }).join('') + '</div></div>' : '') +
        (d.etymology ? '<div class="result-section"><div class="result-label">Etymology</div><div class="etymology-block">' + esc(d.etymology) + '</div></div>' : '') +
        (d.usage_notes ? '<div class="result-section"><div class="result-label">Usage Notes</div><div class="result-text">' + esc(d.usage_notes) + '</div></div>' : '') +
        '<div class="result-actions" id="result-actions">' + actionHtml + '</div>' +
        // Filled in below, once we know whether this word is actually saved — an
        // unsaved word has no note to write a rating into, so offering the stars
        // would be a control that silently fails.
        '<div id="result-difficulty"></div>' +
        '<div id="extra-examples"></div>' +
        '</div>';
    container.innerHTML = html;

    // Fetch SRS level and either upgrade the existing "Saved" badge or swap the Save button for one.
    EOS.api('/dictionary/api/vault/' + encodeURIComponent(d.word)).then(function(r) {
        if (!r || !r.word) return;
        var btn = document.getElementById('save-btn');
        if (!btn) return;
        var lvl = r.srs ? (r.srs.level || 0) : 0;
        if (btn.classList.contains('saved-badge')) {
            btn.innerHTML = '&#10003; Saved (Level ' + lvl + ')';
        } else {
            btn.outerHTML = '<span class="saved-badge" id="save-btn">&#10003; Saved (Level ' + lvl + ')</span>';
        }
        // This is the moment the whole rating exists for: you have just read the
        // definition of a word you already saved, and you still do not know it.
        var slot = document.getElementById('result-difficulty');
        if (slot) {
            slot.innerHTML = '<div class="result-section"><div class="result-label">How hard is this for you?</div>' +
                diffStarsHtml(r.word, r.difficulty, {label: true}) + '</div>';
        }
    }).catch(function(){});

    // Fetch user-configured word addons (external sites) and append as buttons.
    EOS.api('/dictionary/api/word-addons/' + encodeURIComponent(d.word)).then(function(r) {
        if (!r || !r.addons || !r.addons.length) return;
        var row = document.getElementById('result-actions');
        if (!row) return;
        r.addons.forEach(function(a) {
            var btn = document.createElement('button');
            btn.className = 'examples-btn';
            btn.title = a.label;
            btn.textContent = (a.icon ? a.icon + ' ' : '') + a.label;
            btn.addEventListener('click', function() { window.open(a.url, '_blank', 'noopener'); });
            row.appendChild(btn);
        });
    }).catch(function(){});

    // If this word is also in a picture pack, show that photograph. Most words
    // are not (`obviate`, `contingency`), so {} is the common answer and the
    // slot simply stays empty — never a placeholder, never a guess.
    EOS.api('/dictionary/api/picture/for-word/' + encodeURIComponent(d.word)).then(function(p) {
        if (!p || !p.url) return;
        var slot = document.getElementById('word-picture');
        if (!slot) return;
        var sec = document.createElement('div');
        sec.className = 'result-section';

        var img = document.createElement('img');
        img.src = p.url;
        img.alt = p.name || d.word;
        img.loading = 'lazy';
        img.style.cssText = 'max-width:100%;border-radius:var(--radius,8px);display:block';
        // A pack whose photo 404s should leave no broken-image icon behind.
        img.onerror = function() { sec.remove(); };

        var link = document.createElement('a');
        link.href = p.href || '#';
        link.title = 'Open in the picture packs';
        link.style.textDecoration = 'none';
        link.appendChild(img);

        // Captioned with its source on purpose: exact matching still lets a
        // homonym through (a `crane` note about the machine gets the bird), and
        // a labelled photo is visibly wrong rather than silently wrong.
        var cap = document.createElement('div');
        cap.className = 'result-label';
        cap.style.cssText = 'margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;align-items:baseline';
        var src = document.createElement('span');
        src.textContent = (p.pack ? p.pack + ' pack' : 'Picture pack') + ' · ' + (p.name || '');
        cap.appendChild(src);
        var cr = p.credit || {};
        if (cr.artist || cr.license) {
            var by = document.createElement('span');
            by.style.cssText = 'opacity:.75;font-weight:normal';
            by.textContent = [cr.artist, cr.license].filter(Boolean).join(' · ');
            cap.appendChild(by);
        }

        sec.appendChild(link);
        sec.appendChild(cap);
        slot.appendChild(sec);
    }).catch(function(){});
}

function saveWord() {
    if (!currentResult) return;
    var btn = document.getElementById('save-btn');
    btn.disabled = true;
    btn.innerHTML = 'Saving...';
    EOS.post('/dictionary/api/save', currentResult).then(function() {
        btn.outerHTML = '<span class="saved-badge">&#10003; Saved</span>';
        EOS_UI.toast('Word saved to vault');
    }).catch(function() {
        btn.disabled = false;
        btn.innerHTML = '&#128190; Save to Vault';
        EOS_UI.toast('Failed to save');
    });
}

function loadExamples(word) {
    var el = document.getElementById('extra-examples');
    if (!el) return;
    el.innerHTML = '<div class="loading-pulse">Loading examples...</div>';
    EOS.api('/dictionary/api/examples/' + encodeURIComponent(word)).then(function(d) {
        if (!d || !d.examples || !d.examples.length) {
            el.innerHTML = '';
            return;
        }
        el.innerHTML = '<div class="extra-examples"><ul>' +
            d.examples.map(function(ex) { return '<li>' + esc(ex) + '</li>'; }).join('') +
            '</ul></div>';
    }).catch(function() { el.innerHTML = ''; });
}

// ── Vocabulary Tab ──
// Threads — the deck grouped by the `topics` / `word_family` each word already
// carries. Renders nothing at all when the feature is off or when no group has
// two members yet, so an unthreaded deck shows no empty scaffolding.
function loadThreads() {
    var area = document.getElementById('threads-area');
    if (!area) return;
    EOS.apiSafe('/dictionary/api/threads').then(function(d) {
        if (!d || d.error || !d.enabled || !d.threads || !d.threads.length) {
            area.innerHTML = '';
            return;
        }
        area.innerHTML = '<h3 style="margin:18px 0 10px;font-size:15px">Threads ' +
            '<span style="font-weight:400;color:var(--text-muted);font-size:13px">' +
            d.threaded_words + ' of ' + d.deck_size + ' words connected</span></h3>' +
            d.threads.map(function(t) {
                return '<div class="thread-group">' +
                    '<div class="thread-head">' +
                        '<span class="thread-name">' + esc(t.thread) + '</span>' +
                        '<span class="thread-kind">' + esc(t.kind) + '</span>' +
                        '<span class="thread-kind">' + t.words.length + '</span>' +
                    '</div>' +
                    '<div class="thread-words">' + t.words.map(function(w) {
                        return '<button class="thread-word" title="Look up ' + escAttr(w) +
                            '" onclick="lookupWord(' + escAttr(JSON.stringify(w)) + ')">' +
                            esc(w) + '</button>';
                    }).join('') + '</div>' +
                    '</div>';
            }).join('');
    });
}

function exportVocabCsv() {
    window.open('/dictionary/api/export?format=csv', '_blank', 'noopener');
}

// ── CSV import (dictionary-word-list-import) ──────────────────────────
var VOCAB_IMPORT_PREVIEW = null;

function openVocabImportPicker() {
    document.getElementById('vocab-import-file-input').click();
}

async function handleVocabImportFile(ev) {
    var file = ev.target.files && ev.target.files[0];
    ev.target.value = '';  // allow re-selecting the same file later
    if (!file) return;
    var fd = new FormData();
    fd.append('file', file);
    var data = await EOS.apiSafe('/dictionary/api/vault/import/preview', { method: 'POST', body: fd });
    if (data.error) { EOS_UI.toast(data.error, false); return; }
    showVocabImportPreview(data);
}

function showVocabImportPreview(data) {
    VOCAB_IMPORT_PREVIEW = data;
    var s = data.summary || {};
    var warn = (data.warnings || []).map(function(w) {
        return '<div style="color:var(--amber,#c90);font-size:12px;margin:2px 0">' + esc(w) + '</div>';
    }).join('');
    var rows = (data.rows || []).map(function(r, i) {
        return '<tr>' +
            '<td><input type="checkbox" class="vocab-import-row-cb" data-idx="' + i + '" checked></td>' +
            '<td>' + esc(r.word) + '</td>' +
            '<td>' + esc(r.definition || '—') + '</td>' +
            '<td>' + esc(r.chinese || '—') + '</td>' +
            '<td>' + (r.already_in_deck ? '<span style="color:var(--text-muted)">already in deck (will enrich)</span>' : 'new') + '</td>' +
        '</tr>';
    }).join('');
    var body =
        warn +
        '<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px">' +
            esc(s.total || 0) + ' rows parsed · ' + esc(s.new || 0) + ' new · ' + esc(s.already_in_deck || 0) + ' already in deck' +
            (s.skipped ? ' · ' + esc(s.skipped) + ' skipped (no word found)' : '') +
        '</div>' +
        '<div style="max-height:360px;overflow:auto">' +
        '<table class="req-table" style="width:100%;border-collapse:collapse"><thead><tr>' +
        '<th></th><th>Word</th><th>Definition</th><th>Chinese</th><th>Status</th></tr></thead>' +
        '<tbody>' + rows + '</tbody></table></div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">' +
            '<button class="eos-btn eos-btn-secondary" onclick="EOS_UI.closeModal()">Cancel</button>' +
            '<button class="eos-btn eos-btn-primary" onclick="confirmVocabImport()">Import selected</button>' +
        '</div>';
    EOS_UI.modal({ title: 'Import word list — review before saving', body: body, width: '720px' });
}

async function confirmVocabImport() {
    if (!VOCAB_IMPORT_PREVIEW) return;
    var checked = Array.prototype.slice.call(document.querySelectorAll('.vocab-import-row-cb:checked'));
    var rows = checked.map(function(cb) { return VOCAB_IMPORT_PREVIEW.rows[parseInt(cb.dataset.idx, 10)]; });
    if (!rows.length) { EOS_UI.toast('Nothing selected', false); return; }
    var res = await EOS.apiSafe('/dictionary/api/vault/import/confirm', { method: 'POST', body: JSON.stringify({ rows: rows }) });
    if (res.error) { EOS_UI.toast(res.error, false); return; }
    EOS_UI.toast('Imported ' + res.imported + ' word(s)' + (res.skipped ? ', skipped ' + res.skipped : ''), true);
    EOS_UI.closeModal();
    VOCAB_IMPORT_PREVIEW = null;
    loadVocab();
}

// ── Difficulty stars — the reader's own "how hard is this for me", 1-5 ──
//
// Separate from everything else on the row on purpose. The level dots are the
// SRS ladder (how a schedule has gone) and the heart is Favorite; neither can
// say "I have looked this up four times and it still will not stick", which is
// the one thing the reader knows and the machine cannot infer.
var DIFF_MAX = 5;

function diffStarsHtml(word, value, opts) {
    opts = opts || {};
    var v = Number(value) || 0;
    var out = '<span class="diff-stars" data-word="' + escAttr(word) + '" data-value="' + v + '"' +
        ' role="group" aria-label="How hard is this word for you"' +
        ' title="' + (v ? 'Hard for you: ' + v + ' of ' + DIFF_MAX + ' — click the same star to clear'
                        : 'Rate how hard this word is for you') + '">';
    for (var i = 1; i <= DIFF_MAX; i++) {
        out += '<button type="button" class="diff-star' + (i <= v ? ' on' : '') + '"' +
            ' onclick="event.stopPropagation();setDifficulty(' + EOS_UI.jsArg(word) + ',' + i + ',this)"' +
            ' aria-pressed="' + (i <= v) + '" aria-label="' + i + ' of ' + DIFF_MAX + '">&#9733;</button>';
    }
    if (opts.label) out += '<span class="diff-label">' + (v ? v + '/' + DIFF_MAX : 'rate') + '</span>';
    return out + '</span>';
}

function paintDiffStars(wrap, value) {
    wrap.dataset.value = value;
    var stars = wrap.querySelectorAll('.diff-star');
    for (var i = 0; i < stars.length; i++) {
        var on = (i + 1) <= value;
        stars[i].classList.toggle('on', on);
        stars[i].setAttribute('aria-pressed', String(on));
    }
    var label = wrap.querySelector('.diff-label');
    if (label) label.textContent = value ? value + '/' + DIFF_MAX : 'rate';
    // The row states the rating in WORDS as well as in star colour, and it has to
    // stay in step with an optimistic repaint — otherwise a just-rated row says it
    // only by colour until the next load, which is the one thing
    // .claude/rules/list-card-density.md rules out. Absent on the lookup and review
    // cards, which carry their own label instead.
    var item = wrap.closest('.vocab-item');
    var hard = item && item.querySelector('.vocab-hard');
    if (hard) {
        hard.textContent = value ? 'Hard for me ' + value + '/' + DIFF_MAX : '';
        hard.hidden = !value;
    }
    wrap.title = value ? 'Hard for you: ' + value + ' of ' + DIFF_MAX + ' — click the same star to clear'
                       : 'Rate how hard this word is for you';
}

function setDifficulty(word, n, btn) {
    var wrap = btn.closest('.diff-stars');
    var current = Number(wrap.dataset.value) || 0;
    // Clicking the star you are already on clears the rating. Without it a 1-star
    // word can only ever go up, and "actually this one is fine now" is the whole
    // point of a rating the reader owns.
    var next = (current === n) ? 0 : n;
    paintDiffStars(wrap, next);                       // optimistic — the write is one field
    EOS.post('/dictionary/api/difficulty', { word: word, difficulty: next }).then(function(d) {
        if (!d || d.error) { paintDiffStars(wrap, current); EOS_UI.toast(d && d.error || 'Could not save rating'); return; }
        // Repaint from the server's number, not ours: it clamps, and a note edited
        // elsewhere may not have held the value this row was showing. Every row for
        // the same word repaints — the lookup card and the list can both be showing it.
        // Matched in JS rather than through an attribute selector built from `word`:
        // a saved phrase ("in spite of") or an apostrophe would break the selector,
        // and CSS.escape is for identifiers, not for the string inside [attr="..."].
        var saved = Number(d.difficulty) || 0;
        var all = document.querySelectorAll('.diff-stars');
        for (var i = 0; i < all.length; i++) {
            if (all[i].dataset.word === word) paintDiffStars(all[i], saved);
        }
        // Keep the fetched deck in step, or the next cut/sort renders from a
        // stale rating — the list is filtered from this array, not refetched.
        for (var j = 0; j < _vocabAll.length; j++) {
            if (_vocabAll[j].word === word) _vocabAll[j].difficulty = saved;
        }
        EOS_UI.toast(saved ? 'Marked ' + saved + '/' + DIFF_MAX + ' hard' : 'Rating cleared');
    }).catch(function() {
        paintDiffStars(wrap, current);
        EOS_UI.toast('Could not save rating');
    });
}

// DIFF_MAX mirrors `vocab_schema.DIFFICULTY_MAX` — the backend clamps to it, so a
// change there has to be made here too. Not read from the API: the lookup card and
// the review card render stars without ever calling the vocabulary list.
var vocabFilter = { min: 0, sort: '' };
var _vocabCuts = null;
var _vocabAll = [];   // the whole deck as fetched; the cuts render from this

function initVocabFilters() {
    if (_vocabCuts) return;
    var cuts = [];
    for (var n = 3; n <= DIFF_MAX; n++) {
        cuts.push({ key: String(n), label: '\u2605 ' + n + (n < DIFF_MAX ? '+' : '') });
    }
    _vocabCuts = EOS_UI.filterBar('vocab-cuts', cuts, function(key) {
        vocabFilter.min = (key === 'all') ? 0 : Number(key);
        renderVocabList();
    }, { allLabel: 'All words' });
    renderVocabSort();
}

function toggleVocabSort() {
    vocabFilter.sort = vocabFilter.sort === 'difficulty' ? '' : 'difficulty';
    renderVocabSort();
    renderVocabList();
}

function renderVocabSort() {
    var el = document.getElementById('vocab-sort');
    if (!el) return;
    var on = vocabFilter.sort === 'difficulty';
    el.innerHTML = '<button class="eos-filter-pill' + (on ? ' active' : '') + '"' +
        ' onclick="toggleVocabSort()" aria-pressed="' + on + '"' +
        ' title="Order the deck by how hard you rated each word">Hardest first</button>';
}

function loadVocab() {
    loadProgress();
    loadThreads();
    EOS.api('/dictionary/api/srs/stats').then(function(stats) {
        if (stats) {
            EOS_UI.statCards('vocab-stats', [
                { label: 'Total Words', value: stats.total_words || 0 },
                { label: 'Mastered', value: stats.mastered || 0 },
                { label: 'Learning', value: stats.learning || 0 },
                { label: 'Due Today', value: stats.due_today || 0 },
                { label: 'Hard for me', value: stats.hard || 0 }
            ]);
        }
    });

    var listEl = document.getElementById('vocab-list');
    listEl.innerHTML = '<div class="loading-pulse">Loading vocabulary...</div>';

    initVocabFilters();
    // Fetched WHOLE and filtered in the browser. `/api/vault` also honours
    // `min_difficulty` + `sort` (they are the API contract, and tested), but the
    // UI must not use them: that route opens every note in the deck to read its
    // frontmatter, so refetching per filter click costs ~500 file reads to narrow
    // a list whose rows already carry `difficulty`. The row IS the data.
    EOS.api('/dictionary/api/vault').then(function(d) {
        _vocabAll = (d && d.words) || [];
        renderVocabList();
    }).catch(function() {
        listEl.innerHTML = EOS_UI.errorState({message: 'Failed to load vocabulary.', onRetry: 'loadVocab()'});
    });
}

function renderVocabList() {
    var listEl = document.getElementById('vocab-list');
    if (!listEl) return;
    var words = _vocabAll;
    if (vocabFilter.min) {
        words = words.filter(function(w) { return (w.difficulty || 0) >= vocabFilter.min; });
    }
    if (vocabFilter.sort === 'difficulty') {
        // Copy before sorting — `_vocabAll` is the fetched order and other views
        // read it. Ties break alphabetically so the list does not reshuffle
        // between renders, matching how the review deck breaks its own ties.
        words = words.slice().sort(function(a, b) {
            return (b.difficulty || 0) - (a.difficulty || 0)
                || a.word.toLowerCase().localeCompare(b.word.toLowerCase());
        });
    }
    (function(d) {
        if (!d || !d.words || !d.words.length) {
            listEl.innerHTML = '<div class="vocab-empty">' + (vocabFilter.min
                ? 'No words rated ' + vocabFilter.min + ' stars or harder yet. Rate one from its row, the lookup card, or a review.'
                : 'No saved words yet. Look up a word and save it!') + '</div>';
            return;
        }
        listEl.innerHTML = '<div class="vocab-list">' + d.words.map(function(w, idx) {
            var dots = '';
            for (var i = 0; i < 7; i++) {
                var cls = i < (w.level || 0) ? ((w.level || 0) >= 7 ? 'mastered' : 'filled') : '';
                dots += '<div class="level-dot ' + cls + '"></div>';
            }
            // Favorite is the heart and difficulty is the stars — one glyph per
            // idiom, so a filled star always means "hard" and never "starred".
            var heart = w.favorite ? '&#9829;' : '&#9825;';
            var review = w.next_review ? 'Review: ' + esc(w.next_review) : '';
            return '<div class="vocab-item" style="animation-delay:' + (idx * 0.03) + 's">' +
                '<div class="vocab-header" onclick="toggleVocabDetail(this)">' +
                    '<span class="vocab-word">' + esc(w.word) + '</span>' +
                    '<div class="vocab-level" title="Level ' + (w.level || 0) + '/7">' + dots + '</div>' +
                    diffStarsHtml(w.word, w.difficulty) +
                    '<button class="vocab-star' + (w.favorite ? ' on' : '') + '" onclick="event.stopPropagation();toggleFav(' + escAttr(JSON.stringify(w.word)) + ',this)" title="Favorite" aria-label="Favorite">' + heart + '</button>' +
                '</div>' +
                '<div class="vocab-meta">' +
                    '<span>Level ' + (w.level || 0) + '</span>' +
                    '<span class="vocab-hard"' + (w.difficulty ? '' : ' hidden') + '>' +
                        (w.difficulty ? 'Hard for me ' + w.difficulty + '/' + DIFF_MAX : '') + '</span>' +
                    (review ? '<span>' + esc(review) + '</span>' : '') +
                '</div>' +
                '<div class="vocab-detail" id="vd-' + idx + '"><div class="loading-pulse">Loading...</div></div>' +
                '</div>';
        }).join('') + '</div>';
    })({ words: words });
}

function toggleVocabDetail(headerEl) {
    var item = headerEl.closest('.vocab-item');
    var detail = item.querySelector('.vocab-detail');
    if (detail.classList.contains('show')) {
        detail.classList.remove('show');
        return;
    }
    // Close others
    document.querySelectorAll('.vocab-detail.show').forEach(function(d) { d.classList.remove('show'); });
    detail.classList.add('show');

    var word = item.querySelector('.vocab-word').textContent;
    var wordSafe = esc(word).replace(/'/g, "\\'");
    EOS.api('/dictionary/api/vault/' + encodeURIComponent(word)).then(function(d) {
        if (!d) { detail.innerHTML = '<div>No details</div>'; return; }
        var def = d.definition || '';
        var chi = d.chinese || '';
        var ex = d.example || '';
        detail.innerHTML = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
                '<button class="pronounce-btn pronounce-btn-small" onclick="pronounce(\'' + wordSafe + '\',this)" title="Play pronunciation" aria-label="Play pronunciation">&#128266;</button>' +
                '<span style="font-size:13px;color:var(--text-muted)">Pronounce</span>' +
            '</div>' +
            (def ? '<div class="result-text">' + esc(def) + '</div>' : '<div class="result-text" style="color:var(--text-muted);font-style:italic">No definition saved. Look this word up again to repopulate.</div>') +
            (chi ? '<div class="result-chinese" style="margin-top:6px">' + esc(chi) + '</div>' : '') +
            (ex ? '<div class="result-example" style="margin-top:6px">' + esc(ex) + '</div>' : '') +
            '<button class="vocab-delete" onclick="deleteWord(\'' + wordSafe + '\')">Delete</button>';
    }).catch(function() {
        detail.innerHTML = '<div>Failed to load details</div>';
    });
}

function toggleFav(word, btn) {
    EOS.post('/dictionary/api/favorite', { word: word }).then(function(d) {
        var on = !!(d && d.favorite);
        btn.innerHTML = on ? '&#9829;' : '&#9825;';
        btn.classList.toggle('on', on);
        EOS_UI.toast(on ? 'Favorited' : 'Unfavorited');
    });
}

function deleteWord(word) {
    EOS_UI.confirm({message: 'Delete "' + word + '" from vocabulary?', action: 'Delete', danger: true}, function() {
        EOS.api('/dictionary/api/vault/' + encodeURIComponent(word), { method: 'DELETE' }).then(function() {
            EOS_UI.toast('Deleted "' + word + '"');
            loadVocab();
        }).catch(function() {
            EOS_UI.toast('Failed to delete');
        });
    });
}

// ── SRS Review Tab ──
var reviewDeck = [];
var reviewIdx = 0;
var reviewRevealed = false;
var reviewResults = [];

function loadReview() {
    var area = document.getElementById('review-area');
    area.innerHTML = '<div class="loading-pulse">Loading review deck...</div>';

    EOS.api('/dictionary/api/srs/deck?limit=20').then(function(d) {
        if (!d || !d.deck || !d.deck.length) {
            area.innerHTML = '<div class="srs-empty">' +
                '<p>No cards due for review!</p>' +
                '<p style="font-size:13px;color:var(--text-muted)">' + (d && d.total_words ? d.total_words + ' words in collection' : 'Save some words first') + '</p>' +
                '</div>';
            return;
        }
        reviewDeck = d.deck;
        reviewIdx = 0;
        reviewRevealed = false;
        reviewResults = [];
        renderReviewCard();
    }).catch(function() {
        area.innerHTML = EOS_UI.errorState({ message: 'Failed to load the review deck.', onRetry: 'loadReview()' });
    });
}

function renderReviewCard() {
    var area = document.getElementById('review-area');
    if (reviewIdx >= reviewDeck.length) {
        renderReviewDone();
        return;
    }
    var card = reviewDeck[reviewIdx];
    var html = '<div class="srs-progress">' +
        '<span>' + (reviewIdx + 1) + ' / ' + reviewDeck.length + '</span>' +
        '<div class="srs-bar"><div class="srs-bar-fill" style="width:' + ((reviewIdx / reviewDeck.length) * 100) + '%"></div></div>' +
        (card.level !== undefined ? '<span>Lv ' + card.level + '</span>' : '') +
        '</div>' +
        '<div class="flashcard">' +
        '<div class="flashcard-word">' + esc(card.word) +
            ' <button class="pronounce-btn pronounce-btn-small" onclick="pronounce(' + escAttr(JSON.stringify(card.word)) + ',this)" title="Play pronunciation" aria-label="Play pronunciation">&#128266;</button>' +
        '</div>' +
        (card.new_word ? '<span class="result-pos" style="margin:0 auto">NEW</span>' : '') +
        // A rating is not a grade. Again/Hard/Good/Easy below tells FSRS how THIS
        // attempt went; this says the word is hard for you in general, and it
        // sticks — so a short session can be spent on the ones you flagged.
        '<div style="display:flex;justify-content:center;margin:2px 0 6px">' +
            diffStarsHtml(card.word, card.difficulty) + '</div>' +
        // The sentence the word was actually met in, on the FRONT — recalling a
        // word in the context you read it in is the whole point of a sentence
        // card. Absent for words saved before the context was carried through.
        (card.sentence ? '<div class="flashcard-context">' + boldWord(card.sentence, card.word) + '</div>' : '') +
        momentLinkHtml(card.source) +
        '<div id="card-reveal" style="display:none"></div>' +
        '<button class="reveal-btn" id="reveal-btn" onclick="revealCard()">Show Definition</button>' +
        '<div class="flashcard-hint">Press Space to reveal</div>' +
        '</div>';
    area.innerHTML = html;
    reviewRevealed = false;

    // The deck already carries the context when card-context is enabled; only
    // fall back to a per-card fetch when it does not, so the enabled path costs
    // one request for the whole deck instead of one per card.
    if (card.definition) {
        fillReveal(card.definition, card.chinese || '', card.sentence || '');
    } else {
        EOS.api('/dictionary/api/vault/' + encodeURIComponent(card.word)).then(function(d) {
            if (!d) return;
            fillReveal(d.definition || '', d.chinese || '', d.example || '');
        }).catch(function() { fillReveal('', '', ''); });
    }
}

// Show the target word in its sentence — a sentence card is useless if the eye
// cannot find the word it is testing.
function boldWord(sentence, word) {
    var s = sentence || '', w = word || '';
    if (!w) return esc(s);

    // Whole tokens only, and the WHOLE token: the harvest stores the LEMMA
    // ("ratify") while the sentence carries what was actually said ("ratified"),
    // so this is the common case, not an edge one. Trying the full word before
    // its stem keeps "run" from lighting up the first three letters of
    // "running" and leaving the rest dark.
    function tokenAt(prefix) {
        if (prefix.length < 3) return null;
        var re = new RegExp('\\b' + prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + "[a-z'-]*", 'i');
        return re.exec(s);
    }
    var m = tokenAt(w) || tokenAt(w.slice(0, Math.max(4, w.length - 3)));
    var i = m ? m.index : s.toLowerCase().indexOf(w.toLowerCase());
    var len = m ? m[0].length : w.length;

    if (i < 0) return esc(s);
    return esc(s.slice(0, i)) + '<b>' + esc(s.slice(i, i + len)) + '</b>' + esc(s.slice(i + len));
}

// "Hear it where you found it" — only for a source carrying a timestamp, which
// is what makes it a moment rather than a link to the top of an hour of video.
function momentLinkHtml(source) {
    if (!source || !/^https?:\/\//i.test(source) || !/[?&]t=\d+s?/.test(source)) return '';
    return '<a class="flashcard-moment" href="' + escAttr(source) + '" target="_blank" rel="noopener">' +
        '&#9654; hear it where you found it</a>';
}

function fillReveal(def, chi, ex) {
    var revEl = document.getElementById('card-reveal');
    if (!revEl) return;
    revEl.innerHTML = '<div class="flashcard-reveal">' +
        (def ? '<div class="flashcard-def">' + esc(def) + '</div>'
             : '<div class="flashcard-def" style="color:var(--text-muted)">No definition saved for this word.</div>') +
        (chi ? '<div class="flashcard-chinese">' + esc(chi) + '</div>' : '') +
        (ex ? '<div class="flashcard-example">' + esc(ex) + '</div>' : '') +
        '</div>' +
        '<div class="rating-row">' +
        '<button class="rate-btn" data-q="1" onclick="rateCard(1)">Forgot <span class="rate-key">1</span></button>' +
        '<button class="rate-btn" data-q="2" onclick="rateCard(2)">Hard <span class="rate-key">2</span></button>' +
        '<button class="rate-btn" data-q="3" onclick="rateCard(3)">Good <span class="rate-key">3</span></button>' +
        '<button class="rate-btn" data-q="4" onclick="rateCard(4)">Easy <span class="rate-key">4</span></button>' +
        '</div>';
}

function revealCard() {
    reviewRevealed = true;
    var revEl = document.getElementById('card-reveal');
    var btn = document.getElementById('reveal-btn');
    if (revEl) revEl.style.display = 'block';
    if (btn) btn.style.display = 'none';
    var hint = document.querySelector('.flashcard-hint');
    if (hint) hint.style.display = 'none';
}

function rateCard(quality) {
    var card = reviewDeck[reviewIdx];
    reviewResults.push({ word: card.word, quality: quality });
    EOS.post('/dictionary/api/srs/review', { word: card.word, quality: quality });
    reviewIdx++;
    renderReviewCard();
}

function renderReviewDone() {
    var area = document.getElementById('review-area');
    var good = 0, total = reviewResults.length;
    reviewResults.forEach(function(r) { if (r.quality >= 3) good++; });
    area.innerHTML = '<div class="srs-done">' +
        '<h2>Review Complete!</h2>' +
        '<p>' + total + ' cards reviewed</p>' +
        '<p style="font-size:18px;font-weight:700;color:var(--success);margin-top:12px">' + good + '/' + total + ' correct</p>' +
        '<p style="margin-top:8px">' + (total - good) + ' to review again</p>' +
        '<button class="start-btn" style="margin-top:20px" onclick="loadReview()">Review Again</button>' +
        '</div>';
}

// Keyboard for SRS — only when Practice tab + Flashcards sub-mode are active.
document.addEventListener('keydown', function(e) {
    var practiceActive = document.getElementById('tab-practice').classList.contains('active');
    var flashActive = _practiceMode === 'flashcards';
    if (practiceActive && flashActive && reviewDeck.length > 0 && reviewIdx < reviewDeck.length) {
        if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            if (!reviewRevealed) revealCard();
        } else if (reviewRevealed && e.key >= '1' && e.key <= '4') {
            e.preventDefault();
            rateCard(parseInt(e.key));
        }
    }
});

// ── Quiz Tab ──
var quizQuestions = [];
var quizIdx = 0;
var quizScore = 0;
var quizAnswered = false;

function showQuizStart() {
    var area = document.getElementById('quiz-area');
    area.innerHTML = '<div class="quiz-start">' +
        '<p>Test your vocabulary with a multiple-choice quiz.</p>' +
        '<button class="start-btn" onclick="startQuiz()">Start Quiz (5 questions)</button>' +
        '</div>';
}

function startQuiz() {
    var area = document.getElementById('quiz-area');
    area.innerHTML = '<div class="loading-pulse">Generating quiz...</div>';

    EOS.api('/dictionary/api/quiz?count=5').then(function(d) {
        if (!d || !d.questions || !d.questions.length) {
            area.innerHTML = '<div class="quiz-start"><p>Not enough saved words for a quiz. Save at least 4 words first!</p>' +
                '<button class="start-btn" style="margin-top:12px" onclick="switchTab(\'lookup\')">Go to Lookup</button></div>';
            return;
        }
        quizQuestions = d.questions;
        quizIdx = 0;
        quizScore = 0;
        renderQuizQuestion();
    }).catch(function() {
        area.innerHTML = '<div class="quiz-start"><p>Failed to generate quiz.</p><button class="start-btn" onclick="startQuiz()">Try Again</button></div>';
    });
}

function renderQuizQuestion() {
    var area = document.getElementById('quiz-area');
    if (quizIdx >= quizQuestions.length) {
        renderQuizScore();
        return;
    }
    var q = quizQuestions[quizIdx];
    quizAnswered = false;
    var html = '<div class="quiz-progress">Question ' + (quizIdx + 1) + ' of ' + quizQuestions.length + '</div>' +
        '<div class="quiz-question">' +
        '<div class="quiz-prompt">' + esc(q.definition) + '</div>' +
        (q.chinese ? '<div class="quiz-chinese">' + esc(q.chinese) + '</div>' : '') +
        '<div class="quiz-options">' +
        q.options.map(function(opt, i) {
            return '<button class="quiz-opt" data-answer="' + escAttr(opt) + '" onclick="pickAnswer(this,' + escAttr(JSON.stringify(q.answer)) + ')">' + esc(opt) + '</button>';
        }).join('') +
        '</div>' +
        '<button class="quiz-next" id="quiz-next" onclick="nextQuizQuestion()">Next</button>' +
        '</div>';
    area.innerHTML = html;
}

function pickAnswer(btn, correct) {
    if (quizAnswered) return;
    quizAnswered = true;
    var picked = btn.getAttribute('data-answer');
    var isCorrect = picked === correct;
    if (isCorrect) quizScore++;

    var opts = document.querySelectorAll('.quiz-opt');
    opts.forEach(function(o) {
        o.classList.add('disabled');
        if (o.getAttribute('data-answer') === correct) o.classList.add('correct');
    });
    if (!isCorrect) btn.classList.add('wrong');

    document.getElementById('quiz-next').style.display = 'inline-block';
}

function nextQuizQuestion() {
    quizIdx++;
    renderQuizQuestion();
}

function renderQuizScore() {
    var area = document.getElementById('quiz-area');
    var pct = Math.round((quizScore / quizQuestions.length) * 100);
    area.innerHTML = '<div class="quiz-score">' +
        '<h2>Quiz Complete!</h2>' +
        '<div class="big-score">' + pct + '%</div>' +
        '<p>' + quizScore + ' out of ' + quizQuestions.length + ' correct</p>' +
        '<button class="start-btn" style="margin-top:20px" onclick="startQuiz()">Play Again</button>' +
        '</div>';
}

// ── App Settings slide-out (shared helper) ──
var _appSettings = EOS_UI.settingsPanel({
    id: 'app-settings-panel',
    title: 'Dictionary Settings',
    app: 'dictionary',
});
function openAppSettings() { _appSettings.open(); }

// ── Model pill (shared helper) ──
// The lookup a learner reads IS model output (`lookup()` -> self.think), so the
// provider that spends their budget belongs on this surface, not only on the
// pack-compose authoring flow.
EOS_UI.modelPill({ app: 'dictionary', mount: '#model-pill', domain: 'text' });

// ── Progress to 30k (My words tab) ──
function _sparkline(points, opts) {
    opts = opts || {};
    var w = opts.w || 240, h = opts.h || 46, pad = 5;
    if (!points.length) return '';
    var ys = points.map(function(p){ return p.y; });
    var ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
    var span = (ymax - ymin) || 1; ymin -= span * 0.15; ymax += span * 0.15;
    var n = points.length;
    var coords = points.map(function(p, i) {
        var x = pad + (n === 1 ? (w - 2 * pad) / 2 : i * (w - 2 * pad) / (n - 1));
        var y = h - pad - (p.y - ymin) / (ymax - ymin) * (h - 2 * pad);
        return [x, y];
    });
    var path = coords.map(function(c, i) { return (i ? 'L' : 'M') + c[0].toFixed(1) + ' ' + c[1].toFixed(1); }).join(' ');
    var dots = coords.map(function(c) { return '<circle cx="' + c[0].toFixed(1) + '" cy="' + c[1].toFixed(1) + '" r="2.5" fill="var(--accent)"/>'; }).join('');
    return '<svg width="' + w + '" height="' + h + '">' +
        '<path d="' + path + '" fill="none" stroke="var(--accent)" stroke-width="2"/>' + dots + '</svg>';
}

function _progressBar(label, value, target) {
    var pct = Math.max(0, Math.min(100, value / target * 100));
    return '<div style="margin:10px 0">' +
        '<div style="display:flex;justify-content:space-between;font-size:.9em;opacity:.85">' +
        '<span>' + esc(label) + '</span><span>' + value.toLocaleString() + ' / ' + target.toLocaleString() + '</span></div>' +
        '<div style="height:10px;border-radius:6px;background:var(--border);margin-top:4px;overflow:hidden">' +
        '<div style="height:100%;width:' + pct.toFixed(1) + '%;background:var(--accent)"></div></div>' +
        '<div style="font-size:.78em;opacity:.55;margin-top:2px">' + pct.toFixed(0) + '%</div></div>';
}

function loadProgress() {
    var area = document.getElementById('progress-area');
    if (!area) return;
    EOS.api('/dictionary/api/progress').then(function(d) {
        if (!d || d.enabled === false) { area.innerHTML = ''; return; }
        var score = d.latest_score, tests = d.tests || [];
        var html = '<div style="border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:18px">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">' +
            '<h3 style="margin:0">📈 Progress to 30k</h3>' +
            '<button class="eos-btn-sm" onclick="logTest()">Log test result</button></div>';
        if (score != null) {
            html += _progressBar('Phase 1 → 10,000', score, 10000);
            html += _progressBar('North star → 30,000', score, 30000);
        }
        html += '<div style="margin-top:10px;opacity:.85">🗂️ <strong>' + (d.deck_size || 0) + '</strong> words in your SRS deck</div>';
        if (tests.length) {
            html += '<div style="margin-top:12px"><div style="font-size:.85em;opacity:.7;margin-bottom:4px">Test scores over time</div>' +
                _sparkline(tests.map(function(t) { return { x: t.date, y: t.score }; })) +
                '<div style="font-size:.8em;opacity:.6;margin-top:4px">' +
                tests.map(function(t) { return esc(t.date) + ': ' + t.score.toLocaleString(); }).join('  ·  ') + '</div></div>';
        }
        if (d.days_since_test != null && d.days_since_test >= 90) {
            html += '<div style="margin-top:10px;padding:8px 10px;border-radius:8px;background:var(--border);font-size:.88em">' +
                '⏱️ Last test was ' + d.days_since_test + ' days ago — time to re-take it (quarterly cadence).</div>';
        }
        html += '</div>';
        area.innerHTML = html;
    }).catch(function() { area.innerHTML = ''; });
}

function logTest() {
    EOS_UI.formModal('Log a vocabulary-test result', [
        { key: 'score', label: 'Score (number of words)', type: 'number', autofocus: true },
    ], function(vals) {
        var score = parseInt(String(vals.score || '').replace(/[^0-9]/g, ''), 10);
        if (!score) { EOS_UI.toast('Enter a number'); return; }
        EOS.post('/dictionary/api/progress/log-test', { score: score }).then(function(d) {
            if (d && d.ok) { EOS_UI.toast('Logged ' + score + ' words'); loadProgress(); }
            else { EOS_UI.toast((d && d.error) || 'Failed'); }
        }).catch(function() { EOS_UI.toast('Failed'); });
    });
}

// ── Vocabulary Expansion Loop ──
var _prodWords = [];

function _loopCard(inner) {
    return '<div class="loop-card" style="border:1px solid var(--border);border-radius:10px;' +
        'padding:12px;margin:8px 0;background:var(--surface,transparent)">' + inner + '</div>';
}

// Today tab — daily breadth feed + words harvested from reading.
function loadLoop() {
    var area = document.getElementById('loop-area');
    area.innerHTML = '<div style="opacity:.6;padding:20px">Loading…</div>';
    Promise.all([
        EOS.api('/dictionary/api/feed').catch(function(){ return {words: []}; }),
        EOS.api('/dictionary/api/harvest/inbox').catch(function(){ return {inbox: []}; })
    ]).then(function(res) {
        var feed = res[0] || {}, inbox = res[1] || {};
        if (feed.enabled === false) {
            area.innerHTML = '<div style="opacity:.75;padding:20px;line-height:1.6">' +
                '<strong>Vocabulary loop is off.</strong><br>Enable <code>feature.vocab-loop.enabled</code> ' +
                'for this app to get a daily breadth feed and words harvested from your reading.</div>';
            return;
        }
        var html = '';

        // Daily feed
        html += '<section style="margin-bottom:26px"><h3 style="margin:0 0 8px">📖 Today’s words</h3>';
        var fw = feed.words || [];
        if (!fw.length) html += '<p style="opacity:.6">Building today’s feed… check back shortly.</p>';
        fw.forEach(function(w) {
            html += _loopCard(
                '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px">' +
                  '<div><strong class="feed-word" title="Look up & save" onclick="lookupWord(' + escAttr(JSON.stringify(w.word || '')) + ')">' + esc(w.word || '') + '</strong> <span style="opacity:.55">' + esc(w.phonetic || '') + '</span> ' +
                  '<span style="opacity:.55;font-size:.85em">' + esc(w.part_of_speech || '') + '</span></div>' +
                  '<button class="eos-btn-sm" onclick="feedAdd(' + escAttr(JSON.stringify(w.word || '')) + ', this)">+ Add</button>' +
                '</div>' +
                '<div style="margin-top:4px">' + esc(w.definition || '') + '</div>' +
                (w.example ? '<div style="opacity:.75;margin-top:4px;font-style:italic">“' + esc(w.example) + '”</div>' : '') +
                (w.chinese ? '<div style="opacity:.7;margin-top:4px">' + esc(w.chinese) + '</div>' : '') +
                (w.usage_notes ? '<div style="opacity:.6;margin-top:4px;font-size:.85em">💡 ' + esc(w.usage_notes) + '</div>' : '')
            );
        });
        html += '</section>';

        // Harvested from reading
        var ib = inbox.inbox || [];
        html += '<section><h3 style="margin:0 0 8px">🌾 Harvested from your reading' +
            (ib.length ? ' (' + ib.length + ')' : '') + '</h3>';
        if (!ib.length) html += '<p style="opacity:.6">Nothing yet. When you digest an article, new words land here for review.</p>';
        ib.forEach(function(c) {
            html += _loopCard(
                '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px">' +
                  '<div><strong class="feed-word" title="Look up & save" onclick="lookupWord(' + escAttr(JSON.stringify(c.word || '')) + ')">' + esc(c.word || '') + '</strong> <span style="opacity:.55;font-size:.85em">' + esc(c.part_of_speech || '') + '</span></div>' +
                  '<div style="display:flex;gap:6px">' +
                    '<button class="eos-btn-sm" onclick="harvestResolve(' + escAttr(JSON.stringify(c.word || '')) + ', \'approve\', this)">+ Add</button>' +
                    '<button class="eos-btn-sm" style="opacity:.6" onclick="harvestResolve(' + escAttr(JSON.stringify(c.word || '')) + ', \'reject\', this)">Skip</button>' +
                  '</div>' +
                '</div>' +
                '<div style="margin-top:4px">' + esc(c.definition || '') + (c.chinese ? ' — ' + esc(c.chinese) : '') + '</div>' +
                (c.sentence ? '<div style="opacity:.7;margin-top:4px;font-style:italic">“' + esc(c.sentence) + '”</div>' : '') +
                (c.source ? '<div style="opacity:.45;margin-top:4px;font-size:.8em">from: ' + esc(c.source) + '</div>' : '')
            );
        });
        html += '</section>';

        area.innerHTML = html;
    });
}

// Practice → Writing rep — use review words in a paragraph, LLM checks active use.
function loadWriting() {
    var area = document.getElementById('writing-area');
    area.innerHTML = '<div style="opacity:.6;padding:20px">Loading…</div>';
    EOS.api('/dictionary/api/production/prompt').then(function(prod) {
        if (prod && prod.enabled === false) {
            area.innerHTML = '<div style="opacity:.75;padding:20px;line-height:1.6">' +
                '<strong>Vocabulary loop is off.</strong><br>Enable <code>feature.vocab-loop.enabled</code> to get weekly writing reps.</div>';
            return;
        }
        _prodWords = (prod && prod.words) || [];
        var html = '';
        if (!_prodWords.length) {
            html += '<p style="opacity:.6">Review a few words first — then come back to put them to active use.</p>';
        } else {
            html += '<p style="opacity:.8">Write a short paragraph that uses all of these naturally:</p>';
            html += '<div style="margin:8px 0">' + _prodWords.map(function(w) {
                return '<span class="eos-badge" style="margin:2px">' + esc(w) + '</span>';
            }).join(' ') + '</div>';
            html += '<textarea id="prod-text" rows="5" style="width:100%;box-sizing:border-box;padding:10px;' +
                'border-radius:8px;border:1px solid var(--border)" placeholder="Use the words above in a few sentences…"></textarea>';
            html += '<div style="margin-top:8px"><button class="eos-btn" onclick="productionCheck()">Check my usage</button></div>';
            html += '<div id="prod-feedback" style="margin-top:12px"></div>';
        }
        area.innerHTML = html;
    }).catch(function() { area.innerHTML = '<p style="opacity:.6">Could not load.</p>'; });
}

function feedAdd(word, btn) {
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    EOS.post('/dictionary/api/feed/add', { word: word }).then(function(d) {
        if (d && d.ok) { if (btn) btn.textContent = '✓ Added'; EOS_UI.toast('Added “' + word + '” to your deck'); }
        else { if (btn) { btn.disabled = false; btn.textContent = '+ Add'; } EOS_UI.toast((d && d.error) || 'Failed'); }
    }).catch(function() { if (btn) { btn.disabled = false; btn.textContent = '+ Add'; } EOS_UI.toast('Failed'); });
}

function harvestResolve(word, action, btn) {
    var card = btn ? btn.closest('.loop-card') : null;
    EOS.post('/dictionary/api/harvest/resolve', { word: word, action: action }).then(function(d) {
        if (d && d.ok) {
            if (card) card.remove();
            EOS_UI.toast(action === 'approve' ? 'Added “' + word + '”' : 'Skipped “' + word + '”');
        } else { EOS_UI.toast((d && d.error) || 'Failed'); }
    }).catch(function() { EOS_UI.toast('Failed'); });
}

function productionCheck() {
    var ta = document.getElementById('prod-text');
    var text = ta ? ta.value : '';
    if (!text.trim()) { EOS_UI.toast('Write something first'); return; }
    var fb = document.getElementById('prod-feedback');
    fb.innerHTML = '<div style="opacity:.6">Checking…</div>';
    EOS.post('/dictionary/api/production/check', { text: text, words: _prodWords }).then(function(d) {
        if (!d || d.error || !d.feedback) { fb.innerHTML = '<p style="opacity:.6">' + esc((d && d.error) || 'Check failed') + '</p>'; return; }
        var f = d.feedback, items = f.items || [], html = '';
        items.forEach(function(it) {
            var mark = !it.used ? '○ unused' : (it.correct ? '✓' : '✗');
            html += '<div style="padding:8px 0;border-bottom:1px solid var(--border)">' +
                '<strong>' + esc(it.word || '') + '</strong> <span style="opacity:.7">' + mark + '</span>' +
                (it.note ? '<div style="opacity:.8;margin-top:2px">' + esc(it.note) + '</div>' : '') + '</div>';
        });
        if (f.overall) html += '<div style="margin-top:10px;opacity:.85"><strong>Overall:</strong> ' + esc(f.overall) + '</div>';
        fb.innerHTML = html || '<p style="opacity:.6">No feedback.</p>';
    }).catch(function() { fb.innerHTML = '<p style="opacity:.6">Check failed</p>'; });
}

// ── Init ──
// Reveal the Today tab when the loop is enabled (cheap status, no LLM); default to it; honour deep-links.
// The "Today's words" feed supersedes the standalone Word-of-the-Day banner when the loop is on —
// show WOTD only as the lightweight fallback when the loop is off.
// Lookup links must not wait for the optional loop-status request: callers such as
// Speaking Practice expect the requested word to appear as soon as Dictionary opens.
var initialHash = (location.hash || '').replace('#', '');
if (initialHash.indexOf('lookup/') === 0) {
    var initialLookupTerm = '';
    try { initialLookupTerm = decodeURIComponent(initialHash.slice('lookup/'.length)); }
    catch (e) { initialLookupTerm = ''; }
    if (initialLookupTerm) {
        inputEl.value = initialLookupTerm;
        switchTab('lookup');
        doLookup();
    }
}
EOS.api('/dictionary/api/loop/status').then(function(d) {
    var on = d && d.enabled;
    if (on) {
        var btn = document.getElementById('tab-btn-today');
        if (btn) btn.style.display = '';
        var wotd = document.getElementById('wotd');
        if (wotd) wotd.style.display = 'none';
    } else {
        loadWotd();
    }
    var h = (location.hash || '').replace('#', '');
    if (h.indexOf('lookup/') === 0) return;
    // `#pictures` and `#pictures/<slug>`. Namespaced rather than a bare `#slug`
    // (what the standalone page used) because a bare one would collide with the
    // routes below — an animal called `review` is not the point, but `#quiz`
    // genuinely means two different things now.
    if (h === 'pictures' || h.indexOf('pictures/') === 0) {
        switchTab('pictures');
        var slug = h.indexOf('/') > -1 ? decodeURIComponent(h.slice(h.indexOf('/') + 1)) : '';
        if (slug && window.openPictureDetail) openPictureDetail(slug);
        return;
    }
    if ((h === 'today' || h === 'loop') && on) { switchTab('today'); return; }
    if (h === 'practice' || h === 'srs' || h === 'review' || h === 'quiz') {
        switchTab('practice');
        if (h === 'quiz') practiceMode('quiz');
        else if (h === 'writing') practiceMode('writing');
        return;
    }
    // Growth-first default: land on Today when the loop is on.
    if (on) switchTab('today');
}).catch(function() { loadWotd(); });
