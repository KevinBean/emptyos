// rooms-input.js — chat input affordances: @mention insert, [[ wikilink,
// /slash commands, mention autocomplete, keydown router.
// Owns: insertMention, vault-file picker, SLASH_COMMANDS table,
// _mention state machine, chatInputChanged, chatInputKeydown.

// being typed). Triggered from member-chip clicks.
function insertMention(participantId) {
    var input = document.getElementById('chat-input');
    if (!input) return;
    var text = input.value || '';
    var cursor = input.selectionStart || text.length;
    // If user is mid-@-token, replace it; otherwise insert at cursor.
    var anchor = cursor;
    for (var i = cursor - 1; i >= 0; i--) {
        var c = text[i];
        if (c === '@') {
            var prev = i === 0 ? ' ' : text[i - 1];
            if (/\s/.test(prev) || prev === '') { anchor = i; }
            break;
        }
        if (/\s/.test(c)) break;
    }
    var insert = '@' + participantId + ' ';
    input.value = text.slice(0, anchor) + insert + text.slice(cursor);
    var pos = (text.slice(0, anchor) + insert).length;
    input.selectionStart = input.selectionEnd = pos;
    input.focus();
    _hideMention();
}

// --- Vault file references — [[ trigger (Phase 11) ---
//
// Type `[[` in the input to open a vault note picker. Selecting one inserts
// `[[<path>]]` (Obsidian-native wikilink). Backend resolves these at chat
// time, prepending each file's content as a Knowledge context block.

var _fileSearchTimer = null;
var _fileSearchSeq = 0;

// Walk back from cursor looking for an unclosed `[[`. Returns
// {openIdx, query} or null. Bails on `]` (would close a previous link).
function _scanWikilinkAtCursor(input) {
    var pos = input.selectionStart || 0;
    var text = input.value || '';
    if (pos < 2) return null;
    for (var i = pos - 1; i >= 1; i--) {
        var c = text[i];
        if (c === ']') return null;
        if (c === '\n') return null;
        if (c === '[' && text[i - 1] === '[') {
            return {openIdx: i - 1, query: text.slice(i + 1, pos)};
        }
    }
    return null;
}

function _renderFilePopup(files) {
    var el = document.getElementById('mention-popup');
    if (!el) return;
    if (!files.length) {
        el.classList.remove('open'); _mention.open = false; return;
    }
    el.innerHTML = files.map(function(f, i) {
        var active = i === _mention.active ? ' active' : '';
        return '<div class="mention-item' + active + '" data-idx="' + i +
               '" onmousedown="event.preventDefault();_acceptFile(' + i + ')">' +
               '<div class="mention-avatar" style="background:var(--text-muted)">📄</div>' +
               '<div style="flex:1;min-width:0">' +
                   '<div style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(f.name) + '</div>' +
                   '<div style="font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(f.folder || f.path) + '</div>' +
               '</div></div>';
    }).join('');
    el.classList.add('open');
    _mention.open = true;
}

function _acceptFile(idx) {
    if (idx < 0 || idx >= _mention.items.length) return;
    var input = document.getElementById('chat-input');
    if (!input) return;
    var f = _mention.items[idx];
    var text = input.value || '';
    var open = _mention.anchorIdx;
    if (open < 0 || text.slice(open, open + 2) !== '[[') return;
    var cursor = input.selectionStart || text.length;
    // The path stored in the wikilink — strip .md so it round-trips cleanly.
    var ref = (f.path || f.name || '').replace(/\.md$/i, '');
    var before = text.slice(0, open);
    var after = text.slice(cursor);
    var insert = '[[' + ref + ']] ';
    input.value = before + insert + after;
    var pos = (before + insert).length;
    input.selectionStart = input.selectionEnd = pos;
    _hideMention();
    input.focus();
}

async function _fetchVaultFiles(query) {
    var seq = ++_fileSearchSeq;
    try {
        var res = await EOS.api('/rooms/api/vault-search?q=' + encodeURIComponent(query) + '&limit=15');
        if (seq !== _fileSearchSeq) return null;  // stale response
        return (res && res.files) || [];
    } catch (e) {
        return [];
    }
}

