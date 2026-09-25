// Projects — core view state, card/view rendering, filters, timeline/calendar/stats, detail frame.
// Tab content loaders live in tabs.js; modal/dialog code lives in dialogs.js.

var allProjects = [];
var currentCategory = '';  // tag-based category filter ('' = all)
var CATEGORY_RESERVED = {'project': 1, 'inbox': 1};  // baseline tags, not useful as categories

function projectTags(p) {
    if (!p || !p.tags) return [];
    if (Array.isArray(p.tags)) return p.tags.map(function(t){return String(t).toLowerCase().trim()}).filter(Boolean);
    return String(p.tags).toLowerCase().split(/[,\s]+/).map(function(t){return t.trim()}).filter(Boolean);
}

function collectCategories() {
    var counts = {};
    allProjects.forEach(function(p) {
        if (!showArchived && p.status === 'archived') return;
        projectTags(p).forEach(function(t) {
            if (CATEGORY_RESERVED[t]) return;
            counts[t] = (counts[t] || 0) + 1;
        });
    });
    return counts;
}

// Chips are <button>s wired by delegation (no inline handler), and the tail of
// single-use tags folds behind "+N more": this vault carries 36 tags, 24 of
// them used exactly once, which rendered as five wrapped rows of chrome between
// the search box and the first project.
var _chipsExpanded = false;
var CHIP_MIN_COUNT = 2;

function renderCategoryBar() {
    var bar = document.getElementById('category-bar');
    if (!bar) return;
    var counts = collectCategories();
    var tags = Object.keys(counts).sort(function(a,b){return counts[b]-counts[a] || a.localeCompare(b)});
    var total = (showArchived ? allProjects : allProjects.filter(function(p){return p.status!=='archived'})).length;
    if (!tags.length) { bar.innerHTML = ''; bar.style.display = 'none'; return; }
    bar.style.display = 'flex';

    // The selected tag stays visible even when it lives in the folded tail —
    // otherwise picking one from the expanded list and then collapsing hides
    // the only clue about why the board just emptied out.
    var core = tags.filter(function(t) { return counts[t] >= CHIP_MIN_COUNT || t === currentCategory; });
    var shown = _chipsExpanded ? tags : core;
    var foldable = tags.length - core.length;

    function chip(label, value, count, active) {
        return '<button type="button" class="pj-chip' + (active ? ' active' : '') +
               '" data-tag="' + escAttr(value) + '">' + esc(label) +
               ' <span class="pj-chip-n">' + count + '</span></button>';
    }
    var html = chip('All', '', total, currentCategory === '');
    shown.forEach(function(t) { html += chip(t, t, counts[t], currentCategory === t); });
    if (foldable > 0) {
        html += '<button type="button" class="pj-chip pj-chip-more" data-chip-more="1">' +
                (_chipsExpanded ? 'Fewer' : '+' + foldable + ' more') + '</button>';
    }
    bar.innerHTML = html;
}

// One delegated listener for the whole bar, so it survives every re-render.
document.addEventListener('click', function(e) {
    var bar = document.getElementById('category-bar');
    var btn = (bar && e.target.closest) ? e.target.closest('.pj-chip') : null;
    if (!btn || !bar.contains(btn)) return;
    if (btn.hasAttribute('data-chip-more')) {
        _chipsExpanded = !_chipsExpanded;
        renderCategoryBar();
        sizeKanbanColumns();   // the bar just changed height above the board
        return;
    }
    setCategory(btn.getAttribute('data-tag') || '');
});

function setCategory(tag) {
    currentCategory = tag || '';
    renderCategoryBar();
    filterProjects();
}
var currentView = localStorage.getItem('eos-projects-view') || 'kanban';
var currentSort = 'recent';   // validated against SORTS below, once it exists
var quickFilter = '';          // '' | key of QUICK_FILTERS - driven by the stat strip
var showArchived = false;
var filteredProjects = [];
var typeConfig = {};
var toolsByType = {};
var featureRegistry = {};

// ALL_STATUSES lives in workspace.js (loaded before this file).
var STATUS_LABELS = { idea: 'Ideas', active: 'Active', 'spec-ready': 'Spec Ready', blocked: 'Blocked', shelved: 'Shelved', completed: 'Done', archived: 'Archived' };
function getStatusOrder() { return showArchived ? ALL_STATUSES : ALL_STATUSES.filter(function(s){return s!=='archived'}); }

