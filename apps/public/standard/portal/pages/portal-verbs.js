// portal-verbs.js — split out of portal.js (B0, 2026-09-12). Owns the hero verbs Capture / Find / Learn and their pickers.
//
// Loads BEFORE portal.js, in the same global scope (not a module), so the
// page's markup handlers and portal.js's boot code resolve these names
// unchanged. Top-level names must stay unique across portal.js and every
// portal-*.js — tests/test_unit_page_script_globals.py fails on a
// collision, because the script that parses last would silently win.

// ── Verb: Capture ──────────────────────────────────────────────────
// Opens a "Save as" picker — text gets POSTed to the chosen app's
// existing add endpoint. No thread is created.

var _captureModal = null;
function _closeCaptureModal() {
    if (_captureModal && _captureModal.close) { try { _captureModal.close(); } catch(e){} }
    _captureModal = null;
}

async function _handleCapture(text, folder) {
    var rowBtn = 'display:flex;flex-direction:column;gap:2px;align-items:flex-start;padding:12px 14px;border-radius:8px;background:none;border:1px solid var(--border);font-family:inherit;cursor:pointer;text-align:left;width:100%;color:var(--text)';
    var rowHover = 'this.style.borderColor=getComputedStyle(this).getPropertyValue(\'--accent\')';
    var body =
        '<div style="display:flex;flex-direction:column;gap:8px">' +
        '<div style="padding:10px 12px;border-radius:6px;background:var(--bg);border:1px solid var(--border);font-size:12.5px;color:var(--text-secondary);max-height:120px;overflow-y:auto;white-space:pre-wrap">' +
            esc(text) + '</div>' +
        '<button style="' + rowBtn + '" onclick="_captureTo(\'capture\')">' +
            '<span style="font-weight:500">&#x1F4E5; Capture</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Drop into the inbox (untagged)</span>' +
        '</button>' +
        '<button style="' + rowBtn + '" onclick="_captureTo(\'task\')">' +
            '<span style="font-weight:500">&#x2705; Task</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Add as a task (tag=task)</span>' +
        '</button>' +
        '<button style="' + rowBtn + '" onclick="_captureTo(\'journal\')">' +
            '<span style="font-weight:500">&#x1F4D3; Journal</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Append to today\'s journal</span>' +
        '</button>' +
        '<button style="' + rowBtn + '" onclick="_captureTo(\'kb\')">' +
            '<span style="font-weight:500">&#x1F4A1; KB note</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Create a knowledge-base note</span>' +
        '</button>' +
        '<div style="display:flex;justify-content:flex-end;padding-top:6px">' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="_closeCaptureModal()">Cancel</button>' +
        '</div></div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _captureModal = EOS_UI.modal({ title: 'Save as…', body: body });
        // Stash the text so _captureTo can read it without re-fetching from the input.
        _captureModal._text = text;
    }
}