// `@@<query>` file picker — distinct from wikilink `[[...]]`. Returns vault
// notes + repo source files. On accept, inserts `@<path>` (single @) which
// pi-coding-agent + claude-cli natively expand as file-include directives.
function _scanFileAtCursor(input) {
    var pos = input.selectionStart || 0;
    var text = input.value || '';
    if (pos < 2) return null;
    // Walk back from cursor looking for `@@`. Stop on whitespace or other
    // boundary chars — `@@` mode only spans one query token.
    for (var i = pos - 1; i >= 1; i--) {
        var c = text[i];
        if (/\s/.test(c)) return null;
        if (c === '@' && text[i - 1] === '@') {
            return {openIdx: i - 1, query: text.slice(i + 1, pos)};
        }
    }
    return null;
}

async function _fetchFiles(query) {
    var seq = ++_fileSearchSeq;
    try {
        var res = await EOS.api('/rooms/api/files/search?q=' + encodeURIComponent(query) + '&limit=15');
        if (seq !== _fileSearchSeq) return null;
        return (res && res.files) || [];
    } catch (e) {
        return [];
    }
}

function _acceptFileMention(idx) {
    if (idx < 0 || idx >= _mention.items.length) return;
    var input = document.getElementById('chat-input');
    if (!input) return;
    var f = _mention.items[idx];
    var text = input.value || '';
    var open = _mention.anchorIdx;
    if (open < 0 || text.slice(open, open + 2) !== '@@') return;
    var cursor = input.selectionStart || text.length;
    // Insert `@<path>` so pi / claude-cli / etc. resolve it as a file include.
    // The single `@` form is the universal coding-CLI convention. `_strip_room_mentions`
    // on the backend leaves these alone (path contains `/` which isn't in the
    // mention-token regex).
    var ref = f.path || f.name || '';
    var before = text.slice(0, open);
    var after = text.slice(cursor);
    var insert = '@' + ref + ' ';
    input.value = before + insert + after;
    var pos = (before + insert).length;
    input.selectionStart = input.selectionEnd = pos;
    _hideMention();
    input.focus();
}

// Slightly distinct popup renderer — surfaces the file's `kind` (vault / repo)
// so the user can tell a Python source file from a markdown note at a glance.
function _renderFileMentionPopup(files) {
    var el = document.getElementById('mention-popup');
    if (!el) return;
    if (!files.length) {
        el.classList.remove('open'); _mention.open = false; return;
    }
    el.innerHTML = files.map(function(f, i) {
        var active = i === _mention.active ? ' active' : '';
        var isRepo = f.kind === 'repo';
        var icon = isRepo ? '⌘' : '📄';
        var bg = isRepo ? 'var(--accent)' : 'var(--text-muted)';
        return '<div class="mention-item' + active + '" data-idx="' + i +
               '" onmousedown="event.preventDefault();_acceptFileMention(' + i + ')">' +
               '<div class="mention-avatar" style="background:' + bg + '">' + icon + '</div>' +
               '<div style="flex:1;min-width:0">' +
                   '<div style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(f.name) + '</div>' +
                   '<div style="font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(f.folder || f.path) + '</div>' +
               '</div></div>';
    }).join('');
    el.classList.add('open');
    _mention.open = true;
}

// --- Slash commands (Phase 10) ---
//
// Typing `/` at the start of the input opens a command palette. Same UX
// shell as the @-mention popup — re-uses #mention-popup. Each command knows
// whether it takes free-text args (slot is filled by the user before Enter)
// or fires immediately on Enter.
//
// Commands are pure frontend wrappers around existing API endpoints, so
// adding a new one is one entry in SLASH_COMMANDS plus its action. No
// backend changes needed.

