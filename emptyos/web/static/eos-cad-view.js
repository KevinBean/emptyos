// eos-cad-view.js — defineView: the reusable per-pane lifecycle for the CAD
// layout host (the Blender-editor analogue).
//
// A VIEW renders the shared document (the CadStore, eos-cad-store.js) into ONE
// pane of a layout. It does NOT own a document, a backend, or a viewport — the 3D
// view is the single exception (it wraps the one persistent CadViewport). Sibling
// of the retired defineWorkspace (eos-workspace.js), but for a pane, not a whole
// surface. See .claude/rules/cad-layouts.md.
//
//   const _v = defineView({
//     id: 'plan', styles: '…', markup: '…',
//     events: ['doc', 'object', 'select', 'chainage'],   // store events this view redraws on
//     mount(vctx) { /* wire + initial draw; vctx.pane is already populated */ },
//     update(vctx, evt) { /* pure redraw from vctx.store — fired on a relevant event */ },
//     teardown(vctx) { /* drop listeners; the host clears the pane for you too */ },
//   });
//   export const mount = _v.mount; export const teardown = _v.teardown;
//
// vctx = { pane, store, viewport, config, api, setStatus } supplied by the host:
//   pane     — the host <div> for THIS view
//   store    — the shared CadStore (the ONLY way a view reads/writes the doc)
//   viewport — the single shared CadViewport (or null if the layout has no 3D pane)
//   config   — the layout descriptor's per-pane { view, shows:[kinds], opts:{} }
//   api      — fetch bound to the DOCUMENT backend (/cad/api), not per-view
//
// A view's update is a pure function of the store; it never fetches its own doc
// (the host loads once at boot). That is what makes multi-view sync trivial.

// HTML-escape for view markup — shared by every CAD view (was duplicated in each).
export function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function defineView(spec) {
  let _unsub = null;
  let _vctx = null;

  function _injectStyleOnce() {
    if (typeof document === 'undefined' || !spec.styles) return;
    // Per view-TYPE, idempotent + left in place: a layout switch may immediately
    // remount the same view in another pane; churning the <style> is pointless.
    const sid = 'eos-cad-view-style-' + (spec.id || 'view');
    if (document.getElementById(sid)) return;
    const el = document.createElement('style');
    el.id = sid;
    el.dataset.cadView = spec.id || 'view';
    el.textContent = spec.styles;
    document.head.appendChild(el);
  }

  async function mount(vctx) {
    _vctx = vctx;
    _injectStyleOnce();
    if (vctx.pane) vctx.pane.innerHTML = spec.markup || '';
    if (spec.update) {
      _unsub = vctx.store.subscribe(evt => {
        // A view's redraw must never break its siblings.
        try { spec.update(vctx, evt); }
        catch (e) {
          if (typeof console !== 'undefined' && console.warn) {
            console.warn('[cad-view ' + (spec.id || '?') + '] update failed', evt, e);
          }
        }
      }, spec.events);
    }
    if (spec.mount) await spec.mount(vctx);
  }

  function teardown() {
    if (_unsub) { try { _unsub(); } catch (e) { /* best-effort */ } _unsub = null; }
    if (spec.teardown && _vctx) { try { spec.teardown(_vctx); } catch (e) { /* best-effort */ } }
    if (_vctx && _vctx.pane) _vctx.pane.innerHTML = '';
    _vctx = null;
  }

  return { id: spec.id, mount, teardown };
}
