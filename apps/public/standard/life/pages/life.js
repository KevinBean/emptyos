// Life surface — render from state (export-convention §1: render() is a pure
// function of STATE). Data comes from /life/api/timeline, which merges every
// [[contributes.life.timeline]] member. This page owns no data.

var STATE = { days: 1, enabled: null, items: [], sources: [] };

function setRange(days) {
    STATE.days = days;
    document.querySelectorAll('#range-tabs .tab').forEach(function (t) {
        t.classList.toggle('active', Number(t.dataset.days) === days);
    });
    loadTimeline();
}

async function loadTimeline() {
    var data = await EOS.apiSafe('/life/api/timeline?days=' + STATE.days);
    if (data && !data.error) {
        STATE.enabled = !!data.enabled;
        STATE.items = data.items || [];
        STATE.sources = data.sources || [];
    } else {
        STATE.enabled = null;
        STATE.items = [];
        STATE.sources = [];
    }
    render();
}

function kindClass(kind) {
    var known = ['journal', 'journal-auto', 'worklog', 'expense'];
    return 'kind kind-' + (known.indexOf(kind) >= 0 ? kind : 'other');
}

function itemExtra(it) {
    var bits = [];
    if (it.emoji) bits.push(it.emoji);
    if (it.status) bits.push(it.status);
    if (it.category) bits.push(it.category);
    return bits.join(' · ');
}

function render() {
    var tl = document.getElementById('timeline');
    var src = document.getElementById('sources');

    if (STATE.enabled === false) {
        src.textContent = '';
        tl.innerHTML = '<div class="note">The Life surface is dark by default. Enable it with '
            + '<code>[apps.life]</code> <code>feature.enabled = true</code> in emptyos.toml '
            + '(then restart), and it will compose one timeline from journal, worklog and expense.</div>';
        return;
    }
    if (STATE.enabled === null) {
        src.textContent = '';
        tl.innerHTML = EOS_UI.errorState ? EOS_UI.errorState({ title: 'Timeline unavailable' }) :
            '<div class="note">Timeline unavailable.</div>';
        return;
    }

    src.textContent = STATE.sources.length
        ? 'sources: ' + STATE.sources.map(function (s) { return s.app + ' (' + s.count + ')'; }).join(' · ')
        : '';

    if (!STATE.items.length) {
        tl.innerHTML = '<div class="note">Nothing on the timeline yet for this range. '
            + 'Entries from journal, worklog and expense will appear here as one day story.</div>';
        return;
    }

    // Group by date, newest date first (items arrive ts-desc from the API).
    var groups = [];
    var byDate = {};
    STATE.items.forEach(function (it) {
        var d = String(it.ts || '').slice(0, 10);
        if (!byDate[d]) { byDate[d] = []; groups.push({ date: d, items: byDate[d] }); }
        byDate[d].push(it);
    });

    tl.innerHTML = groups.map(function (g) {
        var rows = g.items.map(function (it) {
            var t = String(it.ts || '').slice(11, 16);
            var time = (t && t !== '00:00') ? t : '';
            return '<div class="item" onclick="openItem(this)" data-href="' + escAttr(it.href || '') + '">'
                + '<span class="time">' + esc(time) + '</span>'
                + '<span class="' + kindClass(it.kind) + '">' + esc(it.kind || '') + '</span>'
                + '<span class="title">' + esc(it.title || '') + '</span>'
                + '<span class="extra">' + esc(itemExtra(it)) + '</span>'
                + '</div>';
        }).join('');
        return '<div class="day"><div class="day-head">' + esc(g.date) + '</div>' + rows + '</div>';
    }).join('');
}

function openItem(el) {
    var href = el.getAttribute('data-href');
    if (href) window.location.href = href;
}

loadTimeline();