var SLASH_COMMANDS = [
    {
        name: 'fork', desc: 'Branch from a past message',
        args: '[entry_id]', needsRoom: true,
        run: function(rest) { openForkPicker((rest || '').trim() || null); },
    },
    {
        name: 'branches', desc: 'List active branches in this room',
        args: '', needsRoom: true,
        run: function() { openBranchPicker(); },
    },
    {
        name: 'distill', desc: 'Summarise this room into a KB note',
        args: '', needsRoom: true,
        run: function() { distillRoom(); },
    },
    {
        name: 'export', desc: 'Export the thread as a vault markdown note',
        args: '', needsRoom: true,
        run: function() { exportChatToVault(); },
    },
    {
        name: 'archive', desc: 'Archive this room (move to Archived tab)',
        args: '', needsRoom: true,
        run: function() {
            // Toggle handles unarchive too — the menu label adjusts based on status.
            toggleRoomArchive();
        },
    },
    {
        name: 'task', desc: 'Attach a task to this room and the inbox project',
        args: '<text>', needsRoom: true,
        run: function(rest) {
            var text = (rest || '').trim();
            if (!text) { EOS_UI.toast('Usage: /task <description>', false); return; }
            EOS.post('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/tasks',
                {text: text, project_id: 'inbox'})
                .then(function(res) {
                    if (res && res.error) { EOS_UI.toast(res.error, false); return; }
                    EOS_UI.toast('Task added');
                    loadRoomTaskCount(currentAgent.id);
                })
                .catch(function() { EOS_UI.toast('Failed to add task', false); });
        },
    },
    {
        name: 'find', desc: 'Search across all rooms (in-message)',
        args: '<query>',
        run: function(rest) {
            var q = (rest || '').trim();
            if (!q) { EOS_UI.toast('Usage: /find <query>', false); return; }
            var s = document.getElementById('search');
            if (s) { s.value = q; s.focus(); filterAgents(); }
        },
    },
    {
        name: 'add', desc: 'Add a participant by id (e.g. /add curator or /add @claude-cli)',
        args: '<id>', needsRoom: true,
        run: function(rest) {
            var id = (rest || '').trim().replace(/^@/, '');
            if (!id) { EOS_UI.toast('Usage: /add <id>', false); return; }
            var type = id === 'claude-cli' ? 'cli' : 'agent';
            EOS.post('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/participants',
                {type: type, id: id})
                .then(function(res) {
                    if (res && res.error) { EOS_UI.toast(res.error, false); return; }
                    EOS_UI.toast('Added ' + id);
                    loadAgents().then(function() {
                        currentAgent = (agents || []).find(function(a){ return a.id === currentAgent.id; }) || currentAgent;
                        renderMembers();
                    });
                })
                .catch(function() { EOS_UI.toast('Failed to add', false); });
        },
    },
    {
        name: 'remove', desc: 'Remove a participant by id',
        args: '<id>', needsRoom: true,
        run: function(rest) {
            var id = (rest || '').trim().replace(/^@/, '');
            if (!id) { EOS_UI.toast('Usage: /remove <id>', false); return; }
            EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) +
                '/participants/' + encodeURIComponent(id), {method:'DELETE'})
                .then(function(res) {
                    if (res && res.error) { EOS_UI.toast(res.error, false); return; }
                    EOS_UI.toast('Removed ' + id);
                    loadAgents().then(function() {
                        currentAgent = (agents || []).find(function(a){ return a.id === currentAgent.id; }) || currentAgent;
                        renderMembers();
                    });
                })
                .catch(function() { EOS_UI.toast('Failed to remove', false); });
        },
    },
    {
        name: 'remind', desc: 'Schedule a reminder for this room (e.g. /remind 2h finish review)',
        args: '<when> [note]', needsRoom: true,
        run: function(rest) { scheduleReminder(rest); },
    },
    {
        name: 'context', desc: 'Inspect what the LLM sees on the next turn',
        args: '', needsRoom: true,
        run: function() { inspectRoomContext(); },
    },
    {
        name: 'remember', desc: 'Add a persistent memory the agent always sees (e.g. /remember I prefer tabs)',
        args: '<fact>', needsRoom: true,
        run: async function(rest) {
            var fact = (rest || '').trim();
            if (!fact) { EOS_UI.toast('Usage: /remember <fact>', false); return; }
            await rememberFact(fact);
        },
    },
    {
        name: 'schedule', desc: 'Set / view the cron-driven check-in for this room',
        args: '', needsRoom: true,
        run: function() { openScheduleModal(); },
    },
    {
        name: 'snip', desc: 'Insert a saved snippet into the input by name',
        args: '<name>',
        run: function(rest) { expandSnippetToInput(rest); },
    },
    {
        name: 'save', desc: 'Save the current input as a snippet (name required)',
        args: '<name>',
        run: function(rest) { saveSnippetFromInput(rest); },
    },
    {
        name: 'snippets', desc: 'Open the snippet library',
        args: '',
        run: function() { openSnippetsLibrary(); },
    },
    {
        name: 'speak', desc: 'Toggle auto-speak (read agent replies aloud)',
        args: '', needsRoom: true,
        run: function() { toggleAutoSpeak(); },
    },
    {
        name: 'mute', desc: 'Stop any current speech and disable auto-speak',
        args: '', needsRoom: true,
        run: function() {
            speakStop();
            if (currentAgent && isAutoSpeakOn(currentAgent.id)) {
                setAutoSpeak(currentAgent.id, false);
            }
            EOS_UI.toast('Muted');
        },
    },
    {
        name: 'discard', desc: 'Discard the current draft (clears the input)',
        args: '', needsRoom: true,
        run: function() {
            if (!currentAgent) return;
            clearDraft(currentAgent.id);
            var input = document.getElementById('chat-input');
            if (input) {
                input.value = '';
                input.style.height = 'auto';
                input.focus();
            }
            EOS_UI.toast('Draft discarded');
        },
    },
    {
        name: 'pin', desc: 'Pin the most recent assistant message',
        args: '', needsRoom: true,
        run: async function() {
            try {
                var data = await EOS.api('/rooms/api/history/' + encodeURIComponent(currentAgent.id));
                var msgs = (data && data.messages) || [];
                // Walk from the end, skip user turns — we usually want to pin
                // an answer / decision, not the user's own prompt.
                for (var i = msgs.length - 1; i >= 0; i--) {
                    if (msgs[i].role !== 'user' && msgs[i].ts) {
                        togglePin(msgs[i].ts);
                        return;
                    }
                }
                EOS_UI.toast('Nothing to pin yet', false);
            } catch(e) { EOS_UI.toast('Pin failed', false); }
        },
    },
    {
        name: 'clear', desc: 'Clear chat history for this room',
        args: '', needsRoom: true,
        run: function() { clearHistory(); },
    },
    {
        name: 'help', desc: 'List slash commands',
        args: '',
        run: function() {
            var lines = SLASH_COMMANDS.map(function(c) {
                return '/' + c.name + (c.args ? ' ' + c.args : '') + ' — ' + c.desc;
            }).join('\n');
            EOS_UI.modal({title: 'Slash commands', body: '<pre style="white-space:pre-wrap;font-family:var(--font-mono,monospace);font-size:12px;line-height:1.6">' + esc(lines) + '</pre>', width: '480px'});
        },
    },
];