async function _captureTo(target) {
    var text = (_captureModal && _captureModal._text) || '';
    _closeCaptureModal();
    if (!text) return;
    var route, body, label;
    if (target === 'capture') {
        // Let quick-action's classifier pick the tag/route. It falls back to a
        // plain add when no think provider is reachable, so this never dead-ends.
        route = '/quick-action/api/smart-add'; body = { text: text }; label = 'Capture';
    } else if (target === 'task') {
        route = '/quick-action/api/add'; body = { text: text, tag: 'task' }; label = 'Task';
    } else if (target === 'journal') {
        route = '/journal/api/add'; body = { text: text, mood: 'okay' }; label = 'Journal';
    } else if (target === 'kb') {
        // KB notes need a title — derive from the first line.
        var firstLine = text.split('\n')[0].trim();
        var title = firstLine.length > 60 ? firstLine.slice(0, 60) + '…' : firstLine;
        route = '/kb/api/notes'; label = 'KB note';
        body = { kind: 'concept', title: title, body: text };
    } else { return; }
    try {
        var res = await fetch(route, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        var data = await res.json();
        if (data && data.error) throw new Error(data.error);
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Saved to ' + label, true);
        var input = document.getElementById('hero-input');
        if (input) {
            input.value = '';
            input.style.height = 'auto';
            document.getElementById('hero-send').classList.remove('ready');
        }
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Save failed: ' + (err.message || err), false);
    }
}

// ── Verb: Find ────────────────────────────────────────────────────
// Vault search; results render inline in a new pane state.

async function _handleFind(text, folder) {
    history.pushState(null, '', location.pathname + '#find:' + encodeURIComponent(text));
    await _runFind(text);
}

async function _runFind(query) {
    var qEl = document.getElementById('portal-find-query');
    if (qEl) qEl.textContent = '"' + query + '"';
    _showFind();
    var resultsEl = document.getElementById('portal-find-results');
    if (!resultsEl) return;
    resultsEl.innerHTML = '<div class="portal-find-empty">Searching&hellip;</div>';
    try {
        var res = await EOS.api('/search/api/search?q=' + encodeURIComponent(query) + '&top=20');
        var paths = (res && res.results) || [];
        // Also surface threads whose name matches the query — across ALL
        // conversation backends (rooms, assistant, agent), not just rooms.
        var q = query.toLowerCase();
        var threadMatches = []
            .concat((allAgents || []).map(function(a) {
                return { id: a.id, name: a.name || a.id, kind: 'thread' };
            }))
            .concat((allAsstSessions || []).map(function(s) {
                return { id: 'asst:' + s.id, name: s.name || 'Assistant chat', kind: 'assistant chat' };
            }))
            .concat((allAgentSessions || []).map(function(s) {
                return { id: 'agent:' + s.id, name: s.name || 'Agent run', kind: 'agent run' };
            }))
            .filter(function(t) {
                return (t.name || '').toLowerCase().indexOf(q) >= 0;
            }).slice(0, 12);
        // And apps whose label/id matches.
        var appMatches = Object.keys(APP_CATALOG).filter(function(id) {
            var n = (APP_CATALOG[id] || '').toLowerCase();
            return id.toLowerCase().indexOf(query.toLowerCase()) >= 0
                || n.indexOf(query.toLowerCase()) >= 0;
        }).slice(0, 10).map(function(id) {
            return { id: id, name: APP_CATALOG[id] || id };
        });
        _renderFindResults(query, paths, threadMatches, appMatches);
    } catch (err) {
        resultsEl.innerHTML = '<div class="portal-find-empty">Search failed: ' + esc(err.message || String(err)) + '</div>';
    }
}

function _renderFindResults(query, paths, threads, apps) {
    var el = document.getElementById('portal-find-results');
    if (!el) return;
    var groups = [];
    if (paths.length) {
        groups.push('<div class="portal-find-group">' +
            '<h3>Vault notes &middot; ' + paths.length + '</h3>' +
            '<div class="portal-find-stack">' +
            paths.map(function(r) {
                var p = r.path || r;
                var title = p.split('/').pop().replace(/\.md$/i, '');
                var dir = p.indexOf('/') >= 0 ? p.slice(0, p.lastIndexOf('/')) : '';
                return '<a class="portal-find-card" href="#" ' +
                    'onclick="event.preventDefault();_openVaultNote(' + EOS_UI.jsArg(p) + ')">' +
                    '<span class="fc-title">' + esc(title) + '</span>' +
                    '<span class="fc-path">' + esc(dir || '/') + '</span>' +
                    '</a>';
            }).join('') +
            '</div></div>');
    }
    if (threads.length) {
        groups.push('<div class="portal-find-group">' +
            '<h3>Threads &middot; ' + threads.length + '</h3>' +
            '<div class="portal-find-stack">' +
            threads.map(function(t) {
                return '<a class="portal-find-card" href="#" ' +
                    'onclick="event.preventDefault();_navTo(' + EOS_UI.jsArg(t.id) + ')">' +
                    '<span class="fc-title">' + esc(t.name || t.id) + '</span>' +
                    '<span class="fc-meta">' + esc(t.kind || 'thread') + ' &middot; ' + esc(t.id) + '</span>' +
                    '</a>';
            }).join('') +
            '</div></div>');
    }
    if (apps.length) {
        groups.push('<div class="portal-find-group">' +
            '<h3>Apps &middot; ' + apps.length + '</h3>' +
            '<div class="portal-find-stack">' +
            apps.map(function(a) {
                return '<a class="portal-find-card" href="#" ' +
                    'onclick="event.preventDefault();_navToApp(' + EOS_UI.jsArg(a.id) + ')">' +
                    '<span class="fc-title">' + esc(a.name) + '</span>' +
                    '<span class="fc-meta">app &middot; /' + esc(a.id) + '/</span>' +
                    '</a>';
            }).join('') +
            '</div></div>');
    }
    if (!groups.length) {
        el.innerHTML = '<div class="portal-find-empty">No matches for &ldquo;' + esc(query) + '&rdquo;.</div>';
    } else {
        el.innerHTML = groups.join('');
    }
}

function _openVaultNote(relPath) {
    // EOS.viewNote routes through the kernel's note-viewer (which knows the
    // active vault). EOS.openInViewer opens in the external viewer (obsidian://).
    // Prefer in-page viewing — keeps the portal shell.
    if (typeof EOS !== 'undefined' && EOS.viewNote) {
        EOS.viewNote(relPath);
    } else {
        window.open('/note-viewer/?path=' + encodeURIComponent(relPath), '_blank');
    }
}

// ── Verb: Learn ────────────────────────────────────────────────────
// "Process as" picker: video digest (URL) or KB note (any text).

var _learnModal = null;
function _closeLearnModal() {
    if (_learnModal && _learnModal.close) { try { _learnModal.close(); } catch(e){} }
    _learnModal = null;
}

async function _handleLearn(text, folder) {
    var isUrl = /^https?:\/\//i.test(text);
    var rowBtn = 'display:flex;flex-direction:column;gap:2px;align-items:flex-start;padding:12px 14px;border-radius:8px;background:none;border:1px solid var(--border);font-family:inherit;cursor:pointer;text-align:left;width:100%;color:var(--text)';
    var disabledStyle = 'opacity:0.45;cursor:not-allowed';
    var ytBtn = isUrl
        ? '<button style="' + rowBtn + '" onclick="_learnTo(\'video-digest\')">' +
            '<span style="font-weight:500">&#x1F3A5; Video digest</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Queue this URL for transcript + summary</span>' +
          '</button>'
        : '<button style="' + rowBtn + ';' + disabledStyle + '" disabled title="Paste a YouTube URL to enable">' +
            '<span style="font-weight:500">&#x1F3A5; Video digest</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Paste a URL to enable</span>' +
          '</button>';
    var body =
        '<div style="display:flex;flex-direction:column;gap:8px">' +
        '<div style="padding:10px 12px;border-radius:6px;background:var(--bg);border:1px solid var(--border);font-size:12.5px;color:var(--text-secondary);max-height:120px;overflow-y:auto;white-space:pre-wrap">' +
            esc(text) + '</div>' +
        ytBtn +
        '<button style="' + rowBtn + '" onclick="_learnTo(\'kb\')">' +
            '<span style="font-weight:500">&#x1F4A1; KB note</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Save as a structured knowledge note</span>' +
        '</button>' +
        '<div style="display:flex;justify-content:flex-end;padding-top:6px">' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="_closeLearnModal()">Cancel</button>' +
        '</div></div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _learnModal = EOS_UI.modal({ title: 'Process as…', body: body });
        _learnModal._text = text;
    }
}

async function _learnTo(target) {
    var text = (_learnModal && _learnModal._text) || '';
    _closeLearnModal();
    if (!text) return;
    var route, body, label;
    if (target === 'video-digest') {
        route = '/video-digest/api/queue'; body = { url: text }; label = 'Video digest';
    } else if (target === 'kb') {
        var firstLine = text.split('\n')[0].trim();
        var title = firstLine.length > 60 ? firstLine.slice(0, 60) + '…' : firstLine;
        route = '/kb/api/notes'; label = 'KB note';
        body = { kind: 'concept', title: title, body: text };
    } else { return; }
    try {
        var res = await fetch(route, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        var data = await res.json();
        if (data && (data.error || data.ok === false)) throw new Error(data.error || 'failed');
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Sent to ' + label, true);
        var input = document.getElementById('hero-input');
        if (input) {
            input.value = '';
            input.style.height = 'auto';
            document.getElementById('hero-send').classList.remove('ready');
        }
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Send failed: ' + (err.message || err), false);
    }
}