// Undated projects must sort LAST under "deadline first"; this sentinel is
// above every ISO date string, so it does that without a null branch.
var NO_DEADLINE = '\uffff';

// Sorting. 100 projects, 55 of them untouched for over a month - alphabetical
// is the least useful order this data admits, so "recently active" leads. The
// choice persists per machine.
var SORTS = {
    recent:   function(a, b) { return (a.stale_days || 0) - (b.stale_days || 0) || a.name.localeCompare(b.name); },
    stale:    function(a, b) { return (b.stale_days || 0) - (a.stale_days || 0) || a.name.localeCompare(b.name); },
    progress: function(a, b) { return (b.progress || 0) - (a.progress || 0) || a.name.localeCompare(b.name); },
    name:     function(a, b) { return a.name.localeCompare(b.name); },
    deadline: function(a, b) {
        var A = a.deadline || NO_DEADLINE, B = b.deadline || NO_DEADLINE;
        return A < B ? -1 : A > B ? 1 : a.name.localeCompare(b.name);
    },
};

// Assigning a <select>.value with no matching <option> sets selectedIndex = -1
// and renders an empty control, so the stored value is validated on the way IN,
// not only on the way out.
currentSort = SORTS[localStorage.getItem('eos-projects-sort')] ? localStorage.getItem('eos-projects-sort') : 'recent';

function setSort(v) {
    currentSort = SORTS[v] ? v : 'recent';
    localStorage.setItem('eos-projects-sort', currentSort);
    filterProjects();
}

// Quick filters. Each is also a stat card, and card and filter read the SAME
// predicate - so the number shown and the rows it reveals cannot drift apart.
//
// Every "needs attention" signal is scoped to live projects. The API's own
// `overdue` is purely date-based, so 26 finished projects were counted overdue
// and stale alongside the live ones — a count nothing can act on.
function isLive(p) { return p.status !== 'completed' && p.status !== 'archived'; }
function isStale(p) { return isLive(p) && (p.stale_days || 0) > 30; }

var QUICK_FILTERS = {
    active:  {label: 'Active',     test: function(p) { return p.status === 'active'; }},
    overdue: {label: 'Overdue',    test: function(p) { return isLive(p) && !!p.overdue; }},
    stale:   {label: 'Stale 30d+', test: isStale},
    notasks: {label: 'No tasks',   test: function(p) { return isLive(p) && !p.total_tasks; }},
};

function setQuickFilter(key) {
    quickFilter = (quickFilter === key) ? '' : (QUICK_FILTERS[key] ? key : '');
    renderStats();
    filterProjects();
}

// A stat-card click that only changed the result set would be invisible state
// with no way back - this pill is the off switch.
function renderActiveFilter() {
    var el = document.getElementById('active-filter');
    if (!el) return;
    var f = QUICK_FILTERS[quickFilter];
    el.innerHTML = f
        ? '<button type="button" class="pj-active-filter" onclick="setQuickFilter(' +
          EOS_UI.jsArg(quickFilter) + ')" title="Clear this filter">' + esc(f.label) + '</button>'
        : '';
}

function toggleArchived() {
    showArchived = !showArchived;
    var btn = document.getElementById('btn-archive');
    if (btn) {
        // The button is a toggle, so it names the thing it governs and reports
        // its own state — a label that flips between "Show"/"Hide" leaves the
        // reader deducing the current state from the verb.
        btn.classList.toggle('active', showArchived);
        btn.setAttribute('aria-pressed', showArchived ? 'true' : 'false');
        btn.title = showArchived ? 'Hide archived projects' : 'Include archived projects';
    }
    renderCategoryBar();
    filterProjects();
}

// Open the project-tracker preset as a board. Idempotent: creates the board on
// first click, opens it directly on every subsequent click.
async function openAsBoard() {
    try {
        var j = await EOS.post('/boards/api/boards/from-preset', {preset_id: 'project-tracker'});
        if (j && j.id) {
            window.location.href = '/boards/#' + encodeURIComponent(j.id);
        } else {
            EOS_UI.toast(j && j.error ? j.error : 'Could not open board', false);
        }
    } catch (e) {
        EOS_UI.toast('Could not open board: ' + e, false);
    }
}