// State machine state added to existing _mention object: mode='slash' enters
// command-palette, mode='mention' is the existing @-popup.
function _scanSlashAtCursor(input) {
    var v = input.value || '';
    if (!v.startsWith('/')) return null;
    // Only match while the user is still typing the command name (no space yet).
    var sp = v.indexOf(' ');
    var partial = sp >= 0 ? v.slice(1, sp) : v.slice(1);
    if (sp >= 0) return null;  // past command name — let Enter fire
    return {query: partial.toLowerCase()};
}

function _scanSlashPrefilled(input) {
    // Returns {cmd, rest} when input is `/<name> <rest>` and <name> is a known command.
    var v = (input.value || '').trim();
    if (!v.startsWith('/')) return null;
    var sp = v.indexOf(' ');
    var name = (sp >= 0 ? v.slice(1, sp) : v.slice(1)).toLowerCase();
    var cmd = SLASH_COMMANDS.find(function(c){ return c.name === name; });
    if (!cmd) return null;
    var rest = sp >= 0 ? v.slice(sp + 1) : '';
    return {cmd: cmd, rest: rest};
}

function _renderSlashPopup() {
    var el = document.getElementById('mention-popup');
    if (!el) return;
    if (!_mention.items.length) {
        el.classList.remove('open'); _mention.open = false; return;
    }
    el.innerHTML = _mention.items.map(function(c, i) {
        var active = i === _mention.active ? ' active' : '';
        return '<div class="mention-item' + active + '" data-idx="' + i +
               '" onmousedown="event.preventDefault();_acceptSlash(' + i + ')">' +
               '<div class="mention-avatar" style="background:var(--accent)">/</div>' +
               '<span style="font-family:var(--font-mono,monospace);font-size:13px">/' + esc(c.name) + (c.args ? ' ' + esc(c.args) : '') + '</span>' +
               '<span class="mention-id" style="font-family:inherit">' + esc(c.desc) + '</span>' +
               '</div>';
    }).join('');
    el.classList.add('open');
    _mention.open = true;
}

