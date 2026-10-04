// Notebook page — vault tree, note view, links panel.
// Globals are prefixed `nb` / `NB` (page + sibling share one namespace).

var NB = {
    path: '',          // open note, vault-relative, as the server names it
    want: '',          // the navigation in flight (a newer one supersedes it)
    links: {},         // normalized wikilink target -> [paths] for the open note
    blTimer: null,     // backlinks poll while the link index builds
    blSeq: 0,          // bumps per note open, so a stale poll chain stops
    loading: {},       // folder path -> promise of its children rendering
};

// Backlinks poll: 40 x (5 s + the server's 6 s wait) ≈ 7 min, above the
// 100-345 s index rebuild measured in link/app.py.
var NB_POLL_MS = 5000;
var NB_POLL_MAX = 40;

function nbEsc(s) { return EOS_UI.esc(s == null ? '' : String(s)); }
function nbAttr(s) { return EOS_UI.escAttr(s); }
function nbKey(t) {
    // Mirror of emptyos.sdk.utils.normalize_link_target (anchor/.md/case/slashes).
    t = String(t || '').trim().replace(/\\/g, '/');
    t = t.split(/[#^]/)[0].trim().replace(/^\/+|\/+$/g, '');
    if (/\.md$/i.test(t)) t = t.slice(0, -3);
    return t.toLowerCase();
}
function nbHref(path) { return '#' + encodeURIComponent(path); }
function nbDecode(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }

// ── Tree ────────────────────────────────────────────────────────────────
function nbLoadDir(dir, ul) {
    if (!NB.loading[dir]) {
        NB.loading[dir] = EOS.apiSafe('/notebook/api/tree?dir=' + encodeURIComponent(dir)).then(function(d) {
            if (d.error) {
                delete NB.loading[dir];   // let a later expand retry
                ul.innerHTML = '<li class="nb-err" role="alert">' + nbEsc(d.error) + '</li>';
                return false;
            }
            var html = '';
            d.folders.forEach(function(f) {
                html += '<li><button type="button" class="nb-item nb-folder" aria-expanded="false" data-dir="' + nbAttr(f.path) + '" title="' + nbAttr(f.path + ' — ' + f.count + ' notes') + '">' +
                    '<span aria-hidden="true">▸</span><span class="nb-name">' + nbEsc(f.name) + '</span>' +
                    '<span class="nb-count" aria-label="' + f.count + ' notes">' + f.count + '</span></button><ul hidden></ul></li>';
            });
            d.files.forEach(function(f) {
                html += '<li><a class="nb-item nb-file" href="' + nbHref(f.path) + '" data-path="' + nbAttr(f.path) + '" title="' + nbAttr(f.path) + '">' +
                    '<span class="nb-name">' + nbEsc(f.name) + '</span></a></li>';
            });
            ul.innerHTML = html || '<li class="nb-muted">No notes here.</li>';
            nbMarkActive();
            return true;
        });
    }
    return NB.loading[dir];
}

async function nbToggleFolder(btn, open) {
    var ul = btn.nextElementSibling;
    var expand = open === undefined ? btn.getAttribute('aria-expanded') !== 'true' : open;
    btn.setAttribute('aria-expanded', expand ? 'true' : 'false');
    btn.firstElementChild.textContent = expand ? '▾' : '▸';
    ul.hidden = !expand;
    if (expand && !NB.loading[btn.dataset.dir]) {
        ul.innerHTML = '<li class="nb-muted">Loading…</li>';
    }
    if (expand) await nbLoadDir(btn.dataset.dir, ul);
}

async function nbReveal(path) {
    var parts = path.split('/').slice(0, -1);
    var acc = '';
    for (var i = 0; i < parts.length; i++) {
        if (NB.path !== path) return;   // superseded by a newer navigation
        acc = acc ? acc + '/' + parts[i] : parts[i];
        var btn = document.querySelector('.nb-folder[data-dir="' + CSS.escape(acc) + '"]');
        if (!btn) return;
        await nbToggleFolder(btn, true);
    }
    nbMarkActive();
}

function nbMarkActive() {
    document.querySelectorAll('.nb-file').forEach(function(a) {
        var on = a.dataset.path === NB.path;
        a.classList.toggle('active', on);
        if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
}

// ── Note ────────────────────────────────────────────────────────────────
function nbWikiLink(target, label) {
    return '<a href="#" class="obs-link nb-wl" data-target="' + nbAttr(target) + '">' + nbEsc(label) + '</a>';
}

// Plain `#path` hrefs (tree, links panel, picker) need no handler of their
// own: a fragment navigation fires `popstate`, which hashRoute listens to.
async function nbShow(path) {
    if (EOS_UI.closeModal) EOS_UI.closeModal();
    NB.want = path;
    var head = document.getElementById('nb-head');
    var note = document.getElementById('nb-note');
    head.removeAttribute('data-entity-path');
    document.getElementById('nb-crumb').textContent = path;
    note.innerHTML = '<div class="nb-muted">Loading…</div>';
    var d = await EOS.apiSafe('/api/vault/read?path=' + encodeURIComponent(path));
    if (NB.want !== path) return;   // a newer navigation won
    if (d.error) {
        NB.path = '';
        note.innerHTML = EOS_UI.errorState({ message: d.error });
        nbRenderLinks(null);
        nbMarkActive();
        return;
    }
    // The server's name for the note, so the links panel and the tree agree
    // with it even when the hash spelled the path differently.
    var rel = d.relative || path;
    NB.path = rel;
    if (rel !== path) history.replaceState(history.state, '', nbHref(rel));
    document.getElementById('nb-crumb').textContent = rel;
    var opts = { wikiLink: nbWikiLink, notePath: rel };
    note.innerHTML = (d.viz_embeds && d.viz_embeds.length)
        ? EOS_UI.renderMarkdownWithEmbeds(d.content, d.viz_embeds, opts)
        : EOS_UI.renderMarkdown(d.content, opts);
    head.setAttribute('data-entity-path', rel);
    document.title = EOS.fileName(rel) + ' — Notebook';
    nbArrive();
    nbMarkActive();
    nbReveal(rel);
    nbLoadLinks(rel);
}

function nbHide() {
    NB.want = NB.path = '';
    document.getElementById('nb-crumb').textContent = '';
    document.getElementById('nb-head').removeAttribute('data-entity-path');
    document.getElementById('nb-note').innerHTML = EOS_UI.emptyState({ message: 'Pick a note from the tree.' });
    document.title = 'Notebook — EmptyOS';
    nbRenderLinks(null);
    nbMarkActive();
}

// Every navigation lands on the new note — tree taps and panel links too, not
// only in-note links: top of the note, keyboard focus on it, and on a phone
// (tree stacked above) scrolled into view. Skipped on the first render so a
// deep link does not steal focus from the page.
function nbArrive() {
    var main = document.getElementById('nb-main');
    main.scrollTop = 0;
    if (NB.skipFocus) { NB.skipFocus = false; return; }
    document.getElementById('nb-note').focus({ preventScroll: true });
    if (window.matchMedia('(max-width: 900px)').matches) main.scrollIntoView({ block: 'start' });
}

function nbGo(path) { nbRoute.set(path); }

async function nbFollow(target) {
    var paths = NB.links[nbKey(target)];
    if (!paths) {
        var r = await EOS.apiSafe('/notebook/api/resolve?title=' + encodeURIComponent(target));
        if (r.error && !Array.isArray(r.candidates)) {
            EOS_UI.toast('Could not look up “' + target + '”: ' + r.error, false);
            return;
        }
        paths = r.path ? [r.path] : r.candidates;
    }
    if (paths.length === 1) { nbGo(paths[0]); return; }
    if (!paths.length) { EOS_UI.toast('No note named “' + target + '” yet', false); return; }
    EOS_UI.modal({
        title: '“' + target + '” names ' + paths.length + ' notes',
        body: '<ul class="nb-pick">' + paths.map(function(p) {
            return '<li><a class="nb-item" href="' + nbHref(p) + '">' + nbEsc(p) + '</a></li>';
        }).join('') + '</ul>',
    });
}

// A link inside the rendered note: keep every note-to-note move in this page.
function nbNoteClick(e) {
    var a = e.target.closest('a');
    if (!a || !e.currentTarget.contains(a)) return;
    if (a.classList.contains('nb-wl')) {
        e.preventDefault();
        nbFollow(a.dataset.target);
        return;
    }
    if (a.classList.contains('note-ref')) {
        // The renderer's bare `x.md` / vault-path mentions carry an inline
        // onclick that opens the overlay viewer; stopping here (capture phase)
        // keeps it from running and opens the note in place instead.
        e.preventDefault();
        e.stopPropagation();
        // The path is only intact in the handler itself: the link text has its
        // hyphens turned into spaces, and bare-name links carry no title.
        var m = /EOS\.viewNote\('((?:\\.|[^'\\])*)'\)/.exec(a.getAttribute('onclick') || '');
        var t = m ? m[1].replace(/\\'/g, "'") : (a.getAttribute('title') || a.textContent);
        var vp = (EOS.vaultPath || '').replace(/\/+$/, '');
        if (vp && t.indexOf(vp + '/') === 0) t = t.slice(vp.length + 1);
        nbFollow(t);
        return;
    }
    var href = a.getAttribute('href') || '';
    if (href.charAt(0) === '#') {
        // An in-note anchor, not a note path: scroll to it rather than routing.
        e.preventDefault();
        var el = document.getElementById(nbDecode(href.slice(1)));
        if (el) el.scrollIntoView({ block: 'start' });
        return;
    }
    if (/^[^:?#]+\.md(#.*)?$/i.test(href) && href.charAt(0) !== '/') {
        e.preventDefault();
        var dir = NB.path.replace(/[^/]+$/, '');
        var parts = (dir + href.split('#')[0]).split('/');
        var out = [];
        parts.forEach(function(p) {
            if (p === '..') out.pop(); else if (p && p !== '.') out.push(nbDecode(p));
        });
        nbGo(out.join('/'));
    }
}

// ── Links panel ─────────────────────────────────────────────────────────
function nbLinkItem(p) {
    return '<li><a class="nb-item" href="' + nbHref(p) + '"><span class="nb-name" title="' + nbAttr(p) + '">' +
        nbEsc(EOS.fileName(p)) + '</span></a></li>';
}

function nbRenderLinks(outgoing, error) {
    clearTimeout(NB.blTimer);
    NB.blSeq++;
    var bl = document.getElementById('nb-backlinks');
    var og = document.getElementById('nb-outgoing');
    NB.links = {};
    if (!outgoing && !error) {
        bl.innerHTML = '<li class="nb-muted">Open a note to see its links.</li>';
        og.innerHTML = '';
        return;
    }
    if (error) {
        og.innerHTML = '<li class="nb-err" role="alert">' + nbEsc(error) + '</li>';
        return;
    }
    og.innerHTML = outgoing.length ? outgoing.map(function(l) {
        NB.links[nbKey(l.target)] = l.paths;
        if (l.paths.length === 1) return nbLinkItem(l.paths[0]);
        var why = l.paths.length ? l.paths.length + ' notes' : 'no such note';
        return '<li class="nb-muted"><span class="' + (l.paths.length ? '' : 'nb-broken') + '">' +
            nbEsc(l.target) + '</span> — ' + why + '</li>';
    }).join('') : '<li class="nb-muted">No links.</li>';
}

async function nbLoadLinks(path) {
    var og = await EOS.apiSafe('/notebook/api/outgoing?path=' + encodeURIComponent(path));
    if (NB.path !== path) return;
    nbRenderLinks(og.error ? null : og.links, og.error);
    nbLoadBacklinks(path, 0, NB.blSeq);
}

async function nbLoadBacklinks(path, attempt, seq) {
    var el = document.getElementById('nb-backlinks');
    if (attempt === 0) el.innerHTML = '<li class="nb-muted">Loading…</li>';
    var d = await EOS.apiSafe('/notebook/api/backlinks?path=' + encodeURIComponent(path));
    if (NB.path !== path || NB.blSeq !== seq) return;
    if (d.pending) {
        if (attempt + 1 >= NB_POLL_MAX) {
            el.innerHTML = '<li class="nb-muted">The link index is still building — reopen this note in a few minutes.</li>';
            return;
        }
        el.innerHTML = '<li class="nb-muted">Building the link index…</li>';
        NB.blTimer = setTimeout(function() { nbLoadBacklinks(path, attempt + 1, seq); }, NB_POLL_MS);
        return;
    }
    if (d.error) {
        el.innerHTML = '<li class="nb-err" role="alert">' + nbEsc(d.error) + '</li>';
        return;
    }
    if (d.unavailable) {
        // `link` is optional: its absence is a supported setup, not a failure.
        el.innerHTML = '<li class="nb-muted">Backlinks unavailable (' + nbEsc(d.unavailable) + ').</li>';
        return;
    }
    el.innerHTML = d.backlinks.length ? d.backlinks.map(nbLinkItem).join('') : '<li class="nb-muted">Nothing links here.</li>';
}

// ── Wiring ──────────────────────────────────────────────────────────────
var nbRoute = EOS_UI.hashRoute({ onShow: nbShow, onHide: nbHide });

document.getElementById('nb-tree').addEventListener('click', function(e) {
    var btn = e.target.closest('.nb-folder');
    if (btn) nbToggleFolder(btn);
});
document.getElementById('nb-note').addEventListener('click', nbNoteClick, true);

async function nbBoot() {
    var root = document.getElementById('nb-tree');
    root.innerHTML = '<ul></ul>';
    delete NB.loading[''];
    var ok = await nbLoadDir('', root.firstElementChild);
    if (!ok) {
        root.innerHTML = EOS_UI.errorState({ message: 'Could not load the vault tree.', onRetry: 'nbBoot()' });
    }
    NB.skipFocus = !!location.hash;   // a deep link's first render
    nbRoute.init();
}
nbBoot();
