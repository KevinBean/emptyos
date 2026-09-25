// ppt -- page logic, extracted verbatim from pages/index.html (P4 Atomic
// split, .claude/rules/multi-module-apps.md frontend pattern). Loaded at the
// same position as the old inline <script>, so global scope and load order
// vs eos.js / eos-components.js are unchanged.
var STATE = { decks: [], currentId: null, currentDeck: null, dirty: false, query: '', _previewInst: null, elements: null };
var VISUAL_STYLE_ORDER = ['studio', 'editorial', 'tech', 'luxury', 'clay', 'ink'];
var VISUAL_STYLES = {
    studio: {label: 'Studio', desc: 'Balanced dark deck with crisp accent color.'},
    editorial: {label: 'Editorial', desc: 'Publication-style serif layouts with warm paper tones.'},
    tech: {label: 'Tech', desc: 'High-contrast grid, cyan accents, and product-demo energy.'},
    luxury: {label: 'Luxury', desc: 'Refined dark palette with gold highlights.'},
    clay: {label: 'Clay', desc: 'Soft tactile palette for friendly product storytelling.'},
    ink: {label: 'Ink', desc: 'Notebook-inspired, clean, hand-drawn feeling.'},
};

function normalizeVisualStyle(value) {
    var key = String(value || 'studio').toLowerCase();
    return VISUAL_STYLES[key] ? key : 'studio';
}
function visualStyleLabel(value) {
    var key = normalizeVisualStyle(value);
    return VISUAL_STYLES[key].label;
}
function visualStyleOptionsHtml(active) {
    active = normalizeVisualStyle(active);
    return VISUAL_STYLE_ORDER.map(function(k) {
        return '<option value="'+k+'"'+(k === active ? ' selected' : '')+'>'+EOS_UI.esc(VISUAL_STYLES[k].label)+'</option>';
    }).join('');
}
function visualStyleCardsHtml(active) {
    active = normalizeVisualStyle(active);
    return '<div class="ppt-visual-grid">' + VISUAL_STYLE_ORDER.map(function(k) {
        var s = VISUAL_STYLES[k];
        return '<button type="button" class="ppt-visual-card'+(k === active ? ' active' : '')+'" data-visual-style="'+k+'" title="'+EOS_UI.escAttr(s.desc)+'">' +
            '<span class="ppt-visual-swatch is-'+k+'"></span>' +
            '<strong>'+EOS_UI.esc(s.label)+'</strong>' +
            '<span>'+EOS_UI.esc(s.desc)+'</span>' +
        '</button>';
    }).join('') + '</div>';
}

var _pptJob = null;
function setBusy(message) {
    if (!message) {
        if (_pptJob) { try { _pptJob.hide(); } catch (e) {} _pptJob = null; }
        return;
    }
    if (!_pptJob) _pptJob = EOS_UI.jobProgress({id: 'ppt'});
    _pptJob.update({stage: message});
}

async function loadElements() {
    if (STATE.elements) return STATE.elements;
    try { STATE.elements = await fetch('/ppt/api/elements').then(r => r.json()); } catch(e) { STATE.elements = null; }
    return STATE.elements;
}
function elementsCheckboxesHtml(checkedSet) {
    if (!STATE.elements) return '';
    var labels = STATE.elements.labels || {};
    return '<div class="eos-form-group"><label class="eos-form-label">AI may use these slide surfaces</label>' +
        '<div id="ppt-elements-grid" class="ppt-elements-grid">' +
        STATE.elements.all.map(function(k){
            var checked = checkedSet.has(k) ? 'checked' : '';
            var disabled = (k === 'bullets') ? 'disabled' : '';
            return '<label class="ppt-element-choice">' +
                '<input type="checkbox" name="elem" value="'+k+'" '+checked+' '+disabled+'> ' +
                EOS_UI.esc(labels[k] || k) +
                '</label>';
        }).join('') +
        '</div>' +
        '<div class="ppt-field-hint">Bullets are always on. Disable surfaces you don\'t want the AI to introduce.</div>' +
        '</div>';
}
function readElementChoice() {
    var nodes = document.querySelectorAll('#ppt-elements-grid input[name=elem]');
    var picked = [];
    nodes.forEach(function(n){ if (n.checked) picked.push(n.value); });
    if (picked.indexOf('bullets') < 0) picked.unshift('bullets');
    return picked;
}

var _appSettings = EOS_UI.settingsPanel({
    id: 'ppt-settings-panel',
    title: 'Presentations',
    fields: [
        {key: 'ppt.default_theme', label: 'Default theme', type: 'select', default: 'dark',
         options: [{value:'dark',label:'Dark'},{value:'light',label:'Light'},{value:'mono',label:'Mono'}]},
        {key: 'ppt.default_aspect', label: 'Default aspect', type: 'select', default: '16:9',
         options: [{value:'16:9',label:'16:9'},{value:'4:3',label:'4:3'}]},
        {key: 'ppt.default_visual_style', label: 'Default visual style', type: 'select', default: 'studio',
         options: VISUAL_STYLE_ORDER.map(function(k){ return {value:k, label:VISUAL_STYLES[k].label}; })},
        {key: 'ppt.embed_base', label: 'Embed host (live)', type: 'text', default: '',
         hint: "Prepend to ![embed: /journal/] etc. Empty = current host. Override per-deck via 'embed_base:' in frontmatter."},
        {key: 'ppt.export_embed_base', label: 'Embed host (exported HTML)', type: 'text', default: '',
         hint: "A publicly-reachable URL for this daemon so exported bundles don't embed localhost. Falls back to ppt.embed_base if blank; if both are blank, Export HTML warns instead."},
    ],
    // NOTE: hand-listed (not `app: 'ppt'`) because default_visual_style's options
    // are derived from VISUAL_STYLE_ORDER at runtime — a manifest can't express
    // that. Keep this list in sync with manifest.toml; check-settings-panel-drift.py
    // enforces it.
});
function openAppSettings() { _appSettings.open(); }

var _route = EOS_UI.hashRoute({
    onShow: function(id) { showDeck(id); },
    onHide: function() { showEmpty(); },
});

async function load() {
    var resp = await fetch('/ppt/api/decks');
    STATE.decks = await resp.json();
    renderList();
}
function renderList() {
    var el = document.getElementById('deck-list');
    var countEl = document.getElementById('deck-count');
    if (countEl) countEl.textContent = String(STATE.decks.length);
    var q = (STATE.query || '').toLowerCase();
    var decks = STATE.decks.filter(function(d) {
        if (!q) return true;
        return [d.title, d.id, d.theme, d.aspect, d.visual_style, visualStyleLabel(d.visual_style)].join(' ').toLowerCase().indexOf(q) >= 0;
    });
    if (!STATE.decks.length) {
        el.innerHTML = '<div class="ppt-empty"><p>No decks yet.</p><button class="eos-btn eos-btn-primary" onclick="newDeck()" title="Create a new markdown deck">+ New deck</button></div>';
        return;
    }
    if (!decks.length) {
        el.innerHTML = '<div class="ppt-empty"><p>No matching decks.</p></div>';
        return;
    }
    el.innerHTML = decks.map(function(d){
        var cls = 'ppt-deck-row' + (d.id === STATE.currentId ? ' active' : '') + (d.system ? ' is-system' : '');
        var pin = d.system ? '<span class="ppt-system-mark" title="Tutorial deck; cannot be deleted">&#128214;</span>' : '';
        var del = d.system ? '' : '<button class="eos-row-del" data-del="'+EOS_UI.escAttr(d.id)+'" aria-label="Delete deck" title="Delete this deck"></button>';
        return '<div class="'+cls+'" data-id="'+EOS_UI.escAttr(d.id)+'" role="button" tabindex="0" title="Open '+EOS_UI.escAttr(d.title)+'">' +
                 '<div class="title">'+pin+'<span>'+EOS_UI.esc(d.title)+'</span></div>' +
                 '<div class="meta">' +
                    '<span class="ppt-chip">'+d.slide_count+' slides</span>' +
                    '<span class="ppt-chip">'+EOS_UI.esc(d.theme)+'</span>' +
                    '<span class="ppt-chip">'+EOS_UI.esc(d.aspect || '16:9')+'</span>' +
                    '<span class="ppt-chip">'+EOS_UI.esc(visualStyleLabel(d.visual_style))+'</span>' +
                 '</div>' +
                 del +
               '</div>';
    }).join('');
    el.querySelectorAll('.ppt-deck-row').forEach(function(r){
        r.addEventListener('click', function(e){
            if (e.target && e.target.classList.contains('eos-row-del')) return;
            _route.set(r.dataset.id);
        });
        r.addEventListener('keydown', function(e){
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                _route.set(r.dataset.id);
            }
        });
    });
    el.querySelectorAll('.eos-row-del').forEach(function(b){
        b.addEventListener('click', function(e){
            e.stopPropagation();
            deleteDeck(b.dataset.del);
        });
    });
}

