// rooms-sidebar.js — left rail rendering, tier filter, cross-room search.
// Owns: COLORS, _unreadMap, renderSidebar, setTierFilter, filterAgents,
// _renderMessageHits. Reads: agents, currentAgent, tierFilter (inline state).
//
// Adding a function here? Add no extra wiring — globals are visible across files.

var COLORS = ['#8b5cf6','#3b82f6','#10b981','#f59e0b','#ef4444','#ec4899','#06b6d4','#84cc16','#f97316','#6366f1'];

// Phase 9 — unread state per room id, refreshed after loadAgents.
var _unreadMap = {};

function agentColor(name) {
    var h = 0;
    for (var i = 0; i < name.length; i++) h = ((h << 5) - h) + name.charCodeAt(i) | 0;
    return COLORS[Math.abs(h) % COLORS.length];
}

function initial(name) { return (name || '?')[0].toUpperCase(); }
function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + '...' : (s || ''); }

// --- Sidebar rendering ---

function renderSidebar() {
    var el = document.getElementById('agent-list');
    var query = (document.getElementById('search').value || '').toLowerCase();
    // Archived rooms only show when the Archived tab is selected; they're
    // hidden from All / User / System views so the default sidebar stays
    // focused on live conversations.
    var filtered = agents.filter(function(a) {
        if (query && a.name.toLowerCase().indexOf(query) < 0) return false;
        var isArchived = a.status === 'archived';
        if (tierFilter === 'archived') return isArchived;
        if (isArchived) return false;
        if (tierFilter !== 'all' && (a.tier || 'user') !== tierFilter) return false;
        return true;
    });
    document.getElementById('agent-count').textContent = agents.length + ' agent' + (agents.length !== 1 ? 's' : '');

    if (!filtered.length) {
        el.innerHTML = '<div style="text-align:center;padding:30px 10px;color:var(--text-muted);font-size:13px">' +
            (agents.length ? 'No matches' : 'No agents yet') + '</div>';
        return;
    }
    // Sort: most-recently-active first, then anything that has never seen
    // a message falls to the bottom in alphabetical order. System tier
    // gets a small boost so the user's core agents (general-assistant etc.)
    // stay above totally-stale persona records that would otherwise outrank
    // them by name. Unread rooms always float to the very top.
    filtered.sort(function(a, b) {
        var au = !!_unreadMap[a.id], bu = !!_unreadMap[b.id];
        if (au !== bu) return au ? -1 : 1;
        var at = a.last_msg_ts || '';
        var bt = b.last_msg_ts || '';
        if (at && bt && at !== bt) return at < bt ? 1 : -1;
        if (at && !bt) return -1;
        if (bt && !at) return 1;
        // Both lack activity — system tier sinks above everyone, then name.
        var asys = a.tier === 'system' ? 0 : 1;
        var bsys = b.tier === 'system' ? 0 : 1;
        if (asys !== bsys) return asys - bsys;
        return (a.name || '').localeCompare(b.name || '');
    });
    el.innerHTML = filtered.map(function(a) {
        var active = currentAgent && currentAgent.id === a.id;
        var tier = a.tier || 'user';
        var isGroup = tier === 'group';
        var color = agentColor(a.name);
        var badge = '<span class="tier-badge ' + tier + '">' + tier + '</span>';
        var lock = a.builtin ? ' 🔒' : '';
        var preview, avatar;
        if (isGroup) {
            var parts = a.participants || [];
            var responders = parts.filter(function(p){
                return p.type === 'agent' || p.type === 'cli';
            });
            var responderCount = responders.length;
            var hasCli = parts.some(function(p){ return p.type === 'cli'; });
            // Stacked mini-avatars (max 3) + overflow count. Reuses participantVisual.
            var miniMax = 3;
            var minis = responders.slice(0, miniMax).map(function(p, idx) {
                var v = participantVisual(p);
                var z = miniMax - idx;
                return '<span class="member-avatar" style="background:' + v.color + ';width:18px;height:18px;font-size:9px;border:2px solid var(--bg);margin-left:' + (idx === 0 ? '0' : '-7px') + ';z-index:' + z + ';position:relative">' + esc(v.icon) + '</span>';
            }).join('');
            var overflow = responderCount > miniMax
                ? '<span style="font-size:11px;color:var(--text-muted);margin-left:6px">+' + (responderCount - miniMax) + '</span>'
                : '';
            preview = '<span style="display:inline-flex;align-items:center">' + minis + overflow +
                      '<span style="margin-left:8px;color:var(--text-muted);font-size:11px">' +
                          responderCount + (hasCli ? ' incl. CLI' : '') +
                      '</span></span>';
            avatar = '<div class="agent-avatar" style="background:' + color + '">👥</div>';
        } else {
            preview = truncate(a.system_prompt, 40) || 'No prompt set';
            avatar = '<div class="agent-avatar" style="background:' + color + '">' + initial(a.name) + '</div>';
        }
        // Group rooms compose preview as HTML (mini-avatar stack); 1:1 rooms
        // pass plain text. The flag distinguishes which path to take.
        var previewHtml = isGroup ? preview : esc(preview);
        // Unread indicator — small dot with count, positioned right of the name.
        var unread = _unreadMap[a.id];
        var unreadDot = '';
        if (unread && unread.count > 0) {
            var n = unread.count > 9 ? '9+' : String(unread.count);
            unreadDot = '<span class="unread-dot" title="' + unread.count + ' new since last visit">' + n + '</span>';
        }
        return '<div class="agent-item' + (active ? ' active' : '') + (unread ? ' has-unread' : '') + '" onclick="openChat(\'' + escAttr(a.id) + '\')">' +
            avatar +
            '<div class="agent-preview">' +
                '<div class="agent-preview-name">' + esc(a.name) + badge + lock + unreadDot + '</div>' +
                '<div class="agent-preview-msg">' + previewHtml + '</div>' +
            '</div></div>';
    }).join('');
}