function setView(v) {
    currentView = v;
    localStorage.setItem('eos-projects-view', v);
    ['kanban','list','timeline','calendar'].forEach(function(b) {
        var el = document.getElementById('btn-' + b);
        if (!el) return;
        el.classList.toggle('active', v === b);
        el.setAttribute('aria-pressed', v === b ? 'true' : 'false');
    });
    renderView();
}

function deadlineLabel(p) {
    if (!p.deadline) return '';
    // A finished project's date is history: keep it, drop the urgency. Marking a
    // completed project "Overdue 122d" is the same false alarm as calling it
    // stale — it demands attention nothing can act on.
    if (!isLive(p)) {
        return '<span class="project-deadline pj-quiet">' + esc(p.deadline) + '</span>';
    }
    var d = p.days_until_deadline;
    if (d === null) return '';
    if (d < 0) return '<span class="project-deadline overdue">Overdue ' + Math.abs(d) + 'd</span>';
    if (d <= 7) return '<span class="project-deadline soon">Due in ' + d + 'd</span>';
    return '<span class="project-deadline">' + esc(p.deadline) + '</span>';
}

// progressBar() and typeBadge() live in workspace.js (loaded before this file).

function stageLabel(p) {
    if (!p.stage || !p.type || p.type === 'personal') return '';
    var tc = (typeConfig[p.type] || {}).labels || {};
    return '<span style="font-size:10px;color:var(--text-muted)">' + (tc[p.stage] || p.stage) + '</span>';
}

// A summary card exists to answer "does this need me?" BEFORE the click
// (.claude/rules/list-card-density.md). The kanban card carried a bare
// "0/8 tasks" and a 4px bar, so a project with no tasks rendered as a title and
// nothing else, and every card read as equally urgent.
function taskMeta(p) {
    if (p.total_tasks > 0) {
        return '<span>' + p.done_tasks + '/' + p.total_tasks + ' tasks &middot; ' + p.progress + '%</span>';
    }
    return '<span class="pj-quiet">No tasks yet</span>';
}

// Staleness is the loudest signal in this vault, but it is meaningless on a
// finished project: "Stale 141d" on a completed one is noise, not a nudge.
// COLD_DAYS is the app's own existing "long dead" boundary, not a new
// invention: projects/reading.py scores staleness against 90 and 180 days, and
// panels.py treats 90+ as the neglected band. Reusing 90 keeps the card's red
// tier aligned with what the backend already calls cold.
var COLD_DAYS = 90;

function staleLabel(p) {
    if (!isStale(p)) return '';
    var d = p.stale_days || 0;
    return '<span class="pj-stale' + (d > COLD_DAYS ? ' cold' : '') + '">Stale ' + d + 'd</span>';
}

// One meta builder for both views, so kanban and list cannot end up describing
// the same project differently.
function cardMeta(p) {
    var bits = [taskMeta(p)];
    var sl = stageLabel(p); if (sl) bits.push(sl);
    var dl = deadlineLabel(p); if (dl) bits.push(dl);
    var st = staleLabel(p); if (st) bits.push(st);
    return bits.join('');
}

function projectCardHtml(p) {
    var badges = [];
    if (p.type && p.type !== 'personal') badges.push({label: p.type, variant: 'neutral'});
    return EOS_UI.entityCard({
        title: p.name,
        badges: badges,
        meta: cardMeta(p),
        body: progressBar(p) || undefined,
        onClick: "openProject(" + JSON.stringify(p.id) + ")",
    });
}

// Status → eos-pill palette. Mirrors the col-* CSS at the top of index.html so
// the kanban header pill matches the per-status accent users expect.
var STATUS_COLORS = {
    idea: 'gray', active: 'green', 'spec-ready': 'blue', blocked: 'red',
    shelved: 'amber', completed: 'purple', archived: 'gray',
};

