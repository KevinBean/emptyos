// eos-cad-views/checks.js — the consequence readout: what the world says back.
//
// Phase 2 of live-consequence (.claude/rules/cad-layouts.md). Phase 1 added the tick
// that recomputes engineering checks on edit and publishes them to store.analysis;
// this is the pane that makes that legible — status, the failing results, and a click
// that selects the offending equipment so every other pane highlights it.
//
// THE RENDER PATH IS A PURE FUNCTION OF THE STORE: it never fetches, never mutates
// the doc, and holds no state of its own. It renders store.analysis (written by the
// tick or by a manual "Run checks") and resolves row → feature id against store.doc.
// Works with the tick off too — a manual run publishes to the same channel.
// The one exception is the "?" popover, which fetches a KB note on user click (same
// shape as corridor-inspector: draw-from-store + one on-demand fetch).
//
// Domain-shaped but not domain-coupled: the result vocabulary (family / passes /
// actual_mm) is engineering-scene's check contract, and rows it cannot interpret
// still render with their raw verdict rather than being dropped.

import { defineView, esc } from '/static/eos-cad-view.js';
import {
  familyLabel, verdictClass, pickId, describeResult, sortResults, citation,
} from '/static/eos-cad-checks-core.js';

const PREFIX = '/engineering-scene/api';

const STYLES = `
  .cadv-ck { height: 100%; overflow-y: auto; padding: 10px 12px; box-sizing: border-box;
    border-top: 1px solid var(--border); background: var(--bg); }
  .cadv-ck .ck-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .cadv-ck .ck-pill { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
    border: 1px solid var(--border); color: var(--text); }
  .cadv-ck .ck-pill.ok { color: var(--green); border-color: var(--green); }
  .cadv-ck .ck-pill.review { color: var(--red); border-color: var(--red); }
  .cadv-ck .ck-pill.incomplete, .cadv-ck .ck-pill.stale { color: var(--amber); border-color: var(--amber); }
  .cadv-ck .ck-sum { font-size: 11.5px; color: var(--muted); }
  .cadv-ck .ck-row { display: flex; align-items: baseline; gap: 8px; padding: 5px 0;
    border-bottom: 1px solid var(--border); font-size: 12.5px; }
  .cadv-ck .ck-row.pick { cursor: pointer; }
  .cadv-ck .ck-row.pick:hover { background: var(--hover, rgba(127,127,127,.08)); }
  .cadv-ck .ck-row.sel { box-shadow: inset 2px 0 0 var(--accent); }
  .cadv-ck .ck-dot { flex: 0 0 auto; width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .cadv-ck .ck-dot.bad { background: var(--red); }
  .cadv-ck .ck-dot.good { background: var(--green); }
  .cadv-ck .ck-dot.unknown { background: var(--amber); }
  .cadv-ck .ck-fam { flex: 0 0 auto; color: var(--muted); font-size: 11px; text-transform: uppercase;
    letter-spacing: .04em; }
  .cadv-ck .ck-txt { flex: 1 1 auto; }
  .cadv-ck .ck-cite { flex: 0 0 auto; font-size: 11px; color: var(--muted); }
  .cadv-ck .ck-why { flex: 0 0 auto; border: 1px solid var(--border); background: none;
    color: var(--muted); border-radius: 4px; font-size: 11px; line-height: 1;
    padding: 2px 5px; cursor: pointer; }
  .cadv-ck .ck-why:hover { color: var(--text); border-color: var(--accent); }
  .cadv-ck .ck-note { margin: 2px 0 8px 16px; padding: 8px 10px; border-left: 2px solid var(--accent);
    background: var(--hover, rgba(127,127,127,.06)); font-size: 12px; line-height: 1.55;
    white-space: pre-wrap; }
  .cadv-ck .ck-note .nt { font-weight: 600; margin-bottom: 4px; }
  .cadv-ck .ck-note.gap { border-left-color: var(--amber); color: var(--muted); }
  .cadv-ck .ck-warn { margin-top: 8px; font-size: 11.5px; color: var(--amber); line-height: 1.5; }
  .cadv-ck .ck-empty { color: var(--muted); font-size: 12px; padding: 6px 0; }
`;