function setTierFilter(tier) {
    tierFilter = tier;
    document.querySelectorAll('.tier-filter button').forEach(function(b) {
        b.classList.toggle('active', b.textContent.toLowerCase() === tier);
    });
    renderSidebar();
}

// --- Cross-room message search (Phase 7b) ---
//
// When the sidebar query is ≥2 chars, also query /rooms/api/search and
// append hits below the agent list. Debounced 200ms so typing fast doesn't
// hammer the daemon. Hits link directly into the room.

var _searchTimer = null;
var _lastSearchQuery = '';

function filterAgents() {
    renderSidebar();
    var q = (document.getElementById('search').value || '').trim();
    if (_searchTimer) clearTimeout(_searchTimer);
    if (q.length < 2) {
        _renderMessageHits([]);
        return;
    }
    _searchTimer = setTimeout(async function() {
        if (q === _lastSearchQuery) return;
        _lastSearchQuery = q;
        try {
            var hits = await EOS.api('/rooms/api/search?q=' + encodeURIComponent(q)) || [];
            _renderMessageHits(hits);
        } catch(e) {
            _renderMessageHits([]);
        }
    }, 200);
}

function _renderMessageHits(hits) {
    var container = document.getElementById('msg-hits');
    if (!container) return;
    if (!hits.length) {
        container.innerHTML = '';
        container.style.display = 'none';
        return;
    }
    container.style.display = '';
    container.innerHTML =
        '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;padding:8px 14px 4px">In messages</div>' +
        hits.map(function(h) {
            var when = h.ts ? new Date(h.ts).toLocaleDateString([], {month:'short', day:'numeric'}) : '';
            return '<div class="agent-item" style="padding:8px 14px;cursor:pointer" onclick="openChat(\'' + escAttr(h.room_id) + '\')">' +
                '<div style="flex:1;min-width:0">' +
                    '<div style="font-size:12px;color:var(--text-muted);margin-bottom:2px">' +
                        (h.kind === 'group' ? '👥 ' : '') + esc(h.room_name) +
                        ' · ' + esc(h.speaker) +
                        (when ? ' · ' + esc(when) : '') +
                    '</div>' +
                    '<div style="font-size:13px;color:var(--text);overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">' +
                        esc(h.snippet) +
                    '</div>' +
                '</div></div>';
        }).join('');
}

