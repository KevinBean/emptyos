// eos-cad-views/history.js — "Git for CAD" (Phase B): branches + revision
// history panel for the part-edit right-tabs rail. Talks to the vcs.py
// routes (branches/checkout/merge/revisions) added this phase. Checkout and
// a clean merge both call store.setDoc(...) directly (the same live-update
// path the AI-apply panel already uses) — no page reload needed.

import { defineView, esc } from '/static/eos-cad-view.js';

const STYLES = `
  .cadv-hist { height: 100%; overflow-y: auto; padding: 12px; box-sizing: border-box;
    border-right: 1px solid var(--border); background: var(--bg); }
  .cadv-hist .sec-title { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 12px 0 8px; }
  .cadv-hist .sec-title:first-child { margin-top: 0; }
  .cadv-hist .h-branch-row { display: flex; align-items: center; gap: 6px; padding: 4px 6px;
    border-radius: 6px; cursor: pointer; font-size: 12px; }
  .cadv-hist .h-branch-row:hover { background: var(--bg-card-hover); }
  .cadv-hist .h-branch-row.current { background: var(--accent-bg); font-weight: 600; }
  .cadv-hist .h-branch-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cadv-hist .h-branch-del { flex: none; opacity: .5; font-size: 11px; }
  .cadv-hist .h-branch-del:hover { opacity: 1; }
  .cadv-hist .h-new-row { display: flex; gap: 6px; margin-top: 6px; }
  .cadv-hist .h-new-row input { flex: 1; min-width: 0; }
  .cadv-hist .h-merge-row { display: flex; gap: 6px; margin-top: 4px; }
  .cadv-hist .h-merge-row select { flex: 1; min-width: 0; }
  .cadv-hist .h-rev { font-size: 11px; padding: 5px 6px; border-left: 2px solid var(--border); margin-bottom: 4px; }
  .cadv-hist .h-rev-msg { color: var(--text); }
  .cadv-hist .h-rev-meta { color: var(--muted); }
  .cadv-hist .h-conflict { font-size: 11px; border: 1px solid var(--red); border-radius: 6px;
    padding: 6px; margin-top: 8px; }
  .cadv-hist .h-conflict-row { margin-bottom: 6px; }
  .cadv-hist .h-conflict-row:last-child { margin-bottom: 0; }
  .cadv-hist .pt-fill { flex: 1; }
  .cadv-hist .pt-shape-status { font-size: 11px; line-height: 1.5; margin-top: 6px; min-height: 1.5em; }
  .cadv-hist .pt-shape-status.pt-shape-ok { color: var(--green); }
  .cadv-hist .pt-shape-status.pt-shape-err { color: var(--red); }
`;

const MARKUP = `<div class="cadv-hist">
  <div data-h-unsaved class="eos-tool-empty" hidden>Save the document first to use branches/history.</div>
  <div data-h-body hidden>
    <div class="sec-title">Branches <span data-h-current></span></div>
    <div data-h-branches></div>
    <div class="h-new-row">
      <input class="eos-tool-field" data-h-newname placeholder="new branch name" aria-label="new branch name">
      <button class="eos-tool-btn" data-h-fork title="Fork the current branch's tip — instant, no AI call">Fork</button>
    </div>
    <div class="h-merge-row">
      <select class="eos-tool-field" data-h-mergefrom aria-label="merge from branch"></select>
      <button class="eos-tool-btn" data-h-merge title="Three-way merge the selected branch into the current one">Merge in</button>
    </div>
    <div data-h-mergeresult></div>
    <div class="sec-title">Revisions</div>
    <div data-h-revisions></div>
    <div class="pt-row eos-tool-row"><button class="eos-tool-btn pt-fill" data-h-refresh>&#8635; Refresh</button></div>
  </div>
</div>`;