function renderKanban(projects) {
    var mountEl = document.getElementById('main-view');
    // EOS_UI.kanbanLayout has no empty state of its own — it maps over `groups`
    // unconditionally. Without this branch the explained empty states below were
    // unreachable in the one view that is default on a fresh browser.
    if (!projects.length) { mountEl.innerHTML = emptyStateHtml(); return; }
    var order = getStatusOrder();
    var groups = order.map(function(s) {
        return {key: s, label: STATUS_LABELS[s], color: STATUS_COLORS[s] || 'gray'};
    });
    mountEl.innerHTML = '<div id="projects-kanban-mount"' + (showArchived ? ' data-show-archived="true"' : '') + '></div>';
    EOS_UI.kanbanLayout({
        mountId: 'projects-kanban-mount',
        items: projects,
        groups: groups,
        getGroup: function(p) { return p.status; },
        getItemId: function(p) { return p.id; },
        wrapCards: false,                       // entityCard already provides the card surface
        renderCard: projectCardHtml,
        onMove: async function(p, newStatus) {
            try {
                await EOS.post('/projects/api/projects/' + encodeURIComponent(p.id) + '/status',
                               {status: newStatus});
                p.status = newStatus;            // optimistic; reload syncs anyway
                EOS_UI.toast('Moved to ' + STATUS_LABELS[newStatus]);
                await load();                    // refresh stats + columns
            } catch (e) {
                EOS_UI.toast('Failed to move project: ' + e, false);
            }
        },
    });
    sizeKanbanColumns();
}

// R2-#2: the per-column scroll height used to be `calc(100dvh - 330px)`, a
// constant standing in for chrome that is not constant — expanding the tag
// chips moves the board down 64px (measured), the toolbar wraps at narrow
// widths, and the 330 accounted for none of body's 80px FAB-dock padding, so
// the scroller's bottom edge landed exactly under the dock. Measure instead.
function sizeKanbanColumns() {
    var mount = document.getElementById('projects-kanban-mount');
    if (!mount) return;
    // Measure the SCROLLER, not the mount. .eos-kanban-items starts ~69px below
    // .eos-kanban (the board's own padding-top plus the column header), so
    // measuring the mount overshoots by exactly that much and the max-height
    // lets the column run past the FAB dock.
    var box = mount.querySelector('.eos-kanban-items') || mount;
    var dock = parseFloat(getComputedStyle(document.body).paddingBottom) || 0;
    var h = window.innerHeight - box.getBoundingClientRect().top - dock - 12;
    mount.style.setProperty('--pj-col-max', Math.max(220, Math.round(h)) + 'px');
}

window.addEventListener('resize', sizeKanbanColumns);

function renderList(projects) {
    var mount = document.getElementById('main-view');
    if (!projects.length) { mount.innerHTML = emptyStateHtml(); return; }
    mount.innerHTML = '<div class="project-list">' + projects.map(function(p) {
        return EOS_UI.entityCard({
            title: p.name,
            badges: [{label: p.status, variant: EOS_UI.statusVariant(p.status, {'spec-ready': 'draft'})}],
            meta: cardMeta(p),
            body: progressBar(p) || undefined,
            onClick: "openProject(" + JSON.stringify(p.id) + ")",
            className: 'list-card',
        });
    }).join('') + '</div>';
}

// "Nothing here" has to say WHY. A search miss, a quick filter, a tag filter
// and a genuinely empty vault are four situations with four different next
// moves, and the old single "No projects" line answered none of them.
function emptyStateHtml() {
    var q = ((document.getElementById('search') || {}).value || '').trim();
    if (q) {
        return EOS_UI.emptyState({icon: '&#128269;', message: 'No project matches "' + q + '".',
                                  actionLabel: 'Clear search', onAction: 'clearSearch()'});
    }
    if (quickFilter) {
        var f = QUICK_FILTERS[quickFilter] || {};
        return EOS_UI.emptyState({icon: '&#10003;', message: 'Nothing is ' + (f.label || '') + ' right now.',
                                  actionLabel: 'Clear filter',
                                  onAction: 'setQuickFilter(' + EOS_UI.jsArg(quickFilter) + ')'});
    }
    if (currentCategory) {
        return EOS_UI.emptyState({icon: '&#127991;', message: 'No projects tagged "' + currentCategory + '".',
                                  actionLabel: 'Show all tags', onAction: "setCategory('')"});
    }
    return EOS_UI.emptyState({icon: '&#128193;', message: 'No projects yet.',
                              actionLabel: '+ New project', onAction: 'openCreate()'});
}