function _acceptSlash(idx) {
    if (idx < 0 || idx >= _mention.items.length) return;
    var input = document.getElementById('chat-input');
    if (!input) return;
    var c = _mention.items[idx];
    if (c.args) {
        // Prefill the input so the user can type the arg, then hit Enter.
        input.value = '/' + c.name + ' ';
        var pos = input.value.length;
        input.selectionStart = input.selectionEnd = pos;
        _hideMention();
        input.focus();
    } else {
        // No args — fire immediately + clear the input.
        _hideMention();
        if (c.needsRoom && !currentAgent) {
            EOS_UI.toast('Open a room first', false);
            return;
        }
        input.value = '';
        c.run();
    }
}

// Try to fire a fully-typed slash command. Returns true if handled.
function tryRunSlashCommand() {
    var input = document.getElementById('chat-input');
    if (!input) return false;
    var match = _scanSlashPrefilled(input);
    if (!match) return false;
    if (match.cmd.needsRoom && !currentAgent) {
        EOS_UI.toast('Open a room first', false);
        return true;
    }
    input.value = '';
    input.style.height = 'auto';
    match.cmd.run(match.rest);
    return true;
}

// --- @mention autocomplete ---
//
// State machine: detect a `@<query>` token at cursor on every input event,
// render matches into the popover, navigate with ↑/↓, accept with Enter/Tab/click,
// dismiss on Esc or whitespace. Pure DOM — no framework, plays nicely with
// the existing send-on-Enter handler.

var _mention = { open: false, anchorIdx: -1, items: [], active: 0, mode: 'mention' };

function _mentionPool() {
    // Returns [{id, name, type}] for everyone in the current room except `user`.
    if (!currentAgent) return [];
    var parts = currentAgent.participants;
    if (!parts) {
        // Legacy 1:1 record loaded via /api/agents may not carry participants.
        // Synthesise the same shape the backend would on read.
        parts = [{type:'user'}, {type:'agent', id: currentAgent.id}];
    }
    return parts
        .filter(function(p) { return p.type === 'agent' || p.type === 'cli'; })
        .map(function(p) {
            return {id: p.id, name: agentNameById(p.id), type: p.type};
        });
}

function _renderMentionPopup() {
    var el = document.getElementById('mention-popup');
    if (!el) return;
    if (!_mention.items.length) {
        el.classList.remove('open');
        _mention.open = false;
        return;
    }
    el.innerHTML = _mention.items.map(function(it, i) {
        var icon, color;
        if (it.type === 'cli') {
            icon = '⚡'; color = 'var(--accent)';
        } else {
            icon = initial(it.name); color = agentColor(it.name);
        }
        var active = i === _mention.active ? ' active' : '';
        return '<div class="mention-item' + active + '" data-idx="' + i +
               '" onmousedown="event.preventDefault();_acceptMention(' + i + ')">' +
               '<div class="mention-avatar" style="background:' + color + '">' + esc(icon) + '</div>' +
               '<span>' + esc(it.name) + '</span>' +
               '<span class="mention-id">@' + esc(it.id) + '</span>' +
               '</div>';
    }).join('');
    el.classList.add('open');
    _mention.open = true;
}

function _hideMention() {
    var el = document.getElementById('mention-popup');
    if (el) el.classList.remove('open');
    _mention.open = false;
    _mention.anchorIdx = -1;
    _mention.items = [];
    _mention.active = 0;
}

// Find the @-token boundary at cursor. Returns {atIdx, query} or null.
// Trigger rules: `@` is the first char, preceded by whitespace, or at line start.
// Stops at whitespace — so "user@host" never triggers.
function _scanMentionAtCursor(input) {
    var pos = input.selectionStart || 0;
    var text = input.value || '';
    if (pos === 0) return null;
    // Walk back from cursor, looking for an `@` we'd accept.
    for (var i = pos - 1; i >= 0; i--) {
        var c = text[i];
        if (c === '@') {
            var prev = i === 0 ? ' ' : text[i - 1];
            if (/\s/.test(prev) || prev === '') {
                return {atIdx: i, query: text.slice(i + 1, pos).toLowerCase()};
            }
            return null;
        }
        if (/\s/.test(c)) return null;
    }
    return null;
}