async function deleteDeck(id) {
    var deck = STATE.decks.find(function(d){ return d.id === id; });
    var label = deck ? deck.title : id;
    var ok = await EOS_UI.confirmDelete({label: label});
    if (!ok) return;
    var r = await fetch('/ppt/api/decks/' + encodeURIComponent(id), {method: 'DELETE'});
    var data = await r.json();
    if (data.error) { EOS_UI.toast(data.error, false); return; }
    EOS_UI.toast('Deleted: ' + label);
    if (STATE.currentId === id) { STATE.currentId = null; _route.clear(); }
    await load();
}

// The generate-from-plan provenance chip attaches to #edit-pane and nothing
// retracts it on its own (a deck GET is a different endpoint), so it is cleared
// whenever the pane shows a deck other than the one that was just generated.
var _generatedDeckId = null;
function clearGenerateProvenance() {
    var pane = document.getElementById('edit-pane');
    var chip = pane && pane.nextElementSibling;
    if (chip && chip.classList.contains('eos-auto-provenance')) chip.remove();
    if (pane) pane.removeAttribute('data-prov-path');
}
async function showDeck(id) {
    if (id !== _generatedDeckId) clearGenerateProvenance();
    STATE.currentId = id;
    renderList();
    var r = await fetch('/ppt/api/decks/' + encodeURIComponent(id));
    var deck = await r.json();
    if (deck.error) { EOS_UI.toast(deck.error, false); return; }
    STATE.currentDeck = deck;
    document.getElementById('empty').hidden = true;
    document.getElementById('toolbar').hidden = false;
    document.getElementById('edit-pane').hidden = false;
    document.getElementById('cur-title').textContent = deck.frontmatter.title || id;
    document.getElementById('cur-meta').textContent = (deck.slides || []).length + ' slides · ' + deck.theme + ' · ' + deck.aspect + ' · ' + visualStyleLabel(deck.visual_style);
    var nOn = deck.frontmatter.narration;
    var nEnabled = !!nOn && String(nOn).toLowerCase() !== 'false' && String(nOn).toLowerCase() !== '0' && String(nOn).toLowerCase() !== 'no';
    var tog = document.getElementById('narration-toggle');
    if (tog) tog.checked = nEnabled;
    STATE.slideCount = (deck.slides || []).length;
    renderStyleControls();
    if (deck.narration_stale) {
        EOS_UI.toast('Narration audio is stale — re-run 🔊 Narrate to re-pair audio with the current slides', 'warning');
    }
    var ed = document.getElementById('md-editor');
    ed.value = deck.raw;
    STATE.dirty = false;
    updateDeckStatus();
    renderPreview(deck);
    ed.oninput = function() {
        STATE.dirty = true;
        updateDeckStatus();
        clearTimeout(window._ppt_t);
        window._ppt_t = setTimeout(function() { rePreviewFromEditor(); }, 350);
    };
}
function showEmpty() {
    clearGenerateProvenance();
    STATE.currentId = null;
    STATE.currentDeck = null;
    renderList();
    document.getElementById('empty').hidden = false;
    document.getElementById('toolbar').hidden = true;
    document.getElementById('edit-pane').hidden = true;
    updateDeckStatus();
}

function updateDeckStatus() {
    var dirty = document.getElementById('dirty-status');
    if (dirty) {
        dirty.textContent = STATE.dirty ? 'Unsaved' : 'Saved';
        dirty.classList.toggle('dirty', !!STATE.dirty);
    }
    var status = document.querySelector('.ppt-status');
    if (status) status.classList.toggle('dirty', !!STATE.dirty);
}

function renderStyleControls() {
    var deck = STATE.currentDeck || {};
    document.querySelectorAll('[data-style-field]').forEach(function(btn) {
        var field = btn.getAttribute('data-style-field');
        var val = btn.getAttribute('data-style-value');
        btn.classList.toggle('active', String(deck[field] || '') === val);
    });
    var visualStyle = normalizeVisualStyle(deck.visual_style);
    var visualName = document.getElementById('visual-style-name');
    var visualDot = document.getElementById('visual-style-dot');
    if (visualName) visualName.textContent = visualStyleLabel(visualStyle);
    if (visualDot) visualDot.className = 'ppt-visual-dot is-' + visualStyle;
}