function clearSearch() {
    var el = document.getElementById('search');
    if (el) el.value = '';
    filterProjects();
}

function renderView() {
    if (currentView === 'kanban') renderKanban(filteredProjects);
    else if (currentView === 'list') renderList(filteredProjects);
    else if (currentView === 'timeline') loadTimeline();
    else if (currentView === 'calendar') loadCalendar();
}

// --- Timeline View ---
var _timelineData = null;

async function loadTimeline() {
    // The payload is the whole portfolio and does not depend on the filters —
    // only the rows we draw from it do. Re-fetching per keystroke blanked the
    // chart to a loading state on every character typed into the search box.
    if (_timelineData) { renderTimeline(_timelineData); return; }
    var mv = document.getElementById('main-view');
    mv.innerHTML = '<div class="eos-empty">Loading timeline...</div>';
    try {
        _timelineData = await EOS.api('/projects/api/timeline');
        renderTimeline(_timelineData);
    } catch(e) {
        mv.innerHTML = EOS_UI.errorState({message: 'Failed to load timeline', onRetry: 'loadTimeline()'});
    }
}

function renderTimeline(data) {
    // /api/timeline returns the whole portfolio. Left as-is, filtering to
    // "Overdue" and then switching to Timeline silently restored all 77 rows
    // while the heading still read "7 of 100" — the views disagreed about what
    // the user had asked for.
    var visible = {};
    filteredProjects.forEach(function(p) { visible[p.id] = true; });
    var projects = data.projects.filter(function(p) { return visible[p.id]; });
    if (!projects.length) { document.getElementById('main-view').innerHTML = emptyStateHtml(); return; }
    var rangeMin = new Date(data.range.min + 'T00:00:00');
    var rangeMax = new Date(data.range.max + 'T00:00:00');
    var today = new Date(data.today + 'T00:00:00');
    var totalDays = Math.max(1, (rangeMax - rangeMin) / 86400000);

    function dateToX(dateStr) {
        var d = new Date(dateStr + 'T00:00:00');
        return Math.max(0, Math.min(100, ((d - rangeMin) / 86400000 / totalDays) * 100));
    }
    var todayX = dateToX(data.today);

    var months = [];
    var cursor = new Date(rangeMin);
    cursor.setDate(1);
    while (cursor <= rangeMax) {
        months.push(cursor.toLocaleDateString('en', {month: 'short', year: '2-digit'}));
        cursor.setMonth(cursor.getMonth() + 1);
    }

    var statusColors = {idea:'var(--text-muted)',active:'var(--success)',blocked:'var(--danger)',shelved:'var(--warning)',completed:'var(--accent)'};
    var typeColors = {personal:'var(--accent)',engineering:'var(--warning)',development:'#3b82f6'};

    var html = '<div class="timeline-container">';
    html += '<div class="timeline-axis">' + months.map(function(m) { return '<div class="timeline-month">' + m + '</div>'; }).join('') + '</div>';
    html += '<div style="position:relative">';
    // `X% * (100% - 160px) / 100` multiplies two lengths, which calc() rejects,
    // so the whole declaration was dropped and the marker fell back to left:auto
    // -> the container's left edge. A red line pinned to x=0 does not read as
    // broken, it reads as a border, so a Gantt chart has been claiming for
    // months that today precedes every project in it. Multiplying a length by a
    // unitless number is the valid form.
    html += '<div class="timeline-today" style="left:calc(var(--tl-label) + ' +
            '(100% - var(--tl-label)) * ' + todayX.toFixed(2) + ' / 100)" ' +
            'title="Today (' + escAttr(data.today) + ')"></div>';

    projects.sort(function(a, b) { return a.start < b.start ? -1 : 1; });
    projects.forEach(function(p) {
        var left = dateToX(p.start);
        var right = dateToX(p.end);
        var width = Math.max(1, right - left);
        var color = statusColors[p.status] || 'var(--accent)';
        var borderColor = typeColors[p.type] || 'var(--accent)';

        html += '<div class="timeline-row">' +
            '<div class="timeline-label" onclick="openProject(' + EOS_UI.jsArg(p.id) + ')" title="' + escAttr(p.name) + '">' +
                typeBadge(p) + esc(p.name) +
            '</div>' +
            '<div class="timeline-bar-area">' +
                '<div class="timeline-bar" style="left:' + left + '%;width:' + width + '%;background:' + color + ';border:1px solid ' + borderColor + '" ' +
                    'onclick="openProject(' + EOS_UI.jsArg(p.id) + ')" title="' + escAttr(p.name) + ' (' + p.progress + '%)">' +
                    '<div class="timeline-fill" style="width:' + p.progress + '%;background:' + color + '"></div>' +
                '</div>' +
            '</div>' +
        '</div>';
    });
    html += '</div></div>';
    document.getElementById('main-view').innerHTML = html;
}