function timeAgo(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (!isFinite(ms) || ms < 0) return iso.slice(0, 16).replace('T', ' ');
  const m = Math.floor(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

function renderConflicts(vctx, result) {
  const el = vctx.pane.querySelector('[data-h-mergeresult]');
  if (!el) return;
  if (!result) { el.innerHTML = ''; return; }
  if (result.ok) {
    el.innerHTML = '<div class="pt-shape-status pt-shape-ok">Merged cleanly.</div>';
    return;
  }
  const conflicts = result.conflicts || [];
  const errors = result.errors || [];
  let html = '<div class="h-conflict">';
  if (conflicts.length) {
    html += `<div>${conflicts.length} conflict(s) — resolve by hand, nothing was committed:</div>`;
    conflicts.forEach((c) => {
      const label = c.feature_id ? `feature '${esc(c.feature_id)}'` : `param '${esc(c.param)}'`;
      html += `<div class="h-conflict-row">${label}<br>` +
        `mine: <code>${esc(JSON.stringify(c.mine))}</code><br>` +
        `theirs: <code>${esc(JSON.stringify(c.theirs))}</code></div>`;
    });
  } else if (errors.length) {
    html += `<div>Merged tree is invalid — resolve by hand, nothing was committed:</div>`;
    errors.forEach((e) => { html += `<div class="h-conflict-row">${esc(String(e))}</div>`; });
  }
  html += '</div>';
  el.innerHTML = html;
}

async function loadAndRender(vctx) {
  const doc = vctx.store.doc;
  const unsavedEl = vctx.pane.querySelector('[data-h-unsaved]');
  const bodyEl = vctx.pane.querySelector('[data-h-body]');
  if (!doc.id) {
    if (unsavedEl) unsavedEl.hidden = false;
    if (bodyEl) bodyEl.hidden = true;
    return;
  }
  if (unsavedEl) unsavedEl.hidden = true;
  if (bodyEl) bodyEl.hidden = false;

  const [branchesRes, revsRes] = await Promise.all([
    vctx.api('/documents/' + encodeURIComponent(doc.id) + '/branches'),
    vctx.api('/documents/' + encodeURIComponent(doc.id) + '/revisions'),
  ]);

  const currentEl = vctx.pane.querySelector('[data-h-current]');
  if (currentEl) currentEl.textContent = branchesRes && branchesRes.ok ? '(' + branchesRes.current + ')' : '';

  const branchesEl = vctx.pane.querySelector('[data-h-branches]');
  const mergeSel = vctx.pane.querySelector('[data-h-mergefrom]');
  if (branchesEl) {
    const branches = (branchesRes && branchesRes.branches) || [];
    const current = branchesRes ? branchesRes.current : '';
    if (!branches.length) {
      branchesEl.innerHTML = '<div class="eos-tool-empty">No revisions yet — save once to start history.</div>';
    } else {
      branchesEl.innerHTML = branches.map((b) => {
        const isCurrent = b.name === current;
        return `<div class="h-branch-row${isCurrent ? ' current' : ''}" data-h-checkout="${esc(b.name)}" title="${esc(b.message || '')}">` +
          `<span class="h-branch-name">${esc(b.name)}</span>` +
          `<span class="h-branch-del" data-h-delbranch="${esc(b.name)}" title="Delete branch">&times;</span>` +
          `</div>`;
      }).join('');
    }
    if (mergeSel) {
      mergeSel.innerHTML = branches.filter((b) => b.name !== current)
        .map((b) => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('')
        || '<option value="">(no other branches)</option>';
    }
  }

  const revsEl = vctx.pane.querySelector('[data-h-revisions]');
  if (revsEl) {
    const revs = (revsRes && revsRes.revisions) || [];
    revsEl.innerHTML = revs.length
      ? revs.slice(0, 30).map((r) =>
          `<div class="h-rev"><div class="h-rev-msg">${esc(r.message || '(save)')}</div>` +
          `<div class="h-rev-meta">${esc(r.branch)} &middot; ${esc(timeAgo(r.created))}</div></div>`).join('')
      : '<div class="eos-tool-empty">No revisions yet.</div>';
  }
}

async function doFork(vctx) {
  // Fork AND check out the new branch — this is what makes "Fork" +
  // "keep editing / use the AI panel's Edit mode as usual" the composed
  // "fork + edit" flow the plan describes, with no second mechanism needed:
  // the branch is instant (no LLM call); the edit that follows is just a
  // normal save on the newly-checked-out branch.
  const nameEl = vctx.pane.querySelector('[data-h-newname]');
  const name = (nameEl && nameEl.value || '').trim();
  if (!name) { if (vctx.setStatus) vctx.setStatus('Name the new branch first.', true); return; }
  const doc = vctx.store.doc;
  const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/branches', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, checkout: true }),
  });
  if (r && r.ok) {
    if (nameEl) nameEl.value = '';
    if (vctx.setStatus) vctx.setStatus("Forked and checked out '" + name + "' — edit and Save to build on it.");
    await loadAndRender(vctx);
  } else if (vctx.setStatus) {
    vctx.setStatus('Fork failed: ' + ((r && r.error) || '?'), true);
  }
}

async function doCheckout(vctx, branch) {
  const doc = vctx.store.doc;
  const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/checkout', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ branch }),
  });
  if (r && r.ok) {
    vctx.store.pushUndo();               // a checkout replaces the live doc — make it undoable
    vctx.store.setDoc(r.document);        // live update, no page reload
    if (vctx.setStatus) vctx.setStatus("Checked out '" + branch + "'");
    await loadAndRender(vctx);
  } else if (vctx.setStatus) {
    vctx.setStatus('Checkout failed: ' + ((r && r.error) || '?'), true);
  }
}