function render(vctx) {
  const host = vctx.pane.querySelector('[data-ck]');
  if (!host) return;
  const a = vctx.store.analysis;

  if (!a || !a.status || a.status === 'not-run') {
    host.innerHTML = '<div class="ck-empty">No checks yet — edit the scene or run checks.</div>';
    return;
  }

  const s = a.summary || {};
  const cls = a.status === 'ok' ? 'ok' : (a.status === 'review' ? 'review' : 'incomplete');
  const parts = [];
  parts.push('<div class="ck-head">'
    + '<span class="ck-pill ' + cls + '">' + esc(a.status) + '</span>'
    + '<span class="ck-sum">' + esc(String(s.failed || 0)) + ' failed · '
    + esc(String(s.incomplete || 0)) + ' incomplete · ' + esc(String(s.total || 0)) + ' checks</span>'
    + '</div>');

  const results = Array.isArray(a.results) ? a.results : [];
  if (!results.length) {
    parts.push('<div class="ck-empty">Nothing to report.</div>');
  } else {
    sortResults(results).forEach((r, i) => {
      const id = pickId(vctx.store.doc, r);
      const sel = id && id === vctx.store.selection ? ' sel' : '';
      const cite = citation(r);
      // escAttr (global, from eos.js) for attribute context — esc() is body-context
      // only and check-attr-escaper.py gates the difference.
      parts.push('<div class="ck-row' + (id ? ' pick' : '') + sel + '"'
        + (id ? ' data-pick="' + escAttr(id) + '"' : '')
        + (id ? ' title="Select ' + escAttr(id) + '"' : '') + '>'
        + '<span class="ck-dot ' + verdictClass(r.passes) + '"></span>'
        + '<span class="ck-fam">' + esc(familyLabel(r.family)) + '</span>'
        + '<span class="ck-txt">' + esc(describeResult(r)) + '</span>'
        + (cite ? '<span class="ck-cite">' + esc(cite.text) + '</span>' : '')
        // Only a kb-backed rule has something to expand — a published standard is
        // already fully cited by its name.
        + (cite && cite.kind === 'kb'
          ? '<button type="button" class="ck-why" data-why="' + escAttr(cite.slug) + '"'
            + ' data-slot="' + escAttr(String(i)) + '" title="Why is this the rule?">?</button>'
          : '')
        + '</div>');
      parts.push('<div data-note="' + escAttr(String(i)) + '"></div>');
    });
  }

  const warns = Array.isArray(a.warnings) ? a.warnings : [];
  if (warns.length) {
    parts.push('<div class="ck-warn">' + warns.map((w) => esc(String(w))).join('<br>') + '</div>');
  }

  host.innerHTML = parts.join('');
  host.querySelectorAll('[data-pick]').forEach((el) => {
    el.addEventListener('click', () => vctx.store.select(el.getAttribute('data-pick')));
  });
  host.querySelectorAll('[data-why]').forEach((el) => {
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();                       // don't also select the row
      toggleWhy(host, el.getAttribute('data-why'), el.getAttribute('data-slot'));
    });
  });
}

// The ONE fetch in this view, and it is user-initiated — the render path stays a
// pure function of the store. Resolves a project rule's KB note, or says plainly
// that nobody has written it yet (which was true of all three shipped slugs, and
// is the honest prompt to go write one rather than a broken-looking button).
async function toggleWhy(host, slug, slot) {
  const box = host.querySelector('[data-note="' + slot + '"]');
  if (!box) return;
  if (box.innerHTML) { box.innerHTML = ''; return; }   // second click closes
  box.innerHTML = '<div class="ck-note">Loading…</div>';
  let r = null;
  try {
    r = await fetch(PREFIX + '/checks/explain/' + encodeURIComponent(slug)).then((x) => x.json());
  } catch (e) { r = null; }
  if (!r || !r.ok) {
    box.innerHTML = '<div class="ck-note gap">Could not load the explanation.</div>';
    return;
  }
  if (r.missing) {
    box.innerHTML = '<div class="ck-note gap"><div class="nt">No basis recorded</div>'
      + 'This distance is an EmptyOS project default, not a published standard, and the '
      + 'KB note <code>' + esc(slug) + '</code> that should justify it has not been written yet.</div>';
    return;
  }
  box.innerHTML = '<div class="ck-note"><div class="nt">' + esc(r.title || slug) + '</div>'
    + esc(r.body || '') + '</div>';
}

const _v = defineView({
  id: 'checks', styles: STYLES,
  markup: `<div class="cadv-ck"><div data-ck></div></div>`,
  // `analysis` is the whole point; `doc` because row→feature resolution reads it;
  // `select` so the highlighted row follows selection made in any other pane.
  events: ['analysis', 'doc', 'select'],
  mount(vctx) { render(vctx); },
  update(vctx) { render(vctx); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