// --- Calendar View ---
var _calendarMonth = new Date().toISOString().slice(0,7);

async function loadCalendar() {
    var mv = document.getElementById('main-view');
    mv.innerHTML = '<div class="eos-empty">Loading calendar...</div>';
    try {
        var data = await EOS.api('/projects/api/calendar?month=' + _calendarMonth);
        renderCalendar(data);
    } catch(e) {
        mv.innerHTML = EOS_UI.errorState({message: 'Failed to load calendar', onRetry: 'loadCalendar()'});
    }
}

function renderCalendar(data) {
    // Same disagreement as the timeline: the month grid is built from every
    // project's tasks, so a filtered board became an unfiltered calendar.
    var visible = {};
    filteredProjects.forEach(function(p) { visible[p.id] = true; });
    var calendar = {};
    Object.keys(data.calendar || {}).forEach(function(day) {
        var kept = data.calendar[day].filter(function(t) { return visible[t.project_id]; });
        if (kept.length) calendar[day] = kept;
    });
    // Replace only the calendar map; a wholesale rebuild would silently drop
    // any other field api_calendar returns now or later.
    data = Object.assign({}, data, {calendar: calendar});

    var parts = _calendarMonth.split('-');
    var year = parseInt(parts[0]), month = parseInt(parts[1]);
    var firstDay = new Date(year, month - 1, 1);
    var lastDay = new Date(year, month, 0);
    var startDow = (firstDay.getDay() + 6) % 7;
    var daysInMonth = lastDay.getDate();
    var todayStr = new Date().toISOString().slice(0,10);
    var monthLabel = firstDay.toLocaleDateString('en', {month: 'long', year: 'numeric'});

    var typeColors = {personal:'var(--accent)',engineering:'var(--warning)',development:'#3b82f6'};

    var html = '<div class="cal-header">' +
        '<button class="cal-nav" onclick="calNav(-1)" aria-label="Previous month">&lt;</button>' +
        '<div style="font-size:16px;font-weight:600">' + monthLabel + '</div>' +
        '<button class="cal-nav" onclick="calNav(1)" aria-label="Next month">&gt;</button>' +
    '</div>';

    html += '<div class="cal-grid">';
    ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(function(d) {
        html += '<div class="cal-day-header">' + d + '</div>';
    });

    for (var i = 0; i < startDow; i++) html += '<div class="cal-cell empty"></div>';

    for (var d = 1; d <= daysInMonth; d++) {
        var dateStr = _calendarMonth + '-' + String(d).padStart(2, '0');
        var isToday = dateStr === todayStr;
        var tasks = data.calendar[dateStr] || [];

        html += '<div class="cal-cell' + (isToday ? ' today' : '') + '">';
        html += '<div class="cal-date">' + d + '</div>';
        tasks.slice(0, 3).forEach(function(t) {
            var color = typeColors[t.type] || 'var(--accent)';
            var cls = 'cal-task' + (t.is_deadline ? ' is-deadline' : '');
            // A cell is ~130px wide, so every entry is cut at 25 chars and
            // the half that matters is often the half that got cut. The
            // tooltip carries the whole task and the project it belongs to.
            var full = t.task + ' \u2014 ' + (t.project || t.project_id || '');
            html += '<div class="' + cls + '" title="' + escAttr(full) + '"' +
                ' onclick="openProject(' + EOS_UI.jsArg(t.project_id) + ')">' +
                '<span class="cal-dot" style="background:' + color + '"></span>' +
                esc(t.task.substring(0, 25)) +
            '</div>';
        });
        if (tasks.length > 3) html += '<div style="font-size:9px;color:var(--text-muted)">+' + (tasks.length - 3) + ' more</div>';
        html += '</div>';
    }

    var totalCells = startDow + daysInMonth;
    var remainder = totalCells % 7;
    if (remainder > 0) for (var i = 0; i < 7 - remainder; i++) html += '<div class="cal-cell empty"></div>';

    html += '</div>';
    document.getElementById('main-view').innerHTML = html;
}

