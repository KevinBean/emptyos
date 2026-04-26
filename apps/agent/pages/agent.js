// Agent web client — session management + WebSocket turn streaming.

(function () {
    'use strict';

    var state = {
        sessions: [],
        activeId: null,
        ws: null,
        currentAssistantEl: null,   // the .turn div for the in-progress assistant reply
        currentAssistantText: '',    // accumulated text for current assistant turn
        toolCallEls: {},             // tool_use_id → DOM node (so tool_result can fill in)
        slashCmds: [],               // loaded from /agent/api/slash-commands
        slashIdx: 0,                 // active item in palette
        statusCache: null,           // last /api/status response
        filesTouched: [],            // paths edited/written this session view
        // Per-turn accumulators for the footer (elapsed, tools, tokens, cache, cost).
        // Reset on send(), populated from agent:tool_call + agent:done events.
        turn: {start: 0, tools: 0},
        // Running session totals — mirrors the CLI's session_state dict.
        session: {in: 0, out: 0, cost: 0.0, turns: 0},
        // Plan-mode flag — toggled via /plan /execute /scrap. Server has the
        // authoritative state; client caches it for fast banner rendering.
        planMode: false,
        // Most recent TaskList tool_call input — rendered by /tasks. Updated
        // whenever the agent fires TaskList; cleared on session switch.
        tasks: [],
        // Input history (Up/Down arrows), persisted in localStorage so it
        // survives reloads. Newest at end.
        history: [],
        historyIdx: -1,  // -1 = not browsing; 0..n-1 = position from end
    };

    function registerFileTouch(path) {
        if (!path || state.filesTouched.indexOf(path) !== -1) return;
        state.filesTouched.push(path);
        renderFilesChip();
    }

    function resetFilesTouched() {
        state.filesTouched = [];
        renderFilesChip();
    }

    function renderFilesChip() {
        var $chip = document.getElementById('hdr-files');
        if (!$chip) return;
        var n = state.filesTouched.length;
        $chip.style.display = n ? '' : 'none';
        var $count = document.getElementById('hdr-files-count');
        if ($count) $count.textContent = n + (n === 1 ? ' file' : ' files');
        $chip.title = n
            ? 'Files touched this session:\n• ' + state.filesTouched.join('\n• ')
            : '';
    }

    // Deep-link routing — /agent/#<session-id> opens that session, browser
    // back/forward walks session history. Uses shared EOS_UI.hashRoute.
    var _route = EOS_UI.hashRoute({
        onShow: function (id) {
            if (id && id !== state.activeId) openSession(id, {fromRoute: true});
        },
        onHide: function () {
            // Hash cleared (user navigated to /agent/) — no session selected.
            // Leave the current UI as-is; session selection only changes on explicit clicks.
        },
    });

    var $transcript = document.getElementById('transcript');
    var $sessionList = document.getElementById('session-list');
    var $newBtn = document.getElementById('new-session-btn');
    var $input = document.getElementById('input-box');
    var $send = document.getElementById('send-btn');
    var $cancel = document.getElementById('cancel-btn');
    var $status = document.getElementById('status-bar');

    function esc(s) { return EOS_UI.esc(String(s == null ? '' : s)); }

    function setStatus(msg) { $status.textContent = msg || ''; }

    // ── Status header + slash commands ─────────────────────

    async function loadStatus(sid) {
        try {
            var url = '/agent/api/status' + (sid ? ('?session_id=' + encodeURIComponent(sid)) : '');
            var resp = await fetch(url);
            var data = await resp.json();
            state.statusCache = data;
            renderStatusHeader(data);
        } catch (e) { /* non-fatal */ }
    }

    function renderStatusHeader(data) {
        var $hdr = document.getElementById('status-header');
        if (!data) { $hdr.classList.remove('show'); return; }
        $hdr.classList.add('show');

        var $sess = document.getElementById('hdr-session');
        $sess.textContent = (data.session && data.session.name) || (data.session_id ? data.session_id : 'No session');

        var $model = document.getElementById('hdr-model');
        var $modelName = document.getElementById('hdr-model-name');
        var prov = data.provider || {};
        if (prov.available) {
            // Show "provider · model" — what's actually running.
            $modelName.textContent = prov.name + (prov.model ? (' · ' + prov.model) : '');
            $model.classList.remove('offline');
            $model.title =
                'Provider: ' + prov.name +
                (prov.model ? ' / ' + prov.model : '') +
                ' (' + (prov.kind === 'native' ? 'native agent' : 'EmptyOS tool-use loop — wire: ' + (prov.kind || '?')) + ')' +
                '. Click to change.';
        } else {
            $modelName.textContent = (prov.requested || 'offline') + ' (unavailable)';
            $model.classList.add('offline');
            $model.title = 'Provider ' + (prov.requested || '?') + ' is not available. Check settings.';
        }

        document.getElementById('hdr-tools-count').textContent = (data.tools || {}).count || 0;
        document.getElementById('hdr-policy-name').textContent = 'policy: ' + (data.policy || 'ask');
    }

    async function loadSlashCommands() {
        try {
            var resp = await fetch('/agent/api/slash-commands');
            state.slashCmds = await resp.json();
        } catch (e) { state.slashCmds = []; }
        // Merge skills as slash commands with a 'skill' kind badge
        try {
            var sr = await fetch('/agent/api/skills');
            var skills = await sr.json();
            var skillCmds = skills.map(function (s) {
                var args = (s.params && s.params.length)
                    ? s.params.map(function (p) { return '{{' + p + '}}'; }).join(' ')
                    : '';
                return { name: '/' + s.name, args: args, help: s.description, kind: 'skill' };
            });
            state.slashCmds = state.slashCmds.concat(skillCmds);
        } catch (e) {}
    }

    function checkSlashPalette() {
        var val = $input.value;
        var $p = document.getElementById('slash-palette');
        if (!val.startsWith('/')) { $p.classList.remove('show'); return; }
        // Filter to matches on the first token only ("/mo" → /model)
        var firstTok = val.split(/\s/, 1)[0].toLowerCase();
        var matches = state.slashCmds.filter(function (c) { return c.name.startsWith(firstTok); });
        if (!matches.length) { $p.classList.remove('show'); return; }
        state.slashIdx = Math.min(state.slashIdx, matches.length - 1);
        $p.innerHTML = matches.map(function (c, i) {
            return '<div class="slash-item' + (i === state.slashIdx ? ' active' : '') + '" data-name="' + esc(c.name) + '">' +
                '<span class="sc-name">' + esc(c.name) + '</span>' +
                (c.args ? '<span class="sc-args">' + esc(c.args) + '</span>' : '') +
                (c.kind === 'skill' ? '<span class="sc-badge">skill</span>' : '') +
                '<span class="sc-help">' + esc(c.help) + '</span>' +
                '</div>';
        }).join('');
        $p.classList.add('show');
        // Click-to-complete
        Array.from($p.querySelectorAll('.slash-item')).forEach(function (el) {
            el.onclick = function () {
                $input.value = el.getAttribute('data-name') + ' ';
                $p.classList.remove('show');
                $input.focus();
            };
        });
    }

    function slashPaletteNav(dir) {
        var $p = document.getElementById('slash-palette');
        if (!$p.classList.contains('show')) return false;
        var items = $p.querySelectorAll('.slash-item');
        if (!items.length) return false;
        state.slashIdx = (state.slashIdx + dir + items.length) % items.length;
        items.forEach(function (el, i) { el.classList.toggle('active', i === state.slashIdx); });
        return true;
    }

    function slashPaletteCommit() {
        var $p = document.getElementById('slash-palette');
        if (!$p.classList.contains('show')) return false;
        var active = $p.querySelector('.slash-item.active');
        if (!active) return false;
        $input.value = active.getAttribute('data-name') + ' ';
        $p.classList.remove('show');
        $input.focus();
        return true;
    }

    async function runSlashCommand(line) {
        var parts = line.trim().split(/\s+/);
        var cmd = parts[0].toLowerCase();
        var arg = parts.slice(1).join(' ').trim();

        if (cmd === '/help') {
            var turn = appendTurn('assistant', '');
            var html = '<div class="turn-content markdown"><p>Available commands:</p><ul>' +
                state.slashCmds.map(function (c) {
                    return '<li><code>' + esc(c.name) + (c.args ? ' ' + esc(c.args) : '') + '</code> — ' + esc(c.help) + '</li>';
                }).join('') + '</ul></div>';
            turn.querySelector('.turn-content').outerHTML = html;
            return;
        }
        if (cmd === '/status') {
            await loadStatus(state.activeId);
            var s = state.statusCache || {};
            var p = s.provider || {};
            var provLine = 'provider: ' + (p.name || p.requested || '—') +
                (p.model ? ' · model: ' + p.model : '') +
                (p.kind ? ' · wire: ' + p.kind : '') +
                (p.available === false ? ' [UNAVAILABLE]' : '');
            var lines = [
                'session: ' + ((s.session && s.session.name) || s.session_id || '—'),
                provLine,
                'tools: ' + ((s.tools && s.tools.count) || 0) + ' — ' + ((s.tools && s.tools.names) || []).join(', '),
                'policy: ' + (s.policy || 'ask') + ' · max_iters: ' + (s.max_iters || '?'),
            ];
            appendTurn('assistant', lines.join('\n'));
            return;
        }
        if (cmd === '/tools') {
            try {
                var resp = await fetch('/agent/api/tools');
                var tools = await resp.json();
                var turn = appendTurn('assistant', '');
                var html = '<div class="turn-content markdown"><p><strong>' + tools.length + '</strong> tools available:</p><ul>' +
                    tools.map(function (t) {
                        return '<li><code>' + esc(t.name) + '</code> ' +
                            '<span style="opacity:0.6">(perm: ' + esc(t.permission) + ')</span> — ' +
                            esc(t.description || '') + '</li>';
                    }).join('') + '</ul></div>';
                turn.querySelector('.turn-content').outerHTML = html;
            } catch (e) {
                appendTurn('assistant', 'Failed to load tools: ' + e);
            }
            return;
        }
        if (cmd === '/clear') {
            $transcript.innerHTML = '';
            setStatus('transcript cleared (session history kept)');
            return;
        }
        if (cmd === '/new') {
            await newSession();
            return;
        }
        if (cmd === '/settings') {
            if (window.openAgentSettings) window.openAgentSettings();
            return;
        }
        if (cmd === '/model') {
            if (!arg) {
                var s = state.statusCache || {};
                appendTurn('assistant',
                    'Current: ' + ((s.provider && s.provider.name) || '—') +
                    '. Pass a provider name: /model ollama | anthropic_sdk | openai | openai-mini | openai-nano | claude-cli'
                );
                return;
            }
            if (!state.activeId) { appendTurn('assistant', 'No active session.'); return; }
            var resp = await fetch('/agent/api/sessions/' + encodeURIComponent(state.activeId), {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({provider: arg}),
            });
            var body = await resp.json();
            if (body.error) { appendTurn('assistant', 'Error: ' + body.error); return; }
            await loadStatus(state.activeId);
            appendTurn('assistant', 'Session provider updated to ' + arg + '. Reconnecting…');
            // Reconnect WS so server picks up the new provider for next turn
            openWS(state.activeId);
            return;
        }
        if (cmd === '/plan' || cmd === '/execute' || cmd === '/scrap') {
            if (!state.ws || state.ws.readyState !== 1) {
                appendTurn('assistant', 'Not connected — open a session first.');
                return;
            }
            if (cmd === '/plan' && state.planMode) {
                appendTurn('assistant', 'Already in plan mode. /execute to proceed · /scrap to discard.');
                return;
            }
            if ((cmd === '/execute' || cmd === '/scrap') && !state.planMode) {
                appendTurn('assistant', 'Not in plan mode — ' + (cmd === '/execute' ? '/execute' : '/scrap') + ' is a no-op.');
                return;
            }
            state.ws.send(JSON.stringify({type: 'set_plan_mode', on: cmd === '/plan'}));
            // Optimistic — server will echo back agent:plan_mode and setPlanMode runs again.
            setPlanMode(cmd === '/plan', false);
            appendTurn('assistant',
                cmd === '/plan'  ? '⚑ Plan mode ON. Investigation only — Write/Edit/Bash-write/CallApp/RestartDaemon blocked. Describe what you want to plan.'
              : cmd === '/execute' ? '✓ Plan mode OFF — tools unblocked. Your next message nudges the agent to proceed with the drafted plan.'
              :                      '✗ Plan scrapped — plan mode OFF. Back to normal chat.'
            );
            return;
        }
        if (cmd === '/stats') {
            var s = state.session;
            if (!s.turns) {
                appendTurn('assistant', 'No turns yet this session.');
                return;
            }
            appendTurn('assistant',
                s.turns + ' turn' + (s.turns === 1 ? '' : 's') + ' · ' +
                s.in.toLocaleString() + ' in · ' + s.out.toLocaleString() + ' out · ' +
                _fmtCost(s.cost)
            );
            return;
        }
        if (cmd === '/revert') {
            if (!state.activeId) { appendTurn('assistant', 'No active session.'); return; }
            var n = 1;
            if (arg) {
                var parsed = parseInt(arg, 10);
                if (isNaN(parsed) || parsed < 1) {
                    appendTurn('assistant', 'Usage: /revert [n] — got ' + JSON.stringify(arg));
                    return;
                }
                n = parsed;
            }
            try {
                var resp = await fetch('/agent/api/sessions/' + encodeURIComponent(state.activeId) + '/revert', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({n: n}),
                });
                var result = await resp.json();
                if (result.empty) {
                    appendTurn('assistant', 'No Write/Edit to revert this session.');
                    return;
                }
                var lines = [];
                var okCount = 0;
                (result.reverted || []).forEach(function (r) {
                    if (r.ok) {
                        okCount += 1;
                        var verb = r.mode === 'deleted' ? 'deleted (was created)' : 'restored (' + (r.action || 'edit') + ')';
                        lines.push('↶ ' + verb + ' — ' + r.path);
                    } else {
                        lines.push('✗ could not revert ' + r.path + ': ' + (r.error || '?'));
                    }
                });
                if (okCount > 0) {
                    var hint = result.python_edits ? ' · daemon holds stale .py bytecode — restart to pick up' : '';
                    lines.push('');
                    lines.push('reverted ' + okCount + ' · ' + (result.remaining || 0) + ' remain on undo stack' + hint);
                }
                appendTurn('assistant', lines.join('\n'));
            } catch (e) {
                appendTurn('assistant', 'Revert failed: ' + e);
            }
            return;
        }
        if (cmd === '/skills') {
            try {
                var resp = await fetch('/agent/api/skills');
                var skills = await resp.json();
                var turn = appendTurn('assistant', '');
                if (!skills || !skills.length) {
                    turn.querySelector('.turn-content').textContent = '(no skills installed)';
                    return;
                }
                var html = '<div class="turn-content markdown"><p><strong>' + skills.length + '</strong> skills available — click to invoke:</p><ul class="skill-list">' +
                    skills.map(function (s) {
                        return '<li><button class="skill-invoke" data-name="' + esc(s.name) + '">' +
                            '<code>/' + esc(s.name) + '</code></button>' +
                            ' <span class="dim">[' + esc(s.source) + ']</span> ' +
                            esc(s.description || '(no description)') + '</li>';
                    }).join('') + '</ul></div>';
                turn.querySelector('.turn-content').outerHTML = html;
                // Wire click-to-invoke on the freshly-rendered buttons.
                document.querySelectorAll('.skill-invoke').forEach(function (btn) {
                    btn.onclick = function () {
                        $input.value = '/' + btn.getAttribute('data-name');
                        $input.focus();
                    };
                });
            } catch (e) {
                appendTurn('assistant', 'Failed to load skills: ' + e);
            }
            return;
        }
        if (cmd === '/tasks') {
            if (!state.tasks || !state.tasks.length) {
                appendTurn('assistant', 'No TaskList set this session. Ask the agent to plan something.');
                return;
            }
            var rows = state.tasks.map(function (t) {
                var mark;
                var color;
                if (t.status === 'completed')   { mark = '[x]'; color = 'var(--success, #2ea043)'; }
                else if (t.status === 'in_progress') { mark = '[~]'; color = 'var(--warning, #d4a017)'; }
                else                            { mark = '[ ]'; color = 'var(--text-muted)'; }
                return '<li><span style="color:' + color + ';font-family:var(--font-mono);">' +
                    esc(mark) + '</span> <span class="dim">' + esc(t.id || '?') + '.</span> ' +
                    esc(t.content || '') + '</li>';
            }).join('');
            var done = state.tasks.filter(function (t) { return t.status === 'completed'; }).length;
            var turn = appendTurn('assistant', '');
            turn.querySelector('.turn-content').outerHTML =
                '<div class="turn-content markdown"><ul class="task-list" style="list-style:none;padding-left:0;">' +
                rows + '</ul><p class="dim" style="font-size:11px;margin-top:8px;">' +
                done + '/' + state.tasks.length + ' done</p></div>';
            return;
        }
        if (cmd === '/sessions') {
            // The sidebar already lists them; print a compact summary inline
            // too so it shows up in the transcript for reference.
            var list = (state.sessions || []).slice(0, 20);
            if (!list.length) {
                appendTurn('assistant', 'No sessions yet.');
                return;
            }
            var turn = appendTurn('assistant', '');
            var html = '<div class="turn-content markdown"><p>Recent sessions — ● current, click an id to /resume:</p><ul style="list-style:none;padding-left:0;">' +
                list.map(function (s) {
                    var isCurrent = s.id === state.activeId;
                    var marker = isCurrent ? '● ' : '  ';
                    var last = (s.last_message || s.created || '').slice(0, 16).replace('T', ' ');
                    return '<li style="font-family:var(--font-mono);font-size:11.5px;">' +
                        marker + '<button class="resume-link" data-sid="' + esc(s.id) + '" style="background:none;border:none;color:var(--accent);cursor:pointer;padding:0;">' +
                        esc(s.id) + '</button>' +
                        ' <span class="dim">' + esc((s.name || '').slice(0, 28)) + '</span>' +
                        ' <span class="dim">· ' + (s.message_count || 0) + ' msgs · ' + esc(last) + '</span></li>';
                }).join('') + '</ul></div>';
            turn.querySelector('.turn-content').outerHTML = html;
            document.querySelectorAll('.resume-link').forEach(function (btn) {
                btn.onclick = function () { openSession(btn.getAttribute('data-sid')); };
            });
            return;
        }
        if (cmd === '/resume') {
            if (!arg) {
                appendTurn('assistant', 'Usage: /resume <id-prefix-or-name>');
                return;
            }
            var needle = arg.toLowerCase();
            var exact = (state.sessions || []).filter(function (s) { return s.id.toLowerCase().startsWith(needle); });
            var named = (state.sessions || []).filter(function (s) { return (s.name || '').toLowerCase().indexOf(needle) >= 0; });
            var candidates = exact.length ? exact : named;
            if (!candidates.length) {
                appendTurn('assistant', 'No session matches ' + JSON.stringify(arg) + '. Try /sessions.');
                return;
            }
            if (candidates.length > 1 && !exact.length) {
                appendTurn('assistant', 'Ambiguous — ' + candidates.length + ' matches. Use a more specific id/name.');
                return;
            }
            openSession(candidates[0].id);
            return;
        }
        if (cmd === '/rename') {
            if (!arg) {
                appendTurn('assistant', 'Usage: /rename <new name>');
                return;
            }
            if (!state.activeId) {
                appendTurn('assistant', 'No active session.');
                return;
            }
            try {
                var resp = await fetch('/agent/api/sessions/' + encodeURIComponent(state.activeId), {
                    method: 'PATCH',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({name: arg}),
                });
                var body = await resp.json();
                if (body.error) {
                    appendTurn('assistant', 'Error: ' + body.error);
                    return;
                }
                await loadSessions();  // refresh sidebar label
                appendTurn('assistant', 'Renamed to ' + arg + '.');
            } catch (e) {
                appendTurn('assistant', 'Rename failed: ' + e);
            }
            return;
        }
        if (cmd === '/delete') {
            if (!arg) {
                appendTurn('assistant', 'Usage: /delete <id-prefix>. The × button in the sidebar also works.');
                return;
            }
            var needle = arg.toLowerCase();
            var match = (state.sessions || []).filter(function (s) { return s.id.toLowerCase().startsWith(needle); });
            if (!match.length) {
                appendTurn('assistant', 'No session matches ' + JSON.stringify(arg) + '.');
                return;
            }
            if (match.length > 1) {
                appendTurn('assistant', 'Ambiguous — use a longer prefix.');
                return;
            }
            var target = match[0];
            if (target.id === state.activeId) {
                appendTurn('assistant', 'Cannot delete the active session. /resume another one first.');
                return;
            }
            await deleteSession(target.id);
            appendTurn('assistant', 'Deleted ' + target.id + ' (' + (target.name || '') + ').');
            return;
        }
        if (cmd === '/quit' || cmd === '/exit') {
            appendTurn('assistant', '(Use the × button to delete the session; /quit is a CLI-only command.)');
            return;
        }
        appendTurn('assistant', 'Unknown command: ' + cmd + '. Try /help.');
    }

    // ── Sessions ───────────────────────────────────────────

    async function loadSessions() {
        var resp = await fetch('/agent/api/sessions');
        var list = await resp.json();
        state.sessions = list || [];
        renderSessionList();
    }

    function renderSessionList() {
        $sessionList.innerHTML = '';
        var $count = document.getElementById('session-count');
        if ($count) {
            var total = state.sessions.length;
            var empty = state.sessions.filter(function (s) { return !s.message_count; }).length;
            $count.textContent = total ? (total + ' session' + (total === 1 ? '' : 's') + (empty ? ' · ' + empty + ' empty' : '')) : '';
        }
        state.sessions.forEach(function (s) {
            var el = document.createElement('div');
            el.className = 'session-item' + (s.id === state.activeId ? ' active' : '');
            var count = s.message_count || 0;
            el.innerHTML =
                '<div class="s-body">' +
                  '<div>' + esc(s.name || 'Untitled') + '</div>' +
                  '<span class="meta">' + esc(s.id) + ' · ' + count + (count === 1 ? ' msg' : ' msgs') + '</span>' +
                '</div>' +
                '<span class="sess-actions">' +
                  (count > 0 ? '<button class="arch" title="Archive session to vault">⬇</button>' : '') +
                  '<button class="del" title="Delete session" data-sid="' + esc(s.id) + '">×</button>' +
                '</span>';
            el.onclick = function (ev) {
                if (ev.target && ev.target.classList && (ev.target.classList.contains('del') || ev.target.classList.contains('arch'))) return;
                openSession(s.id);
            };
            var delBtn = el.querySelector('.del');
            delBtn.onclick = function (ev) {
                ev.stopPropagation();
                deleteSession(s.id);
            };
            var archBtn = el.querySelector('.arch');
            if (archBtn) {
                (function (sid) {
                    archBtn.onclick = function (ev) {
                        ev.stopPropagation();
                        archiveSession(sid);
                    };
                })(s.id);
            }
            $sessionList.appendChild(el);
        });
    }

    async function deleteSession(sid) {
        var wasActive = (sid === state.activeId);
        await fetch('/agent/api/sessions/' + encodeURIComponent(sid), {method: 'DELETE'});
        if (wasActive) {
            if (state.ws) try { state.ws.close(); } catch (e) {}
            state.activeId = null;
            $transcript.innerHTML = '';
            _route.clear();
        }
        await loadSessions();
    }

    async function archiveSession(sid) {
        EOS_UI.toast('Summarising session…', 'info');
        try {
            var resp = await fetch('/agent/api/sessions/' + encodeURIComponent(sid) + '/archive', {method: 'POST'});
            var data = await resp.json();
            if (data.ok) {
                var msg = 'Archived to vault.';
                if (data.note_path) msg += ' ' + data.note_path;
                EOS_UI.toast(msg, 'success');
                // If this is the active session, show it in the transcript too.
                if (sid === state.activeId) {
                    var out = 'Session archived to vault.\n\n**path** `' + (data.note_path || '') + '`';
                    if (data.url) out += '\n\n[Open in vault](' + data.url + ')';
                    appendTurn('assistant', out);
                }
            } else {
                EOS_UI.toast('Archive failed: ' + (data.error || 'unknown'), 'error');
            }
        } catch (e) {
            EOS_UI.toast('Archive error: ' + e.message, 'error');
        }
    }

    async function clearEmptySessions() {
        var empties = state.sessions.filter(function (s) { return !s.message_count; });
        if (!empties.length) return;
        var ok = await EOS_UI.confirm({
            message: 'Delete ' + empties.length + ' empty session' + (empties.length === 1 ? '' : 's') + '?',
            action: 'Delete',
        });
        if (!ok) return;
        for (var i = 0; i < empties.length; i++) {
            await fetch('/agent/api/sessions/' + encodeURIComponent(empties[i].id), {method: 'DELETE'});
            if (empties[i].id === state.activeId) {
                if (state.ws) try { state.ws.close(); } catch (e) {}
                state.activeId = null;
                $transcript.innerHTML = '';
                _route.clear();
            }
        }
        await loadSessions();
    }

    async function newSession() {
        var resp = await fetch('/agent/api/sessions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: 'New chat'}),
        });
        var sess = await resp.json();
        await loadSessions();
        await openSession(sess.id);
    }

    async function openSession(sid, opts) {
        opts = opts || {};
        state.activeId = sid;
        renderSessionList();
        // Keep URL in sync unless we're already responding to a hash change.
        if (!opts.fromRoute) _route.set(sid);
        $transcript.innerHTML = '';
        state.currentAssistantEl = null;
        state.toolCallEls = {};
        resetFilesTouched();
        // Fresh view — reset session-level accumulators + plan-mode banner.
        // Server side is authoritative for plan mode (it tracks per-session-id),
        // but the banner should clear optimistically; server will echo on first turn.
        state.session = {in: 0, out: 0, cost: 0.0, turns: 0};
        updateSessionCostBadge();
        state.tasks = [];
        setPlanMode(false, false);
        renderTaskListPanel();  // hides the panel since tasks is empty
        refreshUndoChip();  // sync for the session we just opened

        // Restore persisted task list (survives daemon restart)
        try {
            var tr = await fetch('/agent/api/sessions/' + encodeURIComponent(sid) + '/tasks');
            var td = await tr.json();
            if (td.tasks && td.tasks.length) {
                state.tasks = td.tasks;
                renderTaskListPanel();
            }
        } catch (e) {}

        var resp = await fetch('/agent/api/sessions/' + encodeURIComponent(sid));
        var sess = await resp.json();
        var expected = ((state.sessions || []).find(function(x){return x.id===sid}) || {}).message_count || 0;
        var got = (sess.messages || []).length;
        if (expected > 0 && got === 0) {
            console.warn('[agent] session', sid, 'expected', expected, 'messages but got 0 — data-layer bug?');
        }
        if (sess.messages && sess.messages.length) {
            sess.messages.forEach(renderHistoricalMessage);
        } else {
            $transcript.innerHTML = '<div id="empty-state" style="padding:40px 20px;text-align:center;color:var(--text-secondary);font-size:13px">Send a message, or type <code>/help</code> for commands.</div>';
        }

        openWS(sid);
        loadStatus(sid);
    }

    function renderHistoricalMessage(msg) {
        var role = msg.role;
        var content = msg.content;
        if (role === 'user') {
            if (typeof content === 'string') {
                appendTurn('user', content);
            } else if (Array.isArray(content)) {
                // tool_result blocks — render as a separator
                var toolResults = content.filter(function (b) { return b && b.type === 'tool_result'; });
                if (toolResults.length) {
                    toolResults.forEach(function (r) {
                        renderToolResultBlock(r.tool_use_id, r.content, !!r.is_error);
                    });
                }
            }
        } else if (role === 'assistant') {
            if (typeof content === 'string') {
                // Historical string content — render as markdown.
                var turn = startAssistantTurn();
                finalizeAssistantTurn(turn, content);
            } else if (Array.isArray(content)) {
                var turn = startAssistantTurn();
                // Accumulate text across blocks so markdown boundaries (code fences,
                // lists) that span segments render correctly.
                var buf = '';
                content.forEach(function (b) {
                    if (b.type === 'text') {
                        buf += (b.text || '');
                    } else if (b.type === 'tool_use') {
                        // Flush accumulated text, then insert tool-call inline
                        if (buf) { finalizeAssistantTurn(turn, buf, {append: true}); buf = ''; }
                        appendToolCall(turn, b.id, b.name, b.input);
                    }
                });
                if (buf) finalizeAssistantTurn(turn, buf, {append: true});
                state.currentAssistantEl = null; // historical turn complete
            }
        } else if (role === 'tool') {
            renderToolResultBlock(msg.tool_call_id, content, false);
        }
    }

    function finalizeAssistantTurn(turnEl, text, opts) {
        opts = opts || {};
        var content = turnEl.querySelector('.turn-content');
        if (!content) return;
        content.classList.remove('streaming');
        turnEl.classList.remove('eos-ai-streaming');
        content.classList.add('markdown');
        var html = (window.EOS_UI && EOS_UI.renderMarkdown)
            ? EOS_UI.renderMarkdown(text || '')
            : esc(text || '');
        if (opts.append) content.innerHTML += html;
        else content.innerHTML = html;
        $transcript.scrollTop = $transcript.scrollHeight;
    }

    // ── Rendering helpers ──────────────────────────────────

    function clearEmptyState() {
        var e = document.getElementById('empty-state');
        if (e) e.remove();
    }

    function appendTurn(role, text) {
        clearEmptyState();
        var el = document.createElement('div');
        el.className = 'turn ' + role;
        el.innerHTML =
            '<div class="turn-role ' + role + '">' + role + '</div>' +
            '<div class="turn-content">' + esc(text) + '</div>';
        $transcript.appendChild(el);
        $transcript.scrollTop = $transcript.scrollHeight;
        return el;
    }

    function startAssistantTurn() {
        clearEmptyState();
        var el = document.createElement('div');
        el.className = 'turn assistant eos-ai-streaming';
        el.innerHTML =
            '<div class="turn-role assistant">assistant</div>' +
            '<div class="turn-content streaming"></div>';
        $transcript.appendChild(el);
        $transcript.scrollTop = $transcript.scrollHeight;
        return el;
    }

    function appendAssistantText(turnEl, text) {
        var content = turnEl.querySelector('.turn-content');
        // During streaming we stay in plain-text mode; markdown renders on done.
        content.textContent = (content.textContent || '') + text;
        state.currentAssistantText = (state.currentAssistantText || '') + text;
        $transcript.scrollTop = $transcript.scrollHeight;
    }

    function appendToolCall(turnEl, id, name, input) {
        var tc = document.createElement('div');
        tc.className = 'tool-call';
        tc.setAttribute('data-tool-id', id);
        var inputJson = '';
        try { inputJson = JSON.stringify(input || {}, null, 2); } catch (e) {}
        tc.innerHTML =
            '<div class="tc-header">' +
                '<span class="tc-name">' + esc(name) + '</span>' +
                '<span class="tc-status">running…</span>' +
            '</div>' +
            '<details><summary>input</summary><pre>' + esc(inputJson) + '</pre></details>' +
            '<div class="tc-result" style="display:none"><details><summary>output</summary><pre></pre></details></div>';
        turnEl.appendChild(tc);
        state.toolCallEls[id] = tc;
        $transcript.scrollTop = $transcript.scrollHeight;
    }

    function renderDiffHtml(diff) {
        // Colorize a unified-diff string. Each line gets a CSS class so theme
        // tokens control the palette. Falls back to plain <pre> if empty.
        if (!diff) return '';
        var out = diff.split('\n').map(function (line) {
            var cls = 'd-ctx';
            if (line.startsWith('+++') || line.startsWith('---')) cls = 'd-hdr';
            else if (line.startsWith('@@')) cls = 'd-hunk';
            else if (line.startsWith('+')) cls = 'd-add';
            else if (line.startsWith('-')) cls = 'd-del';
            return '<span class="' + cls + '">' + esc(line) + '</span>';
        }).join('\n');
        return '<pre class="diff">' + out + '</pre>';
    }

    function renderToolDisplayExtras(display) {
        // Build tool-specific rich views (diff, preview, bash exit/stderr).
        // Returns HTML appended above the raw output <pre>.
        if (!display || typeof display !== 'object') return '';
        var parts = [];
        if (display.diff) {
            parts.push('<div class="tc-section"><div class="tc-section-label">diff</div>' +
                renderDiffHtml(display.diff) + '</div>');
        }
        if (display.preview) {
            parts.push('<div class="tc-section"><div class="tc-section-label">preview</div>' +
                '<pre class="diff">' + esc(display.preview) + '</pre></div>');
        }
        if (display.path && (display.bytes_delta !== undefined || display.action)) {
            var meta = esc(display.path);
            if (display.bytes_delta !== undefined) {
                var d = display.bytes_delta;
                meta += ' · ' + (d >= 0 ? '+' : '') + d + ' bytes';
            }
            if (display.action) meta += ' · ' + esc(display.action);
            if (display.replacements !== undefined) meta += ' · ' + display.replacements + ' replacement(s)';
            parts.push('<div class="tc-meta">' + meta + '</div>');
        }
        if (display.exit_code !== undefined) {
            var ok = display.exit_code === 0;
            parts.push('<div class="tc-meta">' +
                '<span class="tc-exit ' + (ok ? 'ok' : 'bad') + '">exit ' + display.exit_code + '</span>' +
                (display.command ? ' <code>' + esc(display.command) + '</code>' : '') +
                '</div>');
        }
        return parts.join('');
    }

    function markToolResult(id, isError, resultText, display) {
        var tc = state.toolCallEls[id];
        if (!tc) return;
        tc.querySelector('.tc-status').textContent = isError ? 'error' : 'done';
        if (isError) tc.classList.add('tool-error');

        // Track files touched by Edit/Write for the session file chip
        if (display && display.path && (display.action || display.replacements !== undefined)) {
            registerFileTouch(display.path);
        }

        var extras = renderToolDisplayExtras(display);
        var result = tc.querySelector('.tc-result');
        result.style.display = '';
        // Replace the wrapper with extras + (optional) raw output
        var inner = extras;
        if (resultText) {
            inner += '<details' + (extras ? '' : ' open') + '><summary>output</summary>' +
                '<pre>' + esc(resultText) + '</pre></details>';
        }
        result.innerHTML = inner;
    }

    function renderToolResultBlock(toolUseId, content, isError, display) {
        // Used for historical replay
        var text = typeof content === 'string' ? content :
                   (Array.isArray(content) ? content.map(function (b) { return b && b.text || ''; }).join('') : JSON.stringify(content));
        markToolResult(toolUseId, isError, text, display);
    }

    // ── WebSocket ──────────────────────────────────────────

    function openWS(sid) {
        if (state.ws) try { state.ws.close(); } catch (e) {}
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var url = proto + '//' + location.host + '/agent/ws/' + encodeURIComponent(sid);
        var ws = new WebSocket(url);
        state.ws = ws;
        ws.onopen = function () { setStatus('connected'); };
        ws.onclose = function () { setStatus('disconnected'); };
        ws.onerror = function () { setStatus('connection error'); };
        ws.onmessage = function (ev) {
            var msg;
            try { msg = JSON.parse(ev.data); } catch (e) { return; }
            handleWSEvent(msg);
        };
    }

    // ── Per-turn footer + inline notices ──────────────────

    // Pricing tables live on the server (openai_compat.PRICING +
    // anthropic_sdk.PRICING) — every tool-capable provider populates
    // `usage.cost` server-side so the client doesn't need its own
    // per-model rate table (which would drift independently — see the
    // bug that prompted this cleanup). If `usage.cost` is missing we
    // just show the footer without a cost number — never fabricate one.

    function _fmtCost(c) {
        if (!c || c <= 0) return '$0';
        if (c < 0.0001) return '<$0.0001';
        return '$' + c.toFixed(4);
    }

    function renderTurnFooter(usage) {
        if (!state.currentAssistantEl) return;
        var pt = parseInt(usage.prompt_tokens || usage.input_tokens || 0, 10) || 0;
        var ct = parseInt(usage.completion_tokens || usage.output_tokens || 0, 10) || 0;
        var cached = parseInt(usage.cached_tokens || usage.cache_read_input_tokens || 0, 10) || 0;
        var elapsed = state.turn.start ? (Date.now() - state.turn.start) / 1000 : 0;

        // Server is single source of truth for cost (handles cache discounts
        // + provider-specific rate tables). Missing cost → omit the $ column
        // rather than fabricating; never show a wrong number.
        var cost = parseFloat(usage.cost);
        if (!isFinite(cost) || cost < 0) cost = 0;

        state.session.in += pt;
        state.session.out += ct;
        state.session.cost += cost;
        state.session.turns += 1;

        var parts = [elapsed.toFixed(1) + 's'];
        var total = pt + ct;
        if (total > 0) parts.push(total.toLocaleString() + ' tokens');
        if (cached > 0 && pt > 0) parts.push(Math.round(100 * cached / pt) + '% cache');
        if (state.turn.tools > 0) parts.push(state.turn.tools + ' tool' + (state.turn.tools === 1 ? '' : 's'));
        if (cost > 0) parts.push(_fmtCost(cost));
        if (state.planMode) parts.push('plan mode');

        var footer = document.createElement('div');
        footer.className = 'turn-footer';
        footer.innerHTML = '· ' + parts.map(esc).join('<span class="sep">·</span>');
        state.currentAssistantEl.appendChild(footer);
        updateSessionCostBadge();
        scrollToBottom();
    }

    function updateSessionCostBadge() {
        var cost = state.session.cost || 0;
        var el = document.getElementById('hdr-cost');
        var val = document.getElementById('hdr-cost-val');
        if (!el || !val) return;
        if (cost <= 0) { el.style.display = 'none'; return; }
        el.style.display = '';
        val.textContent = _fmtCost(cost);
        el.title = state.session.turns + ' turn' + (state.session.turns === 1 ? '' : 's') +
            ' · ' + (state.session.in + state.session.out).toLocaleString() + ' tokens total';
    }

    function addInlineNotice(text, cls) {
        var el = document.createElement('div');
        el.className = 'inline-notice ' + (cls || '');
        el.textContent = text;
        $transcript.appendChild(el);
        scrollToBottom();
    }

    function surfaceNudgesFromContent(content) {
        if (!content || typeof content !== 'string') return;
        // These markers are appended by run_turn's safety reflexes
        // (loop.py). Parse them out and render as inline notices so
        // the user sees what the model is seeing.
        if (content.indexOf('[daemon-hint]') >= 0) {
            addInlineNotice(
                '⚠ Python edit detected — daemon still holds the old bytecode. ' +
                'Restart it (restart.bat or RestartDaemon tool) then Fetch to verify.',
                'notice-hint'
            );
        }
        if (content.indexOf('[loop-guard]') >= 0) {
            addInlineNotice(
                '✋ Multiple errors in a row. Agent was nudged to stop retrying and re-plan.',
                'notice-loop'
            );
        }
        if (content.indexOf('[plan mode]') >= 0) {
            addInlineNotice(
                '⚑ A tool was blocked by plan mode. ' +
                'Use /execute to leave plan mode and proceed, or /scrap to discard.',
                'notice-hint'
            );
        }
    }

    // ── Live TaskList panel ─────────────────────────────────
    // Sticky panel between the header and the transcript; auto-populated
    // from state.tasks whenever the agent calls TaskList. Hidden when the
    // session has no task list. Collapsible via ▾ chevron.

    function renderTaskListPanel() {
        var $panel = document.getElementById('tasklist-panel');
        if (!$panel) return;
        if (!state.tasks || !state.tasks.length) {
            $panel.classList.remove('on');
            return;
        }
        $panel.classList.add('on');
        var $body = document.getElementById('tasklist-body');
        var $progress = document.getElementById('tasklist-progress');
        if (!$body || !$progress) return;
        var done = 0;
        var rows = state.tasks.map(function (t) {
            var status = (t.status || 'pending').toLowerCase();
            if (status === 'completed') done += 1;
            var mark;
            if (status === 'completed')   mark = '✓';
            else if (status === 'in_progress') mark = '▸';
            else                           mark = '·';
            return (
                '<div class="task-row ' + esc(status) + '">' +
                  '<span class="mark">' + mark + '</span>' +
                  '<span class="id">' + esc(String(t.id || '?')) + '</span>' +
                  '<span class="content">' + esc(t.content || '') + '</span>' +
                '</div>'
            );
        }).join('');
        $body.innerHTML = rows;
        var inProgress = state.tasks.filter(function (t) { return (t.status || '').toLowerCase() === 'in_progress'; }).length;
        $progress.textContent = done + '/' + state.tasks.length + ' done' +
            (inProgress ? ' · ' + inProgress + ' in progress' : '');
    }

    function wireTaskListPanel() {
        var $hdr = document.getElementById('tasklist-header');
        var $panel = document.getElementById('tasklist-panel');
        if ($hdr && $panel) {
            $hdr.onclick = function () {
                $panel.classList.toggle('collapsed');
                var $chev = document.getElementById('tasklist-chevron');
                if ($chev) $chev.textContent = $panel.classList.contains('collapsed') ? '▸' : '▾';
            };
        }
    }

    // ── Undo chip (live count of revertable Write/Edit entries) ──

    async function refreshUndoChip() {
        if (!state.activeId) {
            var $c = document.getElementById('hdr-undo');
            if ($c) $c.style.display = 'none';
            return;
        }
        try {
            var resp = await fetch('/agent/api/sessions/' + encodeURIComponent(state.activeId) + '/edit-stack');
            var stack = await resp.json();
            var n = Array.isArray(stack) ? stack.length : 0;
            var $chip = document.getElementById('hdr-undo');
            var $count = document.getElementById('hdr-undo-count');
            if (!$chip) return;
            if (n === 0) {
                $chip.style.display = 'none';
            } else {
                $chip.style.display = '';
                if ($count) $count.textContent = String(n);
                $chip.title = 'Revert the last of ' + n + ' Write/Edit change' + (n === 1 ? '' : 's') + ' this session';
            }
        } catch (e) {
            // Silent — chip stays in whatever state it was. The user can still use /revert.
        }
    }

    var $planBanner = null;
    function setPlanMode(on, announce) {
        state.planMode = !!on;
        if (!$planBanner) $planBanner = document.getElementById('plan-banner');
        if ($planBanner) {
            if (on) $planBanner.classList.add('on');
            else $planBanner.classList.remove('on');
        }
        if (announce) {
            setStatus(on ? 'plan mode ON' : 'plan mode OFF');
        }
    }

    function scrollToBottom() {
        if ($transcript) $transcript.scrollTop = $transcript.scrollHeight;
    }

    function handleWSEvent(msg) {
        var type = msg.type;
        if (type === 'agent:turn_start') {
            state.currentAssistantEl = null;
            state.currentAssistantText = '';
            state.turn = {start: Date.now(), tools: 0};
            setStatus('thinking…');
            $cancel.style.display = '';
        } else if (type === 'agent:text') {
            if (!state.currentAssistantEl) state.currentAssistantEl = startAssistantTurn();
            appendAssistantText(state.currentAssistantEl, msg.delta || '');
        } else if (type === 'agent:tool_call') {
            if (!state.currentAssistantEl) state.currentAssistantEl = startAssistantTurn();
            appendToolCall(state.currentAssistantEl, msg.id, msg.name, msg.input);
            state.turn.tools += 1;
            // Cache TaskList input so /tasks can re-render it on demand (the
            // tool is stateless — every call carries the full list).
            if (msg.name === 'TaskList' && msg.input && Array.isArray(msg.input.tasks)) {
                state.tasks = msg.input.tasks.slice();
                renderTaskListPanel();  // live update the sticky panel
            }
            setStatus('calling ' + msg.name + '…');
        } else if (type === 'agent:skill_loaded') {
            // Server loaded a /<skill-name> invocation. Surface a dim notice so
            // the user sees which playbook is running (same UX as the CLI).
            addInlineNotice('⚑ loaded skill ' + (msg.name || '(unknown)'), 'notice-compaction');
        } else if (type === 'agent:tool_result') {
            markToolResult(msg.id, !!msg.is_error, msg.content || '', msg.display || {});
            // Surface server-side nudges in the transcript (daemon-hint, loop-guard,
            // plan-mode gate) as dim inline notices so the user sees them, not just
            // the model. The tool_result content carries these markers.
            surfaceNudgesFromContent(msg.content || '');
        } else if (type === 'agent:permission_requested') {
            EOS_UI.agentPermission({
                id: msg.id, session_id: msg.session_id, tool: msg.tool,
                input: msg.input, summary: msg.summary,
            });
        } else if (type === 'agent:compacted') {
            // Session compaction fired — mirror the CLI's dim footer line.
            addInlineNotice(
                '· compacted history — saved ~' + (msg.chars_saved || 0).toLocaleString() +
                ' chars (' + (msg.message_count || 0) + ' messages)',
                'notice-compaction'
            );
        } else if (type === 'agent:plan_mode') {
            // Server echoes the flag after /plan /execute /scrap so we can
            // trust it (ignores client-side drift if the user has two tabs open).
            setPlanMode(!!msg.on, /*announce=*/true);
        } else if (type === 'agent:orient') {
            var plan = msg.plan || {};
            var lines = [];
            if (plan.task_type) {
                var hdr = plan.task_type;
                if (plan.subject) hdr += ': ' + plan.subject;
                lines.push('**' + hdr + '**');
            }
            (plan.relevant_rules || []).forEach(function (r) { lines.push('• ' + r); });
            if (plan.success_criteria) lines.push('**Done when:** ' + plan.success_criteria);
            (plan.risk_flags || []).forEach(function (f) { lines.push('⚠ ' + f); });
            if (lines.length) addInlineNotice(lines.join('\n'), 'orient-notice');
        } else if (type === 'agent:slash_result') {
            // Server-side slash command response (/context etc.)
            var turn = appendTurn('assistant', '');
            renderMarkdown(turn.querySelector('.turn-content'), msg.text || '');
        } else if (type === 'agent:done') {
            renderTurnFooter(msg.usage || {});
            setStatus('done');
            finalizeCurrentAssistant();
            $cancel.style.display = 'none';
            // Undo-chip count can grow during a turn (Write/Edit ran) — refresh.
            refreshUndoChip();
        } else if (type === 'agent:cancelled') {
            setStatus('cancelled');
            finalizeCurrentAssistant();
            $cancel.style.display = 'none';
        } else if (type === 'agent:max_iters') {
            setStatus('stopped at max iterations');
            finalizeCurrentAssistant();
            $cancel.style.display = 'none';
        } else if (type === 'agent:status') {
            setStatus(msg.status || '');
        } else if (type === 'agent:error') {
            setStatus('error: ' + (msg.error || ''));
            finalizeCurrentAssistant();
            $cancel.style.display = 'none';
        } else if (type === 'error') {
            setStatus('error: ' + (msg.message || ''));
        }
    }

    function finalizeCurrentAssistant() {
        if (state.currentAssistantEl && state.currentAssistantText) {
            finalizeAssistantTurn(state.currentAssistantEl, state.currentAssistantText);
        }
        state.currentAssistantEl = null;
        state.currentAssistantText = '';
    }

    // ── Input handling ─────────────────────────────────────

    async function send() {
        var text = $input.value.trim();
        if (!text) return;
        // Slash commands execute client-side, never hit the LLM.
        if (text.startsWith('/')) {
            document.getElementById('slash-palette').classList.remove('show');
            $input.value = '';
            $input.style.height = 'auto';
            appendTurn('user', text);
            await runSlashCommand(text);
            return;
        }
        if (!state.activeId) {
            setStatus('No active session — click + New session first.');
            return;
        }
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
            setStatus('WebSocket not connected.');
            return;
        }
        appendTurn('user', text);
        state.ws.send(JSON.stringify({type: 'message', text: text}));
        $input.value = '';
        $input.style.height = 'auto';
    }

    // Mobile drawer: clicking a session closes the drawer, tap outside closes too.
    var $sbToggle = document.getElementById('sidebar-toggle');
    var $sbScrim = document.getElementById('sidebar-scrim');
    function closeDrawer() { document.body.classList.remove('sidebar-open'); }
    function openDrawer() { document.body.classList.add('sidebar-open'); }
    // Restore persisted collapsed state on desktop.
    try {
        if (window.innerWidth > 720 && localStorage.getItem('agent.sidebarHidden') === '1') {
            document.body.classList.add('sidebar-hidden');
        }
    } catch (e) {}
    function toggleDesktopSidebar() {
        document.body.classList.toggle('sidebar-hidden');
        try {
            localStorage.setItem('agent.sidebarHidden',
                document.body.classList.contains('sidebar-hidden') ? '1' : '0');
        } catch (e) {}
    }
    if ($sbToggle) $sbToggle.onclick = function () {
        if (window.innerWidth <= 720) {
            // Mobile: drawer behaviour
            if (document.body.classList.contains('sidebar-open')) closeDrawer();
            else openDrawer();
        } else {
            // Desktop: only shown when sidebar is hidden → re-expand
            toggleDesktopSidebar();
        }
    };
    var $hdrToggle = document.getElementById('hdr-sidebar-toggle');
    if ($hdrToggle) $hdrToggle.onclick = function () {
        if (window.innerWidth <= 720) {
            if (document.body.classList.contains('sidebar-open')) closeDrawer();
            else openDrawer();
        } else {
            toggleDesktopSidebar();
        }
    };
    if ($sbScrim) $sbScrim.onclick = closeDrawer;
    document.getElementById('session-list').addEventListener('click', function (ev) {
        if (ev.target.closest('.session-item') && window.innerWidth <= 720) closeDrawer();
    });

    $send.onclick = send;
    $cancel.onclick = function () {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({type: 'cancel'}));
        }
    };
    $newBtn.onclick = newSession;
    var $clearEmptyBtn = document.getElementById('clear-empty-btn');
    if ($clearEmptyBtn) $clearEmptyBtn.onclick = clearEmptySessions;

    // Header-chip shortcuts
    var $hdrModel = document.getElementById('hdr-model');
    if ($hdrModel) $hdrModel.onclick = function () {
        // Resolve current provider from the status data last loaded into the chip.
        var currentProv = '';
        try {
            var nm = document.getElementById('hdr-model-name').textContent || '';
            currentProv = nm.split(' · ')[0].trim();  // "openai · gpt-5.4-mini" → "openai"
        } catch (e) {}
        EOS_UI.tierPicker({
            current: currentProv,
            title: 'Model tier for this session',
            onSelect: function (choice) {
                $input.value = '/model ' + choice.provider;
                send();
            },
        });
    };
    var $hdrTools = document.getElementById('hdr-tools');
    if ($hdrTools) $hdrTools.onclick = function () {
        $input.value = '/tools'; send();
    };
    var $hdrPolicy = document.getElementById('hdr-policy');
    if ($hdrPolicy) $hdrPolicy.onclick = function () {
        if (window.openAgentSettings) window.openAgentSettings();
    };
    var $hdrUndo = document.getElementById('hdr-undo');
    if ($hdrUndo) $hdrUndo.onclick = function () {
        $input.value = '/revert';
        send();
    };

    // ── Input history (localStorage, Up/Down arrows) ──────
    var HISTORY_KEY = 'eos.agent.input_history';
    var HISTORY_MAX = 100;
    try {
        state.history = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') || [];
    } catch (e) { state.history = []; }

    function pushHistory(text) {
        if (!text || !text.trim()) return;
        // Skip duplicates of the previous entry so "Enter, up-arrow, edit, Enter"
        // doesn't flood the buffer with near-misses.
        if (state.history.length && state.history[state.history.length - 1] === text) return;
        state.history.push(text);
        if (state.history.length > HISTORY_MAX) state.history = state.history.slice(-HISTORY_MAX);
        try { localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history)); } catch (e) {}
    }

    function historyNav(delta) {
        if (!state.history.length) return false;
        // Only navigate when the slash palette isn't showing AND the caret is
        // on the first (Up) / last (Down) line — lets Up/Down still move
        // between lines inside a multi-line composition.
        var palette = document.getElementById('slash-palette');
        if (palette && palette.classList.contains('show')) return false;
        var before = $input.value.substring(0, $input.selectionStart);
        var after = $input.value.substring($input.selectionEnd);
        if (delta < 0 && before.indexOf('\n') >= 0) return false;   // not on first line
        if (delta > 0 && after.indexOf('\n') >= 0) return false;    // not on last line
        if (state.historyIdx === -1 && delta > 0) return false;     // already at bottom
        if (state.historyIdx === -1) state.historyIdx = state.history.length;
        state.historyIdx = Math.max(0, Math.min(state.history.length, state.historyIdx + delta));
        $input.value = (state.historyIdx >= state.history.length) ? '' : state.history[state.historyIdx];
        // Resize to fit the loaded value.
        $input.style.height = 'auto';
        $input.style.height = Math.min(160, $input.scrollHeight) + 'px';
        return true;
    }

    $input.addEventListener('keydown', function (e) {
        // Palette navigation takes priority when it's open.
        if (e.key === 'ArrowDown') { if (slashPaletteNav(+1)) { e.preventDefault(); return; } }
        if (e.key === 'ArrowUp')   { if (slashPaletteNav(-1)) { e.preventDefault(); return; } }
        // Then history — won't interfere with multi-line navigation.
        if (e.key === 'ArrowUp')   { if (historyNav(-1)) { e.preventDefault(); return; } }
        if (e.key === 'ArrowDown') { if (historyNav(+1)) { e.preventDefault(); return; } }
        if (e.key === 'Tab')       { if (slashPaletteCommit()) { e.preventDefault(); return; } }
        if (e.key === 'Escape')    { document.getElementById('slash-palette').classList.remove('show'); return; }
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            pushHistory($input.value);
            state.historyIdx = -1;
            send();
        }
    });
    $input.addEventListener('input', function () {
        $input.style.height = 'auto';
        $input.style.height = Math.min(160, $input.scrollHeight) + 'px';
        checkSlashPalette();
    });

    // ── Init ───────────────────────────────────────────────

    loadSlashCommands();
    loadStatus();
    wireTaskListPanel();

    loadSessions().then(function () {
        var hashId = _route.current();
        if (hashId) {
            // onShow fires synchronously — opens the session from the URL.
            _route.init();
        } else if (state.sessions.length) {
            openSession(state.sessions[0].id);
        }
    });
})();