function _frontmatterValue(value) {
    var s = String(value == null ? '' : value);
    return /[:#\[\]{}]/.test(s) ? '"' + s.replace(/"/g, '\\"') + '"' : s;
}

function updateRawFrontmatterField(raw, field, value) {
    var newline = raw.indexOf('\r\n') >= 0 ? '\r\n' : '\n';
    var lines = raw.split(/\r?\n/);
    var nextLine = field + ': ' + _frontmatterValue(value);
    if (lines[0] === '---') {
        var close = -1;
        for (var i = 1; i < lines.length; i++) {
            if (lines[i].trim() === '---') { close = i; break; }
        }
        if (close > 0) {
            var replaced = false;
            var re = new RegExp('^' + field + '\\s*:');
            for (var j = 1; j < close; j++) {
                if (re.test(lines[j])) {
                    lines[j] = nextLine;
                    replaced = true;
                    break;
                }
            }
            if (!replaced) lines.splice(close, 0, nextLine);
            return lines.join(newline);
        }
    }
    return ['---', nextLine, '---', '', raw].join(newline);
}

function splitDeckRaw(raw) {
    var newline = raw.indexOf('\r\n') >= 0 ? '\r\n' : '\n';
    var lines = raw.split(/\r?\n/);
    var frontmatter = '';
    var body = raw || '';
    if (lines[0] === '---') {
        for (var i = 1; i < lines.length; i++) {
            if (lines[i].trim() === '---') {
                frontmatter = lines.slice(0, i + 1).join(newline);
                body = lines.slice(i + 1).join(newline).replace(/^\s+/, '');
                break;
            }
        }
    }
    // -{3,} mirrors the server's _HR_RE (parser.py) so the board's slide
    // count always matches what parse_deck renders.
    var slides = body.split(/\r?\n\s*-{3,}\s*\r?\n/g).map(function(part) {
        return {md: part.trim()};
    }).filter(function(s) { return s.md; });
    if (!slides.length) slides = [{md: '# Untitled slide'}];
    return {frontmatter: frontmatter, slides: slides, newline: newline};
}

function buildDeckRaw(parts) {
    var newline = parts.newline || '\n';
    var body = (parts.slides || []).map(function(s) {
        return String((s && s.md) || '').trim() || '# Untitled slide';
    }).join(newline + newline + '---' + newline + newline);
    return (parts.frontmatter ? parts.frontmatter + newline + newline : '') + body + newline;
}

function slideTitle(md, index) {
    var lines = String(md || '').split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (!line) continue;
        line = line.replace(/^#{1,6}\s*/, '').replace(/^[-*]\s*/, '').replace(/^>\s*/, '');
        line = line.replace(/!\[[^\]]*\]\([^)]+\)/g, '').replace(/!\[\[[^\]]+\]\]/g, '');
        line = line.replace(/\*\*|__|`/g, '').trim();
        if (line) return line.slice(0, 80);
    }
    return 'Slide ' + (index + 1);
}

function slideBodyLineCount(md) {
    return String(md || '').split(/\r?\n/).filter(function(line) {
        return line.trim() && !/^#{1,6}\s+/.test(line.trim());
    }).length;
}

async function setDeckStyle(field, value) {
    if (!STATE.currentId || !field) return;
    if (field === 'visual_style') value = normalizeVisualStyle(value);
    var dirtyBefore = STATE.dirty;
    var ed = document.getElementById('md-editor');
    if (ed) ed.value = updateRawFrontmatterField(ed.value, field, value);
    if (STATE.currentDeck) STATE.currentDeck[field] = value;
    STATE.dirty = !!dirtyBefore;
    renderStyleControls();
    updateDeckStatus();
    try {
        var body = {};
        body[field] = value;
        var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId), {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        var data = await r.json();
        if (data.error) { EOS_UI.toast(data.error, false); return; }
        await rePreviewFromEditor();
        await load();
        if (!dirtyBefore) STATE.dirty = false;
        updateDeckStatus();
    } catch (e) {
        EOS_UI.toast('Style update failed: ' + (e.message || e), false);
    }
}

function openVisualStyleLab() {
    if (!STATE.currentId) return;
    var active = normalizeVisualStyle(STATE.currentDeck && STATE.currentDeck.visual_style);
    var body =
        '<div class="eos-form-group">' +
            '<label class="eos-form-label">Visual system</label>' +
            visualStyleCardsHtml(active) +
        '</div>' +
        '<div class="eos-form-actions">' +
            '<button class="eos-btn" onclick="EOS_UI.closeModal()" title="Close without changing visual style">Cancel</button>' +
        '</div>';
    var modal = EOS_UI.modal({title: 'Visual system', body: body, width: '760px'});
    modal.querySelectorAll('[data-visual-style]').forEach(function(btn) {
        btn.onclick = function() {
            var style = normalizeVisualStyle(btn.dataset.visualStyle);
            EOS_UI.closeModal();
            setDeckStyle('visual_style', style);
        };
    });
}

function openSlideBoard() {
    if (!STATE.currentId) return;
    var ed = document.getElementById('md-editor');
    var parts = splitDeckRaw(ed ? ed.value : (STATE.currentDeck && STATE.currentDeck.raw) || '');
    STATE.slideBoard = {parts: parts, selected: 0, dragging: null};
    var body =
        '<div class="ppt-slide-board" id="ppt-slide-board">' +
            '<div class="ppt-slide-rail">' +
                '<div class="ppt-slide-rail-head">' +
                    '<strong>Slides</strong>' +
                    '<button type="button" class="eos-btn eos-btn-sm" id="ppt-slide-add" title="Add a slide after the current slide">+ Slide</button>' +
                '</div>' +
                '<div class="ppt-slide-list" id="ppt-slide-list"></div>' +
            '</div>' +
            '<div class="ppt-slide-editor">' +
                '<div class="ppt-slide-editor-head">' +
                    '<strong id="ppt-slide-edit-title">Slide</strong>' +
                    '<span class="ppt-pane-hint" id="ppt-slide-edit-meta"></span>' +
                '</div>' +
                '<textarea id="ppt-slide-md" spellcheck="false"></textarea>' +
                '<div class="ppt-slide-edit-actions">' +
                    '<div class="left">' +
                        '<button type="button" class="eos-btn" id="ppt-slide-duplicate" title="Duplicate this slide">Duplicate</button>' +
                        '<button type="button" class="eos-btn eos-btn-danger" id="ppt-slide-delete" title="Delete this slide">Delete</button>' +
                    '</div>' +
                    '<div class="right">' +
                        '<button type="button" class="eos-btn" id="ppt-slide-apply" title="Apply slide edits to the markdown editor">Apply</button>' +
                        '<button type="button" class="eos-btn eos-btn-primary" id="ppt-slide-save" title="Apply slide edits and save the deck">Save deck</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</div>';
    var modal = EOS_UI.modal({title: 'Slide board', body: body, width: '1040px'});
    renderSlideBoard();
    document.getElementById('ppt-slide-add').onclick = function() { addBoardSlide(); };
    document.getElementById('ppt-slide-duplicate').onclick = function() { duplicateBoardSlide(); };
    document.getElementById('ppt-slide-delete').onclick = function() { deleteBoardSlide(); };
    document.getElementById('ppt-slide-apply').onclick = function() { applySlideBoard(); EOS_UI.closeModal(); };
    document.getElementById('ppt-slide-save').onclick = async function() {
        applySlideBoard();
        EOS_UI.closeModal();
        await saveCurrent();
    };
    var ta = document.getElementById('ppt-slide-md');
    ta.oninput = function() {
        var st = STATE.slideBoard;
        if (!st) return;
        st.parts.slides[st.selected].md = ta.value;
        renderSlideCards();
        syncSlideEditorMeta();
    };
    if (modal) modal.querySelector('#ppt-slide-md').focus();
}

function renderSlideBoard() {
    renderSlideCards();
    renderSlideEditor();
}

function renderSlideCards() {
    var st = STATE.slideBoard;
    var host = document.getElementById('ppt-slide-list');
    if (!st || !host) return;
    host.innerHTML = st.parts.slides.map(function(slide, i) {
        var title = slideTitle(slide.md, i);
        var cls = 'ppt-slide-card' + (i === st.selected ? ' active' : '');
        var lineCount = Math.min(3, Math.max(1, slideBodyLineCount(slide.md)));
        var lines = '';
        for (var j = 0; j < lineCount; j++) lines += '<span class="ppt-slide-thumb-line"></span>';
        return '<div class="'+cls+'" draggable="true" data-i="'+i+'" role="button" tabindex="0" title="Edit slide '+(i+1)+'">' +
            '<span class="ppt-slide-num">'+String(i + 1).padStart(2, '0')+'</span>' +
            '<div class="ppt-slide-thumb">' +
                '<span class="ppt-slide-thumb-title">'+EOS_UI.esc(title)+'</span>' +
                lines +
            '</div>' +
            '<div class="ppt-slide-card-actions">' +
                '<button type="button" class="ppt-icon-button" data-act="up" title="Move this slide up">&#9650;</button>' +
                '<button type="button" class="ppt-icon-button" data-act="down" title="Move this slide down">&#9660;</button>' +
            '</div>' +
        '</div>';
    }).join('');
    host.querySelectorAll('.ppt-slide-card').forEach(function(card) {
        var i = parseInt(card.dataset.i, 10);
        card.onclick = function(e) {
            if (e.target.closest('[data-act]')) return;
            selectBoardSlide(i);
        };
        card.onkeydown = function(e) {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                selectBoardSlide(i);
            }
        };
        card.ondragstart = function(e) {
            STATE.slideBoard.dragging = i;
            card.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', String(i));
        };
        card.ondragend = function() {
            card.classList.remove('dragging');
            if (STATE.slideBoard) STATE.slideBoard.dragging = null;
        };
        card.ondragover = function(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        };
        card.ondrop = function(e) {
            e.preventDefault();
            var from = STATE.slideBoard ? STATE.slideBoard.dragging : null;
            if (from == null) from = parseInt(e.dataTransfer.getData('text/plain'), 10);
            moveBoardSlide(from, i);
        };
    });
    host.querySelectorAll('[data-act]').forEach(function(btn) {
        btn.onclick = function(e) {
            e.stopPropagation();
            var card = btn.closest('.ppt-slide-card');
            var i = parseInt(card.dataset.i, 10);
            moveBoardSlide(i, btn.dataset.act === 'up' ? i - 1 : i + 1);
        };
    });
}

function renderSlideEditor() {
    var st = STATE.slideBoard;
    if (!st) return;
    var slide = st.parts.slides[st.selected];
    var ta = document.getElementById('ppt-slide-md');
    if (ta) ta.value = slide ? slide.md : '';
    var title = document.getElementById('ppt-slide-edit-title');
    if (title) title.textContent = 'Slide ' + (st.selected + 1) + ' / ' + st.parts.slides.length;
    syncSlideEditorMeta();
}

function syncSlideEditorMeta() {
    var st = STATE.slideBoard;
    var meta = document.getElementById('ppt-slide-edit-meta');
    if (!st || !meta) return;
    var md = (st.parts.slides[st.selected] || {}).md || '';
    meta.textContent = slideTitle(md, st.selected);
}

function selectBoardSlide(index) {
    var st = STATE.slideBoard;
    if (!st) return;
    st.selected = Math.max(0, Math.min(st.parts.slides.length - 1, index));
    renderSlideBoard();
}

function moveBoardSlide(from, to) {
    var st = STATE.slideBoard;
    if (!st || from === to || from < 0 || to < 0 || from >= st.parts.slides.length || to >= st.parts.slides.length) return;
    var item = st.parts.slides.splice(from, 1)[0];
    st.parts.slides.splice(to, 0, item);
    st.selected = to;
    renderSlideBoard();
}

function addBoardSlide() {
    var st = STATE.slideBoard;
    if (!st) return;
    var insertAt = st.selected + 1;
    st.parts.slides.splice(insertAt, 0, {md: '## New slide\n\n- First point'});
    st.selected = insertAt;
    renderSlideBoard();
}

function duplicateBoardSlide() {
    var st = STATE.slideBoard;
    if (!st) return;
    var current = st.parts.slides[st.selected] || {md: '## New slide'};
    st.parts.slides.splice(st.selected + 1, 0, {md: current.md});
    st.selected += 1;
    renderSlideBoard();
}

function deleteBoardSlide() {
    var st = STATE.slideBoard;
    if (!st || st.parts.slides.length <= 1) {
        EOS_UI.toast('Keep at least one slide', false);
        return;
    }
    st.parts.slides.splice(st.selected, 1);
    st.selected = Math.max(0, Math.min(st.selected, st.parts.slides.length - 1));
    renderSlideBoard();
}

function applySlideBoard() {
    var st = STATE.slideBoard;
    var ed = document.getElementById('md-editor');
    if (!st || !ed) return;
    ed.value = buildDeckRaw(st.parts);
    STATE.dirty = true;
    updateDeckStatus();
    rePreviewFromEditor();
}

function renderPreview(deck) {
    var pv = document.getElementById('preview');
    pv.innerHTML = '<div id="deck-preview"></div>';
    if (STATE._previewInst && STATE._previewInst.destroy) {
        try { STATE._previewInst.destroy(); } catch(e) {}
    }
    STATE._previewInst = EOS_DECK.create(document.getElementById('deck-preview'), {
        mode: 'manual',
        slides: (deck.slides || []).map(function(s){ return {html:s.html, notes:s.notes, audio_url:s.audio_url}; }),
        theme: deck.theme,
        aspect: deck.aspect,
        visualStyle: deck.visual_style,
    });
    var ps = document.getElementById('preview-status');
    if (ps) ps.textContent = (deck.theme || 'dark') + ' · ' + (deck.aspect || '16:9') + ' · ' + visualStyleLabel(deck.visual_style);
}
async function rePreviewFromEditor() {
    if (!STATE.currentId) return;
    var raw = document.getElementById('md-editor').value;
    var ps = document.getElementById('preview-status');
    if (ps) ps.textContent = 'Rendering';
    // Round-trip through the parser so preview matches what'll be saved.
    var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/preview', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({raw: raw}),
    });
    if (r.ok) {
        var deck = await r.json();
        if (STATE.currentDeck) {
            STATE.currentDeck.theme = deck.theme;
            STATE.currentDeck.aspect = deck.aspect;
            STATE.currentDeck.visual_style = deck.visual_style;
        }
        renderStyleControls();
        renderPreview(deck);
    } else if (ps) {
        ps.textContent = 'Preview failed';
    }
}

async function saveCurrent() {
    if (!STATE.currentId) return;
    var raw = document.getElementById('md-editor').value;
    var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId), {
        method: 'PUT', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({raw: raw}),
    });
    var data = await r.json();
    if (data.error) { EOS_UI.toast(data.error, false); return; }
    EOS_UI.toast('Saved');
    STATE.dirty = false;
    updateDeckStatus();
    await load();
}
async function exportCurrent() {
    if (!STATE.currentId) return;
    var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/export', {method:'POST'});
    var data = await r.json();
    if (data.error) { EOS_UI.toast(data.error, false); return; }
    if (data.warning) EOS_UI.toast(data.warning);
    EOS_UI.toast('Exported → ' + data.rel);
}
async function exportPdf() {
    if (!STATE.currentId) return;
    EOS_UI.toast('Rendering PDF (one page per slide) — this takes a few seconds…');
    var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/export?format=pdf', {method:'POST'});
    var data = await r.json();
    if (data.error) { EOS_UI.toast(data.error, false); return; }
    EOS_UI.toast('Exported ' + data.slides + ' slide(s) → ' + data.rel);
}
function presentCurrent() {
    if (!STATE.currentId) return;
    window.open('/ppt/pages/present.html?id=' + encodeURIComponent(STATE.currentId), '_blank');
}
async function toPodcast() {
    if (!STATE.currentId) return;
    var ok = await EOS_UI.confirm({
        message: 'Generate a two-host podcast episode from this deck? Uses Emma + Michael voices and the en_mf preset. Takes ~1 min per minute of audio.',
        action: 'Generate',
        danger: false,
    });
    if (!ok) return;
    EOS_UI.toast('Generating podcast (this takes a while)…');
    var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/to-podcast', {method: 'POST'});
    var data = await r.json();
    if (data.error) { EOS_UI.toast(data.error, false); return; }
    EOS_UI.toast('Episode ready → /podcast/');
}

async function toPost() {
    if (!STATE.currentId) return;
    var ok = await EOS_UI.confirm({
        message: 'Convert this deck into a draft blog post under 30_Resources/Published/? It will appear in the publish app as an unpublished draft.',
        action: 'Convert',
        danger: false,
    });
    if (!ok) return;
    var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/to-post', {method: 'POST'});
    var data = await r.json();
    if (data.error) { EOS_UI.toast(data.error, false); return; }
    EOS_UI.toast('Draft post created → /publish/');
}

function newFromCanvas() {
    var fields = [
        {key: 'board_id', label: 'Canvas board id', type: 'text', placeholder: 'inbox'},
        {key: 'title', label: 'Deck title (optional — defaults to "Deck from <board>")', type: 'text'},
    ];
    EOS_UI.formModal('From canvas', fields, async function(vals) {
        var board = (vals.board_id || '').trim() || 'inbox';
        var r = await fetch('/ppt/api/from-canvas', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({board_id: board, title: vals.title || ''}),
        });
        var data = await r.json();
        if (data.error) { EOS_UI.toast(data.error, false); return; }
        EOS_UI.toast('Drafted ' + (data.slides || 0) + ' slides');
        await load();
        _route.set(data.id);
    });
}

async function regenerateDeck() {
    if (!STATE.currentId) return;
    await loadElements();
    var deck = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId)).then(r => r.json());
    var stored = (deck && deck.frontmatter && deck.frontmatter.allowed_elements) || (STATE.elements && STATE.elements.defaults) || [];
    var slideCount = (deck && deck.slides) ? deck.slides.length : 0;

    // Capture the editor's current text selection BEFORE the modal steals focus.
    var ed = document.getElementById('md-editor');
    var selStart = ed ? ed.selectionStart : 0;
    var selEnd   = ed ? ed.selectionEnd   : 0;
    var selectedText = (ed && selEnd > selStart) ? ed.value.substring(selStart, selEnd) : '';

    var slideOptions = '';
    for (var i = 0; i < slideCount; i++) {
        slideOptions += '<option value="'+i+'">Slide ' + (i+1) + (deck.slides[i].md ? ' — ' + EOS_UI.esc(deck.slides[i].md.split('\n')[0].slice(0,60)) : '') + '</option>';
    }
    var selScopeOpt = selectedText ? '<option value="selection">Selected text in editor (' + selectedText.length + ' chars)</option>' : '';

    var body =
        '<div class="eos-form-group"><label class="eos-form-label">What to regenerate</label>' +
            '<select id="ppt-regen-scope" class="eos-form-input">' +
                '<option value="whole">Whole deck</option>' +
                (slideCount ? '<option value="slide">One slide…</option>' : '') +
                selScopeOpt +
            '</select></div>' +
        '<div class="eos-form-group" id="ppt-regen-slide-row" style="display:none">' +
            '<label class="eos-form-label">Which slide</label>' +
            '<select id="ppt-regen-slide" class="eos-form-input">' + slideOptions + '</select>' +
        '</div>' +
        '<div class="eos-form-group"><label class="eos-form-label">Direction (what should change?)</label>' +
            '<textarea id="ppt-regen-direction" class="eos-form-input" rows="3" placeholder="e.g. shorten · simplify for non-technical audience · add a code slide · more vivid metaphor · sharper opening line"></textarea></div>' +
        elementsCheckboxesHtml(new Set(stored.length ? stored : (STATE.elements && STATE.elements.defaults) || [])) +
        '<div class="eos-form-actions">' +
            '<button class="eos-btn" onclick="EOS_UI.closeModal()" title="Close without regenerating">Cancel</button>' +
            '<button class="eos-btn eos-btn-primary" id="ppt-regen-go" title="Regenerate the selected deck content">Regenerate</button>' +
        '</div>';
    EOS_UI.modal({title: 'Regenerate', body: body});

    setTimeout(function(){
        var scopeSel = document.getElementById('ppt-regen-scope');
        var slideRow = document.getElementById('ppt-regen-slide-row');
        function syncScope() {
            slideRow.style.display = (scopeSel.value === 'slide') ? '' : 'none';
        }
        scopeSel.onchange = syncScope; syncScope();

        var dirEl = document.getElementById('ppt-regen-direction');
        if (dirEl) dirEl.focus();

        document.getElementById('ppt-regen-go').onclick = async function() {
            var direction = (document.getElementById('ppt-regen-direction').value || '').trim();
            if (!direction) { EOS_UI.toast('Direction is required', false); return; }
            var scope = scopeSel.value;
            var elements = readElementChoice();
            var payload = {direction: direction, allowed_elements: elements, scope: scope};
            if (scope === 'slide') {
                payload.slide_index = parseInt(document.getElementById('ppt-regen-slide').value, 10);
            } else if (scope === 'selection') {
                payload.selection = selectedText;
                payload.selection_start = selStart;
                payload.selection_end = selEnd;
            }
            EOS_UI.closeModal();
            setBusy('Regenerating ' + (scope === 'whole' ? 'deck' : scope) + '…');
            try {
                var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/regenerate', {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify(payload),
                });
                var data = await r.json();
                if (data.error) { EOS_UI.toast(data.error, false); return; }
                await showDeck(STATE.currentId);
                load();
                EOS_UI.toast('Regenerated · ' + (data.slides || '?') + ' slides');
            } catch (e) {
                EOS_UI.toast('Regenerate failed: ' + (e.message || e), false);
            } finally {
                setBusy(null);
            }
        };
    }, 30);
}

async function resolveImages() {
    if (!STATE.currentId) return;
    var ok = await EOS_UI.confirm({
        message: 'Resolve every image placeholder in this deck? Generates missing images via AI (image:), pulls existing ones from the vault (vault:), or screenshots a URL (screenshot:). AI generation can take ~30s per image.',
        action: 'Resolve',
        danger: false,
    });
    if (!ok) return;
    setBusy('Resolving images… (~30s per image)');
    try {
        var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/resolve-images', {method: 'POST'});
        var data = await r.json();
        if (data.error) { EOS_UI.toast(data.error, false); return; }
        var msg = (data.resolved || 0) + ' resolved';
        if ((data.errors || []).length) msg += ', ' + data.errors.length + ' failed';
        EOS_UI.toast(msg);
        await showDeck(STATE.currentId);
    } catch (e) {
        EOS_UI.toast('Resolve failed: ' + (e.message || e), false);
    } finally {
        setBusy(null);
    }
}

function _ensurePickerStyle() {
    if (document.getElementById('ppt-picker-style')) return;
    if (!document.getElementById('ppt-picker-fonts')) {
        var l1 = document.createElement('link'); l1.rel = 'preconnect'; l1.href = 'https://fonts.googleapis.com';
        var l2 = document.createElement('link'); l2.rel = 'preconnect'; l2.href = 'https://fonts.gstatic.com'; l2.crossOrigin = '';
        var l3 = document.createElement('link'); l3.rel = 'stylesheet'; l3.id = 'ppt-picker-fonts';
        l3.href = 'https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,200;0,9..144,400;0,9..144,600;1,9..144,400&family=JetBrains+Mono:wght@400;500;600&display=swap';
        document.head.appendChild(l1); document.head.appendChild(l2); document.head.appendChild(l3);
    }
    var s = document.createElement('style'); s.id = 'ppt-picker-style';
    s.textContent = [
        '.ppt-pick { font-family: "Fraunces", "Times New Roman", serif; padding: 4px 6px 8px; color: var(--text); }',
        '.ppt-pick__eyebrow { font: 600 10px/1 "JetBrains Mono", monospace; letter-spacing: .28em; text-transform: uppercase; color: var(--text-muted); }',
        '.ppt-pick__title { font: 400 38px/1.05 "Fraunces", serif; font-variation-settings: "opsz" 144, "SOFT" 30; letter-spacing: -.02em; margin: 8px 0 4px; }',
        '.ppt-pick__title em { font-style: italic; font-variation-settings: "opsz" 144, "SOFT" 100; }',
        '.ppt-pick__lede { font: 400 14.5px/1.45 "Fraunces", serif; color: var(--text-muted); max-width: 42ch; margin: 0 0 22px; }',
        '.ppt-pick__rule { height: 1px; background: var(--border); margin: 16px 0; transform-origin: left; animation: pp-rule .6s ease-out both; }',
        '@keyframes pp-rule { from { transform: scaleX(0); } to { transform: scaleX(1); } }',
        '.ppt-pick__layout { display: grid; grid-template-columns: 196px 1fr; gap: 28px; align-items: start; }',
        '.ppt-pick__every { border: 1px solid var(--border); padding: 18px 16px 16px; cursor: pointer; transition: background .18s, border-color .18s; position: relative; }',
        '.ppt-pick__every:hover { border-color: var(--text); }',
        '.ppt-pick__every[data-on=true] { border-color: var(--text); background: color-mix(in srgb, var(--text) 4%, transparent); }',
        '.ppt-pick__every-tag { font: 600 9px/1 "JetBrains Mono", monospace; letter-spacing: .28em; text-transform: uppercase; color: var(--text-muted); }',
        '.ppt-pick__every-num { font: 200 64px/1 "Fraunces", serif; font-variation-settings: "opsz" 144; margin: 8px 0 2px; letter-spacing: -.04em; }',
        '.ppt-pick__every-foot { font: italic 400 13px/1 "Fraunces", serif; color: var(--text-muted); }',
        '.ppt-pick__or { font: italic 400 12px/1 "Fraunces", serif; color: var(--text-muted); margin: 0 0 8px; padding-left: 2px; }',
        '.ppt-pick__or::before { content: "—"; margin-right: 8px; color: var(--border); }',
        '.ppt-pick__ladder { display: grid; grid-template-columns: repeat(auto-fill, minmax(40px, 1fr)); gap: 4px; }',
        '.ppt-pick__num { font: 500 12.5px/1 "JetBrains Mono", monospace; padding: 11px 0 10px; text-align: center; border: 1px solid var(--border); cursor: pointer; transition: background .12s, color .12s, border-color .12s; user-select: none; opacity: 0; animation: pp-num .35s cubic-bezier(.2,.6,.2,1) forwards; }',
        '@keyframes pp-num { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }',
        '.ppt-pick__num:hover { border-color: var(--text); }',
        '.ppt-pick__num[data-on=true] { background: var(--text); color: var(--bg); border-color: var(--text); }',
        '.ppt-pick__overwrite { display: flex; gap: 10px; align-items: flex-start; margin: 20px 0 0; padding: 12px 14px; border-left: 2px solid var(--border); cursor: pointer; }',
        '.ppt-pick__overwrite:hover { border-left-color: var(--text); }',
        '.ppt-pick__overwrite input { margin-top: 3px; accent-color: var(--text); }',
        '.ppt-pick__overwrite-text { font: 400 13px/1.4 "Fraunces", serif; }',
        '.ppt-pick__overwrite-text em { font: italic 400 13px/1.4 "Fraunces", serif; color: var(--text-muted); display: block; margin-top: 2px; }',
        '.ppt-pick__actions { display: flex; gap: 0; justify-content: flex-end; margin-top: 28px; padding-top: 18px; border-top: 1px solid var(--border); }',
        '.ppt-pick__btn { font: 500 10.5px/1 "JetBrains Mono", monospace; letter-spacing: .22em; text-transform: uppercase; padding: 13px 22px; border: 1px solid transparent; background: transparent; color: var(--text-muted); cursor: pointer; transition: color .15s, background .15s, border-color .15s; }',
        '.ppt-pick__btn:hover { color: var(--text); }',
        '.ppt-pick__btn--primary { color: var(--bg); background: var(--text); border-color: var(--text); margin-left: 4px; }',
        '.ppt-pick__btn--primary:hover { color: var(--bg); background: color-mix(in srgb, var(--text) 88%, var(--accent)); border-color: var(--accent); }',
    ].join('\n');
    document.head.appendChild(s);
}

function pickSlideRange(opts) {
    // Modal-based slide picker. Resolves to {slide_index?, overwrite?} or null on cancel.
    _ensurePickerStyle();
    return new Promise(function(resolve) {
        var total = STATE.slideCount || 0;
        var showOverwrite = !!opts.allowOverwrite;
        var titleHtml = (opts.title || 'Pick scope').replace(/^([\w’']+)\s+(.*)$/, '$1&nbsp;<em>$2</em>');

        // Numbered ladder — staggered fade-in.
        var nums = '';
        for (var i = 1; i <= total; i++) {
            var delay = (Math.min(i, 30) * 14) + 'ms';
            nums += '<div class="ppt-pick__num" data-n="' + i + '" style="animation-delay:' + delay + '">' +
                String(i).padStart(2, '0') + '</div>';
        }

        var html =
            '<div class="ppt-pick">' +
                '<div class="ppt-pick__eyebrow">' + EOS_UI.esc(opts.eyebrow || 'Action › Scope') + '</div>' +
                '<h2 class="ppt-pick__title">' + titleHtml + '</h2>' +
                '<p class="ppt-pick__lede">' + EOS_UI.esc(opts.description || '') + '</p>' +
                '<div class="ppt-pick__rule"></div>' +
                '<div class="ppt-pick__layout">' +
                    '<div class="ppt-pick__every" data-on="true" id="pp-every">' +
                        '<div class="ppt-pick__every-tag">Whole deck</div>' +
                        '<div class="ppt-pick__every-num">' + total + '</div>' +
                        '<div class="ppt-pick__every-foot">slides, in order</div>' +
                    '</div>' +
                    '<div>' +
                        '<p class="ppt-pick__or">or pick a single slide</p>' +
                        '<div class="ppt-pick__ladder" id="pp-ladder">' + nums + '</div>' +
                    '</div>' +
                '</div>' +
                (showOverwrite ?
                    '<label class="ppt-pick__overwrite">' +
                        '<input id="pp-overwrite" type="checkbox">' +
                        '<span class="ppt-pick__overwrite-text">Overwrite existing Say: lines' +
                            '<em>otherwise slides already speakified are skipped</em>' +
                        '</span>' +
                    '</label>'
                : '') +
                '<div class="ppt-pick__actions">' +
                    '<button class="ppt-pick__btn" id="pp-cancel" title="Close without running this action">Cancel</button>' +
                    '<button class="ppt-pick__btn ppt-pick__btn--primary" id="pp-ok" title="Run this action on the selected scope">' + EOS_UI.esc(opts.action || 'Run') + '</button>' +
                '</div>' +
            '</div>';

        var modal = EOS_UI.modal({title: '', body: html, width: '620px'});
        // Hide the default modal header — the body owns the title.
        var hdr = modal.querySelector('.eos-modal-header');
        if (hdr) hdr.style.display = 'none';

        var picked = null;  // null = "all"; else slide number
        var ladder = modal.querySelector('#pp-ladder');
        var every = modal.querySelector('#pp-every');

        function setPick(n) {
            picked = n;
            every.setAttribute('data-on', n === null ? 'true' : 'false');
            ladder.querySelectorAll('.ppt-pick__num').forEach(function(el) {
                el.setAttribute('data-on', String(parseInt(el.dataset.n, 10) === n));
            });
        }

        every.onclick = function() { setPick(null); };
        ladder.onclick = function(e) {
            var t = e.target.closest('.ppt-pick__num');
            if (!t) return;
            setPick(parseInt(t.dataset.n, 10));
        };

        var resolved = false;
        function done(val) { if (!resolved) { resolved = true; EOS_UI.closeModal(); resolve(val); } }
        EOS_UI._modalOnClose = function() { if (!resolved) { resolved = true; resolve(null); } };

        modal.querySelector('#pp-cancel').onclick = function() { done(null); };
        modal.querySelector('#pp-ok').onclick = function() {
            var out = {};
            if (picked !== null) out.slide_index = picked;
            if (showOverwrite && modal.querySelector('#pp-overwrite').checked) out.overwrite = true;
            done(out);
        };

        // Enter submits, number keys jump to that slide (single-digit quick-pick).
        modal.tabIndex = -1; modal.focus();
        modal.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') { e.preventDefault(); modal.querySelector('#pp-ok').click(); }
        });
    });
}

async function narrateCurrent() {
    if (!STATE.currentId) return;
    var pick = await pickSlideRange({
        eyebrow: 'Narrate › Scope',
        title: 'Speak the deck aloud',
        description: 'Generate TTS audio per slide. Reads each Say: line (audience-facing) or falls back to Notes when Say is absent.',
        action: 'Generate',
    });
    if (!pick) return;
    var body = {};
    if (pick.slide_index) body.slide_index = pick.slide_index;
    var label = pick.slide_index ? ('slide ' + pick.slide_index) : 'whole deck';
    setBusy('Generating narration for ' + label + '…');
    try {
        var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/narrate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        var data = await r.json();
        if (data.error) { EOS_UI.toast(data.error, false); return; }
        var msg = (data.generated || 0) + ' track' + (data.generated === 1 ? '' : 's') + ' generated';
        if (data.skipped) msg += ', ' + data.skipped + ' skipped (no notes)';
        if ((data.errors || []).length) msg += ', ' + data.errors.length + ' failed';
        EOS_UI.toast(msg);
        await showDeck(STATE.currentId);
    } catch (e) {
        EOS_UI.toast('Narrate failed: ' + (e.message || e), false);
    } finally {
        setBusy(null);
    }
}

async function speakifyCurrent() {
    if (!STATE.currentId) return;
    var pick = await pickSlideRange({
        eyebrow: 'Speakify › Scope',
        title: 'Rewrite for the audience',
        description: 'Convert presenter Notes into audience-facing Say: lines via AI, so narration reads what the speaker would actually say — not director\'s coaching.',
        action: 'Speakify',
        allowOverwrite: true,
    });
    if (!pick) return;
    var body = {overwrite: !!pick.overwrite};
    if (pick.slide_index) body.slide_index = pick.slide_index;
    var label = pick.slide_index ? ('slide ' + pick.slide_index) : 'whole deck';
    setBusy('Speakifying ' + label + ' (think call per slide)…');
    try {
        var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/speakify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        var data = await r.json();
        if (data.error) { EOS_UI.toast(data.error, false); return; }
        var msg = (data.rewritten || 0) + ' Say: line' + (data.rewritten === 1 ? '' : 's') + ' written';
        if (data.skipped) msg += ', ' + data.skipped + ' skipped';
        if ((data.errors || []).length) msg += ', ' + data.errors.length + ' failed';
        EOS_UI.toast(msg);
        await showDeck(STATE.currentId);
    } catch (e) {
        EOS_UI.toast('Speakify failed: ' + (e.message || e), false);
    } finally {
        setBusy(null);
    }
}

async function toggleNarration(enabled) {
    if (!STATE.currentId) return;
    try {
        var r = await fetch('/ppt/api/decks/' + encodeURIComponent(STATE.currentId) + '/narration-toggle', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({enabled: enabled}),
        });
        var data = await r.json();
        if (data.error) { EOS_UI.toast(data.error, false); return; }
        EOS_UI.toast('Auto-play ' + (enabled ? 'on' : 'off'));
    } catch (e) {
        EOS_UI.toast('Toggle failed: ' + (e.message || e), false);
    }
}

var STYLE_PRESETS = {
    'text':       {label: '📝 Text only',        desc: 'Bullets, quotes, code, tables — no images',         elements: ['bullets','quote','table','code','divider']},
    'images':     {label: '🎨 With images',      desc: 'Adds AI-generated images for visual concepts',     elements: ['bullets','quote','table','code','image','divider']},
    'demo':       {label: '📺 Demo deck',        desc: 'Live embeds of EmptyOS pages or any URL',          elements: ['bullets','code','embed','divider']},
    'everything': {label: '🌈 Use everything',   desc: 'All surfaces incl. vault images & screenshots',    elements: ['bullets','quote','table','code','image','vault','screenshot','embed','divider']},
};

async function newDeck() {
    await loadElements();
    var presetKeys = Object.keys(STYLE_PRESETS);
    var visualStyle = 'studio';
    var visualStyleHtml = '<div class="eos-form-group"><label class="eos-form-label">Visual system</label>' +
        visualStyleCardsHtml(visualStyle) +
        '</div>';
    var stylePillsHtml = '<div class="eos-form-group"><label class="eos-form-label">Surface mix</label>' +
        '<div id="ppt-style-pills" class="ppt-pill-row">' +
        presetKeys.map(function(k){
            var p = STYLE_PRESETS[k];
            var active = (k === 'images') ? ' ppt-pill-active' : '';
            return '<button type="button" class="ppt-pill'+active+'" data-preset="'+k+'" title="'+EOS_UI.escAttr(p.desc)+'">'+p.label+'</button>';
        }).join('') +
        '<button type="button" class="ppt-pill" data-preset="custom" title="Pick surfaces yourself below">⚙ Custom</button>' +
        '</div>' +
        '<div id="ppt-style-desc" class="ppt-field-hint">'+EOS_UI.esc(STYLE_PRESETS.images.desc)+'</div>' +
        '</div>';
    var audienceHtml = '<div class="eos-form-group ppt-form-grid">' +
            '<div><label class="eos-form-label">Audience (optional)</label>' +
                '<input id="ppt-new-audience" class="eos-form-input" type="text" placeholder="e.g. mid-senior backend engineers"></div>' +
            '<div><label class="eos-form-label">Duration (min)</label>' +
                '<input id="ppt-new-duration" class="eos-form-input" type="number" value="5" min="2" max="60"></div>' +
        '</div>';
    var followUpHtml = '<div class="eos-form-group"><label class="eos-form-label">After creating</label>' +
        '<div class="ppt-checks">' +
            '<label class="ppt-check">' +
                '<input type="checkbox" id="ppt-plan-first" checked>' +
                '<span><strong>Plan first</strong> — review intent + per-slide surface before drafting <span class="ppt-muted">(recommended)</span></span>' +
            '</label>' +
            '<label class="ppt-check">' +
                '<input type="checkbox" id="ppt-after-resolve">' +
                '<span>Resolve all image placeholders now <span class="ppt-muted">(slow — ~30s per image)</span></span>' +
            '</label>' +
            '<label class="ppt-check">' +
                '<input type="checkbox" id="ppt-after-podcast">' +
                '<span>Also generate a podcast episode <span class="ppt-muted">(takes a minute)</span></span>' +
            '</label>' +
        '</div></div>';
    var body =
        '<div class="eos-form-group"><label class="eos-form-label">Title</label>' +
            '<input id="ppt-new-title" class="eos-form-input" type="text" placeholder="Cable rating fundamentals" autofocus></div>' +
        '<div class="eos-form-group"><label class="eos-form-label">Outline / topic hints (optional)</label>' +
            '<textarea id="ppt-new-outline" class="eos-form-input" rows="3" placeholder="Leave blank for a starter template, or describe the talk for an AI draft."></textarea></div>' +
        visualStyleHtml +
        stylePillsHtml +
        '<details class="ppt-details"><summary>Fine-tune slide surfaces</summary>' +
            elementsCheckboxesHtml(new Set(STYLE_PRESETS.images.elements)) +
        '</details>' +
        audienceHtml +
        followUpHtml +
        '<div class="eos-form-actions">' +
            '<button class="eos-btn" onclick="EOS_UI.closeModal()" title="Close without creating a deck">Cancel</button>' +
            '<button class="eos-btn eos-btn-primary" id="ppt-new-go" title="Create this deck">Create deck</button>' +
        '</div>';
    EOS_UI.modal({title: 'New deck', body: body});
    setTimeout(function(){
        var titleEl = document.getElementById('ppt-new-title');
        if (titleEl) titleEl.focus();

        var visualCards = document.querySelectorAll('[data-visual-style]');
        visualCards.forEach(function(card) {
            card.onclick = function() {
                visualStyle = normalizeVisualStyle(card.dataset.visualStyle);
                visualCards.forEach(function(x){ x.classList.toggle('active', x.dataset.visualStyle === visualStyle); });
            };
        });

        // Style-pill click → update checkboxes + description.
        var pills = document.querySelectorAll('#ppt-style-pills .ppt-pill');
        var descEl = document.getElementById('ppt-style-desc');
        pills.forEach(function(p) {
            p.onclick = function() {
                pills.forEach(function(x){ x.classList.remove('ppt-pill-active'); });
                p.classList.add('ppt-pill-active');
                var key = p.dataset.preset;
                if (key === 'custom') {
                    descEl.textContent = 'Pick surfaces yourself below.';
                    return;
                }
                var preset = STYLE_PRESETS[key];
                descEl.textContent = preset.desc;
                var picked = new Set(preset.elements);
                document.querySelectorAll('#ppt-elements-grid input[name=elem]').forEach(function(c){
                    if (!c.disabled) c.checked = picked.has(c.value);
                });
            };
        });

        var goBtn = document.getElementById('ppt-new-go');
        if (!goBtn) return;
        goBtn.onclick = async function() {
            var title = (document.getElementById('ppt-new-title').value || '').trim();
            if (!title) { EOS_UI.toast('Title is required', false); return; }
            var outline = document.getElementById('ppt-new-outline').value || '';
            var audience = (document.getElementById('ppt-new-audience').value || '').trim();
            var duration = parseInt(document.getElementById('ppt-new-duration').value, 10) || 5;
            var elements = readElementChoice();
            var planFirst = document.getElementById('ppt-plan-first').checked;
            var doResolve = document.getElementById('ppt-after-resolve').checked;
            var doPodcast = document.getElementById('ppt-after-podcast').checked;
            EOS_UI.closeModal();

            if (planFirst) {
                setBusy('Drafting plan…');
                try {
                    var pr = await fetch('/ppt/api/plan', {
                        method: 'POST', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({
                            title: title, outline: outline, audience: audience,
                            duration_min: duration, allowed_elements: elements,
                            visual_style: visualStyle,
                        }),
                    });
                    var plan = await pr.json();
                    if (plan.error) { EOS_UI.toast(plan.error, false); return; }
                    plan.visual_style = visualStyle;
                    setBusy(null);
                    showPlanReview(plan, {doResolve: doResolve, doPodcast: doPodcast, visualStyle: visualStyle});
                } catch (e) {
                    EOS_UI.toast('Plan failed: ' + (e.message || e), false);
                } finally {
                    setBusy(null);
                }
                return;
            }

            var totalSteps = 1 + (doResolve ? 1 : 0) + (doPodcast ? 1 : 0);
            var stepIdx = 0;
            var job = EOS_UI.jobProgress({id: 'ppt-new'});
            var stepStr = function() { return (stepIdx + 1) + '/' + totalSteps; };
            var pctFor = function() { return Math.round((stepIdx / totalSteps) * 100); };
            job.update({stage: 'Drafting deck', detail: title, step: stepStr(), pct: pctFor()});
            var r = await fetch('/ppt/api/decks', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({title: title, outline: outline, allowed_elements: elements, visual_style: visualStyle}),
            });
            var data = await r.json();
            if (data.error) { job.hide(); EOS_UI.toast(data.error, false); return; }
            stepIdx = 1;
            await load();
            _route.set(data.id);
            if (doResolve) {
                job.update({stage: 'Resolving images', detail: data.id, step: stepStr(), pct: pctFor()});
                var rr = await fetch('/ppt/api/decks/' + encodeURIComponent(data.id) + '/resolve-images', {method:'POST'});
                var rdata = await rr.json();
                if (rdata.error) { job.hide(); EOS_UI.toast(rdata.error, false); return; }
                stepIdx++;
                EOS_UI.toast((rdata.resolved||0) + ' images resolved');
            }
            if (doPodcast) {
                job.update({stage: 'Generating podcast', detail: 'this takes a while…', step: stepStr(), pct: pctFor()});
                var pr = await fetch('/ppt/api/decks/' + encodeURIComponent(data.id) + '/to-podcast', {method:'POST'});
                var pdata = await pr.json();
                if (pdata.error) { job.hide(); EOS_UI.toast(pdata.error, false); return; }
                stepIdx++;
                EOS_UI.toast('Episode ready → /podcast/');
            }
            job.done({stage: 'Deck ready', detail: data.id, step: null});
            showDeck(data.id);
        };
    }, 30);
}

async function loadIntents() {
    if (STATE.intents) return STATE.intents;
    try { STATE.intents = (await fetch('/ppt/api/intents').then(r => r.json())).intents || []; }
    catch(e) { STATE.intents = []; }
    return STATE.intents;
}

async function showPlanReview(plan, followups) {
    await loadIntents();
    await loadElements();
    followups = followups || {};
    var allowed = plan.allowed_elements || (STATE.elements && STATE.elements.defaults) || ['bullets'];
    plan.visual_style = normalizeVisualStyle(plan.visual_style || followups.visualStyle);
    PLAN_STATE.plan = plan;
    PLAN_STATE.allowed = allowed;
    PLAN_STATE.followups = followups;

    var intentOpts = (STATE.intents || []).map(function(it){
        var sel = (it.id === plan.intent) ? ' selected' : '';
        return '<option value="'+EOS_UI.escAttr(it.id)+'"'+sel+'>'+EOS_UI.esc(it.label)+'</option>';
    }).join('');
    var intentDesc = '';
    var found = (STATE.intents || []).find(function(i){ return i.id === plan.intent; });
    if (found) intentDesc = found.guidance;

    var body =
        '<div class="ppt-plan-grid">' +
            '<div class="eos-form-group"><label class="eos-form-label">Title</label>' +
                '<input id="plan-title" class="eos-form-input" type="text" value="'+EOS_UI.escAttr(plan.title||'')+'"></div>' +
            '<div class="eos-form-group"><label class="eos-form-label">Audience</label>' +
                '<input id="plan-audience" class="eos-form-input" type="text" value="'+EOS_UI.escAttr(plan.audience||'')+'"></div>' +
            '<div class="eos-form-group"><label class="eos-form-label">Min</label>' +
                '<input id="plan-duration" class="eos-form-input" type="number" value="'+(plan.duration_min||5)+'" min="2" max="60"></div>' +
        '</div>' +
        '<div class="eos-form-group"><label class="eos-form-label">Intent</label>' +
            '<select id="plan-intent" class="eos-form-input">'+intentOpts+'</select>' +
            '<div id="plan-intent-desc" class="ppt-field-hint">'+EOS_UI.esc(intentDesc)+'</div>' +
        '</div>' +
        '<div class="eos-form-group"><label class="eos-form-label">Subtitle (optional)</label>' +
            '<input id="plan-subtitle" class="eos-form-input" type="text" value="'+EOS_UI.escAttr(plan.subtitle||'')+'"></div>' +
        '<div class="eos-form-group"><label class="eos-form-label">Visual system</label>' +
            '<select id="plan-visual-style" class="eos-form-input">'+visualStyleOptionsHtml(plan.visual_style)+'</select></div>' +
        '<div class="eos-form-group"><label class="eos-form-label ppt-plan-head">' +
            '<span>Slides — '+plan.slides.length+'</span>' +
            '<button type="button" class="eos-btn eos-btn-sm" id="plan-add-slide" title="Add a slide to this plan">+ Add slide</button>' +
        '</label>' +
            '<div id="plan-slides" class="ppt-plan-slides"></div>' +
        '</div>' +
        '<div class="eos-form-actions">' +
            '<button class="eos-btn" onclick="EOS_UI.closeModal()" title="Close without generating a deck">Cancel</button>' +
            '<button class="eos-btn" id="plan-replan" title="Return to the deck setup form">Re-plan from outline</button>' +
            '<button class="eos-btn eos-btn-primary" id="plan-generate" title="Generate the deck from this plan">Generate deck</button>' +
        '</div>';
    EOS_UI.modal({title: 'Review the plan', body: body, width: '780px'});

    setTimeout(function(){
        renderPlanSlides();
        var intSel = document.getElementById('plan-intent');
        if (intSel) intSel.onchange = function() {
            var pick = (STATE.intents || []).find(function(i){ return i.id === intSel.value; });
            document.getElementById('plan-intent-desc').textContent = pick ? pick.guidance : '';
        };
        document.getElementById('plan-add-slide').onclick = function() {
            PLAN_STATE.plan.slides.push({surface: 'bullets', headline: 'New slide', beat: ''});
            renderPlanSlides();
        };
        document.getElementById('plan-replan').onclick = async function() {
            EOS_UI.closeModal();
            newDeck();  // reopen the setup modal
        };
        document.getElementById('plan-generate').onclick = generateFromPlan;
    }, 30);
}

var PLAN_STATE = { plan: null, allowed: [], followups: {} };

function renderPlanSlides() {
    var host = document.getElementById('plan-slides');
    if (!host) return;
    var slides = PLAN_STATE.plan.slides || [];
    host.innerHTML = slides.map(function(s, i){
        var surfOpts = PLAN_STATE.allowed.map(function(a){
            return '<option value="'+a+'"'+(a===s.surface?' selected':'')+'>'+a+'</option>';
        }).join('');
        return ''+
        '<div class="plan-slide-row" data-i="'+i+'">' +
            '<div class="plan-slide-num">'+(i+1)+'</div>' +
            '<select data-field="surface" class="eos-form-input plan-slide-surface">'+surfOpts+'</select>' +
            '<div class="plan-slide-fields">' +
                '<input data-field="headline" class="eos-form-input plan-slide-headline" type="text" placeholder="Slide heading" value="'+EOS_UI.escAttr(s.headline||'')+'">' +
                '<input data-field="beat" class="eos-form-input plan-slide-beat" type="text" placeholder="What this slide accomplishes (beat)" value="'+EOS_UI.escAttr(s.beat||'')+'">' +
            '</div>' +
            '<div class="plan-slide-actions">' +
                '<button type="button" class="ppt-icon-button" data-act="up" title="Move this slide up">&#9650;</button>' +
                '<button type="button" class="ppt-icon-button" data-act="down" title="Move this slide down">&#9660;</button>' +
                '<button type="button" class="ppt-icon-button is-danger" data-act="del" title="Delete this slide">&times;</button>' +
            '</div>' +
        '</div>';
    }).join('');
    host.querySelectorAll('.plan-slide-row').forEach(function(row){
        var i = parseInt(row.dataset.i, 10);
        row.querySelectorAll('[data-field]').forEach(function(inp){
            inp.onchange = function() {
                PLAN_STATE.plan.slides[i][inp.dataset.field] = inp.value;
            };
            inp.oninput = inp.onchange;
        });
        row.querySelectorAll('[data-act]').forEach(function(btn){
            btn.onclick = function() {
                var act = btn.dataset.act;
                var arr = PLAN_STATE.plan.slides;
                if (act === 'up' && i > 0) { var t = arr[i-1]; arr[i-1] = arr[i]; arr[i] = t; }
                else if (act === 'down' && i < arr.length-1) { var t2 = arr[i+1]; arr[i+1] = arr[i]; arr[i] = t2; }
                else if (act === 'del') { arr.splice(i, 1); }
                renderPlanSlides();
            };
        });
    });
}

async function generateFromPlan() {
    // Pull current values back into PLAN_STATE.plan
    var plan = PLAN_STATE.plan;
    plan.title = document.getElementById('plan-title').value.trim();
    plan.audience = document.getElementById('plan-audience').value.trim();
    plan.duration_min = parseInt(document.getElementById('plan-duration').value, 10) || 5;
    plan.intent = document.getElementById('plan-intent').value;
    plan.subtitle = document.getElementById('plan-subtitle').value.trim();
    plan.visual_style = normalizeVisualStyle(document.getElementById('plan-visual-style').value);
    plan.allowed_elements = PLAN_STATE.allowed;
    if (!plan.title) { EOS_UI.toast('Title is required', false); return; }
    if (!plan.slides || !plan.slides.length) { EOS_UI.toast('Plan needs at least one slide', false); return; }
    EOS_UI.closeModal();

    var followups = PLAN_STATE.followups || {};
    var totalSteps = 1 + (followups.doResolve ? 1 : 0) + (followups.doPodcast ? 1 : 0);
    var stepIdx = 0;
    var job = EOS_UI.jobProgress({id: 'ppt-new-from-plan'});
    var stepStr = function() { return (stepIdx + 1) + '/' + totalSteps; };
    var pctFor = function() { return Math.round((stepIdx / totalSteps) * 100); };

    try {
        job.update({stage: 'Generating deck from plan', detail: plan.title, step: stepStr(), pct: pctFor()});
        var r = await fetch('/ppt/api/generate-from-plan', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({plan: plan}),
        });
        var data = await r.json();
        if (data.error) { job.hide(); EOS_UI.toast(data.error, false); return; }
        stepIdx = 1;
        _generatedDeckId = data.id;
        await load();
        _route.set(data.id);
        if (followups.doResolve) {
            job.update({stage: 'Resolving images', detail: data.id, step: stepStr(), pct: pctFor()});
            var rr = await fetch('/ppt/api/decks/' + encodeURIComponent(data.id) + '/resolve-images', {method:'POST'});
            var rdata = await rr.json();
            if (!rdata.error) { stepIdx++; EOS_UI.toast((rdata.resolved||0) + ' images resolved'); }
        }
        if (followups.doPodcast) {
            job.update({stage: 'Generating podcast', detail: 'this takes a while…', step: stepStr(), pct: pctFor()});
            var pr = await fetch('/ppt/api/decks/' + encodeURIComponent(data.id) + '/to-podcast', {method:'POST'});
            var pdata = await pr.json();
            if (!pdata.error) { stepIdx++; EOS_UI.toast('Episode ready → /podcast/'); }
        }
        job.done({stage: 'Deck ready', detail: data.id, step: null});
        showDeck(data.id);
    } catch (e) {
        job.hide();
        EOS_UI.toast('Generate failed: ' + (e.message || e), false);
    }
}

window.addEventListener('beforeunload', function(e) {
    if (STATE.dirty) { e.preventDefault(); e.returnValue = ''; }
});

function bindPageControls() {
    var search = document.getElementById('deck-search');
    if (search) {
        search.addEventListener('input', function() {
            STATE.query = search.value || '';
            renderList();
        });
    }
    document.querySelectorAll('[data-style-field]').forEach(function(btn) {
        btn.addEventListener('click', function() {
            setDeckStyle(btn.getAttribute('data-style-field'), btn.getAttribute('data-style-value'));
        });
    });
}

bindPageControls();
load().then(function(){ _route.init(); });