function calNav(delta) {
    var parts = _calendarMonth.split('-');
    var d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1 + delta, 1);
    _calendarMonth = d.toISOString().slice(0, 7);
    loadCalendar();
}

var _portfolioStats = null;
var _healthScore = null;
var _statsFetched = false;   // set only on SUCCESS, so a failure still retries

// The five .eos-hero cards wrapped into three rows (~310px) because .eos-hero
// is a hard two-column grid, so the first project sat below the fold on a
// 1200px screen. statCards is the dense sibling; the four that name a subset of
// the portfolio are also the filters for it, so a number is a door.
function statItems() {
    // Same base as the board — see visibleBase().
    var projects = visibleBase();
    var counts = {total: projects.length};
    ['active', 'overdue', 'stale', 'notasks'].forEach(function(k) {
        counts[k] = projects.filter(QUICK_FILTERS[k].test).length;
    });
    var totalTasks = projects.reduce(function(s, p) { return s + p.total_tasks; }, 0);
    var doneTasks = projects.reduce(function(s, p) { return s + p.done_tasks; }, 0);
    var pct = totalTasks > 0 ? Math.round(doneTasks / totalTasks * 100) : 0;

    function filterCard(key, label, variant) {
        return {
            key: key, label: label, value: counts[key],
            // A zero count is not a state worth colouring; muted keeps the
            // strip from crying "danger" when nothing is overdue.
            variant: counts[key] ? variant : 'muted',
            onClick: function() { setQuickFilter(key); },
        };
    }
    return [
        {key: '', label: 'Projects', value: counts.total, variant: 'accent',
         onClick: function() { setQuickFilter(''); }},
        filterCard('active',  'Active',     'success'),
        filterCard('overdue', 'Overdue',    'danger'),
        filterCard('stale',   'Stale 30d+', 'warning'),
        filterCard('notasks', 'No tasks',   'muted'),
        {key: null, label: 'Tasks done', value: pct + '%', variant: 'muted'},
        {key: null, label: 'Health', value: _healthScore == null ? '\u2014' : _healthScore,
         variant: _healthScore == null ? 'muted'
                  : _healthScore > 70 ? 'success' : _healthScore > 40 ? 'warning' : 'danger'},
        {key: null, label: 'Done / 7d',
         value: _portfolioStats && typeof _portfolioStats.velocity_7d === 'number'
                ? _portfolioStats.velocity_7d : '\u2014',
         variant: 'muted'},
    ];
}

function paintStats() {
    var items = statItems();
    EOS_UI.statCards('stats-row', items);
    // statCards has no per-card class hook, so mark the armed filter after
    // render — without it, a clicked card gives no sign it is the one filtering.
    var host = document.getElementById('stats-row');
    if (!host) return;
    items.forEach(function(it, i) {
        if (!quickFilter || it.key !== quickFilter) return;
        var card = host.querySelector('[data-stat-index="' + i + '"]');
        if (card) card.classList.add('is-on');
    });
}

async function renderStats() {
    paintStats();
    renderActiveFilter();
    if (_statsFetched) return;
    try {
        var res = await Promise.all([
            EOS.api('/projects/api/portfolio-health'),
            EOS.api('/projects/api/stats'),
        ]);
        _healthScore = (res[0] && typeof res[0].score === 'number') ? res[0].score : null;
        _portfolioStats = res[1] || {};
        _statsFetched = true;      // only a SUCCESS closes the door
    } catch (e) {
        // Deliberately do NOT set _statsFetched: these two endpoints are
        // LLM/index-backed and fail transiently, and latching the flag here made
        // a single 500 permanent for the session — the `r` refresh key and the
        // post-drag reload both returned early and repainted the same em dash.
        _portfolioStats = _portfolioStats || {};
    }
    paintStats();
}