async function doDeleteBranch(vctx, branch) {
  const doc = vctx.store.doc;
  const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/branches/' + encodeURIComponent(branch),
    { method: 'DELETE' });
  if (r && r.ok) {
    if (vctx.setStatus) vctx.setStatus("Deleted '" + branch + "'");
    await loadAndRender(vctx);
  } else if (vctx.setStatus) {
    vctx.setStatus('Delete failed: ' + ((r && r.error) || '?'), true);
  }
}

async function doMerge(vctx) {
  const sel = vctx.pane.querySelector('[data-h-mergefrom]');
  const from_branch = sel && sel.value;
  if (!from_branch) { if (vctx.setStatus) vctx.setStatus('No branch to merge from.', true); return; }
  const doc = vctx.store.doc;
  const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/merge', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from_branch }),
  });
  renderConflicts(vctx, r);
  if (r && r.ok && r.merged) {
    vctx.store.pushUndo();
    vctx.store.setDoc(r.document);
    if (vctx.setStatus) vctx.setStatus('Merged ' + from_branch + ' in.');
    await loadAndRender(vctx);
  } else if (r && r.ok && !r.merged) {
    if (vctx.setStatus) vctx.setStatus(r.message || 'Already up to date.');
  } else if (vctx.setStatus) {
    vctx.setStatus(r && r.conflicts && r.conflicts.length ? 'Merge has conflicts — see below.' : 'Merge failed.', true);
  }
}

function wire(vctx) {
  const pane = vctx.pane;
  const on = (sel, ev, fn) => {
    const el = pane.querySelector(sel);
    if (el && !el._wired) { el._wired = true; el.addEventListener(ev, fn); }
  };
  on('[data-h-fork]', 'click', () => doFork(vctx));
  on('[data-h-merge]', 'click', () => doMerge(vctx));
  on('[data-h-refresh]', 'click', () => loadAndRender(vctx));
  if (!pane._delegated) {
    pane._delegated = true;
    pane.addEventListener('click', (e) => {
      const checkoutEl = e.target.closest('[data-h-checkout]');
      const delEl = e.target.closest('[data-h-delbranch]');
      if (delEl) { e.stopPropagation(); doDeleteBranch(vctx, delEl.dataset.hDelbranch); return; }
      if (checkoutEl) doCheckout(vctx, checkoutEl.dataset.hCheckout);
    });
  }
}

const _v = defineView({
  id: 'history',
  styles: STYLES,
  markup: MARKUP,
  events: [],   // no store event maps to "a save/fork/merge happened" — refresh
                // is on mount + this panel's own actions + the Refresh button
  mount(vctx) { wire(vctx); loadAndRender(vctx); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