function chatInputChanged() {
    var input = document.getElementById('chat-input');
    if (!input) return;
    // Phase 16 — auto-save draft as the user types (debounced).
    scheduleDraftSave();
    // `@@<query>` file picker — opens vault notes + repo source files.
    // Checked BEFORE wikilink so the double-@ is unambiguous, and BEFORE
    // single-@ mention so `@@app` doesn't get mis-routed into participants.
    var fileq = _scanFileAtCursor(input);
    if (fileq !== null) {
        if (_fileSearchTimer) clearTimeout(_fileSearchTimer);
        _mention.mode = 'file';
        _mention.anchorIdx = fileq.openIdx;
        _mention.active = 0;
        _fileSearchTimer = setTimeout(async function() {
            var files = await _fetchFiles(fileq.query);
            if (files === null) return;
            _mention.items = files;
            _mention.active = 0;
            _renderFileMentionPopup(files);
        }, 150);
        return;
    }
    // Wikilink mode — `[[` opens a vault file picker. Highest precedence
    // among bracket-shaped triggers; @@ above wins via earlier order.
    var wl = _scanWikilinkAtCursor(input);
    if (wl !== null) {
        if (_fileSearchTimer) clearTimeout(_fileSearchTimer);
        _mention.mode = 'wikilink';
        _mention.anchorIdx = wl.openIdx;
        _mention.active = 0;
        _fileSearchTimer = setTimeout(async function() {
            var files = await _fetchVaultFiles(wl.query);
            if (files === null) return;  // stale
            _mention.items = files;
            _mention.active = 0;
            _renderFilePopup(files);
        }, 150);
        return;
    }
    // Slash command mode takes precedence — a `/` at start trumps any @ later.
    var slash = _scanSlashAtCursor(input);
    if (slash !== null) {
        var q = slash.query;
        var matched = SLASH_COMMANDS.filter(function(c) {
            return !q || c.name.indexOf(q) >= 0;
        });
        _mention.mode = 'slash';
        _mention.items = matched;
        _mention.active = 0;
        _renderSlashPopup();
        return;
    }
    // Mention mode (existing logic).
    var match = _scanMentionAtCursor(input);
    if (!match) { _hideMention(); return; }
    var pool = _mentionPool();
    if (!pool.length) { _hideMention(); return; }
    var qq = match.query;
    var filtered = pool.filter(function(it) {
        return !qq || it.id.toLowerCase().indexOf(qq) >= 0 ||
               it.name.toLowerCase().indexOf(qq) >= 0 ||
               it.name.toLowerCase().replace(/\s+/g,'-').indexOf(qq) >= 0;
    });
    _mention.mode = 'mention';
    _mention.anchorIdx = match.atIdx;
    _mention.items = filtered;
    _mention.active = 0;
    _renderMentionPopup();
}

function _acceptMention(idx) {
    if (idx < 0 || idx >= _mention.items.length) return;
    var input = document.getElementById('chat-input');
    if (!input) return;
    var it = _mention.items[idx];
    var text = input.value || '';
    var anchor = _mention.anchorIdx;
    if (anchor < 0) return;
    var cursor = input.selectionStart || text.length;
    var before = text.slice(0, anchor);
    var after = text.slice(cursor);
    var insert = '@' + it.id + ' ';
    input.value = before + insert + after;
    var newPos = (before + insert).length;
    input.selectionStart = input.selectionEnd = newPos;
    _hideMention();
    input.focus();
}

function chatInputKeydown(event) {
    if (_mention.open) {
        var renderer = _renderMentionPopup;
        var accept = _acceptMention;
        if (_mention.mode === 'slash') {
            renderer = _renderSlashPopup; accept = _acceptSlash;
        } else if (_mention.mode === 'wikilink') {
            renderer = function() { _renderFilePopup(_mention.items); };
            accept = _acceptFile;
        } else if (_mention.mode === 'file') {
            renderer = function() { _renderFileMentionPopup(_mention.items); };
            accept = _acceptFileMention;
        }
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            _mention.active = (_mention.active + 1) % _mention.items.length;
            renderer();
            return;
        }
        if (event.key === 'ArrowUp') {
            event.preventDefault();
            _mention.active = (_mention.active - 1 + _mention.items.length) % _mention.items.length;
            renderer();
            return;
        }
        if (event.key === 'Enter' || event.key === 'Tab') {
            event.preventDefault();
            accept(_mention.active);
            return;
        }
        if (event.key === 'Escape') {
            event.preventDefault();
            _hideMention();
            return;
        }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        // Fully-typed slash command (e.g. "/task buy milk") fires here.
        if (tryRunSlashCommand()) return;
        sendMessage();
    }
}