// The set every count in this page is measured against. Archived projects are
// out of scope unless the toggle says otherwise, and the stat strip, the
// subtitle and the board all have to agree about that or the numbers contradict
// each other on screen (the Projects card read 100 beside a board of 77).
function visibleBase() {
    return showArchived ? allProjects : allProjects.filter(function(p) { return p.status !== 'archived'; });
}

function filterProjects(q) {
    // An explicit '' from a caller (EOS.registerActions dispatches filter(""))
    // means "clear the search", so clear the box too — otherwise the input still
    // reads `acme` beside an unfiltered board.
    var box = document.getElementById('search');
    if (q === undefined || q === null) {
        q = (box || {}).value || '';
    } else if (box && box.value !== q) {
        box.value = q;
    }
    q = String(q).toLowerCase();
    var base = visibleBase();
    if (currentCategory) {
        base = base.filter(function(p) { return projectTags(p).indexOf(currentCategory) !== -1; });
    }
    var qf = QUICK_FILTERS[quickFilter];
    if (qf) base = base.filter(qf.test);
    if (q) {
        base = base.filter(function(p) {
            return p.name.toLowerCase().includes(q) ||
                projectTags(p).join(' ').includes(q) ||
                p.status.toLowerCase().includes(q);
        });
    }
    // Copy before sorting: allProjects is the shared source every other view
    // reads, and Array.prototype.sort mutates in place.
    filteredProjects = base.slice().sort(SORTS[currentSort] || SORTS.recent);
    renderActiveFilter();
    updateSubtitle();
    renderView();
}

// The heading is the one place that can honestly say "you are looking at 8 of
// 100" — without it a filtered board looks like a vault that lost its projects.
function updateSubtitle() {
    var el = document.getElementById('subtitle');
    if (!el) return;
    var shown = filteredProjects.length, total = visibleBase().length;
    el.textContent = shown === total
        ? total + ' projects'
        : shown + ' of ' + total + ' projects';
}

// Opening a project navigates to its full-page workspace (the one project
// surface — the old in-list #hash detail toggle was retired). Card / kanban /
// timeline / calendar clicks all route here. The project renderer + shared
// helpers (typeBadge / progressBar / ALL_STATUSES) live in pages/workspace.js.
function openProject(id) {
    location.href = '/projects/workspace/' + encodeURIComponent(id);
}
// Back-compat: anything still deep-linking to /projects/#<id> (old bookmarks,
// other apps) is redirected to the workspace on load — see init below.

async function load() {
    try {
        var [projects, tc, _] = await Promise.all([
            EOS.api('/projects/api/projects'),
            EOS.api('/projects/api/type-config'),
            loadTemplates(),
        ]);
        _timelineData = null;      // portfolio changed; the cached chart is stale
        allProjects = projects;
        typeConfig = tc.types || {};
        toolsByType = tc.tools || {};
        featureRegistry = tc.features || {};
        ALL_STATUSES = tc.statuses || ALL_STATUSES;
        filteredProjects = allProjects;
        renderStats();
        renderCategoryBar();
        filterProjects();
    } catch(e) {
        document.getElementById('subtitle').textContent = 'Failed to load';
    }
}

// Init
['kanban','list','timeline','calendar'].forEach(function(v) {
    var el = document.getElementById('btn-' + v);
    if (!el) return;
    el.classList.toggle('active', currentView === v);
    el.setAttribute('aria-pressed', currentView === v ? 'true' : 'false');
});
(function () {
    // The sort is restored from localStorage, so the control has to be told —
    // otherwise the list is sorted one way and the picker claims another.
    var sel = document.getElementById('sort');
    if (sel) sel.value = currentSort;
})();
if (EOS.keys) { EOS.keys.register('n', 'New project', function() { openCreate(); }); EOS.keys.register('r', 'Refresh', function() { load(); }); }

// Hash redirect for legacy /projects/#<id> deep-links → the workspace page.
(function () {
    var h = (location.hash || '').slice(1);
    if (h) { location.replace('/projects/workspace/' + h); return; }
    load();
})();
